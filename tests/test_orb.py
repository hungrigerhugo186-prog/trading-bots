"""Offline-Tests der Daytrading-Logik (Falke, Rakete) mit kuenstlichen Minutenkerzen.

Aufruf: python tests/test_orb.py      (kein Internet, keine Keys noetig)
"""
import datetime as dt
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import orb  # noqa: E402
from backtest import (max_drawdown, run_day, simulate_exit, simulate_fill,  # noqa: E402
                      summarize)
from config import (DAY_BOTS, ENTRY_LIMIT_PCT, FLATTEN_BEFORE_CLOSE_MIN,  # noqa: E402
                    FLATTEN_TIME, MIN_RISK_PCT)

FALKE, RAKETE = DAY_BOTS["falke"], DAY_BOTS["rakete"]
T = dt.time
DAY = "2026-01-05"


def day(price=100.0, vol=1000.0, start="09:30", end="16:00", date=DAY):
    """Ein ruhiger Handelstag: jede Minute dieselbe Kerze zum Preis `price`."""
    idx = pd.date_range("%s %s" % (date, start), "%s %s" % (date, end), freq="1min",
                        tz="America/New_York", inclusive="left")
    return pd.DataFrame({"open": price, "high": price, "low": price, "close": price,
                         "volume": vol}, index=idx)


def put(df, hhmm, o, h, lo, c, v=None):
    """Ueberschreibt die Kerze um `hhmm`."""
    ts = pd.Timestamp("%s %s" % (df.index[0].date(), hhmm), tz="America/New_York")
    df.loc[ts, ["open", "high", "low", "close"]] = [o, h, lo, c]
    if v is not None:
        df.loc[ts, "volume"] = v
    return df


def fill(df, start, end, price):
    """Setzt alle Kerzen von `start` (inkl.) bis `end` (exkl.) auf `price`."""
    t = df.index.time
    m = (t >= orb.parse_time(start)) & (t < orb.parse_time(end))
    df.loc[m, ["open", "high", "low", "close"]] = price
    return df


def pos(df, hhmm):
    """Zeilennummer der Kerze um `hhmm`."""
    return list(df.index.time).index(orb.parse_time(hhmm))


def falke_day(date=DAY):
    """Spanne 99-101 (Mitte 100), Ausbruch um 09:50 (Schluss 101.5), danach 101.5."""
    d = day(100.0, date=date)
    put(d, "09:31", 100, 101, 100, 100.5)
    put(d, "09:40", 100, 100.5, 99, 100)
    put(d, "09:50", 100.5, 101.6, 100.5, 101.5)
    fill(d, "09:51", "16:00", 101.5)
    return d


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


# --- Regeln aus dem Auftrag stehen wirklich so in config.py ------------------
def test_config_rules():
    assert FALKE["sides"] == ("long",) and FALKE["leverage"] == 1.0
    assert (FALKE["range_minutes"], FALKE["risk_pct"], FALKE["max_trades_per_day"]) == (15, 0.0025, 2)
    assert (FALKE["max_pos_pct"], FALKE["daily_loss_limit"], FALKE["dd_halt"]) == (0.25, 0.0075, 0.05)
    assert RAKETE["sides"] == ("long", "short") and RAKETE["leverage"] == 2.0
    assert (RAKETE["range_minutes"], RAKETE["risk_pct"], RAKETE["max_trades_per_day"]) == (5, 0.01, 4)
    assert (RAKETE["max_pos_pct"], RAKETE["daily_loss_limit"], RAKETE["dd_halt"]) == (0.50, 0.03, 0.25)
    assert (RAKETE["min_gap_pct"], RAKETE["min_rel_volume"], RAKETE["min_short_price"]) == (0.02, 2.0, 10.0)
    assert FALKE["min_range_pct"] == RAKETE["min_range_pct"] == 0.003
    assert ENTRY_LIMIT_PCT == 0.001 and FLATTEN_TIME == "15:50"


# --- Spanne berechnen -------------------------------------------------------
def test_opening_range():
    d = falke_day()
    put(d, "09:45", 100, 150, 50, 100)                # 09:45 gehoert NICHT mehr zur 15-Min-Spanne
    r = orb.opening_range(d, 15)
    assert (r["high"], r["low"], r["mid"]) == (101.0, 99.0, 100.0), r
    assert r["open"] == 100.0 and r["volume"] == 15 * 1000.0
    r5 = orb.opening_range(d, 5)                      # 5-Min-Spanne sieht das Tief von 09:40 nicht
    assert (r5["high"], r5["low"]) == (101.0, 100.0), r5
    # zu wenige Kerzen (Datenluecke) -> keine Spanne, also kein Trade
    assert orb.opening_range(d[d.index.time >= T(9, 40)], 15) is None
    assert orb.opening_range(day(100.0), 15) is None  # Hoch = Tief: keine brauchbare Spanne


