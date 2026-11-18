"""Feed (2026-11-18): every desk event to Telegram, persisted cursor + outbox, good-liquidity alert, mute."""
import asyncio, db, links, telegram
from agents import feed
from conftest import run, PIKA
from test_agents import _coin, ago
from test_telegram import D

def _hb(conn):
    for a in feed.HB_AGENTS: db.heartbeat(conn, a)

def F(conn, tmp_path):
    _hb(conn); return feed.Feed(conn, tmp_path / "feed_state.json")

def _scan(conn, liq, verdict="kill", reason="liq $9.0K"):
    conn.execute("INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,h1_change_pct,age_min,verdict,reason) VALUES(?,?,?,?,?,?,?,?)",
                 (PIKA, ago(1), liq, 20, 12, 11, verdict, reason))

def texts(f): return [o["t"] for o in f.st["outbox"]]

def test_first_start_no_history_flood_then_every_event(conn, tmp_path):
    _coin(conn); _scan(conn, 9000)
    f = F(conn, tmp_path); f.start("PAPER"); f.collect(True, db.settings_all(conn))
    assert texts(f) == ["🟦 Desk started · PAPER · feed on"]
    f.st["outbox"] = []
    _scan(conn, 9000)
    conn.execute("INSERT INTO audits(mint,ts,layer,name,value_text,pass,source) VALUES(?,?,?,?,?,?,?)", (PIKA, ago(0), 7, "insiders", "graph empty", 0, "rugcheck"))
    conn.execute("INSERT INTO verdicts(mint,ts,score,pass,reason) VALUES(?,?,?,?,?)", (PIKA, ago(0), 55, 0, "chase"))
    db.set_setting(conn, "risk.stop_pct", 8, who="telegram")
    db.activity(conn, "rejected", "auditor", "PIKA COIN", "layer 7", mint=PIKA)
    f.collect(True, db.settings_all(conn))
    t = "\n".join(texts(f))
    assert "Scanner ❌ KILL · PIKA COIN · liq $9.0K · top10 20% · 1h +12% · age 11m · liq $9.0K" in t and PIKA in t
    assert "Auditor L7 insiders FAIL" in t and "Analyst REJECT · PIKA COIN · score 55 · chase" in t
    assert "Setting risk.stop_pct: 6 → 8 (by telegram)" in t and "auditor · rejected · PIKA COIN" in t
    assert "GOOD LIQUIDITY" not in t

def test_cursor_persists_across_restart_no_dup_no_loss(conn, tmp_path):
    f = F(conn, tmp_path); f.start("PAPER"); f.collect(True, db.settings_all(conn)); f.save()
    _coin(conn); _scan(conn, 9000)                     # happens while the desk is down
    g = F(conn, tmp_path); g.start("PAPER"); g.collect(True, db.settings_all(conn))
    t = texts(g)
    assert t.count("🟦 Desk started · PAPER · feed on") == 2      # first one never sent: kept in the outbox
    assert sum("Scanner ❌ KILL" in x for x in t) == 1 and sum("New coin graduated" in x for x in t) == 1
    g.collect(True, db.settings_all(conn))
    assert sum("Scanner ❌ KILL" in x for x in texts(g)) == 1

def test_good_liquidity_alert_uses_scanner_min_and_links(conn, tmp_path):
    _coin(conn); f = F(conn, tmp_path); f.start("PAPER"); f.st["outbox"] = []
    s = db.settings_all(conn)
    assert feed.good_liq_usd(s) == s["scanner.min_liquidity_usd"] == 40_000
    _scan(conn, 45_000, "pass", None)
    f.collect(True, db.settings_all(conn))
    alert = [o for o in f.st["outbox"] if o["a"]]
    assert len(alert) == 1
    a = alert[0]["t"]
    assert a.startswith("🟢 GOOD LIQUIDITY · PIKA COIN") and links.coin_url(PIKA, "solana") in a and "r=savipww" in a
    assert f"https://dexscreener.com/solana/{PIKA}" in a and "buy" not in a.lower() and "your OK" in a
    assert alert[0]["u"] == links.coin_url(PIKA, "solana")
    _scan(conn, 50_000, "pass", None); f.collect(True, db.settings_all(conn))
    assert sum(o["a"] for o in f.st["outbox"]) == 1          # once per coin
    db.set_setting(conn, "feed.good_liq_usd", 60_000)
    assert feed.good_liq_usd(db.settings_all(conn)) == 60_000

