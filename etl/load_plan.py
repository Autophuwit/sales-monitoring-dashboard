"""
Load profiles, targets, game plans, close gap and market share for BOTH channels (7-11 and RTR).

  1. Download both Google Sheets as .xlsx (File -> Download -> Microsoft Excel) into
        BMA East Dashboard\\source\\google\\
     The script recognises them by their tabs:  'Store Profile' = 7-11 file,  'RTR Store Profile' = RTR file.
  2. python etl\\load_plan.py --month 202607

Tabs / files used
  7-11 file : Store Profile, Target By AE Name, Target All Type, Game Plan, Close Gap Store
  RTR  file : RTR Store Profile, Retail Name, RTR Target By AE Name, RTR Gameplan
  Market    : Desktop\\เอกสาร\\Battle field.xlsx, sheet 'Sheet5'   (--market to change, --no-market to skip)
Month-based tables (targets, game plans, close gap, market share) are replaced for --month, so re-running is safe.
Profiles always reflect the latest sheet. Names that cannot be matched go to logs\\unmatched_<month>.csv.
Prints counts only.
"""
import argparse
import glob
import os
import sys

import pandas as pd
from openpyxl import load_workbook

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bma_db  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIR = os.path.join(ROOT, "source", "google")
DEFAULT_MARKET = os.path.join(os.path.expanduser("~"), "OneDrive", "เดสก์ท็อป", "เอกสาร", "Battle field.xlsx")


# ----------------------------------------------------------------- cell helpers
def s(v):
    """text cell -> clean str or None"""
    if v is None:
        return None
    if not isinstance(v, str):
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(v, float) and v.is_integer():
            v = int(v)
    t = " ".join(str(v).split())
    return t or None


def num(v, nd=None):
    x = pd.to_numeric(v, errors="coerce")
    if pd.isna(x):
        return None
    return round(float(x), nd) if nd is not None else float(x)


def i(v):
    x = num(v)
    return None if x is None else int(round(x))


def code(v):
    x = i(v)
    return x if x is not None and 0 < x <= 2_147_483_647 else None


def day(v):
    x = pd.to_datetime(v, errors="coerce", dayfirst=True)
    return None if pd.isna(x) else x.date()


def share(v):
    """percentage cell -> fraction 0..1 (accepts 0.35 or 35)"""
    x = num(v)
    if x is None:
        return None
    return round(x / 100 if abs(x) > 1.5 else x, 6)


def need(df, tab, cols):
    miss = [c for c in cols if c not in df.columns]
    if miss:
        raise ValueError(f"tab '{tab}' is missing columns {miss}")


