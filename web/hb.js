/* Heartbeat freshness, measured against the state file's own generated ts (S.ts), never the phone clock:
   Pages serves state.json 2-5 min after the desk wrote it, so "now - heartbeat" would call a healthy desk down.
   Whether the data itself is old is the offline row's job (STALE_S in app.js). */
(function (root) {
  const FRESH_S = 120;
  const t = s => { const v = Date.parse(s); return isNaN(v) ? null : v; };
  function age(S, iso) { const a = t(iso), b = t(S && S.ts); return a == null || b == null ? null : Math.max(0, (b - a) / 1000); }
  function fresh(S, iso) { const x = age(S, iso); return x != null && x < FRESH_S; }
  function live(S, agents) { return agents.filter(n => S.agents && S.agents[n] && !S.agents[n].paused && fresh(S, (S.heartbeats || {})[n] || S.agents[n].heartbeat)).length; }
  const api = { FRESH_S, age, fresh, live };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.DeskHB = api;
})(typeof window !== "undefined" ? window : globalThis);
