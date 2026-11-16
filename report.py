"""Morning Page data (STEP 12). build(c, start, end) -> dict, to_markdown(dict) -> str.
Every coin carries links.coin_url; the footer is "New to FOMO? " + SIGNUP_URL.
The Page itself is made by the night-report routine from output/night_report_<date>.md."""
import re
import db, links

JEV_USD_PER_1K_TOKENS = 0.0   # analyst.mode=rules: no Jev calls, 0 tokens, $0

def _name(c, mint):
    r = c.execute("SELECT name,symbol FROM coins WHERE mint=?", (mint,)).fetchone()
    return (r and (r["name"] or r["symbol"])) or mint[:6]

def _coin(c, mint):
    return {"mint": mint, "name": _name(c, mint), "coin_url": links.coin_url(mint, "solana")}

def kill_reason_group(reason: str) -> str:
    r = (reason or "").lower()
    for key, label in (("liq", "liquidity"), ("top 10", "top 10 holders"), ("top10", "top 10 holders"), ("h1", "1h pump"),
                       ("copy", "copycat"), ("runner", "24h runner"), ("timeout", "waited too long"), ("wait", "waited too long")):
        if key in r: return label
    return re.sub(r"[\d$.,%KMk+-]+", "", r).strip() or "other"

def build(c, start: str, end: str) -> dict:
    s = db.settings_all(c)
    trades = []
    for p in c.execute("SELECT * FROM positions WHERE opened_at>=? AND opened_at<? ORDER BY opened_at", (start, end)):
        pct = None
        if p["exit_price"] and p["entry_price"]: pct = round(100 * (p["exit_price"] / p["entry_price"] - 1), 2)
        trades.append({**_coin(c, p["mint"]), "mode": p["mode"], "opened_at": p["opened_at"], "closed_at": p["closed_at"],
                       "entry": p["entry_price"], "exit": p["exit_price"], "size_usd": p["size_usd"], "pnl_usd": p["pnl_usd"],
                       "pnl_pct": round(100 * p["pnl_usd"] / p["size_usd"], 2) if p["pnl_usd"] is not None and p["size_usd"] else None,
                       "price_pct": pct, "rule": p["exit_reason"] or "open", "fees_usd": p["fees_usd"] or 0.0})
    red = [t for t in trades if (t["pnl_usd"] or 0) < 0]
    green = [t for t in trades if t not in red]
    seen = [r[0] for r in c.execute("SELECT mint FROM coins WHERE migrated_at>=? AND migrated_at<? ORDER BY migrated_at", (start, end))]
    kills = {}
    for r in c.execute("SELECT mint,reason,ts FROM scans WHERE verdict='kill' AND ts>=? AND ts<? ORDER BY ts", (start, end)):
        kills.setdefault(kill_reason_group(r["reason"]), []).append({**_coin(c, r["mint"]), "reason": r["reason"], "ts": r["ts"]})
    refusals = {}
    for r in c.execute("SELECT mint,layer,name,value_text,ts FROM audits WHERE pass=0 AND ts>=? AND ts<? ORDER BY ts", (start, end)):
        refusals.setdefault(f"{r['layer']} {r['name']}", []).append({**_coin(c, r["mint"]), "value": r["value_text"], "ts": r["ts"],
                                                                      "time": db.local_hhmm(r["ts"])})
    rejects = [{**_coin(c, r["mint"]), "rule": r["reason"], "score": r["score"], "ts": r["ts"], "time": db.local_hhmm(r["ts"])}
               for r in c.execute("SELECT mint,reason,score,ts FROM verdicts WHERE pass=0 AND ts>=? AND ts<? ORDER BY ts", (start, end))]
    tokens = c.execute("SELECT COALESCE(SUM(input_tokens),0) FROM verdicts WHERE ts>=? AND ts<?", (start, end)).fetchone()[0]
    fees = round(sum(t["fees_usd"] for t in trades if t["closed_at"]), 2)
    pnl = round(sum(t["pnl_usd"] or 0 for t in trades if t["closed_at"]), 2)
    import datetime as _d
    from zoneinfo import ZoneInfo
    day = _d.datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(ZoneInfo("Europe/Budapest")).date().isoformat()
    return {"night_start": start, "night_end": end, "date": db.local_date(start), "date_iso": day, "mode": s["desk.mode"],
            "analyst_mode": s["analyst.mode"], "red_trades": red, "green_trades": green, "pnl_usd": pnl,
            "coins_seen": [_coin(c, m) for m in seen], "scanner_kills": kills, "auditor_refusals": refusals,
            "analyst_rejects": rejects, "jev": {"tokens": tokens, "cost_usd": round(tokens / 1000 * JEV_USD_PER_1K_TOKENS, 4),
                                                "note": "analyst.mode=rules: Jev not called" if s["analyst.mode"] == "rules" else ""},
            "fees_usd": fees, "footer": "New to FOMO? " + links.SIGNUP_URL}

