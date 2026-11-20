"""Feed (user 2026-11-18): every desk event to the owner chat of the Telegram bot.
Tails new rows from coins, scans, audits, verdicts, signals, positions, activity and change_log
after a cursor persisted in var/feed_state.json, plus in-process notes (websocket reconnects,
task deaths) and heartbeat up/down changes. Events go to a persisted outbox first, so a restart
or a Telegram outage never drops one; sends are batched (~1 msg/s, split under 4096 chars).
Informational only: it never places an order and never says buy. Secrets are never in a message."""
import asyncio, collections, json, os, time
import db, links, chains
from config import ROOT
from agents.base import agent_logger

NAME = "feed"
log = agent_logger(NAME)
STATE = ROOT / "var" / "feed_state.json"
TICK_S, GAP_S, MAX_CHARS = 3, 1.2, 3900
HB_AGENTS = ["scanner", "auditor", "analyst", "executor", "risk", "main"]
STALE_S = 120
DEFAULT_GOOD_LIQ = 20_000
_NOTES: collections.deque = collections.deque()     # in-process events (ws reconnects, task deaths)

def note(text: str):
    """Any agent can drop a one-line event here; the feed loop picks it up within a few seconds."""
    _NOTES.append(f"{time.strftime('%H:%M:%S')} {text}")

# ---------------------------------------------------------------- formatting
def k(usd):
    if usd is None: return "?"
    if usd >= 1_000_000: return f"${usd/1_000_000:.1f}M"
    if usd >= 1_000: return f"${usd/1_000:.1f}K"
    return f"${usd:.0f}"

def pct(x, sign=False):
    if x is None: return "?"
    return f"{x:+.0f}%" if sign else f"{x:.0f}%"

def hhmm(iso):
    try: return db.local_hhmm(iso)
    except Exception: return "--:--"

def good_liq_usd(s: dict, mint=None) -> float:
    """feed.good_liq_usd if set (>0); else the Scanner's minimum liquidity; else $20K.
    For a BNB mint, never below scanner.bnb_min_liquidity_usd when that bar is >0."""
    v = s.get("feed.good_liq_usd") or 0
    base = float(v) if v and v > 0 else float(s.get("scanner.min_liquidity_usd") or DEFAULT_GOOD_LIQ)
    if mint and chains.is_evm(mint):
        bnb_bar = float(s.get("scanner.bnb_min_liquidity_usd") or 0)
        if bnb_bar > 0:
            return max(base, bnb_bar)
    return base

def only_clean(s: dict) -> bool:
    """Default on: Telegram only gets coins that cleared every check (and system events)."""
    v = s.get("feed.only_clean")
    return True if v is None else bool(v)

def coin_cleared(c, mint) -> bool:
    """Scanner pass + every auditor layer green + analyst pass, tonight."""
    start, _ = db.night_bounds()
    if not c.execute("SELECT 1 FROM scans WHERE mint=? AND ts>=? AND verdict='pass'", (mint, start)).fetchone():
        return False
    if not c.execute("SELECT 1 FROM audits WHERE mint=? AND ts>=?", (mint, start)).fetchone():
        return False
    if c.execute("SELECT 1 FROM audits WHERE mint=? AND ts>=? AND pass=0", (mint, start)).fetchone():
        return False
    row = c.execute("SELECT pass FROM verdicts WHERE mint=? AND ts>=? ORDER BY id DESC LIMIT 1", (mint, start)).fetchone()
    return bool(row and row["pass"])

