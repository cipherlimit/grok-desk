"""Clean-only feed + holders / volume / scam checks (user 2026-11-20)."""
import copy
import db
import bnb
from agents import scanner, auditor, feed
from conftest import raw, PIKA
from test_agents import _coin, ago, _audit_src, _scan_msg
from test_bnb import BEA, gp_clean, HP404
from test_feed import F, texts
from bus import Bus
from conftest import run


def test_volume_bar_uses_h24_else_h1(conn):
    db.set_setting(conn, "scanner.min_liquidity_usd", 10_000, who="test")
    db.set_setting(conn, "scanner.min_volume_h24_usd", 25_000, who="test")
    s = db.settings_all(conn)
    assert s["scanner.min_volume_h24_usd"] == 25_000
    # below the bar: killed (h24)
    assert scanner.decide(s, 50_000, 20, 10, None, 30, vol=5_000)[:2] == ("kill", "vol $5.0K")
    # at/above the bar: passes the volume (and liq) rule
    assert scanner.decide(s, 50_000, 20, 10, None, 30, vol=25_000)[:2] == ("pass", None)
    assert scanner.decide(s, 50_000, 20, 10, None, 30, vol=80_000)[:2] == ("pass", None)
    # volume_usd prefers h24, then h1
    assert scanner.volume_usd({"vol_h24": 12_000, "vol_h1": 80_000}) == 12_000
    assert scanner.volume_usd({"vol_h24": None, "vol_h1": 80_000}) == 80_000
    assert scanner.volume_usd({"vol_h1": 3_000}) == 3_000
    assert scanner.volume_usd({}) is None
    # omitted vol skips the rule (other decide tests stay valid)
    assert scanner.decide(s, 50_000, 20, 10, None, 30)[:2] == ("pass", None)
    # 0 = off
    db.set_setting(conn, "scanner.min_volume_h24_usd", 0, who="test")
    assert scanner.decide(db.settings_all(conn), 50_000, 20, 10, None, 30, vol=1)[:2] == ("pass", None)


def test_holders_unknown_or_few_fail_enough_pass():
    assert auditor.holders_ok(None, 100) == (False, "no data: holders")
    assert auditor.holders_ok(36, 100) == (False, "holders 36")
    assert auditor.holders_ok(100, 100) == (True, "100 holders")
    assert auditor.holders_ok(200, 100) == (True, "200 holders")
    assert auditor.holders_ok(0, 100) == (False, "holders 0")


def test_audit_fails_few_holders_after_8_layers(conn):
    db.set_setting(conn, "auditor.min_holders", 100, who="test")
    _coin(conn); bus = Bus()
    g = raw("insiders_probe/graph_WIGGLES_2kWx47CUrcXtzptFNnbSXKq3vo1ebqyLBhRytKRGpump.json")
    out = run(auditor.Auditor(conn, bus, _audit_src(g)).audit(_scan_msg()))
    assert out["passed"] is False and out["reason"] == "holders 36" and out["holders"] == 36
    assert out["layers"][-1]["name"] == "Holders" and out["layers"][-1]["pass"] is False
    assert bus.queues["analyst"].empty()


def test_rugcheck_danger_fails_and_clean_risks_pass():
    rep = raw("rugcheck_report.json")
    ok, why = auditor.l_rug_risks(rep)
    assert ok is False and why.startswith("rugcheck danger:")
    assert "Creator history of rugged tokens" in why
    clean = copy.deepcopy(rep); clean["risks"] = []
    assert auditor.l_rug_risks(clean) == (True, "no danger risks")
    assert auditor.l_rug_risks({"token": {}}) is None          # no risks list: skip
    assert auditor.l_rug_risks({"risks": [{"level": "warn", "name": "x"}]}) == (True, "no danger risks")


def test_audit_pika_raw_report_fails_on_danger(conn):
    _coin(conn)
    out = run(auditor.Auditor(conn, Bus(), _audit_src([], report=raw("rugcheck_report.json"))).audit(_scan_msg()))
    assert out["passed"] is False and out["layersChecked"] == 5
    assert out["reason"].startswith("rugcheck danger:")