# --- Mindestbreite der Spanne -----------------------------------------------
def test_range_filter():
    assert orb.range_wide_enough({"high": 100.2, "low": 100.0, "mid": 100.1}, 0.003) is False  # 0,2 %
    assert orb.range_wide_enough({"high": 100.4, "low": 100.0, "mid": 100.2}, 0.003) is True   # 0,4 %
    assert orb.range_wide_enough({"high": 100.3, "low": 100.0, "mid": 100.15}, 0.003) is False  # knapp drunter
    assert orb.range_wide_enough({"high": 100.2, "low": 100.0, "mid": 100.1}, None) is True    # Filter aus
    # Ganzer Tag: Spanne nur 0,2 % breit -> trotz sauberem Ausbruch kein Trade
    d = day(100.0)
    put(d, "09:31", 100, 100.2, 100, 100.1)
    put(d, "09:50", 100.1, 100.6, 100.1, 100.5)
    fill(d, "09:51", "16:00", 100.5)
    assert run_day(FALKE, {"AAA": d}, {}, 10_000.0, slip=0.0) == []
    assert len(run_day(dict(FALKE, min_range_pct=0.001), {"AAA": d}, {}, 10_000.0, slip=0.0)) == 1
    assert len(run_day(FALKE, {"AAA": falke_day()}, {}, 10_000.0, slip=0.0)) == 1   # 2 % breit


# --- Long- und Short-Signal -------------------------------------------------
def test_find_breakout():
    d = falke_day()
    r = orb.opening_range(d, 15)
    sig = orb.find_breakout(d, r, 15, T(11, 30))
    assert d.index[sig].time() == T(9, 50)
    assert orb.find_breakout(d, r, 15, T(11, 30), "short") is None
    # Docht ueber dem Hoch, aber Schluss darunter -> kein Signal
    w = day(100.0)
    put(w, "09:31", 100, 101, 99, 100)
    put(w, "09:50", 100, 102, 100, 100.9)
    assert orb.find_breakout(w, orb.opening_range(w, 15), 15, T(11, 30)) is None
    # Signalkerze 11:29 ist um 11:30 fertig -> gilt gerade noch; 11:30 ist zu spaet
    late = day(100.0)
    put(late, "09:31", 100, 101, 99, 100)
    put(late, "11:30", 100, 102, 100, 101.5)
    assert orb.find_breakout(late, orb.opening_range(late, 15), 15, T(11, 30)) is None
    put(late, "11:29", 100, 102, 100, 101.5)
    assert late.index[orb.find_breakout(late, orb.opening_range(late, 15), 15, T(11, 30))].time() == T(11, 29)
    # Kurs steigt schon INNERHALB der Spanne: erst die erste Kerze danach zaehlt
    inside = day(100.0)
    put(inside, "09:31", 100, 101, 99, 100)
    put(inside, "09:44", 100, 103, 100, 103)
    fill(inside, "09:45", "16:00", 100.0)
    assert orb.find_breakout(inside, orb.opening_range(inside, 15), 15, T(11, 30)) is None
    # Short: Schluss unter dem Tief der 5-Minuten-Spanne
    s = day(103.0)
    put(s, "09:31", 103, 104, 102, 103)
    put(s, "09:40", 103, 103, 101.4, 101.5)
    r5 = orb.opening_range(s, 5)
    assert s.index[orb.find_breakout(s, r5, 5, T(11, 30), "short")].time() == T(9, 40)
    assert orb.find_breakout(s, r5, 5, T(11, 30), "long") is None


