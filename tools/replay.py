"""STEP 13 test 6: run the live migrations recorded so far (up to the last 2 hours) through PAPER,
in a fresh temp db, with the live sources (DexScreener, RugCheck, Helius RPC) as they answer NOW.
Telegram is replaced by a recorder: nothing is sent. Agent logs go to logs/acceptance/replay_logs/.
Prints the Activity and a summary, and how long the recorded window covers."""
import asyncio, os, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
os.environ["DESK_LOG_DIR"] = str(ROOT / "logs" / "acceptance" / "replay_logs")
sys.path.insert(0, str(ROOT))
import db, telegram, links
from bus import Bus
from config import rpc_url_for
from sources import Sources, DEX_BATCH, SourceError
from agents import scanner, auditor, analyst, executor, risk

PUSHES = []
async def record(text, buttons=None, retries=3):
    PUSHES.append((text, buttons)); return True
telegram.send = record
# every agent calls telegram.send through the same module: nothing leaves the box

async def main(hours=2.0):
    live = db.connect()
    rows = list(live.execute("SELECT * FROM coins WHERE migrated_at IS NOT NULL ORDER BY migrated_at"))
    if not rows: print("no recorded migrations"); return
    last = db.parse_iso(rows[-1]["migrated_at"])
    rows = [r for r in rows if db.parse_iso(r["migrated_at"]) >= last - hours * 3600]
    span_min = (last - db.parse_iso(rows[0]["migrated_at"])) / 60
    tmp = Path(tempfile.mkdtemp(prefix="desk_replay_")) / "replay.db"
    c = db.connect(tmp); db.seed_settings(c); db.add_balance(c, db.settings_all(c)["desk.paper_start_stack"])
    for r in rows:
        c.execute("INSERT INTO coins(mint,name,symbol,pool,migrated_at,creator,creator_balance_at_migration) VALUES(?,?,?,?,?,?,?)",
                  (r["mint"], r["name"], r["symbol"], r["pool"], r["migrated_at"], r["creator"], r["creator_balance_at_migration"]))
    c.commit()
    src = Sources(lambda: rpc_url_for("helius"), lambda: 250)
    bus = Bus()
    sc, au, an, ex, rk = (scanner.Scanner(c, bus, src), auditor.Auditor(c, bus, src), analyst.Analyst(c, bus, src),
                          executor.Executor(c, bus, src), risk.Risk(c, bus, src))
    t0 = time.time()
    mints = [r["mint"] for r in rows]; pairs = []
    for i in range(0, len(mints), DEX_BATCH):
        try: got = await src.dex_tokens(mints[i:i + DEX_BATCH])
        except SourceError as e: print("dexscreener error", e); got = []
        db.write_price_batch(c, "scanner", got); pairs += got
    s = db.settings_all(c); now = time.time()
    for r in c.execute("SELECT * FROM coins ORDER BY migrated_at").fetchall():
        await sc.judge(r, pairs, s, now)
    async def drain(topic, fn):
        q = bus.queues[topic]
        while not q.empty(): await fn(q.get_nowait())
    await drain("auditor", au.audit); await drain("analyst", an.analyze); await drain("executor", ex.handle)
    if db.open_positions(c): await rk.poll()
    c.commit()
    # ---- report ----
    sc_rows = c.execute("SELECT verdict, COUNT(*) n FROM scans GROUP BY verdict").fetchall()
    print(f"recorded window: {rows[0]['migrated_at']} -> {rows[-1]['migrated_at']} UTC = {span_min:.0f} min "
          f"({'under' if span_min < 120 else 'full'} 2 hours), {len(rows)} migrations, replayed in {time.time() - t0:.0f}s")
    print("scanner:", {r["verdict"]: r["n"] for r in sc_rows}, "| not judged (DexScreener has no pool / still too young):",
          len(rows) - sum(r["n"] for r in sc_rows))
    print("auditor:", c.execute("SELECT COUNT(DISTINCT mint) FROM audits").fetchone()[0], "taken apart,",
          c.execute("SELECT COUNT(DISTINCT mint) FROM audits WHERE pass=0").fetchone()[0], "red")
    print("analyst:", c.execute("SELECT COUNT(*) FROM verdicts").fetchone()[0], "scored,",
          c.execute("SELECT COUNT(*) FROM verdicts WHERE pass=1").fetchone()[0], "passed")
    print("executor:", c.execute("SELECT COUNT(*) FROM positions").fetchone()[0], "PAPER positions,",
          c.execute("SELECT COUNT(*) FROM signals WHERE status='skipped'").fetchone()[0], "skipped")
    print("pushes recorded (not sent):", len(PUSHES))
    for t, b in PUSHES: print("  push:", t.replace("\n", " | "), "|", [x.get("url") for row in (b or []) for x in row if x.get("url")])
    print("\nActivity:")
    acts = c.execute("SELECT ts,kind,title,subtitle,value,mint FROM activity ORDER BY id").fetchall()
    if not acts: print("  Nothing yet tonight")
    for a in acts: print(f"  {db.local_hhmm(a['ts'])} CET  {a['title']} / {a['subtitle']} / {a['value']}  {links.coin_url(a['mint'], 'solana')}")
    print("\nScanner kills:")
    for r in c.execute("SELECT s.reason, c.name, s.mint FROM scans s JOIN coins c USING(mint) WHERE verdict='kill'"):
        print(f"  {r['name']}: {r['reason']}")
    await src.http.aclose()

if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]) if len(sys.argv) > 1 else 2.0))
