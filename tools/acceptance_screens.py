"""STEP 13 test 8: 390px PNG screenshots of every screen + its empty and error state.
Builds 2 data sets with api.write_all (the real writer):
  full  = a FIXTURE db (labelled: coin names end in "· fixture"; real mints, synthetic numbers),
  empty = a fresh db (schema + default settings only),
serves web/ + d/<fixture key>/ on localhost and shoots headless Chrome at 390px.
error = full data loaded once (kept in localStorage), then the data path goes away (fetch fails)."""
import asyncio, json, math, os, random, shutil, socket, sys, tempfile, threading, time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DESK_LOG_DIR", tempfile.mkdtemp(prefix="desk_screens_logs_"))
import db, api, links

OUT = ROOT / "logs" / "acceptance" / "screens"
FKEY = "fixture" + "0" * 33      # not the real DATA_PATH: a 40-char placeholder for the local server
iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

PIKA, MARV, WIG, STOP = ("Fz4HmKzMZ9C3LGENsiEd6sJ9r23uQHcEmzhEF8wnpump", "HJB7wrMMgakSbBeCoHrw3fyVtR3j7ummQt5CbSJVpump",
                         "B1278eTdn8B5Ark6bvQYC9vGyqdGt2VSVbdADx1Spump", "5S9XiLv1Ae6xsCN4KSsbnC5mDR3nqtZrY6DqA18zpump")

