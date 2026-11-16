"""Agent 1 · Scanner. You count and you cut. (PART 4, Agent 1)
One PumpPortal websocket (subscribeMigration only), waiting room in db.coins, DexScreener
batch every [60]s -> db.prices, kill order liq -> top 10 -> h1 -> copycat, pass at pool age >= [10] min."""
import asyncio, json, re, time, unicodedata
import websockets
import db, fields, onchain
from sources import PUMPPORTAL_WS, DEX_BATCH, RateLimited, SourceError, with_retry
from agents.base import agent_logger, paused

NAME = "scanner"
log = agent_logger(NAME)

def norm(s: str | None) -> str:
    """lowercase, strip spaces, digits, emoji (anything that is not a letter)."""
    if not s: return ""
    return "".join(ch for ch in s.lower() if unicodedata.category(ch).startswith("L"))

def copycat_of(name, symbol, mint, runners: dict) -> str | None:
    mine = [x for x in (norm(name), norm(symbol)) if x]
    for rmint, (rname, rsym) in runners.items():
        if rmint == mint: continue
        for theirs in (x for x in (norm(rname), norm(rsym)) if x):
            for m in mine:
                if m == theirs or m in theirs or theirs in m:
                    return rname or rsym
    return None

def fmt_k(usd: float) -> str:
    if usd >= 1_000_000: return f"${usd/1_000_000:.1f}M"
    if usd >= 1_000: return f"${usd/1_000:.1f}K"
    return f"${usd:.0f}"

def decide(s: dict, liq, top10_fn_result, h1, copycat, age_min) -> tuple[str | None, str | None, bool]:
    """Pure kill/pass logic in spec order. top10_fn_result: float, or None = unknown (wait).
    Returns (verdict, reason, needs_top10) ; verdict None = keep waiting.
    User rule 2026-11-16: no kill is judged before pool age >= [10] min (scanner.judge_from_age_min)."""
    if age_min < s["scanner.judge_from_age_min"]: return None, None, False
    if liq < s["scanner.min_liquidity_usd"]: return "kill", f"liq {fmt_k(liq)}", False
    if top10_fn_result is None: return None, None, True
    if top10_fn_result > s["scanner.max_top10_pct"]: return "kill", f"top 10 hold {top10_fn_result:.0f}%", False
    if h1 is not None and h1 > s["scanner.max_h1_change_pct"]: return "kill", f"+{h1:.0f}% in 1h", False
    if copycat: return "kill", f"copycat of {copycat}", False
    if age_min >= s["scanner.min_pool_age_min"]: return "pass", None, False
    return None, None, False

