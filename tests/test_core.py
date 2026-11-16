import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import links, fees, db
from config import DEFAULTS as S
from agents import analyst, risk, executor

def test_links_carry_referral():
    u = links.coin_url("Fz4HmKzMZ9C3LGENsiEd6sJ9r23uQHcEmzhEF8wnpump", "solana")
    assert u == "https://fomo.family/tokens/solana/Fz4HmKzMZ9C3LGENsiEd6sJ9r23uQHcEmzhEF8wnpump?r=savipww"
    assert links.profile_url("CipherLimit").endswith("CipherLimit?source=share_link&r=savipww")

def test_fees():
    assert abs(fees.FEE - 0.0045) < 1e-12
    pnl, f = fees.paper_pnl(100, 1.0, 1.0)
    assert pnl < 0 and abs(f - 0.897975) < 1e-6

def test_analyst_rules_gate():
    dex = {"vol_h1": 30000, "buys_h1": 200, "chg_m5": -6, "buys_m5": 10, "sells_m5": 5, "chg_h1": 8}
    a = analyst.rules_answers(dex, 0, S)
    assert a == {"alive_6h": 1.0, "organic": 1.0, "flush": 1.0, "chase": 0.1}
    score, ok, failed = analyst.gate(a, S)
    assert ok and score == 98
    a2 = analyst.rules_answers(dex, 3, S)
    assert a2["organic"] == 0.5 and not analyst.gate(a2, S)[1]

def test_risk_order():
    pos = {"entry_price": 1.0, "peak_price": 1.0, "stop_pct": 6, "trail_active": False}
    assert risk.evaluate(pos, 0.94, 0, S)[1] == "stop"
    p, r = risk.evaluate(pos, 1.25, 0, S); assert p["trail_active"] and r is None
    assert risk.evaluate(p, 1.09, 0, S)[1] == "trail"
    assert risk.evaluate(pos, 1.02, 3, S)[1] == "timeout"
    assert risk.tighter_stop(6, 8) == 6 and risk.tighter_stop(6, 4) == 4

def test_executor_caps():
    assert executor.size(200, 0, 100000, S) == (16.0, "size_pct")
    assert executor.size(200, 0, 500, S) == (10.0, "liquidity")

def test_db_wal(tmp_path):
    c = db.connect(tmp_path / "t.db")
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    for t in ("positions", "prices", "activity", "heartbeats", "settings", "tickets", "analyses", "audits", "scans", "coins", "balance", "pushes"):
        c.execute(f"SELECT * FROM {t} LIMIT 0")
