"""Offline-Tests des Live-Bots (daybot.py) mit einem Schein-Broker.

Der Schein-Broker tut so, als waere er Alpaca: Er liefert kuenstliche Kurse, merkt
sich jede Order und laesst sich die Uhr stellen. So wird geprueft, was der Bot in
welcher Lage senden wuerde - ohne Internet und ohne Keys.

Aufruf: python tests/test_daybot.py
"""
import datetime as dt
import json
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daybot  # noqa: E402
import orb  # noqa: E402
from config import DAY_BOTS  # noqa: E402
from test_orb import (DAY, day, falke_day, fill, put, rakete_day,  # noqa: E402
                      rakete_short_day)

FALKE, RAKETE = DAY_BOTS["falke"], DAY_BOTS["rakete"]
T = dt.time
D = dt.date.fromisoformat(DAY)
NY = daybot.NY


def at(hhmm, sec=6):
    h, m = hhmm.split(":")
    return dt.datetime(D.year, D.month, D.day, int(h), int(m), sec, tzinfo=NY)


def order(client_id, symbol, side, kind, qty, filled=0.0, avg=None, limit=None, stop=None,
          status="new", created=None, filled_at=None, parent=None):
    return {"id": "o%d" % id(client_id) + symbol + kind + str(qty) + str(stop), "client_id": client_id,
            "symbol": symbol, "side": side, "type": kind, "qty": float(qty),
            "filled_qty": float(filled), "filled_avg": avg, "limit": limit, "stop": stop,
            "status": status, "created": created, "filled_at": filled_at, "parent": parent}


def history(prev_close=100.0, first_vol=2000.0, minutes=5):
    """12 Vortage in Kerzen der Spannen-Laenge: Schluss prev_close, Start-Volumen first_vol."""
    frames = []
    for i in range(12):
        d = "2025-12-%02d" % (10 + i)
        idx = pd.date_range(d + " 09:30", d + " 16:00", freq="%dmin" % minutes,
                            tz="America/New_York", inclusive="left")
        f = pd.DataFrame({"open": prev_close, "high": prev_close, "low": prev_close,
                          "close": prev_close, "volume": 100.0}, index=idx)
        f.iloc[0, f.columns.get_loc("volume")] = first_vol
        frames.append(f)
    return pd.concat(frames)


class FakeBroker:
    def __init__(self, bars, now, equity=10_000.0, last_equity=10_000.0, session=(T(9, 30), T(16, 0))):
        self.day_bars, self.t = bars, now
        self.equity, self.last_equity, self.sess = equity, last_equity, session
        self.pos, self.orders, self.calls = [], [], []
        self.ctx_bars, self.shortable_set, self.peak = {}, set(), 0.0
        self.fail_bars, self.stuck, self.reject = False, False, False

    def now(self):
        return self.t

    def sleep_until(self, when):
        self.t = max(self.t, when)

    def session(self, d):
        return self.sess

    def account(self):
        return {"equity": self.equity, "last_equity": self.last_equity, "cash": self.equity}

    def positions(self):
        return [dict(p) for p in self.pos]

    def orders_today(self, d):
        return [dict(o) for o in self.orders]

    def equity_peak(self, since=None):
        return self.peak

    def shortable(self, sym):
        return sym in self.shortable_set

    def bars(self, symbols, start, end, minutes=1):
        if self.fail_bars:
            raise ConnectionError("Netzwerk weg")
        src = self.day_bars if minutes == 1 else self.ctx_bars
        return {s: src[s][(src[s].index >= start) & (src[s].index <= end)] for s in symbols if s in src}

    def daily_closes(self, symbol, end):
        return [(D - dt.timedelta(days=1), 500.0), (D, 505.0)]

    def submit_bracket(self, symbol, side, qty, limit, stop, target, client_id):
        if self.reject:
            raise RuntimeError("insufficient buying power")
        self.calls.append(("bracket", symbol, side, qty, limit, stop, target, client_id))
        self.orders.append(order(client_id, symbol, "buy" if side == "long" else "sell", "limit",
                                 qty, limit=limit, created=self.t))

    def submit_stop(self, symbol, exit_side, qty, stop):
        self.calls.append(("stop", symbol, exit_side, qty, stop))

    def replace_stop(self, order_id, stop):
        self.calls.append(("replace", order_id, stop))

    def cancel(self, order_id):
        self.calls.append(("cancel", order_id))
        for o in self.orders:
            if o["id"] == order_id:
                o["status"] = "canceled"

    def cancel_all(self):
        self.calls.append(("cancel_all",))
        for o in self.orders:
            if o["status"] not in daybot.DONE:
                o["status"] = "canceled"

    def close_all(self):
        self.calls.append(("close_all",))
        if not self.stuck:
            self.pos = []

    def sent(self, kind):
        return [c for c in self.calls if c[0] == kind]


