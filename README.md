# Trading-Bots (nur Paper Trading)

Drei regelbasierte Bots für US-Aktien über Alpaca. **Es wird ausschließlich mit Spielgeld
gehandelt** (`paper=True` steht fest im Code, es gibt keinen Schalter für Echtgeld).
Jeder Bot hat ein eigenes Paper-Konto mit 10.000 $.

| Bot | Art | Läuft |
|---|---|---|
| **Fels** | Swing-Trading, Vergleichsbot | einmal pro Handelstag nach US-Börsenschluss |
| **Falke** | Daytrading, vorsichtig | während der Börsenzeit, jede Minute |
| **Rakete** | Daytrading, aggressiv | während der Börsenzeit, jede Minute |

## Die Regeln

| | Fels | Falke | Rakete |
|---|---|---|---|
| Aktien | 80 große US-Aktien | SPY, QQQ + 30 Mega Caps | 100 Wachstumsaktien |
| Vorauswahl | Aufwärtstrend, Markt (SPY) über 200-Tage-Linie | keine | Kurslücke ≥ 2 % zum Vortag **und** ≥ 2-faches Volumen in der Eröffnungsspanne |
| Richtung | nur Long | nur Long | Long und Short |
| Short nur wenn | – | – | Alpaca meldet „shortable" und „easy to borrow", Kurs > 10 $ |
| Kredit | nein | nein (Positionen ≤ Kontowert) | ja, alle Positionen zusammen ≤ 2 × Kontowert |
| Eröffnungsspanne | – | erste 15 Minuten (09:30–09:45) | erste 5 Minuten (09:30–09:35) |
| Mindestbreite der Spanne | – | 0,3 % des Kurses | 0,3 % des Kurses |
| Einstieg | Rücksetzer an die 20-Tage-Linie | 1-Minuten-Kerze schließt über dem Hoch der Spanne | Long: Kerze schließt über dem Hoch, Short: unter dem Tief |
| Einstiegszeit | nach Börsenschluss für den nächsten Tag | 09:45–11:30 | 09:35–11:30 |
| Stop | 1,5 × ATR unter dem Einstieg | Mitte der Spanne | Long: Tief der Spanne, Short: Hoch der Spanne |
| Ziel | 2 × Risiko | 2 × Risiko | halbe Position bei 2 × Risiko, Rest mit nachgezogenem Stop, Not-Ziel 5 × Risiko |
| Risiko pro Trade | 0,5 % | 0,25 % | 1 % |
| Max. Trades pro Tag | 2 neue | 2 | 4 |
| Max. pro Aktie am Tag | – | 1 Trade | 1 Trade |
| Max. Größe pro Aktie | 20 % des Kontos | 25 % des Kontos | 50 % des Kontos |
| Tagesgrenze | – | bei −0,75 % keine neuen Trades | bei −3 % keine neuen Trades |
| Konto-Notbremse | Pause bei 10 % unter Höchststand | 5 % unter Höchststand → Stopp bis zur Freigabe | 25 % unter Höchststand → Stopp bis zur Freigabe |
| Über Nacht | ja (höchstens 20 Tage) | nie | nie |

**Für alle Bots gilt:**

- Stückzahl = (Kontowert × Risiko) ÷ Abstand zum Stop, abgerundet, begrenzt durch die Grenzen oben.
- Jede Position hat ab der ersten Sekunde einen Stop **beim Broker** (Bracket-Order), nicht nur im Programm.
- Einstieg (Falke, Rakete) als Limit-Order 0,1 % über (Long) bzw. unter (Short) dem Schluss der Signal-Kerze.
  Ist sie nach 2 Minuten nicht ausgeführt, wird sie storniert.
- Falke und Rakete schließen um **15:50 Uhr New York** alle Positionen, stornieren alle Orders und
  prüfen danach, dass das Konto leer ist. An verkürzten Handelstagen 10 Minuten vor Schluss.
- Keine Optionen, keine Hebel-ETFs, kein Krypto.
- Alle Zeiten in New Yorker Zeit; Feiertage, halbe Tage und Sommerzeit kommen aus dem Alpaca-Kalender.
- Kursdaten: Alpaca IEX (Gratis-Plan).

