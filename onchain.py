"""Shared on-chain reads (Scanner kill #2 and Auditor layer 4 use the same top-10 math)."""
from solders.pubkey import Pubkey
from config import PUMP_PROGRAM
import fields

def bonding_curve(mint: str) -> str:
    pda, _ = Pubkey.find_program_address([b"bonding-curve", bytes(Pubkey.from_string(mint))],
                                         Pubkey.from_string(PUMP_PROGRAM))
    return str(pda)

def top10_from(largest: list[dict], owners: dict[str, str | None], supply_raw: int,
               pool: str | None, curve: str) -> float:
    """largest = getTokenLargestAccounts value list; owners = token account -> owner wallet.
    Drop accounts owned by the pool or the bonding curve (or that are them), sum the top 10."""
    excluded = {x for x in (pool, curve) if x}
    kept = [a for a in largest if a["address"] not in excluded and owners.get(a["address"]) not in excluded]
    kept.sort(key=lambda a: int(a["amount"]), reverse=True)
    total = sum(int(a["amount"]) for a in kept[:10])
    return round(100.0 * total / supply_raw, 2)

async def supply_raw(src, mint) -> int:
    b = await src.rpc("getTokenSupply", [mint])
    return int(fields.dig(b, fields.RPC["supply"]) or 0)

async def top10_pct(src, mint: str, pool: str | None, supply: int) -> float:
    b = await src.rpc("getTokenLargestAccounts", [mint])
    largest = fields.dig(b, fields.RPC["largest"]) or []
    addrs = [a["address"] for a in largest]
    owners = {}
    if addrs:
        m = await src.rpc("getMultipleAccounts", [addrs, {"encoding": "jsonParsed"}])
        for addr, acc in zip(addrs, fields.dig(m, "result.value") or []):
            owners[addr] = fields.dig(acc, "data.parsed.info.owner") if acc else None
    return top10_from(largest, owners, supply, pool, bonding_curve(mint))

async def wallet_balance_raw(src, owner: str, mint: str) -> int:
    b = await src.rpc("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed"}])
    return sum(int(fields.dig(a, "account.data.parsed.info.tokenAmount.amount") or 0)
               for a in (fields.dig(b, "result.value") or []))

def best_pumpswap_pair(pairs: list[dict], mint: str) -> dict | None:
    """The desk's pool pair for this mint; if several, the one with the most liquidity.
    Solana: dexId pumpswap, exact mint (unchanged). BNB (0x...): dexId pancakeswap (v2/v3/infinity),
    address compared case-insensitively (DexScreener returns checksummed addresses)."""
    import chains
    if chains.chain_of(mint) == chains.BNB:
        c = [p for p in pairs or [] if chains.same(fields.dig(p, "baseToken.address"), mint)
             and p.get("dexId") == chains.POOL_DEX[chains.BNB]]
    else:
        c = [p for p in pairs or [] if fields.dig(p, "baseToken.address") == mint and p.get("dexId") == fields.DEX_POOL_DEX_ID]
    if not c: return None
    return max(c, key=lambda p: fields.dig(p, "liquidity.usd", 0) or 0)

best_pair = best_pumpswap_pair      # chain-aware name for new code
