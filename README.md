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
