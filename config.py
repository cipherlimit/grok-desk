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

# ---- STEP 10 settings: key -> (default, min, max, group, aliases) ---------------------------
# Every [bracket] number in the agent prompts is here; STEP 10 names first, extra brackets after.
# Values are validated by validate(); out of range = refused with the range.
SPEC = {
    # scanner
    "scanner.min_liquidity_usd": (40_000, 0, 10_000_000, "scanner", ["min liquidity", "min liq"]),
    "scanner.max_top10_pct": (25, 1, 100, "scanner", ["scanner top 10"]),
    "scanner.max_h1_change_pct": (80, 1, 10_000, "scanner", ["max h1", "h1 change"]),
    "scanner.poll_s": (60, 15, 600, "scanner", []),
    "scanner.min_pool_age_min": (10, 0, 240, "scanner", ["pool age"]),
    "scanner.max_wait_min": (60, 5, 720, "scanner", ["max wait"]),
    "scanner.runner_h24_pct": (300, 10, 100_000, "scanner", ["runner"]),
    "scanner.judge_from_age_min": (10, 0, 240, "scanner", ["judge from"]),       # user 2026-11-16
    "scanner.dex_max_rpm": (250, 10, 300, "scanner", []),
    "scanner.ws_silent_s": (120, 30, 3600, "scanner", []),   # Scanner prompt: closed or silent for [120]s -> reconnect
    "scanner.ws_backoff_max_s": (60, 1, 600, "scanner", []),
    "scanner.dex_429_wait_s": (30, 1, 600, "scanner", []),
    # auditor
    "auditor.enabled": (True, None, None, "auditor", ["take apart"]),
    "auditor.max_dev_pct": (5, 0, 100, "auditor", ["max dev", "dev"]),
    "auditor.max_top10_pct": (25, 1, 100, "auditor", ["top 10", "top10"]),
    "auditor.min_lp_locked_pct": (99, 0, 100, "auditor", ["lp locked", "lp"]),
    "auditor.liq_drop_max_pct": (50, 1, 100, "auditor", []),
    "auditor.min_pool_age_min": (10, 0, 240, "auditor", []),
    "auditor.max_bundled": (5, 1, 1000, "auditor", ["bundled", "max bundled"]),
    "auditor.max_delegated_accounts": (10, 1, 10_000, "auditor", ["delegations", "delegated"]),
    "auditor.retries": (3, 1, 10, "auditor", ["retries"]),
    "auditor.retry_gap_s": (2, 0, 60, "auditor", []),
    # analyst
    "analyst.mode": ("rules", ["jev", "rules"], None, "analyst", ["analyst mode"]),
    "analyst.min_alive_6h": (0.70, 0, 1, "analyst", ["alive"]),
    "analyst.min_organic": (0.65, 0, 1, "analyst", ["organic"]),
    "analyst.max_chase": (0.30, 0, 1, "analyst", ["chase"]),
    "analyst.min_score": (70, 0, 100, "analyst", ["score", "min score"]),
    "analyst.timeout_s": (10, 1, 60, "analyst", []),
    "analyst.retry_after_s": (2, 0, 60, "analyst", []),
    "analyst.queue_429_s": (60, 1, 600, "analyst", []),
    "analyst.state_max_chars": (3000, 200, 20_000, "analyst", []),
    "analyst.rules.alive_volume_h1": (25_000, 1, 10_000_000, "analyst", ["alive volume"]),
    "analyst.rules.organic_buys_h1": (150, 1, 100_000, "analyst", ["organic buys"]),
    "analyst.rules.bundled_penalty": (0.5, 0, 1, "analyst", ["bundled penalty"]),
    "analyst.rules.bundled_wallets": (3, 1, 1000, "analyst", []),
    "analyst.rules.flush_drop_m5_pct": (5, 0, 100, "analyst", ["flush"]),
    "analyst.rules.chase_h1_pct": (80, 1, 10_000, "analyst", ["chase h1"]),
    # executor
    "executor.stack_pct": (8, 0.5, 100, "executor", ["stack pct", "size"]),
    "executor.max_pool_pct": (2, 0.1, 100, "executor", ["pool pct", "max pool"]),
    "executor.min_ticket_usd": (47.50, 0, 10_000, "executor", ["min ticket"]),
    "executor.below_min_paper": ("trade", ["trade", "bump", "skip"], None, "executor", ["below min paper"]),  # user
    "executor.below_min_live": ("skip", ["trade", "bump", "skip"], None, "executor", ["below min live", "below min"]),  # user
    "executor.recheck_liq_drop_pct": (20, 0, 100, "executor", ["liq drop"]),
    "executor.recheck_price_up_pct": (15, 0, 1000, "executor", ["price up"]),
    "executor.confirm_window_min": (5, 1, 60, "executor", ["buy window"]),
    "executor.paper_bar_s": (1.2, 0, 10, "executor", []),
    # risk
    "risk.poll_s": (15, 5, 300, "risk", []),
    "risk.stop_pct": (6, 0.5, 90, "risk", ["stop"]),
    "risk.trail_start_pct": (20, 0.5, 1000, "risk", ["trail start"]),
    "risk.trail_pct": (12, 0.5, 90, "risk", ["trail"]),
    "risk.timeout_h": (3, 0.25, 72, "risk", ["timeout"]),
    "risk.timeout_min_gain_pct": (5, 0, 1000, "risk", ["timeout gain"]),
    "risk.confirm_window_min": (2, 1, 60, "risk", ["sell window"]),
    "risk.no_price_polls": (4, 1, 100, "risk", []),
    "risk.backoff_429_s": (30, 1, 600, "risk", []),
    # desk
    "desk.mode": ("PAPER", ["PAPER", "LIVE"], None, "desk", ["mode"]),
    "desk.paper_start_stack": (200.0, 10, 1_000_000, "desk", ["paper stack"]),
    "desk.stack": (None, 0, 10_000_000, "desk", ["live stack"]),                 # "stack 1240"
    "desk.report_time": ("08:00", "time", None, "desk", ["report time"]),
    "desk.notifier": ("telegram", ["telegram", "webpush"], None, "desk", ["notifier"]),
    "desk.rpc_url": ("helius", "url", None, "desk", ["rpc"]),   # "helius" = built at runtime from env HELIUS_API_KEY
    "desk.push_rejects": (False, None, None, "desk", ["push rejects"]),
    "desk.fees_ref_discount": (0.10, 0, 1, "desk", ["ref discount"]),
    "desk.fomo_handle": ("CipherLimit", "text", None, "desk", ["fomo handle", "handle"]),
    "desk.hosting": ("planB_github_pages", "text", None, "desk", []),
    "desk.publish_every_s": (180, 60, 3600, "desk", []),
    "api.prices_every_s": (60, 15, 600, "desk", []),
}
DEFAULTS = {k: v[0] for k, v in SPEC.items()}
DEFAULTS.update({f"paused.{a}": False for a in ("scanner", "auditor", "analyst", "executor", "risk")})

