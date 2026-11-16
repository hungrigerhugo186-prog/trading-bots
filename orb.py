"""Reine Logik der Daytrading-Bots Falke und Rakete (Opening-Range-Breakout).

Kein Broker-Code, keine Internet-Zugriffe: alles hier laesst sich offline testen.
Backtest (backtest.py) und Live-Bot (daybot.py) benutzen dieselben Funktionen.

Minutenkerzen ("bars") sind ein pandas-DataFrame EINES Handelstages:
  Index   = Startzeit der Kerze in New Yorker Zeit, aufsteigend sortiert
  Spalten = open, high, low, close, volume

Richtung ("side"): "long" = kaufen und spaeter teurer verkaufen,
                   "short" = geliehene Aktien verkaufen und spaeter billiger zurueckkaufen.
"""
import datetime as dt
import math

MARKET_OPEN = dt.time(9, 30)
REGULAR_CLOSE = dt.time(16, 0)


# --- Zeiten -----------------------------------------------------------------
def parse_time(text: str) -> dt.time:
    h, m = text.split(":")
    return dt.time(int(h), int(m))


def add_minutes(t: dt.time, minutes: int) -> dt.time:
    return (dt.datetime.combine(dt.date(2000, 1, 3), t) + dt.timedelta(minutes=minutes)).time()


def session_times(cfg: dict, flatten_time: str, before_close_min: int,
                  session_close: dt.time = REGULAR_CLOSE):
    """Liefert (letzter Einstieg, Zwangsschluss) fuer einen Handelstag.

    An verkuerzten Handelstagen (z. B. Schluss 13:00) rueckt der Zwangsschluss
    nach vorn: before_close_min Minuten vor Boersenschluss.
    """
    flatten = min(parse_time(flatten_time), add_minutes(session_close, -before_close_min))
    last_entry = min(parse_time(cfg["last_entry"]), flatten)
    return last_entry, flatten


def must_flatten(now: dt.time, flatten: dt.time) -> bool:
    """True, sobald alles geschlossen werden muss (keine Position ueber Nacht)."""
    return now >= flatten


# --- Spanne und Ausbruch ----------------------------------------------------
def opening_range(bars, minutes: int, min_bars_frac: float = 0.5):
    """Hoch/Tief/Mitte der ersten `minutes` Minuten. None, wenn zu wenig Daten da sind."""
    t = bars.index.time
    end = add_minutes(MARKET_OPEN, minutes)
    w = bars[(t >= MARKET_OPEN) & (t < end)]
    if len(w) < max(1, math.ceil(minutes * min_bars_frac)):
        return None
    high, low = float(w["high"].max()), float(w["low"].min())
    if not (high > low > 0):
        return None
    return {"high": high, "low": low, "mid": (high + low) / 2.0,
            "open": float(w["open"].iloc[0]), "volume": float(w["volume"].sum())}


def range_wide_enough(rng: dict, min_range_pct) -> bool:
    """True, wenn die Spanne (Hoch - Tief) mindestens `min_range_pct` des Kurses breit ist.

    Als Kurs zaehlt die Mitte der Spanne. 0.003 = 0,3 %. Ohne Vorgabe ist alles erlaubt.
    """
    if not min_range_pct:
        return True
    return (rng["high"] - rng["low"]) / rng["mid"] >= min_range_pct


def find_breakout(bars, rng: dict, minutes: int, last_entry: dt.time, side: str = "long"):
    """Zeilennummer der Signalkerze oder None.

    Signal = die ERSTE fertige Kerze nach der Spanne, die ueber dem Hoch schliesst
    (Short: unter dem Tief). Die Kerze muss vor `last_entry` begonnen haben, also
    spaetestens um `last_entry` fertig sein. Pro Tag gibt es nur diesen einen Versuch.
    """
    t = bars.index.time
    closes = bars["close"].to_numpy()
    end = add_minutes(MARKET_OPEN, minutes)
    for i in range(len(bars)):
        if t[i] < end:
            continue
        if t[i] >= last_entry:
            return None
        if (closes[i] > rng["high"]) if side == "long" else (closes[i] < rng["low"]):
            return i
    return None