# ----------------------------------------------------------------- db helpers
class Ctx:
    def __init__(self, conn, month):
        self.conn, self.cur, self.month = conn, conn.cursor(), month
        self.unmatched = []

    def q(self, sql, *p):
        return self.cur.execute(sql, *p)

    def owner(self, name, channel):
        if not name:
            return None
        r = self.q("SELECT owner_id FROM dim.Owner WHERE owner_name = ?", name).fetchone()
        if r:
            self.q("UPDATE dim.Owner SET channel = COALESCE(channel, ?) WHERE owner_id = ?", channel, r[0])
            return r[0]
        return self.q("INSERT dim.Owner (owner_name, channel) OUTPUT INSERTED.owner_id VALUES (?, ?)", name, channel).fetchone()[0]

    def ensure_code(self, c, name, company=None):
        """make sure partner code c exists in dim.Store"""
        if not self.q("SELECT 1 FROM dim.Store WHERE partner_code = ?", c).fetchone():
            self.q("INSERT dim.Store (partner_code, partner_name, company) VALUES (?, ?, ?)", c, name or str(c), company)
        elif company:
            self.q("UPDATE dim.Store SET company = ? WHERE partner_code = ?", company, c)

    def outlet_of_code(self, c):
        r = self.q("SELECT s.outlet_id, o.channel FROM dim.Store s LEFT JOIN dim.Outlet o ON o.outlet_id = s.outlet_id "
                   "WHERE s.partner_code = ?", c).fetchone()
        return (r[0], r[1]) if r else (None, None)

    def upsert_outlet(self, outlet_id, key, channel, a):
        """a = dict of outlet attributes. Returns outlet_id."""
        cols = ["outlet_name", "owner_id", "status", "segment", "type_sim", "ranking_priority", "zone_name",
                "district_en", "sub_district_en", "latitude", "longitude"]
        vals = [a.get(c) for c in cols]
        if outlet_id is None:
            r = self.q("SELECT outlet_id FROM dim.Outlet WHERE outlet_key = ?", key).fetchone()
            outlet_id = r[0] if r else None
        if outlet_id is None:
            return self.q(f"INSERT dim.Outlet (channel, outlet_key, in_profile, {', '.join(cols)}) OUTPUT INSERTED.outlet_id "
                          f"VALUES (?, ?, 1, {', '.join('?' * len(cols))})", channel, key, *vals).fetchone()[0]
        sets = ", ".join(f"{c} = ?" for c in cols)
        self.q(f"UPDATE dim.Outlet SET channel = ?, in_profile = 1, {sets}, updated_at = SYSDATETIME() WHERE outlet_id = ?",
               channel, *vals, outlet_id)
        return outlet_id

    def link(self, c, outlet_id):
        self.q("UPDATE dim.Store SET outlet_id = ? WHERE partner_code = ?", outlet_id, c)

    def name_lookup(self, channel):
        """normalised name -> outlet_id, using the outlet name and the names the sales file uses for its codes"""
        lk = {}
        for oid, nm in self.q("SELECT s.outlet_id, s.source_partner_name FROM dim.Store s JOIN dim.Outlet o ON o.outlet_id = s.outlet_id "
                              "WHERE o.channel = ? AND s.source_partner_name IS NOT NULL", channel).fetchall():
            lk[bma_db.norm_name(nm)] = oid
        for oid, nm in self.q("SELECT outlet_id, outlet_name FROM dim.Outlet WHERE channel = ?", channel).fetchall():
            lk[bma_db.norm_name(nm)] = oid               # profile name wins
        return lk


# ----------------------------------------------------------------- 7-11
def store_profile_711(x, df):
    need(df, "Store Profile", ["PARTNER_CODE", "Partner Name", "Owner"])
    seen, bad = set(), 0
    for r in df.to_dict("records"):
        c = code(r.get("PARTNER_CODE"))
        if c is None:
            bad += 1; continue
        name = s(r.get("Partner Name")) or str(c)
        x.ensure_code(c, name)
        oid, ch = x.outlet_of_code(c)
        oid = oid if ch == "7-11" else None                       # was 'Other' -> give it a proper 7-11 outlet
        oid = x.upsert_outlet(oid, f"7-11:{c}", "7-11", {
            "outlet_name": name, "owner_id": x.owner(s(r.get("Owner")), "7-11"), "status": s(r.get("Status")),
            "segment": s(r.get("Segment")), "type_sim": s(r.get("Type Sim")), "ranking_priority": s(r.get("Ranking Priority")),
            "zone_name": s(r.get("Zone Name")), "district_en": s(r.get("District")),
            "latitude": num(r.get("LAT"), 6), "longitude": num(r.get("LONG"), 6)})
        x.link(c, oid); seen.add(oid)
    gone = mark_not_in_profile(x, "7-11", seen)
    return f"outlets {len(seen):,} | bad codes {bad} | no longer in profile {gone}"


def mark_not_in_profile(x, channel, seen):
    ids = x.q("SELECT outlet_id FROM dim.Outlet WHERE channel = ? AND in_profile = 1", channel).fetchall()
    gone = [r[0] for r in ids if r[0] not in seen]
    for oid in gone:
        x.q("UPDATE dim.Outlet SET in_profile = 0, owner_id = NULL WHERE outlet_id = ?", oid)
    return len(gone)


