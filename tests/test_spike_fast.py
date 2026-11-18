"""2026-11-18 fixes after the first PAPER trade (Digital Cowboys, FGiK544d...KVp, entered 4.639e-05 on a one-tick
spike 10s after 3.401e-05; Risk's next 15s poll saw 3.021e-05 -> the -6% stop closed at -37%).
Fix 1: executor spike guard (executor.max_entry_premium_pct over the median of the last 60s of db.prices -> skip "spike").
Fix 2: Risk polls a position every risk.fast_poll_s for its first risk.fast_window_s, then every risk.poll_s."""
import time, pytest, db, config, links
from conftest import FakeSrc, run
from bus import Bus
from agents import executor, risk
from test_agents import pika_pair, _ex_msg

DC = "FGiK544deW8wswpjHf9U3DxEZ2zytbP2BHBW4LXs5KVp"
SIGNAL_TS = "2026-11-18T17:28:37Z"        # signals.ts of the real entry (laptop desk.db)
# laptop db.prices for DC up to the entry (scanner batches every ~60s)
DC_SERIES = [("2026-11-18T17:18:34Z", 3.258e-05), ("2026-11-18T17:19:34Z", 4.211e-05), ("2026-11-18T17:20:34Z", 4.502e-05),
             ("2026-11-18T17:21:34Z", 6.557e-05), ("2026-11-18T17:22:35Z", 6.935e-05), ("2026-11-18T17:23:34Z", 5.879e-05),
             ("2026-11-18T17:24:34Z", 6.413e-05), ("2026-11-18T17:25:34Z", 5.997e-05), ("2026-11-18T17:26:34Z", 7.1e-05),
             ("2026-11-18T17:27:34Z", 5.375e-05), ("2026-11-18T17:28:33Z", 3.401e-05)]
DC_ENTRY = 4.639e-05

def _load_series(conn, shift_s=0.0):
    iso = lambda ts: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(db.parse_iso(ts) + shift_s))
    conn.executemany("INSERT INTO prices(mint,ts,price,volume_m5) VALUES(?,?,?,NULL)", [(DC, iso(t), p) for t, p in DC_SERIES])

def _dc_msg():
    m = _ex_msg(price_at_analyst=DC_ENTRY); m["analyst"]["mint"] = DC; m["name"] = "Digital Cowboys"; return m

def _dc_src(price):
    return FakeSrc(dex=[pika_pair(baseToken__address=DC, baseToken__name="Digital Cowboys", priceUsd=str(price), liquidity__usd=50_000.0)])

# ---------------- Fix 1 ----------------
def test_settings_defaults_and_validate(conn):
    s = db.settings_all(conn)
    assert (s["executor.max_entry_premium_pct"], s["risk.fast_window_s"], s["risk.fast_poll_s"]) == (15, 180, 5)
    assert config.validate("executor.max_entry_premium_pct", "20%") == 20
    assert config.validate("risk.fast_poll_s", "5") == 5 and config.validate("risk.fast_window_s", 300) == 300
    with pytest.raises(config.SettingError): config.validate("risk.fast_poll_s", 1)      # never hammer DexScreener
    assert config.resolve_key("fast poll") == "risk.fast_poll_s" and config.resolve_key("entry premium") == "executor.max_entry_premium_pct"

def test_reference_at_dc_entry_is_last_scan_price(conn):
    _load_series(conn)
    ref, n, basis = executor.reference_price(conn, DC, db.parse_iso(SIGNAL_TS))
    assert (ref, n, basis) == (3.401e-05, 1, "60s")
    assert executor.spike_check(DC_ENTRY, ref, db.settings_all(conn)) == "spike"          # +36.4% > 15%

def test_replay_digital_cowboys_entry_is_skipped(conn, pushes):
    _load_series(conn, shift_s=time.time() - db.parse_iso(SIGNAL_TS))        # same series, "now" = the real signal time
    db.add_balance(conn, 200)
    out = run(executor.Executor(conn, Bus(), _dc_src(DC_ENTRY)).handle(_dc_msg()))
    assert (out["status"], out["reason"], out["signalPrice"]) == ("skipped", "spike", DC_ENTRY)
    assert not pushes                                                     # no Buy card
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    assert conn.execute("SELECT status FROM signals WHERE mint=?", (DC,)).fetchone()[0] == "skipped"

def test_calm_entry_still_buys(conn, pushes):
    _load_series(conn, shift_s=time.time() - db.parse_iso(SIGNAL_TS))
    db.add_balance(conn, 200)
    out = run(executor.Executor(conn, Bus(), _dc_src(3.5e-05)).handle(_dc_msg()))   # +2.9% over the median
    assert out["status"] == "bought" and pushes and pushes[0][1][0][0]["url"] == links.coin_url(DC)

