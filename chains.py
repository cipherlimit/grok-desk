"""Chains the desk covers (user 2026-11-18: BNB Chain added next to Solana).
A coin's chain follows from its address: 0x + 40 hex = BNB Chain (BSC, chainId 56), anything else = Solana.
BNB addresses are stored lowercase so every table, DexScreener batch and GoPlus key lines up.
Which chains the Scanner hunts on is the setting chains.enabled (default "solana": deploying changes nothing)."""
import re

SOLANA, BNB = "solana", "bnb"
_EVM = re.compile(r"0x[0-9a-fA-F]{40}")
LABEL = {SOLANA: "Solana", BNB: "BNB Chain"}
DEX_SLUG = {SOLANA: "solana", BNB: "bsc"}            # DexScreener /tokens/v1/{slug}/ and dexscreener.com/{slug}/
POOL_DEX = {SOLANA: "pumpswap", BNB: "pancakeswap"}  # DexScreener dexId of the pool the desk reads
CHAIN_CHOICES = ["solana", "bnb", "solana,bnb"]

def is_evm(address: str | None) -> bool:
    return bool(address) and bool(_EVM.fullmatch(address))

def chain_of(address: str | None) -> str:
    return BNB if is_evm(address) else SOLANA

def norm(address: str | None) -> str | None:
    """Canonical form: BNB lowercase, Solana untouched (base58 is case-sensitive)."""
    if address is None: return None
    a = address.strip()
    return a.lower() if is_evm(a) else a

def same(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and norm(a) == norm(b)

def enabled(s: dict) -> set[str]:
    v = str(s.get("chains.enabled") or SOLANA).lower()
    return {x.strip() for x in v.split(",") if x.strip()}

def with_bnb(s: dict, on: bool) -> str:
    cur = enabled(s)
    cur = (cur | {BNB}) if on else (cur - {BNB})
    if not cur: cur = {SOLANA}
    return ",".join(c for c in (SOLANA, BNB) if c in cur)

def dexscreener_url(address: str) -> str:
    return f"https://dexscreener.com/{DEX_SLUG[chain_of(address)]}/{address}"
