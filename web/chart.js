/* Hand-drawn canvas charts (no library). devicePixelRatio aware. */
(function () {
  const RM = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  function setup(cv) {
    const dpr = window.devicePixelRatio || 1, w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    const x = cv.getContext("2d"); x.setTransform(dpr, 0, 0, dpr, 0, 0); x.clearRect(0, 0, w, h);
    return [x, w, h];
  }
  const T = (s) => Date.parse(s);
  const FONT = '600 12px -apple-system,"SF Pro Text",system-ui,sans-serif';

  /* Home line: points [[iso, value]], start value dashed, marks above the line (never overlap: later moves up a row). */
  function line(cv, pts, start, marks, opts) {
    opts = opts || {};
    const st = cv._st || (cv._st = {});
    const draw = (shift, markAlpha) => {
      const [x, w, h] = setup(cv);
      const padT = 34, padB = 10;
      if (!pts.length) {
        const y = h / 2; x.strokeStyle = "rgba(235,235,245,0.3)"; x.setLineDash([4, 4]); x.beginPath(); x.moveTo(0, y); x.lineTo(w, y); x.stroke(); x.setLineDash([]);
        return;
      }
      const t0 = T(pts[0][0]), t1 = Math.max(T(pts[pts.length - 1][0]), t0 + 1);
      let lo = Math.min(start, ...pts.map(p => p[1])), hi = Math.max(start, ...pts.map(p => p[1]));
      if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
      const pad = (hi - lo) * 0.12; lo -= pad; hi += pad;
      const X = t => ((t - t0) / (t1 - t0)) * (w - 8) + shift, Y = v => padT + (1 - (v - lo) / (hi - lo)) * (h - padT - padB);
      const col = opts.color || "#00d35a";
      // dashed start line
      x.strokeStyle = "rgba(235,235,245,0.3)"; x.lineWidth = 1; x.setLineDash([4, 4]);
      x.beginPath(); x.moveTo(0, Y(start)); x.lineTo(w, Y(start)); x.stroke(); x.setLineDash([]);
      // gradient fill under the line
      const g = x.createLinearGradient(0, padT, 0, h);
      g.addColorStop(0, col === "#00d35a" ? "rgba(0,211,90,0.28)" : "rgba(255,69,58,0.28)"); g.addColorStop(1, "rgba(0,0,0,0)");
      x.beginPath(); pts.forEach((p, i) => { const px = X(T(p[0])), py = Y(p[1]); i ? x.lineTo(px, py) : x.moveTo(px, py); });
      x.lineTo(X(T(pts[pts.length - 1][0])), h); x.lineTo(X(t0), h); x.closePath(); x.fillStyle = g; x.fill();
      x.beginPath(); pts.forEach((p, i) => { const px = X(T(p[0])), py = Y(p[1]); i ? x.lineTo(px, py) : x.moveTo(px, py); });
      x.strokeStyle = col; x.lineWidth = 2; x.lineJoin = "round"; x.stroke();
      // marks
      x.font = FONT; x.globalAlpha = markAlpha;
      const placed = [];
      (marks || []).filter(m => T(m.ts) >= t0 && T(m.ts) <= t1 + 60000).sort((a, b) => T(a.ts) - T(b.ts)).forEach(m => {
        const tw = x.measureText(m.text).width + 14, cx = Math.min(Math.max(X(T(m.ts)), tw / 2), w - tw / 2);
        let v = pts[0][1]; for (const p of pts) { if (T(p[0]) <= T(m.ts)) v = p[1]; }
        let row = 0; const ly = Y(v);
        const box = r => ({ l: cx - tw / 2, r: cx + tw / 2, y: Math.max(2, ly - 30 - r * 24) });
        while (placed.some(b => { const n = box(row); return n.l < b.r + 4 && n.r > b.l - 4 && Math.abs(n.y - b.y) < 22; }) && row < 6) row++;
        const b = box(row); placed.push(b);
        x.fillStyle = m.color; roundRect(x, b.l, b.y, tw, 20, 10); x.fill();
        x.fillStyle = m.color === "#ffd60a" ? "#000" : "#fff"; x.textAlign = "center"; x.textBaseline = "middle"; x.fillText(m.text, cx, b.y + 10.5);
        x.strokeStyle = m.color; x.lineWidth = 1; x.beginPath(); x.moveTo(cx, b.y + 20); x.lineTo(cx, ly); x.stroke();
        x.beginPath(); x.arc(X(T(m.ts)), ly, 3, 0, 7); x.fillStyle = m.color; x.fill();
      });
      x.globalAlpha = 1;
    };
    const key = pts.length ? pts[pts.length - 1][0] : "";
    const isNew = st.key && key !== st.key && st.len && pts.length >= st.len;
    const marksNew = (marks || []).length !== st.marks;
    st.key = key; st.len = pts.length; st.marks = (marks || []).length;
    if (RM || window.__noAnim || (!isNew && !marksNew)) return draw(0, 1);
    const t = performance.now(), dx = pts.length > 1 ? (cv.clientWidth - 8) / pts.length : 0;
    (function f(n) {
      const p = Math.min(1, (n - t) / 250), q = Math.min(1, (n - t) / 200);
      draw(isNew ? dx * (1 - easeOut(p)) : 0, marksNew ? q : 1);
      if (p < 1 || q < 1) requestAnimationFrame(f);
    })(t);
  }

  /* Coin candles: [{t,o,h,l,c,v}], volume bars at the bottom, orange dashed exit line. */
  function candles(cv, ks, exitLine) {
    const [x, w, h] = setup(cv);
    if (!ks.length) { x.fillStyle = "rgba(235,235,245,0.6)"; x.font = '15px -apple-system,system-ui'; x.textAlign = "center"; x.fillText("No prices yet", w / 2, h / 2); return; }
    const vh = 40, top = 10, ph = h - vh - top - 6;
    let lo = Math.min(...ks.map(k => k.l)), hi = Math.max(...ks.map(k => k.h));
    if (exitLine) { lo = Math.min(lo, exitLine); hi = Math.max(hi, exitLine); }
    if (hi - lo <= 0) { hi *= 1.01; lo *= 0.99; }
    const Y = v => top + (1 - (v - lo) / (hi - lo)) * ph, n = ks.length, cw = Math.max(2, Math.min(10, (w - 8) / n - 2));
    const vmax = Math.max(1e-9, ...ks.map(k => k.v || 0));
    ks.forEach((k, i) => {
      const cx = 4 + (i + 0.5) * (w - 8) / n, up = k.c >= k.o, col = up ? "#00d35a" : "#ff453a";
      x.strokeStyle = col; x.fillStyle = col; x.lineWidth = 1;
      x.beginPath(); x.moveTo(cx, Y(k.h)); x.lineTo(cx, Y(k.l)); x.stroke();
      const y1 = Y(Math.max(k.o, k.c)), y2 = Y(Math.min(k.o, k.c)); x.fillRect(cx - cw / 2, y1, cw, Math.max(1, y2 - y1));
      const bh = (k.v || 0) / vmax * vh; x.globalAlpha = 0.45; x.fillRect(cx - cw / 2, h - bh, cw, bh); x.globalAlpha = 1;
    });
    if (exitLine) {
      const y = Y(exitLine); x.strokeStyle = "#ff9f0a"; x.setLineDash([6, 4]); x.lineWidth = 1.5;
      x.beginPath(); x.moveTo(0, y); x.lineTo(w, y); x.stroke(); x.setLineDash([]);
      x.fillStyle = "#ff9f0a"; x.font = FONT; x.textAlign = "left"; x.textBaseline = "bottom"; x.fillText("Risk trailing stop", 6, y - 3);
    }
  }

  /* Analyst score ring 0-100, green from `green`. */
  function ring(cv, score, green) {
    const [x, w, h] = setup(cv), r = Math.min(w, h) / 2 - 7, cx = w / 2, cy = h / 2;
    x.lineWidth = 10; x.lineCap = "round"; x.strokeStyle = "#2c2c2e"; x.beginPath(); x.arc(cx, cy, r, 0, 7); x.stroke();
    if (score != null) {
      x.strokeStyle = score >= green ? "#00d35a" : "#ffd60a";
      x.beginPath(); x.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + 2 * Math.PI * Math.max(0, Math.min(100, score)) / 100); x.stroke();
    }
    x.fillStyle = "#fff"; x.font = '700 30px -apple-system,"SF Pro Display",system-ui'; x.textAlign = "center"; x.textBaseline = "middle";
    x.fillText(score == null ? "–" : String(score), cx, cy);
  }

  function roundRect(x, l, t, w, h, r) { x.beginPath(); x.moveTo(l + r, t); x.arcTo(l + w, t, l + w, t + h, r); x.arcTo(l + w, t + h, l, t + h, r); x.arcTo(l, t + h, l, t, r); x.arcTo(l, t, l + w, t, r); x.closePath(); }
  function easeOut(p) { return 1 - Math.pow(1 - p, 3); }
  
  /* Coins per hour: rows [[hour_iso, seen, killed, passed]], stacked bars (killed red, passed cyan, rest grey). */
  function bars(cv, rows) {
    const [x, w, h] = setup(cv);
    const padT = 18, padB = 20, n = rows.length;
    if (!n) return;
    const hi = Math.max(1, ...rows.map(r => r[1]));
    const gw = w / n, bw = Math.max(4, Math.min(22, gw * 0.62)), Y = v => (h - padB - padT) * v / hi;
    x.strokeStyle = "rgba(84,84,88,.55)"; x.lineWidth = 0.5; x.beginPath(); x.moveTo(0, h - padB + 0.5); x.lineTo(w, h - padB + 0.5); x.stroke();
    x.font = FONT; x.textAlign = "center";
    rows.forEach((r, i) => {
      const cx = gw * i + gw / 2, [_, seen, killed, passed] = r, other = Math.max(0, seen - killed - passed);
      let y = h - padB;
      const seg = (v, col) => { if (!v) return; const hh = Y(v); x.fillStyle = col; x.beginPath();
        if (x.roundRect) x.roundRect(cx - bw / 2, y - hh, bw, hh, 3); else x.rect(cx - bw / 2, y - hh, bw, hh); x.fill(); y -= hh + 1; };
      seg(killed, "#ff453a"); seg(passed, "#64d2ff"); seg(other, "#48484a");
      if (seen) { x.fillStyle = "rgba(235,235,245,.6)"; x.fillText(String(seen), cx, y - 4); }
      const step = n > 12 ? 4 : n > 6 ? 2 : 1;
      if (i % step === 0 || i === n - 1) {
        x.fillStyle = "rgba(235,235,245,.3)";
        x.fillText(new Date(r[0]).toLocaleTimeString([], { hour: "2-digit", hour12: false }), cx, h - 5);
      }
    });
  }
  window.Charts = { line, candles, ring, bars };
})();