def target_owner(x, df, tab, channel):
    need(df, tab, ["Owner", "Target Type", "Target"])
    agg = {}
    for r in df.to_dict("records"):
        o, t = s(r.get("Owner")), s(r.get("Target Type"))
        if not o or not t:
            continue
        g, m = i(r.get("Target")) or 0, num(r.get("Target M1"), 2) or 0
        k = (x.owner(o, channel), t)
        agg[k] = (agg[k][0] + g, agg[k][1] + m) if k in agg else (g, m)
    x.q("DELETE FROM fact.TargetOwner WHERE month_key = ? AND channel = ?", x.month, channel)
    for (oid, t), (g, m) in agg.items():
        x.q("INSERT fact.TargetOwner (month_key, channel, owner_id, target_type, target_ga, target_m1) VALUES (?, ?, ?, ?, ?, ?)",
            x.month, channel, oid, t, g, m)
    return f"rows {len(agg)}"


def target_store_711(x, df):
    need(df, "Target All Type", ["PARTNER_CODE", "Target GA"])
    agg, bad, added = {}, 0, 0
    for r in df.to_dict("records"):
        c = code(r.get("PARTNER_CODE"))
        if c is None:
            bad += 1; continue
        oid, _ = x.outlet_of_code(c)
        if oid is None:                                   # target for a shop not in Store Profile yet
            x.ensure_code(c, s(r.get("Partner Name")))
            oid = x.q("INSERT dim.Outlet (channel, outlet_key, outlet_name) OUTPUT INSERTED.outlet_id VALUES ('7-11', ?, ?)",
                      f"7-11:{c}", s(r.get("Partner Name")) or str(c)).fetchone()[0]
            x.link(c, oid); added += 1
        g, m = i(r.get("Target GA")) or 0, num(r.get("Target M1"), 2) or 0
        agg[oid] = (agg[oid][0] + g, agg[oid][1] + m) if oid in agg else (g, m)
    x.q("DELETE FROM fact.TargetStore WHERE month_key = ?", x.month)
    for oid, (g, m) in agg.items():
        x.q("INSERT fact.TargetStore (month_key, outlet_id, target_ga, target_m1) VALUES (?, ?, ?, ?)", x.month, oid, g, m)
    return f"rows {len(agg)} | bad codes {bad} | outlets added {added}"


def game_plan(x, df, tab, channel, name_col, plan_col, commit_col, daily_col):
    need(df, tab, [name_col, plan_col, commit_col])
    lk = x.name_lookup(channel)
    plans = {bma_db.norm_name(n): pid for pid, n in x.q("SELECT game_plan_id, game_plan_name FROM dim.GamePlan WHERE channel = ?", channel).fetchall()}
    agg, new_plans = {}, 0
    for r in df.to_dict("records"):
        nm, gp = s(r.get(name_col)), s(r.get(plan_col))
        if not nm or not gp:
            continue
        pid = plans.get(bma_db.norm_name(gp))
        if pid is None and channel == "RTR":              # RTR plans are free text in the sheet -> add them
            pid = x.q("INSERT dim.GamePlan (channel, game_plan_name, sim_rule) OUTPUT INSERTED.game_plan_id VALUES ('RTR', ?, 'ALL')", gp).fetchone()[0]
            plans[bma_db.norm_name(gp)] = pid; new_plans += 1
        oid = lk.get(bma_db.norm_name(nm))
        if oid is None or pid is None:
            x.unmatched.append((tab, nm, gp, "shop not found" if oid is None else "game plan name not found")); continue
        cm, cd = i(r.get(commit_col)) or 0, i(r.get(daily_col))
        k = (oid, pid)
        if k in agg:
            o = agg[k]; agg[k] = (o[0] + cm, (o[1] or 0) + (cd or 0), o[2], o[3])
        else:
            agg[k] = (cm, cd, day(r.get("Start Date")), day(r.get("End Date")))
    x.q("DELETE g FROM fact.GamePlan g JOIN dim.GamePlan p ON p.game_plan_id = g.game_plan_id WHERE g.month_key = ? AND p.channel = ?", x.month, channel)
    for (oid, pid), v in agg.items():
        x.q("INSERT fact.GamePlan (month_key, outlet_id, game_plan_id, commit_ga, commit_ga_daily, start_date, end_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
            x.month, oid, pid, *v)
    nu = sum(1 for u in x.unmatched if u[0] == tab)
    return f"rows {len(agg)} | unmatched {nu}" + (f" | new RTR plans {new_plans}" if new_plans else "")


