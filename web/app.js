/* Grok Bot desk, iPhone app (plan B). Polls /d/{DATA_PATH}/*.json, never SSE.
   The data path comes once from #d=..., lives in localStorage, and is never sent anywhere else.
   No FOMO URL is built here: every link is the exact coin_url / profile_url / signup_url the desk wrote. */
(function () {
  "use strict";
  const POLL_S = 15, STALE_S = 600, AGENTS = ["scanner", "auditor", "analyst", "executor", "risk"];
  const RM = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------- data path (fragment only) ----------
  let KEY = null;
  (function () {
    const m = location.hash.match(/(?:^#|&)d=([A-Za-z0-9_-]{32,})/);
    if (m) { try { localStorage.setItem("desk_d", m[1]); } catch (e) {} KEY = m[1]; history.replaceState(null, "", location.pathname + location.search + "#/home"); }
    else { try { KEY = localStorage.getItem("desk_d"); } catch (e) {} }
  })();
  const BASE = () => "d/" + KEY + "/";

  // ---------- fetch with last-good cache ----------
  const mem = {};
  function cached(p) { if (mem[p]) return mem[p]; try { const v = localStorage.getItem("desk_c_" + p); if (v) return (mem[p] = JSON.parse(v)); } catch (e) {} return null; }
  function keep(p, v) { mem[p] = v; if (/^(state|chart|activity|account|settings|coins|agents\/|coin\/|audit\/)/.test(p)) { try { localStorage.setItem("desk_c_" + p, JSON.stringify(v)); } catch (e) {} } }
  // undefined = not loaded yet (skeleton), null = the desk has no such file (empty state)
  function doc(p) { if (mem[p] !== undefined) return mem[p]; const c = cached(p); return c === null ? (mem.__miss && mem.__miss[p] ? null : undefined) : c; }
  const net = { ok: null, failAt: 0, nextTry: 0, lastTs: null };
  async function get(p, opt) {
    if (!KEY) throw new Error("nokey");
    const r = await fetch(BASE() + p + "?t=" + Date.now(), { cache: "no-store" });
    if (r.status === 404 && opt && opt.missing) { mem[p] = null; try { localStorage.removeItem("desk_c_" + p); } catch (e) {} return null; }
    if (!r.ok) throw new Error("http " + r.status);
    const v = await r.json(); keep(p, v); return v;
  }

  // ---------- formatting ----------
  const money = v => v == null ? "–" : (v < 0 ? "-$" : "$") + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const big = v => v == null ? "n/a" : "$" + (v >= 1e9 ? (v / 1e9).toFixed(2) + "B" : v >= 1e6 ? (v / 1e6).toFixed(2) + "M" : v >= 1e3 ? (v / 1e3).toFixed(1) + "K" : v.toFixed(0));
  const pct = v => v == null ? "–" : (v >= 0 ? "+" : "") + v.toFixed(2) + "%";
  function price(v) { if (v == null) return "–"; if (v >= 1) return "$" + v.toFixed(4); const d = Math.max(2, -Math.floor(Math.log10(v)) + 3); return "$" + v.toFixed(Math.min(d, 12)); }
  const hhmm = iso => iso ? new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : "–";
  const age = m => m == null ? "age n/a" : m < 60 ? m + "m old" : m < 1440 ? Math.floor(m / 60) + "h old" : Math.floor(m / 1440) + "d old";
  const short = s => s && s.length > 12 ? s.slice(0, 4) + "..." + s.slice(-4) : s;
  const ext = (url, inner, cls) => url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer"${cls ? ` class="${cls}"` : ""}>${inner}</a>` : inner;

  // ---------- odometer ----------
  function odometer(el, text) {
    const prev = el.dataset.v || "";
    if (prev === text) return;
    el.dataset.v = text;
    const strip = d => `<span class="d"><span style="transform:translateY(-${d}em)">${[0,1,2,3,4,5,6,7,8,9].map(n => `<b>${n}</b>`).join("")}</span></span>`;
    const roll = !RM && prev && prev.length === text.length;
    el.innerHTML = [...text].map((ch, i) => /\d/.test(ch) ? strip(roll && /\d/.test(prev[i]) ? +prev[i] : +ch) : `<span class="s">${esc(ch)}</span>`).join("");
    if (!roll) return;
    const ds = [...el.querySelectorAll(".d > span")];
    requestAnimationFrame(() => requestAnimationFrame(() => {
      let k = 0;
      for (let i = text.length - 1, j = ds.length - 1; i >= 0; i--) {
        if (!/\d/.test(text[i])) continue;
        const s = ds[j--];
        if (text[i] !== prev[i]) { s.style.transitionDelay = (k++ * 30) + "ms"; s.style.transform = `translateY(-${+text[i]}em)`; }
      }
    }));
  }

  // ---------- offline row ----------
  let offTimer = null;
  function setOffline(on) {
    const el = $("#offline");
    if (!on) { el.hidden = true; clearInterval(offTimer); offTimer = null; return; }
    const tick = () => {
      const n = Math.max(0, Math.ceil((net.nextTry - Date.now()) / 1000));
      el.textContent = `Desk offline · retrying in ${n}s · last update ${net.lastTs ? hhmm(net.lastTs) : "never"}`;
    };
    el.hidden = false; tick(); if (!offTimer) offTimer = setInterval(tick, 1000);
  }

  // ---------- skeleton ----------
  const sk = (h, w, mt) => `<div class="sk" style="height:${h}px;${w ? "width:" + w + ";" : ""}margin-top:${mt || 12}px"></div>`;
  const skel = {
    home: () => nav() + sk(24, "50%", 8) + sk(66, "70%") + sk(56) + sk(110) + sk(160) + sk(44) + sk(28, "50%", 28) + `<div class="carousel" style="margin-top:12px"><div class="acard sk"></div></div>` + sk(60) + sk(60, null, 1),
    list: t => `<div class="nav"><div class="navtitle" style="margin:0">${t}</div></div>` + sk(40, "50%") + [1,2,3,4,5,6].map(() => sk(64)).join(""),
    detail: () => `<div class="nav">${backBtn()}</div>` + sk(34, "60%") + sk(44, "50%") + sk(220) + sk(120) + sk(56),
  };

  // ---------- shared bits ----------
  function nav() {
    const s = cached("state.json"), h = (s && s.fomo_handle) || "?";
    let seen = 0; try { seen = +localStorage.getItem("desk_seen") || 0; } catch (e) {}
    const unread = s && s.unread > 0 && Date.parse(s.ts) > seen;
    return `<div class="nav">
      <a class="avatar" href="#/account" aria-label="Account"><i>${esc(h[0].toUpperCase())}</i></a>
      <a class="brand" href="#/agents">Grok Bot<span class="chev">›</span></a>
      <button class="iconbtn" id="searchbtn" aria-label="Search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg></button>
      <a class="iconbtn" href="#/activity" id="bell" aria-label="Notifications"><svg viewBox="0 0 24 24"><path d="M6 16V11a6 6 0 1 1 12 0v5l2 2H4z"/><path d="M10 21h4"/></svg>${unread ? '<i class="dot"></i>' : ""}</a>
    </div><div id="searchbox"></div>`;
  }
  const backBtn = () => `<a class="back" href="javascript:history.length>1?history.back():location.hash='#/home'">‹ Back</a>`;
  const chainTag = ch => ch === "bnb" ? '<span class="ctag bnb">BNB</span>' : "";
  // cute blob avatars, inline SVG only (no images)
  const BLOB = {
    grokbot: `<circle cx="20" cy="20" r="17" fill="#fff"/><path d="M12 27L28 12" stroke="#000" stroke-width="3.2" stroke-linecap="round"/>`,
    risk: `<path d="M20 3c7 0 15 5 15 14 0 10-7 20-15 20S5 28 5 18C5 9 13 3 20 3z" fill="#ff9f0a"/>`,
    executor: `<path d="M8 8c6-6 18-6 24 2 6 8 2 22-8 24S4 30 5 20c0-5 1-9 3-12z" fill="#bf5af2"/>`,
    analyst: `<path d="M20 4c2 0 3 1 4 3l12 22c1.5 3 0 6-4 6H8c-4 0-5.5-3-4-6L16 7c1-2 2-3 4-3z" fill="#ff453a"/>`,
    auditor: `<path d="M20 3l14 8v18l-14 8-14-8V11z" fill="#8e8e93" stroke-linejoin="round"/>`,
    scanner: `<path d="M20 3l4.6 10 10.9 1.2-8.1 7.4 2.3 10.8L20 26.9l-9.7 5.5 2.3-10.8-8.1-7.4L15.4 13z" fill="#40c8e0" stroke="#40c8e0" stroke-width="2" stroke-linejoin="round"/>`,
  };
  const EYES = `<circle cx="15.5" cy="19" r="2.1" fill="#000"/><circle cx="24.5" cy="19" r="2.1" fill="#000"/>`;
  const blob = (n, sz) => `<svg class="av" viewBox="0 0 40 40" width="${sz || 36}" height="${sz || 36}" aria-hidden="true">${BLOB[n] || BLOB.grokbot}${n === "grokbot" ? "" : EYES}</svg>`;
  const ICON = { bought: ["#0a84ff", "↗"], sold: ["#00d35a", "$"], stopped: ["#ff453a", "↓"], rejected: ["#8e8e93", "✕"] };
  function actRow(r) {
    const [c, g] = ICON[r.kind] || ["#8e8e93", "•"];
    const href = r.mint ? (r.target === "coin" ? "#/coin/" : "#/audit/") + r.mint : "#/activity";
    const vcls = r.kind === "sold" ? (String(r.value).startsWith("-") ? "red" : "green") : r.kind === "stopped" ? "red" : r.kind === "rejected" ? "sec" : "";
    return `<a class="row" href="${href}"><div class="ic" style="color:${c}">${g}</div><div class="mid"><div class="t">${esc(r.title)}${chainTag(r.chain)}</div>
      <div class="s">${esc(r.subtitle || "")}${r.kind === "bought" || r.kind === "rejected" ? "" : " · " + hhmm(r.ts)}</div></div><div class="v ${vcls}">${esc(r.value || "")}</div></a>`;
  }

  // ---------- agent cards ----------
  function card(name, a, s) {
    const head = (t, col) => `<h3>${blob(name, 26)}<span class="sd" style="background:${a.paused ? "#ff9f0a" : col}"></span><span style="color:${col}">${t}</span>${a.paused ? '<span class="sec" style="font-weight:400">· paused</span>' : ""}</h3>`;
    if (name === "scanner") {
      const feed = a.feed && a.feed.length ? `<ul class="feed">${a.feed.slice(0, 5).map(f => `<li class="${f.verdict}">${ext(f.coin_url, `<span class="n">${esc(f.name)}</span>`)}${chainTag(f.chain)}<span class="r">${f.verdict === "kill" ? esc(f.reason) : f.verdict === "pass" ? "→ auditor" : "waiting"}</span></li>`).join("")}</ul>`
        : `<div class="lbl" style="margin-top:14px">Listening for graduations</div>`;
      return `<div class="acard">${head("Scanner", "#64d2ff")}<div class="big">${a.pools} pools</div><div class="lbl">${a.waiting} in the waiting room · ${a.killed} killed</div>${feed}</div>`;
    }
    if (name === "auditor") {
      const L = a.last;
      const strips = `<div class="strips">${[0,1,2,3,4,5,6,7].map(i => { const l = L && L.layers[i]; return `<i class="${l ? (l.status === "pass" ? "pass" : l.status === "red" ? "red" : (l.status === "pending" ? "pending" : "")) : ""}"></i>`; }).join("")}</div>`;
      const st = L ? `<div class="lbl" style="margin-top:14px">${ext("#/audit/" + L.mint, esc(L.name))}</div><div style="margin-top:4px;color:${L.complete ? (L.passed ? "#00d35a" : "#ff453a") : "#64d2ff"}">${esc(a.status)}</div>` : `<div class="lbl" style="margin-top:14px">No coin taken apart yet</div>`;
      return `<a class="acard" href="${L ? "#/audit/" + L.mint : "#/agents"}">${head("Auditor", "#fff")}<div class="big">${a.taken_apart} taken apart</div>${strips}${st}<div class="foot">${a.rejected} rejected</div></a>`;
    }
    if (name === "analyst") {
      const L = a.last;
      return `<div class="acard">${head("Analyst", "#ffd60a")}<div class="lbl">Scoring${a.mode === "rules" ? " · rules" : ""}</div><canvas class="ring" id="ring"></canvas>
        <div style="text-align:center;margin-top:8px">${L ? ext(L.coin_url, esc(L.name)) + ` <span class="${L.pass ? "green" : "red"}">${L.pass ? "pass" : esc(L.reason || "reject")}</span>` : '<span class="sec">Waiting for a coin to score</span>'}</div>
        <div class="foot">${a.scored} scored · green from ${a.green_from}</div></div>`;
    }
    if (name === "executor") {
      const L = a.last; let bar = "";
      if (L) {
        const t = (Date.now() - Date.parse(L.ts)) / 1000, p = L.filled ? 100 : Math.min(99, Math.round(100 * t / (a.bar_s || 3)));
        bar = `<div style="margin-top:16px">${ext(L.coin_url, esc(L.name))} · ${money(L.size_usd)} · ${L.stack_pct}% of stack</div><div class="bar"><i style="width:${p}%"></i></div>
          <div class="lbl" style="margin-top:6px">${L.filled ? '<span class="green">✓ Filled</span>' : L.status === "sent" ? "Waiting for Bought" : "Executing... " + p + "%"}</div>`;
      } else bar = `<div class="lbl" style="margin-top:16px">$– · ${s.stack_pct}% of stack</div><div class="bar"><i style="width:0"></i></div><div class="lbl" style="margin-top:6px">No trade yet tonight</div>`;
      return `<div class="acard">${head("Executor", "#00d35a")}<div class="big">${a.trades} trades</div>${bar}<div class="foot">${a.skipped} skipped</div></div>`;
    }
    const r = a;
    return `<div class="acard">${head("Risk", "#ff6b9a")}<div class="big">Guarding</div><div class="lbl">${r.guarding ? r.guarding + " open position" + (r.guarding > 1 ? "s" : "") : "No open position"}</div>
      <div style="margin-top:14px">${r.last_exit ? `<span class="${r.last_exit === "Stopped" ? "red" : "green"}">${esc(r.last_exit)}</span>` : '<span class="sec">No exit yet tonight</span>'}</div>
      <div class="pills"><span class="tpill red">${esc(r.pill_stop)}</span><span class="tpill green">${esc(r.pill_trail)}</span></div></div>`;
  }

  // ---------- views ----------
  const V = {};
  let hrange = 12;
  const NAMES = { grokbot: "Grok Bot", scanner: "Scanner", auditor: "Auditor", analyst: "Analyst", executor: "Executor", risk: "Risk" };
  V.home = async function () {
    const S = cached("state.json"), C = cached("chart.json"), A = cached("activity.json");
    if (!S) return skel.home();
    const T = S.today || {}, B = S.best, G = S.signal, H = S.hourly || [], marks = (C && C.marks) || [];
    const hbFresh = n => S.heartbeats && S.heartbeats[n] && (Date.now() - Date.parse(S.heartbeats[n])) / 1000 < 120;
    const live = AGENTS.filter(n => !S.agents[n].paused && hbFresh(n)).length;
    const pnl = S.balance - S.night_start_balance, neg = pnl < 0;
    const stat = (k, v, cls) => `<div class="st"><div class="sv ${cls || ""}">${v == null ? "–" : v}</div><div class="sk2">${k}</div></div>`;
    const bestCard = B ? `<a class="hcard" href="${B.stage.startsWith("Audit") ? "#/audit/" + B.mint : "#/coin/" + B.mint}">
        <div class="hk">${B.stage === "Closest call" ? "Closest call tonight" : "Best candidate tonight"}</div>
        <div class="hn">${esc(B.name)}${chainTag(B.chain)}</div><div class="hs"><span class="stage">${esc(B.stage)}</span> ${esc(B.detail || "")}</div>
        <div class="hs ter">${B.liquidity_usd != null ? "liq " + big(B.liquidity_usd) + " · " : ""}${hhmm(B.ts)}</div></a>`
      : `<div class="hcard"><div class="hk">Best candidate tonight</div><div class="hs sec" style="margin-top:8px">No coin judged yet tonight</div></div>`;
    const sigCard = G ? `<div class="hcard"><div class="hk">Latest signal</div><div class="hn">${ext(G.coin_url, esc(G.name))}${chainTag(G.chain)}</div>
        <div class="hs">${esc(G.status)} · ${money(G.size_usd)} · <span class="orange">${esc(G.mode || S.mode)}</span></div><div class="hs ter">${hhmm(G.ts)}</div></div>`
      : `<div class="hcard"><div class="hk">Latest signal</div><div class="hs sec" style="margin-top:8px">No signal tonight. Every agent has to pass first.</div></div>`;
    const html = nav() + `
      <div class="hero">
        <div class="hrow"><span class="pill ${S.mode === "PAPER" ? "paper" : "livep"}">${esc(S.mode)}</span>
          <span class="run"><i class="${live === AGENTS.length ? "" : "warn"}"></i>${live}/${AGENTS.length} agents running</span>
          <span class="chains">${(S.chains || ["solana"]).map(c => `<span class="ctag ${c}">${c === "bnb" ? "BNB" : "SOL"}</span>`).join("")}</span></div>
        <div class="hbig"><span class="odo" id="seen"></span><span class="hu">coins seen tonight</span></div>
        <div class="hsince">since ${hhmm(S.night_start)} · updated ${hhmm(S.ts)}</div>
        <div class="stats4">${stat("killed", T.killed, "red")}${stat("passed scan", T.passed, "cyan")}${stat("audited", T.audited)}${stat("signals", T.signals, T.signals ? "green" : "")}</div>
      </div>
      <div class="hgrid">${bestCard}${sigCard}</div>
      <div class="sechead"><h2>Coins per hour</h2><span class="legend"><i style="background:var(--red)"></i>killed <i style="background:var(--cyan)"></i>passed <i style="background:#48484a"></i>other</span></div>
      <div class="chartbox small"><canvas id="bars"></canvas>${H.some(h => h[1]) ? "" : '<div class="chartempty">No coins judged in this window</div>'}</div>
      <div class="ranges">${[6, 12, 24].map(r => `<button data-h="${r}" class="${r === hrange ? "on" : ""}">${r}H</button>`).join("")}</div>
      <div class="paperline"><span class="pill paper sm">PAPER</span> balance <b>${money(S.balance)}</b> <span class="${neg ? "red" : pnl > 0 ? "green" : "sec"}">${pnl >= 0 ? "+" : "-"}${money(Math.abs(pnl))} (${pct(S.today_pct)})</span> tonight · pretend money</div>
      ${marks.length ? `<div class="sechead"><h2>Paper P&amp;L</h2><span class="sec" style="font-size:13px">tonight · ${marks.filter(m => m.kind !== "bought").length} exits</span></div><div class="chartbox"><canvas id="line"></canvas></div>` : ""}
      <div class="sechead"><h2>Agents · ${S.running} running</h2><a href="#/agents">All</a></div>
      <div class="carousel" id="car">${AGENTS.map(n => card(n, S.agents[n], { stack_pct: S.stack_pct || 8 })).join("")}</div>
      <div class="sechead"><h2>Latest from the desk</h2></div>
      ${msgList(S)}
      <div class="sechead"><h2>Activity</h2><a href="#/activity">See all</a></div>
      ${A && A.rows.length ? `<div class="list">${A.rows.slice(0, 5).map(actRow).join("")}</div>` : '<div class="list"><div class="empty">No buys, sells or rejections yet tonight</div></div>'}`;
    return { html, after: () => {
      odometer($("#seen"), String(T.seen == null ? 0 : T.seen));
      Charts.bars($("#bars"), H.slice(-hrange));
      if (marks.length && C) Charts.line($("#line"), (C.ranges && C.ranges.LIVE) || [], C.start_value, marks, { color: neg ? "#ff453a" : "#00d35a" });
      const L = S.agents.analyst.last; const rg = $("#ring"); if (rg) Charts.ring(rg, L ? L.score : null, S.agents.analyst.green_from);
      document.querySelectorAll(".ranges button").forEach(b => b.onclick = () => { hrange = +b.dataset.h; render(); });
      hookSearch();
    }, keepScroll: ["#car"] };
  };
  function msgList(S) {
    const M = S.messages || [];
    if (!M.length) return '<div class="list"><div class="empty">No messages yet</div></div>';
    let seen = 0; try { seen = +localStorage.getItem("desk_seen") || 0; } catch (e) {}
    return `<div class="list">${M.map(m => {
      const fresh = m.ts && Date.parse(m.ts) > seen && m.agent !== "grokbot";
      const href = m.agent === "grokbot" ? "#/activity" : "#/agents";
      return `<a class="row msg" href="${href}"><div class="blob">${blob(m.agent)}</div><div class="mid"><div class="t"><b>${NAMES[m.agent] || esc(m.agent)}</b>${m.ts && m.agent !== "grokbot" ? `<span class="tm">${hhmm(m.ts)}</span>` : ""}</div>
        <div class="s">${esc(m.text)}${chainTag(m.chain)}</div></div>${fresh ? '<i class="udot"></i>' : ""}</a>`;
    }).join("")}</div>`;
  }
  V.agents = async function () {
    const S = cached("state.json");
    if (!S) return skel.list("Agents");
    const docs = {};
    for (const n of AGENTS) docs[n] = cached("agents/" + n + ".json");
    const count = { scanner: a => `${a.pools} pools · ${a.killed} killed · ${a.passed} passed`, auditor: a => `${a.taken_apart} taken apart · ${a.rejected} rejected`,
      analyst: a => `${a.scored} scored`, executor: a => `${a.trades} trades · ${a.skipped} skipped`, risk: a => `${a.guarding} guarding` };
    const html = `<div class="nav"><div class="navtitle" style="margin:0">Agents</div></div><div class="ptitle">Agents · ${S.running} running</div><div class="hrow" style="margin:-8px 0 14px"><span class="pill ${S.mode === "PAPER" ? "paper" : "livep"}">${esc(S.mode)}</span><span class="sec" style="font-size:13px">notify only · every trade needs your OK</span></div>${msgList(S)}<div style="height:16px"></div>` + AGENTS.map(n => {
      const a = S.agents[n], d = docs[n], lock = n === "risk" && (S.open_positions || []).length > 0;
      const hb = a.heartbeat ? (Date.now() - Date.parse(a.heartbeat)) / 1000 : 1e9;
      const st = a.paused ? "paused" : hb > 120 ? "down" : "running";
      const dec = d && d.decisions.length ? `<ul class="dec">${d.decisions.map(x => `<li><span class="tm">${hhmm(x.ts)}</span><span class="dn">${ext(x.coin_url, esc(x.name))}${chainTag(x.chain)}</span><span class="rs ${/kill|red|reject|stop|skipped/.test(x.verdict) ? "red" : /pass|bought|trail/.test(x.verdict) ? "green" : ""}">${esc(x.verdict)}${x.reason ? " · " + esc(x.reason) : ""}</span></li>`).join("")}</ul>` : `<div class="empty">${n === "scanner" ? "Listening for graduations" : n === "auditor" ? "No coin taken apart yet" : "No decisions yet"}</div>`;
      return `<div class="fcard" id="ag-${n}"><div class="fhead">${blob(n, 30)}<span class="nm" style="color:${a.color}">${a.name}</span>
        <span class="status ${st === "running" ? "" : "paused"}"><i style="${st === "down" ? "background:#ff453a" : ""}"></i>${st}</span>
        <span class="swwrap"><span class="switch ${a.paused ? "" : "on"}" role="switch" aria-checked="${!a.paused}" aria-disabled="true"></span></span></div>
        <div class="count">${count[n](a)} today</div>${dec}
        <div class="hint">${lock ? "Locked while a position is open." : `Pause in Telegram: ${a.paused ? "resume" : "pause"} ${a.name}`}</div>
        <a class="link" href="#/account/${n}">Settings</a></div>`;
    }).join("");
    return { html };
  };
  V.activity = async function () {
    const A = cached("activity.json");
    if (!A) return skel.list("Activity");
    const rows = (A.rows || []).concat(...(extra.pages || []).map(p => p.rows));
    const nextP = extra.pages && extra.pages.length ? extra.pages[extra.pages.length - 1].next : A.next;
    const html = `<div class="nav"><div class="navtitle" style="margin:0">Activity</div></div><div class="ptitle">Activity</div>` +
      (rows.length ? `<div class="list">${rows.map(actRow).join("")}</div>${nextP ? `<button class="more" id="more">Load 30 more</button>` : ""}` : '<div class="list"><div class="empty">Nothing yet tonight</div></div>');
    return { html, after: () => { const b = $("#more"); if (b) b.onclick = async () => { try { (extra.pages = extra.pages || []).push(await get(nextP)); render(); } catch (e) { toast("Couldn't load more"); } }; } };
  };
  const extra = {};
  const shownLayers = {};
  V.audit = async function (mint) {
    const S = cached("state.json");
    if (!mint && S && S.agents.auditor.last) mint = S.agents.auditor.last.mint;
    const D = mint ? doc("audit/" + mint + ".json") : null;
    if (D === undefined) return skel.detail();
    if (!D) return { html: `<div class="nav">${backBtn()}<div class="navtitle">Auditor</div></div><div class="empty" style="margin-top:80px">No coin taken apart yet</div>` };
    const done = D.layers.filter(l => l.status === "pass" || l.status === "red").length;
    const html = `<div class="nav">${backBtn()}<div class="navtitle">${esc(D.name)}</div></div>
      <div class="discs" id="discs">${D.layers.map((l, i) => `<div class="disc" data-i="${i}" style="top:${i * 24}px;z-index:${10 - i}"></div>`).join("")}<div class="beam" id="beam" style="top:-6px" hidden></div></div>
      <div class="verdict ${D.verdict.state}" id="verdict" ${D.complete ? "hidden" : ""}><div class="vt">${esc(D.verdict.title)}</div><div class="vs">${esc(D.verdict.subtitle)}</div></div>
      <div class="list">${D.layers.map((l, i) => `<div class="row lrow" data-i="${i}"><div class="ic">${l.n}</div><div class="mid"><div class="t">${esc(l.name)}</div><div class="s">${l.source ? esc(l.source) + " · " + hhmm(l.ts) : esc(l.status)}</div></div><div class="v ${l.status === "red" ? "red" : l.status === "pass" ? "" : "ter"}">${esc(l.value)}</div></div>`).join("")}</div>
      <div class="toggle"><div class="mid"><div>Take apart before buying</div><div class="s sec" style="font-size:13px">Plan B: change in Telegram, "set take apart to off" (asks twice)</div></div><span class="switch ${D.auditor_enabled ? "on" : ""}" role="switch" aria-checked="${D.auditor_enabled}" aria-disabled="true"></span></div>
      ${ext(D.coin_url, "Open on FOMO", "cta")}`;
    return { html, after: () => beam(D) };
  };
  function beam(D) {
    const discs = [...document.querySelectorAll(".disc")], rows = [...document.querySelectorAll(".lrow")], bm = $("#beam"), vd = $("#verdict");
    const paint = (i) => { const l = D.layers[i], c = l.status === "pass" ? "pass" : l.status === "red" ? "red" : l.status === "not checked" ? "skip" : "";
      discs[i].className = "disc " + c; discs[i].textContent = c === "pass" ? "✓" : c === "red" ? "✕" : "";
      const ic = rows[i].querySelector(".ic"); ic.className = "ic " + (c === "pass" || c === "red" ? c : ""); ic.textContent = c === "pass" ? "✓" : c === "red" ? "✕" : l.n; };
    const from = shownLayers[D.mint] || 0;
    const last = D.layers.findIndex(l => l.status !== "pass" && l.status !== "red");   // first not-yet-sent layer
    const upto = last === -1 ? 8 : (D.layers[last].status === "not checked" && D.complete ? 8 : last);
    for (let i = 0; i < from; i++) paint(i);
    for (let i = from; i < 8; i++) if (D.layers[i].status === "not checked" && D.complete && i >= upto) paint(i);
    if (RM || from >= upto) { for (let i = 0; i < upto; i++) paint(i); for (let i = 0; i < 8; i++) if (D.complete) paint(i); if (D.complete) vd.hidden = false; else waitBeam(last); shownLayers[D.mint] = upto; return; }
    bm.hidden = false; let i = from;
    const step = () => {
      if (i >= upto) {
        if (D.complete) { bm.hidden = true; for (let k = 0; k < 8; k++) paint(k); vd.hidden = false; } else waitBeam(last);
        shownLayers[D.mint] = upto; return;
      }
      bm.style.top = (i * 24 + 20) + "px";
      setTimeout(() => setTimeout(() => { paint(i); i++; step(); }, 150), 350);
    };
    step();
    function waitBeam(n) { if (n >= 0) { bm.hidden = false; bm.style.top = (n * 24 + 20) + "px"; } }   // server slower: the beam waits on that layer
  }
  V.coin = async function (mint) {
    const D = doc("coin/" + mint + ".json");
    if (D === undefined) return skel.detail();
    if (!D) return { html: `<div class="nav">${backBtn()}<div class="navtitle">Coin</div></div><div class="empty" style="margin-top:80px">This coin is not on the desk tonight</div>` };
    const P = D.position, E = D.exit_lines, up = P && P.since_entry_pct != null ? P.since_entry_pct >= 0 : true;
    const html = `<div class="nav">${backBtn()}<div class="navtitle"></div>${D.mode === "PAPER" ? '<span class="pill paper">PAPER</span>' : ""}</div>
      <div class="chead"><h1>${esc(D.name)}</h1>${D.shield ? `<span class="shield ${D.shield === "8/8" ? "" : "red"}">🛡 ${esc(D.shield)}</span>` : ""}</div>
      <div class="sec" style="margin-top:4px">${esc(D.symbol || "")} · ${esc(D.chain)} · ${age(D.age_min)}</div>
      <div class="price">${price(D.price)}</div>
      ${P && P.since_entry_pct != null ? `<div class="${up ? "green" : "red"}">${up ? "▲" : "▼"} ${Math.abs(P.since_entry_pct).toFixed(2)}% since entry</div>` : `<div class="sec">${D.price_ts ? "price at " + hhmm(D.price_ts) : "no price yet"}</div>`}
      <div class="chartbox"><canvas id="candles"></canvas></div>
      <div class="stats"><div><div class="k">Market cap</div><div class="v">${big(D.market_cap)}</div></div><div><div class="k">Liquidity</div><div class="v">${big(D.liquidity_usd)}</div></div>
        <div><div class="k">24h volume</div><div class="v">${big(D.volume_h24)}</div></div><div><div class="k">Holders</div><div class="v">${D.holders == null ? "n/a" : D.holders.toLocaleString("en-US")}</div></div></div>
      ${P ? `<div class="exitcard"><div class="h">${esc(P.label)}</div><div class="sec" style="font-size:13px;margin-top:2px">entry ${price(P.entry_price)} · ${hhmm(P.opened_at)} · ${esc(P.mode)}</div>
        <div class="l"><span>Stop line · -${P.stop_pct}%</span><span class="red">${price(E.stop_line)}</span></div>
        <div class="l"><span>Trail line</span><span class="${E.trail_active ? "green" : "ter"}">${E.trail_active ? price(E.trail_line) : "starts at +" + (D.trail_start_pct || 20) + "%"}</span></div>
        <div class="l"><span>Time left to timeout</span><span>${Math.floor(E.timeout_left_min / 60)}h ${E.timeout_left_min % 60}m</span></div></div>` : ""}
      ${ext(D.coin_url, esc(D.button), "cta" + (P ? " sell" : ""))}`;
    return { html, after: () => Charts.candles($("#candles"), D.candles || [], E ? E.current_exit_line : null) };
  };
  V.account = async function (group) {
    const A = cached("account.json"), ST = cached("settings.json");
    if (!A || !ST) return skel.list("Account");
    const tc = A.ticket_check || {};
    const standalone = navigator.standalone || (window.matchMedia && matchMedia("(display-mode: standalone)").matches);
    const setRow = it => {
      const label = it.aliases && it.aliases.length ? it.aliases[0] : it.key.split(".").pop().replace(/_/g, " ");
      let ctl;
      if (it.type === "number") ctl = `<div class="field"><input type="number" value="${it.value == null ? "" : it.value}" min="${it.min}" max="${it.max}" disabled inputmode="decimal"></div>`;
      else if (it.type === "bool") ctl = `<span class="switch ${it.value ? "on" : ""}" aria-disabled="true"></span>`;
      else if (it.type === "choice") ctl = `<select disabled>${it.choices.map(c => `<option ${c === it.value ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>`;
      else ctl = `<div class="field"><input type="text" value="${esc(it.value == null ? "" : it.value)}" disabled></div>`;
      const slider = it.type === "number" && it.min != null ? `<input type="range" min="${it.min}" max="${it.max}" step="any" value="${it.value == null ? it.min : it.value}" disabled>` : "";
      return `<div class="set"><div class="top"><div><div class="kn">${esc(label)}</div><div class="ks">${esc(it.key)}${it.type === "number" ? ` · ${it.min}–${it.max}` : ""}</div></div>${ctl}</div>${slider}</div>`;
    };
    const order = ["scanner", "auditor", "analyst", "executor", "risk", "desk"];
    const html = `<div class="nav"><div class="navtitle" style="margin:0">Account</div></div><div class="ptitle">@${esc(A.fomo_handle)}</div>
      <div class="list" style="padding:12px 16px"><div style="font-weight:600">Mode</div>
        <div class="seg"><button class="${A.mode === "PAPER" ? "on paperon" : ""}" disabled>PAPER</button><button class="${A.mode === "LIVE" ? "on" : ""}" disabled>LIVE</button></div>
        <div class="hint">Switch in Telegram: send "live", then type LIVE. ${A.live_allowed ? "LIVE allowed." : esc(A.live_rule)}</div>
        <div class="top" style="display:flex;justify-content:space-between;align-items:center;margin-top:12px;min-height:44px"><span>${A.mode === "PAPER" ? "PAPER balance" : "LIVE stack"}</span>
          <div class="field"><input type="number" value="${A.mode === "PAPER" ? A.paper_balance : (A.live_stack == null ? "" : A.live_stack)}" disabled></div></div>
        <div class="hint">LIVE stack: ${A.live_stack == null ? "not set" : money(A.live_stack)} · set in Telegram: stack 1240</div>
        <div class="note ${tc.level === "warn" ? "warn" : ""}" style="padding:8px 0 0">${esc(tc.message || "")}</div></div>
      <div class="list" style="margin-top:16px">
        ${ext(A.profile_url, `<div class="row"><div class="mid"><div class="t">Your FOMO profile</div><div class="s">@${esc(A.fomo_handle)}</div></div><div class="v ter">›</div></div>`)}
        <button class="row" id="invite" data-url="${esc(A.signup_url)}" style="width:100%;text-align:left"><div class="mid"><div class="t">Invite a friend</div><div class="s">Share your FOMO invite</div></div><div class="v ter">›</div></button>
      </div>
      <div class="group"><h3>Notifications</h3><div class="list">
        <div class="row"><div class="mid"><div class="t">Telegram</div><div class="s">${A.telegram.connected ? "connected · " + esc(A.telegram.bot) : "not connected"}</div></div><div class="v ${A.telegram.connected ? "green" : "red"}">${A.telegram.connected ? "●" : "○"}</div></div>
        <a class="row" href="https://t.me/${esc((A.telegram.bot || "").replace("@", ""))}" target="_blank" rel="noopener"><div class="mid"><div class="t">Send test push</div><div class="s">Plan B: send "test" to the bot in Telegram</div></div><div class="v ter">›</div></a>
        ${standalone ? `<div class="row"><div class="mid"><div class="t">Enable notifications</div><div class="s">${esc(A.web_push_note)}</div></div><div class="v ter">off</div></div>` : `<div class="row"><div class="mid"><div class="t">Web push</div><div class="s">${esc(A.web_push_note)}</div></div></div>`}
      </div></div>
      <div class="note">${esc(A.edit_hint)}. Settings are read-only here (plan B).</div>
      ${order.filter(g => ST.groups[g]).map(g => `<div class="group" id="grp-${g}"><h3>${g[0].toUpperCase() + g.slice(1)}</h3><div class="list">${ST.groups[g].map(setRow).join("")}</div></div>`).join("")}`;
    return { html, after: () => {
      $("#invite").onclick = async () => {
        const url = $("#invite").dataset.url;                 // the exact SIGNUP_URL the desk wrote
        try { if (navigator.share) { await navigator.share({ title: "FOMO", url }); return; } } catch (e) { if (e && e.name === "AbortError") return; }
        try { await navigator.clipboard.writeText(url); toast("Link copied"); } catch (e) { toast(url); }
      };
      if (group) { const g = $("#grp-" + group); if (g) g.scrollIntoView(); }
    } };
  };

  // ---------- search ----------
  function hookSearch() {
    const b = $("#searchbtn"); if (!b) return;
    b.onclick = () => {
      const box = $("#searchbox");
      if (box.innerHTML) { box.innerHTML = ""; return; }
      box.innerHTML = `<div class="search"><input id="q" placeholder="Name, symbol or mint" autocomplete="off" autocapitalize="off"></div><div id="qr"></div>`;
      const q = $("#q"); q.focus();
      q.oninput = () => {
        const v = q.value.trim().toLowerCase(), list = ((cached("coins.json") || {}).coins) || [];
        if (!v) { $("#qr").innerHTML = ""; return; }
        const hits = list.filter(c => (c.name || "").toLowerCase().includes(v) || (c.symbol || "").toLowerCase().includes(v) || c.mint.toLowerCase() === v || c.mint.toLowerCase().startsWith(v)).slice(0, 8);
        $("#qr").innerHTML = hits.length ? `<div class="list">${hits.map(c => `<a class="row" href="#/coin/${c.mint}"><div class="mid"><div class="t">${esc(c.name)}</div><div class="s">${esc(c.symbol || "")} · ${short(c.mint)}</div></div></a>`).join("")}</div>` : '<div class="empty">Not on the desk tonight</div>';
      };
    };
  }
  function toast(t) { const el = $("#toast"); el.textContent = t; el.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => el.hidden = true, 1800); }

  // ---------- router + polling ----------
  function route() { const p = (location.hash.replace(/^#\/?/, "") || "home").split("/"); return { v: p[0] || "home", a: p[1] }; }
  let rendering = false;
  async function render() {
    if (rendering) return; rendering = true;
    try {
      const r = route(), fn = V[r.v] || V.home;
      document.querySelectorAll(".tabbar a").forEach(a => a.classList.toggle("on", a.dataset.tab === (V[r.v] ? (r.v === "coin" || r.v === "audit" ? "" : r.v) : "home")));
      if (r.v === "activity") { try { localStorage.setItem("desk_seen", String(Date.now())); } catch (e) {} }
      const out = await fn(r.a);
      const view = $("#view"), res = typeof out === "string" ? { html: out } : out;
      const scrolls = (res.keepScroll || []).map(s => [s, ($(s) || {}).scrollLeft || 0]);
      const searching = $("#q") ? $("#q").value : null;
      view.innerHTML = res.html;
      scrolls.forEach(([s, x]) => { const el = $(s); if (el) el.scrollLeft = x; });
      if (res.after) res.after();
      if (searching !== null && $("#searchbtn")) { $("#searchbtn").click(); $("#q").value = searching; $("#q").oninput(); }
    } finally { rendering = false; }
  }
  async function refresh() {
    const r = route();
    const want = ["state.json"];
    if (r.v === "home" || !V[r.v]) want.push("chart.json", "activity.json", "coins.json");
    if (r.v === "agents") AGENTS.forEach(n => want.push("agents/" + n + ".json"));
    if (r.v === "activity") want.push("activity.json");
    if (r.v === "account") want.push("account.json", "settings.json");
    try {
      const S = await get("state.json");
      await Promise.all(want.slice(1).map(p => get(p)));
      let mint = r.a;
      if (r.v === "audit" && !mint && S.agents.auditor.last) mint = S.agents.auditor.last.mint;
      if (r.v === "audit" && mint) await get("audit/" + mint + ".json", { missing: true }).then(v => mem["audit/" + mint + ".json"] = v);
      if (r.v === "coin" && mint) await get("coin/" + mint + ".json", { missing: true }).then(v => mem["coin/" + mint + ".json"] = v);
      net.lastTs = S.ts;
      const stale = (Date.now() - Date.parse(S.ts)) / 1000 > STALE_S;
      net.ok = !stale; net.nextTry = Date.now() + POLL_S * 1000; setOffline(stale);
    } catch (e) {
      const S = cached("state.json"); net.lastTs = S ? S.ts : null; net.ok = false;
      net.nextTry = Date.now() + POLL_S * 1000; setOffline(true);
    }
    await render();
  }
  window.addEventListener("hashchange", () => { extra.pages = null; render(); refresh(); });
  window.addEventListener("resize", () => { window.__noAnim = true; render().finally(() => { window.__noAnim = false; }); });
  if (!KEY) {
    $("#view").innerHTML = `<div class="nav"><div class="navtitle" style="margin:0">Grok Bot</div></div><div class="empty" style="margin-top:80px">Open the desk once with its private link (…#d=…).<br>It stays on this phone after that.</div>`;
    return;
  }
  render(); refresh(); setInterval(refresh, POLL_S * 1000);
  if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("sw.js").catch(() => {});
})();
