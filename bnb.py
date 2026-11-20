"""BNB Chain (BSC, chainId 56) pieces of the desk (user 2026-11-18). Pure functions on raw API bodies, tested on
recorded samples in tests/fixtures/bnb/. Sources, all free and key-less:
  GeckoTerminal /networks/bsc/new_pools  -> new PancakeSwap pools = the BNB "graduations" (four.meme -> PancakeSwap,
                                            flap.sh and plain launches); four.meme bonding-curve pools are skipped
                                            the same way pump.fun curves are on Solana
  DexScreener /tokens/v1/bsc             -> liquidity, price, volume, txns (Scanner, Analyst, Executor, Risk)
  GoPlus token_security/56               -> contract flags, taxes, owner, creator address
  BSC JSON-RPC (publicnode, bsc.py)      -> live top 10, dev wallet and LP burn/lock from the Transfer logs
                                            (GoPlus holder lists lag new tokens by hours, so they are not used)
  honeypot.is v2 IsHoneypot              -> buy/sell simulation (often "pair not found" on brand-new pools: GoPlus
                                            simulation then decides alone; neither = red)
Strict like layer 7 on Solana: a missing key on a safety layer is red "no data: ...", never a pass."""
import time
import chains

LAYERS = {1: "Honeypot", 2: "Taxes", 3: "Contract", 4: "Owner powers",
          5: "Top 10 holders", 6: "Liquidity", 7: "Pool age", 8: "Dev wallet"}
NODATA = {1: "sell simulation", 2: "tax", 3: "contract", 4: "owner", 5: "top 10", 6: "liquidity",
          7: "pool age", 8: "dev"}
SOURCE = {1: "goplus+honeypot.is", 2: "goplus+honeypot.is", 3: "goplus", 4: "goplus", 5: "bsc rpc",
          6: "bsc rpc", 7: "desk", 8: "goplus+bsc rpc"}
ZERO = "0x0000000000000000000000000000000000000000"
DEAD = {ZERO, "0x000000000000000000000000000000000000dead", "0xdead000000000000000042069420694206942069"}
POOL_DEX_PREFIX = "pancakeswap"          # GeckoTerminal dex ids: pancakeswap_v2, pancakeswap-v3-bsc, pancakeswap-infinity-*
# base tokens that are never a "new coin" even when a new pool opens for them
MAJORS = {
    "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
    "0x55d398326f99059ff775485246999027b3197955",  # USDT
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # USDC
    "0xe9e7cea3dedca5984780bafc599bd69add087d56",  # BUSD
    "0xc5f0f7b66764f6ec8c8dff7ba683102295e16409",  # FDUSD
    "0x8d0d000ee44948fc98c9b98a4fa4921476f08b0d",  # USD1
    "0x2170ed0880ac9a755fd29b2688956bd959f933f8",  # ETH
    "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",  # BTCB
    "0x0e09fabb73bd3ade0a17ecc321fd13a19e81ce82",  # CAKE
}

class NoData(Exception): pass

# ---------------------------------------------------------------- discovery
def parse_new_pools(body: dict) -> list[dict]:
    """GeckoTerminal new_pools -> [{mint, pool, symbol, dex, created_at, reserve_usd}], PancakeSwap pools only,
    newest first, one entry per token (its first pool in the list)."""
    out, seen = [], set()
    for p in (body or {}).get("data") or []:
        try:
            a, rel = p["attributes"], p["relationships"]
            dex = rel["dex"]["data"]["id"]
            base = rel["base_token"]["data"]["id"]
        except (KeyError, TypeError):
            continue
        if not str(dex).startswith(POOL_DEX_PREFIX): continue
        mint = chains.norm(base.split("_", 1)[1] if "_" in base else base)
        pool = chains.norm(a.get("address") or "")
        if not chains.is_evm(mint) or mint in MAJORS or mint in seen: continue
        seen.add(mint)
        try: reserve = float(a.get("reserve_in_usd") or 0)
        except ValueError: reserve = 0.0
        out.append({"mint": mint, "pool": pool if chains.is_evm(pool) else None,
                    "symbol": (a.get("name") or "").split(" / ")[0] or None, "dex": dex,
                    "created_at": a.get("pool_created_at"), "reserve_usd": reserve})
    return out

# ---------------------------------------------------------------- GoPlus helpers
def flag(gp: dict, key: str) -> str | None:
    """'1' | '0' | None (absent or empty = unknown)."""
    v = (gp or {}).get(key)
    if v is None or v == "": return None
    return str(v)

def frac_pct(v) -> float | None:
    """GoPlus fractions ('0.05' = 5%) -> percent."""
    if v is None or v == "": return None
    try: return 100.0 * float(v)
    except (TypeError, ValueError): return None

def pool_addresses(gp: dict, pool: str | None) -> set[str]:
    s = {chains.norm(d.get("pair")) for d in (gp or {}).get("dex") or [] if isinstance(d, dict) and d.get("pair")}
    if pool: s.add(chains.norm(pool))
    return {x for x in s if x}

