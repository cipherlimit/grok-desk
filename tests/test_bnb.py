"""BNB Chain (2026-11-18): discovery, chain-aware schema, the 8 BNB Auditor layers (strict: missing data = red),
Scanner/Analyst/Executor/Risk/feed/Telegram on 0x coins. Recorded API bodies in tests/fixtures/bnb/; the BSC RPC
Transfer-log replay runs against an in-memory fake chain. Nothing here touches the network."""
import copy, json, sqlite3, time
from pathlib import Path
import httpx, pytest
import db, links, chains, bnb, bsc, sources
from conftest import run, FakeSrc, PIKA
from bus import Bus
from sources import SourceError
from agents import scanner, auditor, analyst, executor, risk, feed

FIX = Path(__file__).resolve().parent / "fixtures" / "bnb"
def fx(name): return json.loads((FIX / name).read_text(encoding="utf-8"))
S = lambda c: db.settings_all(c)
ago = lambda m: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - m * 60))

BEA = "0xc7b40b36aa459db01166556ff735947d1569fbca"          # 💯BEA, recorded 2026-11-18
BEA_CK = "0xC7b40B36aa459dB01166556FF735947D1569fbCa"       # checksummed as DexScreener returns it
BEA_POOL = "0xac47e75aa92433590291d2264bcc4f807755a4bf"
CREATOR = "0x8ef7d45e38d896bda8a4b7f043c5496623c7f640"

def gp_new(): return copy.deepcopy(fx("goplus_new_token.json")["result"][BEA])
def gp_clean():
    """The recorded GoPlus body with ownership renounced: what a clean four.meme graduate looks like."""
    g = gp_new(); g["owner_address"] = "0x0000000000000000000000000000000000000000"; g["owner_percent"] = "0"
    return g
def hp_cake(): return fx("honeypot_cake.json")
HP404 = {"code": 404, "error": "pair not found"}
def bea_pair(**kw):
    p = copy.deepcopy(fx("dexscreener_bsc_tokens.json")[0])
    p["pairCreatedAt"] = int((time.time() - kw.pop("age_min", 30) * 60) * 1000)
    p["liquidity"]["usd"] = kw.pop("liq", 50_000.0)
    p["priceChange"]["h1"] = kw.pop("h1", 10)
    p.update(kw); return p
ST = lambda: {**__import__("config").DEFAULTS}

class FakeLedgers:
    def __init__(self, top10=8.0, dev=0.5, lp=(100.0, 0.0)):
        self.v = {"top10": top10, "dev": dev, "lp": lp}; self.st = {}
    async def _get(self, k):
        v = self.v[k]
        if isinstance(v, Exception): raise v
        if v is None: raise bsc.NoData
        return v
    async def top10_pct(self, token, pools): return await self._get("top10")
    async def wallet_pct(self, token, wallet): return await self._get("dev")
    async def lp_burned_locked(self, pair): return await self._get("lp")

class BnbSrc(FakeSrc):
    async def gt_new_pools(self, page=1): return self._get("gt")
    async def goplus_token(self, a): return self._get("gp")
    async def honeypot(self, a, pair=None): return self._get("hp")

# ---------------------------------------------------------------- chains + links
def test_chain_of_and_links():
    assert chains.chain_of(BEA_CK) == "bnb" and chains.chain_of(PIKA) == "solana" and chains.norm(BEA_CK) == BEA
    assert links.coin_url(BEA) == links.coin_url(BEA, "bnb") and "chainId=56&r=savipww" in links.coin_url(BEA)   # exact shape: test_links
    assert links.coin_url(PIKA) == links.coin_url(PIKA, "solana")           # Solana unchanged
    assert chains.dexscreener_url(BEA) == f"https://dexscreener.com/bsc/{BEA}"