def test_positions_signals_risk_changes(conn, tmp_path):
    _coin(conn); f = F(conn, tmp_path); f.start("PAPER"); f.st["outbox"] = []
    conn.execute("INSERT INTO signals(mint,ts,price,size_usd,stack_pct,link,mode,status) VALUES(?,?,1,16,8,'x','PAPER','pending')", (PIKA, ago(0)))
    conn.execute("INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,mode) VALUES(?,?,1,16,1,0.94,'PAPER')", (PIKA, ago(0)))
    f.collect(True, db.settings_all(conn))
    conn.execute("UPDATE signals SET status='bought'")
    conn.execute("UPDATE positions SET stop_price=1.05, trail_active=1")
    f.collect(True, db.settings_all(conn))
    conn.execute("UPDATE positions SET closed_at=?, exit_price=1.1, exit_reason='trail', pnl_usd=1.5, fees_usd=0.2", (ago(0),))
    f.collect(True, db.settings_all(conn))
    t = "\n".join(texts(f))
    for want in ("Executor signal #1 · PAPER · PIKA COIN · $16.00", "Signal #1 · PIKA COIN · pending → bought",
                 "Position #1 opened", "stop 0.94 → 1.05", "trailing stop ON", "Position #1 closed · PIKA COIN · trail · P&L $+1.50"):
        assert want in t, want

def test_heartbeat_down_up_and_notes(conn, tmp_path):
    f = F(conn, tmp_path); f.start("PAPER"); f.st["outbox"] = []
    for a in feed.HB_AGENTS: db.heartbeat(conn, a)
    conn.execute("UPDATE heartbeats SET ts=? WHERE agent='scanner'", (ago(10),))
    feed.note("Scanner websocket closed (ConnectionClosed), reconnecting in 1s")
    f.collect(True, db.settings_all(conn))
    db.heartbeat(conn, "scanner"); f.collect(True, db.settings_all(conn))
    t = "\n".join(texts(f))
    assert "Scanner DOWN" in t and "Scanner back UP" in t and "Scanner websocket closed" in t and "Auditor DOWN" not in t

def test_muted_drops_and_batches_split(conn, tmp_path):
    _coin(conn); f = F(conn, tmp_path); f.start("PAPER"); f.st["outbox"] = []
    _scan(conn, 9000); f.collect(False, db.settings_all(conn))
    assert f.st["outbox"] == []
    for i in range(200): f.push(f"line {i} " + "x" * 50)
    seen, n_msgs = 0, 0
    while f.st["outbox"]:
        text, url, n = f.next_message()
        assert len(text) <= 4096 and url is None
        del f.st["outbox"][:n]; seen += n; n_msgs += 1
    assert seen == 200 and n_msgs >= 3

def test_run_sends_and_keeps_on_failure(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(feed, "STATE", tmp_path / "s.json"); _hb(conn)
    sent, ok = [], [False]
    async def fake_send(text, buttons=None, retries=3):
        sent.append(text); return ok[0]
    monkeypatch.setattr(telegram, "send", fake_send)
    calls = [0]
    async def fake_sleep(s):
        calls[0] += 1
        if calls[0] == 2: ok[0] = True
        if calls[0] > 6: raise asyncio.CancelledError
        await asyncio.sleep(0)
    try: run(feed.run(conn, sleep=fake_sleep))
    except asyncio.CancelledError: pass
    assert sent[0] == sent[1] == "🟦 Desk started · PAPER · feed on"     # failed once, then delivered, not dropped
    assert feed.Feed(conn, tmp_path / "s.json").st["outbox"] == []

def test_feed_commands(conn):
    d = D(conn)
    r = run(d.handle("feed"))[0]
    assert r.startswith("Feed on") and "$40,000" in r and "feed off" in r
    assert run(d.handle("feed off"))[0].startswith("Feed off") and db.settings_all(conn)["feed.enabled"] is False
    assert run(d.handle("/feed on"))[0].startswith("Feed on") and db.settings_all(conn)["feed.enabled"] is True
    assert d.cmd_set("good liq", "30000")[0].startswith("good liq")
    assert "$30,000 (your setting)" in run(d.handle("feed"))[0]
    assert "feed on" in run(d.handle("help"))[0]
