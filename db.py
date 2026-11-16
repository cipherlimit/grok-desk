"""SQLite in WAL mode. Schema exactly per STEP 4 (14 tables). Open/closed is derived:
positions.closed_at IS NULL = open; LIVE "exiting" = exit_reason set and closed_at NULL."""
import json, sqlite3, time
from config import DB_PATH

ACTIVITY_KINDS = ("bought", "sold", "stopped", "rejected")

SCHEMA = """
CREATE TABLE IF NOT EXISTS coins (
  mint TEXT PRIMARY KEY, name TEXT, symbol TEXT, pool TEXT, migrated_at TEXT, creator TEXT,
  creator_balance_at_migration REAL, holders INTEGER);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, ts TEXT NOT NULL,
  liquidity_usd REAL, top10_pct REAL, h1_change_pct REAL, age_min REAL, verdict TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS audits (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, ts TEXT NOT NULL,
  layer INTEGER, name TEXT, value_text TEXT, pass INTEGER, source TEXT);
CREATE TABLE IF NOT EXISTS verdicts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, ts TEXT NOT NULL,
  alive_6h REAL, organic REAL, flush REAL, chase REAL, score INTEGER, pass INTEGER,
  reason TEXT, input_tokens INTEGER);
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, ts TEXT NOT NULL,
  price REAL, size_usd REAL, stack_pct REAL, link TEXT, mode TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, opened_at TEXT NOT NULL,
  entry_price REAL NOT NULL, size_usd REAL NOT NULL, peak_price REAL NOT NULL, stop_price REAL NOT NULL,
  trail_active INTEGER NOT NULL DEFAULT 0, closed_at TEXT, exit_price REAL, exit_reason TEXT,
  pnl_usd REAL, fees_usd REAL, mode TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('bought','sold','stopped','rejected')),
  mint TEXT, agent TEXT, title TEXT, subtitle TEXT, value TEXT);
CREATE TABLE IF NOT EXISTS prices (mint TEXT NOT NULL, ts TEXT NOT NULL, price REAL, volume_m5 REAL);
CREATE TABLE IF NOT EXISTS balance (ts TEXT NOT NULL, value REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS change_log (ts TEXT NOT NULL, key TEXT NOT NULL, old TEXT, new TEXT, who TEXT);
CREATE TABLE IF NOT EXISTS skips (mint TEXT PRIMARY KEY, until TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS heartbeats (agent TEXT PRIMARY KEY, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, auth TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS scans_mint_ts ON scans(mint, ts);
CREATE INDEX IF NOT EXISTS audits_mint_ts ON audits(mint, ts);
CREATE INDEX IF NOT EXISTS verdicts_mint_ts ON verdicts(mint, ts);
CREATE INDEX IF NOT EXISTS signals_mint_ts ON signals(mint, ts);
CREATE INDEX IF NOT EXISTS positions_mint_opened ON positions(mint, opened_at);
CREATE UNIQUE INDEX IF NOT EXISTS positions_one_open ON positions(mint) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS activity_mint_ts ON activity(mint, ts);
CREATE INDEX IF NOT EXISTS activity_ts ON activity(ts);
CREATE INDEX IF NOT EXISTS prices_mint_ts ON prices(mint, ts);
CREATE INDEX IF NOT EXISTS balance_ts ON balance(ts);
CREATE INDEX IF NOT EXISTS change_log_ts ON change_log(ts);
"""

