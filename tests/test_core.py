import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import links, fees, db
from config import DEFAULTS as S

def test_links_carry_referral():
    u = links.coin_url("Fz4HmKzMZ9C3LGENsiEd6sJ9r23uQHcEmzhEF8wnpump", "solana")
    assert u == "https://fomo.family/coin?address=Fz4HmKzMZ9C3LGENsiEd6sJ9r23uQHcEmzhEF8wnpump&chainId=1399811149&r=savipww&source=share_link"
    assert links.profile_url("CipherLimit").endswith("CipherLimit?source=share_link&r=savipww")

def test_fees():
    assert abs(fees.FEE - 0.0045) < 1e-12
    pnl, f = fees.paper_pnl(100, 1.0, 1.0)
    assert pnl < 0 and abs(f - 0.897975) < 1e-6

def test_db_wal(tmp_path):
    c = db.connect(tmp_path / "t.db")
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
