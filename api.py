"""STEP 7 API. Plan B (GitHub Pages): no live server; write_all() writes the same shapes as JSON files
under output/ every [15]s and publisher.py ships them to /d/{DATA_PATH}/ (encrypted in transit).
  state.json            GET /api/state                 mode, desk activity (today/best/signal/hourly/messages), paper balance, 5 agent cards
  chart.json            GET /api/chart?range=...       all ranges in one file: points + marks
  activity.json         GET /api/activity              rows, 30 per page; activity/p{n}.json older pages
  coin/{mint}.json      GET /api/coin/{mint}           price, candles, stats, position, exit lines, coin_url
  audit/{mint}.json     GET /api/audit/{mint}          8 layers with values
  agents/{name}.json    GET /api/agents/{name}/decisions   last 20 decisions
  account.json          GET /api/account               mode, stack, profile_url, SIGNUP_URL
  settings.json         GET /api/settings              read-only in plan B (change via Telegram)
  coins.json            search index (name, symbol, mint)
POST endpoints and SSE do not exist in plan B: settings, pause and mode go through Telegram (STEP 9).
The files carry no keys and no token. The FastAPI app below is the plan A shape, not served."""
import asyncio, json, logging, logging.handlers, os, shutil, time
from fastapi import FastAPI, Header, HTTPException
import db, links, chains, bnb
from config import OUT_DIR, LOG_DIR, SPEC, secret
from sources import DEX_BATCH, SourceError

AGENTS = ["scanner", "auditor", "analyst", "executor", "risk"]
COLORS = {"scanner": "#64d2ff", "auditor": "#ffffff", "analyst": "#ffd60a", "executor": "#00d35a", "risk": "#ff6b9a"}
LAYER_NAMES = {1: "Mint authority", 2: "Freeze authority", 3: "Dev wallet", 4: "Top 10 holders",
               5: "Liquidity", 6: "Pool age", 7: "Bundled wallets", 8: "Delegations"}
PAGE = 30

log = logging.getLogger("api")
if not log.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.TimedRotatingFileHandler(LOG_DIR / "api.log", when="midnight", backupCount=14)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s")); log.addHandler(h); log.setLevel(logging.INFO)
    log.propagate = False

# ------------------------------------------------------------------ helpers
def _rows(c, sql, args=()): return [dict(r) for r in c.execute(sql, args)]
def _one(c, sql, args=()):
    r = c.execute(sql, args).fetchone(); return r[0] if r else None
def coin_name(c, mint):
    r = c.execute("SELECT name, symbol FROM coins WHERE mint=?", (mint,)).fetchone()
    if r and (r["name"] or r["symbol"]): return r["name"] or r["symbol"]
    lp = db.LAST_PAIR.get(mint) or {}
    return lp.get("name") or lp.get("symbol") or f"{mint[:4]}…{mint[-4:]}"
def fmt_pct(x): return f"{x:.1f}".rstrip("0").rstrip(".")
def pos_stop_pct(p): return round((1 - p["stop_price"] / p["entry_price"]) * 100, 2)

def atomic_write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)

def night_start_balance(c, s):
    start, _ = db.night_bounds()
    r = c.execute("SELECT value FROM balance WHERE ts<=? ORDER BY ts DESC LIMIT 1", (start,)).fetchone()
    if r: return r["value"]
    r = c.execute("SELECT value FROM balance WHERE ts>=? ORDER BY ts LIMIT 1", (start,)).fetchone()
    return r["value"] if r else s["desk.paper_start_stack"]

def current_balance(c, s):
    b = db.last_balance(c)
    return b if b is not None else s["desk.paper_start_stack"]

# ------------------------------------------------------------------ agent cards
def card_scanner(c, s, start):
    feed = []
    for r in _rows(c, "SELECT s.ts,s.mint,s.verdict,s.reason,s.liquidity_usd FROM scans s WHERE s.ts>=? ORDER BY s.id DESC LIMIT 6", (start,)):
        feed.append({**r, "name": coin_name(c, r["mint"]), "chain": chains.chain_of(r["mint"]), "coin_url": links.coin_url(r["mint"])})
    room = db.waiting_room(c, s["scanner.max_wait_min"])
    return {"pools": _one(c, "SELECT COUNT(*) FROM coins WHERE migrated_at>=?", (start,)) or 0,
            "waiting": len(room), "passed": _one(c, "SELECT COUNT(*) FROM scans WHERE verdict='pass' AND ts>=?", (start,)) or 0,
            "killed": _one(c, "SELECT COUNT(*) FROM scans WHERE verdict='kill' AND ts>=?", (start,)) or 0, "feed": feed}

