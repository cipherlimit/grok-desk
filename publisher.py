"""Plan B publisher. Every publish_every_s (180s): pack output/*.json, encrypt with DATA_KEY
(AES-256-CBC, PBKDF2, openssl), send as repository_dispatch to the Pages workflow.
The workflow decrypts with the DATA_KEY Actions secret and places files under
/d/{DATA_PATH}/ (DATA_PATH is an Actions secret). Nothing data-related ever enters git."""
import base64, hashlib, io, json, os, shutil, subprocess, sys, tarfile, tempfile, time
from config import OUT_DIR, LOG_DIR, secret
from agents.base import agent_logger

REPO = os.environ.get("DESK_REPO", "cipherlimit/grok-desk")
EVERY = int(os.environ.get("PUBLISH_EVERY_S", "180"))
MAX_BLOB = 60_000          # keep repository_dispatch payloads small
log = agent_logger("publisher")

WITH_REPORT = False       # --with-report: also ship the newest night_report_*.json, however old
REPORT_KEEP_S = 26 * 3600 # every publish keeps shipping a report this fresh, so the next regular
                          # publish (which redeploys the whole site) does not drop it from Pages

def _files():
    """Every plan B JSON under output/ (recursive), except reports and temp files.
    With WITH_REPORT, the newest night_report_<date>.json rides along (for the night-report routine)."""
    out = []
    for p in sorted(OUT_DIR.rglob("*.json")):
        rel = p.relative_to(OUT_DIR).as_posix()
        if rel.startswith("night_report") or "/." in rel: continue
        out.append((rel, p))
    reps = sorted(OUT_DIR.glob("night_report_*.json"))
    if reps and (WITH_REPORT or time.time() - reps[-1].stat().st_mtime < REPORT_KEEP_S):
        out.append((reps[-1].name, reps[-1]))
    return out

def _encrypt(raw: bytes) -> str:
    """Same bytes format as `openssl enc -aes-256-cbc -pbkdf2 -iter 100000 -salt -pass env:DATA_KEY`
    (Salted__ + 8-byte salt + AES-256-CBC/PKCS7, key+iv = PBKDF2-HMAC-SHA256). Pure Python, so the
    desk needs no openssl binary (Windows)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding
    salt = os.urandom(8)
    kiv = hashlib.pbkdf2_hmac("sha256", secret("DATA_KEY").encode(), salt, 100000, 48)
    padder = padding.PKCS7(128).padder()
    data = padder.update(raw) + padder.finalize()
    enc = Cipher(algorithms.AES(kiv[:32]), modes.CBC(kiv[32:])).encryptor()
    return base64.b64encode(b"Salted__" + salt + enc.update(data) + enc.finalize()).decode()

def _tar(files) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as t:
        for rel, p in files: t.add(p, arcname=rel)
    return buf.getvalue()

def pack() -> str:
    """Fit under MAX_BLOB: drop coin/ files of coins without a position or signal (oldest first),
    then older activity pages, never state/chart/activity/account/settings."""
    files = _files()
    blob = _encrypt(_tar(files))
    if len(blob) <= MAX_BLOB: return blob
    keep_first = {"state.json", "chart.json", "activity.json", "account.json", "settings.json", "coins.json"}
    droppable = sorted([f for f in files if f[0].startswith(("coin/", "audit/", "activity/"))],
                       key=lambda f: (f[0].startswith("activity/") is False, f[1].stat().st_mtime))
    pos_mints = set()
    try:
        st = json.loads((OUT_DIR / "state.json").read_text(encoding="utf-8"))
        pos_mints = {p["mint"] for p in st.get("open_positions", [])}
    except Exception: pass
    droppable = [f for f in droppable if not any(m in f[0] for m in pos_mints)]
    while len(blob) > MAX_BLOB and droppable:
        n = max(1, len(droppable) // 4)
        gone, droppable = droppable[:n], droppable[n:]
        files = [f for f in files if f not in gone]
        blob = _encrypt(_tar(files))
    log.info("trimmed to %d files to fit %dB", len(files), MAX_BLOB)
    return blob

def publish_once() -> bool:
    blob = pack()
    if len(blob) > MAX_BLOB:
        log.error("blob too big: %d bytes", len(blob)); return False
    body = {"event_type": "publish", "client_payload": {"blob": blob, "ts": int(time.time())}}
    ok, err = _dispatch(body)
    log.info("dispatch %s blob=%dB %s", "ok" if ok else "FAIL", len(blob), "" if ok else err[:300])
    return ok

def _dispatch(body: dict) -> tuple[bool, str]:
    """GH_TOKEN (env or secrets file) -> GitHub REST directly; otherwise the gh CLI login.
    The token is only ever sent in the Authorization header, never logged."""
    tok = secret("GH_TOKEN")
    if tok:
        import httpx
        try:
            r = httpx.post(f"https://api.github.com/repos/{REPO}/dispatches", json=body, timeout=30,
                           headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                                    "X-GitHub-Api-Version": "2022-11-28"})
        except httpx.HTTPError as e:
            return False, type(e).__name__
        return r.status_code == 204, f"http {r.status_code} {r.text.strip()[:200]}"
    if not shutil.which("gh"):
        return False, "no GH_TOKEN and no gh CLI"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body, f); path = f.name
    try:
        r = subprocess.run(["gh", "api", "-X", "POST", f"repos/{REPO}/dispatches", "--input", path],
                           capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(path)
    return r.returncode == 0, r.stderr.strip()

if __name__ == "__main__":
    WITH_REPORT = "--with-report" in sys.argv
    if "--once" in sys.argv:
        sys.exit(0 if publish_once() else 1)
    while True:
        try: publish_once()
        except Exception as e: log.exception("publish error: %s", e)
        time.sleep(EVERY)