def _l(x): return f"[{x['name']}]({x['coin_url']})"
def _m(v): return "–" if v is None else (f"-${abs(v):,.2f}" if v < 0 else f"+${v:,.2f}")
def _p(v): return "–" if v is None else f"{v:+.2f}%"
def _px(v): return "–" if v is None else f"{v:.10g}"

def to_markdown(d: dict) -> str:
    L = [f"# Night report · {d['date']} ({d['mode']})", "",
         f"{db.local_hhmm(d['night_start'])} → {db.local_hhmm(d['night_end'])} CET · PnL {_m(d['pnl_usd'])} · fees {_m(-d['fees_usd']) if d['fees_usd'] else '$0.00'}", ""]
    for title, rows in (("Red trades", d["red_trades"]), ("Green trades", d["green_trades"])):
        L += [f"## {title} ({len(rows)})", ""]
        if not rows: L += ["None.", ""]; continue
        L += ["| Coin | Entry | Exit | $ | % | Rule |", "|---|---|---|---|---|---|"]
        for t in rows:
            L.append(f"| {_l(t)} | {_px(t['entry'])} · {db.local_hhmm(t['opened_at'])} | {_px(t['exit'])}"
                     f"{' · ' + db.local_hhmm(t['closed_at']) if t['closed_at'] else ''} | {_m(t['pnl_usd'])} | {_p(t['pnl_pct'])} | {t['rule']} |")
        L.append("")
    L += [f"## Coins seen ({len(d['coins_seen'])})", "", ", ".join(_l(x) for x in d["coins_seen"]) or "None.", ""]
    L += [f"## Scanner kills ({sum(len(v) for v in d['scanner_kills'].values())})", ""]
    for k, v in sorted(d["scanner_kills"].items(), key=lambda kv: -len(kv[1])):
        L.append(f"- **{k}** ({len(v)}): " + ", ".join(f"{_l(x)} ({x['reason']})" for x in v))
    if not d["scanner_kills"]: L.append("None.")
    L += ["", f"## Auditor refusals ({sum(len(v) for v in d['auditor_refusals'].values())})", ""]
    for k, v in sorted(d["auditor_refusals"].items(), key=lambda kv: int(kv[0].split()[0])):
        L.append(f"- **Layer {k}** ({len(v)}): " + ", ".join(f"{_l(x)} · {x['value']} · {x['time']}" for x in v))
    if not d["auditor_refusals"]: L.append("None.")
    L += ["", f"## Analyst rejects ({len(d['analyst_rejects'])})", ""]
    L += [f"- {_l(x)} · {x['rule']} · score {x['score']} · {x['time']}" for x in d["analyst_rejects"]] or ["None."]
    j = d["jev"]
    L += ["", "## Jev", "", f"{j['tokens']:,} tokens · ${j['cost_usd']:.2f}" + (f" ({j['note']})" if j["note"] else ""), "",
          "## Fees paid", "", f"${d['fees_usd']:.2f}", "", "---", "", d["footer"], ""]
    return "\n".join(L)
