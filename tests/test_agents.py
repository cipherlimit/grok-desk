import copy, json, time, asyncio
import pytest
import db, fields, links, fees, onchain
from conftest import raw, FakeSrc, Seq, run, PIKA, PIKA_POOL, MARVIN
from sources import SourceError, RateLimited, with_retry
from bus import Bus
from agents import scanner, auditor, analyst, executor, risk
from agents.auditor import NoData

S = lambda c: db.settings_all(c)
ago = lambda m: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - m * 60))

def pika_pair(**over):
    p = copy.deepcopy(raw("dexscreener_pair.json")[0])
    for k, v in over.items():
        cur = p; ks = k.split("__")
        for kk in ks[:-1]: cur = cur[kk]
        cur[ks[-1]] = v
    return p

# ======================= settings =======================
def test_every_bracket_is_a_live_setting(conn):
    s = S(conn)
    for k, v in {"scanner.min_liq_usd": 40000, "scanner.top10_max_pct": 25, "scanner.h1_max_pct": 80,
                 "scanner.min_age_min": 10, "scanner.room_max_min": 60, "scanner.dex_every_s": 60,
                 "scanner.dex_max_rpm": 250, "scanner.runner_h24_pct": 300, "scanner.ws_silent_s": 120,
                 "scanner.dex_429_wait_s": 30, "auditor.dev_max_pct": 5, "auditor.lp_min_pct": 99,
                 "auditor.liq_drop_max_pct": 50, "auditor.bundle_max": 5, "auditor.delegate_max": 10,
                 "auditor.retries": 3, "auditor.retry_gap_s": 2, "analyst.state_max_chars": 3000,
                 "executor.min_usd": 47.5, "executor.liq_drop_max_pct": 20, "executor.price_run_max_pct": 15,
                 "executor.bought_wait_min": 5, "below_min_paper": "trade", "below_min_live": "skip", "risk.no_price_polls": 4, "risk.sold_wait_min": 2}.items():
        assert s[k] == v, k
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] >= 60
    db.set_setting(conn, "scanner.min_liq_usd", 1000, who="test")
    assert S(conn)["scanner.min_liq_usd"] == 1000           # read live

# ======================= Scanner =======================
def test_scanner_kill_order(conn):
    s = S(conn)
    assert scanner.decide(s, 31_200, 90, 500, "X", 30)[:2] == ("kill", "liq $31.2K")       # liq first
    assert scanner.decide(s, 50_000, 38, 500, "X", 30)[:2] == ("kill", "top 10 hold 38%")
    assert scanner.decide(s, 50_000, 20, 112, "X", 30)[:2] == ("kill", "+112% in 1h")
    assert scanner.decide(s, 50_000, 20, 10, "PEPE", 30)[:2] == ("kill", "copycat of PEPE")
    assert scanner.decide(s, 50_000, 20, 10, None, 10)[:2] == ("pass", None)
    assert scanner.decide(s, 50_000, 20, 10, None, 9)[:2] == (None, None)                 # too young: waits
    assert scanner.decide(s, 50_000, None, 10, None, 30) == (None, None, True)            # top 10 unknown: waits

def test_scanner_no_kill_before_10_min(conn):
    s = S(conn)
    assert s["scanner.judge_from_age_min"] == 10
    for age in (0, 5, 9):
        assert scanner.decide(s, 4, 90, 500, "X", age) == (None, None, False)            # young: never killed
    assert scanner.decide(s, 4, 90, 500, "X", 10)[:2] == ("kill", "liq $4")
    db.set_setting(conn, "scanner.judge_from_age_min", 3, who="test")
    assert scanner.decide(S(conn), 4, 90, 500, "X", 5)[:2] == ("kill", "liq $4")         # read live