def audit_doc(c, mint, s=None):
    rows = _rows(c, "SELECT layer,name,value_text,pass,source,ts FROM audits WHERE mint=? ORDER BY id", (mint,))
    if not rows: return None
    # only the latest audit run of this mint (a run restarts at layer 1)
    last1 = max(i for i, r in enumerate(rows) if r["layer"] == 1) if any(r["layer"] == 1 for r in rows) else 0
    rows = rows[last1:]
    layers, red = [], None
    names = LAYER_NAMES if not chains.is_evm(mint) else bnb.LAYERS
    got = {r["layer"]: r for r in rows}
    for n in range(1, 9):
        r = got.get(n)
        if r: layers.append({"n": n, "name": names[n], "pass": bool(r["pass"]), "value": r["value_text"],
                             "source": r["source"], "ts": r["ts"], "status": "pass" if r["pass"] else "red"})
        else: layers.append({"n": n, "name": names[n], "pass": None, "value": "not checked", "source": None,
                             "ts": None, "status": "not checked" if (red or len(got) == 0) else "pending"})
        if r and not r["pass"] and not red: red = r["value_text"]
    for l in layers:                      # after the first red, the rest are grey "not checked"
        if red and l["status"] == "pending": l["status"] = "not checked"
    checked = sum(1 for l in layers if l["status"] in ("pass", "red"))
    complete = bool(red) or checked == 8
    name = coin_name(c, mint)
    sig = c.execute("SELECT size_usd,status FROM signals WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,)).fetchone()
    if red: verdict = {"state": "rejected", "title": f"Rejected · {red}", "subtitle": f"Grok Bot won't buy {name}"}
    elif checked == 8:
        sub = f"Sent to Executor · ${sig['size_usd']:.2f}" if sig and sig["size_usd"] else "Sent to Analyst"
        verdict = {"state": "cleared", "title": "Cleared · 8/8 parts", "subtitle": sub}
    else: verdict = {"state": "running", "title": "taking apart...", "subtitle": ""}
    return {"mint": mint, "name": name, "symbol": _one(c, "SELECT symbol FROM coins WHERE mint=?", (mint,)),
            "layers": layers, "layersChecked": checked, "complete": complete, "passed": complete and not red,
            "reason": red, "verdict": verdict, "ts": rows[-1]["ts"], "coin_url": links.coin_url(mint),
            "auditor_enabled": bool((s or {}).get("auditor.enabled", True))}

def card_auditor(c, s, start):
    mints = [r[0] for r in c.execute("SELECT mint FROM audits WHERE ts>=? GROUP BY mint ORDER BY MAX(id) DESC", (start,))]
    last = audit_doc(c, mints[0], s) if mints else None
    rejected = _one(c, "SELECT COUNT(DISTINCT mint) FROM audits WHERE pass=0 AND ts>=?", (start,)) or 0
    status = None
    if last:
        status = "taking apart..." if not last["complete"] else ("8/8 → Executor" if last["passed"] else last["reason"])
    return {"taken_apart": len(mints), "rejected": rejected, "last": last, "status": status}

def card_analyst(c, s, start):
    r = c.execute("SELECT * FROM verdicts WHERE ts>=? ORDER BY id DESC LIMIT 1", (start,)).fetchone()
    last = None
    if r:
        last = {"mint": r["mint"], "name": coin_name(c, r["mint"]), "score": r["score"], "pass": bool(r["pass"]),
                "reason": r["reason"], "answers": {k: r[k] for k in ("alive_6h", "organic", "flush", "chase")},
                "coin_url": links.coin_url(r["mint"])}
    return {"scored": _one(c, "SELECT COUNT(*) FROM verdicts WHERE ts>=?", (start,)) or 0, "last": last,
            "green_from": s["analyst.min_score"], "mode": s["analyst.mode"]}

def card_executor(c, s, start):
    r = c.execute("SELECT * FROM signals WHERE ts>=? AND status!='skipped' ORDER BY id DESC LIMIT 1", (start,)).fetchone()
    last = None
    if r:
        last = {"mint": r["mint"], "name": coin_name(c, r["mint"]), "size_usd": r["size_usd"], "stack_pct": r["stack_pct"],
                "status": r["status"], "filled": r["status"] == "bought", "coin_url": r["link"], "ts": r["ts"]}
    return {"trades": _one(c, "SELECT COUNT(*) FROM positions WHERE opened_at>=?", (start,)) or 0, "last": last,
            "skipped": _one(c, "SELECT COUNT(*) FROM signals WHERE status='skipped' AND ts>=?", (start,)) or 0,
            "bar_s": s["executor.paper_bar_s"]}

def card_risk(c, s, start):
    open_n = len(db.open_positions(c))
    r = c.execute("SELECT exit_reason FROM positions WHERE closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 1").fetchone()
    label = {"trail": "Trailed out", "stop": "Stopped", "timeout": "Timed out", "pulled": "Pulled"}.get(r["exit_reason"]) if r else None
    return {"guarding": open_n, "last_exit": label, "stop_pct": s["risk.stop_pct"], "trail_pct": s["risk.trail_pct"],
            "pill_stop": f"stop -{fmt_pct(s['risk.stop_pct'])}%", "pill_trail": f"trail {fmt_pct(s['risk.trail_pct'])}%"}

# ------------------------------------------------------------------ desk activity (home hero, 2026-11-18)
def _money(v): return ("-$" if v < 0 else "$") + f"{abs(v):,.2f}"

def _distinct(c, sql, args): return _one(c, sql, args) or 0

def desk_today(c, s, start):
    """Counts since night start, all from the ledger. Coins are counted once each (a rescan is the same coin)."""
    q = lambda w: _distinct(c, f"SELECT COUNT(DISTINCT mint) FROM scans WHERE ts>=?{w}", (start,))
    seen = q("")
    by_chain = {ch: 0 for ch in (chains.SOLANA, chains.BNB)}
    for (m,) in c.execute("SELECT DISTINCT mint FROM scans WHERE ts>=?", (start,)): by_chain[chains.chain_of(m)] += 1
    audited = _distinct(c, "SELECT COUNT(DISTINCT mint) FROM audits WHERE ts>=?", (start,))
    audit_red = _distinct(c, "SELECT COUNT(DISTINCT mint) FROM audits WHERE pass=0 AND ts>=?", (start,))
    return {"seen": seen, "killed": q(" AND verdict='kill'"), "passed": q(" AND verdict='pass'"),
            "audited": audited, "audit_rejected": audit_red,
            "scored": _distinct(c, "SELECT COUNT(DISTINCT mint) FROM verdicts WHERE ts>=?", (start,)),
            "signals": _distinct(c, "SELECT COUNT(*) FROM signals WHERE ts>=?", (start,)),
            "trades": _distinct(c, "SELECT COUNT(*) FROM positions WHERE opened_at>=?", (start,)),
            "by_chain": by_chain, "since": start}

def best_candidate(c, s, start):
    """The coin that got furthest through the desk tonight (signal > analyst pass > most audit layers passed
    > scanner pass). If nothing passed the Scanner: the highest-liquidity coin seen, labelled 'closest call'.
    Information only, never a buy call."""
    best = None
    def consider(rank, mint, ts, stage, detail):
        nonlocal best
        if best is None or (rank, ts) > (best["_rank"], best["ts"]):
            best = {"_rank": rank, "mint": mint, "ts": ts, "stage": stage, "detail": detail}
    for r in c.execute("SELECT mint,ts,status,size_usd FROM signals WHERE ts>=?", (start,)):
        consider(5, r["mint"], r["ts"], "Signal", f"{r['status']} · {_money(r['size_usd'] or 0)}")
    for r in c.execute("SELECT mint,ts,score,pass,reason FROM verdicts WHERE ts>=?", (start,)):
        if r["pass"]: consider(4, r["mint"], r["ts"], "Analyst pass", f"score {r['score']}")
        else: consider(3.5, r["mint"], r["ts"], "Analyst reject", r["reason"] or f"score {r['score']}")
    for (m,) in c.execute("SELECT DISTINCT mint FROM audits WHERE ts>=?", (start,)):
        d = audit_doc(c, m, s)
        if not d: continue
        n = sum(1 for l in d["layers"] if l["status"] == "pass")
        if d["passed"]: consider(3.4, m, d["ts"], "Audit 8/8", "cleared all 8 layers")
        else: consider(2 + n / 10, m, d["ts"], f"Audit {n}/8", ("refused · " + d["reason"]) if d["reason"] else "taking apart...")
    for r in c.execute("SELECT mint,MAX(ts) ts,MAX(liquidity_usd) liq FROM scans WHERE ts>=? AND verdict='pass' GROUP BY mint", (start,)):
        consider(1.5, r["mint"], r["ts"], "Scanner pass", f"liq ${(r['liq'] or 0) / 1000:.1f}K")
    if best is None:
        r = c.execute("SELECT mint,ts,liquidity_usd,reason FROM scans WHERE ts>=? AND liquidity_usd IS NOT NULL "
                      "ORDER BY liquidity_usd DESC, id DESC LIMIT 1", (start,)).fetchone()
        if r: consider(0, r["mint"], r["ts"], "Closest call", f"liq ${(r['liquidity_usd'] or 0) / 1000:.1f}K · killed: {r['reason'] or 'n/a'}")
    if not best: return None
    liq = _one(c, "SELECT liquidity_usd FROM scans WHERE mint=? ORDER BY id DESC LIMIT 1", (best["mint"],))
    best.pop("_rank")
    return {**best, "name": coin_name(c, best["mint"]), "chain": chains.chain_of(best["mint"]), "liquidity_usd": liq,
            "coin_url": links.coin_url(best["mint"])}

def latest_signal(c, start):
    r = c.execute("SELECT * FROM signals WHERE ts>=? ORDER BY id DESC LIMIT 1", (start,)).fetchone()
    if not r: return None
    return {"mint": r["mint"], "name": coin_name(c, r["mint"]), "chain": chains.chain_of(r["mint"]), "ts": r["ts"],
            "status": r["status"], "size_usd": r["size_usd"], "mode": r["mode"], "coin_url": r["link"] or links.coin_url(r["mint"])}

def scans_hourly(c, hours=24):
    """Coins judged by the Scanner per local hour, last `hours` hours: [[hour_start_iso, seen, killed, passed]]."""
    now = int(time.time()); h0 = now - now % 3600 - (hours - 1) * 3600
    buckets = {h0 + i * 3600: [0, 0, 0] for i in range(hours)}
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(h0))
    for r in c.execute("SELECT mint, MAX(ts) ts, verdict FROM scans WHERE ts>=? GROUP BY mint", (since,)):
        t = int(db.parse_iso(r["ts"])); b = t - t % 3600
        if b in buckets:
            buckets[b][0] += 1
            if r["verdict"] == "kill": buckets[b][1] += 1
            elif r["verdict"] == "pass": buckets[b][2] += 1
    return [[time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(b)), *v] for b, v in sorted(buckets.items())]