# --- Limit-Preis, Stop und Kursziele ----------------------------------------
def test_plan_trade():
    assert orb.entry_limit(100.0, "long", 0.001) == 100.1       # 0,1 % ueber dem Schluss
    assert orb.entry_limit(100.0, "short", 0.001) == 99.9       # 0,1 % darunter
    rng = {"high": 101.0, "low": 99.0, "mid": 100.0}
    p = orb.plan_trade(FALKE, 101.6, rng, "long", MIN_RISK_PCT)              # Falke: Stop = Mitte
    assert p["stop"] == 100.0 and approx(p["risk"], 1.6) and p["target"] == 104.8
    assert p["runner_target"] is None
    p = orb.plan_trade(RAKETE, 101.5, rng, "long", MIN_RISK_PCT)             # Rakete Long: Stop = Tief
    assert p["stop"] == 99.0 and approx(p["risk"], 2.5)
    assert p["target"] == 106.5 and p["runner_target"] == 114.0              # 2R und 5R
    p = orb.plan_trade(RAKETE, 98.5, rng, "short", MIN_RISK_PCT)             # Rakete Short: Stop = Hoch
    assert p["stop"] == 101.0 and approx(p["risk"], 2.5)
    assert p["target"] == 93.5 and p["runner_target"] == 86.0                # Ziele UNTER dem Kurs
    # Stop zu nah am Kurs (unter 0,1 %) oder auf der falschen Seite -> kein Trade
    assert orb.plan_trade(FALKE, 100.05, rng, "long", MIN_RISK_PCT) is None
    assert orb.plan_trade(FALKE, 99.5, rng, "long", MIN_RISK_PCT) is None
    assert orb.plan_trade(RAKETE, 101.5, rng, "short", MIN_RISK_PCT) is None


# --- Positionsgroesse mit allen Obergrenzen ---------------------------------
def test_size_position():
    # Falke: 10.000 $ x 0,25 % = 25 $ Risiko; Stop 1,60 $ entfernt -> 15 Stueck
    assert orb.size_position(10_000, 10_000, 0.0025, 101.6, 1.6, 0.25) == 15
    # Rakete: 1 % = 100 $ Risiko; Stop 2,60 $ entfernt -> 38 Stueck
    assert orb.size_position(10_000, 20_000, 0.01, 104.6, 2.6, 0.50) == 38
    # Deckel pro Aktie: Falke 25 % = 2.500 $, Rakete 50 % = 5.000 $ (Aktie 500 $, enger Stop)
    assert orb.size_position(10_000, 10_000, 0.0025, 500.0, 1.0, 0.25) == 5
    assert orb.size_position(10_000, 20_000, 0.01, 500.0, 1.0, 0.50) == 10
    # Kaufkraft: nur noch 1.000 $ frei -> hoechstens 9 Stueck zu 104,60 $
    assert orb.size_position(10_000, 1_000, 0.01, 104.6, 2.6, 0.50) == 9
    assert orb.size_position(10_000, 0, 0.01, 104.6, 2.6, 0.50) == 0
    assert orb.size_position(10_000, 10_000, 0.01, 100.0, 0.0, 0.50) == 0     # kein Stop-Abstand
    assert orb.size_position(10_000, 10_000, 0.0025, 3000.0, 30.0, 0.25) == 0  # Aktie zu teuer
    # Teilverkauf: Haelfte ans Ziel, Rest laeuft; bei 1 Aktie kein Teilverkauf
    assert orb.split_qty(38, 0.5) == (19, 19)
    assert orb.split_qty(5, 0.5) == (3, 2)
    assert orb.split_qty(1, 0.5) == (1, 0)
    assert orb.split_qty(15, 0.0) == (15, 0)


# --- Kredit-Grenze ----------------------------------------------------------
def test_buying_power():
    assert orb.buying_power(10_000, 4_000, 1.0) == 6_000        # Falke: kein Kredit
    assert orb.buying_power(10_000, 10_000, 1.0) == 0.0
    assert orb.buying_power(10_000, 4_000, 2.0) == 16_000       # Rakete: bis 2 x Kontowert
    assert orb.buying_power(10_000, 20_000, 2.0) == 0.0
    assert orb.buying_power(10_000, 25_000, 2.0) == 0.0


# --- Filter "Aktien in Bewegung" und Short-Erlaubnis -------------------------
def test_pick_side():
    assert approx(orb.gap_pct(103.0, 100.0), 0.03)
    assert orb.relative_volume(5000, 2000) == 2.5
    assert orb.pick_side(RAKETE, 0.03, 2.5)[0] == "long"         # Luecke hoch -> Long
    assert orb.pick_side(RAKETE, -0.03, 2.5)[0] == "short"       # Luecke runter -> Short
    assert orb.pick_side(RAKETE, 0.019, 5.0)[0] is None          # Luecke zu klein
    assert orb.pick_side(RAKETE, -0.019, 5.0)[0] is None
    assert orb.pick_side(RAKETE, 0.05, 1.9)[0] is None           # Volumen zu duenn
    assert orb.pick_side(RAKETE, None, 5.0)[0] is None           # Daten fehlen -> im Zweifel nein
    assert orb.pick_side(dict(RAKETE, sides=("long",)), -0.03, 2.5)[0] is None
    assert orb.pick_side(FALKE, None, None)[0] == "long"         # Falke hat keinen Filter
    assert orb.short_allowed(RAKETE, 50.0, True)[0] is True
    assert orb.short_allowed(RAKETE, 50.0, False)[0] is False    # nicht shortable/easy-to-borrow
    assert orb.short_allowed(RAKETE, 10.0, True)[0] is False     # Kurs muss UEBER 10 $ liegen
    assert orb.short_allowed(RAKETE, 10.01, True)[0] is True