class Feed:
    def __init__(self, conn, state_path=None):
        self.c, self.path = conn, state_path or STATE
        self.st = self._load()

    # ---------- state ----------
    def _load(self) -> dict:
        try: st = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError): st = {}
        st.setdefault("cur", None); st.setdefault("outbox", []); st.setdefault("pos", {})
        st.setdefault("sig", {}); st.setdefault("hb_down", []); st.setdefault("alerted", [])
        return st

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.st), encoding="utf-8")
        os.replace(tmp, self.path)

    def _max(self, sql):
        return self.c.execute(sql).fetchone()[0] or 0

    def heads(self) -> dict:
        return {"coins": self._max("SELECT MAX(rowid) FROM coins"), "scans": self._max("SELECT MAX(id) FROM scans"),
                "audits": self._max("SELECT MAX(id) FROM audits"), "verdicts": self._max("SELECT MAX(id) FROM verdicts"),
                "signals": self._max("SELECT MAX(id) FROM signals"), "positions": self._max("SELECT MAX(id) FROM positions"),
                "activity": self._max("SELECT MAX(id) FROM activity"), "change_log": self._max("SELECT MAX(rowid) FROM change_log")}

    def start(self, mode: str):
        """First run ever: start at the current db head (no history flood). Later runs resume from the cursor."""
        if self.st["cur"] is None:
            self.st["cur"] = self.heads()
            self.st["pos"] = {str(r["id"]): self._pos_snap(r) for r in self.c.execute("SELECT * FROM positions WHERE closed_at IS NULL")}
            self.st["sig"] = {str(r["id"]): r["status"] for r in self.c.execute("SELECT id,status FROM signals WHERE status='pending'")}
        self.push(f"🟦 Desk started · {mode} · feed on")
        self.save()

    # ---------- outbox ----------
    def push(self, text, url=None, alone=False):
        self.st["outbox"].append({"t": text, "u": url, "a": alone})

    def name(self, mint):
        import api
        try: return api.coin_name(self.c, mint)
        except Exception: return mint[:4] + "…" + mint[-4:]

    # ---------- collect ----------
    def collect(self, enabled: bool, s: dict):
        """Move every new event after the cursor into the outbox (or drop it when muted), advance the cursor."""
        cur = self.st["cur"]; ev = []          # (text, url, alone)
        clean = only_clean(s)
        for r in self.c.execute("SELECT rowid AS rid, * FROM coins WHERE rowid>? ORDER BY rowid", (cur["coins"],)):
            cur["coins"] = r["rid"]
            if clean: continue
            bnb = " · BNB Chain (PancakeSwap)" if r["chain"] == chains.BNB else ""
            ev.append((f"🆕 New coin graduated · {r['name'] or r['symbol'] or 'unnamed'} · {r['mint']}{bnb}", None, False))
        for r in self.c.execute("SELECT * FROM scans WHERE id>? ORDER BY id", (cur["scans"],)):
            cur["scans"] = r["id"]
            if clean: continue
            nm = self.name(r["mint"])
            icon = "✅ PASS" if r["verdict"] == "pass" else "❌ KILL"
            age = f"{r['age_min']:.0f}m" if r["age_min"] is not None else "?"
            ev.append((f"🔎 Scanner {icon} · {nm} · liq {k(r['liquidity_usd'])} · top10 {pct(r['top10_pct'])} · "
                       f"1h {pct(r['h1_change_pct'], True)} · age {age}" + (f" · {r['reason']}" if r["reason"] else "")
                       + f"\n{r['mint']}", None, False))
        for r in self.c.execute("SELECT * FROM audits WHERE id>? ORDER BY id", (cur["audits"],)):
            cur["audits"] = r["id"]
            if clean: continue
            ev.append((f"🛡 Auditor L{r['layer']} {r['name']} {'pass' if r['pass'] else 'FAIL'} · {self.name(r['mint'])} · "
                       f"{r['value_text']} ({r['source']})", None, False))
        for r in self.c.execute("SELECT * FROM verdicts WHERE id>? ORDER BY id", (cur["verdicts"],)):
            cur["verdicts"] = r["id"]
            if r["pass"]:
                ev += self._clean_alert(s, r)
            elif not clean:
                ev.append((f"🧠 Analyst REJECT · {self.name(r['mint'])} · score {r['score']}"
                           + (f" · {r['reason']}" if r["reason"] else ""), None, False))
        for r in self.c.execute("SELECT * FROM signals WHERE id>? ORDER BY id", (cur["signals"],)):
            cur["signals"] = r["id"]
            self.st["sig"][str(r["id"])] = r["status"]
            ev.append((f"📣 Executor signal #{r['id']} · {r['mode']} · {self.name(r['mint'])} · ${r['size_usd'] or 0:.2f} "
                       f"({r['stack_pct'] or 0:g}% of stack) · {r['status']}", None, False))
        for sid, old in list(self.st["sig"].items()):
            row = self.c.execute("SELECT * FROM signals WHERE id=?", (int(sid),)).fetchone()
            if row is None: self.st["sig"].pop(sid); continue
            if row["status"] != old:
                ev.append((f"📣 Signal #{sid} · {self.name(row['mint'])} · {old} → {row['status']}", None, False))
                self.st["sig"][sid] = row["status"]
            if row["status"] != "pending": self.st["sig"].pop(sid, None)
        for r in self.c.execute("SELECT * FROM positions WHERE id>? ORDER BY id", (cur["positions"],)):
            cur["positions"] = r["id"]
            ev.append((f"📈 Position #{r['id']} opened · {r['mode']} · {self.name(r['mint'])} · ${r['size_usd']:.2f} @ {r['entry_price']:.8g} "
                       f"· stop {r['stop_price']:.8g}", None, False))
            if r["closed_at"] is None: self.st["pos"][str(r["id"])] = self._pos_snap(r)
        ev += self._risk_changes()
        for r in self.c.execute("SELECT * FROM activity WHERE id>? ORDER BY id", (cur["activity"],)):
            cur["activity"] = r["id"]
            if clean and r["kind"] == "rejected":
                continue
            ev.append((f"📋 {r['agent'] or 'desk'} · {r['kind']} · {r['title'] or ''}"
                       + (f" · {r['subtitle']}" if r["subtitle"] else "") + (f" · {r['value']}" if r["value"] else ""), None, False))
        for r in self.c.execute("SELECT rowid AS rid, * FROM change_log WHERE rowid>? ORDER BY rowid", (cur["change_log"],)):
            cur["change_log"] = r["rid"]
            ev.append((f"⚙️ Setting {r['key']}: {r['old']} → {r['new']} (by {r['who']})", None, False))
        ev += self._heartbeats()
        while _NOTES: ev.append(("⚠️ " + _NOTES.popleft(), None, False))
        if enabled:
            for t, u, a in ev: self.push(t, u, a)
        elif ev:
            log.info("muted: %d events not sent", len(ev))
        return len(ev)

    def _clean_alert(self, s, verdict):
        """Fire once a coin has passed Scanner + Auditor + Analyst (not on Scanner pass alone)."""
        mint = verdict["mint"]
        if mint in self.st["alerted"] or not coin_cleared(self.c, mint):
            return []
        self.st["alerted"] = (self.st["alerted"] + [mint])[-500:]
        scan = self.c.execute("SELECT * FROM scans WHERE mint=? AND verdict='pass' ORDER BY id DESC LIMIT 1", (mint,)).fetchone()
        nm = self.name(mint)
        liq = scan["liquidity_usd"] if scan else None
        thr = good_liq_usd(s, mint)
        age = f"{scan['age_min']:.0f}m" if scan and scan["age_min"] is not None else "?"
        top10 = pct(scan["top10_pct"]) if scan else "?"
        h1 = pct(scan["h1_change_pct"], True) if scan else "?"
        url = links.coin_url(mint)
        bnb = " · BNB Chain" if chains.is_evm(mint) else ""
        text = (f"🟢 CLEAN · {nm}{bnb}\n"
                f"liq {k(liq)} (threshold {k(thr)}) · top10 {top10} · 1h {h1} · age {age}\n"
                f"Scanner pass · Auditor all green · Analyst {verdict['score']}"
                + (f" · {verdict['reason']}" if verdict["reason"] else "") + "\n"
                f"Info only, not a signal: a trade still needs your OK.\n"
                f"FOMO: {url}\nDexScreener: {chains.dexscreener_url(mint)}\n{mint}")
        return [(text, url, True)]

    @staticmethod
    def _pos_snap(r):
        return {"stop": r["stop_price"], "trail": r["trail_active"], "exit": r["exit_reason"], "closed": r["closed_at"]}

    def _risk_changes(self):
        ev = []
        for pid, old in list(self.st["pos"].items()):
            r = self.c.execute("SELECT * FROM positions WHERE id=?", (int(pid),)).fetchone()
            if r is None: self.st["pos"].pop(pid); continue
            new, nm = self._pos_snap(r), self.name(r["mint"])
            if new["stop"] != old["stop"]:
                ev.append((f"🛑 Risk · {nm} · stop {old['stop']:.8g} → {new['stop']:.8g}", None, False))
            if new["trail"] != old["trail"]:
                ev.append((f"🛑 Risk · {nm} · trailing stop {'ON' if new['trail'] else 'off'}", None, False))
            if new["exit"] != old["exit"] and new["exit"]:
                ev.append((f"🛑 Risk · {nm} · exit: {new['exit']} @ {r['exit_price'] or 0:.8g}", None, False))
            if new["closed"] and not old["closed"]:
                ev.append((f"📉 Position #{pid} closed · {nm} · {r['exit_reason'] or ''} · P&L ${r['pnl_usd'] or 0:+.2f} "
                           f"(fees ${r['fees_usd'] or 0:.2f})", None, False))
                self.st["pos"].pop(pid); continue
            self.st["pos"][pid] = new
        return ev

    def _heartbeats(self):
        now, ev = time.time(), []
        hb = {r["agent"]: db.parse_iso(r["ts"]) for r in self.c.execute("SELECT agent,ts FROM heartbeats")}
        down = set(self.st["hb_down"])
        for a in HB_AGENTS:
            stale = a not in hb or now - hb[a] > STALE_S
            if stale and a not in down:
                down.add(a)
                why = f"no heartbeat for {int((now - hb[a]) / 60)} min" if a in hb else "no heartbeat yet"
                ev.append((f"🔻 {a.capitalize()} DOWN ({why})", None, False))
            elif not stale and a in down:
                down.discard(a); ev.append((f"🔺 {a.capitalize()} back UP", None, False))
        self.st["hb_down"] = sorted(down)
        return ev

    # ---------- send ----------
    def next_message(self):
        """-> (text, url, count) for the head of the outbox: a lone alert, or as many plain lines as fit."""
        box = self.st["outbox"]
        if not box: return None
        if box[0]["a"]: return box[0]["t"][:4000], box[0]["u"], 1
        lines, n, size = [], 0, 0
        for item in box:
            if item["a"]: break
            t = item["t"]
            if len(t) > MAX_CHARS: t = t[:MAX_CHARS - 1] + "…"
            if lines and size + len(t) + 1 > MAX_CHARS: break
            lines.append(t); size += len(t) + 1; n += 1
        return "\n".join(lines), None, n

async def run(conn, bus=None, src=None, sleep=asyncio.sleep):
    import telegram
    f = Feed(conn)
    f.start(db.settings_all(conn)["desk.mode"])
    log.info("start, outbox %d", len(f.st["outbox"]))
    backoff = 5
    while True:
        try:
            s = db.settings_all(conn)
            enabled = bool(s.get("feed.enabled", True))
            f.collect(enabled, s)
            if not enabled: f.st["outbox"] = []
            f.save()
            while enabled and f.st["outbox"]:
                text, url, n = f.next_message()
                buttons = [[telegram.url_button("Open on FOMO", url)]] if url else None
                if await telegram.send(text, buttons, retries=0):
                    del f.st["outbox"][:n]; f.save(); backoff = 5
                    await sleep(GAP_S)
                else:
                    log.warning("send failed, %d events kept, retry in %ss", len(f.st["outbox"]), backoff)
                    await sleep(backoff); backoff = min(backoff * 2, 120)
                    break
        except Exception as e:
            log.exception("feed error: %s", type(e).__name__)
        await sleep(TICK_S)
