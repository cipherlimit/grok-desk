import asyncio, logging, logging.handlers
import db
from config import LOG_DIR

def agent_logger(name):
    """1 file per agent in logs/, rotated daily at midnight (14 days kept)."""
    lg = logging.getLogger(name)
    if not lg.handlers:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        h = logging.handlers.TimedRotatingFileHandler(LOG_DIR / f"{name}.log", when="midnight", backupCount=14,
                                                     encoding="utf-8", delay=True)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        lg.addHandler(h); lg.setLevel(logging.INFO); lg.propagate = False
    return lg

def paused(conn, name) -> bool:
    return bool(db.settings_all(conn).get(f"paused.{name}"))

async def wait_unpaused(conn, name):
    """Paused agents hold their queue; they heartbeat elsewhere so health sees them alive."""
    while paused(conn, name):
        await asyncio.sleep(2)

async def idle_loop(name, conn, every=15):
    while True:
        db.heartbeat(conn, name)
        await asyncio.sleep(every)
