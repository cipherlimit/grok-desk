"""STEP 13 test 6. Offline: one recorded coin end to end through PAPER (Auditor 8/8 -> Analyst rules ->
Executor fill -> Activity). Live: REPLAY_LIVE=1 runs tools/replay.py on the recorded migrations."""
import os, subprocess, sys
from pathlib import Path
import pytest, db, links
from conftest import raw, run, PIKA
from bus import Bus
from agents import auditor, analyst, executor
from test_agents import _audit_src, _scan_msg, _coin, pika_pair

def test_pipeline_paper_end_to_end(conn, pushes):
    _coin(conn); db.add_balance(conn, 200); bus = Bus()
    g = raw("insiders_probe/graph_WIGGLES_2kWx47CUrcXtzptFNnbSXKq3vo1ebqyLBhRytKRGpump.json")
    src = _audit_src(g)
    src.r["dex"] = [pika_pair(liquidity__usd=50_000.0, priceUsd="0.000001", volume__h1=30000, txns__h1__buys=200,
                              priceChange__m5=-6, priceChange__h1=8)]
    out = run(auditor.Auditor(conn, bus, src).audit(_scan_msg()))
    assert out["passed"]
    msg = bus.queues["analyst"].get_nowait()
    for l in msg["auditor"]["layers"]:                      # WIGGLES has 4 bundled wallets (organic x0.5): use a 2-wallet bundle
        if l["n"] == 7: l["value"] = "2 bundled wallets"
    a = run(analyst.Analyst(conn, bus, src).analyze(msg))
    assert a["passed"], a
    e = run(executor.Executor(conn, bus, src).handle(bus.queues["executor"].get_nowait()))
    assert e["status"] == "bought" and e["sizeUsd"] == 16.0
    act = conn.execute("SELECT kind,title,value FROM activity").fetchall()
    assert [tuple(r) for r in act] == [("bought", "Bought PIKA COIN", "$16.00 · 8% of stack")]
    assert pushes[0][1][0][0]["url"] == links.coin_url(PIKA, "solana")

@pytest.mark.skipif(os.environ.get("REPLAY_LIVE") != "1", reason="live replay runs from the acceptance runner")
def test_live_replay():
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "tools/replay.py"], cwd=root, capture_output=True, text=True, timeout=900)
    assert r.returncode == 0, r.stderr[-2000:]
    assert "recorded window:" in r.stdout and "Activity:" in r.stdout