def close_gap_711(x, df):
    need(df, "Close Gap Store", ["PARTNER_NAME", "Commit GA"])
    lk = x.name_lookup("7-11")
    agg = {}
    for r in df.to_dict("records"):
        nm = s(r.get("PARTNER_NAME"))
        if not nm:
            continue
        oid = lk.get(bma_db.norm_name(nm))
        if oid is None:
            x.unmatched.append(("Close Gap Store", nm, s(r.get("SUB_DISTRICT")), "shop not found")); continue
        agg[oid] = agg.get(oid, 0) + (i(r.get("Commit GA")) or 0)
    x.q("DELETE FROM fact.CloseGap WHERE month_key = ?", x.month)
    for oid, cm in agg.items():
        x.q("INSERT fact.CloseGap (month_key, outlet_id, commit_ga) VALUES (?, ?, ?)", x.month, oid, cm)
    return f"rows {len(agg)} | unmatched {sum(1 for u in x.unmatched if u[0] == 'Close Gap Store')}"


# ----------------------------------------------------------------- RTR
def rtr_attrs(x, r, name):
    return {"outlet_name": name, "owner_id": x.owner(s(r.get("Owner")), "RTR"), "status": s(r.get("Status")),
            "type_sim": s(r.get("TARGET")), "district_en": s(r.get("DISTRICT_EN")), "sub_district_en": s(r.get("SUB_DISTRICT_EN")),
            "latitude": num(r.get("Lat"), 6), "longitude": num(r.get("Long"), 6)}


def rtr_outlet_for_codes(x, codes):
    for c, _ in codes:
        oid, ch = x.outlet_of_code(c)
        if oid is not None and ch == "RTR":
            return oid
    return None


def store_profile_rtr(x, df):
    need(df, "RTR Store Profile", ["RTR Code-Dtac", "RTR Code-True", "Retailer Name"])
    seen, nocode, two = set(), 0, 0
    for r in df.to_dict("records"):
        codes = [(c, comp) for c, comp in ((code(r.get("RTR Code-True")), "TRUE"), (code(r.get("RTR Code-Dtac")), "DTAC")) if c]
        if not codes:
            nocode += 1; continue
        two += len(codes) == 2
        name = s(r.get("Retailer Name")) or str(codes[0][0])
        for c, comp in codes:
            x.ensure_code(c, name, comp)
        oid = x.upsert_outlet(rtr_outlet_for_codes(x, codes), f"RTR:{codes[0][0]}", "RTR", rtr_attrs(x, r, name))
        for c, _ in codes:
            x.link(c, oid)
        seen.add(oid)
    x.rtr_seen = seen                      # 'Retail Name' may add more; marking happens in finish_rtr()
    return f"outlets {len(seen):,} (with 2 codes {two}) | rows without code {nocode}"


def finish_rtr(x):
    gone = mark_not_in_profile(x, "RTR", getattr(x, "rtr_seen", set()))
    return f"RTR outlets no longer in the sheets {gone}"


def retail_name_rtr(x, df):
    """'Retail Name' maps extra RTR codes to a retailer; link any code the profile did not cover."""
    need(df, "Retail Name", ["RTR Code", "Retailer Name"])
    lk = x.name_lookup("RTR")
    linked = created = 0
    for r in df.to_dict("records"):
        c, name = code(r.get("RTR Code")), s(r.get("Retailer Name"))
        if c is None or not name:
            continue
        oid, ch = x.outlet_of_code(c)
        seen = getattr(x, "rtr_seen", set())
        if ch == "RTR":
            if oid not in seen:                            # outlet only known from this tab: keep it current
                x.upsert_outlet(oid, f"RTR:{c}", "RTR", rtr_attrs(x, r, name))
                seen.add(oid); x.rtr_seen = seen
            continue
        x.ensure_code(c, name)
        oid = lk.get(bma_db.norm_name(name))
        if oid is None:
            oid = x.upsert_outlet(None, f"RTR:{c}", "RTR", rtr_attrs(x, r, name)); created += 1
            lk[bma_db.norm_name(name)] = oid
        x.link(c, oid); linked += 1
        seen.add(oid); x.rtr_seen = seen
    return f"extra codes linked {linked} | outlets created {created}"