def full_db(path):
    c = db.connect(path); c.executescript(db.SCHEMA); db.seed_settings(c)
    now = time.time(); start = db.parse_iso(db.night_bounds()[0])
    t0 = max(start + 60, now - 3 * 3600)
    coins = [(PIKA, "PIKA COIN · fixture", "PIKA", now - 95 * 60), (MARV, "Marvin · fixture", "MARV", now - 70 * 60),
             (WIG, "WIGGLES · fixture", "WIGL", now - 150 * 60), (STOP, "Shipyard · fixture", "SHIP", now - 160 * 60)]
    for m, n, s, t in coins:
        c.execute("INSERT INTO coins(mint,name,symbol,pool,migrated_at,creator,creator_balance_at_migration,holders) VALUES(?,?,?,?,?,?,?,?)",
                  (m, n, s, "pool" + m[:6], iso(t), "creator" + m[:4], 1e12, 412 if m == PIKA else None))
    kills = [("Stumpy · fixture", "liq $9.0K"), ("GTA6 · fixture", "top 10 41%"), ("Amigo · fixture", "copy of AMIGO")]
    for i, (n, r) in enumerate(kills):
        m = f"K{i + 1}xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxpump"
        c.execute("INSERT INTO coins(mint,name,symbol,migrated_at) VALUES(?,?,?,?)", (m, n, n[:4].upper(), iso(now - (40 - i * 5) * 60)))
        c.execute("INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,h1_change_pct,age_min,verdict,reason) VALUES(?,?,?,?,?,?,?,?)",
                  (m, iso(now - (30 - i * 5) * 60), 9000 + i * 4000, 41, 12, 10, "kill", r))
    for m, t in ((PIKA, now - 84 * 60), (MARV, now - 59 * 60), (WIG, now - 140 * 60), (STOP, now - 150 * 60)):
        c.execute("INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,h1_change_pct,age_min,verdict,reason) VALUES(?,?,?,?,?,?,?,?)",
                  (m, iso(t), 81200, 16.8, 34, 11, "pass", None))
    vals = {1: "Revoked", 2: "Revoked", 3: "0.9% · sold 0", 4: "16.8%", 5: "100% burned", 6: "43m", 7: "None", 8: "41 wallets → 9fQx...T2a"}
    from agents.auditor import LAYERS
    for m, t in ((PIKA, now - 83 * 60), (WIG, now - 139 * 60), (STOP, now - 149 * 60)):
        for n in range(1, 9):
            c.execute("INSERT INTO audits(mint,ts,layer,name,value_text,pass,source) VALUES(?,?,?,?,?,?,?)",
                      (m, iso(t + n), n, LAYERS[n], vals[n], 1, "rugcheck" if n in (5, 7) else "rpc"))
    for n in range(1, 8):
        bad = n == 7
        c.execute("INSERT INTO audits(mint,ts,layer,name,value_text,pass,source) VALUES(?,?,?,?,?,?,?)",
                  (MARV, iso(now - 58 * 60 + n), n, LAYERS[n], "7 bundled wallets" if bad else vals[n], 0 if bad else 1, "rugcheck" if n in (5, 7) else "rpc"))
    db.activity(c, "rejected", "auditor", "Rejected Marvin · fixture", "Auditor · 7 bundled wallets", "Skipped", MARV)
    for m, t, sc in ((PIKA, now - 82 * 60, 82), (WIG, now - 138 * 60, 77), (STOP, now - 148 * 60, 74)):
        c.execute("INSERT INTO verdicts(mint,ts,alive_6h,organic,flush,chase,score,pass,reason,input_tokens) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (m, iso(t), .8, .7, .1, .2, sc, 1, None, 0))
        c.execute("INSERT INTO signals(mint,ts,price,size_usd,stack_pct,link,mode,status) VALUES(?,?,?,?,?,?,?,?)",
                  (m, iso(t + 5), 1e-5, 16.0, 8, links.coin_url(m), "PAPER", "bought"))
    # closed: WIGGLES trailed out green, Shipyard stopped red; open: PIKA
    c.execute("INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,trail_active,closed_at,exit_price,exit_reason,pnl_usd,fees_usd,mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (STOP, iso(now - 148 * 60), 1e-5, 16, 1.02e-5, 0.94e-5, 0, iso(now - 120 * 60), 0.939e-5, "stop", -1.55, 0.57, "PAPER"))
    c.execute("INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,trail_active,closed_at,exit_price,exit_reason,pnl_usd,fees_usd,mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (WIG, iso(now - 138 * 60), 1e-5, 16, 1.45e-5, 0.94e-5, 1, iso(now - 100 * 60), 1.27e-5, "trail", 3.69, 0.63, "PAPER"))
    c.execute("INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,trail_active,mode) VALUES(?,?,?,?,?,?,?,?)",
              (PIKA, iso(now - 82 * 60), 1.0e-5, 16, 1.31e-5, 0.94e-5, 1, "PAPER"))
    for kind, title, sub, val, m, t in (("bought", "Bought Shipyard · fixture", "Executor · " + db.local_hhmm(iso(now - 148 * 60)), "$16.00 · 8% of stack", STOP, now - 148 * 60),
                                        ("stopped", "Stopped Shipyard · fixture", "Risk · -6% stop", "-$1.55", STOP, now - 120 * 60),
                                        ("bought", "Bought WIGGLES · fixture", "Executor · " + db.local_hhmm(iso(now - 138 * 60)), "$16.00 · 8% of stack", WIG, now - 138 * 60),
                                        ("sold", "Sold WIGGLES · fixture", "Risk · trailing stop", "+$3.69 · +23.1%", WIG, now - 100 * 60),
                                        ("bought", "Bought PIKA COIN · fixture", "Executor · " + db.local_hhmm(iso(now - 82 * 60)), "$16.00 · 8% of stack", PIKA, now - 82 * 60)):
        c.execute("INSERT INTO activity(ts,kind,mint,agent,title,subtitle,value) VALUES(?,?,?,?,?,?,?)", (iso(t), kind, m, kind, title, sub, val))
    c.execute("UPDATE activity SET ts=? WHERE kind='rejected'", (iso(now - 58 * 60),))
    random.seed(7); p, vol = 1.0e-5, 0
    for k in range(82 * 4):
        t = now - 82 * 60 + k * 15
        p *= 1 + random.gauss(0.0009, 0.012); vol += abs(random.gauss(300, 200))
        c.execute("INSERT INTO prices(mint,ts,price,volume_m5) VALUES(?,?,?,?)", (PIKA, iso(t), p, vol))
    last_p = p
    bal = 200.0
    for k in range(int((now - t0) / 60)):
        t = t0 + k * 60
        if abs(t - (now - 120 * 60)) < 30: bal -= 1.55
        if abs(t - (now - 100 * 60)) < 30: bal += 3.69
        mtm = 0
        if t > now - 82 * 60: mtm = 16 * (math.sin((t - now) / 900) * 0.04 + (t - (now - 82 * 60)) / (82 * 60) * 0.25)
        c.execute("INSERT INTO balance(ts,value) VALUES(?,?)", (iso(t), round(bal + mtm, 2)))
    for a in ("scanner", "auditor", "analyst", "executor", "risk", "main"):
        c.execute("INSERT OR REPLACE INTO heartbeats(agent,ts) VALUES(?,?)", (a, iso(now)))
    c.commit()
    db.LAST_PAIR[PIKA] = {"name": "PIKA COIN · fixture", "symbol": "PIKA", "market_cap": 1_310_000, "liquidity_usd": 81_200,
                          "volume_h24": 2_450_000, "pair_created_ms": (now - 11 * 3600) * 1000, "price_change_h24": 31}
    return c, last_p

def empty_db(path):
    c = db.connect(path); c.executescript(db.SCHEMA); db.seed_settings(c)
    for a in ("scanner", "auditor", "analyst", "executor", "risk", "main"):
        c.execute("INSERT OR REPLACE INTO heartbeats(agent,ts) VALUES(?,?)", (a, db.now_iso()))
    c.commit(); return c

class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass
    def end_headers(self):
        self.send_header("Cache-Control", "no-store"); super().end_headers()

def serve(root):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    h = lambda *a, **k: Quiet(*a, directory=str(root), **k)
    srv = ThreadingHTTPServer(("127.0.0.1", port), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port

SCREENS = [("home", "#/home"), ("agents", "#/agents"), ("activity", "#/activity"),
           ("auditor", "#/audit/" + MARV), ("auditor_cleared", "#/audit/" + PIKA), ("coin", "#/coin/" + PIKA), ("account", "#/account")]
EMPTY = [("home", "#/home"), ("agents", "#/agents"), ("activity", "#/activity"), ("auditor", "#/audit"),
         ("coin", "#/coin/" + PIKA), ("account", "#/account")]

async def shoot(port, site, data_full, data_empty):
    from playwright.async_api import async_playwright
    shots = []
    link = site / "d" / FKEY
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        async def ctx():
            return await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1, is_mobile=True, has_touch=True,
                                       user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
        async def snap(page, name, url, wait=3.5):
            await page.goto(f"http://127.0.0.1:{port}/{url}"); await page.wait_for_timeout(int(wait * 1000))
            # full-page capture only: the fixed tab bar is drawn at the end of the page instead of mid-page
            await page.add_style_tag(content=".tabbar{position:static!important}")
            f = OUT / f"{name}.png"; await page.screenshot(path=str(f), full_page=True); shots.append(f)
            if name in ("account", "home", "coin", "auditor"): (OUT / f"{name}.html").write_text(await page.content())
        # full + error, same browser storage
        if link.exists() or link.is_symlink(): link.unlink()
        link.symlink_to(data_full)
        c = await ctx(); page = await c.new_page()
        await page.goto(f"http://127.0.0.1:{port}/#d={FKEY}"); await page.wait_for_timeout(1500)
        for n, h in SCREENS: await snap(page, n, h, 4.5 if n.startswith("auditor") else 3)
        link.unlink()                                               # desk offline: every fetch 404s now
        for n, h in SCREENS[:1] + SCREENS[1:3] + SCREENS[3:4] + SCREENS[5:]: await snap(page, n + "_error", h, 2.5)
        await c.close()
        # loading state (no data yet, fetch pending forever)
        c = await ctx(); page = await c.new_page()
        await page.route("**/d/**", lambda route: None)              # never answer
        await page.goto(f"http://127.0.0.1:{port}/#d={FKEY}"); await page.wait_for_timeout(1200)
        await page.add_style_tag(content=".tabbar{position:static!important}")
        f = OUT / "home_loading.png"; await page.screenshot(path=str(f), full_page=True); shots.append(f); await c.close()
        # empty
        link.symlink_to(data_empty)
        c = await ctx(); page = await c.new_page()
        await page.goto(f"http://127.0.0.1:{port}/#d={FKEY}"); await page.wait_for_timeout(1500)
        for n, h in EMPTY: await snap(page, n + "_empty", h, 3)
        # empty coin screen = coin without a position: no exit card (from the full set, Marvin has no position)
        await c.close(); await b.close()
    link.unlink()
    return shots

def main():
    if OUT.exists(): shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    tmp = Path(tempfile.mkdtemp(prefix="desk_screens_"))
    cf, _ = full_db(tmp / "full.db"); api.write_all(cf, tmp / "full")
    db.LAST_PAIR.clear()
    ce = empty_db(tmp / "empty.db"); api.write_all(ce, tmp / "empty")
    # the empty coin screen: a coin with no position -> no exit card (Marvin from the fixture)
    (tmp / "empty" / "coin").mkdir(exist_ok=True)
    shutil.copy(tmp / "full" / "coin" / f"{MARV}.json", tmp / "empty" / "coin" / f"{PIKA}.json")
    site = tmp / "site"; shutil.copytree(ROOT / "web", site); (site / "d").mkdir()
    srv, port = serve(site)
    shots = asyncio.run(shoot(port, site, tmp / "full", tmp / "empty"))
    srv.shutdown()
    from PIL import Image
    for f in shots:
        w, h = Image.open(f).size
        print(f"{f.relative_to(ROOT)}  {w}x{h}")
    print(f"{len(shots)} screenshots, all 390px wide: {all(Image.open(f).size[0] == 390 for f in shots)}")

if __name__ == "__main__":
    main()
