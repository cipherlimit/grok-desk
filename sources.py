"""Outside data sources: PumpPortal (scanner), DexScreener, RugCheck, Solana RPC.
No keys are ever logged; the RPC URL (which carries the Helius key) is never printed."""
import asyncio, time
import httpx

DEX_URL = "https://api.dexscreener.com/tokens/v1/solana/"
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
        """Raw DexScreener pairs list for up to DEX_BATCH mints (one call)."""
        if not mints: return []
        if len(mints) > DEX_BATCH: raise ValueError("one call takes at most 30 mints")
        await self.limiter.wait(int(self._rpm()))
        try:
            r = await self.http.get(DEX_URL + ",".join(mints))
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
