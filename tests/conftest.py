import sys, json, pathlib, asyncio, os, tempfile
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DESK_LOG_DIR"] = tempfile.mkdtemp(prefix="desk-test-logs-")   # tests never touch logs/
import pytest, db, telegram, sources
from sources import SourceError, RateLimited

RAW = ROOT / "logs" / "raw"   # samples are read from the real tree
def raw(name):
    d = json.loads((RAW / name).read_text())
    if isinstance(d, dict) and "response" in d and "meta" in d: return d["response"]
    if isinstance(d, dict) and "body" in d and "http" in d: return d["body"]
    return d

PIKA = "Fz4HmKzMZ9C3LGENsiEd6sJ9r23uQHcEmzhEF8wnpump"
PIKA_POOL = "HmupEayo4NJMKXpCdJq7S3MmMkvKDvzFoC3FPnTU8Bcs"
MARVIN = "HkRQ6Qcadi5yuoNpZdA1Q7yVF3r7LdEmAw1aKkSxpump"

class FakeSrc:
    """Answers from raw samples. Any value may be an Exception (raised every call) or a list
    of results/exceptions consumed one per call."""
    def __init__(self, **kw):
        self.r = kw; self.calls = []
    def _get(self, key):
        self.calls.append(key)
        v = self.r.get(key)
        if isinstance(v, Seq): v = v.next()
        if isinstance(v, Exception): raise v
        if v is None: raise SourceError(f"no fake for {key}")
        return v
    async def dex_tokens(self, mints): return self._get("dex")
    async def rug_report(self, mint): return self._get("report")
    async def rug_graph(self, mint): return self._get("graph")
    async def rpc(self, method, params): return self._get(method)

class Seq:
    def __init__(self, *items): self.items = list(items)
    def next(self): return self.items.pop(0) if len(self.items) > 1 else self.items[0]

@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db"); db.seed_settings(c); return c

@pytest.fixture
def sleeps(monkeypatch):
    rec = []
    real = asyncio.sleep
    async def fake(s, *a, **k):
        rec.append(s); await real(0)
    monkeypatch.setattr(asyncio, "sleep", fake)
    return rec

@pytest.fixture
def pushes(monkeypatch):
    sent = []
    async def fake_send(text, buttons=None, retries=3):
        sent.append((text, buttons)); return True
    monkeypatch.setattr(telegram, "send", fake_send)
    return sent

def run(coro): return asyncio.run(coro)
