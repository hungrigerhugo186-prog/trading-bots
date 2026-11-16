# Trading-Bots (Paper Trading)

Zwei regelbasierte Bots fuer US-Aktien ueber Alpaca. **Nur Paper Money** (`paper=True` ist im Code fest verdrahtet).

| | Fels (sicher) | Rakete (aggressiv) |
|---|---|---|
| Idee | Ruecksetzer an die 20-Tage-Linie im Aufwaertstrend | Ausbruch ueber das 20-Tage-Hoch mit 1,5-fachem Volumen |
| Aktien | 80 grosse US-Werte | 100 Nasdaq-/Wachstumswerte, nur die staerksten 20 % |
| Risiko pro Trade | 0,5 % des Kontos | 1 % des Kontos |
| Stop | 1,5 x ATR | 2 x ATR oder knapp unter dem Ausbruch |
| Ziel | 2 x Risiko (CRV 2:1) | 5 x Risiko, ab +2R Trailing-Stop (2 x ATR, mind. Einstieg) |
| Max. Positionen | 5 (max. 20 % je Aktie) | 6 (max. 25 % je Aktie) |
| Marktfilter | SPY unter 200-Tage-Linie: keine Kaeufe | QQQ unter 50-Tage-Linie: halbes Risiko |
| Notbremse | -10 % vom 3-Monats-Hoch: keine Kaeufe | -20 % vom 3-Monats-Hoch: keine Kaeufe |
| Zeit-Stop | nach 20 Handelstagen raus | keiner |

## Ablauf
- GitHub Actions startet Mo-Fr um 21:30 UTC (nach US-Boersenschluss).
- Der Bot screent, stellt Limit-Orders (Schlusskurs + Puffer) mit Stop-Loss und Kursziel ein.
- Die Orders werden zur naechsten Eroeffnung ausgefuehrt; was nicht ausgefuehrt wird, wird am naechsten Abend storniert.
- Pro Tag hoechstens 2 neue Positionen je Bot. Kein Kredit (nur vorhandenes Cash).
- Jeder Lauf schreibt ein Protokoll nach `logs/`.

## Einrichtung
1. Vier Repository-Secrets: `ALPACA_FELS_KEY`, `ALPACA_FELS_SECRET`, `ALPACA_RAKETE_KEY`, `ALPACA_RAKETE_SECRET`.
2. Tab "Actions" -> "Trading Bots (Paper)" -> "Run workflow" mit `dry_run = true` (Testlauf ohne Orders).
3. Passt die Ausgabe, laeuft der Bot ab dem naechsten Handelstag automatisch.

## Dateien
- `config.py` - alle Regeln und Zahlen
- `universe.py` - Aktien-Listen
- `strategies.py` - Signale, Positionsgroesse, Trailing-Stop (ohne Broker, testbar)
- `bot.py` - Verbindung zu Alpaca, Orders, Protokoll
- `tests/test_strategies.py` - Logik-Tests (laufen vor jedem Bot-Lauf)

## Bekannte Grenzen
- Noch kein Backtest mit echten Kursen; die Regeln sind ein Startpunkt.
- Keine Pruefung auf Quartalszahlen-Termine, keine Branchen-Obergrenze.
- Kurse kommen aus dem IEX-Feed (Gratis-Plan); das Volumen ist nur ein Teil des Gesamtmarkts.
- Paper-Ausfuehrungen sind idealisiert und kein Beleg fuer echte Ergebnisse.
