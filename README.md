# Grok Desk

FOMO trading desk: 5 agents (Scanner, Auditor, Analyst, Executor, Risk), PAPER first.
The desk never places orders: it pushes Buy/Sell buttons (links.coin_url, r=savipww) to Telegram.

## Hosting: plan B (GitHub Pages)
- `main.py` writes `output/state.json`, `chart.json`, `activity.json` every 15 s (box only, git-ignored).
- `publisher.py` every 180 s encrypts those files (AES-256, key `DATA_KEY`) and sends a
  `repository_dispatch` to `.github/workflows/pages.yml`.
- The workflow decrypts them into `/d/<DATA_PATH>/` (Actions secrets `DATA_PATH`, `DATA_KEY`),
  deploys to Pages, then deletes the Pages artifact.
- No data, data path or keys are ever committed. Secrets live in `/home/box/.desk_secrets` (600).
- The app reads the data path from the URL fragment `#d=...`, which is never sent to the server.
- App data refreshes every ~3-5 min (deploy time + Pages caching). Pushes go through Telegram instantly.


## Limitation: the box hibernates when idle
`./run.sh` detaches the desk (setsid + `/sys/fs/cgroup/background`) so it
survives the end of an agent or routine *process*. That is not enough to run
unattended. This Grok Bot computer is an anyrun pod: a few minutes after the
last session ends, the platform hibernates the whole VM. Guest processes keep
their PIDs but get no CPU until something external wakes the pod (you opening
a chat, or a scheduled routine). There is no in-box keepalive, cgroup, or
supervisor that can prevent that.

The desk therefore only scans while a session is active. The existing hourly
desk-health routine wakes the box about once an hour, notices stale
heartbeats, and restarts the desk; after the routine exits, the pod
hibernates again. Tightening that routine (fastest allowed: `@every 5m`)
shortens the gaps but still cannot give continuous coverage, and each run is
a billed Grok Bot turn. A host that does not hibernate would be required for
true 24/7 PAPER scanning.

## Run
    ./run.sh                        # desk + publisher (setsid, background cgroup)
    .venv/bin/python -m pytest tests

## Windows host (LenovoYoga, D:\TradingDesk) — runs 24/7
- `tools\install_windows.ps1`: `.venv` (py -3.12), requirements, tests, Scheduled Task **GrokDesk**
  (pythonw `supervisor.py` at logon + every 5 min, IgnoreNew, never stopped on battery), AC power never sleeps.
- `supervisor.py` = run.sh for Windows: starts `main.py` + `publisher.py`, `logs\*.pid`, restarts on exit,
  kill-on-close Job object, single instance (`logs\supervisor.lock`). `run.ps1` restarts it via the task.
- Secrets: `D:\TradingDesk\.desk_secrets\secrets.env` (git-ignored). The publisher uses `GH_TOKEN` from it
  (no gh/openssl needed). `tools\desk_health.py` restarts via `schtasks /End` + `/Run GrokDesk`.
- The box keeps a git-ignored `MOVED_TO` file: `run.sh`, `supervisor.py` and `desk_health.py` stand down there.
- Laptop tasks: **GrokDeskHealth** (hourly `tools\desk_health.py`) and **GrokNightReport** (08:01:
  `night_report.py --clear-skips`, then `publisher.py --once --with-report`; regular publishes keep shipping
  a report younger than 26 h so it stays on Pages).
- Box routines read the laptop through Pages: `tools/pages_watch.py` (exit 1 "stale N min" if state.json
  is older than 15 min) and `tools/fetch_night_report.py` (writes output/night_report_<date>.md/.json).

## BNB Chain (branch bnb-chain, 2026-11-18)
Off until the owner sends `bnb on` in Telegram (setting `chains.enabled`, default `solana`; `bnb off` reverts).
- **New coins:** GeckoTerminal `/networks/bsc/new_pools` pages 1-2 every `scanner.bnb_poll_s` (60s): PancakeSwap
  pools only (four.meme graduations, flap.sh, plain launches); four.meme bonding curves and majors (WBNB, USDT, ...)
  skipped. They enter the same waiting room (`coins.chain='bnb'`, `migrated_at` = pool creation) and the same
  Scanner rules (liquidity, top 10, 1h, copycat, judged from 10 min).
- **Market data:** DexScreener `/tokens/v1/bsc` (Sources.dex_tokens splits a batch per chain); the desk's pool is the
  biggest `pancakeswap` pair. BNB addresses are stored lowercase.
- **Holders:** replayed from the token's Transfer logs over a public BSC RPC (`bsc.py`; default
  `https://bsc-rpc.publicnode.com`, override with the secret `BSC_RPC_URL`). GoPlus holder lists lag new tokens.
- **Auditor, 8 BNB layers** (stop at first red; missing data = red, like layer 7):
  1 Honeypot (GoPlus is_honeypot/cannot_sell_all/cannot_buy + honeypot.is simulation when it has the pair)
  2 Taxes (buy/sell/transfer < `auditor.bnb_max_tax_pct` 10%, tax not modifiable)
  3 Contract (verified source, no proxy, not mintable, no self-destruct/hidden owner/take-back/external call)
  4 Owner powers (no balance edits, pause, blacklist, cooldown; owner renounced if `auditor.bnb_require_renounced`)
  5 Top 10 holders < `auditor.max_top10_pct` (pools, burn and launchpad contracts excluded)
  6 Liquidity: V2 LP burned + locked >= `auditor.min_lp_locked_pct`, not pulled > `auditor.liq_drop_max_pct`
  7 Pool age >= `auditor.min_pool_age_min`   8 Dev wallet (creator + live owner) < `auditor.max_dev_pct`
- Telegram: `why 0x...`, `buy 0x...`, or a pasted FOMO coin link; FOMO links come from `links.coin_url` (chainId 56).
- Dry run (read-only, no db/Telegram/publisher): `python tools/bnb_dryrun.py [pages] [coins]`.
- Schema: `chain TEXT NOT NULL DEFAULT 'solana'` appended to coins/scans/audits/signals/positions by
  `db.migrate_chain` on connect (ALTER TABLE ADD COLUMN, existing rows kept, idempotent).
