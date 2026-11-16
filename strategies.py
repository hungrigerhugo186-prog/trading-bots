"""Reine Strategie-Logik (ohne Broker). Laesst sich offline testen."""
import math

import pandas as pd


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """df: Tageskerzen mit open/high/low/close/volume, aufsteigend sortiert."""
    d = df.copy()
    d["sma20"] = d["close"].rolling(20).mean()
    d["sma50"] = d["close"].rolling(50).mean()
    d["sma200"] = d["close"].rolling(200).mean()
    prev = d["close"].shift(1)
    tr = pd.concat(
        [d["high"] - d["low"], (d["high"] - prev).abs(), (d["low"] - prev).abs()], axis=1
    ).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    d["hh20"] = d["high"].shift(1).rolling(20).max()      # 20-Tage-Hoch OHNE heute
    d["vol20"] = d["volume"].shift(1).rolling(20).mean()  # Durchschnittsvolumen OHNE heute
    d["ret126"] = d["close"] / d["close"].shift(126) - 1  # 6-Monats-Rendite
    d["low3"] = d["low"].rolling(3).min()
    return d


def _ok(row, cols) -> bool:
    return all(pd.notna(row[c]) and math.isfinite(float(row[c])) for c in cols)


def pullback_signals(data: dict, cfg: dict, min_price: float = 10.0) -> list:
    """Bot Fels: Ruecksetzer an die 20-Tage-Linie in einem sauberen Aufwaertstrend."""
    out = []
    for sym, d in data.items():
        if len(d) < 210:
            continue
        r, p = d.iloc[-1], d.iloc[-2]
        if not _ok(r, ["sma20", "sma50", "sma200", "atr", "ret126", "low3"]):
            continue
        c, atr = float(r["close"]), float(r["atr"])
        if c < min_price or atr <= 0:
            continue
        trend = c > r["sma50"] > r["sma200"] and r["sma20"] > r["sma50"]
        touched = r["low3"] <= r["sma20"] + 0.25 * atr          # war in den letzten 3 Tagen an der Linie
        turned = c > r["open"] and c > p["close"] and c > r["sma20"]  # dreht wieder nach oben
        not_extended = c <= r["sma20"] + 1.5 * atr
        if trend and touched and turned and not_extended:
            stop = c - cfg["stop_atr"] * atr
            out.append({
                "symbol": sym, "close": c, "atr": atr, "stop": stop,
                "target": c + cfg["target_r"] * (c - stop),
                "score": float(r["ret126"]),
                "reason": "Ruecksetzer an 20-Tage-Linie im Aufwaertstrend",
            })
    return sorted(out, key=lambda x: x["score"], reverse=True)


def position_size(equity: float, cash: float, risk_pct: float, entry: float, stop: float,
                  max_pos_pct: float) -> int:
    """Stueckzahl so, dass beim Stop genau risk_pct des Kontos verloren geht."""
    risk_per_share = entry - stop
    if risk_per_share <= 0 or entry <= 0:
        return 0
    qty = math.floor(equity * risk_pct / risk_per_share)
    qty = min(qty, math.floor(equity * max_pos_pct / entry), math.floor(cash / entry))
    return max(int(qty), 0)


def trailing_stop(entry: float, risk: float, close: float, atr: float, current_stop: float,
                  start_r: float, trail_atr: float):
    """Ab +start_r x Risiko wird der Stop nachgezogen (nie nach unten, mind. Einstieg)."""
    if risk <= 0 or close < entry + start_r * risk:
        return None
    new = max(entry, close - trail_atr * atr)
    return round(new, 2) if new > current_stop * 1.001 else None
