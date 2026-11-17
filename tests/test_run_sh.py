"""run.sh must detach the desk from the launching session and /agent cgroup."""
from pathlib import Path
import subprocess, os, sys, pytest
pytestmark = pytest.mark.skipif(os.name == "nt", reason="run.sh is the box (bash) launcher")

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "run.sh"

def test_run_sh_is_valid_bash():
    subprocess.run(["bash", "-n", str(RUN)], check=True)

def test_run_sh_detaches_from_agent_session():
    text = RUN.read_text()
    assert "setsid" in text
    assert "/sys/fs/cgroup/background" in text
    assert "start_daemon" in text
    assert "main.py" in text and "publisher.py" in text
