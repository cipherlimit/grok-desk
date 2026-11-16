"""Agent 2 · Auditor. 8 layers in order, stop at the first red. (PART 4, Agent 2)
Every source: retry [3] tries [2]s apart; still nothing = red "no data: {layer}". No data is never a pass."""
import asyncio, time
import db, fields, onchain, telegram
from sources import SourceError, with_retry
from agents.base import agent_logger, wait_unpaused

NAME = "auditor"
log = agent_logger(NAME)
LAYERS = {1: "Mint authority", 2: "Freeze authority", 3: "Dev wallet", 4: "Top 10 holders",
          5: "Liquidity", 6: "Pool age", 7: "Bundled wallets", 8: "Delegations"}
NODATA = {1: "mint authority", 2: "freeze authority", 3: "dev", 4: "top 10", 5: "liquidity",
          6: "pool age", 7: "bundles", 8: "delegations"}

class NoData(Exception): pass

# ---------- pure layer logic (tested on raw samples) ----------
def l1_mint(acc) -> tuple[bool, str]:
    if acc is None: raise NoData
    info = fields.dig(acc, "result.value.data.parsed.info")
    if not isinstance(info, dict): raise NoData
    return (True, "Revoked") if info.get("mintAuthority") is None else (False, "mint not revoked")

def l2_freeze(acc) -> tuple[bool, str]:
    if acc is None: raise NoData
    info = fields.dig(acc, "result.value.data.parsed.info")
    if not isinstance(info, dict): raise NoData
    return (True, "Revoked") if info.get("freezeAuthority") is None else (False, "freeze not revoked")

def l3_dev(now_raw: int, at_migration_raw, supply_raw: int, max_pct: float) -> tuple[bool, str]:
    if not supply_raw: return False, "no supply"
    if at_migration_raw is None: raise NoData
    pct = 100.0 * now_raw / supply_raw
    if pct >= max_pct: return False, f"dev holds {pct:.0f}%"
    if now_raw < at_migration_raw: return False, "dev sold"
    return True, f"{pct:.1f}% · sold 0"

def l4_top10(pct: float, max_pct: float) -> tuple[bool, str]:
    return (True, f"{pct:.1f}%") if pct < max_pct else (False, f"top 10 hold {pct:.0f}%")

def l5_liquidity(report: dict, pool: str, liq_now, liq_scan, lp_min: float, drop_max: float) -> tuple[bool, str]:
    market = next((m for m in (report or {}).get("markets") or [] if m.get(fields.RUG_MARKET_POOL_KEY) == pool), None)
    if market is None: raise NoData
    locked = fields.dig(market, fields.RUG_LP_LOCKED_PCT)
    burned = fields.dig(market, "lp.lpBurnedPct")        # not in the STEP 2 samples: stays None
    if locked is None and burned is None: raise NoData
    best, word = max(((burned or 0), "burned"), ((locked or 0), "locked"))
    if best < lp_min: return False, "liquidity unlocked"
    if liq_now is None or not liq_scan: raise NoData
    if liq_now < liq_scan * (1 - drop_max / 100): return False, "liquidity pulled"
    return True, f"{best:.0f}% {word}"

def l6_age(migrated_at_ts: float, now: float, min_age: float) -> tuple[bool, str]:
    mins = (now - migrated_at_ts) / 60
    return (True, f"{int(mins)}m") if mins >= min_age else (False, "pool too young")