def test_chains_setting_default_off_and_toggle(conn):
    s = S(conn)
    assert s["chains.enabled"] == "solana" and chains.enabled(s) == {"solana"}     # deploying changes nothing
    assert chains.with_bnb(s, True) == "solana,bnb"
    assert chains.with_bnb({"chains.enabled": "solana,bnb"}, False) == "solana"
    assert chains.with_bnb({"chains.enabled": "bnb"}, False) == "solana"           # never empty
    import config
    assert config.validate("chains.enabled", "SOLANA,BNB") == "solana,bnb"
    with pytest.raises(config.SettingError): config.validate("chains.enabled", "doge")

# ---------------------------------------------------------------- schema migration
OLD = {  # the laptop's pre-BNB tables (chain column missing)
    "coins": "mint TEXT PRIMARY KEY, name TEXT, symbol TEXT, pool TEXT, migrated_at TEXT, creator TEXT, creator_balance_at_migration REAL, holders INTEGER",
    "scans": "id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, ts TEXT NOT NULL, liquidity_usd REAL, top10_pct REAL, h1_change_pct REAL, age_min REAL, verdict TEXT, reason TEXT",
    "audits": "id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, ts TEXT NOT NULL, layer INTEGER, name TEXT, value_text TEXT, pass INTEGER, source TEXT",
    "signals": "id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, ts TEXT NOT NULL, price REAL, size_usd REAL, stack_pct REAL, link TEXT, mode TEXT, status TEXT",
    "positions": "id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, opened_at TEXT NOT NULL, entry_price REAL NOT NULL, size_usd REAL NOT NULL, peak_price REAL NOT NULL, stop_price REAL NOT NULL, trail_active INTEGER NOT NULL DEFAULT 0, closed_at TEXT, exit_price REAL, exit_reason TEXT, pnl_usd REAL, fees_usd REAL, mode TEXT NOT NULL",
}

def test_migration_keeps_laptop_rows_and_is_idempotent(tmp_path):
    p = tmp_path / "old.db"
    o = sqlite3.connect(p)
    for t, cols in OLD.items(): o.execute(f"CREATE TABLE {t} ({cols})")
    o.execute("INSERT INTO coins(mint,name,migrated_at) VALUES(?,?,?)", (PIKA, "PIKA COIN", "2026-11-17T01:00:00Z"))
    o.execute("INSERT INTO scans(mint,ts,verdict,reason) VALUES(?,?,?,?)", (PIKA, "2026-11-17T01:11:00Z", "kill", "liq $4.5K"))
    o.execute("INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,mode) VALUES(?,?,1,16,1,0.94,'PAPER')", (PIKA, "t"))
    o.commit(); o.close()
    c = db.connect(p)
    assert db.schema_matches(c)
    assert tuple(c.execute("SELECT mint,name,chain FROM coins").fetchone()) == (PIKA, "PIKA COIN", "solana")
    assert tuple(c.execute("SELECT reason,chain FROM scans").fetchone()) == ("liq $4.5K", "solana")
    assert c.execute("SELECT chain FROM positions").fetchone()[0] == "solana"
    assert db.migrate_chain(c) == []                                    # 2nd run: nothing to do
    c.close(); assert db.schema_matches(db.connect(p))

# ---------------------------------------------------------------- discovery (GeckoTerminal)
def test_parse_new_pools_pancakeswap_only_lowercase_no_majors():
    body = fx("geckoterminal_new_pools.json")
    out = bnb.parse_new_pools(body)
    assert out and all(c["dex"].startswith("pancakeswap") for c in out)               # four.meme curves skipped
    assert all(c["mint"] == c["mint"].lower() and chains.is_evm(c["mint"]) for c in out)
    assert {c["mint"] for c in out} >= {"0x98d6400db9cc58628ce949e6a516b2099d7eed13", "0x92eb7e544f49242a9da17b19438e7ef5d92e7777"}
    b = copy.deepcopy(body); b["data"][0]["relationships"]["base_token"]["data"]["id"] = "bsc_0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
    assert "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c" not in {c["mint"] for c in bnb.parse_new_pools(b)}   # WBNB is not a new coin
    infinity = [c for c in out if c["dex"].startswith("pancakeswap-infinity")]
    assert infinity and infinity[0]["pool"] is None            # infinity pool ids are 32-byte hashes, not addresses

