"""STEP 13 runner: each test's full output -> logs/acceptance/test<N>_<name>.txt, one line per test -> summary.txt.
  --send-1b   also send the 1 PAPER test push through Telegram (test 1b). Without it 1b is not run."""
import asyncio, json, os, re, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "logs" / "acceptance"; OUT.mkdir(parents=True, exist_ok=True)
PY = str(ROOT / ".venv" / "bin" / "python")

def sh(args, name, timeout=900, env=None):
    t = time.time()
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})})
    txt = f"$ {' '.join(args)}\n(exit {r.returncode}, {time.time() - t:.0f}s, {time.strftime('%Y-%m-%d %H:%M:%S %Z')})\n\n{r.stdout}\n{r.stderr}"
    (OUT / f"{name}.txt").write_text(txt)
    return r

def pytest_line(r):
    m = re.findall(r"=+ (.*?) in [\d.]+s", r.stdout)
    return m[-1] if m else r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "no output"

def send_1b():
    sys.path.insert(0, str(ROOT))
    import db, telegram, links
    c = db.connect()
    reply, buttons = telegram.Desk(c, None, None).cmd_test()
    url = buttons[0][0]["url"]
    assert url == links.coin_url(c.execute("SELECT mint FROM coins ORDER BY migrated_at DESC LIMIT 1").fetchone()[0]) or url == links.SIGNUP_URL
    ok = asyncio.run(telegram.send(reply, buttons, retries=0))       # exactly 1 message, no retries
    line = f"sent={ok} text={reply.splitlines()[0]!r} button={buttons[0][0]['text']!r} url_has_ref={'r=savipww' in url} url={url}"
    (OUT / "test1b_notify.txt").write_text(line + "\n\nfull text:\n" + reply + "\n")
    return ok and "r=savipww" in url, line

def main():
    res = []
    def add(n, name, ok, line): res.append(f"{n:<3} {name:<14} {'PASS' if ok else 'FAIL'}  {line}")
    r = sh([PY, "-m", "pytest", "-v", "-p", "no:cacheprovider", "tests/test_links.py"], "test1_links"); add("1", "test_links", r.returncode == 0, pytest_line(r))
    if "--send-1b" in sys.argv:
        ok, line = send_1b(); add("1b", "test_notify", ok, line)
    r = sh([PY, "-m", "pytest", "-v", "-p", "no:cacheprovider", "tests/test_gate.py"], "test2_gate"); add("2", "test_gate", r.returncode == 0, pytest_line(r))
    r = sh([PY, "-m", "pytest", "-v", "-p", "no:cacheprovider", "tests/test_risk.py"], "test3_risk"); add("3", "test_risk", r.returncode == 0, pytest_line(r))
    r = sh([PY, "-m", "pytest", "-v", "-p", "no:cacheprovider", "tests/test_executor.py"], "test4_executor"); add("4", "test_executor", r.returncode == 0, pytest_line(r))
    r = sh([PY, "-m", "pytest", "-v", "-p", "no:cacheprovider", "tests/test_auditor.py"], "test5_auditor_freeze"); add("5", "auditor freeze", r.returncode == 0, pytest_line(r))
    r = sh([PY, "-m", "pytest", "-v", "-p", "no:cacheprovider", "tests/test_replay.py"], "test6_replay_offline")
    r2 = sh([PY, "tools/replay.py"], "test6_replay_live")
    first = next((l for l in r2.stdout.splitlines() if l.startswith("recorded window")), "no window line")
    add("6", "test_replay", r.returncode == 0 and r2.returncode == 0, first + " · offline: " + pytest_line(r))
    r = sh([PY, "tools/secret_check.py"], "test7_secrets", timeout=120)
    site = next((l for l in r.stdout.splitlines() if l.startswith("site:")), "")
    add("7", "secrets", r.returncode == 0, ("clean in web/ logs/ output/ repo + git history · " + site) if r.returncode == 0 else r.stdout[-300:])
    r = sh([PY, "tools/acceptance_screens.py"], "test8_screens", timeout=600)
    last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-200:]
    add("8", "screenshots", r.returncode == 0 and "True" in last, last + " -> logs/acceptance/screens/")
    r = sh([PY, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/"], "all_unit_tests"); add("-", "all tests", r.returncode == 0, pytest_line(r))
    s = f"STEP 13 acceptance · {time.strftime('%Y-%m-%d %H:%M %Z')}\n" + "\n".join(res) + "\n"
    (OUT / "summary.txt").write_text(s); print(s)

if __name__ == "__main__":
    main()
