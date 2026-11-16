"""SQLite in WAL mode. Schema per STEP 4 (reconstructed from the agent specs; see README)."""
import json, sqlite3, time
from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS heartbeats (agent TEXT PRIMARY KEY, ts TEXT NOT NULL, status TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS coins (
  mint TEXT PRIMARY KEY, name TEXT, symbol TEXT, pool TEXT, signature TEXT, creator TEXT,
  graduated_at TEXT, first_seen TEXT NOT NULL, raw TEXT);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, ts TEXT NOT NULL,
  passed INTEGER NOT NULL, failed TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS audits (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, ts TEXT NOT NULL,
  layers TEXT NOT NULL, passed INTEGER NOT NULL, failed TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS analyses (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL UNIQUE, ts TEXT NOT NULL,
  state_hash TEXT, alive_6h REAL, organic REAL, flush REAL, chase REAL,
  score INTEGER, passed INTEGER NOT NULL, failed TEXT, model TEXT, input_tokens INTEGER);
CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, night TEXT NOT NULL, ts TEXT NOT NULL,
  signal_price REAL, size_usd REAL, stack_pct REAL, cap_hit TEXT, link TEXT,
  mode TEXT NOT NULL, status TEXT NOT NULL, reason TEXT, UNIQUE(mint, night));
CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, name TEXT, mode TEXT NOT NULL,
  entry_price REAL NOT NULL, size_usd REAL NOT NULL, peak_price REAL NOT NULL,
  stop_pct REAL NOT NULL, stop_line REAL NOT NULL, trail_active INTEGER NOT NULL DEFAULT 0,
  opened_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',   -- open|exiting|closed
  exit_reason TEXT, exit_price REAL, closed_at TEXT, pnl_usd REAL, pct REAL, fees_usd REAL,
  link TEXT, no_price_polls INTEGER NOT NULL DEFAULT 0, last_push_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_per_mint ON positions(mint) WHERE status != 'closed';
CREATE TABLE IF NOT EXISTS prices (
  id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, ts TEXT NOT NULL,
  price_usd REAL, volume_m5 REAL, liquidity_usd REAL);
CREATE INDEX IF NOT EXISTS prices_mint_ts ON prices(mint, ts);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, agent TEXT NOT NULL,
  title TEXT NOT NULL, subtitle TEXT, value TEXT, extra TEXT, mint TEXT);
CREATE TABLE IF NOT EXISTS balance (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, mode TEXT NOT NULL,
  balance REAL NOT NULL, reason TEXT);
CREATE TABLE IF NOT EXISTS pushes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, channel TEXT NOT NULL,
  kind TEXT, mint TEXT, text TEXT, ok INTEGER NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS subscriptions (endpoint TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
"""

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def connect(path=DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(path, timeout=10, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(SCHEMA)
    return c

def heartbeat(c, agent, status="ok", detail=None):
    c.execute("INSERT INTO heartbeats(agent,ts,status,detail) VALUES(?,?,?,?) "
              "ON CONFLICT(agent) DO UPDATE SET ts=excluded.ts,status=excluded.status,detail=excluded.detail",
              (agent, now_iso(), status, detail))

def get_setting(c, key, default=None):
    r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default

def set_setting(c, key, value):
    c.execute("INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
              (key, json.dumps(value), now_iso()))

def activity(c, agent, title, subtitle=None, value=None, extra=None, mint=None):
    c.execute("INSERT INTO activity(ts,agent,title,subtitle,value,extra,mint) VALUES(?,?,?,?,?,?,?)",
              (now_iso(), agent, title, subtitle, value, extra, mint))