def _gt_fresh(minutes=3):
    b = fx("geckoterminal_new_pools.json")
    for p in b["data"]: p["attributes"]["pool_created_at"] = ago(minutes)
    return b

def test_scanner_bnb_discovery_off_by_default_then_on(conn):
    src = BnbSrc(gt=_gt_fresh())
    sc = scanner.Scanner(conn, Bus(), src)
    assert run(sc.bnb_discover()) == 0 and src.calls == []                           # chains.enabled = solana
    db.set_setting(conn, "chains.enabled", "solana,bnb", who="test")
    n = run(sc.bnb_discover())
    assert n == 3 and src.calls == ["gt", "gt"]                                      # pages 1-2
    rows = list(conn.execute("SELECT mint,chain,migrated_at,pool FROM coins"))
    assert all(r["chain"] == "bnb" and r["mint"].startswith("0x") for r in rows)
    assert run(sc.bnb_discover()) == 0                                               # never twice
    old = BnbSrc(gt=_gt_fresh(90)); conn.execute("DELETE FROM coins")
    assert run(scanner.Scanner(conn, Bus(), old).bnb_discover()) == 0                # older than max_wait: skipped

def test_solana_off_ignores_pumpportal(conn):
    db.set_setting(conn, "chains.enabled", "bnb", who="test")
    run(scanner.Scanner(conn, Bus(), FakeSrc()).on_migration({"mint": PIKA}))
    assert conn.execute("SELECT COUNT(*) FROM coins").fetchone()[0] == 0

def test_scanner_judges_bnb_coin_with_rpc_top10(conn, monkeypatch):
    conn.execute("INSERT INTO coins(mint,symbol,pool,migrated_at,chain) VALUES(?,?,?,?,?)", (BEA, "BEA", BEA_POOL, ago(30), "bnb"))
    monkeypatch.setattr(bsc, "ledgers_for", lambda src: FakeLedgers(top10=12.5))
    db.set_setting(conn, "scanner.min_liquidity_usd", 10_000)
    bus = Bus()
    run(scanner.Scanner(conn, bus, FakeSrc(dex=[bea_pair()])).cycle())
    out = bus.queues["auditor"].get_nowait()
    assert (out["verdict"], out["chain"], out["pool"], out["top10Pct"]) == ("pass", "bnb", BEA_POOL, 12.5)
    assert conn.execute("SELECT chain FROM scans").fetchone()[0] == "bnb"
    assert conn.execute("SELECT name FROM coins").fetchone()[0] == "💯Bear"
    # top 10 unknown (RPC can't replay it) = wait, like Solana
    conn.execute("DELETE FROM scans"); monkeypatch.setattr(bsc, "ledgers_for", lambda src: FakeLedgers(top10=None))
    run(scanner.Scanner(conn, Bus(), FakeSrc(dex=[bea_pair()])).cycle())
    assert conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0

# ---------------------------------------------------------------- the 8 layers, on recorded bodies
def test_l1_honeypot_strict():
    assert bnb.l1_honeypot(gp_new(), HP404) == (True, "sells ok (GoPlus)")            # honeypot.is lags: GoPlus decides
    assert bnb.l1_honeypot(gp_new(), hp_cake()) == (True, "sells ok (honeypot.is + GoPlus)")
    hp = hp_cake(); hp["honeypotResult"]["isHoneypot"] = True
    assert bnb.l1_honeypot(gp_new(), hp) == (False, "honeypot (honeypot.is)")
    g = gp_new(); g["cannot_sell_all"] = "1"
    assert bnb.l1_honeypot(g, HP404) == (False, "can't sell all")
    with pytest.raises(bnb.NoData): bnb.l1_honeypot(None, HP404)                      # neither simulated = red
    g = gp_new(); g["is_honeypot"] = ""
    with pytest.raises(bnb.NoData): bnb.l1_honeypot(g, HP404)

