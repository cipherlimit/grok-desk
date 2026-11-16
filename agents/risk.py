"""Agent 5 · Risk. Exits from fill to exit, rules in order every [15]s poll. (PART 5, Agent 5)
Never places an order: pushes [Sell on FOMO] (links.coin_url) + [Sold]."""
import asyncio, time
import db, fees, links, onchain, telegram
from sources import DEX_BATCH, RateLimited, SourceError
from agents.base import agent_logger, paused

NAME = "risk"
log = agent_logger(NAME)

def fmt_pct(x: float) -> str:
    return f"{x:.1f}".rstrip("0").rstrip(".")

def signed_usd(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):.2f}"

def signed_pct(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}{fmt_pct(abs(x))}%"

def pos_stop_pct(entry: float, stop_price: float) -> float:
    return round((1 - stop_price / entry) * 100, 6)

def evaluate(pos: dict, price: float, hours_open: float, s: dict) -> tuple[dict, str | None]:
    """RULES in order: peak, stop, trail on, trail, timeout. pos uses entry_price, peak_price,
    stop_price, trail_active. Exit price = the polled price that fired it."""
    p = dict(pos)
    p["peak_price"] = max(p["peak_price"], price)
    e = p["entry_price"]
    if price <= p["stop_price"]: return p, "stop"
    if price >= e * (1 + s["risk.trail_start_pct"] / 100): p["trail_active"] = 1
    if p["trail_active"] and price <= p["peak_price"] * (1 - s["risk.trail_pct"] / 100): return p, "trail"
    if hours_open >= s["risk.timeout_h"] and price < e * (1 + s["risk.timeout_min_gain_pct"] / 100): return p, "timeout"
    return p, None

def tightened_stop(entry: float, stop_price: float, setting_pct: float) -> float:
    """Never moves a stop down: an open position only takes a new setting if it is tighter."""
    return max(stop_price, entry * (1 - setting_pct / 100))

def push_text(reason, name, pnl, pct, stop_pct, timeout_h) -> str:
    if reason == "trail": return f"Sold {name} · {signed_usd(pnl)} ({signed_pct(pct)})"
    if reason == "stop": return f"{name} hit the -{fmt_pct(stop_pct)}% stop · -${abs(pnl):.2f}"
    if reason == "timeout": return f"Sell {name}, dead money · {signed_pct(pct)} in {fmt_pct(timeout_h)}h"
    return f"Sell {name}, liquidity pulled · {signed_pct(pct)}"       # "pulled": no text in the prompt

def activity_row(reason, name, pnl, pct, stop_pct):
    if reason == "stop":
        return "stopped", f"Stopped {name}", f"Risk · -{fmt_pct(stop_pct)}% stop", f"-${abs(pnl):.2f}"
    sub = {"trail": "Risk · trailing stop", "timeout": "Risk · dead money", "pulled": "Risk · liquidity pulled"}[reason]
    return "sold", f"Sold {name}", sub, f"{signed_usd(pnl)} · {signed_pct(pct)}"

