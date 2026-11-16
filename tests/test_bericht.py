"""Offline-Tests fuer bericht.py. Aufruf: python tests/test_bericht.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bericht  # noqa: E402


def write(folder, name, data):
    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
        json.dump(data, f)


def trade(side, pnl, r):
    return {"symbol": "X", "side": side, "pnl_usd": pnl, "r": r, "status": "geschlossen"}


def test_report():
    tmp = tempfile.mkdtemp(prefix="tb_bericht_")
    write(tmp, "2026-01-05_rakete.json", {
        "bot": "rakete", "date": "2026-01-05", "dry_run": False, "equity_start": 10_000.0,
        "equity": 10_150.0, "spy_prev_close": 500.0, "spy_close": 505.0, "warnings": [{"text": "x"}],
        "trades": [trade("long", 200.0, 2.0), trade("short", -50.0, -0.5),
                   {"symbol": "Y", "side": "long", "pnl_usd": 0.0, "r": 0.0, "status": "offen"}]})
    write(tmp, "2026-01-06_rakete.json", {
        "bot": "rakete", "date": "2026-01-06", "dry_run": False, "equity_start": 10_150.0,
        "equity": 9_947.0, "spy_prev_close": 505.0, "spy_close": 510.0,
        "trades": [trade("short", -100.0, -1.0), trade("short", -103.0, -1.03)]})
    write(tmp, "2026-01-06_falke.json", {"bot": "falke", "date": "2026-01-06", "dry_run": True,
                                         "equity_start": 10_000.0, "equity": 10_000.0, "trades": []})
    write(tmp, "2026-01-06_fels_FEHLER.json", {"bot": "fels", "date": "2026-01-06", "error": "x"})
    write(tmp, "kaputt.json", {"irgendwas": 1})
    logs = bericht.load_logs(tmp)
    assert [len(logs[b]) for b in ("fels", "falke", "rakete")] == [0, 0, 2]     # DRY_RUN zaehlt nicht
    assert len(bericht.load_logs(tmp, with_dry=True)["falke"]) == 1
    r = bericht.bot_report(logs["rakete"])
    assert r["trades"] == 4 and r["win_rate"] == 25.0                           # offener Trade zaehlt nicht
    assert abs(r["avg_r"] - (2.0 - 0.5 - 1.0 - 1.03) / 4) < 1e-9
    assert abs(r["return_pct"] - (-0.53)) < 1e-9
    assert abs(r["max_drawdown_pct"] - (1 - 9_947.0 / 10_150.0) * 100) < 1e-9
    assert r["long"]["trades"] == 1 and r["short"]["trades"] == 3
    assert abs(r["short"]["pnl_usd"] - (-253.0)) < 1e-9
    assert abs(r["spy_return_pct"] - 2.0) < 1e-9                                # 500 -> 510
    assert bericht.bot_report([]) is None
    text = bericht.render({b: bericht.bot_report(l) for b, l in logs.items()})
    assert "Rakete Long" in text and "Rakete Short" in text and "SPY halten" in text
    assert "Keine Protokolle" in bericht.render({})


if __name__ == "__main__":
    test_report()
    print("ok   test_report")
    print("Alle Tests bestanden.")