def test_l2_taxes():
    assert bnb.l2_taxes(gp_new(), HP404, 10) == (True, "buy 0% · sell 0%")
    g = gp_new(); g["sell_tax"] = "0.12"
    assert bnb.l2_taxes(g, HP404, 10) == (False, "sell tax 12%")
    g = gp_new(); g["slippage_modifiable"] = "1"
    assert bnb.l2_taxes(g, HP404, 10) == (False, "tax can be changed")
    g = gp_new(); g["buy_tax"] = g["sell_tax"] = ""                                   # GoPlus not done yet (seen live)
    with pytest.raises(bnb.NoData): bnb.l2_taxes(g, HP404, 10)
    assert bnb.l2_taxes(g, hp_cake(), 10) == (True, "buy 0% · sell 0%")               # honeypot.is simulation fills in

def test_l3_contract_and_l4_owner():
    assert bnb.l3_contract(gp_new()) == (True, "verified · no proxy · not mintable")
    cake = fx("goplus_cake.json")["result"]["0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82"]
    assert bnb.l3_contract(cake) == (False, "mintable")
    g = gp_new(); g["is_proxy"] = "1"; assert bnb.l3_contract(g) == (False, "proxy (upgradeable)")
    g = gp_new(); del g["is_open_source"]
    with pytest.raises(bnb.NoData): bnb.l3_contract(g)
    assert bnb.l4_owner(gp_new(), True) == (False, "owner not renounced")              # recorded: owner = creator
    assert bnb.l4_owner(gp_clean(), True) == (True, "renounced · no blacklist/pause")
    g = gp_clean(); g["transfer_pausable"] = "1"; assert bnb.l4_owner(g, True) == (False, "trading can be paused")
    g = gp_clean(); g["is_blacklisted"] = "1"; assert bnb.l4_owner(g, True) == (False, "blacklist")
    g = gp_clean(); del g["owner_address"]                                             # four.meme style: no owner()
    assert bnb.l4_owner(g, True) == (True, "no owner · no blacklist/pause")
    del g["owner_percent"]
    with pytest.raises(bnb.NoData): bnb.l4_owner(g, True)                               # truly unknown = red
    assert bnb.l4_owner(gp_new(), False)[0] is True                                    # setting off: owner kept is ok

def test_l5_to_l8():
    assert bnb.l5_top10(12.0, 25) == (True, "12.0%") and bnb.l5_top10(30, 25) == (False, "top 10 hold 30%")
    with pytest.raises(bnb.NoData): bnb.l5_top10(None, 25)
    assert bnb.l6_liquidity((100.0, 0.0), 50_000, 50_000, 99, 50) == (True, "100% burned")
    assert bnb.l6_liquidity((2.0, 97.5), 50_000, 50_000, 99, 50) == (True, "100% locked")
    assert bnb.l6_liquidity((0.0, 0.0), 50_000, 50_000, 99, 50) == (False, "liquidity unlocked")
    assert bnb.l6_liquidity((100.0, 0.0), 20_000, 50_000, 99, 50) == (False, "liquidity pulled")
    with pytest.raises(bnb.NoData): bnb.l6_liquidity(None, 50_000, 50_000, 99, 50)    # V3/Infinity: no LP token
    assert bnb.l7_age(0, 600, 10) == (True, "10m") and bnb.l7_age(0, 300, 10) == (False, "pool too young")
    assert bnb.l8_dev(0.5, 5) == (True, "0.5%") and bnb.l8_dev(7, 5) == (False, "dev holds 7%")
    with pytest.raises(bnb.NoData): bnb.l8_dev(None, 5)
    assert bnb.dev_wallets(gp_new()) == [CREATOR] and bnb.dev_wallets(gp_clean()) == [CREATOR]