def test_context_from_bars():
    frames = []
    for i in range(12):                                          # 12 Vortage, 5-Minuten-Kerzen
        d = "2025-12-%02d" % (10 + i)
        idx = pd.date_range(d + " 09:30", d + " 16:00", freq="5min", tz="America/New_York",
                            inclusive="left")
        frames.append(pd.DataFrame({"open": 50.0, "high": 50.0, "low": 50.0, "close": 50.0 + i,
                                    "volume": 100.0}, index=idx))
        frames[-1].iloc[0, frames[-1].columns.get_loc("volume")] = 1000.0 + 100 * i
    bars = pd.concat(frames)
    c = orb.context_from_bars(bars, dt.date(2025, 12, 22), 20, 10)
    assert c["prev_close"] == 61.0                               # Schluss des letzten Vortags
    assert approx(c["avg_or_vol"], sum(1000.0 + 100 * i for i in range(12)) / 12)
    assert orb.context_from_bars(bars, dt.date(2025, 12, 15), 20, 10) is None   # zu wenig Historie
    assert orb.context_from_bars(bars, dt.date(2025, 12, 1), 20, 10) is None


# --- Tagesgrenze und Konto-Notbremse ----------------------------------------
def test_daily_limits():
    assert orb.daily_loss_hit(10_000, 9_925.0, 0.0075) is True      # Falke: genau -0,75 %
    assert orb.daily_loss_hit(10_000, 9_925.01, 0.0075) is False
    assert orb.daily_loss_hit(10_000, 9_700.0, 0.03) is True        # Rakete: -3 %
    assert orb.daily_loss_hit(10_000, 9_701.0, 0.03) is False
    assert orb.entry_allowed(FALKE, 0, 10_000, 10_000, "AAPL", set())[0] is True
    assert orb.entry_allowed(FALKE, 2, 10_000, 10_050, "AAPL", set())[0] is False   # 2 Trades voll
    assert orb.entry_allowed(RAKETE, 3, 10_000, 10_000, "AMD", set())[0] is True
    assert orb.entry_allowed(RAKETE, 4, 10_000, 10_000, "AMD", set())[0] is False
    assert orb.entry_allowed(RAKETE, 1, 10_000, 9_690, "AMD", set())[0] is False    # Tagesgrenze
    assert orb.entry_allowed(RAKETE, 1, 10_000, 10_000, "AMD", {"AMD"})[0] is False  # 1 Trade je Aktie


def test_drawdown_halt():
    assert orb.drawdown_halt(10_000, 9_500.0, 0.05) is True         # Falke: 5 % unter Hoechststand
    assert orb.drawdown_halt(10_000, 9_500.01, 0.05) is False
    assert orb.drawdown_halt(12_000, 9_000.0, 0.25) is True         # Rakete: 25 %
    assert orb.drawdown_halt(12_000, 9_001.0, 0.25) is False
    ok, why = orb.entry_allowed(FALKE, 0, 10_000, 10_000, "AAPL", set(), halted=True)
    assert ok is False and "Notbremse" in why
    assert run_day(FALKE, {"AAA": falke_day()}, {}, 10_000.0, slip=0.0, halted=True) == []


# --- Zeiten und Zwangsschluss -----------------------------------------------
def test_session_times():
    le, fl = orb.session_times(FALKE, FLATTEN_TIME, FLATTEN_BEFORE_CLOSE_MIN)
    assert (le, fl) == (T(11, 30), T(15, 50))
    le, fl = orb.session_times(FALKE, FLATTEN_TIME, FLATTEN_BEFORE_CLOSE_MIN, T(13, 0))
    assert (le, fl) == (T(11, 30), T(12, 50))                 # verkuerzter Handelstag
    assert orb.must_flatten(T(15, 49), T(15, 50)) is False
    assert orb.must_flatten(T(15, 50), T(15, 50)) is True
    assert orb.must_flatten(T(15, 59), T(15, 50)) is True


