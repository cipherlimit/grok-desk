"""Agent 5 Risk. Exit rules as a pure function; poller wired in a later step."""
from agents.base import idle_loop, agent_logger
NAME = "risk"
log = agent_logger(NAME)

def evaluate(pos: dict, price: float, hours_open: float, s: dict) -> tuple[dict, str | None]:
    """Order: peak, stop, trail on, trail, timeout. Returns (updated pos, exit reason|None)."""
    p = dict(pos)
    p["peak_price"] = max(p["peak_price"], price)
    e = p["entry_price"]
    if price <= e * (1 - p["stop_pct"] / 100): return p, "stop"
    if price >= e * (1 + s["risk.trail_on_pct"] / 100): p["trail_active"] = True
    if p.get("trail_active") and price <= p["peak_price"] * (1 - s["risk.trail_pct"] / 100): return p, "trail"
    if hours_open >= s["risk.timeout_h"] and price < e * (1 + s["risk.timeout_min_gain_pct"] / 100): return p, "timeout"
    return p, None

def tighter_stop(current_pct: float, new_pct: float) -> float:
    """Never move a stop down: open positions only take a tighter stop."""
    return min(current_pct, new_pct)

async def run(conn, bus, settings):
    log.info("start (stub)")
    await idle_loop(NAME, conn)