def test_run_layers_stops_at_first_red():
    s = ST(); now = time.time()
    kw = dict(top10=8.0, dev_pct=0.1, lp=(100.0, 0.0), created_ts=now - 1800, liq_now=50_000, liq_scan=50_000, s=s, now=now)
    layers, reason = bnb.run_layers(gp_clean(), HP404, **kw)
    assert reason is None and [l["name"] for l in layers] == list(bnb.LAYERS.values())
    layers, reason = bnb.run_layers(gp_new(), HP404, **kw)
    assert reason == "owner not renounced" and len(layers) == 4
    layers, reason = bnb.run_layers(None, HP404, **kw)
    assert reason == "no data: sell simulation" and len(layers) == 1

# ---------------------------------------------------------------- BSC Transfer-log replay
def _log(frm, to, v, block=1):
    t = lambda a: "0x" + "0" * 24 + a[2:]
    return {"topics": [bsc.TRANSFER, t(frm), t(to)], "data": hex(v), "blockNumber": hex(block)}
Z, DEAD = bsc.ZERO, "0x000000000000000000000000000000000000dead"
A, B, C, POOL = ("0x" + c * 40 for c in "abc1")

def test_replay_top10_and_lp():
    bal = {}
    assert bsc.apply_logs(bal, [_log(Z, A, 1000), _log(A, POOL, 800), _log(POOL, B, 100), _log(POOL, C, 50), _log(B, Z, 10)], "0xtoken")
    assert bal[POOL] == 650 and bal[B] == 90 and bsc.consistent(bal, 990)            # 10 burned out of 1000
    assert bsc.top10_from_balances(bal, 990, {POOL} | bsc.NOT_HOLDERS) == pytest.approx(100 * 340 / 990, 0.01)
    lp = {}                                                                           # a V2 pair's own LP token
    bsc.apply_logs(lp, [_log(Z, Z, 1000), _log(Z, A, 99_000), _log(A, DEAD, 99_000)], POOL)
    assert bsc.consistent(lp, 100_000, True) and bsc.lp_share(lp, 100_000) == (100.0, 0.0)
    lp2 = {}; bsc.apply_logs(lp2, [_log(Z, Z, 1000), _log(Z, A, 99_000), _log(A, POOL, 99_000), _log(POOL, Z, 99_000)], POOL)
    assert lp2.get(Z) == 1000 and bsc.consistent(lp2, 1000, True)                     # a removal is not a "burn"

class ChainRpc:
    """Fake BSC JSON-RPC: logs per address at block numbers, totalSupply per address."""
    def __init__(self, head, logs, supply): self.head, self.logs, self.supply, self.calls = head, logs, supply, []
    async def bsc_rpc(self, method, params):
        self.calls.append(method)
        if method == "eth_blockNumber": return {"result": hex(self.head)}
        if method == "eth_call": return {"result": hex(self.supply.get(params[0]["to"], 0)) if params[0]["to"] in self.supply else "0x"}
        q = params[0]; lo, hi = int(q["fromBlock"], 16), int(q["toBlock"], 16)
        return {"result": [l for l in self.logs.get(q["address"], []) if lo <= int(l["blockNumber"], 16) <= hi]}

