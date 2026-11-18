"""Live BNB Chain holder math from a public BSC JSON-RPC (default https://bsc-rpc.publicnode.com, free, no key;
override with the secret BSC_RPC_URL, never logged). GoPlus' holder lists lag new tokens by hours, so the top 10,
dev wallet and LP burn/lock come from the token's own Transfer logs, replayed from its mint (the BNB counterpart of
getTokenLargestAccounts on Solana).
A ledger is kept per address in memory and extended incrementally; if the mint isn't found within the scan budget
(~2 days of blocks) the balances are unknown -> NoData (strict)."""
import chains

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_TOPIC = "0x" + "0" * 64
CHUNK = 49_000                # publicnode: eth_getLogs range must stay under 50,000 blocks (~6h at 0.45s)
MAX_BACK = 8 * CHUNK          # ~2 days: older tokens have no fresh "graduation" to judge anyway
TOTAL_SUPPLY = "0x18160ddd"
# never counted as a holder: the pool(s) are passed in; these are burn sinks and launchpad / DEX vault contracts
NOT_HOLDERS = {
    "0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead",
    "0x5c952063c7fc8610ffdb798152d69f0b9550762b",   # four.meme TokenManager2
    "0xec4549cadce5da21df6e6422d448034b5233bfbc",   # four.meme TokenManager (v1)
    "0x238a358808379702088667322f80ac48bad5e6c4",   # PancakeSwap Infinity Vault
}
BURN = {"0x0000000000000000000000000000000000000000", "0x000000000000000000000000000000000000dead"}
LOCKERS = {
    "0x407993575c91ce7643a4d4ccacc9a98c36ee1bbe": "PinkLock",
    "0xc765bddb93b0d1c1a88282ba0fa6b2d00e3e0c83": "UNCX",
    "0x25c9c4b56e820e0dea438b145284f02d9ca9bd52": "GoPlus Locker",
}

class NoData(Exception): pass

def topic_addr(t: str) -> str:
    return "0x" + t[-40:].lower()

ZERO = "0x0000000000000000000000000000000000000000"

def apply_logs(bal: dict, logs: list[dict], self_addr: str | None = None) -> bool:
    """Replay Transfer logs into balances. Returns True when one of them is a mint (from 0x0).
    A transfer from the contract itself to 0x0 is a supply burn (a V2 pair burning LP on a liquidity removal):
    0x0 is not credited. Any other transfer to 0x0 is credited to 0x0 (tokens burned by _burn, LP sent to 0x0)."""
    minted = False
    for l in logs:
        tp = l.get("topics") or []
        if len(tp) < 3 or tp[0] != TRANSFER: continue
        try: v = int(l.get("data") or "0x0", 16)
        except ValueError: continue
        frm, to = topic_addr(tp[1]), topic_addr(tp[2])
        if tp[1] == ZERO_TOPIC: minted = True
        else: bal[frm] = bal.get(frm, 0) - v
        if to == ZERO and frm == self_addr: continue
        bal[to] = bal.get(to, 0) + v
    return minted

def consistent(bal: dict, supply: int, zero_holds: bool = False) -> bool:
    """Replayed balances must add up to totalSupply within 1%. Tokens: what reached 0x0 left the supply (_burn).
    LP tokens (zero_holds): LP sent to 0x0 still counts in the supply (removals were not credited, see apply_logs)."""
    tracked = sum(v for a, v in bal.items() if v > 0 and (zero_holds or a != ZERO))
    return supply > 0 and abs(tracked - supply) <= 0.01 * supply

def top10_from_balances(bal: dict, supply: int, excluded: set[str]) -> float:
    if not supply: raise NoData
    kept = sorted((v for a, v in bal.items() if v > 0 and a not in excluded), reverse=True)
    return round(100.0 * sum(kept[:10]) / supply, 2)

