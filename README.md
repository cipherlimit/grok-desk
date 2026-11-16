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

## Run
    ./run.sh                        # desk + publisher (setsid, background cgroup)
    .venv/bin/python -m pytest tests
