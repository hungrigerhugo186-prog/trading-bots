"""Feste Regeln der beiden Bots. Hier und nur hier werden Parameter geändert."""

FELS_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "BRK.B", "JPM", "V", "MA",
    "UNH", "JNJ", "PG", "KO", "PEP", "COST", "WMT", "HD", "LLY", "ABBV",
    "MRK", "TMO", "ABT", "MCD", "LOW", "TXN", "AVGO", "ORCL", "CSCO", "ADBE",
    "CRM", "ACN", "IBM", "HON", "CAT", "DE", "GE", "RTX", "LMT", "UNP",
    "LIN", "NEE", "DUK", "SO", "XOM", "CVX", "SPGI", "ICE", "CME", "BLK",
    "GS", "MS", "AXP", "ISRG", "SYK", "TJX", "BKNG", "AMGN", "VRTX", "REGN",
]

RAKETE_UNIVERSE = [
    "NVDA", "AMD", "AVGO", "MU", "TSM", "ASML", "ANET", "PLTR", "CRWD", "PANW",
    "ZS", "NET", "DDOG", "SNOW", "MDB", "SHOP", "MELI", "UBER", "DASH", "ABNB",
    "COIN", "HOOD", "SOFI", "APP", "TTD", "RBLX", "TSLA", "META", "AMZN", "GOOGL",
    "MSFT", "NOW", "INTU", "ORCL", "AXON", "ONON", "DECK", "ELF", "DUOL", "SE",
    "NU", "PDD", "CPNG", "CAVA", "VRT", "GEV", "PWR", "ETN", "CEG", "VST",
    "IREN", "CCJ", "RKLB", "ASTS", "IONQ", "AFRM", "TOST", "ISRG", "LLY", "SPOT",
]

BOTS = {
    "fels": {
        "name": "Fels (sicher)",
        "env_key": "ALPACA_FELS_KEY",
        "env_secret": "ALPACA_FELS_SECRET",
        "universe": FELS_UNIVERSE,
        "market_symbol": "SPY",
        "strategy": "pullback",
        "risk_per_trade": 0.005,      # 0,5 % des Kontos pro Trade
        "stop_atr": 1.5,              # Stop = Einstieg - 1,5 x ATR
        "target_r": 2.0,              # Ziel = 2 x Risiko (CRV 2:1)
        "trail_atr": None,            # kein Trailing-Stop
        "max_positions": 5,
        "max_position_pct": 0.25,     # max. 25 % des Kontos in einer Aktie
        "max_open_risk": 0.025,       # max. 2,5 % offenes Risiko gesamt
        "max_new_per_day": 2,
        "max_hold_days": 21,          # Kalendertage, dann Zeit-Stop
        "max_drawdown": 0.10,         # ab -10 % vom Hoch: keine neuen Trades
    },
    "rakete": {
        "name": "Rakete (aggressiv)",
        "env_key": "ALPACA_RAKETE_KEY",
        "env_secret": "ALPACA_RAKETE_SECRET",
        "universe": RAKETE_UNIVERSE,
        "market_symbol": "QQQ",
        "strategy": "breakout",
        "risk_per_trade": 0.01,       # 1 % des Kontos pro Trade
        "stop_atr": 2.0,
        "target_r": 3.0,              # CRV 3:1
        "trail_atr": 2.5,             # ab +1R: Stop = Schlusskurs - 2,5 x ATR (nur nach oben)
        "max_positions": 6,
        "max_position_pct": 0.25,
        "max_open_risk": 0.06,
        "max_new_per_day": 3,
        "max_hold_days": 45,
        "max_drawdown": 0.20,
    },
}

SLIPPAGE = 0.0005  # 0,05 % pro Seite im Backtest
