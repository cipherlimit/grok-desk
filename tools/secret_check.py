"""STEP 13 test 7: secret grep. Never prints a value: only names, counts and file names.
Searches web/, logs/, the repo (working tree + full git history), output/ (the plan B JSON = what gets
published, and the Page markdown) for TYPESAFE_API_KEY, TG_TOKEN, VAPID private key, APP_TOKEN, DATA_KEY,
HELIUS_API_KEY values; DATA_PATH must not be in the repo or its history. Then probes the live site:
/d/ and a path without DATA_PATH -> 404, the real path -> 200."""
import base64, os, re, subprocess, sys, urllib.request, urllib.error
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from config import secret
SITE = "https://cipherlimit.github.io/grok-desk"

def values():
    v = {}
    for k in ("TYPESAFE_API_KEY", "TG_TOKEN", "APP_TOKEN", "DATA_KEY", "HELIUS_API_KEY", "DATA_PATH"):
        x = secret(k)
        if x: v[k] = [x]
    bsc = secret("BSC_RPC_URL")                          # BNB step: optional keyed RPC URL (the free default is public)
    if bsc and bsc != "https://bsc-rpc.publicnode.com": v["BSC_RPC_URL"] = [bsc]
    pem = secret("VAPID_PRIVATE_KEY_FILE")
    if pem and Path(pem).exists():
        from cryptography.hazmat.primitives import serialization
        txt = Path(pem).read_text()
        lines = [l for l in txt.splitlines() if l and not l.startswith("-----") and len(l) >= 16]
        key = serialization.load_pem_private_key(txt.encode(), None)
        d = key.private_numbers().private_value.to_bytes(32, "big")
        v["VAPID_PRIVATE_KEY"] = lines + [base64.urlsafe_b64encode(d).decode().rstrip("="), d.hex()]
    v = {k: [x for x in xs if len(x) >= 12] for k, xs in v.items()}
    return v

def scan_tree(paths, vals, skip=()):
    hits = {}
    for base in paths:
        for f in ([base] if base.is_file() else base.rglob("*")):
            if not f.is_file() or any(s in f.parts for s in skip): continue
            try: t = f.read_bytes()
            except OSError: continue
            for k, xs in vals.items():
                if any(x.encode() in t for x in xs): hits.setdefault(k, []).append(str(f.relative_to(ROOT)))
    return hits

def probe(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Cache-Control": "no-cache"}), timeout=20) as r: return r.status
    except urllib.error.HTTPError as e: return e.code
    except Exception as e: return type(e).__name__

def main():
    vals = values()
    print("secrets known (names only):", ", ".join(f"{k}x{len(v)}" for k, v in sorted(vals.items())))
    ok = True
    no_path = {k: v for k, v in vals.items() if k != "DATA_PATH"}
    for label, paths, vs in (("web/", [ROOT / "web"], vals), ("output/ (plan B JSON + Page)", [ROOT / "output"], no_path),
                             ("logs/", [ROOT / "logs"], vals)):
        h = scan_tree(paths, vs)
        print(f"{label}: {'clean' if not h else 'FOUND ' + str({k: v[:5] for k, v in h.items()})}"); ok &= not h
    files = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    h = scan_tree([ROOT / f for f in files], vals)
    print(f"repo working tree ({len(files)} files): {'clean' if not h else 'FOUND ' + str(h)}"); ok &= not h
    hist = subprocess.run(["git", "log", "-p", "--all", "--no-color"], cwd=ROOT, capture_output=True).stdout
    hh = [k for k, xs in vals.items() if any(x.encode() in hist for x in xs)]
    print(f"git history ({len(hist)} bytes of patches): {'clean' if not hh else 'FOUND ' + str(hh)}"); ok &= not hh
    dp = secret("DATA_PATH")
    codes = {"/d/": probe(SITE + "/d/"), "/d/<random 43>/state.json": probe(SITE + "/d/" + "x" * 43 + "/state.json"),
             "/state.json": probe(SITE + "/state.json"), "/d/state.json": probe(SITE + "/d/state.json")}
    real = probe(f"{SITE}/d/{dp}/state.json") if dp else "no DATA_PATH"
    print("site:", ", ".join(f"{k} {v}" for k, v in codes.items()), f"| /d/<DATA_PATH>/state.json {real}")
    ok &= all(v == 404 for v in codes.values()) and real == 200
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