def hp_ok(hp) -> bool:
    """honeypot.is answered with a simulation (not a 404)."""
    return isinstance(hp, dict) and "simulationSuccess" in hp

# ---------------------------------------------------------------- the 8 BNB layers
def l1_honeypot(gp: dict | None, hp: dict | None) -> tuple[bool, str]:
    if hp_ok(hp):
        if (hp.get("honeypotResult") or {}).get("isHoneypot"): return False, "honeypot (honeypot.is)"
        if hp.get("simulationSuccess") is False: return False, "sell simulation failed"
    if gp:
        if flag(gp, "is_honeypot") == "1": return False, "honeypot (GoPlus)"
        if flag(gp, "cannot_sell_all") == "1": return False, "can't sell all"
        if flag(gp, "cannot_buy") == "1": return False, "can't buy"
        if flag(gp, "honeypot_with_same_creator") == "1": return False, "creator made honeypots"
        if flag(gp, "is_airdrop_scam") == "1": return False, "airdrop scam"
    hp_pass = hp_ok(hp) and hp.get("simulationSuccess") is True
    gp_pass = bool(gp) and flag(gp, "is_honeypot") == "0"
    if not hp_pass and not gp_pass: raise NoData
    return True, "sells ok (" + " + ".join(x for x, ok in (("honeypot.is", hp_pass), ("GoPlus", gp_pass)) if ok) + ")"

def taxes(gp: dict | None, hp: dict | None) -> tuple[float | None, float | None, float | None]:
    """(buy, sell, transfer) in %. honeypot.is simulation first, GoPlus otherwise."""
    if hp_ok(hp) and hp.get("simulationSuccess"):
        sim = hp.get("simulationResult") or {}
        b, s, t = sim.get("buyTax"), sim.get("sellTax"), sim.get("transferTax")
        if b is not None and s is not None: return float(b), float(s), (float(t) if t is not None else None)
    if not gp: return None, None, None
    return frac_pct(gp.get("buy_tax")), frac_pct(gp.get("sell_tax")), frac_pct(gp.get("transfer_tax"))

def l2_taxes(gp: dict | None, hp: dict | None, max_tax: float) -> tuple[bool, str]:
    b, s, t = taxes(gp, hp)
    if b is None or s is None: raise NoData
    if flag(gp, "slippage_modifiable") == "1" or flag(gp, "personal_slippage_modifiable") == "1":
        return False, "tax can be changed"
    if b >= max_tax: return False, f"buy tax {b:.0f}%"
    if s >= max_tax: return False, f"sell tax {s:.0f}%"
    if t is not None and t >= max_tax: return False, f"transfer tax {t:.0f}%"
    return True, f"buy {b:.0f}% · sell {s:.0f}%"

def l3_contract(gp: dict | None) -> tuple[bool, str]:
    if not gp: raise NoData
    need = ("is_open_source", "is_proxy", "is_mintable")
    if any(flag(gp, k) is None for k in need): raise NoData
    if flag(gp, "is_open_source") != "1": return False, "source not verified"
    if flag(gp, "is_proxy") == "1": return False, "proxy (upgradeable)"
    if flag(gp, "is_mintable") == "1": return False, "mintable"
    for k, why in (("selfdestruct", "self-destruct"), ("hidden_owner", "hidden owner"),
                   ("can_take_back_ownership", "can take back ownership"), ("external_call", "external call")):
        if flag(gp, k) == "1": return False, why
    return True, "verified · no proxy · not mintable"

def owner_renounced(gp: dict) -> bool | None:
    """True = renounced or no owner, False = someone owns it, None = unknown.
    GoPlus: owner_address '' = no ownership; owner_address absent while it still reports owner_percent and
    hidden_owner/can_take_back_ownership '0' = it read the contract and found no owner (four.meme style tokens);
    absent with none of those = unknown."""
    gp = gp or {}
    if "owner_address" not in gp:
        if (gp.get("owner_percent") not in (None, "") and flag(gp, "hidden_owner") == "0"
                and flag(gp, "can_take_back_ownership") == "0" and (frac_pct(gp.get("owner_percent")) or 0) == 0):
            return True
        return None
    o = gp.get("owner_address")
    return o == "" or chains.norm(o) in DEAD

def l4_owner(gp: dict | None, require_renounced: bool) -> tuple[bool, str]:
    if not gp: raise NoData
    need = ("owner_change_balance", "transfer_pausable", "is_blacklisted")
    if any(flag(gp, k) is None for k in need): raise NoData
    for k, why in (("owner_change_balance", "owner can change balances"), ("transfer_pausable", "trading can be paused"),
                   ("is_blacklisted", "blacklist"), ("trading_cooldown", "trading cooldown"),
                   ("anti_whale_modifiable", "anti-whale can be changed")):
        if flag(gp, k) == "1": return False, why
    ren = owner_renounced(gp)
    if require_renounced:
        if ren is None: raise NoData
        if not ren: return False, "owner not renounced"
    word = "renounced" if ren and "owner_address" in gp else ("no owner" if ren else "owner kept")
    return True, word + " · no blacklist/pause"

