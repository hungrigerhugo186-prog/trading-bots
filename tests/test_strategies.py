"""Offline-Tests der Strategie-Logik mit kuenstlichen Kursen. Aufruf: python tests/test_strategies.py"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import BOTS  # noqa: E402
from strategies import (add_indicators, position_size,  # noqa: E402
                        pullback_signals, trailing_stop)


def make(closes, vol=1_000_000.0, last_vol=None, spread=0.01):
    c = np.array(closes, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * (1 + spread),
                       "low": np.minimum(o, c) * (1 - spread), "close": c,
                       "volume": np.full(len(c), vol)})
    if last_vol:
        df.loc[df.index[-1], "volume"] = last_vol
    return add_indicators(df)


def uptrend(n=260, start=50.0, step=0.2):
    return list(start + step * np.arange(n))


def test_pullback():
    base = uptrend()                     # sauberer Aufwaertstrend
    top = base[-1]
    dip = base + [top - 1.5, top - 3.0, top - 4.0, top - 0.5]   # Ruecksetzer, dann Dreh nach oben
    sig = pullback_signals({"UP": make(dip)}, BOTS["fels"])
    assert len(sig) == 1, sig
    s = sig[0]
    risk = s["close"] - s["stop"]
    assert abs((s["target"] - s["close"]) / risk - 2.0) < 1e-9       # CRV genau 2:1
    assert abs(risk - 1.5 * s["atr"]) < 1e-9
    # kein Signal: Abwaertstrend, und kein Signal ohne Ruecksetzer
    assert pullback_signals({"DOWN": make(list(reversed(uptrend())))}, BOTS["fels"]) == []
    assert pullback_signals({"RUN": make(uptrend(step=0.6), spread=0.001)}, BOTS["fels"]) == []


def test_sizing():
    # 10.000 $, 0,5 % Risiko = 50 $; Stop 5 $ entfernt -> 10 Stueck
    assert position_size(10_000, 10_000, 0.005, 100, 95, 0.20) == 10
    # Positions-Deckel 20 % = 2.000 $ -> hoechstens 20 Stueck, auch bei engem Stop
    assert position_size(10_000, 10_000, 0.005, 100, 99.5, 0.20) == 20
    assert position_size(10_000, 500, 0.01, 100, 95, 0.25) == 5       # nicht mehr als Cash
    assert position_size(10_000, 10_000, 0.01, 100, 100, 0.25) == 0   # kein Stop-Abstand
    assert position_size(10_000, 10_000, 0.005, 3000, 2800, 0.20) == 0  # Aktie zu teuer


def test_trailing():
    assert trailing_stop(100, 5, 108, 2, 95, 2.0, 2.0) is None        # noch nicht +2R
    assert trailing_stop(100, 5, 111, 2, 95, 2.0, 2.0) == 107.0       # 111 - 2x2
    assert trailing_stop(100, 5, 110, 6, 95, 2.0, 2.0) == 100.0       # mindestens Einstieg
    assert trailing_stop(100, 5, 111, 2, 108, 2.0, 2.0) is None       # Stop nie nach unten


if __name__ == "__main__":
    for t in (test_pullback, test_sizing, test_trailing):
        t()
        print("ok  ", t.__name__)
    print("Alle Tests bestanden.")
