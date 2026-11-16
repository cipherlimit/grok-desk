"""STEP 13 test 2: the Analyst gate. alive 0.69 / organic 0.64 / chase 0.31 / score 69 each reject,
all at threshold pass. Same in rules mode with fake DexScreener numbers: rows read "Analyst (rules)"."""
import pytest, db
from conftest import FakeSrc, run, PIKA
from bus import Bus
from agents import analyst
from test_agents import pika_pair, _coin, _an_msg

AT = {"alive_6h": 0.70, "organic": 0.65, "flush": 0.75, "chase": 0.30}        # every rule exactly at threshold: score 70

def test_thresholds_pass(conn):
    s = db.settings_all(conn)
    score, ok, failed = analyst.gate(AT, s)
    assert ok and failed == [] and score >= s["analyst.min_score"]

@pytest.mark.parametrize("key,val,rule", [("alive_6h", 0.69, "alive_6h 0.69"), ("organic", 0.64, "organic 0.64"), ("chase", 0.31, "chase 0.31")])
def test_each_answer_rejects(conn, key, val, rule):
    score, ok, failed = analyst.gate({**AT, key: val}, db.settings_all(conn))
    assert not ok and rule in failed

def test_score_69_rejects_70_passes(conn):
    s = db.settings_all(conn)
    sc, ok, failed = analyst.gate({**AT, "flush": 0.71}, s); assert sc == 69 and not ok and failed == ["score 69"]
    sc, ok, failed = analyst.gate(AT, s); assert sc == 70 and ok

def _rules_row(conn, **dex):
    _coin(conn); msg = _an_msg(); msg["auditor"]["layers"][2]["value"] = "None"
    out = run(analyst.Analyst(conn, Bus(), FakeSrc(dex=[pika_pair(**dex)])).analyze(msg))
    row = conn.execute("SELECT subtitle FROM activity").fetchone()
    return out, row[0] if row else None

GOOD = dict(volume__h1=30000, txns__h1__buys=200, priceChange__m5=-6, priceChange__h1=8)

def test_rules_mode_pass(conn):
    out, row = _rules_row(conn, **GOOD)
    assert out["model"] == "rules" and out["passed"] and row is None

@pytest.mark.parametrize("over,rule", [({"volume__h1": 17000}, "alive_6h"), ({"txns__h1__buys": 90}, "organic"),
                                       ({"priceChange__h1": 85}, "chase")])
def test_rules_mode_rejects_read_analyst_rules(conn, over, rule):
    out, row = _rules_row(conn, **{**GOOD, **over})
    assert not out["passed"] and out["failed"][0].startswith(rule)
    assert row == f"Analyst (rules) · {out['failed'][0]}"