def agent_messages(c, s, cards, today, bal, ns):
    """One latest line per agent (+ Grok Bot's night line), from the ledger. [{agent, text, ts}]"""
    out = []
    pnl = bal - ns
    out.append({"agent": "grokbot", "ts": db.now_iso(),
                "text": f"Night so far · {today['seen']} seen, {today['killed']} killed, {today['passed']} passed · "
                        f"paper {_money(ns)} → {_money(bal)}"})
    r = c.execute("SELECT closed_at ts,mint,exit_reason,pnl_usd FROM positions WHERE closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 1").fetchone()
    g = cards["risk"]["guarding"]
    if g: out.append({"agent": "risk", "ts": _one(c, "SELECT MAX(opened_at) FROM positions WHERE closed_at IS NULL"), "text": f"Guarding {g} open position{'s' if g > 1 else ''} · stop -{fmt_pct(s['risk.stop_pct'])}%"})
    elif r:
        lbl = {"trail": "Trailed out", "stop": "Stopped", "timeout": "Timed out", "pulled": "Pulled"}.get(r["exit_reason"], r["exit_reason"])
        out.append({"agent": "risk", "ts": r["ts"], "text": f"{lbl} {coin_name(c, r['mint'])} · {_money(r['pnl_usd'] or 0)}"})
    else: out.append({"agent": "risk", "ts": None, "text": f"No position to guard · stop -{fmt_pct(s['risk.stop_pct'])}%"})
    L = cards["executor"]["last"]
    out.append({"agent": "executor", "ts": L["ts"] if L else None,
                "text": f"{L['status'].capitalize()} {L['name']} · {_money(L['size_usd'] or 0)}" if L else "No trade tonight"})
    v = c.execute("SELECT * FROM verdicts ORDER BY id DESC LIMIT 1").fetchone()
    out.append({"agent": "analyst", "ts": v["ts"] if v else None,
                "text": f"{coin_name(c, v['mint'])} scored {v['score']} · {'pass' if v['pass'] else (v['reason'] or 'reject')}" if v else "Waiting for a coin to score"})
    A = cards["auditor"]["last"]
    out.append({"agent": "auditor", "ts": A["ts"] if A else None,
                "text": f"{A['name']} · {cards['auditor']['status']}" if A else "No coin taken apart yet"})
    sc = c.execute("SELECT ts,mint,verdict,reason,liquidity_usd FROM scans ORDER BY id DESC LIMIT 1").fetchone()
    if sc:
        t = f"Killed {coin_name(c, sc['mint'])} · {sc['reason']}" if sc["verdict"] == "kill" else \
            f"Passed {coin_name(c, sc['mint'])} → Auditor" if sc["verdict"] == "pass" else f"Watching {coin_name(c, sc['mint'])}"
        out.append({"agent": "scanner", "ts": sc["ts"], "text": t, "chain": chains.chain_of(sc["mint"])})
    else: out.append({"agent": "scanner", "ts": None, "text": "Listening for graduations"})
    return out

