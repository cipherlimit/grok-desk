"""Telegram (STEP 9): pushes with FOMO buttons from links.py, long polling, owner only.
One getUpdates loop handles the agents' buttons (Bought / Skip / Sold) and the command set.
TG_TOKEN comes from env only and is never logged (the API URL carries it)."""
import asyncio, json, logging, logging.handlers, re, time
import httpx
from config import secret, OWNER_FILE, LOG_DIR, OUT_DIR

log = logging.getLogger("telegram")
if not log.handlers:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.TimedRotatingFileHandler(LOG_DIR / "telegram.log", when="midnight", backupCount=14)
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s")); log.addHandler(h); log.setLevel(logging.INFO)
    log.propagate = False

def owner_chat_id() -> int | None:
    try: return json.loads(OWNER_FILE.read_text())["owner_chat_id"]
    except Exception: return None

def _set_owner(chat_id: int):
    OWNER_FILE.write_text(json.dumps({"owner_chat_id": chat_id}))

def _api(method):   # the token is in the URL: never log this string
    return f"https://api.telegram.org/bot{secret('TG_TOKEN')}/{method}"

async def send(text: str, buttons: list[list[dict]] | None = None, retries: int = 3) -> bool:
    """1 try + [3] retries to the owner chat. Returns False when Telegram stays down."""
    if not secret("TG_TOKEN") or not owner_chat_id(): return False
    body = {"chat_id": owner_chat_id(), "text": text, "disable_web_page_preview": True}
    if buttons: body["reply_markup"] = {"inline_keyboard": buttons}
    async with httpx.AsyncClient(timeout=10) as cl:
        for i in range(1 + retries):
            try:
                r = await cl.post(_api("sendMessage"), json=body)
                if r.status_code == 200 and r.json().get("ok"):
                    log.info("sent: %s | buttons: %s", text.replace("\n", " | "),
                             json.dumps([b.get("url") or b.get("callback_data") for row in (buttons or []) for b in row]))
                    return True
                log.warning("send HTTP %s (try %d)", r.status_code, i + 1)
            except httpx.HTTPError as e:
                log.warning("send error %s (try %d)", type(e).__name__, i + 1)
            if i < retries: await asyncio.sleep(1)
    log.error("push failed: %s", text.replace("\n", " | "))
    return False

def url_button(text, url): return {"text": text, "url": url}
def cb_button(text, data): return {"text": text, "callback_data": data[:64]}

# ======================================================================== commands
HELP = ("Commands:\n"
        "buy X · sell X · why X\n"
        "pause Scanner · resume Scanner (any agent; Risk not while a position is open)\n"
        "set stop to -8% (any setting by name or alias)\n"
        "stack 1240 · status · paper · live · invite · report · test\n"
        "feed · feed on · feed off (every desk event here; off = mute)\n"
        "X = name, symbol or mint")
AGENTS = ["scanner", "auditor", "analyst", "executor", "risk"]

