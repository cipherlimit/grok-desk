"""BNB Chain dry run (box or laptop): real new BSC coins through the Scanner rules and the 8 BNB Auditor layers.
Read-only: no db writes, no Telegram, no publisher, no orders. Prints one block per coin.
  python tools/bnb_dryrun.py [pages=3] [max_coins=8]"""
import asyncio, sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import bnb, bsc, chains, db, fields, onchain
from config import DEFAULTS
from sources import Sources, SourceError
from agents.scanner import decide

async def main(pages=3, max_coins=8):
    s = dict(DEFAULTS); s["scanner.min_liquidity_usd"] = 10_000          # the laptop's current Scanner bar
    src = Sources(lambda: "https://invalid.local", lambda: 60)          # no Solana RPC needed
    try:
        found, got = [], 0
        for p in range(1, pages + 1):
            try: found += bnb.parse_new_pools(await src.gt_new_pools(p)); got = p
            except SourceError as e:
                print(f"  geckoterminal page {p}: {e}, stopping at {p - 1} pages"); break
            await asyncio.sleep(7)                                     # free tier: stay well under its rate limit
        seen, coins = set(), []
        for c in found:
            if c["mint"] not in seen: seen.add(c["mint"]); coins.append(c)
        print(f"GeckoTerminal: {len(coins)} new PancakeSwap coins on BSC (pages 1-{got})")
        now = time.time()
        old_enough = lambda c: (now - db.parse_iso(c["created_at"])) / 60 >= s["scanner.judge_from_age_min"]
        coins = sorted(coins, key=lambda c: (not old_enough(c), -c["reserve_usd"]))[:max_coins]   # judgeable ones first
        pairs = await src.dex_tokens([c["mint"] for c in coins])
        now = time.time()
        for c in coins:
            mint = c["mint"]; pair = onchain.best_pair(pairs, mint)
            d = fields.dex_read(pair) if pair else {}
            liq = d.get("liquidity_usd"); pool = chains.norm(d.get("pool")) or c["pool"]
            age = int((now - db.parse_iso(c["created_at"])) / 60)
            pools = {chains.norm(p.get("pairAddress")) for p in pairs if chains.same(fields.dig(p, "baseToken.address"), mint)}
            g = await bnb.gather(src, bsc.ledgers_for(src), mint, pool, bsc.is_v2(pair), pools)
            gp, hp, top10 = g["gp"], g["hp"], g["top10"]
            s2 = dict(s); s2["scanner.judge_from_age_min"] = 0                # show the rule outcome even before 10 min
            v, why, _ = decide(s2, liq or 0, top10, d.get("chg_h1"), None, age) if liq is not None else ("wait", "no DexScreener pair yet", 0)
            v = v or ("wait (top 10 unknown)" if top10 is None else "wait (pool < 10 min)")
            layers, reason = bnb.run_layers(gp, hp, top10=top10, dev_pct=g["dev_pct"], lp=g["lp"],
                                            created_ts=db.parse_iso(c["created_at"]), liq_now=liq, liq_scan=liq, s=s, now=now)
            lp = g["lp"]
            dev_txt = "?" if g["dev_pct"] is None else f"{g['dev_pct']:.1f}%"
            print(f"\n{d.get('symbol') or c['symbol']} · {mint} · {c['dex']} · age {age}m · liq ${liq or 0:,.0f}"
                  f" · top10 {top10 if top10 is not None else '?'}% · 1h {d.get('chg_h1')}%")
            print(f"  Scanner: {v}" + (f" ({why})" if why else "") + f" · honeypot.is: {'simulated' if bnb.hp_ok(hp) else 'pair not found'}"
                  f" · GoPlus: {'yes' if gp else 'no'} · LP burned/locked: {'n/a' if lp is None else f'{lp[0]:.0f}%/{lp[1]:.0f}%'}"
                  f" · dev: {dev_txt}")
            for l in layers: print(f"  L{l['n']} {l['name']}: {'pass' if l['pass'] else 'RED'} · {l['value']}")
            print(f"  Auditor: {'8/8 PASS' if reason is None else 'KILL · ' + reason}")
    finally:
        await src.close()

if __name__ == "__main__":
    a = [int(x) for x in sys.argv[1:3]]
    asyncio.run(main(*a))