# Spec, used by tests and migration: table -> columns in order.
SPEC = {
    "coins": ["mint", "name", "symbol", "pool", "migrated_at", "creator", "creator_balance_at_migration", "holders"],
    "scans": ["id", "mint", "ts", "liquidity_usd", "top10_pct", "h1_change_pct", "age_min", "verdict", "reason"],
    "audits": ["id", "mint", "ts", "layer", "name", "value_text", "pass", "source"],
    "verdicts": ["id", "mint", "ts", "alive_6h", "organic", "flush", "chase", "score", "pass", "reason", "input_tokens"],
    "signals": ["id", "mint", "ts", "price", "size_usd", "stack_pct", "link", "mode", "status"],
    "positions": ["id", "mint", "opened_at", "entry_price", "size_usd", "peak_price", "stop_price", "trail_active",
                  "closed_at", "exit_price", "exit_reason", "pnl_usd", "fees_usd", "mode"],
    "activity": ["id", "ts", "kind", "mint", "agent", "title", "subtitle", "value"],
    "prices": ["mint", "ts", "price", "volume_m5"],
    "balance": ["ts", "value"],
    "settings": ["key", "value", "updated_at"],
    "change_log": ["ts", "key", "old", "new", "who"],
    "skips": ["mint", "until"],
    "heartbeats": ["agent", "ts"],
    "push_subs": ["endpoint", "p256dh", "auth", "created_at"],
}

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def connect(path=DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(path, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.executescript(SCHEMA)
    return c

def table_columns(c, table) -> list[str]:
    return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]

def schema_matches(c) -> bool:
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    return tables == set(SPEC) and all(table_columns(c, t) == cols for t, cols in SPEC.items())

# ---- small helpers -------------------------------------------------------
def heartbeat(c, agent):
    c.execute("INSERT INTO heartbeats(agent,ts) VALUES(?,?) ON CONFLICT(agent) DO UPDATE SET ts=excluded.ts",
              (agent, now_iso()))

def get_setting(c, key, default=None):
    r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default

def set_setting(c, key, value, who="system"):
    old = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    new = json.dumps(value); ts = now_iso()
    if old and old["value"] == new: return
    c.execute("INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (key, new, ts))
    c.execute("INSERT INTO change_log(ts,key,old,new,who) VALUES(?,?,?,?,?)",
              (ts, key, old["value"] if old else None, new, who))

def activity(c, kind, agent, title, subtitle=None, value=None, mint=None):
    if kind not in ACTIVITY_KINDS: raise ValueError(f"activity.kind must be one of {ACTIVITY_KINDS}")
    c.execute("INSERT INTO activity(ts,kind,mint,agent,title,subtitle,value) VALUES(?,?,?,?,?,?,?)",
              (now_iso(), kind, mint, agent, title, subtitle, value))

def add_balance(c, value, ts=None):
    c.execute("INSERT INTO balance(ts,value) VALUES(?,?)", (ts or now_iso(), round(float(value), 6)))

def last_balance(c):
    r = c.execute("SELECT value FROM balance ORDER BY ts DESC, rowid DESC LIMIT 1").fetchone()
    return r["value"] if r else None

def is_skipped(c, mint) -> bool:
    return c.execute("SELECT 1 FROM skips WHERE mint=? AND until>?", (mint, now_iso())).fetchone() is not None

# ---- db.prices: exactly 1 DexScreener batch per writer, never a 2nd source ----
PRICE_WRITERS = {
    "scanner": 60,   # waiting room
    "risk": 15,      # open positions
    "api": 60,       # coins pushed/audited/opened in the app tonight that neither covers
}

def write_price_batch(c, writer: str, pairs: list[dict], ts: str | None = None) -> int:
    """Write one DexScreener /tokens/v1/solana/{mints} response (list of pairs) to db.prices.
    One row per mint (the pumpswap pair, else the first pair for that mint), one shared ts.
    No other source may call this; pass the raw DexScreener list, nothing derived."""
    if writer not in PRICE_WRITERS: raise ValueError(f"unknown price writer {writer!r}")
    if not isinstance(pairs, list): raise TypeError("pairs must be the raw DexScreener list")
    ts = ts or now_iso()
    best: dict[str, dict] = {}
    for p in pairs:
        mint = (p.get("baseToken") or {}).get("address")
        if not mint: continue
        if mint not in best or (p.get("dexId") == "pumpswap" and best[mint].get("dexId") != "pumpswap"):
            best[mint] = p
    rows = []
    for mint, p in best.items():
        price = p.get("priceUsd")
        rows.append((mint, ts, float(price) if price is not None else None, (p.get("volume") or {}).get("m5")))
    c.execute("BEGIN")
    try:
        c.executemany("INSERT INTO prices(mint,ts,price,volume_m5) VALUES(?,?,?,?)", rows)
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK"); raise
    return len(rows)