def make(name, bars, now, tmp, dry=False, cfg=None, **kw):
    br = FakeBroker(bars, now, **kw)
    bot = daybot.DayBot(name, cfg or DAY_BOTS[name], br, dry_run=dry,
                        log_dir=os.path.join(tmp, "logs"), state_dir=os.path.join(tmp, "state"),
                        out=lambda *_: None)
    bot.symbols = sorted(bars)
    return bot, br


def reasons(bot):
    return " | ".join("%s %s %s" % (d["symbol"], d["action"], d["reason"]) for d in bot.log["decisions"])


def tmpdir():
    return tempfile.mkdtemp(prefix="tb_test_")


# --- Order-Kennung und Geheimnisse ------------------------------------------
def test_ids_and_clean():
    cid = daybot.make_id("rakete", D, "BRK.B", "short", "R", 104.6, 2.6)
    assert cid == "rakete-20260105-BRK.B-S-R-10460-260"
    assert daybot.parse_id(cid, "rakete", D) == {"symbol": "BRK.B", "side": "short", "part": "R",
                                                 "entry": 104.6, "risk": 2.6}
    assert daybot.parse_id(cid, "falke", D) is None                    # anderer Bot
    assert daybot.parse_id(cid, "rakete", D + dt.timedelta(days=1)) is None   # anderer Tag
    assert daybot.parse_id("irgendwas", "rakete", D) is None
    os.environ["ALPACA_TEST_KEY"] = "geheimer-testwert-0123456789"
    try:
        txt = daybot.clean("Fehler bei geheimer-testwert-0123456789, Konto 0a1b2c3d-1111-2222-3333-444455556666")
        assert "geheimer" not in txt and "0a1b2c3d" not in txt and "***" in txt and "<id>" in txt
    finally:
        del os.environ["ALPACA_TEST_KEY"]


# --- Falke: Signal -> genau eine Bracket-Order ------------------------------
def test_falke_entry():
    bot, br = make("falke", {"AAPL": falke_day()}, at("09:25"), tmpdir())
    assert bot.prepare(at("09:25")) is True
    assert (bot.range_end, bot.last_entry, bot.flatten_t) == (T(9, 45), T(11, 30), T(15, 50))
    bot.step(at("09:50"))                    # Kerze 09:50 laeuft noch -> noch kein Signal
    assert br.sent("bracket") == []
    bot.step(at("09:51"))                    # Kerze 09:50 ist fertig -> Order
    (_, sym, side, qty, limit, stop, target, cid), = br.sent("bracket")
    assert (sym, side, qty, limit, stop, target) == ("AAPL", "long", 15, 101.6, 100.0, 104.8)
    assert daybot.parse_id(cid, "falke", D)["part"] == "T"
    bot.step(at("09:52"))                    # nie doppelt kaufen
    assert len(br.sent("bracket")) == 1
    assert "Order gesendet" in reasons(bot)


def test_dry_run_sends_nothing():
    tmp = tmpdir()
    bot, br = make("falke", {"AAPL": falke_day()}, at("09:25"), tmp, dry=True)
    bot.prepare(at("09:25"))
    for m in ("09:51", "09:52", "09:53"):
        bot.step(at(m))
    assert br.calls == []                    # keine einzige Order, kein Storno, nichts
    assert len(bot.dry_orders) == 1 and bot.dry_orders[0]["qty"] == 15
    assert "WUERDE handeln" in reasons(bot)
    bot.write_log()
    assert not os.path.exists(os.path.join(tmp, "state", "falke.json"))   # DRY_RUN aendert nichts Dauerhaftes


