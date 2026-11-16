import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import links, fees, db
from config import DEFAULTS as S

def test_fees_tiers():
    assert fees.side_fee(16) == 0.288                     # 2% of $16, minus 10%
    assert fees.side_fee(47.5) == 0.855 and fees.side_fee(190) == 0.855   # flat 0.95 minus 10%
    assert fees.side_fee(1000) == 4.5                     # 0.50% minus 10%
    assert fees.side_fee(100, 0) == 0.95
    pnl, f = fees.paper_pnl(100, 1.0, 1.0)
    assert f == 1.71 and pnl == -1.71                     # 0.855 each side, flat tier both ways

def test_db_wal(tmp_path):
    c = db.connect(tmp_path / "t.db")
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
