"""Field mapping accepted in STEP 2 (see fields_mapping.md, raw samples in logs/raw/).
Paths only; no guessed keys. None = the source has no such key."""
from typing import Any

def dig(obj: Any, path: str, default=None):
    cur = obj
    for part in path.split("."):
        if cur is None: return default
        if isinstance(cur, list):
            try: cur = cur[int(part)]
            except (ValueError, IndexError): return default
        elif isinstance(cur, dict): cur = cur.get(part)
        else: return default
    return default if cur is None else cur

# PumpPortal subscribeMigration message
PUMPPORTAL = {"mint": "mint", "signature": "signature", "tx_type": "txType", "pool_type": "pool"}
# DexScreener /tokens/v1/solana/{mint} -> pick the pair with dexId == "pumpswap"
DEX = {
    "dex_id": "dexId", "pool": "pairAddress", "name": "baseToken.name", "symbol": "baseToken.symbol",
    "mint": "baseToken.address", "price_usd": "priceUsd", "liquidity_usd": "liquidity.usd",
    "mcap": "marketCap", "fdv": "fdv",
    "vol_m5": "volume.m5", "vol_h1": "volume.h1", "vol_h24": "volume.h24",
    "buys_m5": "txns.m5.buys", "sells_m5": "txns.m5.sells",
    "buys_h1": "txns.h1.buys", "sells_h1": "txns.h1.sells",
    "chg_m5": "priceChange.m5", "chg_h1": "priceChange.h1", "chg_h24": "priceChange.h24",
    "pair_created_ms": "pairCreatedAt",
}
DEX_POOL_DEX_ID = "pumpswap"
# RugCheck /v1/tokens/{mint}/report
RUG = {
    "creator": "creator", "creator_balance": "creatorBalance",
    "mint_authority": "mintAuthority", "freeze_authority": "freezeAuthority",
    "supply": "token.supply", "decimals": "token.decimals", "token_program": "tokenProgram",
    "total_holders": "totalHolders", "risks": "risks", "graph_insiders": "graphInsidersDetected",
    "insider_networks": "insiderNetworks", "top_holders": "topHolders",
}
RUG_MARKET_POOL_KEY = "pubkey"          # match DexScreener pairAddress; there is no markets[].pool
RUG_LP_LOCKED_PCT = "lp.lpLockedPct"    # LP burned %: None, RugCheck has no such key
# RugCheck /v1/tokens/{mint}/insiders/graph : list of networks
GRAPH_NODES, GRAPH_EDGES, GRAPH_HOLDS = "nodes", "links", "holdings"   # holds = holdings > 0
LAYER7_EMPTY_GRAPH = "red"              # strict: [] is red ("no data: bundles")
# RPC
RPC = {
    "mint_authority": "result.value.data.parsed.info.mintAuthority",
    "freeze_authority": "result.value.data.parsed.info.freezeAuthority",
    "supply": "result.value.amount",       # getTokenSupply
    "largest": "result.value",             # getTokenLargestAccounts: address, amount, uiAmount, decimals, uiAmountString
    "gpa_owner": "account.data.parsed.info.owner",
    "gpa_amount": "account.data.parsed.info.tokenAmount.amount",
    "gpa_delegate": "account.data.parsed.info.delegate",
    "gpa_delegated_amount": "account.data.parsed.info.delegatedAmount",
}
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

def dex_pool_pair(pairs: list[dict]) -> dict | None:
    for p in pairs or []:
        if p.get("dexId") == DEX_POOL_DEX_ID: return p
    return None

def dex_read(pair: dict) -> dict:
    out = {k: dig(pair, v) for k, v in DEX.items()}
    if out["price_usd"] is not None: out["price_usd"] = float(out["price_usd"])
    return out

def lp_locked_pct(report: dict, pool: str):
    for m in report.get("markets") or []:
        if m.get(RUG_MARKET_POOL_KEY) == pool: return dig(m, RUG_LP_LOCKED_PCT)
    return None