class Desk:
    """Command handler. Reads the db, writes settings through config.validate + db.set_setting (change_log)."""
    def __init__(self, conn, bus, src):
        self.c, self.bus, self.src = conn, bus, src
        self.pending_live = 0.0
        self.pending_confirm: dict[str, tuple] = {}

    # ---------- helpers ----------
    def resolve(self, x: str):
        """-> ("ok", mint) | ("ask", text, buttons) | ("unknown", text)."""
        import links
        x = x.strip()
        if re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", x):
            return ("ok", x)
        rows = list(self.c.execute("SELECT mint,name,symbol FROM coins WHERE lower(name)=lower(?) OR lower(symbol)=lower(?) "
                                   "OR lower(symbol)=lower(?)", (x, x, x.lstrip("$"))))
        if not rows: return ("unknown", f"I don't know {x}. Send me its mint.")
        if len(rows) == 1: return ("ok", rows[0]["mint"])
        lines = [f"{x} matches {len(rows)} coins. Which one? Reply with the mint:"]
        for r in rows[:8]: lines.append(f"{r['name'] or r['symbol']} · {r['mint']}\n{links.coin_url(r['mint'], 'solana')}")
        return ("ask", "\n".join(lines), None)

    def name(self, mint):
        import api
        return api.coin_name(self.c, mint)

    def coin_line(self, mint):
        import links
        return f"{self.name(mint)}\n{links.coin_url(mint, 'solana')}"

    @staticmethod
    def show(key, v):
        if v is None: return "not set"
        if key in ("desk.stack", "desk.paper_start_stack") or key.endswith("_usd"): return f"${v:,.2f}"
        if key == "risk.stop_pct": return f"-{v:g}%"
        if key.endswith("_pct") or key.endswith("pct"): return f"{v:g}%"
        if key.endswith("_usd"): return f"${v:,.2f}"
        if isinstance(v, bool): return "on" if v else "off"
        if isinstance(v, float): return f"{v:g}"
        return str(v)

    # ---------- dispatcher ----------
    async def handle(self, text: str) -> tuple[str, list | None]:
        t = text.strip()
        if t.startswith("/"): t = t[1:]
        t = re.sub(r"@\w+bot\b", "", t, flags=re.I).strip()
        low = t.lower()
        if t == "LIVE": return await self.confirm_live()
        if low in ("start", "help", "commands", "?"): return HELP, None
        m = re.fullmatch(r"(buy|sell|why)\s+(.+)", t, re.I)
        if m: return await getattr(self, "cmd_" + m.group(1).lower())(m.group(2))
        m = re.fullmatch(r"(pause|resume)\s+(\w+)", low)
        if m: return self.cmd_pause(m.group(1), m.group(2))
        m = re.fullmatch(r"set\s+(.+?)\s+(?:to|=)\s+(.+)", t, re.I)
        if m: return self.cmd_set(m.group(1), m.group(2))
        m = re.fullmatch(r"stack\s+\$?([\d,.]+)", low)
        if m: return self.cmd_set("desk.stack", m.group(1))
        if low == "status": return self.cmd_status()
        if low == "paper": return self.cmd_paper()
        if low == "live":
            self.pending_live = time.time()
            return "Type LIVE (capitals) to switch to LIVE.", None
        if low == "invite":
            import links
            return f"New to FOMO? {links.SIGNUP_URL}", None
        if low == "report": return self.cmd_report()
        if low in ("test", "test push"): return self.cmd_test()
        m = re.fullmatch(r"feed(?:\s+(on|off))?", low)
        if m: return self.cmd_feed(m.group(1))
        return HELP, None

    # ---------- commands ----------
    async def cmd_buy(self, x):
        import db, links
        from agents import auditor as aud
        r = self.resolve(x)
        if r[0] != "ok": return r[1], None
        mint = r[1]; url = links.coin_url(mint, "solana")
        start, _ = db.night_bounds()
        sig = self.c.execute("SELECT * FROM signals WHERE mint=? AND ts>=? AND status!='skipped' ORDER BY id DESC LIMIT 1", (mint, start)).fetchone()
        if sig:
            from agents.executor import push_text
            s = db.settings_all(self.c)
            v = self.c.execute("SELECT score FROM verdicts WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,)).fetchone()
            txt = push_text(sig["mode"], self.name(mint), sig["size_usd"], sig["stack_pct"], v["score"] if v else "?",
                            s["risk.stop_pct"], s["risk.trail_pct"])
            return txt, [[url_button("Buy on FOMO", sig["link"])],
                         [cb_button("Bought", f"bought:{mint}"), cb_button("Skip", f"skip:{mint}")]]
        scan = await aud.adhoc_scan(self.c, self.src, mint)
        if not scan:
            return f"{self.name(mint)}: DexScreener has no PumpSwap pool for it, can't take it apart.\n{url}", [[url_button("Buy on FOMO", url)]]
        out = await aud.Auditor(self.c, self.bus, self.src).audit(scan, adhoc=True)
        lines = [f"{self.name(mint)} · Auditor {'8/8' if out['passed'] else 'red'}"]
        done = {l["n"]: l for l in out["layers"]}
        for n in range(1, 9):
            l = done.get(n)
            lines.append(f"{n} {aud.LAYERS[n]}: " + ((("✓ " if l["pass"] else "✗ ") + l["value"]) if l else "not checked"))
        lines += ["not sized, not on the desk", url]
        return "\n".join(lines), [[url_button("Buy on FOMO", url)]]

    async def cmd_sell(self, x):
        import links
        r = self.resolve(x)
        if r[0] != "ok": return r[1], None
        mint = r[1]; url = links.coin_url(mint, "solana")
        p = self.c.execute("SELECT * FROM positions WHERE mint=? AND closed_at IS NULL ORDER BY id DESC LIMIT 1", (mint,)).fetchone()
        if not p: return f"No open {self.name(mint)} position.\n{url}", None
        return (f"Sell {self.name(mint)} · ${p['size_usd']:.2f} in ({p['mode']})\n{url}",
                [[url_button("Sell on FOMO", url)], [cb_button("Sold", f"sold:{p['id']}")]])

    async def cmd_why(self, x):
        import db, links
        r = self.resolve(x)
        if r[0] != "ok": return r[1], None
        mint = r[1]; url = links.coin_url(mint, "solana"); nm = self.name(mint)
        hh = lambda ts: f"{db.local_hhmm(ts)} CET" if ts else "?"
        p = self.c.execute("SELECT * FROM positions WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,)).fetchone()
        if p and p["closed_at"]:
            return f"{nm}: Risk closed it · rule {p['exit_reason']} · exit {p['exit_price']:.10g} · PnL {p['pnl_usd']:+.2f} · {hh(p['closed_at'])}\n{url}", None
        if p: return f"{nm}: not stopped, open position since {hh(p['opened_at'])}\n{url}", None
        sig = self.c.execute("SELECT * FROM signals WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,)).fetchone()
        if sig and sig["status"] in ("skipped", "missed"):
            reason = self._executor_reason(mint) or sig["status"]
            return f"{nm}: Executor · {reason} · {hh(sig['ts'])}\n{url}", None
        v = self.c.execute("SELECT * FROM verdicts WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,)).fetchone()
        if v and not v["pass"]:
            return f"{nm}: Analyst · {v['reason']} · score {v['score']} · {hh(v['ts'])}\n{url}", None
        a = self.c.execute("SELECT * FROM audits WHERE mint=? AND pass=0 ORDER BY id DESC LIMIT 1", (mint,)).fetchone()
        if a: return f"{nm}: Auditor · layer {a['layer']} {a['name']} · {a['value_text']} · {hh(a['ts'])}\n{url}", None
        sc = self.c.execute("SELECT * FROM scans WHERE mint=? ORDER BY id DESC LIMIT 1", (mint,)).fetchone()
        if sc and sc["verdict"] == "kill":
            return (f"{nm}: Scanner · {sc['reason']} · liq ${sc['liquidity_usd'] or 0:,.0f} · top 10 "
                    f"{'n/a' if sc['top10_pct'] is None else str(sc['top10_pct']) + '%'} · age {sc['age_min']}m · {hh(sc['ts'])}\n{url}"), None
        if sig: return f"{nm}: not stopped, pushed at {hh(sig['ts'])} ({sig['status']})\n{url}", None
        return f"{nm}: nothing stopped it yet (still in the pipeline or never seen)\n{url}", None

    def _executor_reason(self, mint):
        try:
            for line in reversed((LOG_DIR / "executor.log").read_text().splitlines()[-2000:]):
                if f"mint={mint}" in line and "reason=" in line:
                    return line.split("reason=", 1)[1].split(" link=")[0]
        except OSError: pass
        return None

    def cmd_pause(self, verb, agent):
        import db
        agent = agent.lower()
        if agent not in AGENTS: return f"Unknown agent {agent}. Agents: {', '.join(a.capitalize() for a in AGENTS)}", None
        if verb == "pause" and agent == "risk" and db.open_positions(self.c):
            return "Risk can't be paused while a position is open.", None
        db.set_setting(self.c, f"paused.{agent}", verb == "pause", who="telegram")
        return f"{agent.capitalize()} {'paused' if verb == 'pause' else 'running'}.", None

    def cmd_set(self, name, raw):
        import db, config
        from agents.executor import ticket_check
        key = config.resolve_key(name)
        if not key: return f"Unknown setting {name}. Send 'status' or see the Account tab for names.", None
        if key == "desk.mode": return "Use 'paper' or 'live' to switch mode.", None
        raw = raw.strip()
        if key == "risk.stop_pct": raw = raw.lstrip("-")
        try: new = config.validate(key, raw)
        except config.SettingError as e: return f"Refused: {e}", None
        s = db.settings_all(self.c); old = s.get(key)
        if key == "auditor.enabled" and new is False and old:
            last = self.pending_confirm.get(key)
            if not last or time.time() - last[0] > 120:
                self.pending_confirm[key] = (time.time(),)
                return "Turn off 'Take apart before buying'? Coins would reach the Analyst unaudited. Send the same command again within 2 min to confirm.", None
            self.pending_confirm.pop(key, None)
        db.set_setting(self.c, key, new, who="telegram")
        label = name.strip()                     # reply in the user's words: "stop -6% -> -8%"
        reply = f"{label} {self.show(key, old)} -> {self.show(key, new)}"
        if key == "risk.stop_pct":
            reply += "\nNew positions carry it; open ones only if it is tighter."
        if key in ("desk.stack", "executor.stack_pct", "executor.below_min_paper", "executor.below_min_live",
                   "executor.min_ticket_usd", "desk.paper_start_stack"):
            reply += "\n" + ticket_check(db.settings_all(self.c))[1]
        return reply, None

    def cmd_status(self):
        import db, links, api
        s = db.settings_all(self.c)
        bal = api.current_balance(self.c, s)
        lines = [f"{s['desk.mode']} · balance ${bal:,.2f}" + (f" · LIVE stack ${s['desk.stack']:,.2f}" if s['desk.stack'] else "")]
        paused = [a.capitalize() for a in AGENTS if s.get(f"paused.{a}")]
        lines.append("agents: " + ("paused " + ", ".join(paused) if paused else "5 running"))
        buttons = []
        ops = db.open_positions(self.c)
        if not ops: lines.append("no open positions")
        for p in ops:
            url = links.coin_url(p["mint"], "solana")
            lines.append(f"{self.name(p['mint'])} · ${p['size_usd']:.2f} in · stop -{api.pos_stop_pct(dict(p)):g}%\n{url}")
            buttons.append([url_button(f"Sell on FOMO · {self.name(p['mint'])}"[:60], url)])
        return "\n".join(lines), buttons or None

    def cmd_paper(self):
        import db
        db.set_setting(self.c, "desk.mode", "PAPER", who="telegram")
        return "Mode: PAPER.", None

    async def confirm_live(self):
        import db
        if time.time() - self.pending_live > 300:
            return "Send 'live' first, then type LIVE.", None
        self.pending_live = 0
        ok, why = db.full_paper_night(self.c)
        if not ok: return f"Not switching to LIVE: {why}.", None
        s = db.settings_all(self.c)
        if not s["desk.stack"]: return "Set your LIVE stack first: stack 1240", None
        db.set_setting(self.c, "desk.mode", "LIVE", who="telegram")
        from agents.executor import ticket_check
        return "Mode: LIVE. Money moves only when you tap Buy on FOMO and buy inside FOMO.\n" + ticket_check(db.settings_all(self.c))[1], None

    def cmd_test(self):
        """Account tab "Send test push" (plan B goes through Telegram): 1 PAPER push with the links.py button."""
        import links
        r = self.c.execute("SELECT mint FROM coins ORDER BY migrated_at DESC LIMIT 1").fetchone()
        if not r: return "PAPER · Test push · Grok Bot · now\nNo coin seen yet.", [[url_button("Open FOMO", links.SIGNUP_URL)]]
        url = links.coin_url(r["mint"], "solana")
        return (f"PAPER · Test push · Grok Bot · now\n{self.name(r['mint'])} · test only, no position, no order\n{url}",
                [[url_button("Buy on FOMO", url)]])

    def cmd_feed(self, arg=None):
        """feed on / feed off / feed: the live event feed (agents/feed.py). Default on."""
        import db
        from agents.feed import good_liq_usd
        if arg: db.set_setting(self.c, "feed.enabled", arg == "on", who="telegram")
        s = db.settings_all(self.c)
        on = bool(s.get("feed.enabled", True))
        thr = good_liq_usd(s)
        src = "your setting" if (s.get("feed.good_liq_usd") or 0) > 0 else "the Scanner's min liquidity"
        if arg == "off": return "Feed off: desk events are no longer sent here. Send `feed on` to turn it back on.", None
        head = "Feed on: you get every desk event here." if on else "Feed off (muted). Send `feed on` to turn it back on."
        return (f"{head}\nGood-liquidity alert at ${thr:,.0f} ({src}). Change it with `set good liq to 30000` "
                f"(0 = follow the Scanner).\nSend `feed off` to mute."), None

    def cmd_report(self):
        p = OUT_DIR / "last_page_url.txt"
        if p.exists(): return f"Last morning Page: {p.read_text().strip()}", None
        reps = sorted(OUT_DIR.glob("night_report_*.md"))
        if reps: return f"No Page link yet. Last night report on the desk: {reps[-1].name}", None
        return "No morning Page yet.", None

# ======================================================================== poller
async def poll(conn, bus, src):
    """Long polling. Owner lock: the first /start becomes owner_chat_id, everyone else is ignored."""
    if not secret("TG_TOKEN"): return
    desk = Desk(conn, bus, src)
    offset = None
    async with httpx.AsyncClient(timeout=40) as cl:
        while True:
            try:
                params = {"timeout": 30, "allowed_updates": json.dumps(["message", "callback_query"])}
                if offset: params["offset"] = offset
                r = await cl.get(_api("getUpdates"), params=params)
                if r.status_code != 200:
                    log.warning("getUpdates HTTP %s", r.status_code); await asyncio.sleep(5); continue
                for u in r.json().get("result") or []:
                    offset = u["update_id"] + 1
                    try: await _route(cl, desk, bus, u)
                    except Exception as e: log.exception("update error: %s", type(e).__name__)
            except Exception as e:
                log.warning("getUpdates error %s", type(e).__name__); await asyncio.sleep(5)

async def _route(cl, desk, bus, u):
    q = u.get("callback_query")
    if q:
        chat = ((q.get("message") or {}).get("chat") or {}).get("id")
        await cl.post(_api("answerCallbackQuery"), json={"callback_query_id": q["id"]})
        if chat != owner_chat_id(): return
        action, _, arg = (q.get("data") or "").partition(":")
        log.info("callback %s %s", action, arg)
        if action in ("bought", "skip"): await bus.put("cb.executor", {"action": action, "mint": arg})
        elif action == "sold": await bus.put("cb.risk", {"action": "sold", "position_id": arg})
        return
    m = u.get("message") or {}
    chat, text = (m.get("chat") or {}).get("id"), (m.get("text") or "").strip()
    if not text or chat is None: return
    if owner_chat_id() is None and text.startswith("/start"):
        _set_owner(chat); log.info("owner lock set")
    if chat != owner_chat_id(): return                 # owner only
    log.info("command: %s", text[:200])
    reply, buttons = await desk.handle(text)
    await send(reply, buttons)

poll_callbacks = poll   # backwards name
