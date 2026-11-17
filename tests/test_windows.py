"""Portable pieces used by the Windows laptop: supervisor, publisher crypto, moved marker."""
import base64, hashlib, os, subprocess, sys, shutil
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def test_publisher_encrypt_is_openssl_format(monkeypatch):
    monkeypatch.setenv("DATA_KEY", "unit-test-key")
    import publisher
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    raw = os.urandom(3001)
    b = base64.b64decode(publisher._encrypt(raw))
    assert b[:8] == b"Salted__"
    kiv = hashlib.pbkdf2_hmac("sha256", b"unit-test-key", b[8:16], 100000, 48)
    d = Cipher(algorithms.AES(kiv[:32]), modes.CBC(kiv[32:])).decryptor()
    out = d.update(b[16:]) + d.finalize()
    assert out[:-out[-1]] == raw
    if shutil.which("openssl"):
        r = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "100000",
                            "-pass", "env:DATA_KEY"], input=b, capture_output=True, env=dict(os.environ))
        assert r.stdout == raw

def test_supervisor_and_run_ps1_present():
    s = (ROOT / "supervisor.py").read_text()
    for k in ("main.py", "publisher.py", "KILL_ON_JOB_CLOSE", "supervisor.lock", "MOVED_TO"):
        assert k in s
    ps = (ROOT / "run.ps1").read_text()
    assert "GrokDesk" in ps and "supervisor.py" in ps

def test_supervisor_respects_moved(tmp_path):
    d = tmp_path / "desk"; d.mkdir()
    shutil.copy(ROOT / "supervisor.py", d)
    (d / "MOVED_TO").write_text("LenovoYoga\n")
    env = {k: v for k, v in os.environ.items() if k != "DESK_LOG_DIR"}
    r = subprocess.run([sys.executable, "supervisor.py"], cwd=d, capture_output=True, text=True, timeout=30, env=env)
    assert r.returncode == 0
    assert "not starting" in (d / "logs" / "supervisor.log").read_text()
