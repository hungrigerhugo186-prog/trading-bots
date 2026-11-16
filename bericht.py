"""Fasst die Tages-Protokolle aus logs/ zusammen. Braucht kein Internet und keine Keys.

Aufruf:
    python bericht.py                 alle echten Laeufe (ohne DRY_RUN)
    python bericht.py --mit-dry-run   auch Testlaeufe mitzaehlen
    python bericht.py --ordner logs   anderer Protokoll-Ordner

Pro Bot: Anzahl Trades, Trefferquote, Durchschnitt in R, Gesamtergebnis, groesster
Drawdown, bei Rakete Long und Short getrennt, dazu der Vergleich mit "SPY halten"
ueber denselben Zeitraum. Das Ergebnis wird angezeigt und nach reports/bericht.md
geschrieben.
"""
import argparse
import glob
import json
import os

BOTS = ("fels", "falke", "rakete")


def load_logs(folder, with_dry=False):
    """{Bot: [Tages-Protokolle, nach Datum sortiert]}"""
    out = {b: [] for b in BOTS}
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                log = json.load(f)
        except (OSError, ValueError):
            continue
        if log.get("bot") not in out or "error" in log or "equity" not in log:
            continue
        if log.get("dry_run") and not with_dry:
            continue
        out[log["bot"]].append(log)
    for logs in out.values():
        logs.sort(key=lambda x: x["date"])
    return out


def max_drawdown(values):
    peak, worst = None, 0.0
    for v in values:
        peak = v if peak is None else max(peak, v)
        if peak and peak > 0:
            worst = max(worst, 1 - v / peak)
    return worst


def trade_stats(trades):
    n = len(trades)
    wins = sum(1 for t in trades if t["pnl_usd"] > 0)
    rs = [t["r"] for t in trades if t.get("r") is not None]
    return {"trades": n, "win_rate": wins / n * 100 if n else None,
            "avg_r": sum(rs) / len(rs) if rs else None,
            "pnl_usd": sum(t["pnl_usd"] for t in trades)}


def bot_report(logs):
    """Kennzahlen eines Bots aus seinen Tages-Protokollen. None, wenn es keine gibt."""
    if not logs:
        return None
    start = logs[0].get("equity_start") or logs[0]["equity"]
    curve = [start] + [l["equity"] for l in logs]
    trades = [t for l in logs for t in l.get("trades", []) if t.get("status", "geschlossen") == "geschlossen"]
    rep = trade_stats(trades)
    rep.update({
        "days": len(logs), "from": logs[0]["date"], "to": logs[-1]["date"],
        "start_equity": start, "end_equity": curve[-1],
        "return_pct": (curve[-1] / start - 1) * 100 if start else 0.0,
        "max_drawdown_pct": max_drawdown(curve) * 100,
        "long": trade_stats([t for t in trades if t.get("side") == "long"]),
        "short": trade_stats([t for t in trades if t.get("side") == "short"]),
        "warnings": sum(len(l.get("warnings", [])) for l in logs),
        "spy_return_pct": None,
    })
    first = next((l for l in logs if l.get("spy_prev_close")), None)
    last = next((l for l in reversed(logs) if l.get("spy_close")), None)
    if first and last:
        rep["spy_return_pct"] = (last["spy_close"] / first["spy_prev_close"] - 1) * 100
    return rep


def fmt(value, pattern):
    return "-" if value is None else pattern % value


def render(reports):
    """Tabelle als Text (Markdown, laesst sich auch im Terminal gut lesen)."""
    cols = []
    for name in BOTS:
        r = reports.get(name)
        if not r:
            continue
        cols.append((name.capitalize(), r, True))
        if name == "rakete":
            cols.append(("Rakete Long", r["long"], False))
            cols.append(("Rakete Short", r["short"], False))
    if not cols:
        return "Keine Protokolle gefunden. Die Bots muessen erst mindestens einen Tag gelaufen sein."
    rows = [
        ("Zeitraum", lambda r, full: "%s bis %s" % (r["from"], r["to"]) if full else ""),
        ("Handelstage", lambda r, full: str(r["days"]) if full else ""),
        ("Trades", lambda r, full: str(r["trades"])),
        ("Trefferquote", lambda r, full: fmt(r["win_rate"], "%.1f %%")),
        ("Durchschnitt in R", lambda r, full: fmt(r["avg_r"], "%+.2f R")),
        ("Gewinn/Verlust Trades", lambda r, full: "%+.2f $" % r["pnl_usd"]),
        ("Gesamtergebnis Konto", lambda r, full: "%+.2f %%" % r["return_pct"] if full else ""),
        ("Kontowert", lambda r, full: "%.2f $" % r["end_equity"] if full else ""),
        ("Groesster Drawdown", lambda r, full: "%.2f %%" % r["max_drawdown_pct"] if full else ""),
        ("SPY halten (gleicher Zeitraum)", lambda r, full: fmt(r["spy_return_pct"], "%+.2f %%") if full else ""),
        ("Warnungen im Protokoll", lambda r, full: str(r["warnings"]) if full else ""),
    ]
    lines = ["| | " + " | ".join(c[0] for c in cols) + " |", "|---|" + "---|" * len(cols)]
    for label, fn in rows:
        lines.append("| %s | " % label + " | ".join(fn(c[1], c[2]) for c in cols) + " |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Auswertung der Bot-Protokolle")
    ap.add_argument("--ordner", default="logs")
    ap.add_argument("--mit-dry-run", action="store_true")
    args = ap.parse_args()
    logs = load_logs(args.ordner, args.mit_dry_run)
    text = render({b: bot_report(l) for b, l in logs.items()})
    print(text)
    os.makedirs("reports", exist_ok=True)
    with open(os.path.join("reports", "bericht.md"), "w", encoding="utf-8") as f:
        f.write("# Bericht der Trading-Bots (Paper Trading)\n\n" + text + "\n")
    print("\nGespeichert: reports/bericht.md")


if __name__ == "__main__":
    main()