# ------------------------------------------------------------------ documents
def state_doc(c, s):
    start, end = db.night_bounds()
    bal, ns = current_balance(c, s), night_start_balance(c, s)
    hb = {r["agent"]: r["ts"] for r in c.execute("SELECT agent,ts FROM heartbeats")}
    cards = {"scanner": card_scanner(c, s, start), "auditor": card_auditor(c, s, start),
             "analyst": card_analyst(c, s, start), "executor": card_executor(c, s, start), "risk": card_risk(c, s, start)}
    for a in AGENTS:
        cards[a].update({"name": a.capitalize(), "color": COLORS[a], "paused": bool(s.get(f"paused.{a}")),
                         "heartbeat": hb.get(a)})
    openp = []
    for p in db.open_positions(c):
        openp.append({"id": p["id"], "mint": p["mint"], "name": coin_name(c, p["mint"]), "size_usd": p["size_usd"],
                      "entry_price": p["entry_price"], "stop_pct": pos_stop_pct(p), "coin_url": links.coin_url(p["mint"])})
    today = desk_today(c, s, start)
    unread = _one(c, "SELECT COUNT(*) FROM activity WHERE ts>=?", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600)),)) or 0
    return {"ts": db.now_iso(), "mode": s["desk.mode"], "balance": round(bal, 2),
            "today_pct": round(100 * (bal - ns) / ns, 2) if ns else 0.0, "multiplier": round(bal / ns, 2) if ns else 1.0,
            "night_start": start, "night_end": end, "night_start_balance": round(ns, 2),
            "running": sum(1 for a in AGENTS if not s.get(f"paused.{a}")), "agents": cards, "open_positions": openp,
            "heartbeats": hb, "unread": unread, "stack_pct": s["executor.stack_pct"], "fomo_handle": s["desk.fomo_handle"], "hosting": s["desk.hosting"],
            "chains": sorted(chains.enabled(s)), "today": today, "best": best_candidate(c, s, start), "signal": latest_signal(c, start),
            "hourly": scans_hourly(c), "messages": agent_messages(c, s, cards, today, bal, ns)}

