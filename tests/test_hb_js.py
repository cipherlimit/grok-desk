"""web/hb.js: agent heartbeats are judged against state.json's own ts, not the phone clock (Pages lag 2-5 min)."""
import json, shutil, subprocess, pathlib, pytest
JS = pathlib.Path(__file__).resolve().parents[1] / "web" / "hb.js"

@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_lagged_state_still_counts_fresh_heartbeats():
    S = {"ts": "2026-11-18T14:39:07Z", "agents": {a: {"paused": False} for a in ("scanner", "auditor", "analyst", "executor", "risk")},
         "heartbeats": {"scanner": "2026-11-18T14:39:06Z", "auditor": "2026-11-18T14:39:00Z", "analyst": "2026-11-18T14:38:50Z",
                        "executor": "2026-11-18T14:39:05Z", "risk": "2026-11-18T14:36:30Z", "main": "2026-11-18T14:39:07Z"}}
    S2 = json.loads(json.dumps(S)); S2["agents"]["auditor"]["paused"] = True
    code = f"""const H=require({json.dumps(str(JS))}); const S={json.dumps(S)}, S2={json.dumps(S2)};
    const agents=["scanner","auditor","analyst","executor","risk"];
    // the phone clock is ~10 min ahead of S.ts: must not matter
    const real=Date.now; Date.now=()=>Date.parse(S.ts)+600000;
    console.log(JSON.stringify([H.live(S,agents), H.fresh(S,S.heartbeats.risk), H.live(S2,agents), H.age(S,"bad"), H.age(S,S.heartbeats.scanner)]));"""
    out = json.loads(subprocess.run(["node", "-e", code], capture_output=True, text=True, check=True).stdout)
    assert out == [4, False, 3, None, 1]       # risk 157s behind S.ts = down; auditor paused; bad ts = unknown
