"""
Export the data for the HTML dashboards from the BMA_East database.

  python etl\\export_html.py

Writes data\\dashboard-data.js (format version 3: one row per OUTLET, 7-11 + RTR + Other channels).
Targets / game plans / close gap / market share are taken for the current month (the month of the
latest sales day); if a table has nothing for that month, its latest earlier month is used.
Prints counts only - never data values.
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bma_db  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANNELS = ["7-11", "RTR", "Other"]
BANDS = ["1-49", "50-99", "100-119", "120-149", "150-199", "200-249", "250-299", "300UP"]


class Dict:
    """value -> small integer, so repeated text is stored once"""
    def __init__(self, fixed=None):
        self.items, self.idx = [], {}
        for v in fixed or []:
            self(v)

    def __call__(self, v):
        v = "" if v is None else str(v)
        if v not in self.idx:
            self.idx[v] = len(self.items); self.items.append(v)
        return self.idx[v]


def month_for(cur, table, cur_month, extra_where=""):
    """current month if the table has rows for it, else its latest month before that"""
    r = cur.execute(f"SELECT MAX(month_key) FROM {table} WHERE month_key <= ? {extra_where}", cur_month).fetchone()
    return r[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default=bma_db.DEFAULT_SERVER)
    ap.add_argument("--db", default=bma_db.DEFAULT_DB)
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "dashboard-data.js"))
    a = ap.parse_args()
    conn = bma_db.connect(a.server, a.db)
    cur = conn.cursor()

    asof = cur.execute("SELECT as_of_key, current_month_key FROM rpt.AsOf").fetchone()
    if not asof:
        sys.exit("No sales data in the database yet - run load_sales.py first.")
    as_of_key, cur_month = asof

    # ---------- outlets ----------
    # [label(codes), name, owner, segment, status, lat, lng, zone, type_sim, priority, district, channel, in_profile, sub_district]
    outlets, oidx = [], {}
    rows = cur.execute("""
        SELECT o.outlet_id, o.outlet_name, ow.owner_name, o.segment, o.[status], o.latitude, o.longitude, o.zone_name,
               o.type_sim, o.ranking_priority, o.district_en, o.channel, o.in_profile, o.sub_district_en,
               STUFF((SELECT ' / ' + CAST(s.partner_code AS VARCHAR(12)) FROM dim.Store s WHERE s.outlet_id = o.outlet_id
                      ORDER BY s.partner_code FOR XML PATH('')), 1, 3, '') AS codes
        FROM dim.Outlet o LEFT JOIN dim.Owner ow ON ow.owner_id = o.owner_id
        ORDER BY o.outlet_id""").fetchall()
    for r in rows:
        oidx[r.outlet_id] = len(outlets)
        outlets.append([r.codes or "", r.outlet_name, r.owner_name or "", r.segment or "", r.status or "",
                        float(r.latitude) if r.latitude is not None else None, float(r.longitude) if r.longitude is not None else None,
                        r.zone_name or "", r.type_sim or "", r.ranking_priority or "", r.district_en or "", r.channel,
                        1 if r.in_profile else 0, r.sub_district_en or ""])

    # ---------- sales facts (two codes of one RTR shop are added together) ----------
    d = {k: Dict() for k in ["gs", "st", "stg", "doc", "dist", "sub", "comp"]}
    d["band"] = Dict(BANDS)
    common = """
        SELECT f.date_key, s.outlet_id, st.group_sim, f.sim_type, st.sim_type_group, f.doc_type,
               COALESCE(s.district_en, o.district_en) AS dist, COALESCE(s.sub_district_en, o.sub_district_en) AS sub,
               COALESCE(st.company, s.company) AS comp, {extra}
        FROM {table} f
        JOIN dim.Store s    ON s.partner_code = f.partner_code
        JOIN dim.Outlet o   ON o.outlet_id = s.outlet_id
        JOIN dim.SimType st ON st.sim_type = f.sim_type
        GROUP BY f.date_key, s.outlet_id, st.group_sim, f.sim_type, st.sim_type_group, f.doc_type,
                 COALESCE(s.district_en, o.district_en), COALESCE(s.sub_district_en, o.sub_district_en), COALESCE(st.company, s.company){gb}"""
    F = {k: [] for k in ["day", "store", "gs", "st", "stg", "doc", "dist", "sub", "comp", "ga", "m1", "ap1d"]}
    for r in cur.execute(common.format(table="fact.SalesDaily", gb="",
                                       extra="SUM(f.ga) AS ga, SUM(f.m1_amount) AS m1, SUM(COALESCE(f.ap1d, 0)) AS ap1d")):
        F["day"].append(r.date_key); F["store"].append(oidx[r.outlet_id])
        F["gs"].append(d["gs"](r.group_sim)); F["st"].append(d["st"](r.sim_type)); F["stg"].append(d["stg"](r.sim_type_group))
        F["doc"].append(d["doc"](r.doc_type)); F["dist"].append(d["dist"](r.dist)); F["sub"].append(d["sub"](r.sub))
        F["comp"].append(d["comp"](r.comp)); F["ga"].append(int(r.ga)); F["m1"].append(round(float(r.m1), 2)); F["ap1d"].append(int(r.ap1d))
    B = {k: [] for k in ["day", "store", "gs", "st", "stg", "doc", "dist", "sub", "comp", "band", "q"]}
    for r in cur.execute(common.format(table="fact.SalesAP1DBand", gb=", f.price_band", extra="f.price_band, SUM(f.ap1d) AS q")):
        B["day"].append(r.date_key); B["store"].append(oidx[r.outlet_id])
        B["gs"].append(d["gs"](r.group_sim)); B["st"].append(d["st"](r.sim_type)); B["stg"].append(d["stg"](r.sim_type_group))
        B["doc"].append(d["doc"](r.doc_type)); B["dist"].append(d["dist"](r.dist)); B["sub"].append(d["sub"](r.sub))
        B["comp"].append(d["comp"](r.comp)); B["band"].append(d["band"](r.price_band)); B["q"].append(int(r.q))

    # ---------- month-based tables ----------
    used = {}
    m = used["targets"] = month_for(cur, "fact.TargetOwner", cur_month)
    targets = [[r.owner_name, r.target_type, int(r.target_ga), float(r.target_m1), r.channel] for r in cur.execute(
        "SELECT ow.owner_name, t.target_type, t.target_ga, t.target_m1, t.channel FROM fact.TargetOwner t "
        "JOIN dim.Owner ow ON ow.owner_id = t.owner_id WHERE t.month_key = ?", m)] if m else []
    m = used["tat"] = month_for(cur, "fact.TargetStore", cur_month)
    tat = [[oidx[r.outlet_id], int(r.target_ga), float(r.target_m1), int(r.target_ga_daily), float(r.target_m1_daily)] for r in cur.execute(
        "SELECT outlet_id, target_ga, target_m1, target_ga_daily, target_m1_daily FROM fact.TargetStore WHERE month_key = ?", m)] if m else []
    gameplan = []
    for ch in ["7-11", "RTR"]:
        m = used[f"gameplan {ch}"] = month_for(cur, "fact.GamePlan g JOIN dim.GamePlan p ON p.game_plan_id = g.game_plan_id",
                                               cur_month, f"AND p.channel = '{ch}'")
        if m:
            gameplan += [[oidx[r.outlet_id], r.game_plan_name, int(r.commit_ga), int(r.commit_ga_daily or 0), r.channel, r.sim_rule,
                          r.start_date.isoformat() if r.start_date else "", r.end_date.isoformat() if r.end_date else ""]
                         for r in cur.execute("SELECT g.outlet_id, p.game_plan_name, g.commit_ga, g.commit_ga_daily, p.channel, p.sim_rule, "
                                              "g.start_date, g.end_date FROM fact.GamePlan g JOIN dim.GamePlan p ON p.game_plan_id = g.game_plan_id "
                                              "WHERE g.month_key = ? AND p.channel = ?", m, ch)]
    plans = [[r.game_plan_name, r.channel, r.sim_rule] for r in cur.execute("SELECT game_plan_name, channel, sim_rule FROM dim.GamePlan ORDER BY channel, game_plan_name")]
    m = used["closegap"] = month_for(cur, "fact.CloseGap", cur_month)
    closegap = [[oidx[r.outlet_id], int(r.commit_ga)] for r in cur.execute("SELECT outlet_id, commit_ga FROM fact.CloseGap WHERE month_key = ?", m)] if m else []
    market = [[oidx[r.outlet_id], float(r.share_true) if r.share_true is not None else None, r.market_size, r.ga_true, r.ga_ais, r.tumbon or ""]
              for r in cur.execute("SELECT outlet_id, share_true, market_size, ga_true, ga_ais, tumbon FROM rpt.OutletMarketShare WHERE channel = 'RTR'")]
    conn.close()

    data = {"version": 3, "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "asOfKey": as_of_key,
            "channels": CHANNELS, "dict": {k: v.items for k, v in d.items()}, "stores": outlets, "f": F, "b": B,
            "plans": plans, "targets": targets, "tat": tat, "gameplan": gameplan, "closegap": closegap, "market": market,
            "months": {k: v for k, v in used.items()}}
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(f"/* Generated {data['generated']} from SQL Server BMA_East. Contains store-level sales data - share with care. */\n")
        fh.write("window.DASH_DATA = ")
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")

    by_ch = {c: sum(1 for o in outlets if o[11] == c) for c in CHANNELS}
    print(f"Outlets      : {len(outlets):,}  ({', '.join(f'{k} {v}' for k, v in by_ch.items())})")
    print(f"Sales rows   : {len(F['day']):,}   AP1D band rows: {len(B['day']):,}")
    print(f"Targets      : owner {len(targets)} | shop {len(tat)}   Game plan rows {len(gameplan)}   Close gap {len(closegap)}   Market share {len(market)}")
    print(f"Months used  : " + ", ".join(f"{k}={v}" for k, v in used.items()))
    print(f"File         : {os.path.relpath(a.out, ROOT)}  ({os.path.getsize(a.out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
