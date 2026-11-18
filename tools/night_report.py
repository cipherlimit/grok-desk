"""night-report routine script (STEP 12). Builds the night data and writes
output/night_report_<date>.md (+ .json). Prints the paths on one line.
  --tonight       the night still running (default: the last completed night)
  --clear-skips   clear db.skips after writing the report (the routine passes this)"""
import argparse, json, sys, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import db, report
from config import OUT_DIR

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tonight", action="store_true")
    ap.add_argument("--clear-skips", action="store_true")
    a = ap.parse_args()
    c = db.connect()
    start, end = db.night_bounds()
    if not a.tonight:
        prev = dt.datetime.fromisoformat(start.replace("Z", "+00:00")) - dt.timedelta(seconds=1)
        start, end = db.night_bounds(prev)
    d = report.build(c, start, end)
    OUT_DIR.mkdir(exist_ok=True)
    md = OUT_DIR / f"night_report_{d['date_iso']}.md"; js = md.with_suffix(".json")
    md.write_text(report.to_markdown(d), encoding="utf-8"); js.write_text(json.dumps(d, indent=1), encoding="utf-8")
    cleared = 0
    if a.clear_skips:
        cleared = c.execute("DELETE FROM skips").rowcount; c.commit()
    n = len(d["red_trades"]) + len(d["green_trades"])
    print(f"night {d['date']}: {n} trades, PnL {d['pnl_usd']:+.2f}, {len(d['coins_seen'])} coins seen -> {md} {js}"
          + (f" · skips cleared: {cleared}" if a.clear_skips else ""))

if __name__ == "__main__":
    main()