RANGES = {"LIVE": "night", "1H": 3600, "4H": 4 * 3600, "1D": 86400, "1W": 7 * 86400, "ALL": None}

def chart_doc(c, s):
    out = {"ts": db.now_iso(), "start_value": round(night_start_balance(c, s), 2), "ranges": {}, "marks": []}
    now = time.time()
    for name, secs in RANGES.items():
        if secs == "night": since = db.night_bounds()[0]                      # LIVE = tonight, since night start
        else: since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - secs)) if secs else "0"
        pts = [[r["ts"], r["value"]] for r in c.execute("SELECT ts,value FROM balance WHERE ts>=? ORDER BY ts", (since,))]
        if len(pts) > 240:                                  # downsample, keep the last point
            step = len(pts) / 240
            pts = [pts[int(i * step)] for i in range(240)] + [pts[-1]]
        out["ranges"][name] = pts
    for p in _rows(c, "SELECT * FROM positions ORDER BY id"):
        name, url = coin_name(c, p["mint"]), links.coin_url(p["mint"])
        out["marks"].append({"ts": p["opened_at"], "kind": "bought", "color": "#0a84ff", "text": f"Bought {name}", "mint": p["mint"], "coin_url": url})
        if p["closed_at"]:
            pnl = p["pnl_usd"] or 0
            if p["exit_reason"] == "stop":
                t, col = f"-{fmt_pct(pos_stop_pct(p))}% · -${abs(pnl):.2f}", "#ff453a"
            elif pnl >= 0: t, col = f"+${pnl:.2f}", "#00d35a"
            else: t, col = f"-${abs(pnl):.2f}", "#ff453a"
            out["marks"].append({"ts": p["closed_at"], "kind": p["exit_reason"], "color": col, "text": t, "mint": p["mint"], "coin_url": url})
    return out

