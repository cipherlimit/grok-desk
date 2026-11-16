"""Plan B publisher. Every publish_every_s (180s): pack output/*.json, encrypt with DATA_KEY
(AES-256-CBC, PBKDF2, openssl), send as repository_dispatch to the Pages workflow.
The workflow decrypts with the DATA_KEY Actions secret and places files under
/d/{DATA_PATH}/ (DATA_PATH is an Actions secret). Nothing data-related ever enters git."""
import base64, io, json, os, subprocess, sys, tarfile, tempfile, time
from config import OUT_DIR, LOG_DIR, secret
from agents.base import agent_logger

REPO = os.environ.get("DESK_REPO", "cipherlimit/grok-desk")
EVERY = int(os.environ.get("PUBLISH_EVERY_S", "180"))
MAX_BLOB = 100_000
log = agent_logger("publisher")

def pack() -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for n in ("state.json", "chart.json", "activity.json"):
            p = OUT_DIR / n
            if p.exists(): t.add(p, arcname=n)
    env = dict(os.environ, DATA_KEY=secret("DATA_KEY"))
    enc = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "100000", "-salt",
                          "-pass", "env:DATA_KEY"], input=buf.getvalue(), capture_output=True, env=env, check=True).stdout
    return base64.b64encode(enc).decode()

def publish_once() -> bool:
    blob = pack()
    if len(blob) > MAX_BLOB:
        log.error("blob too big: %d bytes", len(blob)); return False
    body = {"event_type": "publish", "client_payload": {"blob": blob, "ts": int(time.time())}}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(body, f); path = f.name
    try:
        r = subprocess.run(["gh", "api", "-X", "POST", f"repos/{REPO}/dispatches", "--input", path],
                           capture_output=True, text=True, timeout=30)
    finally:
        os.unlink(path)
    ok = r.returncode == 0
    log.info("dispatch %s blob=%dB %s", "ok" if ok else "FAIL", len(blob), "" if ok else r.stderr.strip()[:300])
    return ok

if __name__ == "__main__":
    if "--once" in sys.argv:
        sys.exit(0 if publish_once() else 1)
    while True:
        try: publish_once()
        except Exception as e: log.exception("publish error: %s", e)
        time.sleep(EVERY)
