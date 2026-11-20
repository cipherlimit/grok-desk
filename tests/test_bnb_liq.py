"""BNB-only Scanner liquidity bar (user 2026-11-20): $100K on BNB, Solana stays on the global bar.
Locked-pool is already enforced in bnb.l6_liquidity; this file only covers the liquidity bar."""
import db
from agents import scanner, feed
from conftest import PIKA
from test_bnb import BEA
from test_feed import F, texts
from test_agents import _coin, ago


def _s(conn, global_bar=10_000, bnb_bar=100_000):
    db.set_setting(conn, "scanner.min_liquidity_usd", global_bar, who="test")
    db.set_setting(conn, "scanner.bnb_min_liquidity_usd", bnb_bar, who="test")
    return db.settings_all(conn)


def test_bnb_min_liquidity_setting_default_and_zero_follows_global(conn):
    s = db.settings_all(conn)
    assert s["scanner.bnb_min_liquidity_usd"] == 100_000
    assert scanner.min_liq_usd(s, "bnb") == 100_000
    assert scanner.min_liq_usd(s, "solana") == s["scanner.min_liquidity_usd"] == 40_000
    assert scanner.min_liq_usd(s) == 40_000
    s = _s(conn, global_bar=10_000, bnb_bar=0)
    assert scanner.min_liq_usd(s, "bnb") == 10_000
    assert scanner.min_liq_usd(s, "solana") == 10_000


def test_bnb_bar_kills_50k_passes_120k_solana_15k_passes(conn):
    """bnb 50K killed, bnb 120K passes the liq rule, solana 15K passes."""
    s = _s(conn, global_bar=10_000, bnb_bar=100_000)
    assert scanner.decide(s, 50_000, 20, 10, None, 30, "bnb")[:2] == ("kill", "liq $50.0K")
    assert scanner.decide(s, 120_000, 20, 10, None, 30, "bnb")[:2] == ("pass", None)
    assert scanner.decide(s, 15_000, 20, 10, None, 30, "solana")[:2] == ("pass", None)
    # omitted chain stays on the global $10K bar (same as Solana)
    assert scanner.decide(s, 15_000, 20, 10, None, 30)[:2] == ("pass", None)
    # a BNB coin under the global bar is also killed
    assert scanner.decide(s, 5_000, 20, 10, None, 30, "bnb")[:2] == ("kill", "liq $5.0K")


def test_feed_does_not_announce_bnb_under_bnb_bar(conn, tmp_path):
    """A BNB coin under the $100K bar never gets a clean alert, even if feed.good_liq_usd is $10K.
    A $120K BNB coin alerts only after Auditor + Analyst also pass, and the threshold shown is $100K."""
    db.set_setting(conn, "scanner.min_liquidity_usd", 10_000, who="test")
    db.set_setting(conn, "scanner.bnb_min_liquidity_usd", 100_000, who="test")
    db.set_setting(conn, "feed.good_liq_usd", 10_000, who="test")
    s = db.settings_all(conn)
    assert feed.good_liq_usd(s) == 10_000
    assert feed.good_liq_usd(s, PIKA) == 10_000
    assert feed.good_liq_usd(s, BEA) == 100_000

    conn.execute(
        "INSERT INTO coins(mint,name,symbol,migrated_at,chain) VALUES(?,?,?,?,?)",
        (BEA, "BEA", "BEA", ago(20), "bnb"),
    )
    f = F(conn, tmp_path); f.start("PAPER"); f.st["outbox"] = []
    conn.execute(
        "INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,h1_change_pct,age_min,verdict,reason,chain) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (BEA, ago(1), 50_000, 20, 12, 15, "kill", "liq $50.0K", "bnb"),
    )
    f.collect(True, db.settings_all(conn))
    t = "\n".join(texts(f))
    assert "CLEAN" not in t and "GOOD LIQUIDITY" not in t
    assert "Scanner ❌ KILL" in t

    conn.execute(
        "INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,h1_change_pct,age_min,verdict,reason,chain) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (BEA, ago(0), 120_000, 20, 12, 15, "pass", None, "bnb"),
    )
    f.collect(True, db.settings_all(conn))
    assert not [o for o in f.st["outbox"] if o["a"]]   # scanner pass alone is not a clean alert
    for n in range(1, 9):
        conn.execute("INSERT INTO audits(mint,ts,layer,name,value_text,pass,source,chain) VALUES(?,?,?,?,?,?,?,?)",
                     (BEA, ago(0), n, "L", "ok", 1, "goplus", "bnb"))
    conn.execute("INSERT INTO verdicts(mint,ts,score,pass,reason) VALUES(?,?,?,?,?)", (BEA, ago(0), 80, 1, None))
    f.collect(True, db.settings_all(conn))
    alerts = [o for o in f.st["outbox"] if o["a"]]
    assert len(alerts) == 1
    assert "CLEAN · BEA · BNB Chain" in alerts[0]["t"]
    assert "liq $120.0K (threshold $100.0K)" in alerts[0]["t"]