def _activity_row(c, r):
    trade = r["kind"] in ("bought", "sold", "stopped")
    return {**r, "target": ("coin" if trade else "audit"), "coin_url": links.coin_url(r["mint"]) if r["mint"] else None,
            "chain": chains.chain_of(r["mint"]) if r["mint"] else None}

def activity_pages(c, max_pages=10):
    rows = [_activity_row(c, r) for r in _rows(c, "SELECT id,ts,kind,mint,agent,title,subtitle,value FROM activity ORDER BY id DESC LIMIT ?", (PAGE * max_pages,))]
    pages = [rows[i:i + PAGE] for i in range(0, len(rows), PAGE)] or [[]]
    return [{"ts": db.now_iso(), "page": i + 1, "rows": pg, "next": (f"activity/p{i + 2}.json" if i + 1 < len(pages) else None)}
            for i, pg in enumerate(pages)]

def candles(c, mint, since_iso):
    rows = _rows(c, "SELECT ts,price,volume_m5 FROM prices WHERE mint=? AND ts>=? AND price IS NOT NULL ORDER BY ts", (mint, since_iso))
    out, prev_vol = [], None
    for r in rows:
        minute = r["ts"][:16] + ":00Z"
        dv = max(0.0, (r["volume_m5"] or 0) - prev_vol) if prev_vol is not None and r["volume_m5"] is not None else 0.0
        prev_vol = r["volume_m5"] if r["volume_m5"] is not None else prev_vol
        if out and out[-1]["t"] == minute:
            k = out[-1]; k["h"] = max(k["h"], r["price"]); k["l"] = min(k["l"], r["price"]); k["c"] = r["price"]; k["v"] += dv
        else: out.append({"t": minute, "o": r["price"], "h": r["price"], "l": r["price"], "c": r["price"], "v": dv})
    return out[-240:]

def coin_doc(c, s, mint):
    coin = c.execute("SELECT * FROM coins WHERE mint=?", (mint,)).fetchone()
    lp = db.LAST_PAIR.get(mint) or {}
    last = c.execute("SELECT price,ts FROM prices WHERE mint=? AND price IS NOT NULL ORDER BY ts DESC LIMIT 1", (mint,)).fetchone()
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 4 * 3600))
    age_min = None
    if lp.get("pair_created_ms"): age_min = int((time.time() * 1000 - lp["pair_created_ms"]) / 60000)
    elif coin and coin["migrated_at"]: age_min = int((time.time() - db.parse_iso(coin["migrated_at"])) / 60)
    aud = audit_doc(c, mint, s)
    p = c.execute("SELECT * FROM positions WHERE mint=? AND closed_at IS NULL ORDER BY id DESC LIMIT 1", (mint,)).fetchone()
    position, exit_lines = None, None
    if p:
        trail_line = p["peak_price"] * (1 - s["risk.trail_pct"] / 100) if p["trail_active"] else None
        current = max(p["stop_price"], trail_line or 0)
        left_h = s["risk.timeout_h"] - (time.time() - db.parse_iso(p["opened_at"])) / 3600
        position = {"id": p["id"], "size_usd": p["size_usd"], "entry_price": p["entry_price"], "peak_price": p["peak_price"],
                    "opened_at": p["opened_at"], "mode": p["mode"], "stop_pct": pos_stop_pct(p),
                    "since_entry_pct": round(100 * (last["price"] / p["entry_price"] - 1), 2) if last else None,
                    "label": f"Your position · ${p['size_usd']:.2f} in · Executor"}
        exit_lines = {"stop_line": p["stop_price"], "trail_line": trail_line, "trail_active": bool(p["trail_active"]),
                      "current_exit_line": current, "timeout_left_min": max(0, int(left_h * 60))}
    return {"ts": db.now_iso(), "mint": mint, "name": coin_name(c, mint), "symbol": (coin["symbol"] if coin else None) or lp.get("symbol"),
            "chain": chains.LABEL[chains.chain_of(mint)], "age_min": age_min, "price": last["price"] if last else None, "price_ts": last["ts"] if last else None,
            "candles": candles(c, mint, since), "market_cap": lp.get("market_cap"), "liquidity_usd": lp.get("liquidity_usd"),
            "volume_h24": lp.get("volume_h24"), "holders": coin["holders"] if coin else None,
            "shield": (f"{aud['layersChecked']}/8" if aud and aud["passed"] else (f"{sum(1 for l in aud['layers'] if l['status']=='pass')}/8" if aud else None)),
            "position": position, "exit_lines": exit_lines, "mode": s["desk.mode"],
            "trail_start_pct": s["risk.trail_start_pct"], "coin_url": links.coin_url(mint), "button": "Sell on FOMO" if p else "Buy on FOMO"}

