"""Aktien-Listen der Bots (US-Aktien, liquide). Unbekannte Ticker werden uebersprungen."""

# Bot "Fels": grosse, etablierte US-Werte quer durch alle Branchen
LARGE_CAPS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AVGO", "ORCL", "CSCO", "IBM",
    "ACN", "ADBE", "CRM", "TXN", "QCOM", "INTU", "JPM", "BAC", "WFC", "GS",
    "MS", "BLK", "AXP", "V", "MA", "SPGI", "CB", "PGR", "UNH", "JNJ",
    "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "ISRG", "SYK", "AMGN",
    "PG", "KO", "PEP", "COST", "WMT", "MCD", "SBUX", "NKE", "HD", "LOW",
    "TJX", "DIS", "NFLX", "CMCSA", "TMUS", "VZ", "XOM", "CVX", "COP", "LIN",
    "CAT", "DE", "HON", "GE", "RTX", "LMT", "UNP", "UPS", "ETN", "NEE",
    "SO", "DUK", "PLD", "AMT", "WM", "ADP", "MMC", "ICE", "CME", "BKNG",
]

# Bot "Falke": nur sehr liquide Werte - SPY, QQQ und 30 Mega-Caps
MEGA_CAPS = [
    "SPY", "QQQ",
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA", "BRK.B", "JPM",
    "LLY", "V", "MA", "XOM", "UNH", "COST", "WMT", "HD", "PG", "JNJ",
    "NFLX", "ORCL", "BAC", "ABBV", "KO", "CVX", "CRM", "AMD", "PEP", "MRK",
]

# Bot "Rakete": Nasdaq-100-Werte und liquide Wachstums-/Mid-Cap-Aktien
GROWTH = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AVGO", "TSLA", "NFLX", "COST",
    "AMD", "ADBE", "QCOM", "INTU", "AMAT", "ISRG", "BKNG", "TXN", "MU", "LRCX",
    "KLAC", "PANW", "CRWD", "SNPS", "CDNS", "MRVL", "ADI", "NXPI", "ASML", "ARM",
    "MELI", "ABNB", "FTNT", "WDAY", "DDOG", "ZS", "TEAM", "MDB", "SNOW", "NOW",
    "PLTR", "APP", "SHOP", "UBER", "DASH", "COIN", "HOOD", "RBLX", "TTD", "AXON",
    "VRT", "GEV", "CEG", "VST", "ANET", "APH", "DELL", "SMCI", "ON", "MPWR",
    "REGN", "VRTX", "GILD", "AMGN", "IDXX", "DXCM", "PODD", "ALNY", "LULU", "DECK",
    "ONON", "CELH", "DUOL", "SPOT", "SE", "NU", "TOST", "AFRM", "SOFI", "NET",
    "HUBS", "DT", "TWLO", "PINS", "RDDT", "CVNA", "PWR", "ETN", "TT", "URI",
    "FICO", "CPRT", "ORLY", "CTAS", "PAYX", "ROST", "MAR", "PYPL", "INTC", "CCJ",
]
