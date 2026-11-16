"""Feste Regeln der beiden Bots. Nur hier werden Zahlen geaendert."""

BOTS = {
    # --- Bot "Fels": sicher, Ruecksetzer im Aufwaertstrend -------------------
    "fels": {
        "strategy": "pullback",
        "universe": "LARGE_CAPS",
        "key_env": "ALPACA_FELS_KEY",
        "secret_env": "ALPACA_FELS_SECRET",
        "risk_pct": 0.005,          # 0,5 % des Kontos pro Trade
        "stop_atr": 1.5,            # Stop = Einstieg - 1,5 x ATR
        "target_r": 2.0,            # Kursziel = 2 x Risiko (CRV 2:1)
        "max_positions": 5,
        "max_new_per_day": 2,
        "max_pos_pct": 0.20,        # hoechstens 20 % des Kontos in einer Aktie
        "entry_buffer": 0.005,      # Limit = Schlusskurs + 0,5 % (kein Hinterherlaufen)
        "market_symbol": "SPY",
        "market_sma": 200,
        "market_rule": "block",     # Markt unter der Linie -> keine neuen Kaeufe
        "dd_pause": 0.10,           # -10 % vom 3-Monats-Hoch -> keine neuen Kaeufe
        "time_stop_days": 20,       # nach 20 Handelstagen ohne Ziel/Stop: raus
        "trail_start_r": None,      # kein Trailing
        "trail_atr": None,
    },
    # --- Bot "Rakete": aggressiver, Momentum-Ausbruch ------------------------
    "rakete": {
        "strategy": "breakout",
        "universe": "GROWTH",
        "key_env": "ALPACA_RAKETE_KEY",
        "secret_env": "ALPACA_RAKETE_SECRET",
        "risk_pct": 0.01,           # 1 % des Kontos pro Trade
        "stop_atr": 2.0,            # Stop = Einstieg - 2 x ATR (oder knapp unter Ausbruch)
        "target_r": 5.0,            # weites Kursziel; ab +2R uebernimmt der Trailing-Stop
        "max_positions": 6,
        "max_new_per_day": 2,
        "max_pos_pct": 0.25,
        "entry_buffer": 0.015,
        "market_symbol": "QQQ",
        "market_sma": 50,
        "market_rule": "half",      # Markt unter der Linie -> halbes Risiko
        "dd_pause": 0.20,           # -20 % vom 3-Monats-Hoch -> keine neuen Kaeufe
        "time_stop_days": None,
        "trail_start_r": 2.0,       # ab +2R: Stop nachziehen
        "trail_atr": 2.0,           # neuer Stop = Schlusskurs - 2 x ATR (mind. Einstieg)
        "vol_mult": 1.5,            # Ausbruch braucht 1,5-faches Volumen
        "rs_top": 0.20,             # nur die staerksten 20 % (6-Monats-Rendite)
    },
}

MIN_PRICE = 10.0        # keine Pennystocks
LOOKBACK_DAYS = 420     # Kalendertage Kurshistorie (reicht fuer die 200-Tage-Linie)
