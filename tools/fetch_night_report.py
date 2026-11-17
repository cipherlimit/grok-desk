"""Box night-report while the desk runs on LenovoYoga. The laptop's GrokNightReport task (08:01) runs
night_report.py --clear-skips and publisher.py --once --with-report; this downloads
night_report_<date>.json from Pages (via DATA_PATH, never printed) and writes
output/night_report_<date>.md (+ .json) with report.to_markdown. Prints the paths on one line,
like night_report.py.
  --date YYYY-MM-DD   night to fetch (default: the last completed night)
  --wait-min N        keep retrying every 30 s for up to N minutes (Pages deploy lag), default 10"""
import argparse, datetime as dt, json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import db, report
from config import OUT_DIR
from pages_live import get_json, PagesError

def last_night_date() -> str:
    start, _ = db.night_bounds()
    prev = dt.datetime.fromisoformat(start.replace("Z", "+00:00")) - dt.timedelta(seconds=1)
    s, _ = db.night_bounds(prev)
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(db._tz()).date().isoformat()

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date"); ap.add_argument("--wait-min", type=float, default=10)
    a = ap.parse_args(argv)
    day = a.date or last_night_date()
    deadline = time.time() + a.wait_min * 60
    while True:
        try:
            d = get_json(f"night_report_{day}.json"); break
        except PagesError as e:
            if time.time() >= deadline:
                print(f"error: {e}"); return 2
            time.sleep(30)
    if d.get("date_iso") != day:
        print(f"error: report date {d.get('date_iso')} != {day}"); return 2
    OUT_DIR.mkdir(exist_ok=True)
    md = OUT_DIR / f"night_report_{day}.md"; js = md.with_suffix(".json")
    md.write_text(report.to_markdown(d), encoding="utf-8"); js.write_text(json.dumps(d, indent=1), encoding="utf-8")
    n = len(d["red_trades"]) + len(d["green_trades"])
    print(f"night {d['date']}: {n} trades, PnL {d['pnl_usd']:+.2f}, {len(d['coins_seen'])} coins seen -> {md} {js}"
          " · source: LenovoYoga via Pages")
    return 0

if __name__ == "__main__":
    sys.exit(main())