class Risk:
    def __init__(self, conn, bus, src):
        self.c, self.bus, self.src = conn, bus, src
        self.no_price: dict[int, int] = {}
        self.exiting: dict[int, dict] = {}   # LIVE: waiting for [Sold]

    def name(self, mint):
        r = self.c.execute("SELECT name FROM coins WHERE mint=?", (mint,)).fetchone()
        return (r["name"] if r else None) or mint[:6]

    def sell_buttons(self, pos_id, mint):
        return [[telegram.url_button("Sell on FOMO", links.coin_url(mint, "solana"))],
                [telegram.cb_button("Sold", f"sold:{pos_id}")]]

    async def poll(self) -> int:
        s = db.settings_all(self.c)
        rows = [dict(r) for r in db.open_positions(self.c)]
        live = [r for r in rows if r["exit_reason"] is None]       # "exiting" ones wait for [Sold]
        if not live: return 0                                      # paused only matters with nothing open
        mints = sorted({r["mint"] for r in live})
        pairs, failed = [], False
        for i in range(0, len(mints), DEX_BATCH):
            try:
                got = await self.src.dex_tokens(mints[i:i + DEX_BATCH])
                db.write_price_batch(self.c, NAME, got); pairs += got
            except RateLimited:
                missed = int(s["risk.backoff_429_s"] // s["risk.poll_s"])
                log.warning("dexscreener 429: backing off %ss, missed polls=%d", s["risk.backoff_429_s"], missed)
                await asyncio.sleep(s["risk.backoff_429_s"]); return len(live)
            except SourceError as e:
                log.warning("dexscreener error %s: no price this poll", e); failed = True
        for pos in live:
            await self.check(pos, None if failed else onchain.best_pumpswap_pair(pairs, pos["mint"]), s)
        return len(live)

    async def check(self, pos, pair, s):
        pid, mint, name = pos["id"], pos["mint"], self.name(pos["mint"])
        st = tightened_stop(pos["entry_price"], pos["stop_price"], s["risk.stop_pct"])
        if st > pos["stop_price"]:
            self.c.execute("UPDATE positions SET stop_price=? WHERE id=?", (st, pid)); pos["stop_price"] = st
        price = float(pair["priceUsd"]) if pair and pair.get("priceUsd") is not None else None
        liq = ((pair or {}).get("liquidity") or {}).get("usd") if pair else None
        if pair is not None and liq is not None and liq <= 0:
            last = price or self.last_price(mint)
            if last is not None: return await self.exit(pos, last, "pulled", s, name)
        if price is None:
            n = self.no_price[pid] = self.no_price.get(pid, 0) + 1
            log.info("pos=%s mint=%s price=None peak=%s stop=%s action=no price (%d)", pid, mint, pos["peak_price"], pos["stop_price"], n)
            if n == int(s["risk.no_price_polls"]):
                await telegram.send(f"{name}: no price for 1 min",
                                    [[telegram.url_button("Sell on FOMO", links.coin_url(mint, "solana"))]])
            return
        self.no_price[pid] = 0
        hours = (time.time() - db.parse_iso(pos["opened_at"])) / 3600
        p, reason = evaluate(pos, price, hours, s)
        trail_line = p["peak_price"] * (1 - s["risk.trail_pct"] / 100) if p["trail_active"] else None
        self.c.execute("UPDATE positions SET peak_price=?, trail_active=? WHERE id=?", (p["peak_price"], int(p["trail_active"]), pid))
        log.info("pos=%s mint=%s price=%s peak=%s stop=%s trail=%s action=%s", pid, mint, price, p["peak_price"],
                 p["stop_price"], trail_line, reason or "hold")
        if reason: await self.exit(p, price, reason, s, name)

    def last_price(self, mint):
        r = self.c.execute("SELECT price FROM prices WHERE mint=? AND price IS NOT NULL ORDER BY ts DESC LIMIT 1", (mint,)).fetchone()
        return r["price"] if r else None

    async def exit(self, pos, price, reason, s, name):
        pid, mint, mode = pos["id"], pos["mint"], pos["mode"]
        pnl, fee = fees.paper_pnl(pos["size_usd"], pos["entry_price"], price, s["desk.fees_ref_discount"])
        pct = 100 * pnl / pos["size_usd"]
        stop_pct = pos_stop_pct(pos["entry_price"], pos["stop_price"])
        link = links.coin_url(mint, "solana")
        status = "closed" if mode == "PAPER" else "exiting"
        ts = db.now_iso()
        if mode == "PAPER":
            self.c.execute("UPDATE positions SET closed_at=?, exit_price=?, exit_reason=?, pnl_usd=?, fees_usd=?, peak_price=? WHERE id=?",
                           (ts, price, reason, round(pnl, 6), fee, pos["peak_price"], pid))
            db.add_balance(self.c, (db.last_balance(self.c) or s["desk.paper_start_stack"]) + pnl)
            kind, title, sub, value = activity_row(reason, name, pnl, pct, stop_pct)
            db.activity(self.c, kind, NAME, title, sub, value, mint)
        else:
            self.c.execute("UPDATE positions SET exit_price=?, exit_reason=? WHERE id=?", (price, reason, pid))
        text = push_text(reason, name, pnl, pct, stop_pct, s["risk.timeout_h"])
        await telegram.send(text, self.sell_buttons(pid, mint))
        out = {"agent": "risk", "mint": mint, "name": name, "ts": ts, "reason": reason, "entryPrice": pos["entry_price"],
               "exitPrice": price, "peakPrice": pos["peak_price"], "pnlUsd": round(pnl, 2), "pct": round(pct, 2),
               "feesUsd": round(fee, 2), "stopPct": stop_pct, "mode": mode, "status": status, "link": link}
        log.info("exit %s", out)
        if mode == "LIVE": self.exiting[pid] = {"out": out, "since": time.time(), "repushed": False, "text": text}
        return out

    async def live_reminders(self):
        while True:
            s = db.settings_all(self.c)
            for pid, e in list(self.exiting.items()):
                if not e["repushed"] and time.time() - e["since"] >= s["risk.confirm_window_min"] * 60:
                    e["repushed"] = True
                    await telegram.send(e["text"] + "\nstill open", self.sell_buttons(pid, e["out"]["mint"]))
            await asyncio.sleep(5)

    async def callbacks(self):
        while True:
            cb = await self.bus.get("cb.risk")
            try: pid = int(cb["position_id"])
            except (ValueError, KeyError): continue
            e = self.exiting.pop(pid, None)
            if not e: continue
            o, s = e["out"], db.settings_all(self.c)
            self.c.execute("UPDATE positions SET closed_at=?, pnl_usd=?, fees_usd=? WHERE id=?", (db.now_iso(), o["pnlUsd"], o["feesUsd"], pid))
            kind, title, sub, value = activity_row(o["reason"], o["name"], o["pnlUsd"], o["pct"], o["stopPct"])
            db.activity(self.c, kind, NAME, title, sub, value, o["mint"])
            log.info("sold tapped pos=%s closed", pid)

async def run(conn, bus, src):
    r = Risk(conn, bus, src)
    log.info("start")
    async def loop():
        while True:
            t0 = time.time()
            try: await r.poll()
            except Exception as e: log.exception("risk poll error: %s", e)
            db.heartbeat(conn, NAME)
            await asyncio.sleep(max(1, db.settings_all(conn)["risk.poll_s"] - (time.time() - t0)))
    async def drain():                # Executor hands fills over; positions are read from db each poll
        while True: await bus.get("risk")
    await asyncio.gather(loop(), drain(), r.live_reminders(), r.callbacks())
