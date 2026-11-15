/* Unit tests for the metric engine (assets/dash-engine.js), with a tiny dataset whose answers are known.
   Run:  node --test tests/                                                                                  */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

/* ---- dataset: AS of 15 Jul 2026 -> current month 202607, run-rate day = 14 ----
   shop 0 (7-11, zone Z1): GA 1 every day 1-30 Jun and 1-14 Jul
   shop 1 (RTR)          : GA 2 on 1 Jul only
   shop 2 (7-11, zone Z1): GA 3 every day in June, nothing in July (-> alert)      */
function dataset() {
  const f = { day: [], store: [], gs: [], st: [], stg: [], doc: [], dist: [], sub: [], comp: [], ga: [], m1: [], ap1d: [] };
  const add = (day, store, ga, doc = 0) => {
    for (const [k, v] of Object.entries({ day, store, gs: 0, st: 0, stg: 0, doc, dist: 0, sub: store === 1 ? 1 : 0, comp: 0, ga, m1: ga, ap1d: 0 })) f[k].push(v);
  };
  for (let d = 1; d <= 30; d++) { add(20260600 + d, 0, 1); add(20260600 + d, 2, 3); }
  for (let d = 1; d <= 14; d++) add(20260700 + d, 0, 1, d <= 7 ? 0 : 1);
  add(20260701, 1, 2);
  // shops 3-6: steady 7-11 shops in the same zone (needed so the zone average / spread is meaningful)
  for (let s = 3; s <= 6; s++) { for (let d = 1; d <= 30; d++) add(20260600 + d, s, 1); for (let d = 1; d <= 14; d++) add(20260700 + d, s, 1); }
  const shop = (name, owner, seg, zone, ch) => ["", name, owner, seg, "", 13.7, 100.5, zone, "", "", "D", ch, 1, "SD"];
  return {
    version: 3, generated: "test", asOfKey: 20260715, channels: ["7-11", "RTR", "Other"],
    dict: { gs: ["Prepaid"], st: ["S1"], stg: ["Mass 350"], doc: ["Thai ID", "Passport"], dist: ["D"], sub: ["Sub A", "Sub B"], comp: ["True"], band: ["1-49"] },
    stores: [shop("Shop 0", "Owner A", "Office", "Z1", "7-11"), shop("Shop 1", "Owner B", "", "", "RTR"), shop("Shop 2", "Owner A", "Asia", "Z1", "7-11"),
             ...[3, 4, 5, 6].map(s => shop("Shop " + s, "Owner A", "Office", "Z1", "7-11"))],
    f, b: null,
    plans: [["3. Increase GA", "7-11", "ALL"]], gameplan: [[0, "3. Increase GA", 40, 2, "7-11", "ALL", "", ""]],
    targets: [["Owner A", "All", 62, 31, "7-11"], ["Owner B", "All", 10, 5, "RTR"]],
    tat: [], closegap: [], market: [[1, 0.6, 100, 60, 40, "T"]],
  };
}

function loadEngine() {
  const noop = () => {};
  const el = { textContent: "", style: {}, dataset: {} };
  const ctx = {
    console, Math, Date, Map, Set, Int8Array, Int32Array, Float64Array, Array, Object, String, Number, JSON, isFinite,
    document: { addEventListener: noop, getElementById: () => null, documentElement: { dataset: {} }, querySelector: () => null, createElement: () => el },
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
    localStorage: { getItem: () => null, setItem: noop }, navigator: {},
  };
  ctx.window = ctx;
  ctx.DASH_DATA = dataset();
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "..", "assets", "dash-engine.js"), "utf8"), ctx);
  return ctx.E;
}
const near = (a, b, msg) => assert.ok(Math.abs(a - b) < 1e-9, `${msg}: ${a} != ${b}`);
function acc(E, keep = () => true) {
  const a = E.newAcc();
  for (let i = 0; i < E.N; i++) if (keep(i)) E.addRow(a, i);
  return E.measures(a);
}

