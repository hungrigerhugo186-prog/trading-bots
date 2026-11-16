"""Trading-Bot (NUR Paper Trading). Aufruf: python bot.py fels | rakete

Ablauf nach US-Boersenschluss:
1. offene, nicht ausgefuehrte Kauf-Orders von gestern stornieren
2. bestehende Positionen pflegen (Trailing-Stop, Zeit-Stop)
3. Notbremse (Drawdown) und Marktfilter pruefen
4. Markt screenen, neue Bracket-Orders (Limit + Stop-Loss + Kursziel) fuer morgen einstellen
5. Protokoll nach logs/ schreiben
"""
import datetime as dt
import json
import os
import sys
import time
from zoneinfo import ZoneInfo

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (GetCalendarRequest, GetOrdersRequest,
                                     GetPortfolioHistoryRequest, LimitOrderRequest,
                                     ReplaceOrderRequest, StopLossRequest, TakeProfitRequest)

import universe
from config import BOTS, LOOKBACK_DAYS, MIN_PRICE
from strategies import (add_indicators, breakout_signals, position_size, pullback_signals,
                        trailing_stop)

NY = ZoneInfo("America/New_York")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
FORCE = os.environ.get("FORCE", "false").lower() == "true"   # auch an Nicht-Handelstagen laufen (Test)


def fetch_bars(dc, symbols, today):
    """Tageskerzen (IEX-Feed, im Gratis-Plan enthalten) -> {symbol: DataFrame}."""
    start = dt.datetime.combine(today - dt.timedelta(days=LOOKBACK_DAYS), dt.time(), tzinfo=NY)
    out = {}

    def load(chunk):
        req = StockBarsRequest(symbol_or_symbols=chunk, timeframe=TimeFrame.Day, start=start,
                               feed=DataFeed.IEX, adjustment=Adjustment.ALL)
        df = dc.get_stock_bars(req).df
        if df.empty:
            return
        for sym in df.index.get_level_values(0).unique():
            d = df.xs(sym).sort_index()
            d.index = pd.to_datetime(d.index).tz_convert(NY).date
            out[sym] = d[["open", "high", "low", "close", "volume"]].astype(float)

    for i in range(0, len(symbols), 40):
        chunk = symbols[i:i + 40]
        try:
            load(chunk)
        except Exception:
            for s in chunk:               # ein kaputter Ticker soll nicht alles stoppen
                try:
                    load([s])
                except Exception as e:
                    print("  keine Daten fuer %s: %s" % (s, str(e)[:80]))
    return out


def open_sell_orders(tc, sym):
    """Alle offenen Verkaufs-Orders (Stop + Kursziel) einer Aktie, auch verschachtelte."""
    res, seen = [], set()
    orders = tc.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[sym], nested=True))
    stack = list(orders)
    while stack:
        o = stack.pop()
        stack.extend(o.legs or [])
        if o.side == OrderSide.SELL and str(o.id) not in seen and str(o.status.value) not in (
                "filled", "canceled", "expired", "replaced", "rejected"):
            seen.add(str(o.id))
            res.append(o)
    return res


def last_entry(tc, bot, sym):
    """Letzte ausgefuehrte Kauf-Order des Bots: liefert (Risiko je Aktie, Kaufdatum)."""
    orders = tc.get_orders(GetOrdersRequest(status=QueryOrderStatus.CLOSED, symbols=[sym],
                                            side=OrderSide.BUY, limit=50))
    for o in orders:  # neueste zuerst
        cid = o.client_order_id or ""
        if o.filled_at and cid.startswith(bot + "-"):
            try:
                return int(cid.rsplit("-", 1)[1]) / 100.0, o.filled_at.astimezone(NY).date()
            except ValueError:
                return None, o.filled_at.astimezone(NY).date()
    return None, None