# --- Filter "Aktien in Bewegung" (Rakete) -----------------------------------
def gap_pct(day_open: float, prev_close):
    """Kursluecke zum Vortagesschluss, 0.02 = +2 %."""
    if not prev_close or prev_close <= 0:
        return None
    return day_open / prev_close - 1.0


def relative_volume(range_volume: float, avg_range_volume):
    """Volumen der heutigen Spanne geteilt durch den Durchschnitt der letzten Tage."""
    if not avg_range_volume or avg_range_volume <= 0:
        return None
    return range_volume / avg_range_volume


def pick_side(cfg: dict, gap, rel_vol):
    """Welche Richtung darf heute gehandelt werden? -> ("long" / "short" / None, Begruendung)

    Ohne Filter (Falke): Long. Mit Filter (Rakete): Luecke nach oben -> Long,
    Luecke nach unten -> Short, jeweils nur mit genug relativem Volumen.
    """
    if cfg["min_gap_pct"] is None:
        return "long", "kein Filter"
    if gap is None or rel_vol is None:
        return None, "Vortagesdaten fehlen"
    if abs(gap) < cfg["min_gap_pct"]:
        return None, "Kursluecke %+.1f %% kleiner als %.1f %%" % (gap * 100, cfg["min_gap_pct"] * 100)
    if rel_vol < cfg["min_rel_volume"]:
        return None, "rel. Volumen %.1fx kleiner als %.1fx" % (rel_vol, cfg["min_rel_volume"])
    side = "long" if gap > 0 else "short"
    if side not in cfg["sides"]:
        return None, "Luecke %+.1f %%, aber %s ist nicht erlaubt" % (gap * 100, side)
    return side, "Kursluecke %+.1f %%, rel. Volumen %.1fx" % (gap * 100, rel_vol)


def short_allowed(cfg: dict, price: float, shortable: bool):
    """Short nur, wenn Alpaca shortable + easy_to_borrow meldet und der Kurs hoch genug ist."""
    if not shortable:
        return False, "nicht als shortable/easy-to-borrow gemeldet"
    if cfg["min_short_price"] and price <= cfg["min_short_price"]:
        return False, "Kurs %.2f $ nicht ueber %.0f $" % (price, cfg["min_short_price"])
    return True, "ok"


def context_from_bars(range_bars, today: dt.date, lookback: int, min_days: int):
    """Vortagesschluss und Durchschnitts-Volumen der Spanne aus den Tagen VOR `today`.

    range_bars: Kerzen in der Laenge der Spanne (z. B. 5-Minuten-Kerzen) ueber mehrere Tage.
    Rueckgabe {"prev_close", "avg_or_vol"} oder None bei zu wenig Historie.
    """
    past = range_bars[range_bars.index.date < today]
    if past.empty:
        return None
    first = past[past.index.time == MARKET_OPEN]["volume"]
    vols = [float(v) for v in first.tail(lookback)]
    if len(vols) < min_days:
        return None
    return {"prev_close": float(past["close"].iloc[-1]), "avg_or_vol": sum(vols) / len(vols)}


# --- Trade planen -----------------------------------------------------------
def entry_limit(signal_close: float, side: str, pct: float) -> float:
    """Limit-Preis der Einstiegs-Order: etwas ueber (Long) bzw. unter (Short) dem Signal-Schluss."""
    return round(signal_close * (1 + pct) if side == "long" else signal_close * (1 - pct), 2)


def plan_trade(cfg: dict, entry: float, rng: dict, side: str = "long", min_risk_pct: float = 0.0):
    """Stop und Kursziele fuer einen Einstieg zum Preis `entry` (= Limit-Preis, schlechtester Fall).

    None, wenn der Stop auf der falschen Seite liegt oder zu nah am Kurs ist.
    """
    sign = 1 if side == "long" else -1
    if cfg["stop_mode"] == "mid":
        stop = rng["mid"]
    else:                                   # "far": gegenueberliegende Seite der Spanne
        stop = rng["low"] if side == "long" else rng["high"]
    stop = round(stop, 2)
    risk = sign * (entry - stop)
    if entry <= 0 or risk <= 0 or risk / entry < min_risk_pct:
        return None
    plan = {"side": side, "entry": entry, "stop": stop, "risk": risk,
            "target": round(entry + sign * cfg["target_r"] * risk, 2), "runner_target": None}
    if cfg["runner_fraction"] and cfg["runner_target_r"]:
        plan["runner_target"] = round(entry + sign * cfg["runner_target_r"] * risk, 2)
    return plan