def test_scanner_young_coin_polled_not_killed_then_judged(conn):
    conn.execute("INSERT INTO coins(mint,migrated_at) VALUES(?,?)", (PIKA, ago(3)))
    young = pika_pair(pairCreatedAt=int((time.time() - 180) * 1000))               # liq $4.47, 3 min old
    src = FakeSrc(dex=Seq([young], raw("dexscreener_pair.json")), **_rpc_ok())
    sc = scanner.Scanner(conn, Bus(), src)
    run(sc.cycle())
    assert conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 1              # prices still written
    assert "getTokenLargestAccounts" not in src.calls                                   # no top-10 RPC while young
    run(sc.cycle())                                                                     # raw pair is hours old
    assert conn.execute("SELECT reason FROM scans").fetchone()[0] == "liq $4"

def test_copycat_normalisation():
    runners = {"R1": ("Pepe", "PEPE")}
    assert scanner.copycat_of("PEPE 2.0 🐸", "PEPE2", "M", runners) == "Pepe"
    assert scanner.copycat_of("Baby Pepe", "BPEPE", "M", runners) == "Pepe"      # one inside the other
    assert scanner.copycat_of("Pepe", "PEPE", "R1", runners) is None             # not itself
    assert scanner.copycat_of("Marvin", "MRV", "M", runners) is None

def test_top10_excludes_pool_and_curve():
    largest = raw("rpc_gettokenlargestaccounts.json")["result"]["value"]
    supply = 2_000_000_000_000_000
    all_in = onchain.top10_from(largest, {}, supply, None, "curve")
    owners = {largest[0]["address"]: PIKA_POOL}
    ex = onchain.top10_from(largest, owners, supply, PIKA_POOL, "curve")
    assert ex == pytest.approx(61.0, abs=1) and ex < all_in and abs(ex - 100 * sum(int(a["amount"]) for a in largest[1:11]) / supply) < 0.01

