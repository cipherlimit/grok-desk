"""desk-health routine script (STEP 12). One line out.
Process down or any heartbeat (5 agents + main) older than 2 min -> ./run.sh and Telegram
"Restarted {agent}, down {n} min". More than 10 websocket reconnects in the last hour -> Telegram."""
import asyncio, datetime as dt, re, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import db, telegram
from config import LOG_DIR

AGENTS = ["scanner", "auditor", "analyst", "executor", "risk", "main"]
STALE_S, WS_MAX = 120, 10

def procs() -> dict:
    out = {}
    for name in ("main.py", "publisher.py"):
        r = subprocess.run(["pgrep", "-f", rf"\.venv/bin/python {name}"], capture_output=True, text=True)
        out[name] = [int(p) for p in r.stdout.split()]
    return out

def ws_reconnects(hours=1.0) -> int:
    cutoff = dt.datetime.now() - dt.timedelta(hours=hours); n = 0
    for f in sorted(LOG_DIR.glob("scanner.log*")):
        try: lines = f.read_text(errors="replace").splitlines()
        except OSError: continue
        for line in lines:
            if "reconnect in" not in line: continue
            try: t = dt.datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError: continue
            if t >= cutoff: n += 1
    return n

def main():
    dry = "--dry-run" in sys.argv          # check only: no restart, no Telegram
    c = db.connect(); now = time.time()
    hb = {r["agent"]: db.parse_iso(r["ts"]) for r in c.execute("SELECT agent,ts FROM heartbeats")}
    stale = {a: (now - hb[a]) / 60 if a in hb else None for a in AGENTS if a not in hb or now - hb[a] > STALE_S}
    p = procs(); down_proc = [k for k, v in p.items() if not v]
    msgs, parts = [], []
    if (stale or down_proc) and dry:
        parts.append(f"would restart: stale {sorted(stale)} down {down_proc}")
    elif stale or down_proc:
        subprocess.run(["./run.sh"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        oldest = max([m for m in stale.values() if m is not None] or [0])
        who = ", ".join(sorted(stale)) if stale else ", ".join(down_proc)
        if down_proc and not stale: who = " + ".join(down_proc)
        msg = f"Restarted {who}, down {max(1, round(oldest)) if oldest else 0} min"
        msgs.append(msg); parts.append(msg)
        time.sleep(25)
        hb2 = {r["agent"]: db.parse_iso(r["ts"]) for r in c.execute("SELECT agent,ts FROM heartbeats")}
        fresh = sum(1 for a in AGENTS if a in hb2 and time.time() - hb2[a] <= STALE_S)
        parts.append(f"after restart {fresh}/{len(AGENTS)} heartbeats fresh, pids {procs()}")
    else:
        parts.append(f"ok: main.py {p['main.py']} publisher.py {p['publisher.py']} up, {len(AGENTS)}/{len(AGENTS)} heartbeats fresh "
                     f"(oldest {int(now - min(hb[a] for a in AGENTS))}s)")
    n = ws_reconnects()
    parts.append(f"ws reconnects 1h: {n}")
    if n > WS_MAX:
        m = f"Scanner websocket reconnected {n} times in the last hour"
        msgs.append(m); parts.append("would tell you" if dry else "told you")
    if not dry:
        for m in msgs: asyncio.run(telegram.send(m))
    print(" · ".join(parts))

if __name__ == "__main__":
    main()