def decisions(c, name, n=20):
    q = {"scanner": "SELECT ts,mint,verdict,reason FROM scans ORDER BY id DESC LIMIT ?",
         "analyst": "SELECT ts,mint,CASE WHEN pass THEN 'pass' ELSE 'reject' END verdict, COALESCE(reason,'score '||score) reason FROM verdicts ORDER BY id DESC LIMIT ?",
         "executor": "SELECT ts,mint,status verdict, '$'||printf('%.2f',COALESCE(size_usd,0))||' · '||COALESCE(stack_pct,0)||'% of stack' reason FROM signals ORDER BY id DESC LIMIT ?",
         "risk": "SELECT closed_at ts,mint,exit_reason verdict, printf('%+.2f',pnl_usd) reason FROM positions WHERE closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT ?"}
    if name == "auditor":
        rows = []
        for r in _rows(c, "SELECT mint, MAX(id) id FROM audits GROUP BY mint ORDER BY id DESC LIMIT ?", (n,)):
            d = audit_doc(c, r["mint"])
            rows.append({"ts": d["ts"], "mint": r["mint"], "verdict": "pass" if d["passed"] else ("red" if d["reason"] else "running"),
                         "reason": d["reason"] or f"{d['layersChecked']}/8"})
    else:
        rows = _rows(c, q[name], (n,))
    return [{**r, "name": coin_name(c, r["mint"]), "chain": chains.chain_of(r["mint"]), "coin_url": links.coin_url(r["mint"])} for r in rows]

def account_doc(c, s):
    import telegram
    from agents.executor import ticket_check
    ok, why = db.full_paper_night(c)
    lvl, msg = ticket_check(s, current_balance(c, s) if s["desk.mode"] == "PAPER" else None)
    return {"ts": db.now_iso(), "mode": s["desk.mode"], "paper_balance": round(current_balance(c, s), 2),
            "live_stack": s["desk.stack"], "fomo_handle": s["desk.fomo_handle"],
            "profile_url": links.profile_url(s["desk.fomo_handle"]), "signup_url": links.SIGNUP_URL,
            "telegram": {"connected": bool(secret("TG_TOKEN") and telegram.owner_chat_id()), "bot": "@CipherGrokBot"},
            "live_allowed": ok, "live_rule": why, "ticket_check": {"level": lvl, "message": msg},
            "plan": "B", "settings_editable": False, "web_push": False,
            "web_push_note": "Plan B has no endpoint to save a subscription: no web push, Telegram only.",
            "edit_hint": "Change settings in Telegram: set <name> to <value>"}

