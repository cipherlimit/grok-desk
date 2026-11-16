"""One asyncio process: agents + heartbeats + plan B output (state/chart/activity.json every 15s).
db.balance gets a row every minute and feeds the Home chart."""
import asyncio, json, os, signal, time
import db
from bus import bus
from config import DEFAULTS, OUT_DIR, TICK_S, LOG_DIR
from agents import scanner, auditor, analyst, executor, risk
from agents.base import agent_logger

log = agent_logger("main")
BALANCE_EVERY_S = 60
CHART_MAX = 480   # 8h of 1-minute points

def settings(conn) -> dict:
    s = dict(DEFAULTS)
    for r in conn.execute("SELECT key,value FROM settings"): s[r["key"]] = json.loads(r["value"])
    return s

def atomic_write(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    os.replace(tmp, path)

def current_balance(conn, s) -> float:
    b = db.last_balance(conn)
    if b is None:
        b = s["paper_start_stack"] if s["mode"] == "PAPER" else (s["stack"] or 0)
        db.add_balance(conn, b)
    return b

async def writer(conn):
    OUT_DIR.mkdir(exist_ok=True)
    last_bal_row = 0.0
    while True:
        s = settings(conn); ts = db.now_iso(); bal = current_balance(conn, s)
        if time.time() - last_bal_row >= BALANCE_EVERY_S:
            db.add_balance(conn, bal); last_bal_row = time.time()
        db.heartbeat(conn, "main")
        hb = {r["agent"]: r["ts"] for r in conn.execute("SELECT agent,ts FROM heartbeats")}
        openp = [dict(r) for r in conn.execute(
            "SELECT id,mint,opened_at,entry_price,size_usd,peak_price,stop_price,trail_active,exit_reason,mode "
            "FROM positions WHERE closed_at IS NULL")]
        atomic_write(OUT_DIR / "state.json", {
            "ts": ts, "mode": s["mode"], "balance": round(bal, 2), "currency": "USD",
            "fomo_handle": s["fomo_handle"], "analyst_mode": s["analyst_mode"],
            "hosting": s["hosting"], "open_positions": openp, "heartbeats": hb,
            "pills": {"stop_pct": s["risk.stop_pct"], "trail_pct": s["risk.trail_pct"]}})
        pts = [[r["ts"], r["value"]] for r in conn.execute(
            "SELECT ts,value FROM (SELECT ts,value,rowid FROM balance ORDER BY ts DESC, rowid DESC LIMIT ?) ORDER BY ts", (CHART_MAX,))]
        atomic_write(OUT_DIR / "chart.json", {"ts": ts, "points": pts})
        acts = [dict(r) for r in conn.execute(
            "SELECT ts,kind,mint,agent,title,subtitle,value FROM activity ORDER BY id DESC LIMIT 100")]
        atomic_write(OUT_DIR / "activity.json", {"ts": ts, "items": acts})
        await asyncio.sleep(TICK_S)

async def amain():
    LOG_DIR.mkdir(exist_ok=True)
    conn = db.connect()
    if not db.schema_matches(conn):
        raise SystemExit("db schema does not match STEP 4 spec; run tools/migrate_step4.py")
    s = settings(conn)
    for k in ("analyst_mode", "fomo_handle", "hosting", "mode", "paper_start_stack"):
        if db.get_setting(conn, k) is None: db.set_setting(conn, k, s[k])
    log.info("desk start pid=%s mode=%s", os.getpid(), s["mode"])
    tasks = [asyncio.create_task(writer(conn), name="writer")]
    for m in (scanner, auditor, analyst, executor, risk):
        tasks.append(asyncio.create_task(m.run(conn, bus, s), name=m.NAME))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT): loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    for t in tasks: t.cancel()
    log.info("desk stop")

if __name__ == "__main__":
    asyncio.run(amain())