def _rpc_ok(top_owner=PIKA_POOL):
    largest = copy.deepcopy(raw("rpc_gettokenlargestaccounts.json"))
    for a in largest["result"]["value"][1:]:            # raw PIKA holders are 61%: scale to a clean coin
        a["amount"] = str(int(a["amount"]) // 100)
    accs = {"result": {"value": [{"data": {"parsed": {"info": {"owner": top_owner if i == 0 else f"W{i}"}}}}
                                 for i in range(len(largest["result"]["value"]))]}}
    return {"getTokenSupply": raw("rpc_gettokensupply.json"), "getTokenLargestAccounts": largest,
            "getMultipleAccounts": accs}

def test_scanner_pika_raw_is_killed_on_liquidity(conn):
    conn.execute("INSERT INTO coins(mint,migrated_at) VALUES(?,?)", (PIKA, ago(12)))
    bus = Bus(); sc = scanner.Scanner(conn, bus, FakeSrc(dex=raw("dexscreener_pair.json"), **_rpc_ok()))
    assert run(sc.cycle()) == 1
    r = conn.execute("SELECT * FROM scans").fetchone()
    assert (r["verdict"], r["reason"], r["liquidity_usd"]) == ("kill", "liq $4", 4.47)
    assert conn.execute("SELECT name,pool FROM coins").fetchone()[:] == ("PIKA COIN", PIKA_POOL)
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 1          # db.prices written
    assert bus.queues["auditor"].empty()

def test_scanner_pass_once_and_never_twice(conn):
    conn.execute("INSERT INTO coins(mint,migrated_at) VALUES(?,?)", (PIKA, ago(30)))
    pair = pika_pair(liquidity__usd=55_000.0, priceChange__h1=12.0, pairCreatedAt=int((time.time() - 1800) * 1000))
    bus = Bus(); sc = scanner.Scanner(conn, bus, FakeSrc(dex=[pair], **_rpc_ok()))
    run(sc.cycle())
    out = bus.queues["auditor"].get_nowait()
    assert out["verdict"] == "pass" and out["pool"] == PIKA_POOL and out["ageMin"] == 30 and out["top10Pct"] < 25
    assert set(out) == {"agent", "mint", "name", "symbol", "pool", "liquidityUsd", "top10Pct", "h1ChangePct", "ageMin", "verdict", "reason", "ts"}
    run(sc.cycle()); assert bus.queues["auditor"].empty()                   # left the room, no 2nd send

def test_scanner_waits_when_dex_unknown_then_never_qualified(conn):
    conn.execute("INSERT INTO coins(mint,migrated_at) VALUES(?,?)", (PIKA, ago(5)))
    sc = scanner.Scanner(conn, Bus(), FakeSrc(dex=[]))
    run(sc.cycle()); assert conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0
    conn.execute("UPDATE coins SET migrated_at=?", (ago(61),))
    run(sc.cycle())
    assert conn.execute("SELECT verdict,reason FROM scans").fetchone()[:] == ("kill", "never qualified")

def test_scanner_429_keeps_room(conn, sleeps):
    conn.execute("INSERT INTO coins(mint,migrated_at) VALUES(?,?)", (PIKA, ago(12)))
    sc = scanner.Scanner(conn, Bus(), FakeSrc(dex=Seq(RateLimited("429"), raw("dexscreener_pair.json")), **_rpc_ok()))
    run(sc.cycle())
    assert 30 in sleeps and conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 1

def test_scanner_migration_from_raw_creator_from_rugcheck(conn):
    msg = raw("pumpportal_migration.json")
    src = FakeSrc(report=raw("rugcheck_report.json"),
                  getTokenAccountsByOwner={"result": {"value": [{"account": {"data": {"parsed": {"info": {"tokenAmount": {"amount": "0"}}}}}}]}})
    sc = scanner.Scanner(conn, Bus(), src)
    run(sc.on_migration(msg)); run(sc.on_migration(msg))
    r = conn.execute("SELECT * FROM coins").fetchall()
    assert len(r) == 1 and r[0]["creator"] == "38Zejy9GcmbHVwkjFHGD3ta2cDSetPLiBRv4psznDrKa" and r[0]["creator_balance_at_migration"] == 0

# ======================= Auditor layers =======================
def test_l1_l2_raw_revoked_and_red():
    acc = raw("rpc_getaccountinfo.json")
    assert auditor.l1_mint(acc) == (True, "Revoked") and auditor.l2_freeze(acc) == (True, "Revoked")
    bad = copy.deepcopy(acc); bad["result"]["value"]["data"]["parsed"]["info"].update(mintAuthority="X", freezeAuthority="Y")
    assert auditor.l1_mint(bad) == (False, "mint not revoked") and auditor.l2_freeze(bad) == (False, "freeze not revoked")
    with pytest.raises(NoData): auditor.l1_mint({"result": {"value": None}})

def test_l3_dev():
    sup = 2_000_000_000_000_000
    assert auditor.l3_dev(int(sup * 0.009), int(sup * 0.009), sup, 5) == (True, "0.9% · sold 0")
    assert auditor.l3_dev(int(sup * 0.31), int(sup * 0.31), sup, 5) == (False, "dev holds 31%")
    assert auditor.l3_dev(10, 20, sup, 5) == (False, "dev sold")
    assert auditor.l3_dev(0, 0, 0, 5) == (False, "no supply")
    with pytest.raises(NoData): auditor.l3_dev(0, None, sup, 5)

def test_l4_l6():
    assert auditor.l4_top10(16.8, 25) == (True, "16.8%") and auditor.l4_top10(58, 25) == (False, "top 10 hold 58%")
    now = time.time()
    assert auditor.l6_age(now - 43 * 60, now, 10) == (True, "43m") and auditor.l6_age(now - 300, now, 10) == (False, "pool too young")

def test_l5_liquidity_raw_report():
    rep = raw("rugcheck_report.json")
    assert auditor.l5_liquidity(rep, PIKA_POOL, 100, 100, 99, 50) == (True, "100% locked")
    assert auditor.l5_liquidity(rep, PIKA_POOL, 40, 100, 99, 50) == (False, "liquidity pulled")
    unl = copy.deepcopy(rep); unl["markets"][0]["lp"]["lpLockedPct"] = 40
    assert auditor.l5_liquidity(unl, PIKA_POOL, 100, 100, 99, 50) == (False, "liquidity unlocked")
    with pytest.raises(NoData): auditor.l5_liquidity(rep, "OtherPool", 100, 100, 99, 50)

def test_l7_marvin_has_data_pika_empty_is_red():
    ok, v, n = auditor.l7_bundles(raw("rugcheck_insiders_graph.json"), 5)
    assert (ok, v, n) == (False, "7 bundled wallets", 7)
    w = raw("insiders_probe/graph_WIGGLES_2kWx47CUrcXtzptFNnbSXKq3vo1ebqyLBhRytKRGpump.json")
    assert auditor.l7_bundles(w, 5)[0] is True
    for empty in (raw("rugcheck_insiders_graph_empty.json"), raw(f"insiders_probe/graph_PIKA_{PIKA}.json")):
        with pytest.raises(NoData): auditor.l7_bundles(empty, 5)
    nolinks = copy.deepcopy(raw("rugcheck_insiders_graph.json")); del nolinks[0]["links"]
    with pytest.raises(NoData): auditor.l7_bundles(nolinks, 5)

def test_l8_delegations_raw_and_drain():
    accs = raw("rpc_getprogramaccounts_token2022.json")["result"]
    assert auditor.l8_delegations(accs, 10) == (True, "None", 18)
    drain = copy.deepcopy(accs)
    for a in drain[:12]: a["account"]["data"]["parsed"]["info"]["delegate"] = "9fQxAAAABBBBT2a"
    ok, v, h = auditor.l8_delegations(drain, 10)
    assert (ok, v) == (False, "12 wallets → 9fQx...T2a")

def _audit_src(graph):
    return FakeSrc(getAccountInfo=raw("rpc_getaccountinfo.json"), getTokenSupply=raw("rpc_gettokensupply.json"),
                   getTokenAccountsByOwner={"result": {"value": []}}, report=raw("rugcheck_report.json"),
                   dex=[pika_pair(liquidity__usd=50_000.0)], graph=graph,
                   getProgramAccounts=raw("rpc_getprogramaccounts_token2022.json"), **{k: v for k, v in _rpc_ok().items() if k != "getTokenSupply"})

def _scan_msg():
    return {"agent": "scanner", "mint": PIKA, "name": "PIKA COIN", "symbol": "PIKA", "pool": PIKA_POOL,
            "liquidityUsd": 50_000.0, "top10Pct": 20.0, "h1ChangePct": 5.0, "ageMin": 15, "verdict": "pass", "reason": None, "ts": db.now_iso()}

def _coin(conn, creator="38Zejy9GcmbHVwkjFHGD3ta2cDSetPLiBRv4psznDrKa", bal=0):
    conn.execute("INSERT INTO coins(mint,name,migrated_at,creator,creator_balance_at_migration) VALUES(?,?,?,?,?)",
                 (PIKA, "PIKA COIN", ago(15), creator, bal))

def test_audit_pika_empty_graph_red_at_layer7(conn, sleeps):
    _coin(conn); bus = Bus()
    out = run(auditor.Auditor(conn, bus, _audit_src(raw("rugcheck_insiders_graph_empty.json"))).audit(_scan_msg()))
    assert out["passed"] is False and out["layersChecked"] == 7 and out["reason"] == "no data: bundles"
    assert [l["value"] for l in out["layers"]] == ["Revoked", "Revoked", "0.0% · sold 0", out["layers"][3]["value"], "100% locked", "15m", "no data: bundles"]
    assert set(out) == {"agent", "mint", "ts", "passed", "layersChecked", "layers", "holders", "reason"}
    a = conn.execute("SELECT kind,title,subtitle,value FROM activity").fetchone()
    assert a[:] == ("rejected", "Rejected PIKA COIN", "Auditor · no data: bundles", "Skipped")
    assert conn.execute("SELECT COUNT(*) FROM audits").fetchone()[0] == 7 and bus.queues["analyst"].empty()
    # never re-audits a rejected mint the same night
    assert run(auditor.Auditor(conn, bus, _audit_src([])).audit(_scan_msg())) is None

def test_audit_8_of_8_passes_to_analyst(conn):
    _coin(conn); bus = Bus()
    g = raw("insiders_probe/graph_WIGGLES_2kWx47CUrcXtzptFNnbSXKq3vo1ebqyLBhRytKRGpump.json")
    out = run(auditor.Auditor(conn, bus, _audit_src(g)).audit(_scan_msg()))
    assert out["passed"] and out["layersChecked"] == 8 and out["holders"] == 36 and out["reason"] is None
    assert bus.queues["analyst"].get_nowait()["auditor"] == out
    assert conn.execute("SELECT holders FROM coins").fetchone()[0] == 36

def test_audit_creator_null_red_layer3(conn):
    _coin(conn, creator=None)
    out = run(auditor.Auditor(conn, Bus(), _audit_src([])).audit(_scan_msg()))
    assert out["layersChecked"] == 3 and out["reason"] == "no data: dev"

def test_retry_rule_3_tries_2s_apart_then_red(conn, sleeps):
    _coin(conn); src = _audit_src(SourceError("down"))
    out = run(auditor.Auditor(conn, Bus(), src).audit(_scan_msg()))
    assert out["reason"] == "no data: bundles" and src.calls.count("graph") == 3 and sleeps.count(2) == 2

def test_retry_rule_success_on_third_try(sleeps):
    src = FakeSrc(graph=Seq(SourceError("a"), SourceError("b"), []))
    assert run(with_retry(lambda: src.rug_graph("m"), 3, 2)) == [] and src.calls.count("graph") == 3

def test_layer8_down_telegram(conn, pushes, sleeps):
    _coin(conn)
    g = raw("insiders_probe/graph_WIGGLES_2kWx47CUrcXtzptFNnbSXKq3vo1ebqyLBhRytKRGpump.json")
    src = _audit_src(g); src.r["getProgramAccounts"] = SourceError("refused")
    out = run(auditor.Auditor(conn, Bus(), src).audit(_scan_msg()))
    assert out["reason"] == "no data: delegations"
    assert pushes[0][0] == "layer 8 down, every coin is red until rpc_url changes"
    assert auditor.layer8_kills_tonight(conn) == 1

# ======================= Analyst (rules) =======================
def test_rules_answers_and_gate(conn):
    s = S(conn)
    d = {"vol_h1": 30000, "buys_h1": 200, "chg_m5": -6, "buys_m5": 10, "sells_m5": 5, "chg_h1": 8}
    a = analyst.rules_answers(d, 0, s)
    assert a == {"alive_6h": 1.0, "organic": 1.0, "flush": 1.0, "chase": 0.1}
    assert analyst.gate(a, s) == (98, True, [])
    assert analyst.rules_answers(d, 3, s)["organic"] == 0.5
    assert analyst.rules_answers({**d, "chg_m5": -2}, 0, s)["flush"] == 0.5
    assert analyst.rules_answers({**d, "chg_m5": 1}, 0, s)["flush"] == 0.0
    score, ok, failed = analyst.gate({"alive_6h": 0.5, "organic": 0.9, "flush": 0, "chase": 0.4}, s)
    assert not ok and failed == ["alive_6h 0.50", "chase 0.40", "score 50"]

def _an_msg():
    aud = {"layers": [{"n": 4, "value": "16.8%"}, {"n": 5, "value": "100% locked"}, {"n": 7, "value": "4 bundled wallets"}, {"n": 8, "value": "None"}]}
    return {"scanner": _scan_msg(), "auditor": aud, "dev_pct": 0.9}

def test_analyst_rules_reject_activity_and_never_reasks(conn):
    _coin(conn); bus = Bus()
    a = analyst.Analyst(conn, bus, FakeSrc(dex=raw("dexscreener_pair.json")))
    out = run(a.analyze(_an_msg()))
    assert out["model"] == "rules" and out["inputTokens"] == 0 and not out["passed"]
    assert out["answers"]["organic"] == pytest.approx(73 / 150 * 0.5)       # 4 bundled >= 3 -> x0.5
    assert out["failed"][0].startswith("alive_6h")
    act = conn.execute("SELECT title,subtitle,value FROM activity").fetchone()
    assert act[:] == ("Rejected PIKA COIN", f"Analyst (rules) · {out['failed'][0]}", "Skipped")
    assert run(a.analyze(_an_msg())) is None                                   # never re-asks

def test_analyst_rules_pass_to_executor(conn):
    _coin(conn); bus = Bus()
    msg = _an_msg(); msg["auditor"]["layers"][2]["value"] = "None"
    p = pika_pair(volume__h1=30000, txns__h1__buys=200, priceChange__m5=-6, priceChange__h1=8)
    out = run(analyst.Analyst(conn, bus, FakeSrc(dex=[p])).analyze(msg))
    assert out["passed"] and out["score"] == 98
    assert set(out) == {"agent", "mint", "answers", "score", "passed", "failed", "model", "inputTokens"}
    assert bus.queues["executor"].get_nowait()["analyst"] == out

def test_analyst_state_numbers_only():
    st = analyst.build_state(_scan_msg(), _an_msg()["auditor"], fields.dex_read(raw("dexscreener_pair.json")[0]), 0.9, 15, 3000)
    assert st.startswith("Coin PIKA COIN on Solana, graduated 15 min ago.") and "buy " not in st.lower().replace("buys", "")
    assert len(analyst.build_state(_scan_msg(), _an_msg()["auditor"], {}, None, 1, 50)) == 50

def test_analyst_jev_mode_without_key_rejects_no_answer(conn, monkeypatch):
    monkeypatch.setattr(analyst, "secret", lambda k: None)
    _coin(conn); db.set_setting(conn, "analyst_mode", "jev")
    out = run(analyst.Analyst(conn, Bus(), FakeSrc(dex=raw("dexscreener_pair.json"))).analyze(_an_msg()))
    assert out["failed"] == ["no answer"] and not out["passed"]          # never falls back silently

# ======================= Executor =======================
def test_sizing(conn):
    s = S(conn)
    assert executor.size_for(200, 0, 100_000, s, "PAPER") == (16.0, "size_pct, under min traded", 16.0, None)
    assert executor.size_for(200, 0, 100_000, s, "LIVE") == (None, "size_pct", 16.0, "under $47.50")
    s2 = {**s, "below_min_paper": "bump"}
    assert executor.size_for(200, 0, 100_000, s2)[0] == 47.5
    assert executor.size_for(200, 160, 100_000, s2)[3] == "no free stack"
    assert executor.size_for(1240, 0, 1_000_000, s) == (99.2, "size_pct", 99.2, None)
    assert executor.size_for(1240, 0, 3_000, s2)[:2] == (60.0, "liquidity")                      # 2% of pool
    assert executor.size_for(1240, 0, 4_000.37, s)[0] == 80.01                                      # cents

def test_recheck(conn):
    s = S(conn)
    assert executor.recheck(79_000, 100_000, 1, 1, s) == "liquidity dropping"
    assert executor.recheck(90_000, 100_000, 1.16, 1, s) == "ran before the push"
    assert executor.recheck(90_000, 100_000, 1.1, 1, s) is None

def test_push_text():
    assert executor.push_text("PAPER", "PIKA COIN", 47.5, 23.75, 82, 6, 12) == \
        "PAPER · Buy PIKA COIN · $47.50 · 23.8% of stack\nAuditor 8/8 · Analyst 82 · stop -6% · trail 12%"

def _ex_msg(price_at_analyst=1e-6):
    return {"analyst": {"mint": PIKA, "score": 82, "passed": True}, "scanner": _scan_msg(),
            "price_at_analyst": price_at_analyst, "name": "PIKA COIN"}

def test_executor_paper_fill_stop_and_one_push(conn, pushes):
    _coin(conn); db.add_balance(conn, 200); db.set_setting(conn, "below_min_paper", "bump"); bus = Bus()
    p = pika_pair(liquidity__usd=50_000.0, priceUsd="0.000001")
    ex = executor.Executor(conn, bus, FakeSrc(dex=[p]))
    out = run(ex.handle(_ex_msg()))
    assert out == {"agent": "executor", "mint": PIKA, "signalPrice": 1e-6, "sizeUsd": 47.5, "stackPct": 23.8,
                   "link": links.coin_url(PIKA, "solana"), "mode": "PAPER", "status": "bought", "reason": None}
    text, buttons = pushes[0]
    assert text.startswith("PAPER · Buy PIKA COIN · $47.50 · 23.8% of stack")
    assert buttons[0][0] == {"text": "Buy on FOMO", "url": links.coin_url(PIKA, "solana")}
    pos = conn.execute("SELECT * FROM positions").fetchone()
    assert pos["entry_price"] == 1e-6 and pos["stop_price"] == pytest.approx(1e-6 * 0.94) and pos["closed_at"] is None
    assert conn.execute("SELECT status,price FROM signals").fetchone()[:] == ("bought", 1e-6)
    act = conn.execute("SELECT kind,title,value FROM activity").fetchone()
    assert act[:] == ("bought", "Bought PIKA COIN", "$47.50 · 23.8% of stack")
    assert run(ex.handle(_ex_msg())) is None and len(pushes) == 1          # 1 push per coin per night

def test_executor_paper_small_size_opens_at_real_size(conn, pushes):
    _coin(conn); db.add_balance(conn, 200)
    ex = executor.Executor(conn, Bus(), FakeSrc(dex=[pika_pair(liquidity__usd=50_000.0, priceUsd="0.000001")]))
    out = run(ex.handle(_ex_msg()))
    assert (out["status"], out["sizeUsd"], out["stackPct"], out["reason"]) == ("bought", 16.0, 8.0, None)
    assert pushes[0][0].startswith("PAPER · Buy PIKA COIN · $16.00 · 8% of stack")
    pos = conn.execute("SELECT size_usd FROM positions").fetchone()
    assert pos[0] == 16.0
    pnl, fee = fees.paper_pnl(16.0, 1e-6, 1e-6)
    assert fee == pytest.approx(16 * 0.0045 + (16 - 16 * 0.0045) * 0.0045)        # fees.py as usual

def test_executor_live_small_size_skips(conn, pushes):
    _coin(conn); db.set_setting(conn, "mode", "LIVE"); db.set_setting(conn, "stack", 200)
    ex = executor.Executor(conn, Bus(), FakeSrc(dex=[pika_pair(liquidity__usd=50_000.0, priceUsd="0.000001")]))
    out = run(ex.handle(_ex_msg()))
    assert (out["status"], out["reason"], out["mode"]) == ("skipped", "under $47.50", "LIVE") and not pushes
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0

def test_executor_push_failed_paper_fills_anyway(conn, monkeypatch):
    import telegram
    async def down(*a, **k): return False
    monkeypatch.setattr(telegram, "send", down)
    _coin(conn); db.add_balance(conn, 200)
    out = run(executor.Executor(conn, Bus(), FakeSrc(dex=[pika_pair(liquidity__usd=50_000.0, priceUsd="0.000001")])).handle(_ex_msg()))
    assert out["status"] == "bought" and conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 1

# ======================= Risk =======================
def _pos(conn, entry=1.0, size=47.5, opened_min=1):
    conn.execute("INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,trail_active,mode) VALUES(?,?,?,?,?,?,0,'PAPER')",
                 (PIKA, ago(opened_min), entry, size, entry, entry * 0.94))
    return dict(conn.execute("SELECT * FROM positions").fetchone())

def test_risk_rules_order(conn):
    s = S(conn); pos = {"entry_price": 1.0, "peak_price": 1.0, "stop_price": 0.94, "trail_active": 0}
    assert risk.evaluate(pos, 0.94, 0, s)[1] == "stop"
    assert risk.evaluate(pos, 0.80, 0, s)[1] == "stop"                                   # gapped past
    p, r = risk.evaluate(pos, 1.25, 0, s); assert p["trail_active"] and r is None and p["peak_price"] == 1.25
    assert risk.evaluate(p, 1.10, 0, s)[1] == "trail"                                    # 1.25*0.88=1.10
    assert risk.evaluate(p, 1.11, 0, s)[1] is None
    assert risk.evaluate(pos, 1.04, 3, s)[1] == "timeout" and risk.evaluate(pos, 1.05, 3, s)[1] is None
    assert risk.tightened_stop(1.0, 0.94, 8) == 0.94 and risk.tightened_stop(1.0, 0.94, 4) == pytest.approx(0.96)

def test_risk_paper_stop_exit_with_fees(conn, pushes):
    _coin(conn); db.add_balance(conn, 200); _pos(conn)
    r = risk.Risk(conn, Bus(), FakeSrc(dex=[pika_pair(priceUsd="0.9", liquidity__usd=50_000.0)]))
    run(r.poll())
    pos = conn.execute("SELECT * FROM positions").fetchone()
    pnl, fee = fees.paper_pnl(47.5, 1.0, 0.9)
    assert pos["exit_reason"] == "stop" and pos["exit_price"] == 0.9 and pos["pnl_usd"] == pytest.approx(pnl) and pos["closed_at"]
    assert db.last_balance(conn) == pytest.approx(200 + pnl)
    assert pushes[0][0] == f"PIKA COIN hit the -6% stop · -${abs(pnl):.2f}"
    assert pushes[0][1][0][0]["url"] == links.coin_url(PIKA, "solana") and pushes[0][1][1][0]["text"] == "Sold"
    assert conn.execute("SELECT kind,title,subtitle,value FROM activity").fetchone()[:] == \
        ("stopped", "Stopped PIKA COIN", "Risk · -6% stop", f"-${abs(pnl):.2f}")
    assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 1

def test_risk_trail_exit_text(conn, pushes):
    _coin(conn); db.add_balance(conn, 200); _pos(conn)
    r = risk.Risk(conn, Bus(), FakeSrc(dex=Seq([pika_pair(priceUsd="1.5", liquidity__usd=5e4)], [pika_pair(priceUsd="1.3", liquidity__usd=5e4)])))
    run(r.poll()); assert conn.execute("SELECT trail_active,peak_price FROM positions").fetchone()[:] == (1, 1.5)
    run(r.poll())
    pnl, _ = fees.paper_pnl(47.5, 1.0, 1.3); pct = 100 * pnl / 47.5
    assert pushes[0][0] == f"Sold PIKA COIN · +${pnl:.2f} (+{risk.fmt_pct(pct)}%)"
    assert conn.execute("SELECT subtitle FROM activity").fetchone()[0] == "Risk · trailing stop"

def test_risk_timeout_text(conn, pushes):
    _coin(conn); db.add_balance(conn, 200); _pos(conn, opened_min=181)
    run(risk.Risk(conn, Bus(), FakeSrc(dex=[pika_pair(priceUsd="1.01", liquidity__usd=5e4)])).poll())
    assert pushes[0][0].startswith("Sell PIKA COIN, dead money · ") and pushes[0][0].endswith(" in 3h")

def test_risk_no_price_4_polls_push_once(conn, pushes):
    _coin(conn); _pos(conn)
    r = risk.Risk(conn, Bus(), FakeSrc(dex=[]))
    for _ in range(6): run(r.poll())
    assert [t for t, _ in pushes] == ["PIKA COIN: no price for 1 min"] and pushes[0][1][0][0]["text"] == "Sell on FOMO"
    assert conn.execute("SELECT closed_at FROM positions").fetchone()[0] is None          # never invents a price

def test_risk_liquidity_zero_pulled(conn, pushes):
    _coin(conn); db.add_balance(conn, 200); _pos(conn)
    run(risk.Risk(conn, Bus(), FakeSrc(dex=[pika_pair(priceUsd="0.97", liquidity__usd=0)])).poll())
    assert conn.execute("SELECT exit_reason,exit_price FROM positions").fetchone()[:] == ("pulled", 0.97)

def test_risk_429_backoff(conn, sleeps):
    _coin(conn); _pos(conn)
    run(risk.Risk(conn, Bus(), FakeSrc(dex=RateLimited("429"))).poll())
    assert 30 in sleeps and conn.execute("SELECT closed_at FROM positions").fetchone()[0] is None
