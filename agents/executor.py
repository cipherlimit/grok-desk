"""Agent 4 Executor. Never places orders; arithmetic + 1 Telegram button. Stub loop for STEP 3."""
from agents.base import idle_loop, agent_logger
NAME = "executor"
log = agent_logger(NAME)

def size(stack: float, open_usd: float, liquidity_usd: float, s: dict) -> tuple[float, str]:
    free = stack - open_usd
    raw = stack * s["executor.size_pct"] / 100
    cap = "size_pct"
    liq_cap = liquidity_usd * s["executor.liq_cap_pct"] / 100
    out = raw
    if liq_cap < out: out, cap = liq_cap, "liquidity"
    if free < out: out, cap = max(free, 0), "free"
    return round(out, 2), cap

async def run(conn, bus, settings):
    log.info("start (stub)")
    await idle_loop(NAME, conn)