def test_threshold_is_live_setting(conn, pushes):
    _load_series(conn, shift_s=time.time() - db.parse_iso(SIGNAL_TS))
    db.add_balance(conn, 200); db.set_setting(conn, "executor.max_entry_premium_pct", 40, who="test")
    out = run(executor.Executor(conn, Bus(), _dc_src(DC_ENTRY)).handle(_dc_msg()))
    assert out["status"] == "bought"

def test_fallback_last3_and_stale(conn):
    now = db.parse_iso(SIGNAL_TS)
    _load_series(conn)
    ref, n, basis = executor.reference_price(conn, DC, now + 100)            # nothing in the last 60s -> last 3
    assert (ref, n, basis) == (5.375e-05, 3, "last3")
    assert executor.reference_price(conn, DC, now + 3600) == (None, 0, "none")     # 1h old rows: no reference
    assert executor.spike_check(DC_ENTRY, None, db.settings_all(conn)) is None    # no reference = not checked

def test_median_of_several_in_window(conn):
    now = time.time()
    iso = lambda dt: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - dt))
    conn.executemany("INSERT INTO prices(mint,ts,price,volume_m5) VALUES(?,?,?,NULL)",
                     [(DC, iso(50), 1.0), (DC, iso(30), 1.1), (DC, iso(10), 5.0), (DC, iso(90), 9.0)])
    assert executor.reference_price(conn, DC, now) == (1.1, 3, "60s")

# ---------------- Fix 2 ----------------
_N = [0]
def _pos(conn, opened_ago_s):
    _N[0] += 1; mint = DC if _N[0] == 1 else f"{DC[:-6]}{_N[0]:06d}"
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - opened_ago_s))
    conn.execute("INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,trail_active,mode,chain) "
                 "VALUES(?,?,1.0,16,1.0,0.94,0,'PAPER','solana')", (mint, ts))
    return dict(conn.execute("SELECT * FROM positions ORDER BY id DESC").fetchone())

def test_interval_fast_then_normal(conn):
    s, now = db.settings_all(conn), time.time()
    r = risk.Risk(conn, Bus(), None)
    assert r.interval_for(_pos(conn, 10), s, now) == 5
    assert r.interval_for(_pos(conn, 179), s, now) == 5
    assert r.interval_for(_pos(conn, 181), s, now) == 15
    assert r.interval_for(_pos(conn, 10), {**s, "risk.fast_window_s": 0}, now) == 15     # window 0 = off

def test_tick_short_only_while_a_position_is_fresh(conn):
    s = db.settings_all(conn); r = risk.Risk(conn, Bus(), None)
    assert r.tick_s(s) == 15                                  # nothing open
    _pos(conn, 600); assert r.tick_s(s) == 15                 # only old positions
    _pos(conn, 20); assert r.tick_s(s) == 5

def test_loop_polls_fresh_position_each_5s_old_one_each_15s(conn, pushes):
    fresh, old = _pos(conn, 20), _pos(conn, 600)
    src = FakeSrc(dex=[pika_pair(baseToken__address=DC, priceUsd="1.0", liquidity__usd=5e4)])
    r = risk.Risk(conn, Bus(), src); s = db.settings_all(conn)
    t0 = time.time(); polled = []
    for k in range(4):                                         # 4 ticks, 5s apart
        now = t0 + 5 * k
        polled.append(sorted(p["id"] for p in [fresh, old] if r.due(p, s, now)))
        for p in [fresh, old]:
            if r.due(p, s, now): r.last_poll[p["id"]] = now
    assert polled == [[fresh["id"], old["id"]], [fresh["id"]], [fresh["id"]], [fresh["id"], old["id"]]]

def test_only_due_poll_makes_no_request_when_nothing_due(conn, pushes):
    _pos(conn, 20)
    src = FakeSrc(dex=[pika_pair(baseToken__address=DC, priceUsd="1.0", liquidity__usd=5e4)])
    r = risk.Risk(conn, Bus(), src)
    assert run(r.poll(only_due=True)) == 1 and len(src.calls) == 1
    assert run(r.poll(only_due=True)) == 0 and len(src.calls) == 1          # <5s later: nothing due, no DexScreener call

def test_429_backoff_still_applies_in_fast_window(conn, sleeps):
    from sources import RateLimited
    _pos(conn, 20)
    r = risk.Risk(conn, Bus(), FakeSrc(dex=RateLimited("429")))
    run(r.poll(only_due=True))
    assert 30 in sleeps                                        # risk.backoff_429_s
