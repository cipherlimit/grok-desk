"""Desk config. Secrets come only from env or /home/box/.desk_secrets (outside the repo).
Never log or write secret values."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SECRETS_FILE = Path(os.environ.get("DESK_SECRETS", "/home/box/.desk_secrets/secrets.env"))
OWNER_FILE = ROOT / "owner.json"
DB_PATH = ROOT / "desk.db"
LOG_DIR = Path(os.environ.get("DESK_LOG_DIR", ROOT / "logs"))
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

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"   # bonding curve PDA ["bonding-curve", mint]
LOCAL_TZ = "Europe/Budapest"

def rpc_url_for(setting_value) -> str:
    """settings.rpc_url holds "helius" (default, key from env at runtime) or a full key-less URL."""
    if not setting_value or setting_value == "helius":
        return rpc_url()
    return setting_value

# Defaults: every number in [brackets] in the master prompt. Seeded into db.settings,
# read live from there on every use (db.settings_all).
DEFAULTS = {
    "mode": "PAPER",
    "paper_start_stack": 200.0,
    "stack": None,                 # LIVE: set with "stack 1240"
    "fomo_handle": "CipherLimit",
    "hosting": "planB_github_pages",
    "notifier": "telegram",
    "publish_every_s": 180,
    "rpc_url": "helius",
    "night_starts_local_hour": 12,  # a "night" runs 12:00 -> 12:00 Europe/Budapest
    # Scanner
    "scanner.dex_every_s": 60,
    "scanner.min_liq_usd": 40_000,
    "scanner.top10_max_pct": 25,
    "scanner.h1_max_pct": 80,
    "scanner.min_age_min": 10,
    "scanner.judge_from_age_min": 10,   # user 2026-11-16: kills judged only from this pool age
    "scanner.room_max_min": 60,
    "scanner.dex_max_rpm": 250,
    "scanner.runner_h24_pct": 300,
    "scanner.ws_silent_s": 120,
    "scanner.ws_backoff_max_s": 60,
    "scanner.dex_429_wait_s": 30,
    # Auditor
    "auditor.dev_max_pct": 5,
    "auditor.top10_max_pct": 25,
    "auditor.lp_min_pct": 99,
    "auditor.liq_drop_max_pct": 50,
    "auditor.min_age_min": 10,
    "auditor.bundle_max": 5,
    "auditor.delegate_max": 10,
    "auditor.retries": 3,
    "auditor.retry_gap_s": 2,
    "auditor.layer7_empty_graph": "red",   # strict, user decision 2026-11-16
    # Analyst
    "analyst_mode": "rules",
    "analyst.state_max_chars": 3000,
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
    "executor.min_usd": 47.50,
    "below_min": "skip",            # "bump" | "skip"; prompt gives no default, stricter = skip
    "executor.liq_drop_max_pct": 20,
    "executor.price_run_max_pct": 15,
    "executor.bought_wait_min": 5,
    "executor.paper_bar_s": 1.2,
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
    # api.py price writer (STEP 4 note)
    "api.prices_every_s": 60,
}
