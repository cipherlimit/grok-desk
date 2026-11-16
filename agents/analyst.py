"""Agent 3 · Analyst. Builds a numbers-only state, asks 4 closed "noul" questions (Jev) or
computes them in code (rules mode), and code applies the GATE. (PART 5, Agent 3)
analyst_mode = "rules" now (no TYPESAFE_API_KEY). The Jev path is wired but inactive."""
import asyncio, hashlib, json, re, time
import httpx
import db, fields, onchain
from config import secret
from sources import SourceError, with_retry
from agents.base import agent_logger, wait_unpaused

NAME = "analyst"
log = agent_logger(NAME)
JEV_URL = "https://api.typesafe.ai/v1/systemone"
QUESTIONS = {
    "alive_6h": {"type": "noul", "instructions": "This coin is still actively traded 6 hours from now"},
    "organic": {"type": "noul", "instructions": "Most buyers in the last 30 minutes are separate real wallets"},
    "flush": {"type": "noul", "instructions": "Price just flushed down and buyers are absorbing it"},
    "chase": {"type": "noul", "instructions": "This entry is chasing a green candle that already ran"},
}

def clamp(x, lo=0.0, hi=1.0): return max(lo, min(hi, x))

def rules_answers(dex: dict, bundled_wallets: int, s: dict) -> dict:
    alive = clamp((dex.get("vol_h1") or 0) / s["analyst.rules.alive_volume_h1"])
    organic = clamp((dex.get("buys_h1") or 0) / s["analyst.rules.organic_buys_h1"])
    if bundled_wallets >= s["analyst.rules.bundled_wallets"]:
        organic *= s["analyst.rules.bundled_penalty"]
    m5 = dex.get("chg_m5") or 0
    if m5 <= -s["analyst.rules.flush_drop_m5_pct"] and (dex.get("buys_m5") or 0) >= (dex.get("sells_m5") or 0): flush = 1.0
    elif m5 < 0: flush = 0.5
    else: flush = 0.0
    chase = clamp((dex.get("chg_h1") or 0) / s["analyst.rules.chase_h1_pct"])
    return {"alive_6h": alive, "organic": organic, "flush": flush, "chase": chase}

def valid(a) -> bool:
    return isinstance(a, dict) and all(isinstance(a.get(k), (int, float)) and 0 <= a[k] <= 1 for k in QUESTIONS)

def gate(a: dict, s: dict) -> tuple[int, bool, list[str]]:
    failed = []
    if a["alive_6h"] < s["analyst.min_alive_6h"]: failed.append(f"alive_6h {a['alive_6h']:.2f}")
    if a["organic"] < s["analyst.min_organic"]: failed.append(f"organic {a['organic']:.2f}")
    if a["chase"] > s["analyst.max_chase"]: failed.append(f"chase {a['chase']:.2f}")
    score = round(100 * (a["alive_6h"] + a["organic"] + a["flush"] + (1 - a["chase"])) / 4)
    if score < s["analyst.min_score"]: failed.append(f"score {score}")
    return score, not failed, failed

def bundled_from(auditor: dict) -> int:
    v = next((l["value"] for l in auditor.get("layers", []) if l["n"] == 7), "None")
    m = re.match(r"(\d+)", v or "")
    return int(m.group(1)) if m else 0

def _num(x, nd=2):
    if x is None: return "null"
    return f"{x:,.{nd}f}".rstrip("0").rstrip(".") if isinstance(x, float) else f"{x:,}"

def build_state(scan: dict, auditor: dict, d: dict, dev_pct, age_min: int, max_chars: int) -> str:
    lv = {l["n"]: l["value"] for l in auditor.get("layers", [])}
    lp = lv.get(5, "null"); bund = lv.get(7, "null"); dele = lv.get(8, "null")
    top10 = re.match(r"([\d.]+)", lv.get(4, "") or "")
    st = (f"Coin {d.get('name') or scan.get('name')} on Solana, graduated {age_min} min ago. "
          f"Liquidity ${_num(d.get('liquidity_usd'), 0)}. Market cap ${_num(d.get('mcap'), 0)}.\n"
          f" Last 5 min: {_num(d.get('buys_m5'))} buys, {_num(d.get('sells_m5'))} sells, volume ${_num(d.get('vol_m5'), 0)}, price {_num(d.get('chg_m5'))}%.\n"
          f" Last 1h: {_num(d.get('buys_h1'))} buys, {_num(d.get('sells_h1'))} sells, volume ${_num(d.get('vol_h1'), 0)}, price {_num(d.get('chg_h1'))}%.\n"
          f" Top 10 hold {top10.group(1) if top10 else 'null'}%. Dev holds {_num(dev_pct, 1) if dev_pct is not None else 'null'}%, sold 0. "
          f"LP {lp}. Bundled {bund}. Delegations {dele}.")
    return st[:int(max_chars)]