def test_falke_filters():
    narrow = day(100.0)
    put(narrow, "09:31", 100, 100.2, 100, 100.1)
    put(narrow, "09:50", 100.1, 100.6, 100.1, 100.5)
    bot, br = make("falke", {"AAA": narrow}, at("09:25"), tmpdir())
    bot.prepare(at("09:25"))
    bot.step(at("09:51"))
    assert br.sent("bracket") == [] and "Spanne nur" in reasons(bot)
    # Abgelehnte Order: kein Absturz, kein zweiter Versuch
    bot, br = make("falke", {"AAPL": falke_day()}, at("09:25"), tmpdir())
    br.reject = True
    bot.prepare(at("09:25"))
    bot.step(at("09:51"))
    br.reject = False
    bot.step(at("09:52"))
    assert br.sent("bracket") == [] and "Order abgelehnt" in bot.log["warnings"][0]["text"]


# --- Rakete: Long und Short, Teil-Orders ------------------------------------
def test_rakete_long_and_short():
    bars = {"UP": rakete_day("09:40", 104.6), "DOWN": rakete_short_day(91.0), "NOSH": rakete_short_day(91.0),
            "FLAT": rakete_day("09:40", 104.6)}
    bot, br = make("rakete", bars, at("09:25"), tmpdir())
    br.ctx_bars = {s: history(100.0, 2000.0) for s in ("UP", "DOWN", "NOSH")}
    br.ctx_bars["FLAT"] = history(103.0, 2000.0)          # keine Kursluecke
    br.shortable_set = {"DOWN"}
    bot.prepare(at("09:25"))
    bot.step(at("09:41"))
    sent = {(c[1], daybot.parse_id(c[7], "rakete", D)["part"]): c for c in br.sent("bracket")}
    assert set(sent) == {("UP", "T"), ("UP", "R"), ("DOWN", "T"), ("DOWN", "R")}, sent.keys()
    assert sent[("UP", "T")][2:7] == ("long", 19, 104.6, 102.0, 109.8)      # Haelfte: Ziel 2R
    assert sent[("UP", "R")][2:7] == ("long", 19, 104.6, 102.0, 117.6)      # Rest: Not-Ziel 5R
    assert sent[("DOWN", "T")][2:7] == ("short", 19, 95.4, 98.0, 90.2)      # Short: Stop = Hoch
    assert sent[("DOWN", "R")][2:7] == ("short", 19, 95.4, 98.0, 82.4)
    text = reasons(bot)
    assert "NOSH kein Trade Short-Signal, aber: nicht als shortable" in text
    assert "FLAT kein Trade Kursluecke" in text
    # Short nur ueber der Mindest-Kursgrenze
    bot, br = make("rakete", {"DOWN": rakete_short_day(91.0)}, at("09:25"), tmpdir(),
                   cfg=dict(RAKETE, min_short_price=200.0))
    br.ctx_bars, br.shortable_set = {"DOWN": history(100.0, 2000.0)}, {"DOWN"}
    bot.prepare(at("09:25"))
    bot.step(at("09:41"))
    assert br.sent("bracket") == [] and "nicht ueber 200" in reasons(bot)
    # Nur 1 Aktie: kein Teilverkauf, eine einzige Order mit Ziel 2R
    bot, br = make("rakete", {"UP": rakete_day("09:40", 104.6)}, at("09:25"), tmpdir(), equity=260.0,
                   last_equity=260.0)
    br.ctx_bars = {"UP": history(100.0, 2000.0)}
    bot.prepare(at("09:25"))
    bot.step(at("09:41"))
    (c,) = br.sent("bracket")
    assert c[3] == 1 and c[6] == 109.8


