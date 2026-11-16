"""Feste Regeln aller drei Bots. Nur hier werden Zahlen geaendert.

Fels   = Swing-Bot (laeuft einmal taeglich nach US-Boersenschluss)       -> BOTS
Falke  = Daytrading, sicher     (Opening-Range-Breakout, 15 Minuten)    -> DAY_BOTS
Rakete = Daytrading, aggressiv  (Opening-Range-Breakout, 5 Minuten)     -> DAY_BOTS

Alle Bots: NUR Paper Trading. Keine Optionen, keine Hebel-ETFs, kein Krypto.
"""

# ---------------------------------------------------------------------------
# Swing-Bot
# ---------------------------------------------------------------------------
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
}

MIN_PRICE = 10.0        # keine Pennystocks
LOOKBACK_DAYS = 420     # Kalendertage Kurshistorie (reicht fuer die 200-Tage-Linie)

# ---------------------------------------------------------------------------
# Daytrading-Bots (Opening-Range-Breakout)
# ---------------------------------------------------------------------------
DAY_BOTS = {
    # --- Bot "Falke": sicher --------------------------------------------------
    "falke": {
        "universe": "MEGA_CAPS",    # SPY, QQQ und 30 Mega-Caps
        "key_env": "ALPACA_FALKE_KEY",
        "secret_env": "ALPACA_FALKE_SECRET",
        "sides": ("long",),         # nur Long
        "leverage": 1.0,            # kein Kredit: Summe aller Positionen <= Kontowert
        "range_minutes": 15,        # Spanne = erste 15 Minuten (09:30-09:45 New York)
        "min_range_pct": 0.003,     # Spanne muss mind. 0,3 % des Kurses breit sein
        "last_entry": "11:30",      # Signalkerze muss bis 11:30 Uhr geschlossen haben
        "stop_mode": "mid",         # Stop = Mitte der Spanne
        "target_r": 2.0,            # Kursziel = 2 x Risiko
        "risk_pct": 0.0025,         # 0,25 % des Kontowerts pro Trade
        "max_trades_per_day": 2,    # (und immer hoechstens 1 Trade pro Aktie und Tag)
        "max_pos_pct": 0.25,        # hoechstens 25 % des Kontowerts pro Aktie
        "daily_loss_limit": 0.0075, # bei -0,75 % Tagesverlust keine neuen Trades
        "dd_halt": 0.05,            # Konto 5 % unter Hoechststand -> Notbremse bis zur Freigabe
        "min_gap_pct": None,        # kein Filter "Aktien in Bewegung"
        "min_rel_volume": None,
        "min_short_price": None,
        "runner_fraction": 0.0,     # alles wird am Kursziel verkauft
        "runner_target_r": None,
        "trail_r": None,
    },
    # --- Bot "Rakete": aggressiv ----------------------------------------------
    "rakete": {
        "universe": "GROWTH",       # Wachstums-Liste, davon nur "Aktien in Bewegung"
        "key_env": "ALPACA_RAKETE_KEY",
        "secret_env": "ALPACA_RAKETE_SECRET",
        "sides": ("long", "short"), # Long UND Short
        "leverage": 2.0,            # Kredit: offene Positionen zusammen hoechstens 2 x Kontowert
        "range_minutes": 5,         # Spanne = erste 5 Minuten (09:30-09:35 New York)
        "min_range_pct": 0.003,     # Spanne muss mind. 0,3 % des Kurses breit sein
        "last_entry": "11:30",
        "stop_mode": "far",         # Long: Stop = Tief der Spanne, Short: Stop = Hoch der Spanne
        "target_r": 2.0,            # halbe Position wird bei 2 x Risiko verkauft
        "risk_pct": 0.01,           # 1 % des Kontowerts pro Trade
        "max_trades_per_day": 4,
        "max_pos_pct": 0.50,        # hoechstens 50 % des Kontowerts pro Aktie
        "daily_loss_limit": 0.03,   # bei -3 % Tagesverlust keine neuen Trades
        "dd_halt": 0.25,            # Konto 25 % unter Hoechststand -> Notbremse bis zur Freigabe
        "min_gap_pct": 0.02,        # Kursluecke zum Vortagesschluss mind. 2 % (hoch oder runter)
        "min_rel_volume": 2.0,      # Volumen der Spanne mind. 2 x Durchschnitt
        "min_short_price": 10.0,    # Short nur, wenn der Kurs ueber 10 $ liegt
        "runner_fraction": 0.5,     # zweite Haelfte laeuft nach 2R weiter
        "runner_target_r": 5.0,     # Not-Ziel fuer die zweite Haelfte
        "trail_r": 1.0,             # Stop der zweiten Haelfte: Einstieg, dann 1R hinter dem besten Kurs
    },
}

# Gilt fuer beide Daytrading-Bots
TIMEZONE = "America/New_York"
ENTRY_LIMIT_PCT = 0.001          # Limit-Order 0,1 % ueber (Long) / unter (Short) dem Schluss der Signalkerze
ENTRY_TIMEOUT_MIN = 2            # nach 2 Minuten ohne Ausfuehrung stornieren
FLATTEN_TIME = "15:50"           # dann alles schliessen und alle offenen Orders stornieren
FLATTEN_BEFORE_CLOSE_MIN = 10    # an verkuerzten Handelstagen: 10 Minuten vor Schluss
RELVOL_LOOKBACK_DAYS = 20        # Durchschnitt fuer das relative Volumen
RELVOL_MIN_DAYS = 10             # so viele Tage Historie muessen mindestens da sein
OR_MIN_BARS_FRAC = 0.5           # mind. die Haelfte der Minutenkerzen der Spanne muss vorhanden sein
MIN_RISK_PCT = 0.001             # technischer Schutz: Stop mind. 0,1 % vom Kurs entfernt, sonst kein Trade
EMERGENCY_STOP_PCT = 0.02        # Not-Stop fuer eine Position ohne Stop-Order: 2 % vom aktuellen Kurs
POLL_SECOND = 6                  # der Bot prueft jede Minute, 6 Sekunden nach der vollen Minute
DATA_FEED = "iex"                # Gratis-Plan

# Backtest
SLIPPAGE = 0.0005                # 0,05 % pro Order (Kauf und Verkauf)
START_EQUITY = 10_000.0          # Startkapital je Konto
BACKTEST_MONTHS = 12
BACKTEST_END = "2026-11-15"      # letzter Tag des Backtests (fest, damit geladene Daten wiederverwendet werden)
