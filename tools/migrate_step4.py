"""One-off STEP 4 migration: back up desk.db, recreate with the STEP 4 schema,
carry over settings and balance rows (old balance(ts,mode,balance,reason) -> (ts,value)).
Old rows were PAPER test data only. Stop main.py before running."""
import shutil, sqlite3, sys, time, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import db
from config import DB_PATH, ROOT

if not DB_PATH.exists():
    db.connect(); print("fresh db created"); sys.exit(0)
old = sqlite3.connect(DB_PATH); old.row_factory = sqlite3.Row
old.execute("PRAGMA wal_checkpoint(TRUNCATE)")
if db.schema_matches(old): print("already on STEP 4 schema"); sys.exit(0)
cols = lambda t: [r[1] for r in old.execute(f"PRAGMA table_info({t})")]
bal_cols = cols("balance")
if "value" in bal_cols: bal = [(r["ts"], r["value"]) for r in old.execute("SELECT ts,value FROM balance")]
else: bal = [(r["ts"], r["balance"]) for r in old.execute("SELECT ts,balance FROM balance WHERE mode='PAPER' ORDER BY id")]
sett = [tuple(r) for r in old.execute("SELECT key,value,updated_at FROM settings")] if "settings" in {r[0] for r in old.execute("SELECT name FROM sqlite_master")} else []
old.close()
bdir = ROOT / "backups"; bdir.mkdir(exist_ok=True)
bak = bdir / f"desk.db.{time.strftime('%Y%m%d-%H%M%S')}.bak"
shutil.copy2(DB_PATH, bak)
for suf in ("", "-wal", "-shm"):
    p = pathlib.Path(str(DB_PATH) + suf)
    if p.exists(): p.unlink()
c = db.connect()
c.executemany("INSERT INTO balance(ts,value) VALUES(?,?)", bal)
c.executemany("INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)", sett)
assert db.schema_matches(c)
print(f"backup {bak.name}; kept {len(bal)} balance rows (last {bal[-1][1] if bal else None}), {len(sett)} settings")