def manage_positions(tc, bot, cfg, positions, ind, log):
    for p in positions:
        sym = p.symbol
        if sym not in ind:
            continue
        d = ind[sym]
        close, atr = float(d["close"].iloc[-1]), float(d["atr"].iloc[-1])
        entry = float(p.avg_entry_price)
        sells = open_sell_orders(tc, sym)
        stop_order = next((o for o in sells if o.stop_price is not None), None)
        risk, bought = last_entry(tc, bot, sym)
        if not stop_order:
            log["warnings"].append("%s hat KEINEN Stop-Loss - bitte pruefen" % sym)
        cur_stop = float(stop_order.stop_price) if stop_order else None
        if risk is None and cur_stop is not None:
            risk = entry - cur_stop

        # Zeit-Stop (Fels): Position laeuft zu lange seitwaerts -> raus
        if cfg["time_stop_days"] and bought:
            held = int((pd.Index(d.index) > bought).sum())
            if held >= cfg["time_stop_days"]:
                log["actions"].append("%s: Zeit-Stop nach %d Handelstagen, Position wird geschlossen" % (sym, held))
                if not DRY_RUN:
                    for o in sells:
                        tc.cancel_order_by_id(o.id)
                    time.sleep(3)
                    tc.close_position(sym)
                continue

        # Trailing-Stop (Rakete)
        if cfg["trail_start_r"] and stop_order and risk:
            new = trailing_stop(entry, risk, close, atr, cur_stop, cfg["trail_start_r"], cfg["trail_atr"])
            if new:
                log["actions"].append("%s: Stop von %.2f auf %.2f nachgezogen" % (sym, cur_stop, new))
                if not DRY_RUN:
                    tc.replace_order_by_id(stop_order.id, ReplaceOrderRequest(stop_price=new))


def drawdown(tc, equity):
    """Abstand zum Hoechststand der letzten 3 Monate (0.08 = -8 %)."""
    try:
        ph = tc.get_portfolio_history(GetPortfolioHistoryRequest(period="3M", timeframe="1D"))
        vals = [float(e) for e in (ph.equity or []) if e]
        peak = max(vals + [equity])
        return max(0.0, 1 - equity / peak)
    except Exception as e:
        print("  Drawdown nicht abrufbar: %s" % str(e)[:100])
        return 0.0