async def ask_jev(state: str, s: dict, client: httpx.AsyncClient) -> tuple[dict | None, str, dict | None, str]:
    """Returns (answers|None, model, usage, error). Only "noul" questions, no extra fields."""
    key = secret("TYPESAFE_API_KEY")
    if not key: return None, "jev-latest", None, "no answer"
    body = {"model": "jev-latest", "state": state, "questions": QUESTIONS}
    hdr = {"Authorization": f"Bearer {key}"}
    t_start, tried_retry = time.monotonic(), False
    while True:
        try:
            r = await client.post(JEV_URL, json=body, headers=hdr, timeout=s["analyst.timeout_s"])
        except httpx.HTTPError:
            r = None
        if r is not None and r.status_code == 429:
            if time.monotonic() - t_start >= s["analyst.queue_429_s"]: return None, "jev-latest", None, "no answer"
            await asyncio.sleep(2); continue
        if r is None or r.status_code != 200:
            if tried_retry: return None, "jev-latest", None, "no answer"
            tried_retry = True; await asyncio.sleep(s["analyst.retry_after_s"]); continue
        j = r.json()
        try: ans = {k: j["answers"][k]["noul"] for k in QUESTIONS}
        except (KeyError, TypeError):
            log.warning("bad answer raw=%s", r.text[:2000]); return None, j.get("model", "jev-latest"), j.get("usage"), "bad answer"
        if not valid(ans):
            log.warning("bad answer raw=%s", r.text[:2000]); return None, j.get("model", "jev-latest"), j.get("usage"), "bad answer"
        return ans, j.get("model", "jev-latest"), j.get("usage"), ""

class Analyst:
    def __init__(self, conn, bus, src):
        self.c, self.bus, self.src = conn, bus, src
        self.http = httpx.AsyncClient()

    async def analyze(self, msg: dict) -> dict | None:
        scan, aud = msg["scanner"], msg["auditor"]
        mint, s = scan["mint"], db.settings_all(self.c)
        if self.c.execute("SELECT 1 FROM verdicts WHERE mint=?", (mint,)).fetchone():
            log.info("already asked, never re-asks mint=%s", mint); return None
        rules = s["analyst.mode"] == "rules"
        model, tokens, answers, err, d = ("rules" if rules else "jev-latest"), 0, None, "", {}
        try:
            pairs = await with_retry(lambda: self.src.dex_tokens([mint]), 2, s["analyst.retry_after_s"])
            pair = onchain.best_pumpswap_pair(pairs, mint)
            d = fields.dex_read(pair) if pair else {}
        except SourceError:
            pass
        coin = self.c.execute("SELECT migrated_at FROM coins WHERE mint=?", (mint,)).fetchone()
        age = int((time.time() - db.parse_iso(coin["migrated_at"])) / 60) if coin else scan.get("ageMin")
        state = build_state(scan, aud, d, msg.get("dev_pct"), age, s["analyst.state_max_chars"])
        shash = hashlib.sha256(state.encode()).hexdigest()[:16]
        if not d or d.get("price_usd") is None:
            err = "no answer"
        elif rules:
            answers = rules_answers(d, bundled_from(aud), s)
            if not valid(answers): answers, err = None, "bad answer"
        else:
            answers, model, usage, err = await ask_jev(state, s, self.http)
            tokens = int((usage or {}).get("input_tokens") or (usage or {}).get("inputTokens") or 0)
        if answers:
            score, passed, failed = gate(answers, s)
        else:
            score, passed, failed = 0, False, [err]
        out = {"agent": "analyst", "mint": mint, "answers": answers or {k: None for k in QUESTIONS},
               "score": score, "passed": passed, "failed": failed, "model": model, "inputTokens": tokens}
        a = answers or {}
        self.c.execute("INSERT INTO verdicts(mint,ts,alive_6h,organic,flush,chase,score,pass,reason,input_tokens) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?)", (mint, db.now_iso(), a.get("alive_6h"), a.get("organic"),
                       a.get("flush"), a.get("chase"), score, int(passed), failed[0] if failed else None, tokens))
        log.info("mint=%s state_hash=%s alive_6h=%s organic=%s flush=%s chase=%s score=%s verdict=%s input_tokens=%s",
                 mint, shash, a.get("alive_6h"), a.get("organic"), a.get("flush"), a.get("chase"), score,
                 "pass" if passed else "reject", tokens)
        if passed:
            await self.bus.put("executor", {"analyst": out, "scanner": scan, "price_at_analyst": d.get("price_usd"), "auditor_checked": aud.get("layersChecked", 8),
                                            "name": d.get("name") or scan.get("name")})
        else:
            label = "Analyst (rules)" if rules else "Analyst"
            name = d.get("name") or scan.get("name") or mint[:6]
            db.activity(self.c, "rejected", NAME, f"Rejected {name}", f"{label} · {failed[0]}", "Skipped", mint)
        return out

async def run(conn, bus, src):
    a = Analyst(conn, bus, src)
    log.info("start analyst_mode=%s", db.settings_all(conn)["analyst.mode"])
    async def hb():
        while True: db.heartbeat(conn, NAME); await asyncio.sleep(15)
    async def work():
        while True:
            m = await bus.get("analyst")
            await wait_unpaused(conn, NAME)
            try: await a.analyze(m)
            except Exception as e: log.exception("analyst error: %s", e)
    await asyncio.gather(hb(), work())
