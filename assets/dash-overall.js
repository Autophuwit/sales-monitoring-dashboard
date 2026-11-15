/* Overall Performance calculations shared by the desktop and mobile pages. Requires dash-engine.js */
"use strict";
(function () {
if (!window.E || !E.ok) return;
const C = E.col, OV = window.OV = {};
OV.dims = () => [E.DIM.channel(), E.DIM.owner(), E.DIM.gs(), E.DIM.tt(), E.DIM.doc(), E.DIM.dist(), E.DIM.sub(), E.DIM.month()];
OV.compute = FL => {
  const ok = FL.ok();
  const R = {
    total: E.newAcc(), wkGs: E.group2(), wkDoc: E.group2(), sub: E.group2(), sim: E.group2(), store: new Map(),
    monthDoc: new Map(), monthStg: new Map(),
    dCurGA: new Float64Array(32), dPrevGA: new Float64Array(32), dCurM1: new Float64Array(32), dPrevM1: new Float64Array(32),
    dow: new Float64Array(7), owners: new Set()
  };
  for (let i = 0; i < E.N; i++) {
    if (!ok(i)) continue;
    const o = C.store[i];
    if (E.stores[o][2]) R.owners.add(E.stores[o][2]);
    E.addRow(R.total, i);
    E.add2(R.wkGs, C.gs[i], C.stg[i], i);
    E.add2(R.wkDoc, C.doc[i], C.dist[i], i);
    E.add2(R.sub, C.sub[i], o, i);
    E.add2(R.sim, C.stg[i], C.st[i], i);
    let s = R.store.get(o); if (!s) { s = E.newAcc(); R.store.set(o, s); } E.addRow(s, i);
    const m = C.mon[i], d = C.dom[i], g = C.ga[i];
    let md = R.monthDoc.get(m); if (!md) { md = new Map(); R.monthDoc.set(m, md); } md.set(C.doc[i], (md.get(C.doc[i]) || 0) + g);
    let ms = R.monthStg.get(m); if (!ms) { ms = new Map(); R.monthStg.set(m, ms); } ms.set(C.stg[i], (ms.get(C.stg[i]) || 0) + g);
    if (m === E.CUR_M) { R.dCurGA[d] += g; R.dCurM1[d] += C.m1[i]; }
    if (m === E.PREV_M) { R.dPrevGA[d] += g; R.dPrevM1[d] += C.m1[i]; }
    if (m === E.CUR_M || m === E.PREV_M) R.dow[(new Date(Math.floor(m / 100), m % 100 - 1, d).getDay() + 6) % 7] += g;
  }
  // owner targets (Target By AE Name / RTR Target By AE Name) for the selected channel(s), owner(s), target type(s)
  const owners = FL.sel.owner.size ? new Set([...FL.sel.owner].map(i => E.ownerNames[i])) : R.owners;
  R.target = E.ownerTarget(owners, FL.sel.tt, FL.selectedChannels());
  R.bands = E.bandTable(FL.ok(E.colB));
  R.market = E.marketShare(new Set(R.store.keys()));
  return R;
};
})();