# ----------------------------------------------------------------- market share
def market_share(x, df):
    need(df, "Sheet5", ["RTR Code", "RTR NAME", "%True"])
    lk = x.name_lookup("RTR")
    agg = {}
    for r in df.to_dict("records"):
        c, nm = code(r.get("RTR Code")), s(r.get("RTR NAME"))
        oid = x.outlet_of_code(c)[0] if c else None
        if oid is None and nm:
            oid = lk.get(bma_db.norm_name(nm))
        if oid is None:
            if c or nm:
                x.unmatched.append(("Market Share", nm or str(c), s(r.get("TUMBON")), "shop not found"))
            continue
        row = [s(r.get("TUMBON")), s(r.get("Key location")), i(r.get("GA M+M T")), i(r.get("GA Ais")), i(r.get("Market Size")),
               i(r.get("Total Sale")), share(r.get("%True")), share(r.get("%AIS")), share(r.get("Visibility Share")), share(r.get("Shelf Share"))]
        if oid in agg:                                  # same shop listed twice (True + Dtac code): add volumes, average shares
            o = agg[oid]
            agg[oid] = [o[0] or row[0], o[1] or row[1]] + [(a or 0) + (b or 0) if (a is not None or b is not None) else None for a, b in zip(o[2:6], row[2:6])] \
                       + [((a + b) / 2 if a is not None and b is not None else (a if a is not None else b)) for a, b in zip(o[6:], row[6:])]
        else:
            agg[oid] = row
    x.q("DELETE FROM fact.MarketShare WHERE month_key = ?", x.month)
    for oid, v in agg.items():
        x.q("INSERT fact.MarketShare (month_key, outlet_id, tumbon, key_location, ga_true, ga_ais, market_size, total_sale, "
            "share_true, share_ais, visibility_share, shelf_share) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", x.month, oid, *v)
    return f"rows {len(agg)} | unmatched {sum(1 for u in x.unmatched if u[0] == 'Market Share')}"


def cleanup(x):
    """drop placeholder outlets that no code points to any more and nothing references"""
    x.q("""DELETE o FROM dim.Outlet o
           WHERE o.in_profile = 0
             AND NOT EXISTS (SELECT 1 FROM dim.Store s WHERE s.outlet_id = o.outlet_id)
             AND NOT EXISTS (SELECT 1 FROM fact.TargetStore t WHERE t.outlet_id = o.outlet_id)
             AND NOT EXISTS (SELECT 1 FROM fact.GamePlan g WHERE g.outlet_id = o.outlet_id)
             AND NOT EXISTS (SELECT 1 FROM fact.CloseGap c WHERE c.outlet_id = o.outlet_id)
             AND NOT EXISTS (SELECT 1 FROM fact.MarketShare m WHERE m.outlet_id = o.outlet_id)""")
    return x.cur.rowcount


# ----------------------------------------------------------------- main
def find_files(folder):
    found = {}
    for f in sorted(glob.glob(os.path.join(folder, "*.xlsx")), key=os.path.getmtime):
        if os.path.basename(f).startswith("~$"):
            continue
        tabs = {t.strip() for t in load_workbook(f, read_only=True).sheetnames}
        if "RTR Store Profile" in tabs:
            found["rtr"] = f
        elif "Store Profile" in tabs:
            found["711"] = f
    return found