def biggest_bundle(graph) -> int:
    """Keep wallets holding this mint (holdings > 0); groups = connected components over links."""
    if not isinstance(graph, list) or not graph: raise NoData          # strict: [] is red
    holders, edges = set(), []
    for net in graph:
        if not isinstance(net, dict) or fields.GRAPH_EDGES not in net or fields.GRAPH_NODES not in net: raise NoData
        for n in net[fields.GRAPH_NODES]:
            if (n.get(fields.GRAPH_HOLDS) or 0) > 0: holders.add(n["id"])
        edges += [(l["source"], l["target"]) for l in net[fields.GRAPH_EDGES]]
    parent = {h: h for h in holders}
    def find(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for a, b in edges:
        if a in holders and b in holders: parent[find(a)] = find(b)
    sizes = {}
    for h in holders: r = find(h); sizes[r] = sizes.get(r, 0) + 1
    big = max(sizes.values(), default=0)
    return big if big >= 2 else 0          # a lone wallet is not a bundle

def l7_bundles(graph, max_n: int) -> tuple[bool, str, int]:
    n = biggest_bundle(graph)
    if n >= max_n: return False, f"{n} bundled wallets", n
    return True, ("None" if n == 0 else f"{n} bundled wallets"), n

def l8_delegations(accounts: list[dict], max_n: int) -> tuple[bool, str, int]:
    groups, holders = {}, 0
    for a in accounts:
        info = fields.dig(a, "account.data.parsed.info") or {}
        if int(fields.dig(info, "tokenAmount.amount") or 0) > 0: holders += 1
        d = info.get("delegate")
        if d: groups[d] = groups.get(d, 0) + 1
    if groups:
        d, n = max(groups.items(), key=lambda kv: kv[1])
        if n >= max_n: return False, f"{n} wallets → {d[:4]}...{d[-3:]}", holders
        return True, f"{n} wallets → {d[:4]}...{d[-3:]}", holders
    return True, "None", holders

# ---------- the agent ----------
class Auditor:
    def __init__(self, conn, bus, src):
        self.c, self.bus, self.src = conn, bus, src
        self.layer8_alerted_night = None

    async def audit(self, scan: dict, adhoc: bool = False) -> dict | None:
        """adhoc = Telegram "buy X" for a coin never pushed: audit now, no bus, no Activity, not on the desk."""
        mint, s = scan["mint"], db.settings_all(self.c)
        night_start, _ = db.night_bounds()
        if not adhoc and self.c.execute("SELECT 1 FROM audits WHERE mint=? AND ts>=?", (mint, night_start)).fetchone():
            log.info("already audited tonight, ignored mint=%s", mint); return None
        coin = self.c.execute("SELECT * FROM coins WHERE mint=?", (mint,)).fetchone()
        if not s["auditor.enabled"] and not adhoc:          # "Take apart before buying" off (asked twice in the app/Telegram)
            out = {"agent": "auditor", "mint": mint, "ts": db.now_iso(), "passed": True, "layersChecked": 0,
                   "layers": [], "holders": None, "reason": "auditor off"}
            log.info("auditor off: mint=%s sent on without layers", mint)
            await self.bus.put("analyst", {"scanner": scan, "auditor": out, "dev_pct": None})
            return out
        tries, gap = int(s["auditor.retries"]), s["auditor.retry_gap_s"]
        cache = {}
        async def get(key, fn):              # each source fetched once per audit, with retries
            if key not in cache:
                try: cache[key] = await with_retry(fn, tries, gap)
                except SourceError as e:
                    log.warning("source %s failed after %d tries mint=%s (%s)", key, tries, mint, e)
                    cache[key] = None
            if cache[key] is None: raise NoData
            return cache[key]
        pool = scan.get("pool")
        async def layer(n):
            if n in (1, 2):
                acc = await get("acc", lambda: self.src.rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}]))
                return (l1_mint if n == 1 else l2_freeze)(acc) + ("rpc",)
            if n == 3:
                if not coin or not coin["creator"]: raise NoData
                try: sup = await get("supply", lambda: onchain.supply_raw(self.src, mint))
                except NoData: return False, "no supply", "rpc"
                now_raw = await get("devbal", lambda: onchain.wallet_balance_raw(self.src, coin["creator"], mint))
                ok, v = l3_dev(now_raw, coin["creator_balance_at_migration"], sup, s["auditor.max_dev_pct"])
                cache["dev_pct"] = 100.0 * now_raw / sup if sup else None
                return ok, v, "rpc"
            if n == 4:
                sup = await get("supply", lambda: onchain.supply_raw(self.src, mint))
                pct = await get("top10", lambda: onchain.top10_pct(self.src, mint, pool, sup))
                return l4_top10(pct, s["auditor.max_top10_pct"]) + ("rpc",)
            if n == 5:
                rep = await get("report", lambda: self.src.rug_report(mint))
                pairs = await get("dex", lambda: self.src.dex_tokens([mint]))
                pair = onchain.best_pumpswap_pair(pairs, mint)
                liq_now = fields.dig(pair, "liquidity.usd") if pair else None
                return l5_liquidity(rep, pool, liq_now, scan.get("liquidityUsd"),
                                    s["auditor.min_lp_locked_pct"], s["auditor.liq_drop_max_pct"]) + ("rugcheck",)
            if n == 6:
                if not coin or not coin["migrated_at"]: raise NoData
                return l6_age(db.parse_iso(coin["migrated_at"]), time.time(), s["auditor.min_pool_age_min"]) + ("rpc",)
            if n == 7:
                g = await get("graph", lambda: self.src.rug_graph(mint))
                ok, v, _ = l7_bundles(g, s["auditor.max_bundled"]); return ok, v, "rugcheck"
            if n == 8:
                async def gpa():
                    a = await self.src.rpc("getProgramAccounts", [fields.TOKEN_PROGRAM, {"encoding": "jsonParsed",
                        "filters": [{"dataSize": 165}, {"memcmp": {"offset": 0, "bytes": mint}}]}])
                    b = await self.src.rpc("getProgramAccounts", [fields.TOKEN_2022, {"encoding": "jsonParsed",
                        "filters": [{"memcmp": {"offset": 0, "bytes": mint}}]}])
                    return (a.get("result") or []) + (b.get("result") or [])
                try: accs = await get("gpa", gpa)
                except NoData:
                    await self.layer8_down(); raise
                ok, v, holders = l8_delegations(accs, s["auditor.max_delegated_accounts"])
                cache["holders"] = holders
                self.c.execute("UPDATE coins SET holders=? WHERE mint=?", (holders, mint))
                return ok, v, "rpc"

        layers, reason = [], None
        for n in range(1, 9):
            t0 = time.monotonic()
            try: ok, value, source = await layer(n)
            except NoData:
                ok, value, source = False, f"no data: {NODATA[n]}", ("rugcheck" if n in (5, 7) else "rpc")
            ms = int((time.monotonic() - t0) * 1000)
            ts = db.now_iso()
            layers.append({"n": n, "name": LAYERS[n], "pass": ok, "value": value, "source": source})
            self.c.execute("INSERT INTO audits(mint,ts,layer,name,value_text,pass,source) VALUES(?,?,?,?,?,?,?)",
                           (mint, ts, n, LAYERS[n], value, int(ok), source))     # emitted the moment it finishes
            log.info("mint=%s layer=%d %s value=%r ms=%d %s", mint, n, LAYERS[n], value, ms, "ok" if ok else "RED")
            if not ok: reason = value; break
        passed = reason is None
        out = {"agent": "auditor", "mint": mint, "ts": db.now_iso(), "passed": passed, "layersChecked": len(layers),
               "layers": layers, "holders": cache.get("holders"), "reason": reason}
        log.info("verdict mint=%s passed=%s layers=%d reason=%s%s", mint, passed, len(layers), reason, " (adhoc)" if adhoc else "")
        if adhoc: return out
        if passed:
            await self.bus.put("analyst", {"scanner": scan, "auditor": out, "dev_pct": cache.get("dev_pct")})
        else:
            name = (coin["name"] if coin else None) or scan.get("name") or mint[:6]
            db.activity(self.c, "rejected", NAME, f"Rejected {name}", f"Auditor · {reason}", "Skipped", mint)
        return out

    async def layer8_down(self):
        night = db.night_bounds()[0]
        if self.layer8_alerted_night == night: return
        self.layer8_alerted_night = night
        ok = await telegram.send("layer 8 down, every coin is red until rpc_url changes")
        log.error("layer 8 down: getProgramAccounts refused; telegram sent=%s", ok)

