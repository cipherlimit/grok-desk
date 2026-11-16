"""STEP 13 test 3: +25% then 13% off the top = trail exit; -6.1% = stop; 3h at +4% = timeout;
loosening the stop never moves an open stop down. "set stop to -8%": a new PAPER position dropped 8.1%
-> push, Activity row and chart mark read "-8%", the Risk pill reads "stop -8%"; a position opened at -6% still shows -6%."""
import pytest, db, api, links, telegram
from conftest import FakeSrc, Seq, run, PIKA
from bus import Bus
from agents import risk, executor
from test_agents import pika_pair, _coin, _ex_msg, ago

S = lambda c: db.settings_all(c)
POS = {"entry_price": 1.0, "peak_price": 1.0, "stop_price": 0.94, "trail_active": 0}

def test_trail_after_plus_25_then_13_off_top(conn):
    p, r = risk.evaluate(POS, 1.25, 0.5, S(conn)); assert r is None and p["trail_active"]
    assert risk.evaluate(p, 1.25 * 0.87, 0.6, S(conn))[1] == "trail"

def test_minus_6_1_is_stop(conn):
    assert risk.evaluate(POS, 0.939, 0.1, S(conn))[1] == "stop"
    assert risk.evaluate(POS, 0.941, 0.1, S(conn))[1] is None

def test_3h_at_plus_4_is_timeout(conn):
    assert risk.evaluate(POS, 1.04, 3.0, S(conn))[1] == "timeout"
    assert risk.evaluate(POS, 1.04, 2.9, S(conn))[1] is None

def test_loosening_never_moves_an_open_stop_down():
    assert risk.tightened_stop(1.0, 0.94, 8) == 0.94
    assert risk.tightened_stop(1.0, 0.94, 4) == pytest.approx(0.96)

def _open(conn, opened_min=1, mint=PIKA):
    db.add_balance(conn, 200)
    s = S(conn)
    executor.Executor(conn, Bus(), None).fill(mint, 1.0, 47.5, 23.8, "PAPER", s)
    return conn.execute("SELECT * FROM positions WHERE mint=? ORDER BY id DESC", (mint,)).fetchone()

def test_set_stop_minus_8_new_position(conn, pushes):
    _coin(conn)
    reply, _ = telegram.Desk(conn, Bus(), None).cmd_set("stop", "-8%")
    assert reply.splitlines()[0] == "stop -6% -> -8%"
    assert api.card_risk(conn, S(conn), db.night_bounds()[0])["pill_stop"] == "stop -8%"
    p = _open(conn)
    assert api.pos_stop_pct(dict(p)) == 8
    run(risk.Risk(conn, Bus(), FakeSrc(dex=[pika_pair(priceUsd="0.919", liquidity__usd=5e4)])).poll())
    t = pushes[-1][0]
    assert t.startswith("PIKA COIN hit the -8% stop · -$")
    assert pushes[-1][1][0][0]["url"] == links.coin_url(PIKA, "solana")
    assert conn.execute("SELECT subtitle FROM activity WHERE kind='stopped'").fetchone()[0] == "Risk · -8% stop"
    mark = [m for m in api.chart_doc(conn, S(conn))["marks"] if m["kind"] == "stop"][0]
    assert mark["text"].startswith("-8% · -$") and mark["color"] == "#ff453a"

def test_position_opened_at_6_keeps_6_after_set_8(conn, pushes):
    _coin(conn)
    p = _open(conn)                                       # opened at -6%
    telegram.Desk(conn, Bus(), None).cmd_set("stop", "8")
    run(risk.Risk(conn, Bus(), FakeSrc(dex=[pika_pair(priceUsd="0.97", liquidity__usd=5e4)])).poll())
    p = conn.execute("SELECT * FROM positions").fetchone()
    assert p["closed_at"] is None and api.pos_stop_pct(dict(p)) == 6        # still shows -6%
    assert api.state_doc(conn, S(conn))["open_positions"][0]["stop_pct"] == 6
    run(risk.Risk(conn, Bus(), FakeSrc(dex=[pika_pair(priceUsd="0.935", liquidity__usd=5e4)])).poll())
    assert pushes[-1][0].startswith("PIKA COIN hit the -6% stop")
