"""Outside data sources: PumpPortal (scanner), DexScreener, RugCheck, Solana RPC.
BNB Chain (2026-11-18): GeckoTerminal new BSC pools (scanner), DexScreener /bsc, GoPlus token_security (chain 56),
honeypot.is sell simulation. All free and key-less.
No keys are ever logged; the RPC URL (which carries the Helius key) is never printed."""
import asyncio, time
import httpx

DEX_URL = "https://api.dexscreener.com/tokens/v1/solana/"
DEX_URL_CHAIN = "https://api.dexscreener.com/tokens/v1/{slug}/"
GT_NEW_POOLS = "https://api.geckoterminal.com/api/v2/networks/bsc/new_pools"
GOPLUS_URL = "https://api.gopluslabs.io/api/v1/token_security/56"
HONEYPOT_URL = "https://api.honeypot.is/v2/IsHoneypot"
RUG_URL = "https://api.rugcheck.xyz/v1/tokens/"
PUMPPORTAL_WS = "wss://pumpportal.fun/api/data"
DEX_BATCH = 30            # DexScreener tokens/v1 takes up to 30 addresses per call

class SourceError(Exception): pass
class RateLimited(SourceError): pass

class _Limiter:
    """Sliding 60s window shared by every DexScreener caller in the process."""
    def __init__(self): self.calls: list[float] = []; self.lock = asyncio.Lock()
    async def wait(self, max_rpm: int):
        async with self.lock:
            while True:
                now = time.monotonic()
                self.calls = [t for t in self.calls if now - t < 60]
                if len(self.calls) < max_rpm:
                    self.calls.append(now); return
                await asyncio.sleep(60 - (now - self.calls[0]) + 0.01)

class Sources:
    def __init__(self, rpc_url_fn, dex_max_rpm_fn=lambda: 250, timeout=10):
        self._rpc = rpc_url_fn
        self._rpm = dex_max_rpm_fn
        self.http = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "grok-desk/1.0"})
        self.limiter = _Limiter()
        self._rpc_id = 0

    # ---- DexScreener ----
    async def dex_tokens(self, mints: list[str]) -> list[dict]:
        """Raw DexScreener pairs list for up to DEX_BATCH mints (one call per chain in the batch:
        Solana mints -> /tokens/v1/solana, 0x BNB addresses -> /tokens/v1/bsc)."""
        if not mints: return []
        if len(mints) > DEX_BATCH: raise ValueError("one call takes at most 30 mints")
        import chains
        groups: dict[str, list[str]] = {}
        for m in mints: groups.setdefault(chains.chain_of(m), []).append(m)
        out = []
        for ch in (chains.SOLANA, chains.BNB):
            if ch in groups: out += await self._dex_call(ch, groups[ch])
        return out

    async def _dex_call(self, chain: str, mints: list[str]) -> list[dict]:
        import chains
        url = DEX_URL if chain == chains.SOLANA else DEX_URL_CHAIN.format(slug=chains.DEX_SLUG[chain])
        await self.limiter.wait(int(self._rpm()))
        try:
            r = await self.http.get(url + ",".join(mints))
        except httpx.HTTPError as e:
            raise SourceError(f"dexscreener {type(e).__name__}") from None
        if r.status_code == 429: raise RateLimited("dexscreener 429")
        if r.status_code != 200: raise SourceError(f"dexscreener HTTP {r.status_code}")
        body = r.json()
        return body if isinstance(body, list) else []

    # ---- RugCheck ----
    async def _rug(self, path):
        try:
            r = await self.http.get(RUG_URL + path)
        except httpx.HTTPError as e:
            raise SourceError(f"rugcheck {type(e).__name__}") from None
        if r.status_code == 429: raise RateLimited("rugcheck 429")
        if r.status_code != 200: raise SourceError(f"rugcheck HTTP {r.status_code}")
        return r.json()
    async def rug_report(self, mint): return await self._rug(f"{mint}/report")
    async def rug_graph(self, mint): return await self._rug(f"{mint}/insiders/graph")

    # ---- Solana RPC ----
    async def rpc(self, method: str, params: list):
        self._rpc_id += 1
        try:
            r = await self.http.post(self._rpc(), json={"jsonrpc": "2.0", "id": self._rpc_id, "method": method, "params": params})
        except httpx.HTTPError as e:
            raise SourceError(f"rpc {method} {type(e).__name__}") from None   # never echo the URL
        if r.status_code == 429: raise RateLimited(f"rpc {method} 429")
        if r.status_code != 200: raise SourceError(f"rpc {method} HTTP {r.status_code}")
        body = r.json()
        if "error" in body: raise SourceError(f"rpc {method} error {body['error'].get('code')}")
        return body

    # ---- BNB Chain ----
    async def _get_json(self, name, url, params=None, ok404=False):
        try:
            r = await self.http.get(url, params=params)
        except httpx.HTTPError as e:
            raise SourceError(f"{name} {type(e).__name__}") from None
        if r.status_code == 429: raise RateLimited(f"{name} 429")
        if r.status_code == 404 and ok404:
            try: return r.json()
            except ValueError: return {"code": 404}
        if r.status_code != 200: raise SourceError(f"{name} HTTP {r.status_code}")
        try: return r.json()
        except ValueError: raise SourceError(f"{name} bad json") from None

    async def bsc_rpc(self, method: str, params: list):
        """BNB Chain JSON-RPC (config.bsc_rpc_url: secret BSC_RPC_URL or the free publicnode endpoint). URL never logged."""
        from config import bsc_rpc_url
        self._rpc_id += 1
        try:
            r = await self.http.post(bsc_rpc_url(), json={"jsonrpc": "2.0", "id": self._rpc_id, "method": method, "params": params})
        except httpx.HTTPError as e:
            raise SourceError(f"bsc rpc {method} {type(e).__name__}") from None
        if r.status_code == 429: raise RateLimited(f"bsc rpc {method} 429")
        if r.status_code != 200: raise SourceError(f"bsc rpc {method} HTTP {r.status_code}")
        body = r.json()
        if "error" in body: raise SourceError(f"bsc rpc {method} error {(body.get('error') or {}).get('code')}")
        return body

    async def gt_new_pools(self, page: int = 1) -> dict:
        """GeckoTerminal newest BSC pools (free, no key, ~30 calls/min, pages 1..10)."""
        return await self._get_json("geckoterminal", GT_NEW_POOLS, {"page": page})

    async def goplus_token(self, address: str) -> dict:
        """GoPlus token_security for one BSC token -> the result dict for it (raises SourceError when absent)."""
        body = await self._get_json("goplus", GOPLUS_URL, {"contract_addresses": address.lower()})
        if not isinstance(body, dict) or body.get("code") != 1:
            raise SourceError(f"goplus code {body.get('code') if isinstance(body, dict) else '?'}")
        res = (body.get("result") or {}).get(address.lower())
        if not isinstance(res, dict) or not res: raise SourceError("goplus no result")
        return res

    async def honeypot(self, address: str, pair: str | None = None) -> dict:
        """honeypot.is buy/sell simulation. A 404 ("pair not found", common on brand-new pools) comes back as the body."""
        params = {"address": address, "chainID": 56}
        if pair: params["pair"] = pair
        return await self._get_json("honeypot.is", HONEYPOT_URL, params, ok404=True)

    async def close(self): await self.http.aclose()

async def with_retry(fn, tries: int, gap_s: float):
    """Retry [3] times [2]s apart. Raises the last error when every try failed."""
    last = None
    for i in range(tries):
        try: return await fn()
        except SourceError as e:
            last = e
            if i < tries - 1: await asyncio.sleep(gap_s)
    raise last