test("dates: current / previous month, run-rate day and current week", () => {
  const E = loadEngine();
  assert.equal(E.CUR_M, 202607); assert.equal(E.PREV_M, 202606);
  assert.equal(E.DAY_NO, 14);                       // AS of day - 1
  assert.equal(E.CUR_WEEK, 2);                      // days 8-14
});

test("run rate, MoM and diff for one shop", () => {
  const E = loadEngine();
  const M = acc(E, i => E.col.store[i] === 0);
  assert.equal(M.cur, 14); assert.equal(M.pre, 30);
  near(M.rr, 14 * 31 / 14, "GA RR = GA x 31 / day");
  near(M.mom, (31 - 30) / 30, "%MoM");
  near(M.diff, 1, "diff vs last month");
});

test("weeks: full weeks are 7-day values, week 1 WoW compares with last month's week 4", () => {
  const E = loadEngine();
  const M = acc(E, i => E.col.store[i] === 0);
  near(M.weekVal(1), 7, "week 1"); near(M.weekVal(2), 7, "current week, 7-day run rate");
  assert.equal(M.weekVal(3), null);                 // not started yet
  near(M.prevWeekVal(1), 7, "June week 4 (9 days) normalised to 7 days");
  near(M.wow(1), 0, "WoW week 1"); near(M.wow(2), 0, "WoW week 2");
});

test("no previous month -> %MoM is empty, not infinite", () => {
  const E = loadEngine();
  assert.equal(acc(E, i => E.col.store[i] === 1).mom, null);
});

test("a complete past month uses actual GA as run rate", () => {
  const E = loadEngine();
  E.setPeriod(202606);
  assert.equal(E.FULL_MONTH, true); assert.equal(E.DAY_NO, 30);
  const M = acc(E, i => E.col.store[i] === 0);
  assert.equal(M.cur, 30); near(M.rr, 30, "full month RR = actual");
  E.setPeriod(null);
  assert.equal(E.CUR_M, 202607);
});

test("slicer filter: channel", () => {
  const E = loadEngine();
  const FL = new E.Filters([E.DIM.channel()], () => {});
  FL.sel.channel.add(E.channels.indexOf("RTR"));
  assert.equal(acc(E, FL.ok()).cur, 2);
});

test("click filters: OR inside one visual, AND across visuals, plain click replaces", () => {
  const E = loadEngine();
  const FL = new E.Filters([E.DIM.channel()], () => {});
  const shop0 = E.pkStore("table", 0), shop1 = E.pkStore("table", 1), thai = E.pk("chart", "doc", 0, "Doc: Thai ID");
  FL.pick(shop0);                  assert.equal(acc(E, FL.ok()).cur, 14);
  FL.pick(shop1, true);            assert.equal(acc(E, FL.ok()).cur, 16);   // shop 0 OR shop 1
  FL.pick(thai, true);             assert.equal(acc(E, FL.ok()).cur, 9);    // (shop 0 or 1) AND Thai ID: 7 + 2
  FL.pick(shop1);                  assert.equal(acc(E, FL.ok()).cur, 2);    // plain click: only shop 1
  FL.pick(shop1);                  assert.equal(FL.xs.size, 0);             // click again: cleared
});

test("targets by owner and channel", () => {
  const E = loadEngine();
  assert.deepEqual({ ...E.ownerTarget(null, null, null) }, { ga: 72, m1: 36 });
  assert.deepEqual({ ...E.ownerTarget(new Set(["Owner A"]), null, new Set(["7-11"])) }, { ga: 62, m1: 31 });
});

test("market share: RTR average of %True, 7-11 counted as 100%", () => {
  const E = loadEngine();
  const m = E.marketShare(new Set([0, 1, 2]));
  near(m.rtrAvg, 0.6, "RTR average"); assert.equal(m.n711, 2); assert.equal(m.size, 100);
});

test("alert: shop whose %MoM is at or below zone average minus one standard deviation", () => {
  const E = loadEngine();
  assert.ok(E.alerts.has(2), "shop 2 (no sales this month) is flagged");
  assert.ok(!E.alerts.has(0), "shop 0 (growing) is not flagged");
});