async def adhoc_scan(conn, src, mint: str) -> dict | None:
    """Build the Scanner-shaped input for an ad-hoc audit from DexScreener + RugCheck (coin row if missing)."""
    try: pairs = await with_retry(lambda: src.dex_tokens([mint]), 3, 2)
    except SourceError: pairs = []
    pair = onchain.best_pumpswap_pair(pairs, mint)
    if not pair: return None
    d = fields.dex_read(pair)
    if not conn.execute("SELECT 1 FROM coins WHERE mint=?", (mint,)).fetchone():
        creator = None
        try: creator = fields.dig(await with_retry(lambda: src.rug_report(mint), 3, 2), fields.RUG["creator"])
        except SourceError: pass
        migrated = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((d["pair_created_ms"] or 0) / 1000)) if d["pair_created_ms"] else None
        # creator balance at migration is unknown for a coin the desk never saw: layer 3 stays red "no data: dev"
        conn.execute("INSERT INTO coins(mint,name,symbol,pool,migrated_at,creator) VALUES(?,?,?,?,?,?)",
                     (mint, d["name"], d["symbol"], d["pool"], migrated, creator))
    return {"agent": "scanner", "mint": mint, "name": d["name"], "symbol": d["symbol"], "pool": d["pool"],
            "liquidityUsd": d["liquidity_usd"], "top10Pct": None, "h1ChangePct": d["chg_h1"], "ageMin": None,
            "verdict": "adhoc", "reason": None, "ts": db.now_iso()}

def layer8_kills_tonight(conn) -> int:
    """For the morning Page: coins killed by 'no data: delegations' tonight."""
    return conn.execute("SELECT COUNT(DISTINCT mint) FROM audits WHERE layer=8 AND pass=0 AND value_text='no data: delegations' AND ts>=?",
                        (db.night_bounds()[0],)).fetchone()[0]

async def run(conn, bus, src):
    a = Auditor(conn, bus, src)
    log.info("start")
    async def hb():
        while True: db.heartbeat(conn, NAME); await asyncio.sleep(15)
    async def work():
        while True:
            scan = await bus.get("auditor")
            await wait_unpaused(conn, NAME)
            try: await a.audit(scan)
            except Exception as e: log.exception("audit error mint=%s: %s", scan.get("mint"), e)
    await asyncio.gather(hb(), work())
