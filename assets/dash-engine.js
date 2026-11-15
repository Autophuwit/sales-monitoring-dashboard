/* =====================================================================
   Shared engine for the HTML dashboards (data format v3, from SQL Server).
   Calculates every dashboard metric in the browser (run rates, MoM, WoW,
   targets, game plans, market share, alerts) for all channels (7-11 / RTR / Other). Data: data/dashboard-data.js made by
   etl/export_html.py.
   ===================================================================== */
"use strict";
(function () {
/* theme chosen on the Home page (light default) */
try { if (localStorage.getItem("bma-east-theme") === "dark") document.documentElement.dataset.theme = "dark"; } catch (e) {}
const D = window.DASH_DATA;
const E = window.E = {};
E.ok = !!(D && D.f && D.version >= 3);
E.noDataHtml = `<div class="nodata"><h2>No data file yet</h2>
  <p>Run this in PowerShell from the dashboard folder (SQL Server must be running):</p>
  <p><code>python etl\\export_html.py</code></p>
  <p>It creates <code>data/dashboard-data.js</code> from the BMA_East database. Then reload this page.</p></div>`;
if (!E.ok) return;

/* ---------------- helpers ---------------- */
E.$ = id => document.getElementById(id);
E.css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
E.esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
E.fmt = (n, d = 0) => n == null || !isFinite(n) ? "" : n.toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
E.kfmt = v => Math.abs(v) >= 1e6 ? +(v / 1e6).toFixed(2) + "M" : Math.abs(v) >= 1000 ? +(v / 1000).toFixed(1) + "K" : v;
E.pctTxt = p => { if (p == null || !isFinite(p)) return ""; const r = Math.round(p * 100); return (r === 0 && p < 0 ? "-0" : r) + "%"; };
E.blank = v => v === "" || v == null ? "(Blank)" : v;
const daysIn = E.daysIn = m => new Date(Math.floor(m / 100), m % 100, 0).getDate();
const prevMonth = m => (m % 100 === 1) ? (Math.floor(m / 100) - 1) * 100 + 12 : m - 1;
E.fmtDate = k => `${k % 100}/${Math.floor(k / 100) % 100}/${Math.floor(k / 10000)}`;
E.data = D;

/* ---------------- outlets ---------------- */
// stores (= outlets): [codes, name, owner, segment, status, lat, lng, zone, type_sim, priority, district, channel, in_profile, sub_district]
E.stores = D.stores;
E.channels = D.channels || ["7-11", "RTR", "Other"];
const chIdx = new Map(E.channels.map((c, i) => [c, i]));
E.storeCh = Int8Array.from(D.stores.map(s => chIdx.get(s[11]) ?? 2));
E.ownerNames = [...new Set(D.stores.map(s => s[2] || ""))].sort();
const ownerIdx = new Map(E.ownerNames.map((o, i) => [o, i]));
E.storeOwner = Int32Array.from(D.stores.map(s => ownerIdx.get(s[2] || "")));

/* ---------------- columns ---------------- */
function columns(src, extra) {
  const n = src.day.length, c = { n };
  for (const k of ["day", "store", "gs", "st", "stg", "doc", "dist", "sub", "comp"]) c[k] = Int32Array.from(src[k]);
  for (const k of extra) c[k] = Float64Array.from(src[k]);
  c.mon = new Int32Array(n); c.dom = new Int8Array(n); c.own = new Int32Array(n); c.ch = new Int8Array(n);
  for (let i = 0; i < n; i++) {
    c.mon[i] = Math.floor(c.day[i] / 100); c.dom[i] = c.day[i] % 100;
    c.own[i] = E.storeOwner[c.store[i]]; c.ch[i] = E.storeCh[c.store[i]];
  }
  return c;
}
const C = E.col = columns(D.f, ["ga", "m1", "ap1d"]);
const CB = E.colB = D.b ? columns(D.b, ["q"]) : null;
if (CB) CB.band = Int32Array.from(D.b.band);
const N = E.N = C.n;
E.dict = D.dict;
E.months = [...new Set(C.mon)].sort((a, b) => a - b);

/* ---------------- dates: current / previous month, run-rate day, weeks ---------------- */
/* The period follows the Month slicer: Cur = latest selected month, Pre = the month before it.
   The data's own month runs to AS of day − 1 (run rate = value × 31 ÷ DAY_NO); an earlier, complete
   month uses its full days and run rate = actual. */
E.DATA_AS_OF = D.asOfKey;
E.DATA_M = Math.floor(E.DATA_AS_OF / 100);
E.weekOf = d => d <= 7 ? 1 : d <= 14 ? 2 : d <= 21 ? 3 : 4;
E.setPeriod = m => {
  if (!m || m >= E.DATA_M) m = E.DATA_M;
  E.CUR_M = m; E.PREV_M = prevMonth(m); E.CUR_DIM = daysIn(m);
  E.FULL_MONTH = m < E.DATA_M;
  E.AS_OF = E.FULL_MONTH ? m * 100 + E.CUR_DIM : E.DATA_AS_OF;
  E.DAY_NO = E.FULL_MONTH ? E.CUR_DIM : E.AS_OF % 100 - 1;
  E.CUR_WEEK = E.weekOf(Math.max(E.DAY_NO, 1));
  E.WEEK_START = [1, 8, 15, 22][E.CUR_WEEK - 1];
  E.ELAPSED = E.DAY_NO - E.WEEK_START + 1;
  E.PREV_W4_DAYS = daysIn(E.PREV_M) - 21;
};
E.setPeriod(E.DATA_M);
E.rr = v => E.FULL_MONTH ? v : E.DAY_NO > 0 ? v * 31 / E.DAY_NO : 0;

/* ---------------- aggregation ---------------- */
const THAI = D.dict.doc.indexOf("Thai ID");
E.newAcc = () => ({ pre: 0, cur: 0, m1c: 0, m1p: 0, wk: [0, 0, 0, 0], upto: 0, pw4: 0, thaiCur: 0, apc: 0, app: 0 });
E.addRow = (a, i) => {
  const m = C.mon[i], d = C.dom[i], g = C.ga[i];
  if (m === E.CUR_M) {
    a.cur += g; a.m1c += C.m1[i]; a.apc += C.ap1d[i]; a.wk[E.weekOf(d) - 1] += g;
    if (d >= E.WEEK_START && d <= E.DAY_NO) a.upto += g;
    if (C.doc[i] === THAI) a.thaiCur += g;
  } else if (m === E.PREV_M) {
    a.pre += g; a.m1p += C.m1[i]; a.app += C.ap1d[i];
    if (d >= 22) a.pw4 += g;
  }
};
E.merge = (t, a) => { for (const k of ["pre", "cur", "m1c", "m1p", "upto", "pw4", "thaiCur", "apc", "app"]) t[k] += a[k]; for (let w = 0; w < 4; w++) t.wk[w] += a.wk[w]; return t; };
E.measures = a => {
  const rr = E.rr(a.cur), m1rr = E.rr(a.m1c), diff = rr - a.pre;
  const weekVal = w => w > E.CUR_WEEK ? null
    : w === E.CUR_WEEK ? (E.ELAPSED > 0 ? a.upto * 7 / E.ELAPSED : 0)
    : a.wk[w - 1] * 7 / (w === 4 ? E.CUR_DIM - 21 : 7);
  const prevWeekVal = w => w === 1 ? (E.PREV_W4_DAYS > 0 ? a.pw4 * 7 / E.PREV_W4_DAYS : 0) : a.wk[w - 2];
  const wow = w => { const c = weekVal(w); if (c == null) return null; const p = prevWeekVal(w); return p ? (c - p) / p : 0; };
  return { pre: a.pre, cur: a.cur, rr, m1rr, m1c: a.m1c, m1p: a.m1p, diff, mom: a.pre ? diff / a.pre : null,
           weekVal, prevWeekVal, wow, curWeek: weekVal(E.CUR_WEEK), prevWeek: prevWeekVal(E.CUR_WEEK), curWow: wow(E.CUR_WEEK),
           thai: a.cur ? a.thaiCur / a.cur : 0,
           ap1dCur: a.apc, ap1dPrev: a.app, ap1dRR: E.rr(a.apc),
           pctAp1dCur: a.cur ? a.apc / a.cur : 0, pctAp1dPrev: a.pre ? a.app / a.pre : 0 };
};
E.group2 = () => new Map();
E.add2 = (map, k1, k2, i) => {
  let g = map.get(k1); if (!g) { g = { acc: E.newAcc(), kids: new Map() }; map.set(k1, g); }
  E.addRow(g.acc, i);
  let k = g.kids.get(k2); if (!k) { k = E.newAcc(); g.kids.set(k2, k); }
  E.addRow(k, i);
};
/* AP1D by price band for the band rows passing `ok` -> [{band, cur, prev, rr, diff, mom}] */
E.bandTable = ok => {
  if (!CB) return [];
  const cur = new Float64Array(D.dict.band.length), prev = new Float64Array(D.dict.band.length);
  for (let i = 0; i < CB.n; i++) {
    if (!ok(i)) continue;
    if (CB.mon[i] === E.CUR_M) cur[CB.band[i]] += CB.q[i]; else if (CB.mon[i] === E.PREV_M) prev[CB.band[i]] += CB.q[i];
  }
  return D.dict.band.map((b, k) => { const rr = E.rr(cur[k]); return { band: b, cur: cur[k], prev: prev[k], rr, diff: rr - prev[k], mom: prev[k] ? (rr - prev[k]) / prev[k] : null }; });
};

/* ---------------- plans, targets, commitments, market share ---------------- */
// plans: [name, channel, sim_rule];  gameplan: [outlet, name, commit, commit_daily, channel, sim_rule, start, end]
E.plans = (D.plans || []).map(p => ({ name: p[0], channel: p[1], rule: p[2] }));
E.planNames = E.plans.map(p => p.name);
E.planStores = new Map(E.plans.map(p => [p.name, new Set()]));
for (const r of D.gameplan || []) { if (!E.planStores.has(r[1])) E.planStores.set(r[1], new Set()); E.planStores.get(r[1]).add(r[0]); }
E.planChannel = name => (E.plans.find(p => p.name === name) || {}).channel;
const stgSet = name => new Set([D.dict.stg.indexOf(name)].filter(i => i >= 0));
const RULE = { GP350: stgSet("Mass 350"), GP199: stgSet("Mass 199") };
E.planSims = name => RULE[(E.plans.find(p => p.name === name) || {}).rule] || null;   // null = all SIM types (test on stg)
E.commitRows = (plans, outletPresent) => (D.gameplan || []).filter(r => (!plans || plans.has(r[1])) && (!outletPresent || outletPresent.has(r[0])));
E.planDates = name => { const r = (D.gameplan || []).find(x => x[1] === name && (x[6] || x[7])); return r ? [r[6], r[7]] : ["", ""]; };

// owner targets: [owner, target_type, target_ga, target_m1, channel]
E.targetTypes = [...new Set((D.targets || []).map(t => t[1]))].sort();
E.ownerTarget = (owners, types, channels) => {
  let ga = 0, m1 = 0;
  for (const [o, t, g, m, ch] of D.targets || []) {
    if (owners && !owners.has(o)) continue;
    if (types && types.size && !types.has(t)) continue;
    if (channels && !channels.has(ch)) continue;
    ga += g || 0; m1 += m || 0;
  }
  return { ga, m1 };
};
// shop targets (7-11 'Target All Type'): [outlet, target_ga, target_m1, ga_daily, m1_daily]
E.tat = new Map((D.tat || []).map(r => [r[0], r]));
E.closeGap = new Map((D.closegap || []).map(r => [r[0], r[1]]));
// market share (RTR): [outlet, share_true, market_size, ga_true, ga_ais, tumbon]; 7-11 = 100%
E.market = new Map((D.market || []).map(r => [r[0], { share: r[1], size: r[2], gaTrue: r[3], gaAis: r[4], tumbon: r[5] }]));
E.marketShare = outlets => {
  let n = 0, sum = 0, size = 0, gaT = 0, n711 = 0;
  outlets.forEach(o => {
    const ch = E.stores[o][11];
    if (ch === "7-11") { n711++; return; }
    const m = E.market.get(o); if (!m || m.share == null) return;
    n++; sum += m.share; size += m.size || 0; gaT += m.gaTrue || 0;
  });
  return { rtrAvg: n ? sum / n : null, rtrOutlets: n, size, gaTrue: gaT, n711 };
};

/* ---------------- alerts ----------------
   per 7-11 zone: weighted avg & stdev of %MoM (weight = previous GA); alert when %MoM <= avg - stdev */
E.alerts = (() => {
  const acc = new Map();
  for (let i = 0; i < N; i++) { if (E.stores[C.store[i]][11] !== "7-11") continue; let a = acc.get(C.store[i]); if (!a) { a = E.newAcc(); acc.set(C.store[i], a); } E.addRow(a, i); }
  const zones = new Map();
  acc.forEach((a, o) => { const z = E.stores[o][7]; if (!z) return; const M = E.measures(a); if (!M.pre || M.mom == null) return;
    if (!zones.has(z)) zones.set(z, []); zones.get(z).push({ o, mom: M.mom, w: M.pre }); });
  const out = new Map();
  zones.forEach(list => {
    const W = list.reduce((s, x) => s + x.w, 0); if (!W) return;
    const avg = list.reduce((s, x) => s + x.w * x.mom, 0) / W;
    const sd = Math.sqrt(list.reduce((s, x) => s + x.w * (x.mom - avg) ** 2, 0) / W);
    const thr = avg - sd;
    list.forEach(x => { if (x.mom <= thr) out.set(x.o, { mom: x.mom, thr, avg }); });
  });
  return out;
})();

/* ---------------- click-to-filter targets ----------------
   E.pk(src, col, v, label) registers "rows where column col == v" picked from visual src and returns its id */
E.pickReg = new Map();
const regPick = (id, src, label, test) => { if (!E.pickReg.has(id)) E.pickReg.set(id, { id, src, label, test }); return id; };
E.pk = (src, col, v, label) => regPick(`${src}|${col}=${v}`, src, label, cols => { const c = cols[col]; return c ? (i => c[i] === v) : (() => true); });
E.pkChannel = (src, name) => E.pk(src, "ch", E.channels.indexOf(name), "Channel: " + name);
E.pkStore = (src, si) => E.pk(src, "store", si, "Shop: " + E.stores[si][1]);
E.pkPlan = (src, name) => regPick(`${src}|plan=${name}`, src, `Game Plan: ${name} (${E.planChannel(name)})`, cols => {
  const st = E.planStores.get(name) || new Set(), sims = E.planSims(name);
  return i => st.has(cols.store[i]) && (!sims || sims.has(cols.stg[i]));
});
// customer segment of the shop (stores[i][3]); "" = no segment
E.pkSeg = (src, seg) => regPick(`${src}|seg=${seg}`, src, "Segment: " + (seg || "(Blank)"), cols => {
  const set = new Set(); E.stores.forEach((s, i) => { if ((s[3] || "") === seg) set.add(i); });
  return i => set.has(cols.store[i]);
});
/* customer segment colours: fixed per segment (by shop count), so they do not change when filtering */
const SEG_PAL = ["#e60012", "#8e2a8b", "#1e96e8", "#f08a24", "#d6409f", "#00a3a3", "#6a4fc9", "#9c6b30", "#7fa33a", "#ff7a6b", "#4e6a9e", "#b8a000"];
const SEG_ORDER = (() => { const n = new Map(); E.stores.forEach(s => { if (s[3]) n.set(s[3], (n.get(s[3]) || 0) + 1); });
  return [...n.keys()].sort((a, b) => n.get(b) - n.get(a) || a.localeCompare(b)); })();
E.segColour = g => { const i = SEG_ORDER.indexOf(g); return i < 0 || i >= SEG_PAL.length ? E.css("--other") : SEG_PAL[i]; };
/* legend html for the segments of the shops in storeMap (clickable) */
E.segLegend = (src, storeMap) => {
  const here = new Map(); storeMap.forEach((a, si) => { const g = E.stores[si][3] || ""; here.set(g, (here.get(g) || 0) + 1); });
  const segs = [...here.keys()].sort((a, b) => (a === "") - (b === "") || here.get(b) - here.get(a) || a.localeCompare(b));
  return segs.map(g => { const id = E.pkSeg(src, g);
    return `<span class="${E.pickCls(id).trim()}"${E.pickAttr(id)} title="${here.get(g)} shop(s)"><i style="background:${E.segColour(g)}"></i>${E.esc(g || "(No segment)")}</span>`; }).join("");
};
E.pkDow = (src, dow, label) => regPick(`${src}|dow=${dow}`, src, "Day: " + label, cols => i => {
  const m = cols.mon[i]; return (new Date(Math.floor(m / 100), m % 100 - 1, cols.dom[i]).getDay() + 6) % 7 === dow;
});
E.isPicked = id => { if (!E.FL) return false; const p = E.pickReg.get(id), g = p && E.FL.xs.get(p.src); return !!(g && g.ids.has(id)); };
E.pickedIn = src => { const g = E.FL && E.FL.xs.get(src); return g ? g.ids : null; };   // ids picked in one visual, or null
const multi = ev => !!(ev && (ev.ctrlKey || ev.metaKey || ev.shiftKey));
E.pickAttr = id => id ? ` data-pick="${E.esc(id)}"` : "";
E.pickCls = id => !id ? "" : E.isPicked(id) ? " pickable picked" : " pickable";
/* touch: long-press (0.5 s) on a row = add/remove it, like Ctrl+click */
let lpTimer = null, lpDone = false;
document.addEventListener("touchstart", e => {
  const el = e.target.closest && e.target.closest("[data-pick]"); lpDone = false; if (!el || !E.FL) return;
  lpTimer = setTimeout(() => { lpDone = true; if (navigator.vibrate) navigator.vibrate(15); E.FL.pick(el.dataset.pick, true); }, 500);
}, { passive: true });
["touchend", "touchmove", "touchcancel"].forEach(t => document.addEventListener(t, () => clearTimeout(lpTimer), { passive: true }));
document.addEventListener("contextmenu", e => { if (e.target.closest && e.target.closest("[data-pick]") && lpDone) e.preventDefault(); });
document.addEventListener("click", e => {
  if (lpDone) { lpDone = false; e.stopPropagation(); return; }   // the click that follows a long-press
  if (!E.FL || e.target.closest(".tog, .row.tap, a, input, select")) return;
  const el = e.target.closest("[data-pick]"); if (el) { e.stopPropagation(); E.FL.pick(el.dataset.pick, multi(e)); return; }
  const x = e.target.closest(".xf-clear"); if (x) E.FL.clearCross(x.dataset.src || null);
});
/* Chart.js options for a clickable chart: idFor(element, chart) -> pick id or null */
E.chartPick = (src, idFor) => ({
  onClick: (ev, els, chart) => {
    if (!E.FL) return;
    const near = chart.getElementsAtEventForMode(ev.native || ev, "nearest", { intersect: true }, true);
    if (!near.length) { if (E.FL.xs.has(src)) E.FL.clearCross(src); return; }   // click empty space = clear this chart's selection
    const id = idFor(near[0], chart); if (id) E.FL.pick(id, multi(ev.native));
  },
  onHover: (ev, els, chart) => { const n = chart.getElementsAtEventForMode(ev.native || ev, "nearest", { intersect: true }, true); ev.native.target.style.cursor = n.length ? "pointer" : "default"; }
});
E.crossChip = FL => [...FL.xs.values()].map(g => `<span class="chip xf-chip" title="${E.esc(g.items.map(x => x.label).join("\n"))}">${E.esc(g.label)} <button type="button" class="xf-clear" data-src="${E.esc(g.src)}" aria-label="Clear this selection">✕</button></span>`).join("");

/* ---------------- filters ----------------
   dim: { key, label, opts(): [{v,label}], col?: 'columnName', test?: (set, cols) => (i => bool), cross?: bool } */
E.Filters = function (dims, onChange) {
  const self = this;
  this.dims = dims;
  this.sel = Object.fromEntries(dims.map(d => [d.key, new Set()]));
  const periodDim = dims.find(d => d.period);
  this.syncPeriod = () => {
    if (!periodDim) return;
    const s = self.sel[periodDim.key];
    E.setPeriod(s.size ? Math.max(...s) : null);
    const el = document.getElementById("asOf"); if (el) el.textContent = E.fmtDate(E.AS_OF);
  };
  this.onChange = () => { self.syncPeriod(); onChange(); };
  E.FL = this;
  /* cross-filter (click on a table row / chart point / map bubble)
     xs: visual id -> its selection. Inside one visual the picks are OR-ed; different visuals are AND-ed.
     click = select only this (clears the others); Ctrl/⌘+click = add/remove, also across visuals.
     A visual is never filtered by its own selection (it stays whole and shows the highlight). */
  this.xs = new Map(); this.skipSrc = null;
  const group = (src, ids) => {
    const items = [...ids].map(x => E.pickReg.get(x));
    return { src, ids, items,
      label: items.length === 1 ? items[0].label : `${items[0].label.split(":")[0]}: ${items.length} selected`,
      test: cols => { const ts = items.map(it => it.test(cols)); return i => { for (const t of ts) if (t(i)) return true; return false; }; } };
  };
  this.pick = (id, add) => {
    const p = E.pickReg.get(id); if (!p) return;
    if (add) {
      const ids = new Set(self.xs.has(p.src) ? self.xs.get(p.src).ids : []);
      ids.has(id) ? ids.delete(id) : ids.add(id);
      ids.size ? self.xs.set(p.src, group(p.src, ids)) : self.xs.delete(p.src);
    } else {
      const only = self.xs.size === 1 && self.xs.has(p.src) && self.xs.get(p.src).ids.size === 1 && self.xs.get(p.src).ids.has(id);
      self.xs.clear();
      if (!only) self.xs.set(p.src, group(p.src, new Set([id])));
    }
    self.onChange();
  };
  this.clearCross = src => { if (src) self.xs.delete(src); else self.xs.clear(); self.onChange(); };
  /* run compute() with every selection, plus once per selecting visual without its own selection.
     returns view(visualId) -> the result to draw that visual from */
  this.views = compute => {
    const R = compute(), per = new Map();
    self.xs.forEach((_, src) => { self.skipSrc = src; try { per.set(src, compute()); } finally { self.skipSrc = null; } });
    const view = id => per.get(id) || R;
    view.R = R; return view;
  };
  const testFor = (d, cols) => {
    const s = self.sel[d.key];
    if (!s.size) return null;
    if (d.test) return d.test(s, cols);
    if (d.col) { const col = cols[d.col]; return i => s.has(col[i]); }
    return null;                                     // dims that do not filter rows (e.g. target type)
  };
  this.compile = (skipKey, cols = C) => {
    const t = dims.filter(d => d.key !== skipKey).map(d => testFor(d, cols)).filter(Boolean);
    self.xs.forEach((g, src) => { if (src !== self.skipSrc) t.push(g.test(cols)); });
    return t;
  };
  this.passes = tests => i => { for (const t of tests) if (!t(i)) return false; return true; };
  this.ok = (cols = C, skipKey) => this.passes(this.compile(skipKey, cols));
  this.active = () => [...self.xs.keys()].some(src => src !== self.skipSrc) || dims.some(d => (d.col || d.test) && self.sel[d.key].size);
  this.available = key => {
    const d = dims.find(x => x.key === key), all = d.opts();
    if (!d.col || d.cross === false) return all;
    const ok = this.ok(C, key), present = new Set(), col = C[d.col];
    for (let i = 0; i < N; i++) if (ok(i)) present.add(col[i]);
    return all.filter(o => present.has(o.v) || self.sel[key].has(o.v));
  };
  this.selectedChannels = () => { const s = self.sel.channel; return s && s.size ? new Set([...s].map(i => E.channels[i])) : null; };
};

/* slicer dropdowns: multi-select, search, select all, options cross-filtered by the other slicers */
E.buildSlicers = function (host, FL, opts = {}) {
  let openPop = null;
  host.innerHTML = FL.dims.map(d => `<div class="slicer" data-k="${d.key}"><label>${d.label}</label>
    <button class="sl-btn" type="button" aria-haspopup="true">All</button>
    <div class="sl-pop" hidden><input type="search" placeholder="Search"><div class="sl-opts"></div></div></div>`).join("")
    + (opts.clear === false ? "" : `<button class="clear-btn" type="button">Clear filters</button>`) + `<span class="xf-host"></span>`;
  const render = k => {
    const el = host.querySelector(`.slicer[data-k="${k}"]`);
    const q = el.querySelector("input").value.trim().toLowerCase();
    const list = FL.available(k).filter(o => !q || String(o.label).toLowerCase().includes(q));
    const s = FL.sel[k], shown = list.slice(0, 400);
    el.querySelector(".sl-opts").innerHTML =
      `<label class="opt all"><input type="checkbox" data-all ${s.size ? "" : "checked"}> Select all</label>` +
      shown.map((o, j) => `<label class="opt"><input type="checkbox" data-j="${j}" ${s.has(o.v) ? "checked" : ""}> ${E.esc(o.label)}</label>`).join("") +
      (list.length > shown.length ? `<div class="sl-more">${list.length - shown.length} more… type to search</div>` : "");
    el._list = shown;
  };
  host.querySelectorAll(".slicer").forEach(el => {
    const k = el.dataset.k, pop = el.querySelector(".sl-pop");
    el.querySelector(".sl-btn").addEventListener("click", e => {
      e.stopPropagation();
      if (openPop && openPop !== pop) openPop.hidden = true;
      pop.hidden = !pop.hidden; openPop = pop.hidden ? null : pop;
      if (!pop.hidden) { pop.querySelector("input").value = ""; render(k); if (!opts.noFocus) pop.querySelector("input").focus(); }
    });
    pop.addEventListener("click", e => e.stopPropagation());
    pop.querySelector("input").addEventListener("input", () => render(k));
    pop.querySelector(".sl-opts").addEventListener("change", e => {
      const cb = e.target; if (cb.type !== "checkbox") return;
      const s = FL.sel[k];
      if (cb.dataset.all !== undefined) s.clear();
      else {
        const v = el._list[+cb.dataset.j].v;
        cb.checked ? s.add(v) : s.delete(v);
        if (s.size && s.size === FL.available(k).length) s.clear();
      }
      render(k); FL.onChange();
    });
  });
  document.addEventListener("click", () => { if (openPop) { openPop.hidden = true; openPop = null; } });
  const clr = host.querySelector(".clear-btn");
  if (clr) clr.addEventListener("click", () => { Object.values(FL.sel).forEach(s => s.clear()); FL.xs.clear(); FL.onChange(); });
  return {
    refresh() {
      host.querySelector(".xf-host").innerHTML = E.crossChip(FL);
      FL.dims.forEach(d => {
        const btn = host.querySelector(`.slicer[data-k="${d.key}"] .sl-btn`), s = FL.sel[d.key];
        let txt = "All";
        if (s.size === 1) { const v = [...s][0]; txt = (d.opts().find(o => o.v === v) || {}).label ?? v; }
        else if (s.size > 1) txt = "Multiple selections";
        btn.textContent = txt; btn.title = txt; btn.classList.toggle("active", s.size > 0);
      });
    }
  };
};

/* standard dimension definitions */
const byLabel = (a, b) => String(a.label).localeCompare(String(b.label), "th");
const dictOpts = name => () => D.dict[name].map((o, i) => ({ v: i, label: E.blank(o) })).sort(byLabel);
E.DIM = {
  channel: (label = "Channel") => ({ key: "channel", label, col: "ch", opts: () => E.channels.map((c, i) => ({ v: i, label: c })) }),
  owner: (label = "Owner") => ({ key: "owner", label, col: "own", opts: () => E.ownerNames.map((o, i) => ({ v: i, label: E.blank(o) })) }),
  gs: (label = "GROUP_SIM") => ({ key: "gs", label, col: "gs", opts: dictOpts("gs") }),
  doc: (label = "DOC_TYP_GP") => ({ key: "doc", label, col: "doc", opts: dictOpts("doc") }),
  dist: (label = "DISTRICT_EN") => ({ key: "dist", label, col: "dist", opts: dictOpts("dist") }),
  sub: (label = "SUB_DISTRICT_EN") => ({ key: "sub", label, col: "sub", opts: dictOpts("sub") }),
  comp: (label = "COMPANY") => ({ key: "comp", label, col: "comp", opts: dictOpts("comp") }),
  pname: (label = "PARTNER_NAME") => ({ key: "pname", label, col: "store", opts: () => D.stores.map((s, i) => ({ v: i, label: s[1] })).sort(byLabel) }),
  code: (label = "Partner Code") => ({ key: "code", label, col: "store", opts: () => D.stores.map((s, i) => ({ v: i, label: s[0] })).filter(o => o.label).sort(byLabel) }),
  // keeps the selected months plus the month before the latest one (needed for Pre GA / %MoM)
  month: (label = "Month") => ({ key: "month", label, col: "mon", period: true, opts: () => E.months.map(m => ({ v: m, label: String(m) })),
    test: (s, cols) => i => s.has(cols.mon[i]) || cols.mon[i] === E.PREV_M }),
  tt: (label = "Target Type") => ({ key: "tt", label, opts: () => E.targetTypes.map(t => ({ v: t, label: t })) }),
  plan: (label = "Game Plan") => ({ key: "plan", label, cross: false, opts: () => E.plans.map(p => ({ v: p.name, label: `${p.name} (${p.channel})` })),
    test: (s, cols) => { const u = new Set(); s.forEach(p => (E.planStores.get(p) || []).forEach(x => u.add(x))); return i => u.has(cols.store[i]); } })
};

/* ---------------- shared render bits ---------------- */
E.pctCell = p => {
  if (p == null || !isFinite(p)) return `<td class="pct"></td>`;
  const w = Math.min(Math.abs(p), 1) * 100, r = Math.round(p * 100);
  return `<td class="pct"><div class="bar ${r > 0 ? "pos" : r < 0 ? "neg" : ""}" style="width:${w}%"></div><span>${E.pctTxt(p)}</span></td>`;
};
E.deltaHtml = (diff, base, label) => {
  if (!base) return `<div class="delta"><span class="d flat">–</span><br><span class="vs">no ${label.toLowerCase()} value</span></div>`;
  const c = Math.round(diff) > 0 ? "up" : Math.round(diff) < 0 ? "down" : "flat";
  return `<div class="delta"><span class="d ${c}">${c === "up" ? "▲" : c === "down" ? "▼" : "■"} ${E.pctTxt(diff / base)} (${diff > 0 ? "+" : ""}${E.fmt(diff)})</span><br><span class="vs">vs ${E.fmt(base)} ${label}</span></div>`;
};
E.diffCell = v => `<td class="${Math.round(v) < 0 ? "down" : Math.round(v) > 0 ? "up" : ""}">${v > 0 ? "+" : ""}${E.fmt(v)}</td>`;

/* expandable matrix rows: remembers what is open across re-renders */
E.openGroups = new Set();
document.addEventListener("click", e => {
  const b = e.target.closest(".tog"); if (!b) return;
  const id = b.dataset.g, open = b.getAttribute("aria-expanded") !== "true";
  open ? E.openGroups.add(id) : E.openGroups.delete(id);
  b.setAttribute("aria-expanded", open); b.textContent = open ? "−" : "+";
  b.closest("table").querySelectorAll("tr.child").forEach(tr => { if (tr.dataset.p === id) tr.hidden = !open; });
});
/* generic two-level matrix. cols: [{h, cell(M, acc, key, level) -> "<td>"}] */
E.matrix = (el, map, name1, name2, hdr, cols, opt = {}) => {
  const sortFn = opt.sort || ((a, b) => a.M.diff - b.M.diff);
  const keep = opt.keep || (x => x.M.cur || x.M.pre);
  const groups = [...map.entries()].map(([k, g]) => ({ k, name: name1(k), g, M: E.measures(g.acc) })).filter(keep).sort(sortFn);
  if (!groups.length) { el.innerHTML = `<div class="empty">No data for this selection</div>`; return; }
  const row = (x, lvl) => cols.map(c => c.cell(x.M, x.acc || x.g.acc, x.k, lvl)).join("");
  let h = `<table><thead><tr><th>${hdr}</th>${cols.map(c => `<th>${c.h}</th>`).join("")}</tr></thead><tbody>`;
  const tot = E.newAcc();
  groups.forEach(x => {
    const id = el.id + ":" + x.k, open = E.openGroups.has(id);
    const kids = opt.noKids ? [] : [...x.g.kids.entries()].map(([k, a]) => ({ k, name: name2(k), acc: a, M: E.measures(a) })).filter(keep).sort(sortFn);
    const p1 = opt.pick1 && opt.pick1(x.k);
    h += `<tr class="grp${E.pickCls(p1)}"${E.pickAttr(p1)}><td>${kids.length ? `<button class="tog" data-g="${E.esc(id)}" aria-expanded="${open}">${open ? "−" : "+"}</button>` : ""}${E.esc(x.name)}</td>${row(x, 1)}</tr>`;
    kids.forEach(c => { const p2 = opt.pick2 && opt.pick2(c.k, x.k);
      h += `<tr class="child${E.pickCls(p2)}"${E.pickAttr(p2)} data-p="${E.esc(id)}" ${open ? "" : "hidden"}><td title="${E.esc(c.name)}">${E.esc(c.name)}</td>${row(c, 2)}</tr>`; });
    E.merge(tot, x.g.acc);
  });
  const T = { k: "__total", acc: tot, M: E.measures(tot) };
  h += `<tr class="total"><td>Total</td>${row(T, 0)}</tr></tbody></table>`;
  el.innerHTML = h;
};
/* AP1D by price band table */
E.bandHtml = rows => {
  const live = rows.filter(r => r.cur || r.prev);
  if (!live.length) return `<div class="empty">No AP1D for this selection</div>`;
  const t = live.reduce((s, r) => ({ cur: s.cur + r.cur, prev: s.prev + r.prev }), { cur: 0, prev: 0 });
  const tr = E.rr(t.cur);
  return `<table><thead><tr><th>AP1D Band</th><th>Pre AP1D</th><th>Cur AP1D</th><th>AP1D RR</th><th>Diff</th><th>%MOM</th></tr></thead><tbody>` +
    live.map(r => `<tr><td>${r.band}</td><td>${E.fmt(r.prev)}</td><td>${E.fmt(r.cur)}</td><td>${E.fmt(r.rr)}</td>${E.diffCell(r.diff)}${E.pctCell(r.mom)}</tr>`).join("") +
    `<tr class="total"><td>Total</td><td>${E.fmt(t.prev)}</td><td>${E.fmt(t.cur)}</td><td>${E.fmt(tr)}</td>${E.diffCell(tr - t.prev)}${E.pctCell(t.prev ? (tr - t.prev) / t.prev : null)}</tr></tbody></table>`;
};
E.channelLabel = FL => { const s = FL.selectedChannels(); return s ? [...s].join(" + ") : "All channels"; };

/* ---------------- charts ---------------- */
E.charts = {};
E.theme = () => ({ ink2: E.css("--ink-2"), muted: E.css("--muted"), grid: E.css("--grid"), axis: E.css("--axis"), surface: E.css("--surface"),
  S: ["--s1", "--s2", "--s3", "--s4"].map(E.css), other: E.css("--other"), prev: E.css("--prev") });
E.draw = (id, cfg) => { if (typeof Chart === "undefined") return; if (E.charts[id]) E.charts[id].destroy(); E.charts[id] = new Chart(E.$(id), cfg); };
E.initChartDefaults = () => {
  if (typeof Chart === "undefined") return;
  const T = E.theme();
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.font.size = 11; Chart.defaults.color = T.muted; Chart.defaults.plugins.legend.display = false;
  Chart.defaults.animation = false;
};
E.axes = (T, xTitle, yTitle, extra = {}) => ({
  x: { grid: { display: false }, border: { color: T.axis }, title: { display: !!xTitle, text: xTitle }, ticks: { maxRotation: 0, autoSkip: true } },
  y: { grid: { color: T.grid }, border: { display: false }, beginAtZero: true, title: { display: !!yTitle, text: yTitle }, ticks: { callback: E.kfmt, maxTicksLimit: 6 } }, ...extra });
E.line = (label, data, color, extra = {}) => ({ label, data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 5, pointHitRadius: 10, tension: 0.25, ...extra });
E.lineOpts = (T, x, y, extra = {}) => ({ ...extra, responsive: true, maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, scales: E.axes(T, x, y),
  plugins: { tooltip: { callbacks: { label: c => `${c.dataset.label}: ${E.fmt(c.parsed.y)}` } } } });
E.hLine = (value, label, T) => ({ id: "hline", afterDatasetsDraw(c) {
  if (!value) return;
  const y = c.scales.y.getPixelForValue(value), { left, right } = c.chartArea, x = c.ctx;
  x.save(); x.strokeStyle = T.ink2; x.setLineDash([4, 4]); x.lineWidth = 1; x.beginPath(); x.moveTo(left, y); x.lineTo(right, y); x.stroke();
  x.setLineDash([]); x.fillStyle = T.ink2; x.textAlign = "right"; x.font = "11px system-ui"; x.fillText(label, right, y - 4); x.restore(); } });
E.barLabels = (fmtFn, T, horizontal) => ({ id: "vals", afterDatasetsDraw(c) {
  const x = c.ctx; x.save(); x.fillStyle = T.ink2; x.font = "11px system-ui";
  c.data.datasets.forEach((ds, di) => c.getDatasetMeta(di).data.forEach((b, i) => {
    const v = ds.data[i]; if (v == null) return;
    if (horizontal) { x.textAlign = "left"; x.textBaseline = "middle"; x.fillText(fmtFn(v), b.x + 4, b.y); }
    else { x.textAlign = "center"; x.fillText(fmtFn(v), b.x, b.y - 5); }
  })); x.restore(); } });
/* top-4 series by total, rest folded into "Other" */
E.seriesByMonth = (monthMap, dict, months) => {
  const totals = new Map();
  monthMap.forEach(mm => mm.forEach((v, k) => totals.set(k, (totals.get(k) || 0) + v)));
  const keys = [...totals.entries()].sort((a, b) => b[1] - a[1]).map(x => x[0]);
  const top = keys.slice(0, 4), rest = keys.slice(4);
  const s = top.map(k => ({ k, name: E.blank(dict[k]), data: months.map(m => monthMap.get(m)?.get(k) ?? null) }));
  if (rest.length) s.push({ name: "Other", other: true, data: months.map(m => rest.reduce((t, k) => t + (monthMap.get(m)?.get(k) || 0), 0)) });
  return s;
};
/* click a point on a by-month line chart -> filter that series (doc / sim type group) */
E.seriesPick = (src, ser, col, dict) => E.chartPick(src, el => { const s = ser[el.datasetIndex]; return s && s.k != null ? E.pk(src, col, s.k, `${col === "doc" ? "Doc Type" : "Sim Type Group"}: ${E.blank(dict[s.k])}`) : null; });
E.legendHtml = (ser, T) => ser.map((s, i) => `<span><i style="background:${s.other ? T.other : T.S[i]}"></i>${E.esc(s.name)}</span>`).join("");

/* ---------------- maps ---------------- */
E.makeMap = id => {
  if (typeof L === "undefined") { E.$(id).textContent = "Map needs an internet connection."; return null; }
  const m = L.map(id, { scrollWheelZoom: false }).setView([13.74, 100.52], 12);
  m.on("click", () => { if (E.FL && E.FL.xs.has(id)) E.FL.clearCross(id); });   // click empty map = clear its selection
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", { attribution: "Tiles &copy; Esri", maxZoom: 16 }).addTo(m);
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}", { maxZoom: 16 }).addTo(m);
  return { id, map: m, layer: L.layerGroup().addTo(m), fitKey: null };
};
E.storeTip = (s, M) => `<b>${E.esc(s[1])}</b> <span style="opacity:.7">(${E.esc(s[11])})</span><br>Owner: ${E.esc(s[2] || "–")}${s[3] ? " · Segment: " + E.esc(s[3]) : ""}<br>Pre GA ${E.fmt(M.pre)} · Cur GA ${E.fmt(M.cur)} · GA RR ${E.fmt(M.rr)}<br>Diff ${E.fmt(M.diff)} · %MoM ${E.pctTxt(M.mom) || "–"}`;
E.mapPoints = storeMap => {
  const pts = [];
  storeMap.forEach((a, si) => { const s = E.stores[si]; if (s[5] == null || s[6] == null) return; const M = E.measures(a); if (!M.cur && !M.pre) return; pts.push({ si, s, M }); });
  return pts.sort((a, b) => b.M.rr - a.M.rr);
};
const radius = M => Math.max(4, Math.min(22, 3 + Math.sqrt(Math.max(M.rr, 0)) * 1.3));
/* bubbles: colourFn(point) -> colour; default green/red by %MoM */
E.drawMap = (mm, storeMap, colourFn) => {
  if (!mm) return;
  const good = E.css("--good"), bad = E.css("--bad"), other = E.css("--other");
  const col = colourFn || (p => { const r = p.M.mom == null ? 0 : Math.round(p.M.mom * 100); return r > 0 ? good : r < 0 ? bad : other; });
  const pts = E.mapPoints(storeMap);
  mm.layer.clearLayers();
  const picked = E.pickedIn(mm.id);
  pts.forEach(p => {
    const id = E.pkStore(mm.id, p.si), on = !!picked && picked.has(id), dim = picked && !on;
    L.circleMarker([p.s[5], p.s[6]], { radius: radius(p.M), color: on ? E.css("--ink") : "#fff", weight: on ? 3 : 1.5, fillColor: col(p), fillOpacity: dim ? 0.25 : 0.75, bubblingMouseEvents: false })
      .bindTooltip(E.storeTip(p.s, p.M) + `<br><i style="opacity:.7">click to filter · Ctrl+click to add</i>`)
      .on("click", ev => E.FL && E.FL.pick(id, multi(ev.originalEvent))).addTo(mm.layer);
  });
  // re-fit only when the set of shops changes (not when you click a bubble on this map)
  const key = pts.map(p => p.si).join(",");
  if (pts.length && key !== mm.fitKey) mm.map.fitBounds(L.latLngBounds(pts.map(p => [p.s[5], p.s[6]])), { padding: [20, 20], maxZoom: 15 });
  mm.fitKey = key;
};
E.drawMomMap = (mm, storeMap) => E.drawMap(mm, storeMap);
})();
