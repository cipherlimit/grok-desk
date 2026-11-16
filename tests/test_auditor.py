"""STEP 13 test 5: Auditor on a mint with freeze authority set: red at layer 2, layers 3-8 "not checked"
(in the audit doc the Auditor screen reads). Plus the Telegram "buy X" ad-hoc audit (no bus, no Activity)."""
import copy, db, api
from conftest import raw, run, PIKA
from bus import Bus
from agents import auditor
from test_agents import _audit_src, _scan_msg, _coin

def _freeze_src():
    src = _audit_src(raw("insiders_probe/graph_WIGGLES_2kWx47CUrcXtzptFNnbSXKq3vo1ebqyLBhRytKRGpump.json"))
    acc = copy.deepcopy(raw("rpc_getaccountinfo.json"))
    acc["result"]["value"]["data"]["parsed"]["info"]["freezeAuthority"] = "FreezeAuth1111111111111111111111111111111111"
    src.r["getAccountInfo"] = acc
    return src

def test_freeze_authority_red_at_layer_2(conn):
    _coin(conn); bus = Bus()
    out = run(auditor.Auditor(conn, bus, _freeze_src()).audit(_scan_msg()))
    assert not out["passed"] and out["layersChecked"] == 2 and out["reason"] == "freeze not revoked"
    assert [(l["n"], l["pass"], l["value"]) for l in out["layers"]] == [(1, True, "Revoked"), (2, False, "freeze not revoked")]
    d = api.audit_doc(conn, PIKA, db.settings_all(conn))
    assert [l["status"] for l in d["layers"]] == ["pass", "red"] + ["not checked"] * 6
    assert [l["value"] for l in d["layers"][2:]] == ["not checked"] * 6
    assert d["verdict"]["title"] == "Rejected · freeze not revoked" and d["verdict"]["subtitle"] == "Grok Bot won't buy PIKA COIN"
    assert bus.queues["analyst"].empty()
    assert conn.execute("SELECT subtitle FROM activity").fetchone()[0] == "Auditor · freeze not revoked"

def test_adhoc_audit_no_bus_no_activity(conn):
    _coin(conn); bus = Bus()
    out = run(auditor.Auditor(conn, bus, _freeze_src()).audit(_scan_msg(), adhoc=True))
    assert out["reason"] == "freeze not revoked"
    assert bus.queues["analyst"].empty() and conn.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 0
