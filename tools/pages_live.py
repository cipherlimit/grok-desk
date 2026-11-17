"""Shared by pages_watch.py / fetch_night_report.py: read plan B files from the live Pages site.
The publisher's blob is AES-encrypted in transit (repository_dispatch); the Pages workflow decrypts it
and serves plain JSON under /d/<DATA_PATH>/, so reading needs only DATA_PATH (from the secrets file).
DATA_PATH and the URL are never printed: errors are reduced to a short, secret-free label."""
import json, os, sys, time, urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import secret

SITE = os.environ.get("DESK_PAGES", "https://cipherlimit.github.io/grok-desk")

class PagesError(Exception):
    pass

def get_json(name: str, timeout=20):
    dp = secret("DATA_PATH")
    if not dp: raise PagesError("no DATA_PATH in secrets")
    url = f"{SITE}/d/{dp}/{name}?nocache={int(time.time())}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Cache-Control": "no-cache"}),
                                    timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise PagesError(f"HTTP {e.code} for {name}") from None
    except urllib.error.URLError as e:
        raise PagesError(f"network error ({type(e.reason).__name__}) for {name}") from None
    except (TimeoutError, OSError) as e:
        raise PagesError(f"network error ({type(e).__name__}) for {name}") from None
    except ValueError:
        raise PagesError(f"bad JSON in {name}") from None
