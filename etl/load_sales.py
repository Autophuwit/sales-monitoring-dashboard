"""
Load the monthly sales txt files (REPORT_CS_TRUE_DTAC_yyyymm.txt) into BMA_East.

  python etl\\load_sales.py                 # load new / changed files from the 7-11 folder
  python etl\\load_sales.py --force         # reload every file
  python etl\\load_sales.py --only 202607   # just one month
  python etl\\load_sales.py --only 202607 --allow-drop   # accept a much smaller file than what is loaded

What it does for each file
  1. reads it in chunks (pipe-delimited, Thai Windows-874), by column NAME
  2. keeps only rows of the TDS_PROVINCE below
  3. sums GA / M1 / AP1D per day x store x SIM type x doc type
  4. checks it is safe to replace the month (not empty, not much smaller than what is loaded) - else stops, nothing deleted
  5. adds new stores / SIM types / doc types to the dim tables
  6. replaces that month in fact.SalesDaily (delete + insert, one transaction)
Prints counts only - never data values. Exits 1 if any file failed, so update.ps1 / the scheduler stops.
"""
import argparse
import csv
import glob
import os
import re
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bma_db  # noqa: E402

DEFAULT_FOLDER = os.path.join(os.path.expanduser("~"), "OneDrive", "เดสก์ท็อป", "7-11")
PROVINCE = "BKK : Phra Nakhon, Dusit, Bang Rak, Sathon, Pom Prap Sattru Phai, Samphanthawong, Yan Nawa, Bang Kho Laem"
BANDS = ["1-49", "50-99", "100-119", "120-149", "150-199", "200-249", "250-299", "300UP"]
BAND_COLS = [f"AP1D_{b}" for b in BANDS] + [f"AP1D_AMT_{b}" for b in BANDS]
NEEDED = ["TM_KEY_DAY", "COMPANY", "SIM_TYPE", "GROUP_SIM", "PARTNER_CODE", "PARTNER_NAME", "TDS_PROVINCE", "TDS_CHANNEL",
          "DISTRICT_EN", "SUB_DISTRICT_EN", "GA", "AP1D", "AP1D_AMT", "REV_AMT", "DOC_TYP_GP"] + BAND_COLS
KEYS = ["TM_KEY_DAY", "PARTNER_CODE", "SIM_TYPE", "DOC_TYP_GP"]
NUMS = ["GA", "REV_AMT", "AP1D", "AP1D_AMT"] + BAND_COLS
TARGET_TYPE = {"MASS": "Mass&Migrant", "MIGRANT": "Mass&Migrant", "TOURIST": "Tourist"}
MIN_KEEP_RATIO = 0.5   # a new file with < 50% of the fact rows already loaded for the month is treated as incomplete


def read_file(path, province):
    """Returns (aggregated fact rows, store attrs, sim attrs, stats)."""
    with open(path, encoding="cp874", errors="replace") as fh:
        header = fh.readline().rstrip("\r\n").split("|")
    missing = [c for c in NEEDED if c not in header]
    if missing:
        raise ValueError(f"columns missing in file: {missing}")

    stats = {"rows_read": 0, "rows_kept": 0, "bad_code": 0}
    parts, stores, sims = [], [], []
    reader = pd.read_csv(path, sep="|", encoding="cp874", encoding_errors="replace", dtype=str,
                         usecols=NEEDED, quoting=csv.QUOTE_NONE, keep_default_na=False, na_filter=False,
                         chunksize=400_000, on_bad_lines="skip", low_memory=False)
    for ch in reader:
        stats["rows_read"] += len(ch)
        ch = ch[ch["TDS_PROVINCE"].str.strip() == province]
        if ch.empty:
            continue
        ch = ch.copy()
        for c in ["SIM_TYPE", "PARTNER_NAME", "DOC_TYP_GP", "GROUP_SIM", "COMPANY", "DISTRICT_EN", "SUB_DISTRICT_EN", "TDS_CHANNEL"]:
            ch[c] = ch[c].str.strip()
        ch["PARTNER_CODE"] = pd.to_numeric(ch["PARTNER_CODE"], errors="coerce")
        ch["TM_KEY_DAY"] = pd.to_numeric(ch["TM_KEY_DAY"], errors="coerce")
        bad = ch["PARTNER_CODE"].isna() | ch["TM_KEY_DAY"].isna() | (ch["PARTNER_CODE"] > 2_147_483_647)
        stats["bad_code"] += int(bad.sum())
        ch = ch[~bad]
        ch["PARTNER_CODE"] = ch["PARTNER_CODE"].astype("int64")
        ch["TM_KEY_DAY"] = ch["TM_KEY_DAY"].astype("int64")
        for c in NUMS:
            ch[c] = pd.to_numeric(ch[c], errors="coerce").fillna(0)
        stats["rows_kept"] += len(ch)
        parts.append(ch.groupby(KEYS, as_index=False)[NUMS].sum())
        stores.append(ch[["PARTNER_CODE", "PARTNER_NAME", "DISTRICT_EN", "SUB_DISTRICT_EN", "TDS_CHANNEL"]].drop_duplicates("PARTNER_CODE"))
        sims.append(ch[["SIM_TYPE", "GROUP_SIM", "COMPANY"]].drop_duplicates("SIM_TYPE"))

    if not parts:
        return pd.DataFrame(columns=KEYS + NUMS), pd.DataFrame(), pd.DataFrame(), stats
    fact = pd.concat(parts).groupby(KEYS, as_index=False)[NUMS].sum()
    store = pd.concat(stores).drop_duplicates("PARTNER_CODE")
    sim = pd.concat(sims).drop_duplicates("SIM_TYPE")
    return fact, store, sim, stats