**Rakete, Teilverkauf:** Die Position wird als zwei Orders gesendet. Die erste Hälfte hat ihr Ziel
bei 2 × Risiko. Ist sie verkauft, wandert der Stop der zweiten Hälfte auf den Einstiegskurs und folgt
dann dem besten Kurs im Abstand von 1 × Risiko. Bei nur 1 Aktie gibt es keinen Teilverkauf, das Ziel
liegt dann bei 2 × Risiko.

## Dateien

| Datei | Inhalt |
|---|---|
| `config.py` | **alle Zahlen** (Risiko, Grenzen, Uhrzeiten) |
| `universe.py` | **alle Aktienlisten** |
| `strategies.py`, `bot.py` | Fels (Logik und Ablauf) |
| `orb.py` | Logik von Falke und Rakete (Spanne, Signal, Stückzahl, Grenzen) – ohne Internet testbar |
| `daybot.py` | Ablauf von Falke und Rakete, Verbindung zu Alpaca |
| `backtest.py` | Test der Regeln mit alten Minutendaten |
| `bericht.py` | Auswertung der Protokolle |
| `tests/` | Offline-Tests |
| `logs/` | Tagesprotokolle als JSON |
| `state/` | Höchststand des Kontos und Konto-Notbremse |
| `.github/workflows/` | automatischer Betrieb bei GitHub |

## Auf dem Laptop

```
.venv\Scripts\python.exe tests\test_strategies.py
.venv\Scripts\python.exe tests\test_orb.py
.venv\Scripts\python.exe tests\test_daybot.py
.venv\Scripts\python.exe tests\test_bericht.py
```

Die Keys stehen in der Datei `.env` (Vorlage: `.env.example`). Diese Datei wird nie hochgeladen.

| Befehl | Was passiert |
|---|---|
| `python daybot.py falke --replay 2026-11-13` | spielt einen vergangenen Tag nach, sendet nie eine Order |
| `set DRY_RUN=true` und dann `python daybot.py falke` | echter Lauf, zeigt alles an, sendet keine Order |
| `python daybot.py falke` | echter Lauf im Paper-Konto bis zum Zwangsschluss |
| `python daybot.py falke --bis 12:00` | Lauf endet um 12:00 Uhr New York |
| `python daybot.py falke --kontrolle` | prüft nur, ob das Konto leer ist |
| `python daybot.py falke --freigeben` | gibt die Konto-Notbremse wieder frei |
| `python bot.py fels` | Fels-Lauf (nach US-Börsenschluss) |
| `python bericht.py` | Auswertung aller Protokolle → `reports/bericht.md` |
| `python backtest.py --run alle` | Backtest über 12 Monate |

Statt `falke` geht überall auch `rakete`. Zum Doppelklicken gibt es `start_fels.bat`,
`start_falke.bat` und `start_rakete.bat`.

## Betrieb bei GitHub (öffentliches Repo)

Die Bots laufen über GitHub Actions. Der Laptop muss dafür nicht an sein.

| Workflow | Start | Aufgabe |
|---|---|---|
| `daytrading.yml` | Mo–Fr 12:45 UTC | Falke und Rakete. Job „vormittag" wartet auf die Börsenöffnung und handelt bis 12:00 Uhr New York, Job „nachmittag" übernimmt bis zum Zwangsschluss. |
| `fels.yml` | Mo–Fr 21:30 UTC | Fels, danach Kontrolle „Falke- und Rakete-Konto leer?" und der Bericht |
| `notbremse.yml` | nur von Hand | Konto-Notbremse eines Bots freigeben |

- Ein GitHub-Job darf höchstens 6 Stunden laufen, der Börsentag dauert 6,5 Stunden. Deshalb zwei Jobs.
  Der zweite liest Positionen, Orders und die heutigen Trades neu von Alpaca und macht nahtlos weiter.
- GitHub startet geplante Läufe manchmal verspätet. Der Lauf beginnt deshalb weit vor der Börsenöffnung.
  Startet er trotzdem erst nach dem Ende der Eröffnungsspanne, handelt der Bot an dem Tag nicht und
  schreibt das ins Protokoll.
- Ist nach dem Zwangsschluss noch eine Position offen, schlägt der Job rot fehl.
- Jeder Workflow hat oben rechts den Knopf **Run workflow** mit dem Schalter **DRY_RUN** (voreingestellt: an).
- Die Protokolle (`logs/`), der Zustand (`state/`) und der Bericht werden automatisch ins Repo geschrieben.
  Vorher prüft `scripts/pruefe_upload.py`, dass keine Keys und keine Kontonummern darin stehen.

