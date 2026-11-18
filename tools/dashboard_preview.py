"""Preview of the home / agents / activity screens at 390x844 from a FIXTURE db (coin names end in "· fixture";
synthetic numbers, never real desk data). Writes PNGs to /workspace/output (or argv[1]). Not published, not run by the desk."""
import asyncio, os, random, shutil, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("DESK_LOG_DIR", tempfile.mkdtemp(prefix="desk_prev_logs_"))
import api, db
import acceptance_screens as A

def extra_scans(c):
    """Spread killed/passed coins over the last hours (+1 BNB coin) so the per-hour chart has shape."""
    now = time.time(); random.seed(3)
    reasons = ["no volume", "copycat name", "liq $4.5K", "top 10 38%", "dev sold 40%"]
    for i in range(26):
        t = now - random.uniform(0.2, 11.5) * 3600
        if t < db.parse_iso(db.night_bounds()[0]): continue
        m = f"F{i:02d}" + "x" * 37 + "pump"
        c.execute("INSERT INTO coins(mint,name,symbol,migrated_at) VALUES(?,?,?,?)", (m, f"Coin{i} · fixture", f"C{i}", A.iso(t - 600)))
        c.execute("INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,h1_change_pct,age_min,verdict,reason) VALUES(?,?,?,?,?,?,?,?)",
                  (m, A.iso(t), random.uniform(2000, 30000), 30, 5, 10, "kill", random.choice(reasons)))
    bnb = "0x" + "ab" * 20
    c.execute("INSERT INTO coins(mint,name,symbol,migrated_at) VALUES(?,?,?,?)", (bnb, "Pancake Pup · fixture", "PUP", A.iso(now - 900)))
    c.execute("INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,h1_change_pct,age_min,verdict,reason) VALUES(?,?,?,?,?,?,?,?)",
              (bnb, A.iso(now - 240), 14800, 52, 9, 12, "kill", "top 10 52%"))
    c.commit()

async def shoot(port, out):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                                  user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
        page = await ctx.new_page()
        errs = []; page.on("pageerror", lambda e: errs.append(str(e)))
        await page.goto(f"http://127.0.0.1:{port}/#d={A.FKEY}"); await page.wait_for_timeout(2500)
        shots = []
        for name, h in (("dashboard_preview", "#/home"), ("dashboard_agents", "#/agents"), ("dashboard_activity", "#/activity")):
            await page.goto(f"http://127.0.0.1:{port}/{h}"); await page.wait_for_timeout(2500)
            f = out / f"{name}.png"; await page.screenshot(path=str(f)); shots.append(f)           # 390x844 viewport
            await page.add_style_tag(content=".tabbar{position:static!important}")
            f2 = out / f"{name}_full.png"; await page.screenshot(path=str(f2), full_page=True); shots.append(f2)
            await page.reload(); await page.wait_for_timeout(1500)
        await b.close()
    return shots, errs

def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/output"); out.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="desk_prev_"))
    c, _ = A.full_db(tmp / "full.db"); extra_scans(c); api.write_all(c, tmp / "full")
    site = tmp / "site"; shutil.copytree(ROOT / "web", site); (site / "d").mkdir()
    (site / "d" / A.FKEY).symlink_to(tmp / "full")
    srv, port = A.serve(site)
    shots, errs = asyncio.run(shoot(port, out)); srv.shutdown()
    for f in shots: print(f)
    print("page errors:", errs or "none")
    return 1 if errs else 0

if __name__ == "__main__":
    sys.exit(main())
