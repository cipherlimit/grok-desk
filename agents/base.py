import asyncio, logging
import db
from config import LOG_DIR

def agent_logger(name):
    lg = logging.getLogger(name)
    if not lg.handlers:
        h = logging.FileHandler(LOG_DIR / f"{name}.log")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        lg.addHandler(h); lg.setLevel(logging.INFO)
    return lg

async def idle_loop(name, conn, every=15, detail="stub: wired in a later step"):
    while True:
        db.heartbeat(conn, name, "idle", detail)
        await asyncio.sleep(every)