def settings_doc(c, s):
    groups = {}
    for k, (default, lo, hi, group, aliases) in SPEC.items():
        t = "choice" if isinstance(lo, list) else ("bool" if isinstance(default, bool) or (lo is None and hi is None) else
                                                  ("number" if isinstance(lo, (int, float)) else lo))
        groups.setdefault(group, []).append({"key": k, "value": s.get(k), "default": default, "type": t,
                                             "min": lo if t == "number" else None, "max": hi if t == "number" else None,
                                             "choices": lo if t == "choice" else None, "aliases": aliases,
                                             "telegram": f"set {aliases[0] if aliases else k} to {s.get(k)}"})
    return {"ts": db.now_iso(), "editable": False, "groups": groups,
            "paused": {a: bool(s.get(f"paused.{a}")) for a in AGENTS}}

def coins_tonight(c, recent=40):
    """Coin files: every coin audited, pushed or traded tonight, plus the [40] most recent graduations."""
    start, _ = db.night_bounds()
    q = ("SELECT mint FROM audits WHERE ts>=? UNION SELECT mint FROM signals WHERE ts>=? "
         "UNION SELECT mint FROM positions WHERE closed_at IS NULL OR opened_at>=?")
    keep = [r[0] for r in c.execute(q, (start, start, start))]
    for r in c.execute("SELECT mint FROM coins WHERE migrated_at>=? ORDER BY migrated_at DESC LIMIT ?", (start, recent)):
        if r[0] not in keep: keep.append(r[0])
    return keep

def write_all(c, out_dir=OUT_DIR):
    """Plan B: every [15]s. Files only under out_dir; the publisher puts them under /d/{DATA_PATH}/."""
    s = db.settings_all(c)
    atomic_write(out_dir / "state.json", state_doc(c, s))
    atomic_write(out_dir / "chart.json", chart_doc(c, s))
    pages = activity_pages(c)
    atomic_write(out_dir / "activity.json", pages[0])
    for pg in pages[1:]: atomic_write(out_dir / "activity" / f"p{pg['page']}.json", pg)
    mints = coins_tonight(c)
    keep_coin, keep_audit = set(), set()
    for m in mints:
        atomic_write(out_dir / "coin" / f"{m}.json", coin_doc(c, s, m)); keep_coin.add(f"{m}.json")
        a = audit_doc(c, m, s)
        if a: atomic_write(out_dir / "audit" / f"{m}.json", a); keep_audit.add(f"{m}.json")
    for sub, keep in (("coin", keep_coin), ("audit", keep_audit)):
        d = out_dir / sub
        if d.exists():
            for f in d.glob("*.json"):
                if f.name not in keep: f.unlink()
    for a in AGENTS: atomic_write(out_dir / "agents" / f"{a}.json", {"ts": db.now_iso(), "agent": a, "decisions": decisions(c, a)})
    atomic_write(out_dir / "account.json", account_doc(c, s))
    atomic_write(out_dir / "settings.json", settings_doc(c, s))
    atomic_write(out_dir / "coins.json", {"ts": db.now_iso(), "coins": [{"mint": m, "name": coin_name(c, m),
                 "symbol": _one(c, "SELECT symbol FROM coins WHERE mint=?", (m,)), "chain": chains.chain_of(m)} for m in mints]})

# ------------------------------------------------------------------ api.py price writer (STEP 4)
def uncovered_mints(conn) -> list[str]:
    s = db.settings_all(conn); start, _ = db.night_bounds()
    tonight = {r[0] for r in conn.execute("SELECT mint FROM signals WHERE ts>=? UNION SELECT mint FROM audits WHERE ts>=?", (start, start))}
    room = {r["mint"] for r in db.waiting_room(conn, s["scanner.max_wait_min"])}
    open_ = {r["mint"] for r in db.open_positions(conn)}
    return sorted(tonight - room - open_)

async def price_loop(conn, src):
    while True:
        mints = uncovered_mints(conn)
        for i in range(0, len(mints), DEX_BATCH):
            try: db.write_price_batch(conn, "api", await src.dex_tokens(mints[i:i + DEX_BATCH]))
            except SourceError as e: log.warning("price batch failed: %s", e)
        await asyncio.sleep(db.settings_all(conn)["api.prices_every_s"])

# ------------------------------------------------------------------ plan A shape (not served in plan B)
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

@app.get("/api/{name}")
def plan_a(name: str, x_app_token: str | None = Header(None)):
    if not secret("APP_TOKEN") or x_app_token != secret("APP_TOKEN"): raise HTTPException(401)
    p = OUT_DIR / f"{name}.json"
    if not p.exists(): raise HTTPException(404)
    return json.loads(p.read_text(encoding="utf-8"))
