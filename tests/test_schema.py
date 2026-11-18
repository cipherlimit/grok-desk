import sys, pathlib, sqlite3; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pytest, db

SPEC = {  # STEP 4, written out independently of db.SPEC
    "coins": "mint name symbol pool migrated_at creator creator_balance_at_migration holders chain",
    "scans": "id mint ts liquidity_usd top10_pct h1_change_pct age_min verdict reason chain",
    "audits": "id mint ts layer name value_text pass source chain",
    "verdicts": "id mint ts alive_6h organic flush chase score pass reason input_tokens",
    "signals": "id mint ts price size_usd stack_pct link mode status chain",
    "positions": "id mint opened_at entry_price size_usd peak_price stop_price trail_active closed_at exit_price exit_reason pnl_usd fees_usd mode chain",
    "activity": "id ts kind mint agent title subtitle value",
    "prices": "mint ts price volume_m5",
    "balance": "ts value",
    "settings": "key value updated_at",
    "change_log": "ts key old new who",
    "skips": "mint until",
    "heartbeats": "agent ts",
    "push_subs": "endpoint p256dh auth created_at",
}
PKS = {"coins": ["mint"], "settings": ["key"], "skips": ["mint"], "heartbeats": ["agent"], "push_subs": ["endpoint"]}

@pytest.fixture
def c(tmp_path): return db.connect(tmp_path / "s.db")

def test_exact_tables(c):
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    assert tables == set(SPEC)

@pytest.mark.parametrize("table", sorted(SPEC))
def test_table_info_matches_spec(c, table):
    info = list(c.execute(f"PRAGMA table_info({table})"))
    assert [r[1] for r in info] == SPEC[table].split()
    pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
    if "id" in SPEC[table].split():
        assert pk == ["id"]
        assert "AUTOINCREMENT" in c.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0].upper()
    else:
        assert pk == PKS.get(table, [])

def test_schema_matches_helper(c):
    assert db.schema_matches(c) and db.SPEC == {k: v.split() for k, v in SPEC.items()}

def test_activity_kind_check(c):
    for k in db.ACTIVITY_KINDS: db.activity(c, k, "risk", "x")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO activity(ts,kind) VALUES('t','online')")
    with pytest.raises(ValueError): db.activity(c, "online", "main", "x")

def test_mint_ts_indexes(c):
    for t in ("scans", "audits", "verdicts", "signals", "activity", "prices"):
        idx = [r[1] for r in c.execute(f"PRAGMA index_list({t})")]
        assert any([r[2] for r in c.execute(f"PRAGMA index_info({i})")] == ["mint", "ts"] for i in idx), t

def test_one_open_position_per_mint(c):
    q = "INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,mode) VALUES('M','t',1,1,1,0.94,'PAPER')"
    c.execute(q)
    with pytest.raises(sqlite3.IntegrityError): c.execute(q)
    c.execute("UPDATE positions SET closed_at='t2'"); c.execute(q)

def test_price_batch_one_writer(c):
    pairs = [{"dexId": "pumpfun", "baseToken": {"address": "A"}, "priceUsd": "1", "volume": {"m5": 1}},
             {"dexId": "pumpswap", "baseToken": {"address": "A"}, "priceUsd": "2", "volume": {"m5": 5}},
             {"dexId": "pumpswap", "baseToken": {"address": "B"}, "priceUsd": "3"}]
    assert db.write_price_batch(c, "risk", pairs, ts="T") == 2
    rows = {r["mint"]: (r["ts"], r["price"], r["volume_m5"]) for r in c.execute("SELECT * FROM prices")}
    assert rows == {"A": ("T", 2.0, 5), "B": ("T", 3.0, None)}
    with pytest.raises(ValueError): db.write_price_batch(c, "rugcheck", pairs)

def test_settings_change_log(c):
    db.set_setting(c, "risk.stop_pct", 6, who="tg"); db.set_setting(c, "risk.stop_pct", 5, who="tg")
    rows = list(c.execute("SELECT key,old,new,who FROM change_log"))
    assert [tuple(r) for r in rows] == [("risk.stop_pct", None, "6", "tg"), ("risk.stop_pct", "6", "5", "tg")]

def test_balance_helpers(c):
    assert db.last_balance(c) is None
    db.add_balance(c, 200, ts="2026-11-16T12:00:00Z"); assert db.last_balance(c) == 200