# --- Kredit-Grenze im Live-Bot ----------------------------------------------
def test_credit_limits():
    # Rakete: schon 19.000 $ offen bei 10.000 $ Konto -> nur noch 1.000 $ Kaufkraft = 9 Stueck
    bot, br = make("rakete", {"UP": rakete_day("09:40", 104.6)}, at("09:25"), tmpdir())
    br.ctx_bars = {"UP": history(100.0, 2000.0)}
    br.pos = [{"symbol": "OLD", "qty": -190.0, "entry": 100.0, "price": 100.0}]   # Short zaehlt mit
    br.orders = [order("x", "OLD", "buy", "stop", 190, stop=103.0, status="held")]
    bot.prepare(at("09:25"))
    bot.step(at("09:41"))
    assert sum(c[3] for c in br.sent("bracket")) == 9
    # Falke: kein Kredit. 9.950 $ investiert -> keine neue Position
    bot, br = make("falke", {"AAPL": falke_day()}, at("09:25"), tmpdir())
    br.pos = [{"symbol": "OLD", "qty": 99.5, "entry": 100.0, "price": 100.0}]
    br.orders = [order("x", "OLD", "sell", "stop", 99.5, stop=98.0, status="held")]
    bot.prepare(at("09:25"))
    bot.step(at("09:51"))
    assert br.sent("bracket") == [] and "Stueckzahl 0" in reasons(bot)


# --- Verhalten nach Neustart ------------------------------------------------
def test_restart():
    tmp = tmpdir()
    bars = {"AAA": falke_day(), "BBB": falke_day(), "CCC": falke_day()}
    put(bars["CCC"], "09:50", 100, 100, 100, 100)          # CCC bricht erst um 10:00 aus
    fill(bars["CCC"], "09:51", "10:00", 100.0)
    put(bars["CCC"], "10:00", 100, 101.6, 100, 101.5)
    first, br1 = make("falke", bars, at("09:25"), tmp)
    first.prepare(at("09:25"))
    first.write_log()
    # Neustart um 09:50:30: bei Alpaca liegt schon ein ausgefuehrter Kauf von AAA
    bot, br = make("falke", bars, at("09:50", 30), tmp)
    cid = daybot.make_id("falke", D, "AAA", "long", "T", 101.6, 1.6)
    br.orders = [order(cid, "AAA", "buy", "limit", 15, filled=15, avg=101.5, limit=101.6,
                       status="filled", created=at("09:48"), filled_at=at("09:48", 20)),
                 order("", "AAA", "sell", "limit", 15, limit=104.8, parent=cid),
                 order("", "AAA", "sell", "stop", 15, stop=100.0, status="held", parent=cid)]
    br.pos = [{"symbol": "AAA", "qty": 15.0, "entry": 101.5, "price": 101.5}]
    bot.prepare(at("09:50", 30))
    assert bot.no_entries is None                          # erster Start war rechtzeitig
    bot.step(at("09:51"))
    assert [c[1] for c in br.sent("bracket")] == ["BBB"]   # AAA NICHT noch einmal
    assert br.sent("stop") == []                           # AAA ist durch seinen Stop geschuetzt
    bot.step(at("09:52"))
    assert bot.trades_today() == 2                         # Tageszaehler stimmt nach dem Neustart
    bot.step(at("10:01"))                                  # drittes Signal -> Limit 2 Trades/Tag
    assert [c[1] for c in br.sent("bracket")] == ["BBB"]
    assert "schon 2 Trades heute" in reasons(bot)
    # Tagesverlust stimmt auch nach Neustart: er kommt vom Kontowert bei Alpaca
    bot, br = make("falke", {"AAPL": falke_day()}, at("09:46"), tmp, equity=9_920.0)
    bot.prepare(at("09:46"))
    bot.step(at("09:51"))
    assert br.sent("bracket") == [] and "Tagesgrenze" in reasons(bot)


def test_missed_opening_range():
    bars = {"AAPL": falke_day()}
    put(bars["AAPL"], "09:50", 100, 100, 100, 100)
    fill(bars["AAPL"], "09:51", "10:05", 100.0)
    put(bars["AAPL"], "10:05", 100, 101.6, 100, 101.5)
    bot, br = make("falke", bars, at("10:00"), tmpdir())   # allererster Start erst um 10:00
    bot.prepare(at("10:00"))
    bot.step(at("10:06"))
    assert br.sent("bracket") == [] and "verpasst" in reasons(bot)
    # Verpasstes Signal (Bot war 3 Minuten weg): nicht hinterherkaufen
    tmp = tmpdir()
    bot, br = make("falke", {"AAPL": falke_day()}, at("09:25"), tmp)
    bot.prepare(at("09:25"))
    bot.step(at("09:55"))                                  # Signal war um 09:50
    assert br.sent("bracket") == [] and "Signal um 09:50 verpasst" in reasons(bot)