# --- Nachgezogener Stop -----------------------------------------------------
def test_trail_stop():
    # Long, Einstieg 100, Risiko 2: Stop mindestens Einstieg, sonst 1R hinter dem Hoch
    assert orb.trail_stop(RAKETE, 100, 2, 101.0, 98) == 100.0        # erst einmal auf den Einstieg
    assert orb.trail_stop(RAKETE, 100, 2, 104.0, 98) == 102.0
    assert orb.trail_stop(RAKETE, 100, 2, 107.0, 102.0) == 105.0
    assert orb.trail_stop(RAKETE, 100, 2, 106.0, 105.0) is None      # nie zurueck
    # Short gespiegelt
    assert orb.trail_stop(RAKETE, 100, 2, 99.0, 102, "short") == 100.0
    assert orb.trail_stop(RAKETE, 100, 2, 96.0, 102, "short") == 98.0
    assert orb.trail_stop(RAKETE, 100, 2, 93.0, 98.0, "short") == 95.0
    assert orb.trail_stop(RAKETE, 100, 2, 94.0, 95.0, "short") is None
    assert orb.trail_stop(FALKE, 100, 2, 110.0, 98) is None          # Falke zieht nichts nach


# --- Limit-Order: Ausfuehrung innerhalb von 2 Minuten -----------------------
def test_simulate_fill():
    d = day(100.0)
    sig = pos(d, "09:50")
    assert simulate_fill(d, sig, 100.1, "long") == (sig + 1, 100.0)   # Eroeffnung unter dem Limit
    fill(d, "09:51", "09:53", 100.5)                                  # Kurs laeuft 2 Minuten davon
    assert simulate_fill(d, sig, 100.1, "long") is None               # -> Order wird storniert
    put(d, "09:52", 100.5, 100.5, 100.05, 100.4)                      # in Minute 2 kurz am Limit
    assert simulate_fill(d, sig, 100.1, "long") == (sig + 2, 100.1)
    s = day(100.0)
    assert simulate_fill(s, sig, 99.9, "short") == (sig + 1, 100.0)
    fill(s, "09:51", "09:53", 99.5)
    assert simulate_fill(s, sig, 99.9, "short") is None


# --- Ein Long-Trade von Anfang bis Ende -------------------------------------
def test_simulate_exit():
    plan = {"side": "long", "entry": 101.6, "stop": 100.0, "risk": 1.6, "target": 104.8,
            "runner_target": None}
    fl = T(15, 50)
    p = pos(falke_day(), "09:51")
    # 1) Kursziel
    d = falke_day()
    put(d, "10:30", 101.5, 104.9, 101.5, 104.8)
    r = simulate_exit(d, p, 101.5, 15, plan, FALKE, fl, slip=0.0)
    assert r["exit_reason"] == "Kursziel" and approx(r["pnl"], 15 * 3.3), r
    assert approx(r["r"], 3.3 / 1.5) and r["exit"] == 104.8
    # 2) Stop
    d = falke_day()
    put(d, "10:30", 101.5, 101.5, 99.9, 100.0)
    r = simulate_exit(d, p, 101.5, 15, plan, FALKE, fl, slip=0.0)
    assert r["exit_reason"] == "Stop" and approx(r["pnl"], -15 * 1.5) and approx(r["r"], -1.0), r
    # 3) Stop und Ziel in derselben Minute -> es zaehlt der Stop
    d = falke_day()
    put(d, "10:30", 101.5, 105.0, 99.9, 101.5)
    assert simulate_exit(d, p, 101.5, 15, plan, FALKE, fl, slip=0.0)["exit_reason"] == "Stop"
    # 4) Kursluecke unter den Stop: Verkauf zum schlechteren Eroeffnungskurs
    d = falke_day()
    put(d, "10:30", 99.0, 99.0, 98.5, 98.8)
    r = simulate_exit(d, p, 101.5, 15, plan, FALKE, fl, slip=0.0)
    assert approx(r["pnl"], 15 * (99.0 - 101.5)) and r["r"] < -1.0, r
    # 5) In der Einstiegs-Minute zaehlt das Kursziel noch nicht, der Stop schon
    d = falke_day()
    put(d, "09:51", 101.5, 106.0, 101.4, 101.5)
    assert simulate_exit(d, p, 101.5, 15, plan, FALKE, fl, slip=0.0)["exit_reason"] != "Kursziel"
    put(d, "09:51", 101.5, 106.0, 99.5, 101.5)
    r = simulate_exit(d, p, 101.5, 15, plan, FALKE, fl, slip=0.0)
    assert r["exit_reason"] == "Stop" and r["exit_time"].time() == T(9, 51)
    # 6) Zwangsschluss um 15:50: Position ist NIE ueber Nacht offen
    d = falke_day()
    fill(d, "15:50", "16:00", 102.0)
    r = simulate_exit(d, p, 101.5, 15, plan, FALKE, fl, slip=0.0)
    assert r["exit_reason"] == "Zwangsschluss" and r["exit_time"].time() == T(15, 50)
    assert approx(r["pnl"], 15 * 0.5), r
    # 7) Slippage macht jeden Trade schlechter
    assert simulate_exit(d, p, 101.5, 15, plan, FALKE, fl, slip=0.0005)["pnl"] < r["pnl"]


