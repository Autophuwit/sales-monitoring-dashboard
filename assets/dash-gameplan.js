/* Game Plan calculations shared by the desktop and mobile Gameplan Monitoring pages. Requires dash-engine.js */
"use strict";
(function () {
if (!window.E || !E.ok) return;
const C = E.col, GP = window.GP = {};

GP.compute = FL => {
  const ok = FL.ok();
  const okNoPlan = FL.ok(C, "plan");
  const R = {
    total: E.newAcc(), sim: E.group2(), sub: E.group2(), store: new Map(),
    monthDoc: new Map(), monthStg: new Map(), dCur: new Float64Array(32), dPrev: new Float64Array(32),
    plans: new Map(E.planNames.map(p => [p, E.newAcc()])), present: new Set()
  };
  const planSel = FL.sel.plan, chans = FL.selectedChannels();
  // plans shown = selected plans, limited to the selected channel(s)
  R.planList = E.plans.filter(p => (!planSel.size || planSel.has(p.name)) && (!chans || chans.has(p.channel))).map(p => p.name);
  const planTests = R.planList.map(p => ({ stores: E.planStores.get(p) || new Set(), sims: E.planSims(p), acc: R.plans.get(p) }));
  for (let i = 0; i < E.N; i++) {
    // Overall Game Plan rows: that plan's shops (+ its SIM groups), all other slicers applied
    if (planTests.length && okNoPlan(i)) for (const t of planTests) if (t.stores.has(C.store[i]) && (!t.sims || t.sims.has(C.stg[i]))) E.addRow(t.acc, i);
    if (!ok(i)) continue;
    R.present.add(C.store[i]);
    E.addRow(R.total, i);
    E.add2(R.sim, C.stg[i], C.st[i], i);
    E.add2(R.sub, C.sub[i], C.store[i], i);
    let s = R.store.get(C.store[i]); if (!s) { s = E.newAcc(); R.store.set(C.store[i], s); } E.addRow(s, i);
    const m = C.mon[i], d = C.dom[i], g = C.ga[i];
    let md = R.monthDoc.get(m); if (!md) { md = new Map(); R.monthDoc.set(m, md); } md.set(C.doc[i], (md.get(C.doc[i]) || 0) + g);
    let ms = R.monthStg.get(m); if (!ms) { ms = new Map(); R.monthStg.set(m, ms); } ms.set(C.stg[i], (ms.get(C.stg[i]) || 0) + g);
    if (m === E.CUR_M) R.dCur[d] += g; else if (m === E.PREV_M) R.dPrev[d] += g;
  }
  // Commit, narrowed to the shops left in the filtered data
  const outletFilter = FL.active() ? R.present : null;
  R.planCommit = new Map(R.planList.map(p => [p, E.commitRows(new Set([p]), outletFilter).reduce((s, r) => s + (r[2] || 0), 0)]));
  R.commitDaily = E.commitRows(new Set(R.planList), outletFilter).reduce((s, r) => s + (r[3] || 0), 0);
  // Close Gap commit (7-11) per sub-district = sum of its shops
  R.subCommit = new Map();
  R.sub.forEach((g, k) => { let c = 0; g.kids.forEach((_, si) => c += E.closeGap.get(si) || 0); R.subCommit.set(k, c); });
  R.commitOf = (k, lvl) => lvl === 1 ? (R.subCommit.get(k) || 0) : lvl === 2 ? (E.closeGap.get(k) || 0) : [...R.subCommit.values()].reduce((a, b) => a + b, 0);
  return R;
};

GP.dims = () => [E.DIM.channel(), E.DIM.plan(), E.DIM.owner(), E.DIM.sub(), E.DIM.pname(), E.DIM.doc(), E.DIM.month()];
})();