# --- Einstiegs-Order nach 2 Minuten stornieren ------------------------------
def test_cancel_stale():
    bot, br = make("falke", {"AAPL": falke_day()}, at("09:25"), tmpdir())
    bot.prepare(at("09:25"))
    br.t = at("09:51")                                     # Uhr des Schein-Brokers: Order entsteht 09:51
    bot.step(at("09:51"))
    bot.step(at("09:52"))
    assert br.sent("cancel") == []                         # erst 1 Minute alt
    bot.step(at("09:53"))
    assert len(br.sent("cancel")) == 1 and "Order storniert" in reasons(bot)
    bot.step(at("09:54"))
    assert len(br.sent("bracket")) == 1                    # kein neuer Versuch fuer dieselbe Aktie


# --- Position ohne Stop -> sofort Stop anlegen + Warnung ---------------------
def test_missing_stop():
    bot, br = make("falke", {"AAPL": day(100.0)}, at("10:00"), tmpdir())
    br.pos = [{"symbol": "XYZ", "qty": 10.0, "entry": 50.0, "price": 50.0},
              {"symbol": "SHRT", "qty": -10.0, "entry": 50.0, "price": 50.0}]
    cid = daybot.make_id("falke", D, "KNOWN", "long", "T", 101.6, 1.6)
    br.orders = [order(cid, "KNOWN", "buy", "limit", 15, filled=15, avg=101.5, status="filled",
                       created=at("09:48"), filled_at=at("09:48"))]
    br.pos.append({"symbol": "KNOWN", "qty": 15.0, "entry": 101.5, "price": 101.0})
    bot.prepare(at("10:00"))
    bot.step(at("10:01"))
    stops = {c[1]: c for c in br.sent("stop")}
    assert stops["XYZ"] == ("stop", "XYZ", "sell", 10.0, 49.0)       # Not-Stop 2 % unter dem Kurs
    assert stops["SHRT"] == ("stop", "SHRT", "buy", 10.0, 51.0)      # Short: Stop darueber
    assert stops["KNOWN"] == ("stop", "KNOWN", "sell", 15.0, 100.0)  # geplanter Stop aus der Order-Kennung
    assert sum("OHNE Stop-Order" in w["text"] for w in bot.log["warnings"]) == 3


# --- Teilverkauf erfolgt -> Stop der zweiten Haelfte nachziehen --------------
def test_trailing_runner():
    d = rakete_day("09:40", 104.6)
    put(d, "10:00", 104.6, 112.0, 104.6, 111.0)            # bester Kurs 112
    bot, br = make("rakete", {"UP": d}, at("09:25"), tmpdir())
    br.ctx_bars = {"UP": history(100.0, 2000.0)}
    t_id = daybot.make_id("rakete", D, "UP", "long", "T", 104.6, 2.6)
    r_id = daybot.make_id("rakete", D, "UP", "long", "R", 104.6, 2.6)
    entry = dict(filled=19, avg=104.5, limit=104.6, status="filled", created=at("09:41"),
                 filled_at=at("09:41", 30))
    stop = order("", "UP", "sell", "stop", 19, stop=102.0, status="held", parent=r_id)
    br.orders = [order(t_id, "UP", "buy", "limit", 19, **entry),
                 order(r_id, "UP", "buy", "limit", 19, **entry),
                 order("", "UP", "sell", "limit", 19, limit=117.6, parent=r_id), stop]
    stop_t = order("", "UP", "sell", "stop", 19, stop=102.0, status="held", parent=t_id)
    br.orders.append(stop_t)                               # Stop der ersten Haelfte
    br.pos = [{"symbol": "UP", "qty": 38.0, "entry": 104.5, "price": 111.0}]
    bot.prepare(at("09:25"))
    bot.step(at("10:01"))
    assert br.sent("replace") == []                        # noch volle Position: kein Teilverkauf erfolgt
    br.pos[0]["qty"] = 19.0                                # erste Haelfte wurde bei 2R verkauft
    stop_t["status"] = "canceled"                          # ihr Stop ist damit erledigt
    bot.step(at("10:02"))
    assert br.sent("replace") == [("replace", stop["id"], 109.4)]   # 112 - 1R (2,6), mind. Einstieg
    stop["stop"] = 109.4
    bot.step(at("10:03"))
    assert len(br.sent("replace")) == 1                    # Stop wandert nie zurueck
    assert br.sent("stop") == []