def lp_share(bal: dict, supply: int) -> tuple[float, float]:
    """(% of LP tokens burned, % locked in a known locker)."""
    if not supply: raise NoData
    burned = sum(bal.get(a, 0) for a in BURN)
    locked = sum(bal.get(a, 0) for a in LOCKERS)
    return 100.0 * burned / supply, 100.0 * locked / supply

class Ledgers:
    """address -> {"lo": first block scanned, "hi": last block scanned, "bal": {...}, "complete": mint seen}."""
    def __init__(self, src):
        self.src, self.st = src, {}

    async def latest(self) -> int:
        return int((await self.src.bsc_rpc("eth_blockNumber", []))["result"], 16)

    async def _logs(self, address, lo, hi) -> list[dict]:
        b = await self.src.bsc_rpc("eth_getLogs", [{"address": address, "fromBlock": hex(lo), "toBlock": hex(hi), "topics": [TRANSFER]}])
        return b.get("result") or []

    async def balances(self, address: str) -> dict:
        """Balances from the mint up to the latest block. Raises NoData when the mint is out of reach."""
        address = chains.norm(address)
        head = await self.latest()
        st = self.st.get(address)
        if st is None:
            if len(self.st) >= 2000: self.st.pop(next(iter(self.st)))        # oldest first: bounded memory
            st = self.st[address] = {"lo": head + 1, "hi": head, "bal": {}, "complete": False}
        if head > st["hi"]:                                        # forward: new blocks since the last look
            lo = st["hi"] + 1
            while lo <= head:
                hi = min(head, lo + CHUNK - 1)
                apply_logs(st["bal"], await self._logs(address, lo, hi), address); lo = hi + 1
            st["hi"] = head
        while not st["complete"] and head - st["lo"] < MAX_BACK:   # backward until the mint
            hi = st["lo"] - 1; lo = max(0, hi - CHUNK + 1)
            logs = await self._logs(address, lo, hi)
            if apply_logs(st["bal"], logs, address): st["complete"] = True
            st["lo"] = lo
        if not st["complete"]: raise NoData
        return st["bal"]

    async def total_supply(self, address: str) -> int:
        b = await self.src.bsc_rpc("eth_call", [{"to": chains.norm(address), "data": TOTAL_SUPPLY}, "latest"])
        r = b.get("result") or "0x"
        if len(r) < 3: raise NoData                            # no totalSupply(): not an ERC-20 / LP token
        try: return int(r, 16)
        except ValueError: raise NoData from None

    async def top10_pct(self, token: str, pools: set[str]) -> float:
        bal = await self.balances(token)
        supply = await self.total_supply(token)
        if not consistent(bal, supply): raise NoData          # rebasing/reflection token: logs don't add up to the supply
        return top10_from_balances(bal, supply, NOT_HOLDERS | {chains.norm(p) for p in pools if p} | {chains.norm(token)})

    async def wallet_pct(self, token: str, wallet: str) -> float:
        bal = await self.balances(token)
        supply = await self.total_supply(token)
        if not supply: raise NoData
        return 100.0 * max(0, bal.get(chains.norm(wallet), 0)) / supply

    async def lp_burned_locked(self, pair: str) -> tuple[float, float]:
        """V2 pair LP token: % burned and % in a known locker. V3/Infinity pools have no LP token -> NoData."""
        if not chains.is_evm(pair): raise NoData
        bal = await self.balances(pair)
        supply = await self.total_supply(pair)
        if not consistent(bal, supply, zero_holds=True): raise NoData
        return lp_share(bal, supply)

def ledgers_for(src) -> "Ledgers":
    """One shared ledger cache per Sources object (Scanner and Auditor reuse each other's log replays)."""
    lg = getattr(src, "_bsc_ledgers", None)
    if lg is None:
        lg = Ledgers(src); setattr(src, "_bsc_ledgers", lg)
    return lg

def is_v2(pair: dict | None) -> bool:
    """DexScreener labels a PancakeSwap V2 pool ["v2"]; only V2 pools have an LP token to burn or lock."""
    return bool(pair) and "v2" in [str(x).lower() for x in (pair.get("labels") or [])]
