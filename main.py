"""One asyncio process: agents + heartbeats + plan B output (state/chart/activity.json every 15s).
db.balance gets a row every minute and feeds the Home chart."""
import asyncio, json, os, signal, time
import db
from bus import bus
from config import DEFAULTS, OUT_DIR, TICK_S, LOG_DIR
from agents import scanner, auditor, analyst, executor, risk
from agents.base import agent_logger
from sources import Sources
from config import rpc_url_for
import api, telegram

log = agent_logger("main")
BALANCE_EVERY_S = 60
CHART_MAX = 480   # 8h of 1-minute points

def settings(conn) -> dict:
    return db.settings_all(conn)

def cards(conn) -> dict:
    start, _ = db.night_bounds()
    q = lambda sql: conn.execute(sql, (start,)).fetchone()[0]
    return {"pools": q("SELECT COUNT(*) FROM coins WHERE migrated_at>=?"),
            "scans": q("SELECT COUNT(*) FROM scans WHERE ts>=?"),
            "passed_scanner": q("SELECT COUNT(*) FROM scans WHERE verdict='pass' AND ts>=?"),
            "taken_apart": q("SELECT COUNT(DISTINCT mint) FROM audits WHERE ts>=?"),
            "rejected": q("SELECT COUNT(*) FROM activity WHERE kind='rejected' AND ts>=?"),
            "trades": q("SELECT COUNT(*) FROM positions WHERE opened_at>=?")}

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
            "pills": {"stop_pct": s["risk.stop_pct"], "trail_pct": s["risk.trail_pct"]},
            "cards": cards(conn)})
        pts = [[r["ts"], r["value"]] for r in conn.execute(
            "SELECT ts,value FROM (SELECT ts,value,rowid FROM balance ORDER BY ts DESC, rowid DESC LIMIT ?) ORDER BY ts", (CHART_MAX,))]
        atomic_write(OUT_DIR / "chart.json", {"ts": ts, "points": pts})
        acts = [dict(r) for r in conn.execute(
            "SELECT ts,kind,mint,agent,title,subtitle,value FROM activity ORDER BY id DESC LIMIT 100")]
        scans = [dict(r) for r in conn.execute(
            "SELECT s.ts,s.mint,c.name,s.liquidity_usd,s.top10_pct,s.h1_change_pct,s.age_min,s.verdict,s.reason "
            "FROM scans s LEFT JOIN coins c USING(mint) ORDER BY s.id DESC LIMIT 50")]
        atomic_write(OUT_DIR / "activity.json", {"ts": ts, "items": acts, "scans": scans})
        await asyncio.sleep(TICK_S)

async def amain():
    LOG_DIR.mkdir(exist_ok=True)
    conn = db.connect()
    if not db.schema_matches(conn):
        raise SystemExit("db schema does not match STEP 4 spec; run tools/migrate_step4.py")
    db.seed_settings(conn)
    s = settings(conn)
    src = Sources(lambda: rpc_url_for(db.settings_all(conn)["rpc_url"]),
                  lambda: db.settings_all(conn)["scanner.dex_max_rpm"])
    log.info("desk start pid=%s mode=%s", os.getpid(), s["mode"])
    tasks = [asyncio.create_task(writer(conn), name="writer")]
    for m in (scanner, auditor, analyst, executor, risk):
        tasks.append(asyncio.create_task(m.run(conn, bus, src), name=m.NAME))
    tasks.append(asyncio.create_task(telegram.poll_callbacks(bus), name="tg_callbacks"))
    tasks.append(asyncio.create_task(api.price_loop(conn, src), name="api_prices"))
    def _died(t):
        if not t.cancelled() and t.exception():
            log.error("task %s died: %r", t.get_name(), t.exception())
    for t in tasks: t.add_done_callback(_died)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT): loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    for t in tasks: t.cancel()
    log.info("desk stop")

if __name__ == "__main__":
    asyncio.run(amain())
