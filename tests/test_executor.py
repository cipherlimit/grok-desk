"""STEP 13 test 4 (with the user's decision below_min_paper=trade, below_min_live=skip):
stack $200 -> $16 -> under $47.50 -> PAPER trades $16, LIVE skips; bump (when set) sends $47.50;
PAPER start $200 with skip -> the "every coin will be skipped" warning; pool liquidity $40,000 caps any size at $800."""
import pytest, db, links
from conftest import FakeSrc, run, PIKA
from bus import Bus
from agents import executor
from test_agents import pika_pair, _coin, _ex_msg

def _ex(conn, liq=50_000.0):
    return executor.Executor(conn, Bus(), FakeSrc(dex=[pika_pair(liquidity__usd=liq, priceUsd="0.000001")]))

def test_200_stack_16_paper_trades_as_is(conn, pushes):
    _coin(conn); db.add_balance(conn, 200)
    out = run(_ex(conn).handle(_ex_msg()))
    assert (out["status"], out["sizeUsd"]) == ("bought", 16.0)
    assert pushes[0][0].startswith("PAPER · Buy PIKA COIN · $16.00 · 8% of stack")
    assert pushes[0][1][0][0]["url"] == links.coin_url(PIKA, "solana")

def test_200_stack_16_live_skips(conn, pushes):
    _coin(conn); db.set_setting(conn, "desk.mode", "LIVE"); db.set_setting(conn, "desk.stack", 200)
    out = run(_ex(conn).handle(_ex_msg()))
    assert (out["status"], out["reason"]) == ("skipped", "under $47.50") and not pushes

def test_bump_sends_47_50(conn, pushes):
    _coin(conn); db.add_balance(conn, 200); db.set_setting(conn, "executor.below_min_paper", "bump")
    out = run(_ex(conn).handle(_ex_msg()))
    assert out["sizeUsd"] == 47.5 and "$47.50" in pushes[0][0]
    s = {**db.settings_all(conn), "executor.below_min_live": "bump"}
    assert executor.size_for(200, 0, 100_000, s, "LIVE")[0] == 47.5          # LIVE with bump set: $47.50 too

def test_skip_at_200_warns(conn):
    s = {**db.settings_all(conn), "executor.below_min_paper": "skip"}
    lvl, msg = executor.ticket_check(s, 200)
    assert lvl == "warn" and msg.startswith("every coin will be skipped, stack under $593.75")
    assert "bump" in msg

def test_bump_info_and_trade_info(conn):
    s = db.settings_all(conn)
    assert executor.ticket_check({**s, "executor.below_min_paper": "bump"}, 200)[1] == "tickets bumped to $47.50 = 23.8% of stack"
    assert executor.ticket_check(s, 200)[0] == "info"
    assert executor.ticket_check(s, 1240)[0] == "ok"

def test_liquidity_40000_caps_at_800(conn):
    s = db.settings_all(conn)
    size, cap, raw, reason = executor.size_for(1_000_000, 0, 40_000, s, "PAPER")
    assert size == 800.0 and cap == "liquidity" and raw == 80_000.0 and reason is None
    assert executor.size_for(1_000_000, 0, 40_000, s, "LIVE")[0] == 800.0

def test_ticket_check_runs_on_set_via_telegram(conn):
    import telegram
    d = telegram.Desk(conn, Bus(), None)
    reply, _ = d.cmd_set("below_min_paper", "skip")
    assert "every coin will be skipped" in reply
    reply, _ = d.cmd_set("stack", "1240")
    assert reply.splitlines()[0].endswith("-> $1,240.00")