def check_before_replace(month, fact, stats, loaded_rows, allow_drop=False):
    """Raise before anything is deleted if the new data would wipe or shrink the month.
    loaded_rows = fact.SalesDaily rows already in the database for the month."""
    if stats["rows_kept"] == 0 or len(fact) == 0:
        raise ValueError(f"no rows for {month} matched the province filter - month NOT replaced "
                         f"(check the TDS_PROVINCE text); {loaded_rows:,} loaded rows kept")
    if loaded_rows and not allow_drop and len(fact) < MIN_KEEP_RATIO * loaded_rows:
        raise ValueError(f"only {len(fact):,} fact rows vs {loaded_rows:,} already loaded for {month} "
                         f"(< {MIN_KEEP_RATIO:.0%}) - looks like a partial file, month NOT replaced; "
                         f"re-run with --allow-drop if this is expected")


def loaded_rows(conn, month):
    lo, hi = month * 100 + 1, month * 100 + 31
    return conn.cursor().execute("SELECT COUNT(*) FROM fact.SalesDaily WHERE date_key BETWEEN ? AND ?", lo, hi).fetchone()[0]


def write_month(conn, month, fact, store, sim, load_id):
    cur = conn.cursor()
    # --- doc types
    bma_db.temp_table(cur, "doc", "doc_type NVARCHAR(30)", [(d,) for d in sorted(fact["DOC_TYP_GP"].unique())])
    cur.execute("INSERT dim.DocType (doc_type, is_thai_id) SELECT d.doc_type, CASE WHEN d.doc_type = N'Thai ID' THEN 1 ELSE 0 END "
                "FROM #doc d WHERE NOT EXISTS (SELECT 1 FROM dim.DocType x WHERE x.doc_type = d.doc_type)")
    new_doc = cur.rowcount
    # --- SIM types (group from etl.SimTypeGroupMap, else 'Other')
    bma_db.temp_table(cur, "sim", "sim_type NVARCHAR(100), group_sim NVARCHAR(20), company NVARCHAR(20), target_type NVARCHAR(30)",
                      [(r.SIM_TYPE, r.GROUP_SIM or "", r.COMPANY or None, TARGET_TYPE.get(r.GROUP_SIM)) for r in sim.itertuples()])
    cur.execute("""
        INSERT dim.SimType (sim_type, sim_type_group, group_sim, target_type, company, is_gp_350, is_gp_199)
        SELECT s.sim_type, g.grp, s.group_sim, s.target_type, s.company,
               CASE WHEN g.grp = N'Mass 350' THEN 1 ELSE 0 END, CASE WHEN g.grp = N'Mass 199' THEN 1 ELSE 0 END
        FROM #sim s
        LEFT JOIN etl.SimTypeGroupMap m ON m.sim_type = s.sim_type
        CROSS APPLY (SELECT COALESCE(m.sim_type_group, N'Other') AS grp) g
        WHERE NOT EXISTS (SELECT 1 FROM dim.SimType x WHERE x.sim_type = s.sim_type)""")
    new_sim = cur.rowcount
    # --- stores (partner codes): add unknown ones, keep sales-file name + area up to date
    bma_db.temp_table(cur, "st", "partner_code INT, partner_name NVARCHAR(200), district_en NVARCHAR(100), sub_district_en NVARCHAR(100), "
                      "sales_channel NVARCHAR(50)",
                      [(int(r.PARTNER_CODE), r.PARTNER_NAME or str(r.PARTNER_CODE), r.DISTRICT_EN or None, r.SUB_DISTRICT_EN or None,
                        r.TDS_CHANNEL or None) for r in store.itertuples()])
    cur.execute("""
        INSERT dim.Store (partner_code, partner_name, source_partner_name, district_en, sub_district_en, sales_channel)
        SELECT s.partner_code, s.partner_name, s.partner_name, s.district_en, s.sub_district_en, s.sales_channel
        FROM #st s WHERE NOT EXISTS (SELECT 1 FROM dim.Store x WHERE x.partner_code = s.partner_code)""")
    new_store = cur.rowcount
    cur.execute("""
        UPDATE x SET source_partner_name = s.partner_name,
                     district_en     = COALESCE(s.district_en, x.district_en),
                     sub_district_en = COALESCE(s.sub_district_en, x.sub_district_en),
                     sales_channel   = COALESCE(s.sales_channel, x.sales_channel),
                     updated_at = SYSDATETIME()
        FROM dim.Store x JOIN #st s ON s.partner_code = x.partner_code""")
    # --- codes not yet in any profile get their own outlet (7-11 if the sales file says so, else Other).
    #     load_plan.py moves them to the right 7-11 / RTR outlet when the profile sheets list them.
    cur.execute("""
        INSERT dim.Outlet (channel, outlet_key, outlet_name, district_en, sub_district_en)
        SELECT c.ch, c.ch + ':' + CAST(s.partner_code AS VARCHAR(12)), s.partner_name, s.district_en, s.sub_district_en
        FROM dim.Store s JOIN #st t ON t.partner_code = s.partner_code
        CROSS APPLY (SELECT CASE WHEN s.sales_channel = N'7-11' THEN '7-11' ELSE 'Other' END AS ch) c
        WHERE s.outlet_id IS NULL
          AND NOT EXISTS (SELECT 1 FROM dim.Outlet o WHERE o.outlet_key = c.ch + ':' + CAST(s.partner_code AS VARCHAR(12)))""")
    cur.execute("""
        UPDATE s SET outlet_id = o.outlet_id
        FROM dim.Store s JOIN #st t ON t.partner_code = s.partner_code
        JOIN dim.Outlet o ON o.outlet_key = CASE WHEN s.sales_channel = N'7-11' THEN '7-11' ELSE 'Other' END + ':' + CAST(s.partner_code AS VARCHAR(12))
        WHERE s.outlet_id IS NULL""")
    # --- facts: replace the month
    lo, hi = month * 100 + 1, month * 100 + 31
    cur.execute("DELETE FROM fact.SalesAP1DBand WHERE date_key BETWEEN ? AND ?", lo, hi)
    cur.execute("DELETE FROM fact.SalesDaily WHERE date_key BETWEEN ? AND ?", lo, hi)
    deleted = cur.rowcount
    rows = [(int(r.TM_KEY_DAY), int(r.PARTNER_CODE), r.SIM_TYPE, r.DOC_TYP_GP, int(round(r.GA)),
             round(float(r.REV_AMT), 2), int(round(r.AP1D)), round(float(r.AP1D_AMT), 2), load_id)
            for r in fact[KEYS + ["GA", "REV_AMT", "AP1D", "AP1D_AMT"]].itertuples(index=False, name="R")]
    # AP1D by price band: long format, only non-zero rows
    band_rows = []
    for rec in fact[KEYS + BAND_COLS].to_dict("records"):
        for b in BANDS:
            q, amt = rec[f"AP1D_{b}"], rec[f"AP1D_AMT_{b}"]
            if q or amt:
                band_rows.append((int(rec["TM_KEY_DAY"]), int(rec["PARTNER_CODE"]), rec["SIM_TYPE"], rec["DOC_TYP_GP"], b,
                                  int(round(q)), round(float(amt), 2), load_id))
    cur.fast_executemany = True
    if rows:
        cur.executemany("INSERT fact.SalesDaily (date_key, partner_code, sim_type, doc_type, ga, m1_amount, ap1d, ap1d_amount, load_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    if band_rows:
        cur.executemany("INSERT fact.SalesAP1DBand (date_key, partner_code, sim_type, doc_type, price_band, ap1d, ap1d_amount, load_id) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", band_rows)
    cur.fast_executemany = False
    conn.commit()
    return {"new_doc_types": new_doc, "new_sim_types": new_sim, "new_stores": new_store, "replaced_rows": deleted,
            "inserted_rows": len(rows), "band_rows": len(band_rows)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder", default=DEFAULT_FOLDER)
    ap.add_argument("--server", default=bma_db.DEFAULT_SERVER)
    ap.add_argument("--db", default=bma_db.DEFAULT_DB)
    ap.add_argument("--province", default=PROVINCE)
    ap.add_argument("--only", help="yyyymm - load just this month")
    ap.add_argument("--force", action="store_true", help="reload even if the file is unchanged")
    ap.add_argument("--allow-drop", action="store_true", help=f"replace the month even if the file has < {MIN_KEEP_RATIO:.0%} of the loaded rows")
    a = ap.parse_args()

    files = sorted(glob.glob(os.path.join(a.folder, "REPORT_CS_TRUE_DTAC_*.txt")))
    if a.only:
        files = [f for f in files if a.only in os.path.basename(f)]
    if not files:
        sys.exit(f"No REPORT_CS_TRUE_DTAC_*.txt files found in {a.folder}")
    conn = bma_db.connect(a.server, a.db)
    print(f"Province filter: {a.province}\n")
    failed = []
    for path in files:
        name = os.path.basename(path)
        m = re.search(r"(\d{6})", name)
        if not m:
            print(f"{name}: skipped (no yyyymm in file name)"); continue
        month, size, mod = int(m.group(1)), os.path.getsize(path), bma_db.mtime(path)
        if not a.force:
            done = conn.cursor().execute(
                "SELECT COUNT(*) FROM etl.LoadLog WHERE target_table = 'fact.SalesDaily' AND source_file = ? "
                "AND [status] = 'success' AND file_size = ? AND file_modified = ?", name, size, mod).fetchone()[0]
            if done:
                print(f"{name}: unchanged since last load - skipped"); continue
        t0 = time.time()
        load_id = bma_db.log_start(conn, "fact.SalesDaily", name, month, size, mod)
        try:
            fact, store, sim, st = read_file(path, a.province)
            # rows dated in another month would escape this month's delete and be duplicated on a re-run
            in_month = (fact["TM_KEY_DAY"] // 100) == month
            other_month = int((~in_month).sum())
            fact = fact[in_month]
            check_before_replace(month, fact, st, loaded_rows(conn, month), a.allow_drop)
            res = write_month(conn, month, fact, store, sim, load_id)
            msg = (f"read {st['rows_read']:,} | kept {st['rows_kept']:,} | bad code {st['bad_code']:,} | "
                   f"stores {len(store):,} (new {res['new_stores']}) | SIM types {len(sim)} (new {res['new_sim_types']}) | "
                   f"fact rows {res['inserted_rows']:,} (replaced {res['replaced_rows']:,}) | AP1D band rows {res['band_rows']:,}"
                   + (f" | WARNING {other_month} rows dated outside {month} - not loaded" if other_month else ""))
            bma_db.log_end(conn, load_id, "success", res["inserted_rows"], msg)
            print(f"{name}: OK in {time.time() - t0:,.0f}s\n   {msg}")
        except Exception as e:
            bma_db.log_end(conn, load_id, "failed", None, str(e))
            print(f"{name}: FAILED - {e}")
            failed.append(name)
    left = conn.cursor().execute("SELECT COUNT(*) FROM dim.SimType WHERE sim_type_group = N'Other'").fetchone()[0]
    if left:
        print(f"\nNote: {left} SIM type(s) are grouped as 'Other'. Add them to etl.SimTypeGroupMap if they need their own group.")
    conn.close()
    if failed:
        sys.exit(f"\n{len(failed)} file(s) failed: {', '.join(failed)} - previous data for those months is unchanged.")


if __name__ == "__main__":
    main()