def read_tabs(path, wanted):
    out = {}
    if not path:
        return out
    book = pd.read_excel(path, sheet_name=None, dtype=object)
    for k, df in book.items():
        if k.strip() in wanted:
            df.columns = [" ".join(str(c).split()) for c in df.columns]
            out[k.strip()] = df.dropna(how="all")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", required=True, type=int, help="yyyymm the targets / plans / market share belong to")
    ap.add_argument("--file711", help="7-11 Google Sheet .xlsx (default: found in source\\google)")
    ap.add_argument("--filertr", help="RTR Google Sheet .xlsx (default: found in source\\google)")
    ap.add_argument("--market", default=DEFAULT_MARKET, help="Battle field.xlsx")
    ap.add_argument("--market-sheet", default="Sheet5")
    ap.add_argument("--no-market", action="store_true")
    ap.add_argument("--server", default=bma_db.DEFAULT_SERVER)
    ap.add_argument("--db", default=bma_db.DEFAULT_DB)
    a = ap.parse_args()
    if not (200001 <= a.month <= 209912 and 1 <= a.month % 100 <= 12):
        sys.exit("--month must look like 202607")

    found = find_files(DEFAULT_DIR) if os.path.isdir(DEFAULT_DIR) else {}
    f711, frtr = a.file711 or found.get("711"), a.filertr or found.get("rtr")
    fmkt = None if a.no_market else (a.market if os.path.exists(a.market) else None)
    print(f"Month      : {a.month}\n7-11 file  : {os.path.basename(f711) if f711 else '-- not found --'}\n"
          f"RTR file   : {os.path.basename(frtr) if frtr else '-- not found --'}\nMarket file: {os.path.basename(fmkt) if fmkt else '-- skipped --'}\n")
    if not f711 and not frtr:
        sys.exit(f"No Google Sheet .xlsx found in {DEFAULT_DIR}")

    t711 = read_tabs(f711, {"Store Profile", "Target By AE Name", "Target All Type", "Game Plan", "Close Gap Store"})
    trtr = read_tabs(frtr, {"RTR Store Profile", "Retail Name", "RTR Target By AE Name", "RTR Gameplan"})
    tmkt = read_tabs(fmkt, {a.market_sheet})

    conn = bma_db.connect(a.server, a.db)
    x = Ctx(conn, a.month)
    steps = [  # (tab, source, target table, function) - profiles first, then things that refer to shops
        ("Store Profile", t711, "dim.Outlet (7-11)", lambda df: store_profile_711(x, df)),
        ("RTR Store Profile", trtr, "dim.Outlet (RTR)", lambda df: store_profile_rtr(x, df)),
        ("Retail Name", trtr, "dim.Store (RTR codes)", lambda df: retail_name_rtr(x, df)),
        ("Target By AE Name", t711, "fact.TargetOwner", lambda df: target_owner(x, df, "Target By AE Name", "7-11")),
        ("RTR Target By AE Name", trtr, "fact.TargetOwner", lambda df: target_owner(x, df, "RTR Target By AE Name", "RTR")),
        ("Target All Type", t711, "fact.TargetStore", lambda df: target_store_711(x, df)),
        ("Game Plan", t711, "fact.GamePlan", lambda df: game_plan(x, df, "Game Plan", "7-11", "Partner Name", "Game Plan", "Commit GA", "Commit GA Daily")),
        ("RTR Gameplan", trtr, "fact.GamePlan", lambda df: game_plan(x, df, "RTR Gameplan", "RTR", "RTR NAME", "Gameplan", "Commit", "Commit Daily")),
        ("Close Gap Store", t711, "fact.CloseGap", lambda df: close_gap_711(x, df)),
        (a.market_sheet, tmkt, "fact.MarketShare", lambda df: market_share(x, df)),
    ]
    for tab, src, table, fn in steps:
        if tab not in src:
            if src:
                print(f"{tab:22}: tab not found - skipped")
            continue
        load_id = bma_db.log_start(conn, table, tab, a.month)
        try:
            msg = fn(src[tab])
            conn.commit()
            bma_db.log_end(conn, load_id, "success", len(src[tab]), msg)
            print(f"{tab:22}: OK  {msg}")
        except Exception as e:
            bma_db.log_end(conn, load_id, "failed", None, str(e))
            print(f"{tab:22}: FAILED - {e}")
    if "RTR Store Profile" in trtr:
        print(f"{'(RTR profile check)':22}: {finish_rtr(x)}")
        conn.commit()
    removed = cleanup(x); conn.commit()
    if removed:
        print(f"\nRemoved {removed} placeholder outlet(s) that are now covered by a profile.")
    if x.unmatched:
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        out = os.path.join(ROOT, "logs", f"unmatched_{a.month}.csv")
        pd.DataFrame(x.unmatched, columns=["tab", "name_in_sheet", "detail", "reason"]).to_csv(out, index=False, encoding="utf-8-sig")
        print(f"\n{len(x.unmatched)} row(s) could not be matched -> {out}")
    conn.close()


if __name__ == "__main__":
    main()