def run(bot):
    cfg = BOTS[bot]
    key, secret = os.environ.get(cfg["key_env"]), os.environ.get(cfg["secret_env"])
    if not key or not secret:
        sys.exit("FEHLER: Secrets %s / %s fehlen." % (cfg["key_env"], cfg["secret_env"]))
    tc = TradingClient(key, secret, paper=True)      # paper=True ist fest verdrahtet: kein Echtgeld
    dc = StockHistoricalDataClient(key, secret)
    today = dt.datetime.now(NY).date()
    log = {"bot": bot, "date": str(today), "dry_run": DRY_RUN, "actions": [], "orders": [],
           "candidates": [], "warnings": []}

    if not tc.get_calendar(GetCalendarRequest(start=today, end=today)) and not FORCE:
        print("%s: heute kein US-Handelstag, nichts zu tun." % bot)
        return

    # 1. alte, nicht ausgefuehrte Kauf-Orders stornieren (Signal gilt nur einen Tag)
    for o in tc.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, side=OrderSide.BUY)):
        log["actions"].append("%s: Kauf-Order von gestern nicht ausgefuehrt, storniert" % o.symbol)
        if not DRY_RUN:
            tc.cancel_order_by_id(o.id)
    if not DRY_RUN:
        time.sleep(2)

    acct = tc.get_account()
    equity, cash = float(acct.equity), float(acct.cash)
    positions = tc.get_all_positions()
    held = {p.symbol for p in positions}

    # 2. Daten laden
    syms = sorted(set(getattr(universe, cfg["universe"])) | held | {cfg["market_symbol"]})
    bars = fetch_bars(dc, syms, today)
    ind = {s: add_indicators(d) for s, d in bars.items()}
    fresh = {s: d for s, d in ind.items() if FORCE or d.index[-1] == today}  # nur Kerzen von heute
    log["symbols_loaded"], log["symbols_fresh"] = len(ind), len(fresh)

    # 3. Positionen pflegen
    manage_positions(tc, bot, cfg, positions, ind, log)

    # 4. Notbremse + Marktfilter
    dd = drawdown(tc, equity)
    risk_pct, allow = cfg["risk_pct"], True
    if dd >= cfg["dd_pause"]:
        allow = False
        log["actions"].append("NOTBREMSE: %.1f %% unter 3-Monats-Hoch, keine neuen Kaeufe" % (dd * 100))
    m = ind.get(cfg["market_symbol"])
    market_ok = None
    if m is not None and len(m) >= cfg["market_sma"]:
        sma = float(m["close"].rolling(cfg["market_sma"]).mean().iloc[-1])
        market_ok = float(m["close"].iloc[-1]) > sma
        if not market_ok and cfg["market_rule"] == "block":
            allow = False
            log["actions"].append("Marktfilter: %s unter %d-Tage-Linie, keine neuen Kaeufe"
                                  % (cfg["market_symbol"], cfg["market_sma"]))
        elif not market_ok:
            risk_pct /= 2
            log["actions"].append("Marktfilter: %s unter %d-Tage-Linie, halbes Risiko"
                                  % (cfg["market_symbol"], cfg["market_sma"]))
    else:
        allow = False
        log["warnings"].append("Keine Marktdaten fuer %s - keine neuen Kaeufe" % cfg["market_symbol"])

    # 5. Screenen und Orders einstellen
    pool = {s: d for s, d in fresh.items() if s not in held and s != cfg["market_symbol"]
            and s in set(getattr(universe, cfg["universe"]))}
    fn = pullback_signals if cfg["strategy"] == "pullback" else breakout_signals
    cands = fn(pool, cfg, MIN_PRICE)
    log["candidates"] = [{k: (round(v, 2) if isinstance(v, float) else v) for k, v in c.items()}
                         for c in cands[:10]]
    slots = min(cfg["max_positions"] - len(positions), cfg["max_new_per_day"]) if allow else 0
    for c in cands:
        if slots <= 0:
            break
        limit = round(c["close"] * (1 + cfg["entry_buffer"]), 2)
        stop, target = round(c["stop"], 2), round(c["target"], 2)
        qty = position_size(equity, cash, risk_pct, c["close"], c["stop"], cfg["max_pos_pct"])
        if qty < 1 or stop >= limit or target <= limit:
            continue
        risk_share = c["close"] - c["stop"]
        order = {"symbol": c["symbol"], "qty": qty, "limit": limit, "stop": stop, "target": target,
                 "risk_usd": round(qty * risk_share, 2), "crv": cfg["target_r"], "reason": c["reason"]}
        if not DRY_RUN:
            try:
                tc.submit_order(LimitOrderRequest(
                    symbol=c["symbol"], qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.GTC,
                    limit_price=limit, order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=target),
                    stop_loss=StopLossRequest(stop_price=stop),
                    client_order_id="%s-%s-%s-%d" % (bot, c["symbol"], today.strftime("%Y%m%d"),
                                                     round(risk_share * 100))))
            except Exception as e:
                log["warnings"].append("Order %s abgelehnt: %s" % (c["symbol"], str(e)[:150]))
                continue
        log["orders"].append(order)
        cash -= qty * limit
        slots -= 1

    log.update({"equity": round(equity, 2), "cash_after_orders": round(cash, 2),
                "drawdown_pct": round(dd * 100, 2), "market_ok": market_ok,
                "positions": [{"symbol": p.symbol, "qty": float(p.qty),
                               "entry": float(p.avg_entry_price),
                               "pl_usd": float(p.unrealized_pl)} for p in positions]})
    write_log(log)


def write_log(log):
    os.makedirs("logs", exist_ok=True)
    path = "logs/%s_%s.json" % (log["date"], log["bot"])
    with open(path, "w") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)
    lines = ["## Bot %s - %s%s" % (log["bot"].capitalize(), log["date"], " (TESTLAUF)" if log["dry_run"] else ""),
             "- Kontowert: %.2f $ | Drawdown: %.1f %% | Markt ok: %s"
             % (log["equity"], log["drawdown_pct"], log["market_ok"]),
             "- Positionen: " + (", ".join("%s (%+.0f $)" % (p["symbol"], p["pl_usd"])
                                           for p in log["positions"]) or "keine")]
    lines += ["- NEUE ORDER: %(qty)s x %(symbol)s, Limit %(limit)s, Stop %(stop)s, Ziel %(target)s, "
              "Risiko %(risk_usd)s $ - %(reason)s" % o for o in log["orders"]]
    lines += ["- " + a for a in log["actions"]] + ["- WARNUNG: " + w for w in log["warnings"]]
    text = "\n".join(lines)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(text + "\n\n")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in BOTS:
        sys.exit("Aufruf: python bot.py fels|rakete")
    run(sys.argv[1])
