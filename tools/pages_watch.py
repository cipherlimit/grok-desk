"""Box desk-health while the desk runs on LenovoYoga: is the live app data fresh?
Fetches state.json from Pages (via DATA_PATH, never printed). Exit 0 "ok: ts N min old",
exit 1 "stale N min" when ts is older than --max-min (15), exit 2 with a short error."""
import argparse, datetime as dt, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pages_live import get_json, PagesError

def age_min(ts: str, now: dt.datetime | None = None) -> float:
    t = dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return ((now or dt.datetime.now(dt.timezone.utc)) - t).total_seconds() / 60

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--max-min", type=float, default=15)
    a = ap.parse_args(argv)
    try:
        st = get_json("state.json")
        m = age_min(st["ts"])
    except PagesError as e:
        print(f"error: {e}"); return 2
    except (KeyError, TypeError, ValueError):
        print("error: state.json has no valid ts"); return 2
    if m > a.max_min:
        print(f"stale {int(m)} min"); return 1
    print(f"ok: ts {int(m)} min old, mode {st.get('mode')}"); return 0

if __name__ == "__main__":
    sys.exit(main())