# --- Teilverkauf und nachgezogener Stop (Rakete) ----------------------------
def test_simulate_runner_long():
    plan = {"side": "long", "entry": 100.0, "stop": 98.0, "risk": 2.0, "target": 104.0,
            "runner_target": 110.0}
    p = pos(day(), "09:40")
    d = day(100.0)
    put(d, "10:00", 100, 104.5, 100, 104.5)           # 2R erreicht -> Haelfte verkauft
    fill(d, "10:01", "10:10", 104.5)
    put(d, "10:10", 104.5, 107.0, 104.5, 107.0)       # neues Hoch 107 -> Stop auf 105
    put(d, "10:11", 107.0, 107.0, 104.0, 104.0)       # faellt -> nachgezogener Stop bei 105
    r = simulate_exit(d, p, 100.0, 20, plan, RAKETE, T(15, 50), slip=0.0)
    assert r["exit_reason"] == "Nachgezogener Stop", r
    assert approx(r["pnl"], 10 * 4.0 + 10 * 5.0) and approx(r["r"], 90.0 / 40.0), r
    # Vor dem Teilverkauf wird NICHT nachgezogen: Hoch 103,9, dann zurueck auf den alten Stop
    d = day(100.0)
    put(d, "10:00", 100, 103.9, 100, 103.9)
    put(d, "10:01", 103.9, 103.9, 97.9, 98.0)
    r = simulate_exit(d, p, 100.0, 20, plan, RAKETE, T(15, 50), slip=0.0)
    assert r["exit_reason"] == "Stop" and approx(r["pnl"], -40.0), r
    # Not-Ziel 5R fuer die zweite Haelfte
    d = day(100.0)
    put(d, "10:00", 100, 104.0, 100, 104.0)
    put(d, "10:01", 104.0, 111.0, 104.0, 111.0)
    r = simulate_exit(d, p, 100.0, 20, plan, RAKETE, T(15, 50), slip=0.0)
    assert r["exit_reason"] == "Not-Ziel 5R" and approx(r["pnl"], 10 * 4.0 + 10 * 10.0), r
    # Nur 1 Aktie: kein Teilverkauf, alles bei 2R
    d = day(100.0)
    put(d, "10:00", 100, 104.5, 100, 104.5)
    r = simulate_exit(d, p, 100.0, 1, plan, RAKETE, T(15, 50), slip=0.0)
    assert r["exit_reason"] == "Kursziel" and approx(r["pnl"], 4.0) and r["exit_time"].time() == T(10, 0)


def test_simulate_runner_short():
    plan = {"side": "short", "entry": 100.0, "stop": 102.0, "risk": 2.0, "target": 96.0,
            "runner_target": 90.0}
    p = pos(day(), "09:40")
    # Kurs faellt auf 2R (Haelfte gedeckt), erholt sich -> Rest ueber nachgezogenen Stop bei 97,5
    d = day(100.0)
    put(d, "10:00", 100, 100, 95.5, 95.5)
    fill(d, "10:01", "10:10", 95.5)
    put(d, "10:10", 95.5, 98.0, 95.5, 98.0)
    r = simulate_exit(d, p, 100.0, 20, plan, RAKETE, T(15, 50), slip=0.0)
    assert r["side"] == "short" and r["exit_reason"] == "Nachgezogener Stop", r
    assert approx(r["pnl"], 10 * 4.0 + 10 * 2.5), r
    # Kurs steigt -> Stop, Verlust genau -1R
    d = day(100.0)
    put(d, "10:00", 100, 102.5, 100, 102.5)
    r = simulate_exit(d, p, 100.0, 20, plan, RAKETE, T(15, 50), slip=0.0)
    assert r["exit_reason"] == "Stop" and approx(r["pnl"], -40.0) and approx(r["r"], -1.0), r
    # Slippage geht auch beim Short zum Nachteil; Zwangsschluss gilt genauso
    d = day(100.0)
    fill(d, "09:41", "16:00", 99.0)
    clean = simulate_exit(d, p, 100.0, 20, plan, RAKETE, T(15, 50), slip=0.0)
    assert clean["exit_reason"] == "Zwangsschluss" and approx(clean["pnl"], 20.0)
    assert simulate_exit(d, p, 100.0, 20, plan, RAKETE, T(15, 50), slip=0.0005)["pnl"] < clean["pnl"]


