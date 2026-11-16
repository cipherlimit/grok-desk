"""The ONLY place FOMO URLs are built. Every link carries the referral r=savipww.
Agents call links.coin_url(mint, "solana"); never build a FOMO URL by hand."""
from urllib.parse import quote

REF = "savipww"
BASE = "https://fomo.family"
CHAINS = {"solana": "solana", "base": "base", "bnb": "bnb", "bsc": "bnb", "robinhood": "robinhood"}

def coin_url(mint: str, chain: str = "solana") -> str:
    slug = CHAINS[chain.lower()]
    return f"{BASE}/tokens/{slug}/{quote(mint, safe='')}?r={REF}"

def profile_url(handle: str) -> str:
    return f"{BASE}/profile/{quote(handle, safe='')}?source=share_link&r={REF}"

def signup_url() -> str:
    return f"{BASE}/r/{REF}"