def test_ledgers_scan_back_to_mint_then_incremental():
    head = 200_000
    logs = {A: []}
    tok = "0x" + "e" * 40
    logs[tok] = [_log(Z, B, 1000, head - 120_000), _log(B, POOL, 900, head - 500), _log(POOL, C, 100, head - 10)]
    rpc = ChainRpc(head, logs, {tok: 1000}); L = bsc.Ledgers(rpc)
    assert run(L.top10_pct(tok, {POOL})) == 20.0                                       # B 100 + C 100, pool dropped
    assert rpc.calls.count("eth_getLogs") == 3                                         # 3 chunks back to the mint
    rpc.head += 50; rpc.logs[tok].append(_log(C, A, 50, head + 20)); rpc.calls.clear()
    assert run(L.wallet_pct(tok, A)) == 5.0 and rpc.calls.count("eth_getLogs") == 1    # only the new blocks
    far = "0x" + "f" * 40
    rpc2 = ChainRpc(10_000_000, {far: [_log(A, B, 5, 9_999_000)]}, {far: 10})          # mint older than the budget
    with pytest.raises(bsc.NoData): run(bsc.Ledgers(rpc2).top10_pct(far, set()))
    with pytest.raises(bsc.NoData): run(bsc.Ledgers(rpc).lp_burned_locked("0x" + "9" * 64))   # infinity pool id

# ---------------------------------------------------------------- sources: DexScreener per chain
def test_dex_tokens_routes_by_chain():
    seen = []
    def handler(req):
        seen.append(str(req.url)); return httpx.Response(200, json=[{"baseToken": {"address": str(req.url).rsplit("/", 1)[1]}}])
    src = sources.Sources(lambda: "https://x", lambda: 250)
    src.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    out = run(src.dex_tokens([PIKA, BEA]))
    assert seen == [f"https://api.dexscreener.com/tokens/v1/solana/{PIKA}", f"https://api.dexscreener.com/tokens/v1/bsc/{BEA}"]
    assert len(out) == 2

def test_goplus_and_honeypot_sources():
    def handler(req):
        u = str(req.url)
        if "gopluslabs" in u: return httpx.Response(200, json=fx("goplus_new_token.json"))
        return httpx.Response(404, json=HP404)
    src = sources.Sources(lambda: "https://x"); src.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert run(src.goplus_token(BEA_CK))["token_symbol"] == "💯BEA"
    assert run(src.honeypot(BEA)) == HP404                                             # 404 is an answer, not an error
    with pytest.raises(SourceError): run(src.goplus_token("0x" + "1" * 40))

# ---------------------------------------------------------------- Auditor end to end
def _bnb_coin(conn, mins=30):
    conn.execute("INSERT INTO coins(mint,name,symbol,pool,migrated_at,chain) VALUES(?,?,?,?,?,?)", (BEA, "💯Bear", "BEA", BEA_POOL, ago(mins), "bnb"))
def _bnb_scan():
    return {"agent": "scanner", "mint": BEA, "name": "💯Bear", "symbol": "BEA", "pool": BEA_POOL, "liquidityUsd": 50_000.0,
            "top10Pct": 8.0, "h1ChangePct": 10, "ageMin": 30, "verdict": "pass", "reason": None, "ts": db.now_iso(), "chain": "bnb"}

def test_audit_bnb_clean_passes_8_of_8(conn, monkeypatch, sleeps):
    _bnb_coin(conn); bus = Bus()
    monkeypatch.setattr(bsc, "ledgers_for", lambda src: FakeLedgers())
    out = run(auditor.Auditor(conn, bus, BnbSrc(gp=gp_clean(), hp=HP404, dex=[bea_pair()])).audit(_bnb_scan()))
    assert out["passed"] and out["layersChecked"] == 8 and out["chain"] == "bnb"
    rows = list(conn.execute("SELECT layer,name,pass,source,chain FROM audits ORDER BY layer"))
    assert [r["name"] for r in rows] == list(bnb.LAYERS.values()) and all(r["chain"] == "bnb" and r["pass"] for r in rows)
    msg = bus.queues["analyst"].get_nowait()
    assert msg["dev_pct"] == 0.5 and msg["auditor"]["chain"] == "bnb"