# --- Ganzer Handelstag ------------------------------------------------------
def test_run_day_falke():
    bars = {s: falke_day() for s in ("AAA", "BBB", "CCC")}
    for d in bars.values():
        put(d, "10:30", 101.5, 104.9, 101.5, 104.8)
        fill(d, "10:31", "16:00", 104.8)
    trades = run_day(FALKE, bars, {}, 10_000.0, slip=0.0)
    assert len(trades) == 2, trades                   # drei Ausbrueche, aber hoechstens 2 Trades
    assert [t["symbol"] for t in trades] == ["AAA", "BBB"]
    assert all(t["side"] == "long" and t["qty"] == 15 and t["stop"] == 100.0 for t in trades)
    assert all(t["target"] == 104.8 and t["exit_reason"] == "Kursziel" for t in trades)
    assert all(t["entry_time"].time() == T(9, 51) for t in trades)      # erst NACH der Signalkerze
    assert all(t["risk_pct"] <= 0.25 + 1e-9 and t["exposure_pct"] <= 25.0 for t in trades)
    # Kein Kredit: alle offenen Positionen zusammen <= Kontowert, auch bei engen Stops
    tight = dict(FALKE, max_trades_per_day=6, risk_pct=0.05, max_pos_pct=0.60)
    many = run_day(tight, {s: falke_day() for s in "ABCDEF"}, {}, 10_000.0, slip=0.0)
    assert sum(t["qty"] * t["entry"] for t in many) <= 10_000.0 and len(many) == 2
    # Limit wird nicht erreicht (Kurs springt weg) -> kein Trade
    gone = falke_day()
    fill(gone, "09:51", "16:00", 103.0)
    assert run_day(FALKE, {"AAA": gone}, {}, 10_000.0, slip=0.0) == []
    # verkuerzter Handelstag: Schluss 13:00 -> alles spaetestens 12:50 zu
    t = run_day(FALKE, {"AAA": falke_day()}, {}, 10_000.0, session_close=T(13, 0), slip=0.0)[0]
    assert t["exit_reason"] == "Zwangsschluss" and t["exit_time"].time() == T(12, 50)


def rakete_day(breakout_at, price_after):
    """Luecke +3 % (Vortag 100, Start 103), Spanne 102-104, Ausbruch, dann `price_after`."""
    d = day(103.0, vol=5000.0)
    put(d, "09:31", 103, 104, 102, 103, 5000.0)
    put(d, breakout_at, 103, 104.6, 103, 104.5)
    nxt = orb.add_minutes(orb.parse_time(breakout_at), 1).strftime("%H:%M")
    put(d, nxt, 104.5, 104.5, 104.5, 104.5)           # Einstiegskerze
    after = orb.add_minutes(orb.parse_time(breakout_at), 2).strftime("%H:%M")
    fill(d, after, "16:00", price_after)
    return d


def rakete_short_day(price_after):
    """Luecke -3 % (Vortag 100, Start 97), Spanne 96-98, Ausbruch nach unten um 09:40."""
    d = day(97.0, vol=5000.0)
    put(d, "09:31", 97, 98, 96, 97, 5000.0)
    put(d, "09:40", 97, 97, 95.4, 95.5)
    put(d, "09:41", 95.5, 95.5, 95.5, 95.5)
    fill(d, "09:42", "16:00", price_after)
    return d


CTX = {s: {"prev_close": 100.0, "avg_or_vol": 10_000.0} for s in "ABCDE"}   # rel. Volumen 2,5x