def test_goplus_airdrop_scam_fails_l1():
    g = gp_clean(); assert bnb.l1_honeypot(g, HP404)[0] is True
    g = gp_clean(); g["is_airdrop_scam"] = "1"
    assert bnb.l1_honeypot(g, HP404) == (False, "airdrop scam")
    g = gp_clean(); g.pop("is_airdrop_scam", None)
    assert bnb.l1_honeypot(g, HP404)[0] is True                 # missing flag is ignored


def test_feed_only_clean_hides_unsafe_keeps_system_and_clean_alert(conn, tmp_path):
    _coin(conn)
    f = F(conn, tmp_path, only_clean=True); f.start("PAPER"); f.st["outbox"] = []
    # new coin already in db from _coin, plus a kill and an audit fail: none of these go out
    conn.execute("INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,h1_change_pct,age_min,verdict,reason) VALUES(?,?,?,?,?,?,?,?)",
                 (PIKA, ago(1), 9_000, 20, 12, 11, "kill", "liq $9.0K"))
    conn.execute("INSERT INTO audits(mint,ts,layer,name,value_text,pass,source) VALUES(?,?,?,?,?,?,?)",
                 (PIKA, ago(0), 7, "Bundled wallets", "no data: bundles", 0, "rugcheck"))
    db.set_setting(conn, "risk.stop_pct", 8, who="chief")
    feed.note("Scanner websocket closed (ConnectionClosed), reconnecting in 1s")
    f.collect(True, db.settings_all(conn))
    t = "\n".join(texts(f))
    assert "New coin graduated" not in t and "❌ KILL" not in t
    assert "Auditor L7" not in t and "Rejected" not in t
    assert "Setting risk.stop_pct" in t and "Scanner websocket closed" in t

    # a fully cleared coin is announced once, as CLEAN
    mint2 = "So2aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaapump"
    conn.execute("INSERT INTO coins(mint,name,migrated_at) VALUES(?,?,?)", (mint2, "CLEANCOIN", ago(20)))
    conn.execute("INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,h1_change_pct,age_min,verdict,reason) VALUES(?,?,?,?,?,?,?,?)",
                 (mint2, ago(0), 55_000, 12, 8, 15, "pass", None))
    for n, name in enumerate(["Mint authority", "Freeze authority", "Dev wallet", "Top 10 holders",
                              "Liquidity", "Pool age", "Bundled wallets", "Delegations"], 1):
        conn.execute("INSERT INTO audits(mint,ts,layer,name,value_text,pass,source) VALUES(?,?,?,?,?,?,?)",
                     (mint2, ago(0), n, name, "ok", 1, "rpc"))
    f.collect(True, db.settings_all(conn))
    assert not [o for o in f.st["outbox"] if o["a"]]            # still waiting on analyst
    conn.execute("INSERT INTO verdicts(mint,ts,score,pass,reason) VALUES(?,?,?,?,?)", (mint2, ago(0), 81, 1, None))
    f.collect(True, db.settings_all(conn))
    alerts = [o for o in f.st["outbox"] if o["a"]]
    assert len(alerts) == 1 and alerts[0]["t"].startswith("🟢 CLEAN · CLEANCOIN")
    assert "Scanner pass · Auditor all green · Analyst 81" in alerts[0]["t"]
    assert "❌ KILL" not in "\n".join(texts(f))

    # position / risk updates for an entered coin are still shown
    conn.execute("INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,mode) VALUES(?,?,1,16,1,0.94,'PAPER')",
                 (mint2, ago(0)))
    f.collect(True, db.settings_all(conn))
    assert any("Position #1 opened" in o["t"] for o in f.st["outbox"])


def test_defaults():
    from config import DEFAULTS
    assert DEFAULTS["feed.only_clean"] is True
    assert DEFAULTS["auditor.min_holders"] == 100
    assert DEFAULTS["scanner.min_volume_h24_usd"] == 25_000
