"""Desk config. Secrets come only from env or /home/box/.desk_secrets (outside the repo).
Never log or write secret values."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SECRETS_FILE = Path(os.environ.get("DESK_SECRETS", "/home/box/.desk_secrets/secrets.env"))
OWNER_FILE = ROOT / "owner.json"
DB_PATH = ROOT / "desk.db"
LOG_DIR = ROOT / "logs"
OUT_DIR = ROOT / "output"          # plan B: JSON the publisher ships to Pages
TICK_S = 15

def _load_secrets() -> dict:
    vals = {}
    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
    return vals

_SECRETS = _load_secrets()

def secret(name: str) -> str | None:
    """Env wins, then the secrets file. Value is never printed."""
    return os.environ.get(name) or _SECRETS.get(name)

def rpc_url() -> str:
    """Built at runtime so the Helius key is never stored in any file."""
    key = secret("HELIUS_API_KEY")
    if key:
        return "https://mainnet.helius-rpc.com/?api-key=" + key
    return "https://api.mainnet-beta.solana.com"

# Defaults: every number in [brackets] in the master prompt. Overridable via db.settings.
DEFAULTS = {
    "mode": "PAPER",
    "paper_start_stack": 200.0,
    "stack": None,                 # LIVE: set with "stack 1240"
    "fomo_handle": "CipherLimit",
    "hosting": "planB_github_pages",
    "notifier": "telegram",
    "publish_every_s": 180,
    # Analyst
    "analyst_mode": "rules",
    "analyst.alive_6h_min": 0.70,
    "analyst.organic_min": 0.65,
    "analyst.chase_max": 0.30,
    "analyst.score_min": 70,
    "analyst.timeout_s": 10, "analyst.retry_after_s": 2, "analyst.queue_429_s": 60,
    "analyst.rules.vol_h1_full": 25_000,
    "analyst.rules.buys_h1_full": 150,
    "analyst.rules.bundle_penalty": 0.5,
    "analyst.rules.bundle_wallets": 3,
    "analyst.rules.flush_pct": 5,
    "analyst.rules.chase_h1_full": 80,
    # Executor
    "executor.size_pct": 8,
    "executor.liq_cap_pct": 2,
    # Risk
    "risk.poll_s": 15,
    "risk.stop_pct": 6,
    "risk.trail_on_pct": 20,
    "risk.trail_pct": 12,
    "risk.timeout_h": 3,
    "risk.timeout_min_gain_pct": 5,
    "risk.sold_wait_min": 2,
    "risk.no_price_polls": 4,
    "risk.backoff_429_s": 30,
    # Auditor
    "auditor.layer7_empty_graph": "red",   # strict, user decision 2026-11-16
}
