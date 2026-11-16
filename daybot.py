"""Daytrading-Bot fuer Falke und Rakete (NUR Paper Trading).

Aufruf:
    python daybot.py falke                      laeuft bis zum Zwangsschluss (15:50 New York)
    python daybot.py rakete --bis 12:00         endet um 12:00 New York (der naechste Job uebernimmt)
    python daybot.py falke --kontrolle          nur pruefen: ist das Konto leer? (Fehler, wenn nicht)
    python daybot.py falke --freigeben          Konto-Notbremse wieder freigeben
    python daybot.py falke --replay 2026-11-13  einen vergangenen Tag im DRY_RUN nachspielen

Umgebungsvariable DRY_RUN=true: alles berechnen und anzeigen, aber keine Order senden.

Der Bot merkt sich nichts Wichtiges nur im Speicher: Positionen, Orders und die
heutigen Trades liest er in jeder Minute neu von Alpaca. Deshalb kann er jederzeit
neu gestartet werden, ohne doppelt zu kaufen.

Ende-Codes: 0 = alles in Ordnung, 1 = Fehler, 2 = nach dem Zwangsschluss ist noch
eine Position offen (der Job soll dann sichtbar fehlschlagen).
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

import orb
import universe
from config import (DAY_BOTS, EMERGENCY_STOP_PCT, ENTRY_LIMIT_PCT, ENTRY_TIMEOUT_MIN,
                    FLATTEN_BEFORE_CLOSE_MIN, FLATTEN_TIME, MIN_RISK_PCT, OR_MIN_BARS_FRAC,
                    POLL_SECOND, RELVOL_LOOKBACK_DAYS, RELVOL_MIN_DAYS, TIMEZONE)

NY = ZoneInfo(TIMEZONE)
DONE = {"filled", "canceled", "expired", "replaced", "rejected", "done_for_day"}
ID_RE = re.compile(r"^(?P<bot>[a-z]+)-(?P<day>\d{8})-(?P<sym>[A-Z.]+)-(?P<side>[LS])-"
                   r"(?P<part>[TR])-(?P<entry>\d+)-(?P<risk>\d+)$")


# ===========================================================================
# Hilfsfunktionen (ohne Broker, offline testbar)
# ===========================================================================
def make_id(bot, day, sym, side, part, entry, risk):
    """Eigene Order-Kennung. Darin stehen Einstieg und Risiko, damit der Bot nach
    einem Neustart alles Noetige direkt von Alpaca zuruecklesen kann.
    part: T = Teil mit Kursziel 2R, R = Rest mit nachgezogenem Stop."""
    return "%s-%s-%s-%s-%s-%d-%d" % (bot, day.strftime("%Y%m%d"), sym,
                                     "L" if side == "long" else "S", part,
                                     round(entry * 100), round(risk * 100))


def parse_id(client_id, bot, day):
    m = ID_RE.match(client_id or "")
    if not m or m["bot"] != bot or m["day"] != day.strftime("%Y%m%d"):
        return None
    return {"symbol": m["sym"], "side": "long" if m["side"] == "L" else "short",
            "part": m["part"], "entry": int(m["entry"]) / 100.0, "risk": int(m["risk"]) / 100.0}


def is_open(order):
    return order["status"] not in DONE


def clean(text):
    """Entfernt alles Geheime aus einem Text, bevor er angezeigt oder gespeichert wird:
    API-Keys aus der Umgebung und lange Kennungen (Konto- und Order-Nummern)."""
    text = str(text)
    for name, value in os.environ.items():
        if name.startswith("ALPACA_") and value and len(value) >= 8:
            text = text.replace(value, "***")
    text = re.sub(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                  "<id>", text)
    return text[:300]


def easy_borrow(asset):
    """Meldet Alpaca die Aktie als leicht leihbar? Neues Feld borrow_status,
    bei aelteren Antworten das alte Feld easy_to_borrow."""
    status = getattr(asset, "borrow_status", None)
    if status is not None:
        return getattr(status, "value", str(status)) == "easy_to_borrow"
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return bool(getattr(asset, "easy_to_borrow", False))


def floor_minute(ts):
    return ts.replace(second=0, microsecond=0)


# ===========================================================================
# Broker: die einzige Stelle, die mit Alpaca spricht
# ===========================================================================
class AlpacaBroker:
    def __init__(self, key, secret):
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.trading.client import TradingClient
        self.tc = TradingClient(key, secret, paper=True)   # paper=True ist fest: kein Echtgeld
        self.dc = StockHistoricalDataClient(key, secret)

    # --- Uhr und Kalender ---
    def now(self):
        try:
            return self.tc.get_clock().timestamp.astimezone(NY)
        except Exception:
            return dt.datetime.now(NY)

    def sleep_until(self, when):
        time.sleep(max(0.0, (when - dt.datetime.now(NY)).total_seconds()))

    def session(self, day):
        """(Oeffnung, Schluss) als Uhrzeiten oder None, wenn kein Handelstag."""
        from alpaca.trading.requests import GetCalendarRequest
        cal = self.tc.get_calendar(GetCalendarRequest(start=day, end=day))
        if not cal or cal[0].date != day:
            return None
        return cal[0].open.time(), cal[0].close.time()

    # --- Konto ---
    def account(self):
        a = self.tc.get_account()
        return {"equity": float(a.equity), "last_equity": float(a.last_equity),
                "cash": float(a.cash)}

    def positions(self):
        out = []
        for p in self.tc.get_all_positions():
            qty = float(p.qty)
            if str(getattr(p.side, "value", p.side)) == "short" and qty > 0:
                qty = -qty
            out.append({"symbol": p.symbol, "qty": qty, "entry": float(p.avg_entry_price),
                        "price": float(p.current_price or p.avg_entry_price)})
        return out

    def orders_today(self, day):
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest
        start = dt.datetime.combine(day, dt.time(), tzinfo=NY)
        raw = self.tc.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, after=start,
                                                  limit=500, nested=True))
        out, seen = [], set()

        def add(o, parent):
            if str(o.id) in seen:
                return
            seen.add(str(o.id))
            kind = getattr(o, "order_type", None) or o.type
            out.append({
                "id": str(o.id), "client_id": o.client_order_id or "", "symbol": o.symbol,
                "side": o.side.value, "type": getattr(kind, "value", str(kind)),
                "qty": float(o.qty or 0), "filled_qty": float(o.filled_qty or 0),
                "filled_avg": float(o.filled_avg_price) if o.filled_avg_price else None,
                "limit": float(o.limit_price) if o.limit_price else None,
                "stop": float(o.stop_price) if o.stop_price else None,
                "status": o.status.value, "created": o.created_at, "filled_at": o.filled_at,
                "parent": parent})
            for leg in (o.legs or []):
                add(leg, o.client_order_id or "")

        for o in raw:
            add(o, None)
        return out

    def equity_peak(self, since=None):
        """Hoechster Kontowert (Tagesschluss) seit `since` laut Alpaca."""
        from alpaca.trading.requests import GetPortfolioHistoryRequest
        ph = self.tc.get_portfolio_history(GetPortfolioHistoryRequest(period="1A", timeframe="1D"))
        best = 0.0
        for ts, eq in zip(ph.timestamp or [], ph.equity or []):
            if not eq:
                continue
            if since and dt.datetime.fromtimestamp(ts, NY).date() < since:
                continue
            best = max(best, float(eq))
        return best

    def shortable(self, symbol):
        a = self.tc.get_asset(symbol)
        return bool(a.tradable and a.shortable and easy_borrow(a))

    # --- Kursdaten (IEX, Gratis-Plan) ---
    def bars(self, symbols, start, end, minutes=1):
        import pandas as pd
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        tf = TimeFrame.Minute if minutes == 1 else TimeFrame(minutes, TimeFrameUnit.Minute)
        out, symbols = {}, list(symbols)
        for i in range(0, len(symbols), 50):
            df = self.dc.get_stock_bars(StockBarsRequest(
                symbol_or_symbols=symbols[i:i + 50], timeframe=tf, start=start, end=end,
                feed=DataFeed.IEX, adjustment=Adjustment.SPLIT)).df
            if df.empty:
                continue
            for sym in df.index.get_level_values(0).unique():
                d = df.xs(sym).sort_index()
                d.index = pd.to_datetime(d.index).tz_convert(NY)
                out[sym] = d[["open", "high", "low", "close", "volume"]].astype(float)
        return out

    def daily_closes(self, symbol, end):
        """Die letzten Tagesschlusskurse [(Datum, Schluss), ...] bis `end`."""
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        df = self.dc.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbol, timeframe=TimeFrame.Day, start=end - dt.timedelta(days=10),
            end=end, feed=DataFeed.IEX)).df
        if df.empty:
            return []
        d = df.xs(symbol).sort_index()
        return [(ts.tz_convert(NY).date(), float(c)) for ts, c in d["close"].items()]

    # --- Orders ---
    def submit_bracket(self, symbol, side, qty, limit, stop, target, client_id):
        """Einstieg als Limit-Order; Stop-Loss und Kursziel liegen sofort beim Broker."""
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, StopLossRequest, TakeProfitRequest
        self.tc.submit_order(LimitOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.BUY if side == "long" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY, limit_price=limit, order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=target),
            stop_loss=StopLossRequest(stop_price=stop), client_order_id=client_id))

    def submit_stop(self, symbol, exit_side, qty, stop):
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import StopOrderRequest
        self.tc.submit_order(StopOrderRequest(
            symbol=symbol, qty=qty, side=OrderSide.SELL if exit_side == "sell" else OrderSide.BUY,
            time_in_force=TimeInForce.DAY, stop_price=stop))

    def replace_stop(self, order_id, stop):
        from alpaca.trading.requests import ReplaceOrderRequest
        self.tc.replace_order_by_id(order_id, ReplaceOrderRequest(stop_price=stop))

    def cancel(self, order_id):
        self.tc.cancel_order_by_id(order_id)

    def cancel_all(self):
        self.tc.cancel_orders()

    def close_all(self):
        self.tc.close_all_positions(cancel_orders=True)


class ReplayBroker(AlpacaBroker):
    """Spielt einen vergangenen Handelstag mit echten Kursdaten nach - nur zum Anschauen.
    Die Uhr ist kuenstlich, das Konto gilt als leer, Orders sind technisch unmoeglich."""

    def __init__(self, key, secret, day):
        super().__init__(key, secret)
        self.t = dt.datetime.combine(day, dt.time(9, 25), tzinfo=NY)
        self._account = None

    def now(self):
        return self.t

    def sleep_until(self, when):
        self.t = max(self.t, when)

    def account(self):
        if self._account is None:
            self._account = super().account()
            self._account["last_equity"] = self._account["equity"]
        return dict(self._account)

    def positions(self):
        return []

    def orders_today(self, day):
        return []

    def bars(self, symbols, start, end, minutes=1):
        out = super().bars(symbols, start, min(end, self.t), minutes)
        return {s: d[d.index < self.t] for s, d in out.items()}   # kein Blick in die Zukunft

    def _never(self, *a, **k):
        raise RuntimeError("Im Replay werden nie Orders gesendet.")

    submit_bracket = submit_stop = replace_stop = cancel = cancel_all = close_all = _never


# ===========================================================================
# Der Bot
# ===========================================================================
class DayBot:
    def __init__(self, name, cfg, broker, dry_run=False, log_dir="logs", state_dir="state",
                 out=print):
        self.name, self.cfg, self.broker, self.dry_run = name, cfg, broker, dry_run
        self.log_dir, self.state_dir, self.out = log_dir, state_dir, out
        self.symbols = list(getattr(universe, cfg["universe"]))
        self.day = None
        self.state = {"peak_equity": 0.0, "peak_since": None, "halted": False, "halted_on": None}
        self.log = {}
        self.seen = set()            # schon protokollierte Hinweise (keine Wiederholungen)
        self.tried_local = set()     # Aktien, die heute erledigt sind (kein zweiter Versuch)
        self.dry_orders = []         # was der Bot im DRY_RUN gesendet haette
        self.local_trades = 0        # eben gesendete Orders, bis Alpaca sie zurueckmeldet
        self.local_exposure = 0.0
        self.no_entries = None       # Grund, warum heute gar nicht neu gehandelt wird
        self.ctx = None              # Rakete: Vortagesschluss + Durchschnitts-Volumen je Aktie
        self.short_cache = {}
        self.bar_cache = (None, {})
        self.equity = self.last_equity = 0.0
        self.positions, self.orders, self.entries = {}, [], {}

    # --- Protokoll --------------------------------------------------------
    def note(self, symbol, action, reason, once=False, now=None):
        key = (symbol, action, reason)
        if once and key in self.seen:
            return
        self.seen.add(key)
        stamp = (now or self.broker.now()).strftime("%H:%M")
        self.log.setdefault("decisions", []).append(
            {"time": stamp, "symbol": symbol, "action": action, "reason": clean(reason)})
        self.out("%s  %-6s %-22s %s" % (stamp, symbol, action, clean(reason)))

    def warn(self, text):
        text = clean(text)
        if text in self.seen:
            return
        self.seen.add(text)
        self.log.setdefault("warnings", []).append(
            {"time": self.broker.now().strftime("%H:%M"), "text": text})
        self.out("WARNUNG: " + text)

    def log_path(self):
        return os.path.join(self.log_dir, "%s_%s.json" % (self.day, self.name))

    def load_log(self):
        self.log = {"bot": self.name, "date": str(self.day), "dry_run": self.dry_run,
                    "starts": [], "decisions": [], "warnings": []}
        try:
            with open(self.log_path(), encoding="utf-8") as f:
                old = json.load(f)
            if old.get("date") == str(self.day) and old.get("dry_run") == self.dry_run:
                self.log.update({k: old.get(k, []) for k in ("starts", "decisions", "warnings")})
                for d in self.log["decisions"]:
                    self.seen.add((d["symbol"], d["action"], d["reason"]))
                self.dry_orders = old.get("dry_run_orders", [])
                self.tried_local = set(old.get("tried", []))
        except (OSError, ValueError):
            pass

    def write_log(self):
        eq0 = self.last_equity or 0.0
        self.log.update({
            "equity_start": round(eq0, 2), "equity": round(self.equity, 2),
            "pnl_day_usd": round(self.equity - eq0, 2) if eq0 else 0.0,
            "pnl_day_pct": round((self.equity / eq0 - 1) * 100, 3) if eq0 else 0.0,
            "peak_equity": round(self.state["peak_equity"], 2),
            "notbremse": bool(self.state["halted"]),
            "trades": self.build_trades(), "dry_run_orders": self.dry_orders,
            "tried": sorted(self.tried_local),
        })
        os.makedirs(self.log_dir, exist_ok=True)
        tmp = self.log_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.log, f, indent=2, ensure_ascii=False, default=str)
        for attempt in range(5):          # Windows sperrt die Datei manchmal kurz (Virenscanner)
            try:
                os.replace(tmp, self.log_path())
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.3)

    # --- Zustand, der Tage ueberdauert (Hoechststand, Notbremse) -----------
    def state_path(self):
        return os.path.join(self.state_dir, "%s.json" % self.name)

    def load_state(self):
        try:
            with open(self.state_path(), encoding="utf-8") as f:
                self.state.update(json.load(f))
        except (OSError, ValueError):
            pass

    def save_state(self):
        if self.dry_run:
            return
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.state_path(), "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)

    def check_halt(self):
        """Konto-Notbremse: Hoechststand merken; liegt das Konto zu weit darunter,
        gibt es keine neuen Trades, bis der Nutzer mit --freigeben freigibt."""
        changed = False
        if self.equity > self.state["peak_equity"]:
            self.state["peak_equity"], changed = self.equity, True
        if not self.state["halted"] and orb.drawdown_halt(self.state["peak_equity"], self.equity,
                                                          self.cfg["dd_halt"]):
            self.state.update({"halted": True, "halted_on": str(self.day)})
            changed = True
            self.warn("KONTO-NOTBREMSE: Kontowert %.2f $ liegt %.1f %% unter dem Hoechststand %.2f $. "
                      "Keine neuen Trades bis zur Freigabe (python daybot.py %s --freigeben)."
                      % (self.equity, (1 - self.equity / self.state["peak_equity"]) * 100,
                         self.state["peak_equity"], self.name))
        if changed:
            self.save_state()

    def release(self):
        """Notbremse freigeben: der heutige Kontowert wird der neue Hoechststand."""
        acct = self.broker.account()
        self.state.update({"halted": False, "halted_on": None, "peak_equity": acct["equity"],
                           "peak_since": str(self.broker.now().date())})
        os.makedirs(self.state_dir, exist_ok=True)
        with open(self.state_path(), "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)
        self.out("%s: Notbremse freigegeben. Neuer Hoechststand: %.2f $" % (self.name, acct["equity"]))

    # --- Stand von Alpaca lesen (in jeder Minute neu) ----------------------
    def sync(self):
        acct = self.broker.account()
        positions = {p["symbol"]: p for p in self.broker.positions()}
        orders = self.broker.orders_today(self.day)
        entries = {}
        for o in orders:
            info = parse_id(o["client_id"], self.name, self.day)
            if info:
                e = entries.setdefault(info["symbol"], {"side": info["side"], "entry": info["entry"],
                                                        "risk": info["risk"], "parts": {}})
                e["parts"][info["part"]] = o
        self.equity, self.last_equity = acct["equity"], acct["last_equity"]
        self.positions, self.orders, self.entries = positions, orders, entries
        self.local_trades, self.local_exposure = 0, 0.0
        self.check_halt()

    def trades_today(self):
        """Gezaehlt wird jede Aktie mit ausgefuehrter oder noch offener Einstiegs-Order."""
        n = sum(1 for e in self.entries.values()
                if any(o["filled_qty"] > 0 or is_open(o) for o in e["parts"].values()))
        return n + self.local_trades + len(self.dry_orders)

    def tried(self):
        return set(self.entries) | self.tried_local | {o["symbol"] for o in self.dry_orders}

    def exposure(self):
        """Betrag aller offenen Positionen und noch offenen Einstiegs-Orders (Long + Short)."""
        total = sum(abs(p["qty"]) * p["price"] for p in self.positions.values())
        for e in self.entries.values():
            for o in e["parts"].values():
                if is_open(o):
                    total += (o["qty"] - o["filled_qty"]) * (o["limit"] or e["entry"])
        total += sum(o["qty"] * o["limit"] for o in self.dry_orders if o.get("open", True))
        return total + self.local_exposure

    def open_stops(self, symbol, exit_side):
        return [o for o in self.orders if o["symbol"] == symbol and o["side"] == exit_side
                and o["stop"] is not None and is_open(o)]

    def day_bars(self, symbols, now):
        """Minutenkerzen von heute (nur fertige Kerzen), pro Minute hoechstens einmal geladen."""
        key = floor_minute(now)
        if self.bar_cache[0] != key:
            self.bar_cache = (key, {})
        cache = self.bar_cache[1]
        missing = [s for s in symbols if s not in cache]
        if missing:
            start = dt.datetime.combine(self.day, orb.MARKET_OPEN, tzinfo=NY)
            got = self.broker.bars(missing, start, now, 1)
            for s in missing:
                d = got.get(s)
                cache[s] = d[d.index < key] if d is not None else None
        return {s: cache[s] for s in symbols if cache.get(s) is not None}

    # --- Schutz: jede Position braucht einen Stop beim Broker ---------------
    def protect_positions(self):
        for sym, p in self.positions.items():
            exit_side = "sell" if p["qty"] > 0 else "buy"
            need = abs(p["qty"])
            covered = sum(o["qty"] - o["filled_qty"] for o in self.open_stops(sym, exit_side))
            if covered + 1e-9 >= need:
                continue
            missing = need - covered
            e = self.entries.get(sym)
            sign = 1 if p["qty"] > 0 else -1
            if e:
                stop = round(e["entry"] - sign * e["risk"], 2)
            else:
                stop = round(p["price"] * (1 - sign * EMERGENCY_STOP_PCT), 2)
            self.warn("%s: %g Aktien OHNE Stop-Order gefunden - lege sofort einen Stop bei %.2f an."
                      % (sym, missing, stop))
            if self.dry_run:
                continue
            try:
                self.broker.submit_stop(sym, exit_side, missing, stop)
            except Exception as ex:
                self.warn("%s: Not-Stop konnte nicht angelegt werden (%s)" % (sym, ex))

    # --- Einstiegs-Orders, die nach 2 Minuten nicht ausgefuehrt sind --------
    def cancel_stale(self, now):
        for sym, e in self.entries.items():
            for o in e["parts"].values():
                if not is_open(o) or o["created"] is None:
                    continue
                age = (now - o["created"]).total_seconds()
                if age >= ENTRY_TIMEOUT_MIN * 60 - 5:
                    self.note(sym, "Order storniert", "nach %d Minuten nicht (ganz) ausgefuehrt"
                              % ENTRY_TIMEOUT_MIN, once=True, now=now)
                    if not self.dry_run:
                        try:
                            self.broker.cancel(o["id"])
                        except Exception as ex:
                            self.warn("%s: Stornieren fehlgeschlagen (%s)" % (sym, ex))

    # --- Rakete: Stop der zweiten Haelfte nachziehen ------------------------
    def manage_runners(self, now):
        if not self.cfg["trail_r"]:
            return
        for sym, e in self.entries.items():
            p = self.positions.get(sym)
            part_t, part_r = e["parts"].get("T"), e["parts"].get("R")
            if not p or not part_t or not part_r or part_r["filled_qty"] <= 0 \
                    or part_t["filled_qty"] <= 0:
                continue
            if abs(p["qty"]) > part_r["filled_qty"] + 1e-9:
                continue                  # Teilverkauf bei 2R ist noch nicht erfolgt
            long = e["side"] == "long"
            stops = self.open_stops(sym, "sell" if long else "buy")
            bars = self.day_bars([sym], now).get(sym)
            since = part_r["filled_at"] or part_t["filled_at"]
            if not stops or bars is None or since is None:
                continue
            b = bars[bars.index >= floor_minute(since.astimezone(NY))]
            if b.empty:
                continue
            best = float(b["high"].max()) if long else float(b["low"].min())
            entry = part_r["filled_avg"] or e["entry"]
            for s in stops:
                new = orb.trail_stop(self.cfg, entry, e["risk"], best, s["stop"], e["side"])
                if not new:
                    continue
                self.note(sym, "Stop nachgezogen", "von %.2f auf %.2f (bester Kurs %.2f, Einstieg %.2f)"
                          % (s["stop"], new, best, entry), once=True, now=now)
                if not self.dry_run:
                    try:
                        self.broker.replace_stop(s["id"], new)
                    except Exception as ex:
                        self.warn("%s: Stop nachziehen fehlgeschlagen (%s) - alter Stop bleibt"
                                  % (sym, ex))

    # --- Rakete: Daten der Vortage fuer Kursluecke und relatives Volumen ----
    def load_context(self, now):
        if self.cfg["min_gap_pct"] is None or self.ctx is not None:
            return
        start = dt.datetime.combine(self.day - dt.timedelta(days=2 * RELVOL_LOOKBACK_DAYS + 10),
                                    dt.time(), tzinfo=NY)
        end = dt.datetime.combine(self.day, dt.time(), tzinfo=NY)
        got = self.broker.bars(self.symbols, start, min(end, now), self.cfg["range_minutes"])
        ctx = {}
        for sym, d in got.items():
            t = d.index.time
            d = d[(t >= orb.MARKET_OPEN) & (t < orb.REGULAR_CLOSE)]
            c = orb.context_from_bars(d, self.day, RELVOL_LOOKBACK_DAYS, RELVOL_MIN_DAYS)
            if c:
                ctx[sym] = c
        self.ctx = ctx
        self.note("-", "Vortagesdaten geladen", "fuer %d von %d Aktien" % (len(ctx), len(self.symbols)),
                  once=True, now=now)

    def is_shortable(self, sym):
        if sym not in self.short_cache:
            try:
                self.short_cache[sym] = self.broker.shortable(sym)
            except Exception as ex:
                self.warn("%s: Short-Status nicht abrufbar (%s) - kein Short" % (sym, ex))
                return False
        return self.short_cache[sym]

    # --- Signale suchen -----------------------------------------------------
    def scan(self, now):
        if self.no_entries:
            self.note("-", "kein Trade", self.no_entries, once=True, now=now)
            return
        ok, why = orb.entry_allowed(self.cfg, self.trades_today(), self.last_equity, self.equity,
                                    "", (), self.state["halted"])
        if not ok:
            self.note("-", "kein neuer Trade", why, once=True, now=now)
            return
        self.load_context(now)
        last_bar = floor_minute(now) - dt.timedelta(minutes=1)     # letzte fertige Kerze
        todo = [s for s in self.symbols if s not in self.tried()]
        bars = self.day_bars(todo, now)
        cfg, minutes = self.cfg, self.cfg["range_minutes"]
        for sym in sorted(todo):
            b = bars.get(sym)
            if b is None or b.empty:
                continue
            rng = orb.opening_range(b, minutes, OR_MIN_BARS_FRAC)
            if rng is None:
                self.skip(sym, "zu wenig Kursdaten in der Eroeffnungsspanne", now)
                continue
            if not orb.range_wide_enough(rng, cfg["min_range_pct"]):
                self.skip(sym, "Spanne nur %.2f %% breit (mind. %.1f %%)"
                          % ((rng["high"] - rng["low"]) / rng["mid"] * 100, cfg["min_range_pct"] * 100), now)
                continue
            c = (self.ctx or {}).get(sym) or {}
            side, why = orb.pick_side(cfg, orb.gap_pct(rng["open"], c.get("prev_close")),
                                      orb.relative_volume(rng["volume"], c.get("avg_or_vol")))
            if side is None:
                self.skip(sym, why, now)
                continue
            sig = orb.find_breakout(b, rng, minutes, self.last_entry, side)
            if sig is None:
                continue                  # noch kein Ausbruch - naechste Minute wieder pruefen
            if b.index[sig] < last_bar:
                self.skip(sym, "Signal um %s verpasst (Bot lief da nicht oder Kurse kamen zu spaet)"
                          % b.index[sig].strftime("%H:%M"), now)
                continue
            self.enter(sym, side, rng, float(b["close"].iloc[sig]), why, now)

    def skip(self, sym, reason, now):
        self.tried_local.add(sym)
        self.note(sym, "kein Trade", reason, once=True, now=now)

    def enter(self, sym, side, rng, signal_close, why, now):
        cfg = self.cfg
        ok, reason = orb.entry_allowed(cfg, self.trades_today(), self.last_equity, self.equity,
                                       sym, self.tried(), self.state["halted"])
        if not ok:
            self.skip(sym, "Signal, aber: " + reason, now)
            return
        limit = orb.entry_limit(signal_close, side, ENTRY_LIMIT_PCT)
        if side == "short":
            ok, reason = orb.short_allowed(cfg, limit, self.is_shortable(sym))
            if not ok:
                self.skip(sym, "Short-Signal, aber: " + reason, now)
                return
        plan = orb.plan_trade(cfg, limit, rng, side, MIN_RISK_PCT)
        if plan is None:
            self.skip(sym, "Signal, aber Stop liegt zu nah oder auf der falschen Seite", now)
            return
        power = orb.buying_power(self.equity, self.exposure(), cfg["leverage"])
        qty = orb.size_position(self.equity, power, cfg["risk_pct"], limit, plan["risk"],
                                cfg["max_pos_pct"])
        if qty < 1:
            self.skip(sym, "Signal, aber Stueckzahl 0 (Kaufkraft %.0f $, Kurs %.2f $)" % (power, limit), now)
            return
        q_target, q_runner = orb.split_qty(qty, cfg["runner_fraction"])
        text = "%s %d x %s, Limit %.2f, Stop %.2f, Ziel %.2f" % (
            "KAUF" if side == "long" else "LEERVERKAUF", qty, sym, limit, plan["stop"], plan["target"])
        if q_runner:
            text += " (%d Stueck), Rest %d Stueck mit Not-Ziel %.2f" % (q_target, q_runner,
                                                                       plan["runner_target"])
        text += ", Risiko %.2f $ = %.2f %% | Spanne %.2f-%.2f | %s" % (
            qty * plan["risk"], qty * plan["risk"] / self.equity * 100, rng["low"], rng["high"], why)
        self.tried_local.add(sym)
        if self.dry_run:
            self.dry_orders.append({"time": now.strftime("%H:%M"), "symbol": sym, "side": side,
                                    "qty": qty, "limit": limit, "stop": plan["stop"],
                                    "target": plan["target"], "runner_qty": q_runner,
                                    "runner_target": plan["runner_target"]})
            self.note(sym, "WUERDE handeln (DRY_RUN)", text, now=now)
            return
        try:
            self.broker.submit_bracket(sym, side, q_target, limit, plan["stop"], plan["target"],
                                       make_id(self.name, self.day, sym, side, "T", limit, plan["risk"]))
        except Exception as ex:
            self.warn("%s: Order abgelehnt (%s) - kein Trade" % (sym, ex))
            return
        self.local_trades += 1
        self.local_exposure += q_target * limit
        if q_runner:
            try:
                self.broker.submit_bracket(sym, side, q_runner, limit, plan["stop"],
                                           plan["runner_target"],
                                           make_id(self.name, self.day, sym, side, "R", limit, plan["risk"]))
                self.local_exposure += q_runner * limit
            except Exception as ex:
                self.warn("%s: zweite Teil-Order abgelehnt (%s) - es laeuft nur der Teil mit Kursziel"
                          % (sym, ex))
        self.note(sym, "Order gesendet", text, now=now)

    # --- Trades des Tages aus den Orders bei Alpaca zusammenbauen -----------
    def build_trades(self):
        trades = []
        for sym, e in sorted(self.entries.items()):
            parts = [o for o in e["parts"].values() if o["filled_qty"] > 0]
            qty = sum(o["filled_qty"] for o in parts)
            if qty <= 0:
                continue
            long = e["side"] == "long"
            d = 1 if long else -1
            entry = sum(o["filled_qty"] * (o["filled_avg"] or e["entry"]) for o in parts) / qty
            exit_side = "sell" if long else "buy"
            exits = [o for o in self.orders if o["symbol"] == sym and o["side"] == exit_side
                     and o["filled_qty"] > 0 and not parse_id(o["client_id"], self.name, self.day)]
            exit_qty = sum(o["filled_qty"] for o in exits)
            exit_val = sum(o["filled_qty"] * (o["filled_avg"] or 0.0) for o in exits)
            pnl = d * (exit_val - exit_qty * entry)
            stop = round(e["entry"] - d * e["risk"], 2)
            reason, exit_time = None, None
            if exits:
                last = max(exits, key=lambda o: str(o["filled_at"] or ""))
                exit_time = last["filled_at"]
                if last["type"] == "market":
                    reason = "Zwangsschluss"
                elif last["stop"] is not None:
                    reason = "Stop" if abs(last["stop"] - stop) < 0.011 else "Nachgezogener Stop"
                else:
                    reason = "Kursziel"
            first = min(parts, key=lambda o: str(o["filled_at"] or ""))
            trades.append({
                "symbol": sym, "side": e["side"], "qty": qty, "entry": round(entry, 4),
                "stop": stop, "target": round(e["entry"] + d * self.cfg["target_r"] * e["risk"], 2),
                "exit": round(exit_val / exit_qty, 4) if exit_qty else None,
                "exit_reason": reason, "pnl_usd": round(pnl, 2),
                "r": round(orb.r_multiple(pnl, qty, entry, stop), 3),
                "status": "geschlossen" if exit_qty + 1e-9 >= qty else "offen",
                "entry_time": first["filled_at"], "exit_time": exit_time})
        return trades

    # --- Zwangsschluss ------------------------------------------------------
    def flatten(self, now):
        self.note("-", "Zwangsschluss", "alle Orders stornieren, alle Positionen schliessen",
                  once=True, now=now)
        if self.dry_run:
            self.sync()
            left = sorted(self.positions)
            self.log["flatten"] = {"time": now.strftime("%H:%M"), "dry_run": True,
                                   "positions_left": left, "konto_leer": not left}
            self.note("-", "Kontrolle (DRY_RUN)", "offene Positionen: %s" % (", ".join(left) or "keine"),
                      now=now)
            self.finish_day(now)
            return 0
        left, open_orders = None, None
        for attempt in range(6):
            try:
                self.broker.cancel_all()
                self.broker.close_all()
            except Exception as ex:
                self.warn("Zwangsschluss, Versuch %d: %s" % (attempt + 1, ex))
            self.broker.sleep_until(self.broker.now() + dt.timedelta(seconds=10))
            try:
                self.sync()
                left = sorted(self.positions)
                open_orders = sum(1 for o in self.orders if is_open(o))
                if not left and not open_orders:
                    break
            except Exception as ex:
                self.warn("Kontrolle nach Zwangsschluss fehlgeschlagen: %s" % ex)
        ok = left == [] and open_orders == 0
        self.log["flatten"] = {"time": now.strftime("%H:%M"), "dry_run": False,
                               "positions_left": left, "open_orders_left": open_orders,
                               "konto_leer": ok}
        if ok:
            self.note("-", "Kontrolle", "Konto ist leer: keine Positionen, keine offenen Orders", now=now)
        else:
            self.warn("NACH DEM ZWANGSSCHLUSS NICHT LEER: Positionen %s, offene Orders %s"
                      % (left, open_orders))
        self.finish_day(now)
        return 0 if ok else 2

    def finish_day(self, now):
        try:                              # SPY-Kurse fuer den Vergleich in bericht.py
            closes = self.broker.daily_closes("SPY", now)
            today = [c for d, c in closes if d == self.day]
            before = [c for d, c in closes if d < self.day]
            if today and before:
                self.log["spy_prev_close"], self.log["spy_close"] = before[-1], today[-1]
        except Exception as ex:
            self.warn("SPY-Kurs fuer den Vergleich nicht abrufbar (%s)" % ex)
        self.write_log()
        self.summary()

    def summary(self):
        lines = ["## %s - %s%s" % (self.name.capitalize(), self.day, " (DRY_RUN)" if self.dry_run else ""),
                 "- Kontowert: %.2f $ (heute %+.2f $)" % (self.equity, self.equity - self.last_equity)]
        for t in self.log.get("trades", []):
            lines.append("- %s %s %g Stueck: Einstieg %.2f, Ausstieg %s (%s), %+.2f $ = %+.2f R"
                         % (t["side"], t["symbol"], t["qty"], t["entry"], t["exit"], t["exit_reason"],
                            t["pnl_usd"], t["r"]))
        for o in self.dry_orders:
            lines.append("- DRY_RUN: %s %s %d Stueck, Limit %.2f, Stop %.2f, Ziel %.2f"
                         % (o["side"], o["symbol"], o["qty"], o["limit"], o["stop"], o["target"]))
        if not self.log.get("trades") and not self.dry_orders:
            lines.append("- keine Trades")
        lines += ["- WARNUNG: " + w["text"] for w in self.log.get("warnings", [])]
        text = "\n".join(lines)
        self.out(text)
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                f.write(text + "\n\n")

    # --- Ein Durchlauf pro Minute -------------------------------------------
    def step(self, now):
        """Rueckgabe None = weiterlaufen, sonst der Ende-Code des Tages."""
        if orb.must_flatten(now.time(), self.flatten_t):
            return self.flatten(now)
        self.sync()
        self.protect_positions()
        self.cancel_stale(now)
        self.manage_runners(now)
        last_bar = (floor_minute(now) - dt.timedelta(minutes=1)).time()
        if now.time() >= self.range_end and last_bar < self.last_entry:
            self.scan(now)
        return None

    def prepare(self, now):
        """Handelstag einrichten. Rueckgabe False = heute kein Handelstag."""
        self.day = now.date()
        self.load_state()
        self.load_log()
        sess = None
        for attempt in range(5):
            try:
                sess = self.broker.session(self.day)
                break
            except Exception as ex:
                self.warn("Handelskalender nicht abrufbar (Versuch %d): %s" % (attempt + 1, ex))
                self.broker.sleep_until(self.broker.now() + dt.timedelta(seconds=30))
        else:
            raise RuntimeError("Handelskalender nicht abrufbar - Abbruch ohne Orders")
        if sess is None:
            self.note("-", "kein Handel", "heute ist kein US-Handelstag", once=True, now=now)
            return False
        self.open_t, self.close_t = sess
        self.range_end = orb.add_minutes(orb.MARKET_OPEN, self.cfg["range_minutes"])
        self.last_entry, self.flatten_t = orb.session_times(self.cfg, FLATTEN_TIME,
                                                            FLATTEN_BEFORE_CLOSE_MIN, self.close_t)
        self.log["session"] = {"open": str(self.open_t), "close": str(self.close_t),
                               "range_end": str(self.range_end), "last_entry": str(self.last_entry),
                               "flatten": str(self.flatten_t)}
        in_time = any(s < self.range_end.strftime("%H:%M:%S") for s in self.log["starts"])
        self.log["starts"].append(now.strftime("%H:%M:%S"))
        if not in_time and now.time() >= self.range_end:
            self.no_entries = ("Bot wurde erst um %s gestartet, die Eroeffnungsspanne (bis %s) ist "
                               "verpasst - heute keine neuen Trades" % (now.strftime("%H:%M"),
                                                                        self.range_end.strftime("%H:%M")))
        if self.state["peak_equity"] <= 0:
            try:
                since = dt.date.fromisoformat(self.state["peak_since"]) if self.state["peak_since"] else None
                self.state["peak_equity"] = self.broker.equity_peak(since)
            except Exception as ex:
                self.warn("Hoechststand des Kontos nicht abrufbar (%s)" % ex)
        return True

    def run(self, until=None):
        now = self.broker.now()
        if not self.prepare(now):
            self.write_log()
            return 0
        self.out("%s: Start %s | Spanne bis %s | Einstiege bis %s | Zwangsschluss %s%s"
                 % (self.name, now.strftime("%H:%M:%S"), self.range_end.strftime("%H:%M"),
                    self.last_entry.strftime("%H:%M"), self.flatten_t.strftime("%H:%M"),
                    " | DRY_RUN" if self.dry_run else ""))
        if now.time() >= self.close_t:
            return self.check_empty(now)
        if now.time() < self.open_t:
            self.note("-", "warte", "Boerse oeffnet um %s" % self.open_t.strftime("%H:%M"),
                      once=True, now=now)
            self.write_log()
            self.broker.sleep_until(dt.datetime.combine(self.day, self.open_t, tzinfo=NY)
                                    + dt.timedelta(seconds=POLL_SECOND))
        while True:
            now = self.broker.now()
            if now.time() >= self.close_t:
                return self.check_empty(now)
            code = None
            try:
                code = self.step(now)
            except Exception as ex:       # Netzwerkfehler o. ae.: nichts tun, naechste Minute neu
                self.warn("Durchlauf %s uebersprungen (%s: %s) - keine neue Order"
                          % (now.strftime("%H:%M"), type(ex).__name__, ex))
            if code is not None:
                return code
            try:
                self.write_log()
            except Exception as ex:
                self.out("Protokoll konnte nicht geschrieben werden: %s" % clean(ex))
            if until and now.time() >= until:
                self.note("-", "Lauf endet", "planmaessig um %s, der naechste Job uebernimmt"
                          % until.strftime("%H:%M"), now=now)
                self.write_log()
                return 0
            self.broker.sleep_until(floor_minute(now) + dt.timedelta(minutes=1, seconds=POLL_SECOND))

    def check_empty(self, now):
        """Nach Boersenschluss: ist das Konto leer? Wenn nicht -> Ende-Code 2."""
        self.sync()
        left = sorted(self.positions)
        if left:
            self.warn("NACH BOERSENSCHLUSS NOCH OFFEN: %s" % ", ".join(left))
        else:
            self.note("-", "Kontrolle", "Konto ist leer: keine offenen Positionen", once=True, now=now)
        self.log.setdefault("flatten", {}).update({"check_after_close": now.strftime("%H:%M"),
                                                   "positions_left": left, "konto_leer": not left})
        self.write_log()
        return 2 if left else 0


# ===========================================================================
# Start
# ===========================================================================
def main(argv=None):
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Daytrading-Bot Falke / Rakete (nur Paper Trading)")
    ap.add_argument("bot", choices=list(DAY_BOTS))
    ap.add_argument("--bis", help="Lauf endet um diese Uhrzeit (HH:MM, New York)")
    ap.add_argument("--kontrolle", action="store_true", help="nur pruefen, ob das Konto leer ist")
    ap.add_argument("--freigeben", action="store_true", help="Konto-Notbremse freigeben")
    ap.add_argument("--replay", help="vergangenen Tag (JJJJ-MM-TT) im DRY_RUN nachspielen")
    args = ap.parse_args(argv)

    cfg = DAY_BOTS[args.bot]
    key, secret = os.environ.get(cfg["key_env"]), os.environ.get(cfg["secret_env"])
    if not key or not secret:
        print("FEHLER: %s / %s fehlen (lokal: Datei .env, GitHub: Repository-Secrets)."
              % (cfg["key_env"], cfg["secret_env"]))
        return 1
    dry = os.environ.get("DRY_RUN", "false").lower() == "true"
    try:
        if args.replay:
            broker = ReplayBroker(key, secret, dt.date.fromisoformat(args.replay))
            bot = DayBot(args.bot, cfg, broker, dry_run=True, log_dir=os.path.join("logs", "replay"))
            return bot.run()
        broker = AlpacaBroker(key, secret)
        bot = DayBot(args.bot, cfg, broker, dry_run=dry)
        if args.freigeben:
            bot.load_state()
            bot.release()
            return 0
        if args.kontrolle:
            now = broker.now()
            if not bot.prepare(now):
                bot.write_log()
                return 0
            bot.log["starts"].pop()       # eine Kontrolle zaehlt nicht als Start des Handels
            return bot.check_empty(now)
        return bot.run(until=orb.parse_time(args.bis) if args.bis else None)
    except Exception as ex:
        print("FEHLER - Lauf abgebrochen, keine weiteren Orders. %s: %s"
              % (type(ex).__name__, clean(ex)))
        return 1


if __name__ == "__main__":
    sys.exit(main())
