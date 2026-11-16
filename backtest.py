"""Backtest der Daytrading-Bots Falke und Rakete auf Minutenkerzen von Alpaca.

Aufruf:
    python backtest.py                 Falke und Rakete, 12 Monate, IEX-Daten
    python backtest.py --run falke     nur ein Bot
    python backtest.py --feed sip      vollstaendige Marktdaten (im Gratis-Plan fuer alles aelter als 15 Min.)
    python backtest.py --refresh       Kursdaten neu herunterladen
    python backtest.py --download-only nur Kursdaten laden

Gerechnet wird mit genau den Regeln aus config.py (dieselbe Logik wie der Live-Bot):
- Signal = erste Minutenkerze, die ausserhalb der Spanne schliesst.
- Einstieg erst NACH dem Signal: Limit-Order 0,1 % ueber/unter dem Signal-Schluss,
  gueltig fuer die naechsten 2 Minutenkerzen, sonst kein Trade.
- Slippage (config.SLIPPAGE) bei JEDER Order, zum Nachteil des Bots.
- Treffen Stop und Kursziel in dieselbe Minute, zaehlt der Stop (schlechter Fall).
  In der Einstiegs-Minute zaehlt nur der Stop, nie das Kursziel.
- Der nachgezogene Stop gilt erst ab der naechsten Kerze.
- Konto-Notbremse: einmal ausgeloest, handelt der Bot im Backtest nicht mehr
  (im echten Betrieb bis zur Freigabe).

Der Rechenkern (simulate_fill, simulate_exit, run_day, summarize) braucht kein
Internet und wird in tests/test_orb.py offline getestet.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time

import pandas as pd

import orb
import universe
from config import (BACKTEST_END, BACKTEST_MONTHS, DAY_BOTS, ENTRY_LIMIT_PCT, ENTRY_TIMEOUT_MIN,
                    FLATTEN_BEFORE_CLOSE_MIN, FLATTEN_TIME, MIN_RISK_PCT, OR_MIN_BARS_FRAC,
                    RELVOL_LOOKBACK_DAYS, RELVOL_MIN_DAYS, SLIPPAGE, START_EQUITY, TIMEZONE)

DATA_DIR = "data"
REPORT_DIR = "reports"


# ===========================================================================
# Rechenkern (offline testbar)
# ===========================================================================
def simulate_fill(bars, sig, limit, side, timeout_min=ENTRY_TIMEOUT_MIN):
    """Wird die Limit-Order in den `timeout_min` Minuten nach der Signalkerze ausgefuehrt?

    Rueckgabe (Zeilennummer, Preis ohne Slippage) oder None (Order wird storniert).
    """
    o, h, lo = bars["open"].to_numpy(), bars["high"].to_numpy(), bars["low"].to_numpy()
    t_sig = bars.index[sig]
    for j in range(sig + 1, len(bars)):
        if (bars.index[j] - t_sig).total_seconds() > timeout_min * 60:
            break
        if side == "long" and lo[j] <= limit:
            return j, min(float(o[j]), limit)
        if side == "short" and h[j] >= limit:
            return j, max(float(o[j]), limit)
    return None


def simulate_exit(bars, fill_pos, raw_fill, qty, plan, cfg, flatten, slip=SLIPPAGE):
    """Spielt einen ausgefuehrten Trade bis zum Ausstieg durch."""
    o, h = bars["open"].to_numpy(), bars["high"].to_numpy()
    lo, c = bars["low"].to_numpy(), bars["close"].to_numpy()
    t = bars.index.time
    side = plan["side"]
    long = side == "long"
    d = 1 if long else -1                 # Richtung: +1 Long, -1 Short
    entry = raw_fill * (1 + d * slip)     # Slippage immer zum Nachteil
    q_target, q_runner = orb.split_qty(qty, cfg["runner_fraction"])
    stop, trailing = plan["stop"], False
    best = raw_fill
    pnl, reason, exit_pos, exit_price = 0.0, None, None, None

    def close(q, price):                  # Position (teilweise) schliessen
        return d * q * (price * (1 - d * slip) - entry)

    for i in range(fill_pos, len(bars)):
        if t[i] >= flatten:               # Zwangsschluss: nie eine Position ueber Nacht
            exit_price = float(o[i])
            pnl += close(q_target + q_runner, exit_price)
            reason, exit_pos = "Zwangsschluss", i
            break
        if (lo[i] <= stop) if long else (h[i] >= stop):      # Stop zuerst (schlechter Fall)
            exit_price = min(stop, float(o[i])) if long else max(stop, float(o[i]))
            if i == fill_pos:             # in der Einstiegs-Minute: genau der Stop
                exit_price = stop
            pnl += close(q_target + q_runner, exit_price)
            reason, exit_pos = ("Nachgezogener Stop" if trailing else "Stop"), i
            break
        if i > fill_pos:                  # Kursziele erst ab der Minute nach dem Einstieg
            if q_target and ((h[i] >= plan["target"]) if long else (lo[i] <= plan["target"])):
                pnl += close(q_target, plan["target"])
                q_target = 0
                reason, exit_pos, exit_price = "Kursziel", i, plan["target"]
            rt = plan["runner_target"]
            if q_runner and rt and ((h[i] >= rt) if long else (lo[i] <= rt)):
                pnl += close(q_runner, rt)
                q_runner = 0
                reason, exit_pos, exit_price = "Not-Ziel 5R", i, rt
            if q_target + q_runner == 0:
                break
        best = max(best, float(h[i])) if long else min(best, float(lo[i]))
        if q_target == 0 and q_runner:    # Teilverkauf ist erfolgt -> Stop nachziehen
            new = orb.trail_stop(cfg, entry, plan["risk"], best, stop, side)
            if new:                       # gilt erst ab der naechsten Kerze
                stop, trailing = new, True
    else:                                 # Daten enden vor dem Zwangsschluss
        i = len(bars) - 1
        exit_price = float(c[i])
        pnl += close(q_target + q_runner, exit_price)
        reason, exit_pos = "Datenende", i

    return {"side": side, "entry": entry, "qty": qty, "pnl": pnl, "exit_reason": reason,
            "exit": exit_price, "exit_time": bars.index[exit_pos],
            "r": orb.r_multiple(pnl, qty, entry, plan["stop"])}


def run_day(cfg, day_bars, context, equity, session_close=orb.REGULAR_CLOSE, slip=SLIPPAGE,
            short_ok=frozenset(), halted=False):
    """Ein Handelstag fuer einen Bot. day_bars = {Symbol: Minutenkerzen des Tages}.

    context  = {Symbol: {"prev_close": ..., "avg_or_vol": ...}} (nur fuer den Rakete-Filter).
    short_ok = Symbole, die leerverkauft werden duerfen (shortable + easy-to-borrow).
    Rueckgabe: Liste der Trades des Tages.
    """
    if halted:
        return []
    last_entry, flatten = orb.session_times(cfg, FLATTEN_TIME, FLATTEN_BEFORE_CLOSE_MIN,
                                            session_close)
    cands = []
    for sym, bars in day_bars.items():
        rng = orb.opening_range(bars, cfg["range_minutes"], OR_MIN_BARS_FRAC)
        if rng is None or not orb.range_wide_enough(rng, cfg["min_range_pct"]):
            continue                      # keine oder zu enge Spanne -> kein Trade
        ctx = context.get(sym) or {}
        side, note = orb.pick_side(cfg, orb.gap_pct(rng["open"], ctx.get("prev_close")),
                                   orb.relative_volume(rng["volume"], ctx.get("avg_or_vol")))
        if side is None:
            continue
        sig = orb.find_breakout(bars, rng, cfg["range_minutes"], last_entry, side)
        if sig is not None:
            cands.append((bars.index[sig], sym, sig, rng, note, side))
    cands.sort(key=lambda x: (x[0], x[1]))    # wer zuerst ausbricht, kommt zuerst dran

    trades = []
    for ts, sym, sig, rng, note, side in cands:
        closed = [t for t in trades if t["exit_time"] <= ts]
        still_open = [t for t in trades if t["exit_time"] > ts]
        realized = sum(t["pnl"] for t in closed)
        unrealized = 0.0
        for t in still_open:              # offene Trades zum letzten bekannten Kurs bewerten
            b = day_bars[t["symbol"]]
            past = b["close"][b.index <= ts]
            if len(past):
                direction = 1 if t["side"] == "long" else -1
                unrealized += direction * t["qty"] * (float(past.iloc[-1]) - t["entry"])
        eq_now = equity + realized + unrealized
        ok, _ = orb.entry_allowed(cfg, len(trades), equity, eq_now, sym,
                                  {t["symbol"] for t in trades})
        if not ok:
            break                         # Tagesgrenze oder Trade-Limit: Schluss fuer heute
        bars = day_bars[sym]
        limit = orb.entry_limit(float(bars["close"].iloc[sig]), side, ENTRY_LIMIT_PCT)
        if side == "short" and not orb.short_allowed(cfg, limit, sym in short_ok)[0]:
            continue
        plan = orb.plan_trade(cfg, limit, rng, side, MIN_RISK_PCT)
        if plan is None:
            continue
        power = orb.buying_power(eq_now, sum(t["qty"] * t["entry"] for t in still_open),
                                 cfg["leverage"])
        qty = orb.size_position(eq_now, power, cfg["risk_pct"], limit, plan["risk"],
                                cfg["max_pos_pct"])
        if qty < 1:
            continue
        fill = simulate_fill(bars, sig, limit, side)
        if fill is None:
            continue                      # Limit nicht erreicht -> Order storniert, kein Trade
        res = simulate_exit(bars, fill[0], fill[1], qty, plan, cfg, flatten, slip)
        res.update({"symbol": sym, "signal_time": ts, "entry_time": bars.index[fill[0]],
                    "stop": plan["stop"], "target": plan["target"], "note": note,
                    "risk_pct": qty * plan["risk"] / eq_now * 100,   # geplant riskiert
                    "exposure_pct": qty * res["entry"] / eq_now * 100})
        trades.append(res)
    return trades


def max_drawdown(values):
    """Groesster Rueckgang vom bisherigen Hoechststand (0.08 = -8 %)."""
    peak, worst = None, 0.0
    for v in values:
        peak = v if peak is None else max(peak, v)
        if peak > 0:
            worst = max(worst, 1 - v / peak)
    return worst


def trade_stats(trades):
    """Kennzahlen nur aus der Trade-Liste (auch fuer Long und Short getrennt)."""
    n = len(trades)
    wins = [t for t in trades if t["pnl"] > 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    return {
        "trades": n,
        "win_rate": len(wins) / n if n else 0.0,
        "avg_r": sum(t["r"] for t in trades) / n if n else 0.0,
        "total_r": sum(t["r"] for t in trades),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else None,
        "pnl_usd": sum(t["pnl"] for t in trades),
        "avg_risk_pct": sum(t.get("risk_pct", 0.0) for t in trades) / n if n else 0.0,
    }


def summarize(trades, equity_curve, start_equity):
    """Kennzahlen aus Trade-Liste und taeglicher Kontokurve, Long und Short getrennt."""
    end = equity_curve[-1] if equity_curve else start_equity
    out = trade_stats(trades)
    out.update({
        "return_pct": (end / start_equity - 1) * 100,
        "max_drawdown_pct": max_drawdown([start_equity] + list(equity_curve)) * 100,
        "end_equity": end,
        "long": trade_stats([t for t in trades if t.get("side", "long") == "long"]),
        "short": trade_stats([t for t in trades if t.get("side") == "short"]),
    })
    return out


def verdict(stats, spy_return_pct, halted_on=None):
    """Ehrliche Einordnung in einfachen Worten."""
    out = []
    if halted_on:
        out.append("Konto-Notbremse am %s ausgeloest - danach wurde nicht mehr gehandelt." % halted_on)
    if stats["trades"] < 30:
        out.append("Zu wenige Trades (%d) - das Ergebnis beweist nichts." % stats["trades"])
    if stats["avg_r"] <= 0:
        out.append("Die Strategie verliert im Schnitt Geld pro Trade (%.2f R)." % stats["avg_r"])
    elif stats["avg_r"] < 0.1:
        out.append("Der Vorteil ist winzig (%.2f R pro Trade) und kann Zufall sein." % stats["avg_r"])
    else:
        out.append("Positiver Erwartungswert (%.2f R pro Trade) im Testzeitraum." % stats["avg_r"])
    if stats["return_pct"] < spy_return_pct:
        out.append("Schlechter als 'SPY einfach halten' (%.1f %% gegen %.1f %%)."
                   % (stats["return_pct"], spy_return_pct))
    else:
        out.append("Besser als 'SPY einfach halten' (%.1f %% gegen %.1f %%)."
                   % (stats["return_pct"], spy_return_pct))
    return out


# ===========================================================================
# Daten laden (braucht Internet und Alpaca-Keys aus der Umgebung / .env)
# ===========================================================================
def _keys():
    for name in ("FALKE", "RAKETE", "FELS"):
        k, s = os.environ.get("ALPACA_%s_KEY" % name), os.environ.get("ALPACA_%s_SECRET" % name)
        if k and s:
            return k, s
    sys.exit("FEHLER: Keine Alpaca-Keys gefunden. Trage sie in die Datei .env ein "
             "(Vorlage: .env.example).")


def load_bars(symbols, start, end, feed, refresh=False):
    """Minutenkerzen je Symbol (nur regulaere Handelszeit), mit Zwischenspeicher in data/."""
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    folder = os.path.join(DATA_DIR, feed)
    os.makedirs(folder, exist_ok=True)
    tag = "%s_%s" % (start.date(), end.date())
    dc, out = None, {}
    for n, sym in enumerate(symbols, 1):
        path = os.path.join(folder, "%s_%s.pkl" % (sym.replace(".", "-"), tag))
        if os.path.exists(path) and not refresh:
            out[sym] = pd.read_pickle(path)
            continue
        if dc is None:
            dc = StockHistoricalDataClient(*_keys())
        df = None
        for attempt in range(4):
            try:
                req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Minute,
                                       start=start, end=end, feed=DataFeed(feed),
                                       adjustment=Adjustment.SPLIT)
                df = dc.get_stock_bars(req).df
                break
            except Exception as e:
                print("  %s: Versuch %d fehlgeschlagen (%s)" % (sym, attempt + 1, str(e)[:100]))
                time.sleep(5 * (attempt + 1))
        if df is None or df.empty:
            print("  %s: keine Daten, wird uebersprungen" % sym)
            continue
        df = df.xs(sym).sort_index()
        df.index = pd.to_datetime(df.index).tz_convert(TIMEZONE)
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        tt = df.index.time
        df = df[(tt >= orb.MARKET_OPEN) & (tt < orb.REGULAR_CLOSE)]
        df.to_pickle(path)
        out[sym] = df
        print("  [%d/%d] %s: %d Minutenkerzen geladen" % (n, len(symbols), sym, len(df)))
    return out


def load_calendar(start, end):
    """{Datum: Boersenschluss} von Alpaca (Feiertage, verkuerzte Handelstage)."""
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import GetCalendarRequest

    path = os.path.join(DATA_DIR, "calendar_%s_%s.json" % (start.date(), end.date()))
    if os.path.exists(path):
        with open(path) as f:
            raw = json.load(f)
    else:
        tc = TradingClient(*_keys(), paper=True)      # paper=True fest: kein Echtgeld
        days = tc.get_calendar(GetCalendarRequest(start=start.date(), end=end.date()))
        raw = {str(d.date): d.close.strftime("%H:%M") for d in days}
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump(raw, f)
    return {dt.date.fromisoformat(k): orb.parse_time(v) for k, v in raw.items()}


def load_shortable(symbols, refresh=False):
    """Symbole, die Alpaca HEUTE als shortable UND easy-to-borrow meldet.

    Achtung: Das ist der heutige Stand. Wie es vor Monaten war, liefert Alpaca nicht.
    """
    from alpaca.trading.client import TradingClient

    path = os.path.join(DATA_DIR, "shortable.json")
    known = {}
    if os.path.exists(path) and not refresh:
        with open(path) as f:
            known = json.load(f)
    missing = [s for s in symbols if s not in known]
    if missing:
        tc = TradingClient(*_keys(), paper=True)      # paper=True fest: kein Echtgeld
        for s in missing:
            try:
                a = tc.get_asset(s)
                from daybot import easy_borrow
                known[s] = bool(a.shortable and easy_borrow(a))
            except Exception as e:
                print("  %s: Short-Status nicht abrufbar (%s) -> kein Short" % (s, str(e)[:80]))
                known[s] = False
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump(known, f)
    return frozenset(s for s in symbols if known.get(s))


def split_days(df):
    return {day: d for day, d in df.groupby(df.index.date)}


def build_context(days_by_sym, cfg):
    """Fuer den Rakete-Filter: je Symbol und Tag Vortagesschluss und Durchschnitts-Volumen
    der Spanne aus den Tagen DAVOR (kein Blick in die Zukunft)."""
    ctx = {}
    for sym, days in days_by_sym.items():
        prev_close, vols = None, []
        for day in sorted(days):
            bars = days[day]
            recent = vols[-RELVOL_LOOKBACK_DAYS:]
            if prev_close is not None and len(recent) >= RELVOL_MIN_DAYS:
                ctx.setdefault(day, {})[sym] = {"prev_close": prev_close,
                                                "avg_or_vol": sum(recent) / len(recent)}
            t = bars.index.time
            first = bars[(t >= orb.MARKET_OPEN) & (t < orb.add_minutes(orb.MARKET_OPEN, cfg["range_minutes"]))]
            if len(first):
                vols.append(float(first["volume"].sum()))
            prev_close = float(bars["close"].iloc[-1])
    return ctx


# ===========================================================================
# Hauptprogramm
# ===========================================================================
def backtest_run(cfg, days_by_sym, calendar, test_days, short_ok=frozenset()):
    """Rueckgabe: (Trades, taegliche Kontokurve, Datum der Notbremse oder None)."""
    syms = [s for s in getattr(universe, cfg["universe"]) if s in days_by_sym]
    ctx = build_context({s: days_by_sym[s] for s in syms}, cfg) if cfg["min_gap_pct"] is not None else {}
    equity, peak, curve, trades, halted_on = START_EQUITY, START_EQUITY, [], [], None
    for day in test_days:
        day_bars = {s: days_by_sym[s][day] for s in syms if day in days_by_sym[s]}
        todays = run_day(cfg, day_bars, ctx.get(day, {}), equity,
                         calendar.get(day, orb.REGULAR_CLOSE), short_ok=short_ok,
                         halted=halted_on is not None)
        equity += sum(t["pnl"] for t in todays)
        peak = max(peak, equity)
        curve.append(equity)
        for t in todays:
            t["date"] = str(day)
        trades += todays
        if halted_on is None and orb.drawdown_halt(peak, equity, cfg["dd_halt"]):
            halted_on = str(day)
    return trades, curve, halted_on


def _fmt(st, key):
    if key == "profit_factor":
        return "%.2f" % st[key] if st.get(key) else "-"
    return {"trades": "%d", "win_rate": "%.1f %%", "avg_r": "%+.3f R", "total_r": "%+.1f R",
            "pnl_usd": "%+.0f $", "avg_risk_pct": "%.2f %%", "return_pct": "%+.2f %%",
            "max_drawdown_pct": "%.2f %%", "end_equity": "%.0f $"}[key] % (
                st[key] * 100 if key == "win_rate" else st[key])


ROWS = [("Trades", "trades"), ("Trefferquote", "win_rate"), ("Schnitt pro Trade", "avg_r"),
        ("Summe", "total_r"), ("Profit-Faktor", "profit_factor"),
        ("Gewinn/Verlust", "pnl_usd"), ("Geplantes Risiko/Trade", "avg_risk_pct"),
        ("Ergebnis", "return_pct"), ("Max. Drawdown", "max_drawdown_pct"),
        ("Kontostand", "end_equity")]


def print_table(results, spy=None):
    """Alle Laeufe nebeneinander. results = [(Titel, Kennzahlen), ...]"""
    cols = []
    for title, st in results:
        cols.append((title, st))
        if st["short"]["trades"] or "short" in title.lower():
            cols.append(("davon Long", st["long"]))
            cols.append(("davon Short", st["short"]))
    if spy:
        cols.append(("SPY halten", spy))
    print("\n%-24s" % "" + "".join("%15s" % c[0] for c in cols))
    for label, key in ROWS:
        print("%-24s" % label + "".join("%15s" % (_fmt(c[1], key) if key in c[1] else "")
                                        for c in cols))


def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    ap = argparse.ArgumentParser(description="Backtest Falke / Rakete")
    ap.add_argument("--run", choices=list(DAY_BOTS) + ["alle"], default="alle")
    ap.add_argument("--feed", choices=["iex", "sip"], default="iex")
    ap.add_argument("--months", type=int, default=BACKTEST_MONTHS)
    ap.add_argument("--end", default=BACKTEST_END,
                    help="Letzter Tag (JJJJ-MM-TT). Fest in config.py, damit der Daten-Zwischenspeicher passt.")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--download-only", action="store_true", help="nur Kursdaten laden")
    args = ap.parse_args()

    tz = dt.timezone.utc
    end = dt.datetime.combine(dt.date.fromisoformat(args.end), dt.time(), tzinfo=tz)
    test_start = end - dt.timedelta(days=round(args.months * 30.44))
    start = test_start - dt.timedelta(days=45)        # Vorlauf fuer das relative Volumen
    runs = list(DAY_BOTS) if args.run == "alle" else [args.run]
    if args.download_only:
        runs = list(DAY_BOTS)
    symbols = sorted({"SPY"} | {s for b in runs for s in getattr(universe, DAY_BOTS[b]["universe"])})

    print("Lade Minutenkerzen (%s) fuer %d Symbole, %s bis %s ..."
          % (args.feed.upper(), len(symbols), start.date(), end.date()))
    bars = load_bars(symbols, start, end, args.feed, args.refresh)
    if "SPY" not in bars:
        sys.exit("FEHLER: keine SPY-Daten - Vergleich nicht moeglich.")
    calendar = load_calendar(start, end)
    short_ok = frozenset()
    if any("short" in DAY_BOTS[b]["sides"] for b in runs):
        short_ok = load_shortable(symbols, args.refresh)
        print("Short erlaubt (shortable + easy-to-borrow, Stand heute): %d von %d Symbolen"
              % (len(short_ok), len(symbols)))
    if args.download_only:
        print("Daten geladen: %d Symbole." % len(bars))
        return
    days_by_sym = {s: split_days(d) for s, d in bars.items()}
    test_days = sorted(d for d in days_by_sym["SPY"] if d >= test_start.date())

    spy_days = [days_by_sym["SPY"][d] for d in test_days]
    spy_start = float(spy_days[0]["open"].iloc[0])
    spy_curve = [START_EQUITY * float(d["close"].iloc[-1]) / spy_start for d in spy_days]
    spy = {"return_pct": (spy_curve[-1] / START_EQUITY - 1) * 100,
           "max_drawdown_pct": max_drawdown([START_EQUITY] + spy_curve) * 100,
           "end_equity": spy_curve[-1]}

    os.makedirs(REPORT_DIR, exist_ok=True)
    print("\nZeitraum: %s bis %s (%d Handelstage), Daten: %s, Slippage %.2f %% pro Order"
          % (test_days[0], test_days[-1], len(test_days), args.feed.upper(), SLIPPAGE * 100))
    results = []
    for name in runs:
        cfg = DAY_BOTS[name]
        trades, curve, halted_on = backtest_run(cfg, days_by_sym, calendar, test_days, short_ok)
        st = summarize(trades, curve, START_EQUITY)
        reasons = {}
        for t in trades:
            reasons[t["exit_reason"]] = reasons.get(t["exit_reason"], 0) + 1
        notes = verdict(st, spy["return_pct"], halted_on)
        results.append((name.capitalize(), st))
        print("\n=== %s ===" % name.capitalize())
        print("Ausstiege: %s" % ", ".join("%s %d" % kv for kv in sorted(reasons.items())))
        for line in notes:
            print("-> " + line)
        base = os.path.join(REPORT_DIR, "backtest_%s_%s" % (name, args.feed))
        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump({"run": name, "feed": args.feed, "from": str(test_days[0]),
                       "to": str(test_days[-1]), "slippage": SLIPPAGE, "stats": st,
                       "exit_reasons": reasons, "halted_on": halted_on,
                       "spy_buy_and_hold": spy, "verdict": notes,
                       "rules": {k: v for k, v in cfg.items() if "env" not in k}},
                      f, indent=2, ensure_ascii=False)
        pd.DataFrame(trades).to_csv(base + "_trades.csv", index=False)
        pd.DataFrame({"date": [str(d) for d in test_days], "equity": curve}).to_csv(
            base + "_equity.csv", index=False)
    print_table(results, spy)
    print("\nBerichte liegen in %s/." % REPORT_DIR)
    print("Hinweise: Die Aktienlisten und der Short-Status sind von heute (Aktien, die inzwischen"
          "\nabgestuerzt sind, fehlen evtl. -> Ergebnis eher zu gut). Leihgebuehren und Zinsen fuer"
          "\nKredit sind nicht eingerechnet. Vergangene Ergebnisse sagen nichts Sicheres ueber die Zukunft.")


if __name__ == "__main__":
    main()