### Einrichten, Schritt für Schritt

1. Prüfen, dass nichts Geheimes hochgeladen wird: `.venv\Scripts\python.exe scripts\pruefe_upload.py`
2. Bei GitHub ein neues **öffentliches** Repository anlegen (ohne README, ohne .gitignore).
3. Den Ordner hochladen (mit Git oder GitHub Desktop). `.env`, `.venv/` und `data/` bleiben durch
   `.gitignore` auf dem Laptop.
4. Im Repo: **Settings → Secrets and variables → Actions → New repository secret**. Sechs Secrets anlegen,
   mit genau diesen Namen: `ALPACA_FELS_KEY`, `ALPACA_FELS_SECRET`, `ALPACA_FALKE_KEY`,
   `ALPACA_FALKE_SECRET`, `ALPACA_RAKETE_KEY`, `ALPACA_RAKETE_SECRET`.
5. Im Repo: **Settings → Actions → General → Workflow permissions → Read and write permissions**.
6. Unter **Actions** jeden Workflow einmal von Hand mit DRY_RUN = an starten und die Ausgabe ansehen.
7. Danach laufen die Bots von selbst nach Zeitplan.

Hinweis: GitHub schaltet geplante Workflows ab, wenn im Repo 60 Tage lang nichts passiert. Weil die Bots
täglich Protokolle speichern, tritt das hier nicht ein.

## Später auf einem Server oder dauerhaft am Laptop

Der Code braucht GitHub nicht. Auf einem Server oder Laptop reichen zwei geplante Aufgaben pro Handelstag:

- 15:00 Uhr deutscher Zeit: `start_falke.bat` und `start_rakete.bat` (die Bots warten selbst auf die
  Börsenöffnung und beenden sich nach dem Zwangsschluss).
- 22:45 Uhr deutscher Zeit: `start_fels.bat`.

Unter Windows: Aufgabenplanung → „Einfache Aufgabe erstellen" → Täglich → Programm starten → die `.bat`-Datei
auswählen. Der Rechner darf in der Zeit nicht in den Ruhezustand gehen. Auf Linux dasselbe mit `cron`.
Die Keys kommen dort aus einer `.env`-Datei im Projektordner.

## Backtest (zur Information)

Zeitraum 17.11.2025 bis 13.11.2026 (250 Handelstage), Alpaca-Minutendaten (IEX), Start 10.000 $,
Slippage 0,05 % pro Order, Einstieg frühestens in der Kerze nach dem Signal.

| | Falke | Rakete | davon Long | davon Short | SPY halten |
|---|---|---|---|---|---|
| Trades | 140 | 266 | 149 | 117 | |
| Trefferquote | 35,0 % | 45,9 % | 43,6 % | 48,7 % | |
| Schnitt pro Trade | −0,24 R | −0,11 R | −0,16 R | −0,04 R | |
| Ergebnis | −5,0 % | −20,6 % | −1.788 $ | −276 $ | +12,6 % |
| Max. Drawdown | 5,0 % | 26,6 % | | | 9,1 % |
| Kontostand am Ende | 9.497 $ | 7.936 $ | | | 11.264 $ |

**Ehrlich gesagt:** Beide Daytrading-Strategien haben in diesem Jahr Geld verloren und waren deutlich
schlechter als „SPY kaufen und halten". Bei beiden hat die Konto-Notbremse ausgelöst (Falke am
27.02.2026, Rakete am 19.05.2026), danach wurde im Backtest nicht mehr gehandelt.

Grenzen des Backtests: heutige Aktienlisten und heutiger Short-Status (in der Vergangenheit war das
anders), keine Leihgebühren und Zinsen, IEX zeigt nur etwa 2–3 % des gesamten Börsenhandels.

## Bekannte Grenzen

- Der Bot prüft einmal pro Minute. Zwischen zwei Prüfungen schützt nur der Stop beim Broker.
- Alpaca-Paper-Konten führen Orders einfacher aus als die echte Börse. Echte Ergebnisse wären schlechter.
- Der nachgezogene Stop der Rakete wird höchstens einmal pro Minute angepasst.
- Mit IEX-Daten fehlen bei manchen Aktien Kerzen in der Eröffnungsspanne; dann gibt es keinen Trade
  („zu wenig Kursdaten").
