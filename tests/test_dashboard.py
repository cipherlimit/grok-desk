"""Home hero = desk activity (2026-11-18): counts, best candidate, latest signal, per-hour scans, one line per agent."""
import time
import db, api, links

def _db(tmp_path):
    c = db.connect(tmp_path / "d.db"); c.executescript(db.SCHEMA); db.seed_settings(c); return c

def test_empty_desk_has_no_fake_numbers(tmp_path):
    c = _db(tmp_path); st = api.state_doc(c, db.settings_all(c))
    assert st["today"]["seen"] == 0 and st["today"]["signals"] == 0
    assert st["best"] is None and st["signal"] is None
    assert len(st["hourly"]) == 24 and all(h[1] == 0 for h in st["hourly"])
    assert [m["agent"] for m in st["messages"]] == ["grokbot", "risk", "executor", "analyst", "auditor", "scanner"]

def test_closest_call_and_bnb_chain(tmp_path):
    c = _db(tmp_path); now = db.now_iso(); bnb = "0x" + "cd" * 20
    for m, liq in (("So1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaapump", 9000), (bnb, 21800)):
        c.execute("INSERT INTO scans(mint,ts,liquidity_usd,verdict,reason) VALUES(?,?,?,?,?)", (m, now, liq, "kill", "liq low"))
    c.commit()
    st = api.state_doc(c, db.settings_all(c))
    assert st["today"] == {**st["today"], "seen": 2, "killed": 2, "passed": 0}
    assert st["today"]["by_chain"] == {"solana": 1, "bnb": 1}
    b = st["best"]; assert b["stage"] == "Closest call" and b["mint"] == bnb and b["chain"] == "bnb"
    assert b["coin_url"] == links.coin_url(bnb) and "r=savipww" in b["coin_url"] and "chainId=56" in b["coin_url"]
    assert sum(h[1] for h in st["hourly"]) == 2
    assert st["agents"]["scanner"]["feed"][0]["chain"] in ("bnb", "solana")

def test_signal_beats_scanner_pass(tmp_path):
    c = _db(tmp_path); now = db.now_iso(); m1, m2 = "A" * 40 + "pump", "B" * 40 + "pump"
    c.execute("INSERT INTO scans(mint,ts,liquidity_usd,verdict) VALUES(?,?,?,?)", (m1, now, 50000, "pass"))
    c.execute("INSERT INTO signals(mint,ts,size_usd,stack_pct,link,mode,status) VALUES(?,?,?,?,?,?,?)", (m2, now, 16, 8, links.coin_url(m2), "PAPER", "sent"))
    c.commit()
    st = api.state_doc(c, db.settings_all(c))
    assert st["best"]["mint"] == m2 and st["best"]["stage"] == "Signal"
    assert st["signal"]["mode"] == "PAPER" and st["signal"]["coin_url"] == links.coin_url(m2)
