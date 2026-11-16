"""api.py. Plan B has no live HTTP server (FastAPI app kept for plan A).
Price writer (STEP 4 note): every [60]s, 1 DexScreener batch for coins pushed or audited tonight
that neither Scanner (waiting room) nor Risk (open positions) covers -> db.write_price_batch("api")."""
import asyncio, json, logging
from fastapi import FastAPI, Header, HTTPException
import db
from config import OUT_DIR, LOG_DIR, secret
from sources import DEX_BATCH, SourceError

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
log = logging.getLogger("api")
if not log.handlers:
    h = logging.FileHandler(LOG_DIR / "api.log"); h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h); log.setLevel(logging.INFO)

@app.get("/api/{name}.json")
def data(name: str, x_app_token: str | None = Header(None)):
    if x_app_token != secret("APP_TOKEN"): raise HTTPException(404)
    if name not in {"state", "chart", "activity"}: raise HTTPException(404)
    return json.loads((OUT_DIR / f"{name}.json").read_text())

def uncovered_mints(conn) -> list[str]:
    s = db.settings_all(conn); start, _ = db.night_bounds()
    tonight = {r[0] for r in conn.execute("SELECT mint FROM signals WHERE ts>=? UNION SELECT mint FROM audits WHERE ts>=?", (start, start))}
    room = {r["mint"] for r in db.waiting_room(conn, s["scanner.room_max_min"])}
    open_ = {r["mint"] for r in db.open_positions(conn)}
    return sorted(tonight - room - open_)

async def price_loop(conn, src):
    while True:
        mints = uncovered_mints(conn)
        for i in range(0, len(mints), DEX_BATCH):
            try: db.write_price_batch(conn, "api", await src.dex_tokens(mints[i:i + DEX_BATCH]))
            except SourceError as e: log.warning("price batch failed: %s", e)
        await asyncio.sleep(db.settings_all(conn)["api.prices_every_s"])