def test_audit_bnb_strict_no_data_is_red(conn, monkeypatch, sleeps):
    _bnb_coin(conn)
    monkeypatch.setattr(bsc, "ledgers_for", lambda src: FakeLedgers(lp=None))          # V3 pool / LP unreadable
    out = run(auditor.Auditor(conn, Bus(), BnbSrc(gp=gp_clean(), hp=HP404, dex=[bea_pair()])).audit(_bnb_scan()))
    assert not out["passed"] and out["reason"] == "no data: liquidity" and out["layersChecked"] == 6
    assert conn.execute("SELECT title,subtitle FROM activity").fetchone()[1] == "Auditor · no data: liquidity"
    conn.execute("DELETE FROM audits")
    out = run(auditor.Auditor(conn, Bus(), BnbSrc(gp=SourceError("down"), hp=HP404, dex=[bea_pair()])).audit(_bnb_scan()))
    assert out["reason"] == "no data: sell simulation"                                 # GoPlus down + honeypot.is 404

def test_adhoc_buy_command_lists_bnb_layers(conn, monkeypatch, sleeps):
    import telegram
    monkeypatch.setattr(bsc, "ledgers_for", lambda src: FakeLedgers())
    d = telegram.Desk(conn, Bus(), BnbSrc(gp=gp_new(), hp=HP404, dex=[bea_pair()]))
    txt, btn = run(d.handle(f"buy {BEA_CK}"))
    assert "4 Owner powers: ✗ owner not renounced" in txt and "1 Honeypot: ✓" in txt and "not sized" in txt
    assert btn[0][0]["url"] == links.coin_url(BEA) and "chainId=56" in btn[0][0]["url"]
    assert conn.execute("SELECT chain FROM coins WHERE mint=?", (BEA,)).fetchone()[0] == "bnb"

# ---------------------------------------------------------------- Analyst / Executor / Risk
def test_analyst_bnb_state_and_no_bundles():
    aud = {"chain": "bnb", "layers": [{"n": 5, "name": "Top 10 holders", "value": "8.0%"}, {"n": 2, "name": "Taxes", "value": "buy 0% · sell 0%"},
                                      {"n": 6, "name": "Liquidity", "value": "100% burned"}, {"n": 7, "name": "Pool age", "value": "30m"}]}
    assert analyst.bundled_from(aud) == 0                                              # "30m" is not 30 bundled wallets
    st = analyst.build_state({"name": "Bear"}, aud, {"liquidity_usd": 50000}, 0.5, 30, 3000)
    assert "on BNB Chain" in st and "Top 10 hold 8.0%" in st and "LP 100% burned" in st and "Solana" not in st

def test_analyst_rules_on_bnb_coin(conn):
    _bnb_coin(conn); bus = Bus()
    p = bea_pair(); p["volume"]["h1"] = 50_000; p["txns"]["h1"]["buys"] = 400; p["priceChange"]["m5"] = 1
    aud = {"chain": "bnb", "layersChecked": 8, "layers": [{"n": 5, "name": "Top 10 holders", "value": "8.0%"}]}
    out = run(analyst.Analyst(conn, bus, FakeSrc(dex=[p])).analyze({"scanner": _bnb_scan(), "auditor": aud, "dev_pct": 0.5}))
    assert out["passed"] and bus.queues["executor"].get_nowait()["analyst"]["mint"] == BEA

def test_executor_bnb_link_and_chain_columns(conn, pushes):
    _bnb_coin(conn); db.add_balance(conn, 200)
    msg = {"analyst": {"mint": BEA, "score": 80, "passed": True}, "scanner": _bnb_scan(), "price_at_analyst": float(bea_pair()["priceUsd"]), "name": "💯Bear"}
    out = run(executor.Executor(conn, Bus(), FakeSrc(dex=[bea_pair()])).handle(msg))
    assert out["status"] == "bought" and out["link"] == links.coin_url(BEA, "bnb")
    assert pushes[0][1][0][0]["url"] == out["link"]
    assert conn.execute("SELECT chain FROM signals").fetchone()[0] == "bnb"
    assert conn.execute("SELECT chain FROM positions").fetchone()[0] == "bnb"
    db.write_price_batch(conn, "risk", [bea_pair()])
    assert conn.execute("SELECT mint FROM prices").fetchone()[0] == BEA               # checksummed -> stored lowercase