def l5_top10(top10: float | None, max_pct: float) -> tuple[bool, str]:
    if top10 is None: raise NoData
    return (True, f"{top10:.1f}%") if top10 < max_pct else (False, f"top 10 hold {top10:.0f}%")

def l6_liquidity(lp: tuple[float, float] | None, liq_now, liq_scan, lp_min: float, drop_max: float) -> tuple[bool, str]:
    """lp = (% LP burned, % LP in a known locker) of the V2 pair (bsc.Ledgers.lp_burned_locked)."""
    if lp is None: raise NoData
    burned, locked = lp
    if burned + locked < lp_min: return False, "liquidity unlocked"
    if liq_now is None or not liq_scan: raise NoData
    if liq_now < liq_scan * (1 - drop_max / 100): return False, "liquidity pulled"
    word = "burned" if burned >= locked else "locked"
    return True, f"{min(burned + locked, 100):.0f}% {word}"

def l7_age(created_ts: float, now: float, min_age: float) -> tuple[bool, str]:
    mins = (now - created_ts) / 60
    return (True, f"{int(mins)}m") if mins >= min_age else (False, "pool too young")

def l8_dev(dev_pct: float | None, max_pct: float) -> tuple[bool, str]:
    """Creator wallet (and the owner, if it is a different live wallet) share of supply now, from the Transfer logs."""
    if dev_pct is None: raise NoData
    if dev_pct >= max_pct: return False, f"dev holds {dev_pct:.0f}%"
    return True, f"{dev_pct:.1f}%"

def dev_wallets(gp: dict | None) -> list[str]:
    """Creator (GoPlus creator_address) plus the owner when it is a different, not-renounced wallet."""
    out = []
    c = chains.norm((gp or {}).get("creator_address") or "")
    if chains.is_evm(c): out.append(c)
    o = chains.norm((gp or {}).get("owner_address") or "")
    if chains.is_evm(o) and o not in DEAD and o not in out: out.append(o)
    return out

async def gather(src, ledgers, mint: str, pool: str | None, pool_is_v2: bool, pools: set[str], log=None) -> dict:
    """Every BNB input for the 8 layers, one try each (callers wrap retries around the flaky ones).
    Missing pieces stay None -> their layer is red "no data"."""
    import bsc
    from sources import SourceError
    out = {"gp": None, "hp": None, "top10": None, "dev_pct": None, "lp": None}
    def warn(what, e):
        if log: log.warning("bnb %s mint=%s (%s)", what, mint, type(e).__name__ if not isinstance(e, SourceError) else e)
    try: out["gp"] = await src.goplus_token(mint)
    except SourceError as e: warn("goplus", e)
    try: out["hp"] = await src.honeypot(mint, pool)
    except SourceError as e: warn("honeypot.is", e)
    excl = pools | pool_addresses(out["gp"], pool)
    try: out["top10"] = await ledgers.top10_pct(mint, excl)
    except (SourceError, bsc.NoData) as e: warn("top10", e)
    wallets = dev_wallets(out["gp"])
    if wallets:
        try: out["dev_pct"] = sum([await ledgers.wallet_pct(mint, w) for w in wallets])
        except (SourceError, bsc.NoData) as e: warn("dev", e)
    if pool and pool_is_v2:
        try: out["lp"] = await ledgers.lp_burned_locked(pool)
        except (SourceError, bsc.NoData) as e: warn("lp", e)
    return out

def run_layers(gp: dict | None, hp: dict | None, *, top10, dev_pct, lp, created_ts, liq_now, liq_scan, s: dict, now=None):
    """All 8 layers in order, stop at the first red. Returns (layers, reason). Used by the Auditor
    (after bnb.gather) and by tools/bnb_dryrun.py."""
    now = now or time.time()
    def age():
        if created_ts is None: raise NoData
        return l7_age(created_ts, now, s["auditor.min_pool_age_min"])
    fns = {
        1: lambda: l1_honeypot(gp, hp),
        2: lambda: l2_taxes(gp, hp, s["auditor.bnb_max_tax_pct"]),
        3: lambda: l3_contract(gp),
        4: lambda: l4_owner(gp, s["auditor.bnb_require_renounced"]),
        5: lambda: l5_top10(top10, s["auditor.max_top10_pct"]),
        6: lambda: l6_liquidity(lp, liq_now, liq_scan, s["auditor.min_lp_locked_pct"], s["auditor.liq_drop_max_pct"]),
        7: age,
        8: lambda: l8_dev(dev_pct, s["auditor.max_dev_pct"]),
    }
    layers, reason = [], None
    for n in range(1, 9):
        try: ok, value = fns[n]()
        except NoData: ok, value = False, f"no data: {NODATA[n]}"
        layers.append({"n": n, "name": LAYERS[n], "pass": ok, "value": value, "source": SOURCE[n]})
        if not ok: reason = value; break
    return layers, reason