def test_run_day_rakete_long():
    # Vier Aktien brechen nacheinander aus und fallen sofort durch den Stop
    bars = {s: rakete_day("09:%d" % (40 + 5 * i), 101.0) for i, s in enumerate("ABCD")}
    trades = run_day(RAKETE, bars, CTX, 10_000.0, slip=0.0)
    # Nach 3 Verlierern ist die Tagesgrenze -3 % erreicht -> kein 4. Trade
    assert len(trades) == 3, [(t["symbol"], round(t["pnl"], 2)) for t in trades]
    assert all(t["exit_reason"] == "Stop" and t["side"] == "long" for t in trades)
    assert sum(t["pnl"] for t in trades) <= -0.03 * 10_000.0
    assert trades[0]["qty"] == 38 and trades[0]["stop"] == 102.0 and trades[0]["target"] == 109.8
    # Ohne Luecke, ohne Volumen oder ohne Vortagesdaten wird gar nicht gehandelt
    assert run_day(RAKETE, bars, {s: {"prev_close": 103.0, "avg_or_vol": 10_000.0} for s in "ABCD"},
                   10_000.0, slip=0.0) == []
    assert run_day(RAKETE, bars, {s: {"prev_close": 100.0, "avg_or_vol": 50_000.0} for s in "ABCD"},
                   10_000.0, slip=0.0) == []
    assert run_day(RAKETE, bars, {}, 10_000.0, slip=0.0) == []
    # Kredit: mehr als der Kontowert ist erlaubt, aber nie mehr als das Doppelte
    up = {s: rakete_day("09:40", 104.6) for s in "ABCDE"}
    trades = run_day(RAKETE, up, CTX, 10_000.0, slip=0.0)
    invested = sum(t["qty"] * t["entry"] for t in trades)
    assert len(trades) == 4 and 10_000.0 < invested <= 20_000.0, invested
    assert all(t["risk_pct"] <= 1.0 + 1e-9 and t["exposure_pct"] <= 50.0 + 1e-6 for t in trades)
    wide = dict(RAKETE, risk_pct=0.2)                 # absichtlich riesig: Deckel muessen halten
    big = run_day(wide, up, CTX, 10_000.0, slip=0.0)
    assert sum(t["qty"] * t["entry"] for t in big) <= 20_000.0 + 50.0
    assert all(t["exposure_pct"] <= 50.0 + 1e-6 for t in big)


def test_run_day_rakete_short():
    down = {s: rakete_short_day(91.0) for s in "AB"}
    # Short nur bei Aktien, die als shortable + easy-to-borrow gemeldet sind
    trades = run_day(RAKETE, down, CTX, 10_000.0, slip=0.0, short_ok=frozenset({"A"}))
    assert [(t["symbol"], t["side"]) for t in trades] == [("A", "short")], trades
    t = trades[0]
    assert t["stop"] == 98.0 and t["target"] == 90.2 and t["qty"] == 38          # Stop = Hoch der Spanne
    assert t["pnl"] > 0 and t["exit_reason"] == "Zwangsschluss" and t["exit_time"].time() == T(15, 50)
    assert run_day(RAKETE, down, CTX, 10_000.0, slip=0.0) == []                  # nichts freigegeben
    # Kurs muss ueber der Mindestgrenze liegen (hier testweise 200 $ statt 10 $)
    assert run_day(dict(RAKETE, min_short_price=200.0), down, CTX, 10_000.0, slip=0.0,
                   short_ok=frozenset("AB")) == []
    # Ein Bot ohne Short (nur Long) handelt die Luecke nach unten nicht
    assert run_day(dict(RAKETE, sides=("long",)), down, CTX, 10_000.0, slip=0.0,
                   short_ok=frozenset("AB")) == []
    # Long und Short am selben Tag, getrennt ausgewiesen
    mixed = {"A": rakete_short_day(91.0), "B": rakete_day("09:45", 104.6)}
    both = run_day(RAKETE, mixed, CTX, 10_000.0, slip=0.0, short_ok=frozenset("AB"))
    assert sorted(t["side"] for t in both) == ["long", "short"]
    s = summarize(both, [10_000.0 + sum(t["pnl"] for t in both)], 10_000.0)
    assert s["long"]["trades"] == 1 and s["short"]["trades"] == 1


def test_summary():
    assert approx(max_drawdown([100, 110, 99, 120, 108]), 0.1)
    trades = [{"pnl": 50.0, "r": 2.0, "side": "long"}, {"pnl": -25.0, "r": -1.0, "side": "short"},
              {"pnl": -25.0, "r": -1.0, "side": "short"}, {"pnl": 50.0, "r": 2.0, "side": "long"}]
    s = summarize(trades, [10_050, 10_025, 10_000, 10_050], 10_000)
    assert s["trades"] == 4 and s["win_rate"] == 0.5 and approx(s["avg_r"], 0.5)
    assert approx(s["profit_factor"], 2.0) and approx(s["return_pct"], 0.5)
    assert approx(s["max_drawdown_pct"], (1 - 10_000 / 10_050) * 100)
    assert s["long"]["trades"] == 2 and approx(s["long"]["pnl_usd"], 100.0)
    assert s["short"]["trades"] == 2 and approx(s["short"]["avg_r"], -1.0)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print("Alle %d Tests bestanden." % len(tests))