def size_position(equity: float, power: float, risk_pct: float, entry: float,
                  risk_per_share: float, max_pos_pct: float) -> int:
    """Stueckzahl = (Kontowert x Risiko) / Abstand zum Stop, abgerundet.

    Obergrenzen: max_pos_pct des Kontowerts pro Aktie und die freie Kaufkraft `power`.
    """
    if risk_per_share <= 0 or entry <= 0:
        return 0
    qty = math.floor(equity * risk_pct / risk_per_share)
    qty = min(qty, math.floor(equity * max_pos_pct / entry), math.floor(power / entry))
    return max(int(qty), 0)


def buying_power(equity: float, open_exposure: float, leverage: float) -> float:
    """Wie viel Dollar noch investiert werden duerfen. leverage 1.0 = kein Kredit.
    open_exposure = Betrag aller offenen Positionen und Orders (Long + Short addiert)."""
    return max(0.0, leverage * equity - open_exposure)


def split_qty(qty: int, runner_fraction: float):
    """Teilt die Stueckzahl in (Teil mit Kursziel 2R, Rest mit nachgezogenem Stop).
    Bei 1 Aktie gibt es keinen Rest: alles geht ans Kursziel."""
    runner = int(math.floor(qty * (runner_fraction or 0.0)))
    return qty - runner, runner


def trail_stop(cfg: dict, entry: float, risk: float, best_since_entry: float,
               current_stop: float, side: str = "long"):
    """Stop der zweiten Haelfte NACH dem Teilverkauf: mindestens Einstiegskurs, sonst
    trail_r x Risiko hinter dem besten Kurs. Bewegt sich nie zurueck. None = bleibt.

    Long: best_since_entry = hoechster Kurs seit dem Einstieg. Short: tiefster Kurs.
    """
    if not cfg["trail_r"] or risk <= 0:
        return None
    if side == "long":
        new = round(max(entry, best_since_entry - cfg["trail_r"] * risk), 2)
        return new if new > current_stop else None
    new = round(min(entry, best_since_entry + cfg["trail_r"] * risk), 2)
    return new if new < current_stop else None


# --- Tages- und Kontoregeln -------------------------------------------------
def daily_loss_hit(start_equity: float, equity: float, limit: float) -> bool:
    """True, wenn das Konto heute mindestens `limit` (0.0075 = 0,75 %) verloren hat."""
    return equity <= start_equity * (1.0 - limit)


def drawdown_halt(peak_equity: float, equity: float, dd_halt: float) -> bool:
    """Konto-Notbremse: True, wenn das Konto mindestens dd_halt unter seinem Hoechststand liegt."""
    return peak_equity > 0 and equity <= peak_equity * (1.0 - dd_halt)


def entry_allowed(cfg: dict, trades_today: int, start_equity: float, equity: float,
                  symbol: str, tried_symbols, halted: bool = False):
    """(True/False, Begruendung) fuer einen neuen Einstieg. Im Zweifel: nein."""
    if halted:
        return False, "Konto-Notbremse aktiv (wartet auf Freigabe)"
    if daily_loss_hit(start_equity, equity, cfg["daily_loss_limit"]):
        return False, "Tagesgrenze erreicht (%.2f %%)" % ((equity / start_equity - 1) * 100)
    if trades_today >= cfg["max_trades_per_day"]:
        return False, "schon %d Trades heute" % trades_today
    if symbol in tried_symbols:
        return False, "%s war heute schon dran" % symbol
    return True, "ok"


def r_multiple(pnl: float, qty: float, entry: float, stop: float):
    """Gewinn/Verlust in R: +2 = doppeltes Risiko gewonnen, -1 = geplanter Verlust."""
    risk = qty * abs(entry - stop)
    return pnl / risk if risk > 0 else 0.0
