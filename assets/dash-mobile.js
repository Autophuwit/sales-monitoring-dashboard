/* Mobile helpers: filter sheet, bottom tabs, expandable card lists. Requires dash-engine.js */
"use strict";
(function () {
if (!window.E || !E.ok) return;
const M = window.EM = {};
const chev = `<svg class="chev" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 3l5 5-5 5"/></svg>`;

M.pill = (p, k) => {
  if (p == null || !isFinite(p)) return "";
  const r = Math.round(p * 100);
  return `<span class="pill ${r > 0 ? "pos" : r < 0 ? "neg" : "zero"}">${k ? `<span class="k">${k}</span>` : ""}${r > 0 ? "+" : ""}${E.pctTxt(p)}</span>`;
};

/* filter sheet: chips row shows active filters; button opens the sheet */
M.filterSheet = (FL, chipsHost) => {
  const bg = document.createElement("div"); bg.className = "sheet-bg"; bg.hidden = true;
  const sh = document.createElement("div"); sh.className = "sheet"; sh.hidden = true;
  sh.innerHTML = `<div class="sheet-h"><b>Filters</b><button type="button">Done</button></div><div class="sheet-body"></div>`;
  document.body.append(bg, sh);
  const ctl = E.buildSlicers(sh.querySelector(".sheet-body"), FL, { noFocus: true });
  const open = v => { bg.hidden = sh.hidden = !v; document.body.style.overflow = v ? "hidden" : ""; };
  bg.addEventListener("click", () => open(false));
  sh.querySelector(".sheet-h button").addEventListener("click", () => open(false));
  return {
    refresh() {
      ctl.refresh();
      const act = FL.dims.filter(d => FL.sel[d.key].size);
      chipsHost.innerHTML = `<button class="chip filter-btn" type="button">Filters${act.length ? " · " + act.length : ""}</button>` +
        (act.length ? act : FL.dims.slice(0, 3)).map(d => {
          const s = FL.sel[d.key];
          const v = !s.size ? "All" : s.size === 1 ? ((d.opts().find(o => o.v === [...s][0]) || {}).label ?? [...s][0]) : s.size + " selected";
          return `<span class="chip">${E.esc(d.label)}: <b>${E.esc(v)}</b></span>`;
        }).join("") + E.crossChip(FL);
      chipsHost.querySelector(".filter-btn").onclick = () => open(true);
    }
  };
};

/* slicers shown on the page + a chips row in the header for the click selections */
M.inlineFilters = (FL, slicerHost, chipsHost) => {
  const ctl = E.buildSlicers(slicerHost, FL, { noFocus: true });
  return {
    refresh() {
      ctl.refresh();
      if (!chipsHost) return;
      const n = FL.dims.filter(d => FL.sel[d.key].size).length;
      chipsHost.innerHTML = `<button class="chip filter-btn" type="button">Filters${n ? " · " + n : ""}</button>` +
        (FL.sel.channel ? `<span class="chip"><b>${E.esc(E.channelLabel(FL))}</b></span>` : "") + E.crossChip(FL);
      chipsHost.querySelector(".filter-btn").onclick = () => window.scrollTo({ top: 0, behavior: "smooth" });
    }
  };
};

/* bottom tabs */
M.tabs = (names, onShow) => {
  const show = tab => {
    document.querySelectorAll(".view").forEach(v => v.hidden = v.id !== "v-" + tab);
    document.querySelectorAll("nav.tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.tab === tab));
    window.scrollTo(0, 0); history.replaceState(null, "", "#" + tab);
    if (onShow) onShow(tab);
  };
  document.querySelector("nav.tabs").addEventListener("click", e => { const b = e.target.closest("button"); if (b) show(b.dataset.tab); });
  const h = location.hash.slice(1);
  show(names.includes(h) ? h : names[0]);
};

/* expandable card list. cfg: { big(M,k,l), meta(M,k,l), pills(M,k,l), sort, keep, noKids, pick1(k), pick2(k) }
   tap a group = expand; tap a row without children = filter the page by it (pick1 / pick2) */
const open = new Set();
M.list = (el, map, name1, name2, cfg) => {
  const sort = cfg.sort || ((a, b) => a.M.diff - b.M.diff);
  const keep = cfg.keep || (x => x.M.cur || x.M.pre);
  const groups = [...map.entries()].map(([k, g]) => ({ k, name: name1(k), g, M: E.measures(g.acc) })).filter(keep).sort(sort);
  if (!groups.length) { el.innerHTML = `<div class="empty">No data for this selection</div>`; return; }
  const row = (x, l, cls, attrs, kidsFlag, pid) => `<div class="row ${cls}${E.pickCls(pid)}" ${attrs}${E.pickAttr(pid)}>
      <div class="nm">${kidsFlag ? chev : ""}<span>${E.esc(x.name)}</span></div>
      <div class="big">${cfg.big(x.M, x.k, l)}</div>
      <div class="meta">${cfg.meta(x.M, x.k, l)}</div>
      <div class="side">${cfg.pills(x.M, x.k, l)}</div></div>`;
  let h = ""; const tot = E.newAcc();
  groups.forEach(x => {
    const id = el.id + ":" + x.k, isOpen = open.has(id);
    const kids = cfg.noKids ? [] : [...x.g.kids.entries()].map(([k, a]) => ({ k, name: name2(k), M: E.measures(a) })).filter(keep).sort(sort);
    h += row(x, 1, kids.length ? "tap" : "", kids.length ? `data-g="${E.esc(id)}" aria-expanded="${isOpen}" role="button" tabindex="0"` : "", kids.length,
      !kids.length && cfg.pick1 ? cfg.pick1(x.k) : null);
    kids.forEach(c => h += row(c, 2, "child", `data-p="${E.esc(id)}" ${isOpen ? "" : "hidden"}`, 0, cfg.pick2 ? cfg.pick2(c.k, x.k) : null));
    E.merge(tot, x.g.acc);
  });
  h += row({ k: "__total", name: "Total", M: E.measures(tot) }, 0, "total", "");
  el.innerHTML = h;
};
const toggle = r => {
  const id = r.dataset.g, isOpen = r.getAttribute("aria-expanded") !== "true";
  isOpen ? open.add(id) : open.delete(id);
  r.setAttribute("aria-expanded", isOpen);
  r.parentElement.querySelectorAll(".row.child").forEach(c => { if (c.dataset.p === id) c.hidden = !isOpen; });
};
document.addEventListener("click", e => { const r = e.target.closest(".row.tap"); if (r) toggle(r); });
document.addEventListener("keydown", e => { const r = e.target.closest && e.target.closest(".row.tap"); if (r && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggle(r); } });

M.navIcons = {
  overview: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>`,
  daily: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>`,
  stores: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9l1.5-5h13L20 9M4 9v11h16V9M4 9h16M9 20v-6h6v6"/></svg>`,
  trends: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 8-8M15 7h6v6"/></svg>`,
  target: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5" fill="currentColor"/></svg>`,
  sim: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"><path d="M6 3h8l4 4v14H6z"/><rect x="9" y="11" width="6" height="6" rx="1"/></svg>`
};
M.nav = (items) => {
  const n = document.createElement("nav"); n.className = "tabs"; n.setAttribute("role", "tablist");
  n.innerHTML = items.map(([key, label, icon]) => `<button role="tab" data-tab="${key}" aria-selected="false">${M.navIcons[icon]}${label}</button>`).join("");
  document.body.append(n);
};
})();