def test_risk_prices_bnb_position_and_sell_link(conn, pushes):
    _bnb_coin(conn); db.add_balance(conn, 200)
    price = float(bea_pair()["priceUsd"])
    conn.execute("INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,mode,chain) VALUES(?,?,?,?,?,?,?,?)",
                 (BEA, ago(5), price * 1.2, 16, price * 1.2, price * 1.2 * 0.94, "PAPER", "bnb"))
    r = risk.Risk(conn, Bus(), FakeSrc(dex=[bea_pair()]))
    assert run(r.poll()) == 1
    assert conn.execute("SELECT exit_reason FROM positions").fetchone()[0] == "stop"
    assert pushes[0][1][0][0]["url"] == links.coin_url(BEA) and "chainId=56" in pushes[0][1][0][0]["url"]

# ---------------------------------------------------------------- Telegram + feed
def test_telegram_resolve_bnb_and_fomo_link(conn):
    import telegram
    d = telegram.Desk(conn, Bus(), None)
    assert d.resolve(BEA_CK) == ("ok", BEA)
    assert d.resolve(links.coin_url(BEA_CK, "bnb")) == ("ok", BEA)                    # a pasted FOMO link
    assert d.resolve(links.coin_url(PIKA)) == ("ok", PIKA)
    _bnb_coin(conn)
    conn.execute("INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,h1_change_pct,age_min,verdict,reason,chain) VALUES(?,?,?,?,?,?,?,?,?)",
                 (BEA, ago(1), 8000, None, 5, 12, "kill", "liq $8.0K", "bnb"))
    txt, _ = run(d.handle(f"why {BEA_CK}"))
    assert txt.startswith("💯Bear: Scanner · liq $8.0K") and links.coin_url(BEA) in txt

def test_telegram_bnb_on_off(conn):
    import telegram
    d = telegram.Desk(conn, Bus(), None)
    assert run(d.handle("bnb"))[0].startswith("BNB Chain off. The Scanner covers Solana.")
    r = run(d.handle("/bnb on"))[0]
    assert r.startswith("BNB Chain on. The Scanner now covers Solana + BNB Chain") and "PAPER" in r
    assert S(conn)["chains.enabled"] == "solana,bnb"
    assert conn.execute("SELECT who FROM change_log WHERE key='chains.enabled' ORDER BY rowid DESC").fetchone()[0] == "telegram"
    run(d.handle("bnb off")); assert S(conn)["chains.enabled"] == "solana"
    assert "bnb on" in run(d.handle("help"))[0]

def test_feed_bnb_lines(conn, tmp_path):
    for a in feed.HB_AGENTS: db.heartbeat(conn, a)
    f = feed.Feed(conn, tmp_path / "fs.json"); f.start("PAPER"); f.st["outbox"] = []
    _bnb_coin(conn)
    conn.execute("INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,h1_change_pct,age_min,verdict,reason,chain) VALUES(?,?,?,?,?,?,?,?,?)",
                 (BEA, ago(0), 50_000, 8, 10, 30, "pass", None, "bnb"))
    f.collect(True, S(conn))
    t = [o["t"] for o in f.st["outbox"]]
    assert any(x.startswith("🆕 New coin graduated · 💯Bear") and x.endswith("BNB Chain (PancakeSwap)") for x in t)
    alert = [o for o in f.st["outbox"] if o["a"]][0]
    assert alert["t"].startswith("🟢 GOOD LIQUIDITY · 💯Bear · BNB Chain") and f"https://dexscreener.com/bsc/{BEA}" in alert["t"]
    assert alert["u"] == links.coin_url(BEA)
