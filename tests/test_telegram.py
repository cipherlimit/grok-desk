"""STEP 9 commands: owner only, plain or /command, every coin mention carries coin_url."""
import asyncio, json, db, links, telegram
from conftest import run, PIKA, FakeSrc, raw
from bus import Bus
from test_agents import _coin, pika_pair, ago

def D(conn, src=None): return telegram.Desk(conn, Bus(), src)

def test_unknown_text_shows_list(conn):
    r, _ = run(D(conn).handle("hello there"))
    assert r.startswith("Commands:") and "buy X" in r and "set stop to -8%" in r

def test_slash_and_plain_same(conn):
    assert run(D(conn).handle("/invite")) == run(D(conn).handle("invite"))
    assert run(D(conn).handle("invite"))[0] == "New to FOMO? " + links.SIGNUP_URL

def test_set_reply_and_range_refusal(conn):
    d = D(conn)
    assert d.cmd_set("stop", "-8%")[0].startswith("stop -6% -> -8%")
    r = d.cmd_set("stop", "-95%")[0]
    assert r.startswith("Refused:") and "0.5" in r and "90" in r
    assert conn.execute("SELECT key,old,new,who FROM change_log WHERE key='risk.stop_pct' AND who='telegram'").fetchone()[:] == ("risk.stop_pct", "6", "8", "telegram")
    assert d.cmd_set("nonsense", "3")[0].startswith("Unknown setting")

def test_take_apart_off_asks_twice(conn):
    d = D(conn)
    assert d.cmd_set("take apart", "off")[0].startswith("Turn off")
    assert db.settings_all(conn)["auditor.enabled"] is True
    assert d.cmd_set("take apart", "off")[0] == "take apart on -> off"
    assert db.settings_all(conn)["auditor.enabled"] is False

def test_pause_resume_and_risk_lock(conn):
    d = D(conn)
    assert d.cmd_pause("pause", "scanner")[0] == "Scanner paused." and db.settings_all(conn)["paused.scanner"] is True
    assert d.cmd_pause("resume", "scanner")[0] == "Scanner running."
    _coin(conn)
    conn.execute("INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,mode) VALUES(?,?,1,16,1,0.94,'PAPER')", (PIKA, ago(1)))
    assert d.cmd_pause("pause", "risk")[0] == "Risk can't be paused while a position is open."

def test_live_needs_typed_LIVE_and_full_paper_night(conn):
    d = D(conn)
    assert run(d.handle("live"))[0] == "Type LIVE (capitals) to switch to LIVE."
    r = run(d.handle("LIVE"))[0]
    assert r.startswith("Not switching to LIVE: ")
    assert db.settings_all(conn)["desk.mode"] == "PAPER"
    assert run(d.handle("LIVE"))[0] == "Send 'live' first, then type LIVE."

def test_resolve_name_symbol_mint_and_ambiguous(conn):
    _coin(conn); d = D(conn)
    assert d.resolve("pika coin") == ("ok", PIKA) and d.resolve(PIKA) == ("ok", PIKA)
    assert d.resolve("NOPE")[0] == "unknown" and "Send me its mint" in d.resolve("NOPE")[1]
    other = "So11111111111111111111111111111111111111112"
    conn.execute("INSERT INTO coins(mint,name,symbol) VALUES(?,?,?)", (other, "PIKA COIN", "PIKA2"))
    k, text, _ = d.resolve("PIKA COIN")
    assert k == "ask" and links.coin_url(PIKA, "solana") in text and links.coin_url(other, "solana") in text

def test_why_sell_status_carry_coin_url(conn):
    _coin(conn); d = D(conn)
    conn.execute("INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,age_min,verdict,reason) VALUES(?,?,?,?,?,?,?)", (PIKA, ago(1), 9000, 20, 10, "kill", "liq $9.0K"))
    r = run(d.handle("why PIKA COIN"))[0]
    assert "Scanner · liq $9.0K" in r and links.coin_url(PIKA, "solana") in r
    r, b = run(d.handle("sell PIKA COIN")); assert r.startswith("No open PIKA COIN position") and links.coin_url(PIKA, "solana") in r
    conn.execute("INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,mode) VALUES(?,?,1,16,1,0.94,'PAPER')", (PIKA, ago(1)))
    r, b = run(d.handle("sell PIKA COIN"))
    assert b[0][0] == {"text": "Sell on FOMO", "url": links.coin_url(PIKA, "solana")} and b[1][0]["callback_data"].startswith("sold:")
    db.add_balance(conn, 200)
    r, b = run(d.handle("status"))
    assert r.startswith("PAPER · balance $200.00") and b[0][0]["url"] == links.coin_url(PIKA, "solana")

def test_buy_pushed_tonight_resends(conn):
    _coin(conn); d = D(conn)
    conn.execute("INSERT INTO signals(mint,ts,price,size_usd,stack_pct,link,mode,status) VALUES(?,?,?,?,?,?,?,?)",
                 (PIKA, db.now_iso(), 1e-6, 16, 8, links.coin_url(PIKA, "solana"), "PAPER", "bought"))
    r, b = run(d.handle("buy PIKA COIN"))
    assert r.startswith("PAPER · Buy PIKA COIN · $16.00 · 8% of stack") and b[0][0] == {"text": "Buy on FOMO", "url": links.coin_url(PIKA, "solana")}

def test_owner_lock(conn, monkeypatch, tmp_path):
    sent = []
    async def fake(text, buttons=None, retries=3): sent.append(text); return True
    monkeypatch.setattr(telegram, "send", fake)
    monkeypatch.setattr(telegram, "owner_chat_id", lambda: 111)
    class Cl:
        async def post(self, *a, **k): return None
    d = D(conn)
    run(telegram._route(Cl(), d, Bus(), {"message": {"chat": {"id": 999}, "text": "status"}}))
    assert sent == []                                                   # stranger ignored
    run(telegram._route(Cl(), d, Bus(), {"message": {"chat": {"id": 111}, "text": "/invite"}}))
    assert sent == ["New to FOMO? " + links.SIGNUP_URL]
    b = Bus()
    run(telegram._route(Cl(), d, b, {"callback_query": {"id": "1", "data": "skip:" + PIKA, "message": {"chat": {"id": 999}}}}))
    assert b.queues["cb.executor"].empty()
    run(telegram._route(Cl(), d, b, {"callback_query": {"id": "2", "data": "skip:" + PIKA, "message": {"chat": {"id": 111}}}}))
    assert b.queues["cb.executor"].get_nowait() == {"action": "skip", "mint": PIKA}
