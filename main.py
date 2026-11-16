"""One asyncio process: agents + heartbeats + plan B output (state/chart/activity.json every 15s)."""
import asyncio, json, os, signal, time
import db
from bus import bus
from config import DEFAULTS, OUT_DIR, TICK_S, LOG_DIR
from agents import scanner, auditor, analyst, executor, risk
from agents.base import agent_logger

log = agent_logger("main")
CHART_MAX = 480   # 2h of 15s points, keeps the publish payload small

def settings(conn) -> dict:
    s = dict(DEFAULTS)
    for r in conn.execute("SELECT key,value FROM settings"): s[r["key"]] = json.loads(r["value"])
    return s

def atomic_write(path, obj):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":")))
    os.replace(tmp, path)

def balance(conn, s) -> float:
    r = conn.execute("SELECT balance FROM balance WHERE mode=? ORDER BY id DESC LIMIT 1", (s["mode"],)).fetchone()
    if r: return r["balance"]
    b = s["paper_start_stack"] if s["mode"] == "PAPER" else (s["stack"] or 0)
    conn.execute("INSERT INTO balance(ts,mode,balance,reason) VALUES(?,?,?,?)", (db.now_iso(), s["mode"], b, "start"))
    return b

async def writer(conn):
    OUT_DIR.mkdir(exist_ok=True)
    chart_path = OUT_DIR / "chart.json"
    try: chart = json.loads(chart_path.read_text())["points"]
    except Exception: chart = []
    while True:
        s = settings(conn); ts = db.now_iso(); bal = balance(conn, s)
        db.heartbeat(conn, "main", "ok", f"pid {os.getpid()}")
        hb = {r["agent"]: {"ts": r["ts"], "status": r["status"]} for r in conn.execute("SELECT * FROM heartbeats")}
        openp = [dict(r) for r in conn.execute("SELECT mint,name,entry_price,size_usd,peak_price,stop_line,trail_active,opened_at,link FROM positions WHERE status!='closed'")]
        atomic_write(OUT_DIR / "state.json", {
            "ts": ts, "mode": s["mode"], "balance": round(bal, 2), "currency": "USD",
            "fomo_handle": s["fomo_handle"], "analyst_mode": s["analyst_mode"],
            "hosting": s["hosting"], "open_positions": openp, "heartbeats": hb,
            "pills": {"stop_pct": s["risk.stop_pct"], "trail_pct": s["risk.trail_pct"]}})
        chart.append([int(time.time()), round(bal, 2)]); chart = chart[-CHART_MAX:]
        atomic_write(chart_path, {"ts": ts, "points": chart})
        acts = [dict(r) for r in conn.execute("SELECT ts,agent,title,subtitle,value,extra,mint FROM activity ORDER BY id DESC LIMIT 100")]
        atomic_write(OUT_DIR / "activity.json", {"ts": ts, "items": acts})
        await asyncio.sleep(TICK_S)

async def amain():
    LOG_DIR.mkdir(exist_ok=True)
    conn = db.connect()
    s = settings(conn)
    for k in ("analyst_mode", "fomo_handle", "hosting", "mode", "paper_start_stack"):
        if db.get_setting(conn, k) is None: db.set_setting(conn, k, s[k])
    if not conn.execute("SELECT 1 FROM activity LIMIT 1").fetchone():
        db.activity(conn, "main", "Desk online", "PAPER · plan B (GitHub Pages)", f"${s['paper_start_stack']:.0f}")
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