# --- Zwangsschluss um 15:50 -------------------------------------------------
def test_flatten():
    tmp = tmpdir()
    bot, br = make("falke", {"AAPL": day(100.0)}, at("15:48"), tmp)
    cid = daybot.make_id("falke", D, "AAPL", "long", "T", 101.6, 1.6)
    br.orders = [order(cid, "AAPL", "buy", "limit", 15, filled=15, avg=101.5, status="filled",
                       created=at("09:51"), filled_at=at("09:51")),
                 order("", "AAPL", "sell", "stop", 15, stop=100.0, status="held", parent=cid)]
    br.pos = [{"symbol": "AAPL", "qty": 15.0, "entry": 101.5, "price": 100.0}]
    code = bot.run()
    assert code == 0 and br.sent("close_all") and br.sent("cancel_all")
    assert br.pos == [] and br.t.time() < T(15, 52)
    saved = json.load(open(os.path.join(tmp, "logs", "%s_falke.json" % DAY), encoding="utf-8"))
    assert saved["flatten"]["konto_leer"] is True and saved["flatten"]["time"] == "15:50"
    assert saved["spy_prev_close"] == 500.0 and saved["spy_close"] == 505.0
    # Position laesst sich nicht schliessen -> Ende-Code 2 (Job schlaegt sichtbar fehl)
    bot, br = make("falke", {"AAPL": day(100.0)}, at("15:49"), tmpdir())
    br.pos = [{"symbol": "AAPL", "qty": 15.0, "entry": 101.5, "price": 100.0}]
    br.stuck = True
    assert bot.run() == 2
    assert bot.log["flatten"]["konto_leer"] is False and bot.log["flatten"]["positions_left"] == ["AAPL"]
    # Verkuerzter Handelstag (Schluss 13:00): Zwangsschluss schon um 12:50
    bot, br = make("falke", {"AAPL": day(100.0)}, at("12:49"), tmpdir(), session=(T(9, 30), T(13, 0)))
    br.pos = [{"symbol": "AAPL", "qty": 15.0, "entry": 101.5, "price": 100.0}]
    assert bot.run() == 0 and bot.log["flatten"]["time"] == "12:50" and br.pos == []
    # Start nach Boersenschluss mit offener Position -> Ende-Code 2
    bot, br = make("falke", {"AAPL": day(100.0)}, at("16:05"), tmpdir())
    br.pos = [{"symbol": "AAPL", "qty": 15.0, "entry": 101.5, "price": 100.0}]
    assert bot.run() == 2
    bot, br = make("falke", {"AAPL": day(100.0)}, at("16:05"), tmpdir())
    assert bot.run() == 0


def test_run_until_and_no_trading_day():
    bot, br = make("falke", {"AAPL": falke_day()}, at("09:20"), tmpdir())
    assert bot.run(until=T(10, 0)) == 0                    # wartet auf 09:30, endet um 10:00
    assert len(br.sent("bracket")) == 1 and T(10, 0) <= br.t.time() < T(10, 2)
    assert "Lauf endet" in reasons(bot) and br.sent("close_all") == []
    bot, br = make("falke", {"AAPL": falke_day()}, at("09:20"), tmpdir(), session=None)
    assert bot.run() == 0 and "kein US-Handelstag" in reasons(bot) and br.calls == []