# old key -> STEP 10 key (settings rows written before STEP 10 are migrated by db.seed_settings)
RENAMED = {
    "scanner.min_liq_usd": "scanner.min_liquidity_usd", "scanner.top10_max_pct": "scanner.max_top10_pct",
    "scanner.h1_max_pct": "scanner.max_h1_change_pct", "scanner.dex_every_s": "scanner.poll_s",
    "scanner.min_age_min": "scanner.min_pool_age_min", "scanner.room_max_min": "scanner.max_wait_min",
    "auditor.dev_max_pct": "auditor.max_dev_pct", "auditor.top10_max_pct": "auditor.max_top10_pct",
    "auditor.lp_min_pct": "auditor.min_lp_locked_pct", "auditor.min_age_min": "auditor.min_pool_age_min",
    "auditor.bundle_max": "auditor.max_bundled", "auditor.delegate_max": "auditor.max_delegated_accounts",
    "analyst_mode": "analyst.mode", "analyst.alive_6h_min": "analyst.min_alive_6h",
    "analyst.organic_min": "analyst.min_organic", "analyst.chase_max": "analyst.max_chase",
    "analyst.score_min": "analyst.min_score", "analyst.rules.vol_h1_full": "analyst.rules.alive_volume_h1",
    "analyst.rules.buys_h1_full": "analyst.rules.organic_buys_h1", "analyst.rules.bundle_penalty": "analyst.rules.bundled_penalty",
    "analyst.rules.bundle_wallets": "analyst.rules.bundled_wallets", "analyst.rules.flush_pct": "analyst.rules.flush_drop_m5_pct",
    "analyst.rules.chase_h1_full": "analyst.rules.chase_h1_pct",
    "executor.size_pct": "executor.stack_pct", "executor.liq_cap_pct": "executor.max_pool_pct",
    "executor.min_usd": "executor.min_ticket_usd", "below_min_paper": "executor.below_min_paper",
    "below_min_live": "executor.below_min_live", "executor.liq_drop_max_pct": "executor.recheck_liq_drop_pct",
    "executor.price_run_max_pct": "executor.recheck_price_up_pct", "executor.bought_wait_min": "executor.confirm_window_min",
    "risk.trail_on_pct": "risk.trail_start_pct", "risk.sold_wait_min": "risk.confirm_window_min",
    "mode": "desk.mode", "paper_start_stack": "desk.paper_start_stack", "stack": "desk.stack",
    "fomo_handle": "desk.fomo_handle", "hosting": "desk.hosting", "notifier": "desk.notifier",
    "publish_every_s": "desk.publish_every_s", "rpc_url": "desk.rpc_url",
}
RETIRED = {"night_starts_local_hour", "auditor.layer7_empty_graph", "below_min"}   # layer 7 strict is code, not a knob

