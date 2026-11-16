"""Agent 3 Analyst. rules mode gate implemented in code; Jev mode not wired (no key)."""
from agents.base import idle_loop, agent_logger
NAME = "analyst"
log = agent_logger(NAME)

def clamp(x, lo=0.0, hi=1.0): return max(lo, min(hi, x))

def rules_answers(dex: dict, bundled_wallets: int, s: dict) -> dict:
    alive = clamp((dex.get("vol_h1") or 0) / s["analyst.rules.vol_h1_full"])
    organic = clamp((dex.get("buys_h1") or 0) / s["analyst.rules.buys_h1_full"])
    if bundled_wallets >= s["analyst.rules.bundle_wallets"]:
        organic *= s["analyst.rules.bundle_penalty"]
    m5 = dex.get("chg_m5") or 0
    if m5 <= -s["analyst.rules.flush_pct"] and (dex.get("buys_m5") or 0) >= (dex.get("sells_m5") or 0): flush = 1.0
    elif m5 < 0: flush = 0.5
    else: flush = 0.0
    chase = clamp((dex.get("chg_h1") or 0) / s["analyst.rules.chase_h1_full"])
    return {"alive_6h": alive, "organic": organic, "flush": flush, "chase": chase}

def gate(a: dict, s: dict) -> tuple[int, bool, list[str]]:
    for k, v in a.items():
        if v is None or not (0 <= v <= 1): return 0, False, ["bad answer"]
    failed = []
    if a["alive_6h"] < s["analyst.alive_6h_min"]: failed.append(f"alive_6h {a['alive_6h']:.2f}")
    if a["organic"] < s["analyst.organic_min"]: failed.append(f"organic {a['organic']:.2f}")
    if a["chase"] > s["analyst.chase_max"]: failed.append(f"chase {a['chase']:.2f}")
    score = round(100 * (a["alive_6h"] + a["organic"] + a["flush"] + (1 - a["chase"])) / 4)
    if score < s["analyst.score_min"]: failed.append(f"score {score}")
    return score, not failed, failed

async def run(conn, bus, settings):
    log.info("start (stub, analyst_mode=%s)", settings.get("analyst_mode"))
    await idle_loop(NAME, conn)