# --- Konto-Notbremse --------------------------------------------------------
def test_account_halt():
    tmp = tmpdir()
    os.makedirs(os.path.join(tmp, "state"))
    with open(os.path.join(tmp, "state", "falke.json"), "w") as f:
        json.dump({"peak_equity": 10_000.0, "halted": False}, f)
    bot, br = make("falke", {"AAPL": falke_day()}, at("09:25"), tmp, equity=9_500.0, last_equity=9_500.0)
    bot.prepare(at("09:25"))
    bot.step(at("09:51"))
    assert br.sent("bracket") == [] and "Notbremse" in reasons(bot)
    assert json.load(open(os.path.join(tmp, "state", "falke.json")))["halted"] is True
    # Bleibt aktiv, auch wenn das Konto sich erholt und der Bot neu startet
    bot, br = make("falke", {"AAPL": falke_day()}, at("09:25"), tmp, equity=9_990.0, last_equity=9_990.0)
    bot.prepare(at("09:25"))
    bot.step(at("09:51"))
    assert br.sent("bracket") == []
    # Freigabe durch den Nutzer: neuer Hoechststand = heutiger Kontowert, dann wird wieder gehandelt
    bot.release()
    st = json.load(open(os.path.join(tmp, "state", "falke.json")))
    assert st["halted"] is False and st["peak_equity"] == 9_990.0
    bot, br = make("falke", {"AAPL": falke_day()}, at("09:25"), tmp, equity=9_990.0, last_equity=9_990.0)
    bot.prepare(at("09:25"))
    bot.step(at("09:51"))
    assert len(br.sent("bracket")) == 1
    # Ohne Zustandsdatei kommt der Hoechststand von Alpaca; Rakete bremst erst bei 25 %
    bot, br = make("rakete", {"UP": rakete_day("09:40", 104.6)}, at("09:25"), tmpdir(), equity=7_600.0,
                   last_equity=7_600.0)
    br.peak, br.ctx_bars = 10_000.0, {"UP": history(100.0, 2000.0)}
    bot.prepare(at("09:25"))
    bot.step(at("09:41"))
    assert len(br.sent("bracket")) == 2
    br.equity = 7_500.0
    bot.step(at("09:42"))
    assert bot.state["halted"] is True


# --- Netzwerkfehler ---------------------------------------------------------
def test_network_error():
    bot, br = make("falke", {"AAPL": falke_day()}, at("09:40"), tmpdir())
    br.fail_bars = True
    assert bot.run(until=T(9, 53)) == 0                    # kein Absturz
    assert br.sent("bracket") == []                        # im Zweifel keine Order
    assert any("uebersprungen" in w["text"] for w in bot.log["warnings"])


# --- Protokoll: Trades mit Ergebnis in $ und R ------------------------------
def test_trade_log():
    tmp = tmpdir()
    bot, br = make("rakete", {"UP": day(100.0)}, at("12:00"), tmp)
    t_id = daybot.make_id("rakete", D, "UP", "long", "T", 104.6, 2.6)
    r_id = daybot.make_id("rakete", D, "UP", "long", "R", 104.6, 2.6)
    e = dict(filled=19, avg=104.5, limit=104.6, status="filled", created=at("09:41"), filled_at=at("09:41"))
    br.orders = [order(t_id, "UP", "buy", "limit", 19, **e), order(r_id, "UP", "buy", "limit", 19, **e),
                 order("", "UP", "sell", "limit", 19, filled=19, avg=109.8, limit=109.8, status="filled",
                       filled_at=at("10:00"), parent=t_id),
                 order("", "UP", "sell", "stop", 19, filled=19, avg=106.0, stop=106.0, status="filled",
                       filled_at=at("10:30"), parent=r_id)]
    bot.prepare(at("12:00"))
    bot.step(at("12:01"))
    bot.write_log()
    (t,) = json.load(open(os.path.join(tmp, "logs", "%s_rakete.json" % DAY), encoding="utf-8"))["trades"]
    assert (t["symbol"], t["side"], t["qty"], t["status"]) == ("UP", "long", 38, "geschlossen")
    assert t["stop"] == 102.0 and t["target"] == 109.8 and t["exit_reason"] == "Nachgezogener Stop"
    assert abs(t["pnl_usd"] - (19 * 5.3 + 19 * 1.5)) < 0.01
    assert abs(t["r"] - (19 * 5.3 + 19 * 1.5) / (38 * 2.5)) < 0.01
    text = open(os.path.join(tmp, "logs", "%s_rakete.json" % DAY), encoding="utf-8").read()
    assert '"id"' not in text and "client_id" not in text  # keine Order-Kennungen im Protokoll


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)
             and v.__module__ == "__main__"]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print("Alle %d Tests bestanden." % len(tests))