class SettingError(ValueError): pass

def validate(key: str, raw):
    """Parse + range-check one setting. Raises SettingError('<key> must be between a and b')."""
    if key.startswith("paused."):
        if isinstance(raw, bool): return raw
        return str(raw).lower() in ("1", "true", "on", "yes", "paused")
    if key not in SPEC: raise SettingError(f"unknown setting {key}")
    default, lo, hi, _, _ = SPEC[key]
    if isinstance(lo, list):
        v = str(raw).strip()
        m = {x.lower(): x for x in lo}
        if v.lower() not in m: raise SettingError(f"{key} must be one of {', '.join(lo)}")
        return m[v.lower()]
    if lo == "time":
        import re as _re
        v = str(raw).strip()
        if not _re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v): raise SettingError(f"{key} must be HH:MM")
        return v
    if lo == "url":
        v = str(raw).strip()
        if v != "helius" and not v.startswith("https://"): raise SettingError(f"{key} must be https://... or helius")
        if "api-key=" in v: raise SettingError(f"{key}: put keys in env, not in settings")
        return v
    if lo == "text": return str(raw).strip().lstrip("@")
    if isinstance(default, bool) or lo is None:
        if isinstance(raw, bool): return raw
        return str(raw).strip().lower() in ("1", "true", "on", "yes")
    try: v = float(str(raw).replace("$", "").replace("%", "").replace(",", "").strip())
    except ValueError: raise SettingError(f"{key} must be a number between {lo:g} and {hi:g}") from None
    if not (lo <= v <= hi): raise SettingError(f"{key} must be between {lo:g} and {hi:g}")
    return int(v) if isinstance(default, int) and v.is_integer() else v

def resolve_key(name: str) -> str | None:
    """Setting by full name, short name or alias ('stop', 'trail', 'stop_pct', 'risk.stop_pct')."""
    n = name.strip().lower().replace("-", " ").replace("_", " ")
    for k, (_, _, _, _, aliases) in SPEC.items():
        if n == k.lower().replace("_", " ") or n == k.split(".")[-1].replace("_", " ") or n in aliases:
            return k
    return None
