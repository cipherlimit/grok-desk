"""STEP 13 test 1: every FOMO URL in the repo, the API (plan B JSON), rendered pushes, the Account tab
and the morning Page carries r=savipww (SIGNUP_URL as /r/savipww); coin_url on an unknown chain returns
SIGNUP_URL; no shortened or redirected FOMO URL anywhere. Fail = no deploy."""
import json, re, subprocess, asyncio
from pathlib import Path
import pytest, db, links, api, report, telegram
from conftest import run, PIKA, FakeSrc
from bus import Bus
from agents import executor, risk
from test_agents import _coin, pika_pair, ago

ROOT = Path(__file__).resolve().parents[1]
FOMO = re.compile(r"https?://(?:www\.)?fomo\.family[^\s\"'<>()\[\]|]*")
COIN_RE = re.compile(r"^https://fomo\.family/coin\?address=[1-9A-HJ-NP-Za-km-z]{32,44}&chainId=\d+&r=savipww&source=share_link$")
PROF_RE = re.compile(r"^https://fomo\.family/profile/[^/?]+\?source=share_link&r=savipww$")
SHORT = re.compile(r"(?:\bt\.co/|bit\.ly|tinyurl\.com|goo\.gl|ow\.ly|lnkd\.in|rebrand\.ly|cutt\.ly|is\.gd/|buff\.ly|shorturl\.at|rb\.gy|t\.ly/)", re.I)

def ok_url(u: str) -> bool:
    u = u.replace("&amp;", "&")
    return u == links.SIGNUP_URL or bool(COIN_RE.match(u)) or bool(PROF_RE.match(u))

def check_text(text: str, where: str):
    urls = FOMO.findall(text)
    bad = [u for u in urls if not ok_url(u)]
    assert not bad, f"{where}: FOMO URL without the exact links.py shape: {bad[:3]}"
    assert not SHORT.search(text), f"{where}: shortened link"
    return len(urls)

def test_links_shapes():
    u = links.coin_url(PIKA, "solana")
    assert u == "https://fomo.family/coin?address=Fz4HmKzMZ9C3LGENsiEd6sJ9r23uQHcEmzhEF8wnpump&chainId=1399811149&r=savipww&source=share_link"
    assert links.profile_url("@CipherLimit") == "https://fomo.family/profile/CipherLimit?source=share_link&r=savipww"
    assert links.SIGNUP_URL == "https://fomo.family/r/savipww"
    assert links.coin_url(PIKA, "dogechain") == links.SIGNUP_URL
    for ch, cid in links.CHAIN_ID.items(): assert f"chainId={cid}&r=savipww" in links.coin_url(PIKA, ch)

def test_repo_grep():
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    holders = []
    for f in files:
        p = ROOT / f
        if not p.is_file() or p.suffix in (".png", ".ico"): continue
        t = p.read_text(errors="ignore")
        if "fomo.family" in t: holders.append(f)
        assert not SHORT.search(t) or f == "tests/test_links.py", f"shortener in {f}"
        if f != "links.py": check_text(t, f)            # links.py holds the f-string templates themselves
    assert sorted(holders) == ["links.py", "tests/test_links.py"]       # no FOMO URL written anywhere else

def _fixture_out(tmp_path):
    import tools.acceptance_screens as A
    c, _ = A.full_db(tmp_path / "f.db"); api.write_all(c, tmp_path / "out"); return c, tmp_path / "out"

def test_api_json_fixture_and_live(tmp_path):
    c, out = _fixture_out(tmp_path)
    n = 0
    for f in out.rglob("*.json"): n += check_text(f.read_text(encoding="utf-8"), str(f.relative_to(out)))
    assert n > 20
    acc = json.loads((out / "account.json").read_text(encoding="utf-8"))
    assert acc["profile_url"] == links.profile_url("CipherLimit") and acc["signup_url"] == links.SIGNUP_URL
    live = ROOT / "output"
    for f in live.rglob("*.json"): check_text(f.read_text(encoding="utf-8"), "live " + str(f.relative_to(live)))

def test_rendered_pushes(conn, pushes):
    _coin(conn); db.add_balance(conn, 200)
    ex = executor.Executor(conn, Bus(), FakeSrc(dex=[pika_pair(liquidity__usd=5e4, priceUsd="0.000001")]))
    run(ex.handle({"analyst": {"mint": PIKA, "score": 82, "passed": True}, "scanner": {"mint": PIKA, "liquidityUsd": 5e4, "name": "PIKA COIN"},
                   "price_at_analyst": 1e-6, "name": "PIKA COIN"}))
    run(risk.Risk(conn, Bus(), FakeSrc(dex=[pika_pair(priceUsd="0.0000009", liquidity__usd=5e4)])).poll())
    assert len(pushes) == 2
    for text, buttons in pushes:
        urls = [b["url"] for row in buttons for b in row if "url" in b]
        assert urls and all(u == links.coin_url(PIKA, "solana") for u in urls)
        check_text(text + "\n" + json.dumps(buttons), "push")
    d = telegram.Desk(conn, Bus(), None)
    for cmd in ("invite", "status", "why PIKA COIN", "sell PIKA COIN", "buy PIKA COIN", "test"):
        r, b = run(d.handle(cmd)); check_text(r + "\n" + json.dumps(b or []), "reply " + cmd)

def test_account_tab_and_page(tmp_path):
    c, out = _fixture_out(tmp_path)
    s, e = db.night_bounds()
    md = report.to_markdown(report.build(c, s, e))
    assert check_text(md, "page") >= 5 and md.rstrip().endswith("New to FOMO? " + links.SIGNUP_URL)
    html = ROOT / "logs" / "acceptance" / "screens" / "account.html"
    if html.exists():                                 # the rendered Account tab from the screenshot run
        t = html.read_text(encoding="utf-8"); assert check_text(t, "Account tab DOM") >= 2
        assert links.profile_url("CipherLimit").replace("&", "&amp;") in t
    for f in (ROOT / "output").glob("night_report_*.md"): check_text(f.read_text(encoding="utf-8"), f.name)
    for f in (ROOT / "web").rglob("*"):
        if f.is_file() and f.suffix in (".html", ".js", ".css", ".json"): assert "fomo.family" not in f.read_text(encoding="utf-8"), f
