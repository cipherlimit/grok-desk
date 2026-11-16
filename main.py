"""One asyncio process: agents + heartbeats + plan B output via api.write_all every 15s.
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

def current_balance(conn, s) -> float:
    b = db.last_balance(conn)
    if b is None:
        b = s["desk.paper_start_stack"] if s["desk.mode"] == "PAPER" else (s["desk.stack"] or 0)
        db.add_balance(conn, b)
    return b

async def night_start_check(conn, s, night_seen):
    """STEP 10 ticket check at night start: Telegram only when it warns (every coin would be skipped)."""
    start, _ = db.night_bounds()
    if night_seen.get("n") == start: return
    night_seen["n"] = start
    level, msg = executor.ticket_check(s)
    log.info("ticket check night=%s %s: %s", start, level, msg)
    if level == "warn": await telegram.send(msg)

async def writer(conn):
    """Plan B: api.write_all every [15]s (state, chart, activity pages, coin/audit/agent docs, account, settings)."""
    OUT_DIR.mkdir(exist_ok=True)
    last_bal_row, night_seen = 0.0, {}
    while True:
        try:
            s = settings(conn); bal = current_balance(conn, s)
            if time.time() - last_bal_row >= BALANCE_EVERY_S:
                db.add_balance(conn, bal); last_bal_row = time.time()
            db.heartbeat(conn, "main")
            await night_start_check(conn, s, night_seen)
            api.write_all(conn, OUT_DIR)
        except Exception as e:
            log.exception("writer error: %s", type(e).__name__)
        await asyncio.sleep(TICK_S)

async def amain():
    LOG_DIR.mkdir(exist_ok=True)
    conn = db.connect()
    if not db.schema_matches(conn):
        raise SystemExit("db schema does not match STEP 4 spec; run tools/migrate_step4.py")
    db.seed_settings(conn)
    s = settings(conn)
    src = Sources(lambda: rpc_url_for(db.settings_all(conn)["desk.rpc_url"]),
                  lambda: db.settings_all(conn)["scanner.dex_max_rpm"])
    log.info("desk start pid=%s mode=%s", os.getpid(), s["desk.mode"])
    tasks = [asyncio.create_task(writer(conn), name="writer")]
    for m in (scanner, auditor, analyst, executor, risk):
        tasks.append(asyncio.create_task(m.run(conn, bus, src), name=m.NAME))
    tasks.append(asyncio.create_task(telegram.poll(conn, bus, src), name="telegram"))
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
