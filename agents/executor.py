"""Agent 4 · Executor. Never picks a coin, never places an order, never touches funds.
Arithmetic + 1 Telegram button; PAPER fills at the signal price at once. (PART 5, Agent 4)"""
import asyncio, time
import db, fields, links, onchain, telegram
from sources import SourceError, with_retry
from agents.base import agent_logger

NAME = "executor"
log = agent_logger(NAME)

def fmt_pct(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")

def size_for(stack: float, open_usd: float, liquidity_usd: float, s: dict) -> tuple[float | None, str, float, str | None]:
    """Returns (final size | None, cap hit, raw size, skip reason)."""
    free = stack - open_usd
    raw = stack * s["executor.size_pct"] / 100
    size, cap = raw, "size_pct"
    liq_cap = (liquidity_usd or 0) * s["executor.liq_cap_pct"] / 100
    if liq_cap < size: size, cap = liq_cap, "liquidity"
    mn = s["executor.min_usd"]
    if size < mn:
        if s["below_min"] == "bump":
            size, cap = mn, "bump"
        else:
            return None, cap, raw, f"under ${mn:.2f}"
    if size > free: return None, cap, raw, "no free stack"
    return round(size, 2), cap, raw, None

def recheck(liq_now, liq_scan, price_now, price_analyst, s) -> str | None:
    if liq_scan and liq_now is not None and liq_now < liq_scan * (1 - s["executor.liq_drop_max_pct"] / 100):
        return "liquidity dropping"
    if price_analyst and price_now > price_analyst * (1 + s["executor.price_run_max_pct"] / 100):
        return "ran before the push"
    return None

def push_text(mode, name, size, pct, score, stop_pct, trail_pct) -> str:
    return (f"{'PAPER · ' if mode == 'PAPER' else ''}Buy {name} · ${size:.2f} · {fmt_pct(pct)}% of stack\n"
            f"Auditor 8/8 · Analyst {score} · stop -{fmt_pct(stop_pct)}% · trail {fmt_pct(trail_pct)}%")

class Executor:
    def __init__(self, conn, bus, src):
        self.c, self.bus, self.src = conn, bus, src
        self.pending: dict[str, asyncio.Future] = {}    # LIVE: waiting for [Bought]

    def _out(self, mint, price, size, pct, link, mode, status, reason):
        return {"agent": "executor", "mint": mint, "signalPrice": price, "sizeUsd": size, "stackPct": pct,
                "link": link, "mode": mode, "status": status, "reason": reason}

    def _skip(self, mint, mode, link, reason, stack, raw, cap, price=None):
        self.c.execute("INSERT INTO signals(mint,ts,price,size_usd,stack_pct,link,mode,status) VALUES(?,?,?,?,?,?,?,?)",
                       (mint, db.now_iso(), price, None, None, link, mode, "skipped"))
        log.info("mint=%s stack=%s raw=%s cap=%s final=None mode=%s status=skipped reason=%s link=%s",
                 mint, stack, raw, cap, mode, reason, link)
        return self._out(mint, price, None, None, link, mode, "skipped", reason)

    async def handle(self, msg: dict) -> dict | None:
        an, scan = msg["analyst"], msg["scanner"]
        mint, s = an["mint"], db.settings_all(self.c)
        mode = s["mode"]; link = links.coin_url(mint, "solana")
        night_start, _ = db.night_bounds()
        if self.c.execute("SELECT 1 FROM signals WHERE mint=? AND ts>=?", (mint, night_start)).fetchone():
            log.info("2nd pass ignored (1 push per coin per night) mint=%s", mint); return None
        if self.c.execute("SELECT 1 FROM positions WHERE mint=? AND closed_at IS NULL", (mint,)).fetchone():
            log.info("open position exists, never a 2nd buy mint=%s", mint); return None
        if db.is_skipped(self.c, mint):
            log.info("in db.skips, gone for the night mint=%s", mint); return None
        stack = (db.last_balance(self.c) or s["paper_start_stack"]) if mode == "PAPER" else (s["stack"] or 0)
        open_usd = sum(r["size_usd"] for r in db.open_positions(self.c))
        try:
            pairs = await with_retry(lambda: self.src.dex_tokens([mint]), 2, 2)
            pair = onchain.best_pumpswap_pair(pairs, mint)
        except SourceError:
            pair = None
        d = fields.dex_read(pair) if pair else {}
        price, liq = d.get("price_usd"), d.get("liquidity_usd")
        size, cap, raw, why = size_for(stack, open_usd, liq, s)
        if why: return self._skip(mint, mode, link, why, stack, raw, cap)
        if not price: return self._skip(mint, mode, link, "no price", stack, raw, cap)
        why = recheck(liq, scan.get("liquidityUsd"), price, msg.get("price_at_analyst"), s)
        if why: return self._skip(mint, mode, link, why, stack, raw, cap, price)
        pct = round(100 * size / stack, 1)
        name = d.get("name") or msg.get("name") or mint[:6]
        text = push_text(mode, name, size, pct, an["score"], s["risk.stop_pct"], s["risk.trail_pct"])
        buttons = [[telegram.url_button("Buy on FOMO", link)],
                   [telegram.cb_button("Bought", f"bought:{mint}"), telegram.cb_button("Skip", f"skip:{mint}")]]
        cur = self.c.execute("INSERT INTO signals(mint,ts,price,size_usd,stack_pct,link,mode,status) VALUES(?,?,?,?,?,?,?,?)",
                             (mint, db.now_iso(), price, size, pct, link, mode, "sent"))
        sig_id = cur.lastrowid
        if mode == "LIVE": self.pending[mint] = asyncio.get_running_loop().create_future()
        pushed = await telegram.send(text, buttons)
        if not pushed: log.error("push failed mint=%s", mint)
        status = "sent"
        if mode == "PAPER":
            status = "bought"                       # fill anyway: the paper night stays honest
        elif pushed:
            try:
                r = await asyncio.wait_for(self.pending[mint], s["executor.bought_wait_min"] * 60)
                status = "bought" if r == "bought" else "skipped"
            except asyncio.TimeoutError:
                status = "missed"
        else:
            status = "missed"                       # LIVE + Telegram down = no position
        self.pending.pop(mint, None)
        self.c.execute("UPDATE signals SET status=? WHERE id=?", (status, sig_id))
        if status == "bought": self.fill(mint, price, size, pct, mode, s)
        log.info("mint=%s stack=%s raw=%s cap=%s final=%s mode=%s status=%s link=%s%s",
                 mint, stack, round(raw, 2), cap, size, mode, status, link, "" if pushed else " push failed")
        return self._out(mint, price, size, pct, link, mode, status, None if status in ("sent", "bought") else status)

    def fill(self, mint, price, size, pct, mode, s):
        stop_pct = s["risk.stop_pct"]
        cur = self.c.execute("INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,trail_active,mode) "
                             "VALUES(?,?,?,?,?,?,0,?)", (mint, db.now_iso(), price, size, price, price * (1 - stop_pct / 100), mode))
        name = (self.c.execute("SELECT name FROM coins WHERE mint=?", (mint,)).fetchone() or {"name": None})["name"] or mint[:6]
        db.activity(self.c, "bought", NAME, f"Bought {name}", f"Executor · {db.local_hhmm()}",
                    f"${size:.2f} · {fmt_pct(pct)}% of stack", mint)
        self.bus.put_nowait("risk", {"position_id": cur.lastrowid, "mint": mint})

    async def callbacks(self):
        while True:
            cb = await self.bus.get("cb.executor")
            mint = cb.get("mint")
            if cb["action"] == "skip":
                self.c.execute("INSERT OR REPLACE INTO skips(mint,until) VALUES(?,?)", (mint, db.night_bounds()[1]))
                log.info("skip tapped, mint in db.skips until morning mint=%s", mint)
            fut = self.pending.get(mint)
            if fut and not fut.done(): fut.set_result(cb["action"])

async def run(conn, bus, src):
    ex = Executor(conn, bus, src)
    log.info("start")
    async def hb():
        while True: db.heartbeat(conn, NAME); await asyncio.sleep(15)
    async def work():
        while True:
            m = await bus.get("executor")
            try: asyncio.create_task(ex.handle(m)) if db.settings_all(conn)["mode"] == "LIVE" else await ex.handle(m)
            except Exception as e: log.exception("executor error: %s", e)
    await asyncio.gather(hb(), work(), ex.callbacks())