class Scanner:
    def __init__(self, conn, bus, src):
        self.c, self.bus, self.src = conn, bus, src
        self.runners: dict[str, tuple] = {}      # today's runners: mint -> (name, symbol)
        self.runners_day = db.local_today()
        self.supply: dict[str, int] = {}
        self.received = 0
        self.last_msg = time.time()

    @property
    def s(self): return db.settings_all(self.c)

    # ---------- websocket ----------
    async def ws_loop(self):
        backoff = 1
        while True:
            try:
                async with websockets.connect(PUMPPORTAL_WS, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps({"method": "subscribeMigration"}))
                    log.info("ws open, subscribeMigration sent")
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=self.s["scanner.ws_silent_s"])
                        self.last_msg = time.time(); backoff = 1
                        try: msg = json.loads(raw)
                        except ValueError: continue
                        if isinstance(msg, dict) and msg.get(fields.PUMPPORTAL["mint"]):
                            asyncio.create_task(self.on_migration(msg))
                        else:
                            log.info("ws message (no mint): %s", str(msg)[:120])
            except asyncio.TimeoutError:
                log.warning("ws silent for %ss, reconnect in %ss", self.s["scanner.ws_silent_s"], backoff)
            except Exception as e:
                log.warning("ws closed (%s), reconnect in %ss", type(e).__name__, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.s["scanner.ws_backoff_max_s"])

    async def on_migration(self, msg: dict):
        mint = fields.dig(msg, fields.PUMPPORTAL["mint"])
        if paused(self.c, NAME):
            log.info("paused: migration ignored mint=%s", mint); return
        if self.c.execute("SELECT 1 FROM coins WHERE mint=?", (mint,)).fetchone():
            log.info("duplicate migration ignored mint=%s", mint); return
        self.received += 1
        migrated_at = db.now_iso()
        creator = msg.get("creator")          # key not in the STEP 2 sample: stays null
        self.c.execute("INSERT INTO coins(mint,migrated_at,creator) VALUES(?,?,?)", (mint, migrated_at, creator))
        log.info("graduated mint=%s sig=%s pool_type=%s", mint, msg.get("signature"), msg.get("pool"))
        s = self.s
        if not creator:
            try:
                rep = await with_retry(lambda: self.src.rug_report(mint), s["auditor.retries"], s["auditor.retry_gap_s"])
                creator = fields.dig(rep, fields.RUG["creator"])
            except SourceError as e:
                log.warning("creator lookup failed mint=%s (%s)", mint, e)
        bal = None
        if creator:
            try:
                bal = await with_retry(lambda: onchain.wallet_balance_raw(self.src, creator, mint),
                                       s["auditor.retries"], s["auditor.retry_gap_s"])
            except SourceError as e:
                log.warning("creator balance failed mint=%s (%s)", mint, e)
        self.c.execute("UPDATE coins SET creator=?, creator_balance_at_migration=? WHERE mint=?", (creator, bal, mint))
        log.info("waiting room + mint=%s creator=%s creator_balance_at_migration=%s", mint, creator, bal)

    # ---------- 60s cycle ----------
    async def cycle(self):
        s = self.s
        if paused(self.c, NAME): return 0
        if db.local_today() != self.runners_day: self.runners, self.runners_day = {}, db.local_today()
        room = db.waiting_room(self.c, s["scanner.max_wait_min"])
        if not room: return 0
        mints = [r["mint"] for r in room]
        pairs = []
        for i in range(0, len(mints), DEX_BATCH):
            chunk = mints[i:i + DEX_BATCH]
            while True:
                try:
                    got = await self.src.dex_tokens(chunk); break
                except RateLimited:
                    log.warning("dexscreener 429, waiting %ss, room kept", s["scanner.dex_429_wait_s"])
                    await asyncio.sleep(s["scanner.dex_429_wait_s"])
                except SourceError as e:
                    log.warning("dexscreener error %s, room kept", e); return 0
            db.write_price_batch(self.c, NAME, got)
            pairs += got
        for p in pairs:   # today's runners: every coin seen today with priceChange.h24 >= [+300%]
            h24 = fields.dig(p, "priceChange.h24")
            if h24 is not None and h24 >= s["scanner.runner_h24_pct"]:
                self.runners[fields.dig(p, "baseToken.address")] = (fields.dig(p, "baseToken.name"), fields.dig(p, "baseToken.symbol"))
        now = time.time()
        for row in room:
            await self.judge(row, pairs, s, now)
        return len(room)

    async def judge(self, row, pairs, s, now):
        mint = row["mint"]
        in_room_min = (now - db.parse_iso(row["migrated_at"])) / 60
        pair = onchain.best_pumpswap_pair(pairs, mint)
        if pair is None:
            if in_room_min >= s["scanner.max_wait_min"]:
                return self.emit(row, None, None, None, None, "kill", "never qualified")
            return                                   # DexScreener doesn't know it yet: it waits
        d = fields.dex_read(pair)
        self.c.execute("UPDATE coins SET name=?, symbol=?, pool=? WHERE mint=?", (d["name"], d["symbol"], d["pool"], mint))
        liq = d["liquidity_usd"]
        if liq is None:
            if in_room_min >= s["scanner.max_wait_min"]:
                return self.emit(row, d, None, None, None, "kill", "never qualified")
            return
        age_min = int((now * 1000 - (d["pair_created_ms"] or now * 1000)) / 60000)
        top10 = None
        verdict, reason, need = decide(s, liq, None, d["chg_h1"], None, age_min)
        if need:
            try:
                if mint not in self.supply: self.supply[mint] = await onchain.supply_raw(self.src, mint)
                if self.supply[mint] > 0: top10 = await onchain.top10_pct(self.src, mint, d["pool"], self.supply[mint])
            except SourceError as e:
                log.warning("top10 read failed mint=%s (%s), waiting", mint, e)
            cc = copycat_of(d["name"], d["symbol"], mint, self.runners)
            verdict, reason, _ = decide(s, liq, top10, d["chg_h1"], cc, age_min)
        if verdict is None and in_room_min >= s["scanner.max_wait_min"]:
            verdict, reason = "kill", "never qualified"
        if verdict: self.emit(row, d, liq, top10, age_min, verdict, reason)

    def emit(self, row, d, liq, top10, age_min, verdict, reason):
        mint = row["mint"]
        night_start, _ = db.night_bounds()
        if verdict == "pass" and self.c.execute(
                "SELECT 1 FROM scans WHERE mint=? AND verdict='pass' AND ts>=?", (mint, night_start)).fetchone():
            return                                     # never sends the same mint twice in a night
        out = {"agent": "scanner", "mint": mint, "name": (d or {}).get("name"), "symbol": (d or {}).get("symbol"),
               "pool": (d or {}).get("pool"), "liquidityUsd": liq, "top10Pct": top10,
               "h1ChangePct": (d or {}).get("chg_h1"), "ageMin": age_min,
               "verdict": verdict, "reason": reason, "ts": db.now_iso()}
        self.c.execute("INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,h1_change_pct,age_min,verdict,reason) "
                       "VALUES(?,?,?,?,?,?,?,?)", (mint, out["ts"], liq, top10, out["h1ChangePct"], age_min, verdict, reason))
        log.info("decision ts=%s mint=%s verdict=%s reason=%s liq=%s top10=%s h1=%s age=%s",
                 out["ts"], mint, verdict, reason, liq, top10, out["h1ChangePct"], age_min)
        if verdict == "pass":
            self.bus.put_nowait("auditor", out)
        return out

    async def cycle_loop(self):
        while True:
            t0 = time.time()
            try:
                n = await self.cycle()
                db.heartbeat(self.c, NAME)
            except Exception as e:
                log.exception("cycle error: %s", e)
            await asyncio.sleep(max(1, self.s["scanner.poll_s"] - (time.time() - t0)))

async def run(conn, bus, src):
    sc = Scanner(conn, bus, src)
    db.heartbeat(conn, NAME)
    log.info("start")
    await asyncio.gather(sc.ws_loop(), sc.cycle_loop(), _hb(conn))

async def _hb(conn):
    while True:
        db.heartbeat(conn, NAME); await asyncio.sleep(15)
