"""
Generate a realistic DEMO data file for the dashboards (no database, no real data).

  python etl/make_demo_data.py --out data/dashboard-data.js

Same format as export_html.py (version 3). Every store, owner, number and location is synthetic:
  * ~240 outlets (7-11, RTR - some with two partner codes - and Other) in the 8 districts of the area,
    placed around each sub-district with random jitter
  * 7 months of daily sales by SIM type and document type, driven by each store's customer segment
    (tourist / migrant / Thai mass customers), day of week and season
  * the five game plans, with their effect built in (e.g. more Mass 350 in plan-1 stores from May)
  * owner / store targets, close gap commitments and RTR market share
Uses a fixed random seed, so the output is reproducible.
"""
import argparse
import datetime as dt
import json
import math
import os
import random

SEED = 42
FIRST_MONTH, AS_OF = (2026, 1), dt.date(2026, 7, 29)
CHANNELS = ["7-11", "RTR", "Other"]
BANDS = ["1-49", "50-99", "100-119", "120-149", "150-199", "200-249", "250-299", "300UP"]
DEMO_MARK = "DEMO DATA"

# public geography of the area: district -> (centre lat, lng, [sub-districts])
AREA = {
    "Phra Nakhon":          (13.7560, 100.4980, ["Chana Songkhram", "Talat Yot", "Bowon Niwet", "Sao Chingcha", "Wang Burapha Phirom"]),
    "Dusit":                (13.7770, 100.5130, ["Dusit", "Wachiraphayaban", "Suan Chitlada", "Si Yaek Mahanak"]),
    "Bang Rak":             (13.7270, 100.5240, ["Si Lom", "Suriyawong", "Maha Phruettharam", "Bang Rak", "Si Phraya"]),
    "Sathon":               (13.7080, 100.5300, ["Thung Wat Don", "Yan Nawa", "Thung Maha Mek"]),
    "Pom Prap Sattru Phai": (13.7570, 100.5130, ["Pom Prap", "Wat Thep Sirin", "Khlong Maha Nak", "Ban Bat"]),
    "Samphanthawong":       (13.7380, 100.5120, ["Chakkrawat", "Samphanthawong", "Talat Noi"]),
    "Yan Nawa":             (13.6930, 100.5430, ["Chong Nonsi", "Bang Phongphang"]),
    "Bang Kho Laem":        (13.6930, 100.5030, ["Bang Kho Laem", "Wat Phraya Krai", "Bang Khlo"]),
}
# segment -> customer mix (tourist, migrant, thai mass) and how busy stores are
SEGMENTS = {
    "Europe":   ((0.70, 0.05, 0.25), 1.3), "Asia":     ((0.55, 0.10, 0.35), 1.2), "AEC":      ((0.10, 0.65, 0.25), 1.2),
    "condo":    ((0.20, 0.10, 0.70), 0.9), "Office":   ((0.10, 0.10, 0.80), 1.0), "Student":  ((0.05, 0.05, 0.90), 0.9),
    "Hospital": ((0.10, 0.15, 0.75), 0.8), "Community": ((0.03, 0.22, 0.75), 0.8),
}
# SIM types: name -> (group_sim, sim_type_group, company, price)
SIMS = {
    "TRUE Tourist 399": ("TOURIST", "Tourist 399", "TRUE", 399), "TRUE Tourist 699": ("TOURIST", "Tourist 699", "TRUE", 699),
    "TRUE Tourist 999": ("TOURIST", "Tourist 999", "TRUE", 999), "DTAC Tourist 399": ("TOURIST", "Tourist 399", "DTAC", 399),
    "TRUE Migrant 99":  ("MIGRANT", "Migrant 99", "TRUE", 99),    "DTAC Migrant 99":  ("MIGRANT", "Migrant 99", "DTAC", 99),
    "TRUE Ready 49":    ("MASS", "Mass 49", "TRUE", 49),          "DTAC Ready 49":    ("MASS", "Mass 49", "DTAC", 49),
    "TRUE Ready 199":   ("MASS", "Mass 199", "TRUE", 199),        "DTAC Ready 199":   ("MASS", "Mass 199", "DTAC", 199),
    "TRUE Ready 350":   ("MASS", "Mass 350", "TRUE", 350),        "TRUE Student 129": ("MASS", "Other", "TRUE", 129),
}
TOURIST = ["TRUE Tourist 399", "TRUE Tourist 699", "TRUE Tourist 999", "DTAC Tourist 399"]
MIGRANT = ["TRUE Migrant 99", "DTAC Migrant 99"]
PLANS = [("1. Increase ARPU (199 -> 350)", "7-11", "GP350"), ("2. Increase ARPU (49 ->199)", "7-11", "GP199"),
         ("3. Increase GA", "7-11", "ALL"), ("1. Win Market Share", "RTR", "ALL"), ("2. Potential Store", "RTR", "ALL")]
PRIORITY = ["1) High Sale & Negative MOM", "2) High Sale & Positive MOM", "3) Low Sale & Negative MOM", "4) Low Sale & Positive MOM"]


class Dict:
    def __init__(self, fixed=None):
        self.items, self.idx = [], {}
        for v in fixed or []:
            self(v)

    def __call__(self, v):
        v = "" if v is None else str(v)
        if v not in self.idx:
            self.idx[v] = len(self.items); self.items.append(v)
        return self.idx[v]


def poisson(rng, lam):
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    l, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= l:
            return k
        k += 1


def season(day):
    """tourist low season May-Sep, a little Songkran bump in April"""
    return {1: 1.15, 2: 1.15, 3: 1.05, 4: 1.0, 5: 0.82, 6: 0.78, 7: 0.80}.get(day.month, 1.0)


def make_outlets(rng):
    outlets, code = [], 7_100_000
    subs = [(d, s) for d, (_, _, ss) in AREA.items() for s in ss]
    owners_711, owners_rtr = ["AE 1", "AE 2", "AE 3", "AE 4"], ["RS 1", "RS 2"]

    def place(dist, sub):
        lat, lng, ss = AREA[dist]
        k = ss.index(sub)
        ang = 2 * math.pi * k / len(ss)
        return round(lat + 0.006 * math.sin(ang) + rng.gauss(0, 0.0028), 6), round(lng + 0.006 * math.cos(ang) + rng.gauss(0, 0.0028), 6)

    for ch, n in [("7-11", 150), ("RTR", 60), ("Other", 30)]:
        for j in range(n):
            dist, sub = rng.choice(subs)
            if ch == "7-11":
                seg = rng.choices(list(SEGMENTS), weights=[14, 16, 10, 12, 18, 10, 6, 14])[0]
            elif ch == "RTR":
                seg = rng.choices(["AEC", "Community", "Asia", "Europe"], weights=[40, 35, 15, 10])[0]
            else:
                seg = rng.choice(["Community", "Office", "AEC"])
            lat, lng = place(dist, sub)
            codes = [code]; code += 1
            if ch == "RTR" and rng.random() < 0.35:          # some RTR shops sell with two partner codes (True + Dtac)
                codes.append(code); code += 1
            name = (f"7-Eleven {sub} {j % 9 + 1}" if ch == "7-11" else
                    f"{rng.choice(['Siam', 'Lucky', 'Happy', 'Star', 'Golden', 'Smart', 'City'])} Mobile {sub}" if ch == "RTR" else
                    f"Partner {sub} {j + 1}")
            mix, busy = SEGMENTS[seg]
            outlets.append({
                "codes": codes, "name": name, "channel": ch, "dist": dist, "sub": sub, "lat": lat, "lng": lng,
                "seg": seg if ch == "7-11" else "", "mix": mix, "owner": rng.choice(owners_711 if ch == "7-11" else owners_rtr if ch == "RTR" else [""]),
                "base": busy * {"7-11": rng.uniform(0.8, 3.2), "RTR": rng.uniform(1.5, 6.5), "Other": rng.uniform(0.2, 1.0)}[ch],
                "trend": rng.gauss(0, 0.04), "zone": f"Zone {list(AREA).index(dist) // 2 + 1}" if ch == "7-11" else "",
                "type_sim": ("Tourist" if mix[0] > 0.5 else "Mass-Migrant" if mix[1] > 0.3 else "Mass-Tourist") if ch != "Other" else "",
                "priority": rng.choice(PRIORITY) if ch == "7-11" else "", "plans": set(),
            })
    return outlets


def assign_plans(rng, outlets):
    s711 = [o for o in outlets if o["channel"] == "7-11"]
    rtr = [o for o in outlets if o["channel"] == "RTR"]
    pick = lambda pool, n: rng.sample(pool, min(n, len(pool)))
    for o in pick([o for o in s711 if o["seg"] in ("Asia", "condo", "Office")], 9):
        o["plans"].add(PLANS[0][0])
    for o in pick([o for o in s711 if o["seg"] in ("Student", "Community", "condo") and not o["plans"]], 10):
        o["plans"].add(PLANS[1][0])
    for o in pick([o for o in s711 if o["seg"] in ("Europe", "Asia")], 60):
        o["plans"].add(PLANS[2][0])
    for o in pick(rtr, 17):
        o["plans"].add(PLANS[3][0])
    for o in pick(sorted(rtr, key=lambda o: -o["base"])[:30], 24):
        o["plans"].add(PLANS[4][0])


def daily_mix(o, day):
    """expected GA per SIM type for one store and day"""
    tour, mig, mass = o["mix"]
    months_in = (day.year - FIRST_MONTH[0]) * 12 + day.month - FIRST_MONTH[1]
    lam = o["base"] * (1 + o["trend"]) ** months_in
    wd = day.weekday()
    lam *= 1.25 if wd >= 5 and tour > 0.4 else 1.15 if wd == 6 and mig > 0.4 else 1.0
    lam *= season(day) if tour > 0.4 else 1.0
    plan_on = day >= dt.date(2026, 5, 1)
    p = o["plans"]
    if plan_on and PLANS[2][0] in p:
        lam *= 1.12                                          # tourist-area GA push
    if plan_on and (PLANS[3][0] in p or PLANS[4][0] in p):
        lam *= 1.14
    out = {}
    for s in TOURIST:
        out[s] = lam * tour * {"TRUE Tourist 399": 0.45, "TRUE Tourist 699": 0.25, "TRUE Tourist 999": 0.1, "DTAC Tourist 399": 0.2}[s]
    for s in MIGRANT:
        out[s] = lam * mig * 0.5
    m49, m199, m350 = 0.38, 0.40, 0.14
    if plan_on and PLANS[0][0] in p:
        m199, m350 = m199 - 0.06, m350 + 0.06                # 199 -> 350
    if plan_on and PLANS[1][0] in p:
        m49, m199 = m49 - 0.08, m199 + 0.08                  # 49 -> 199
    for s, w in [("TRUE Ready 49", m49 * 0.6), ("DTAC Ready 49", m49 * 0.4), ("TRUE Ready 199", m199 * 0.6),
                 ("DTAC Ready 199", m199 * 0.4), ("TRUE Ready 350", m350), ("TRUE Student 129", 0.08)]:
        out[s] = lam * mass * w
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="where to write, e.g. data/dashboard-data.js")
    ap.add_argument("--force", action="store_true", help="overwrite a file that is not demo data")
    a = ap.parse_args()
    if os.path.exists(a.out) and not a.force:
        with open(a.out, encoding="utf-8", errors="replace") as fh:
            if DEMO_MARK not in fh.read(300):
                raise SystemExit(f"{a.out} is not a demo file - refusing to overwrite real data (use --force if you are sure).")

    rng = random.Random(SEED)
    outlets = make_outlets(rng)
    assign_plans(rng, outlets)
    d = {k: Dict() for k in ["gs", "st", "stg", "doc", "dist", "sub", "comp"]}
    d["band"] = Dict(BANDS)
    F = {k: [] for k in ["day", "store", "gs", "st", "stg", "doc", "dist", "sub", "comp", "ga", "m1", "ap1d"]}
    B = {k: [] for k in ["day", "store", "gs", "st", "stg", "doc", "dist", "sub", "comp", "band", "q"]}
    band_of = lambda price: next(b for b, hi in zip(BANDS, [49, 99, 119, 149, 199, 249, 299, 10**9]) if price <= hi)

    day = dt.date(*FIRST_MONTH, 1)
    while day <= AS_OF:
        key = day.year * 10000 + day.month * 100 + day.day
        for i, o in enumerate(outlets):
            for sim, lam in daily_mix(o, day).items():
                ga = poisson(rng, lam)
                if not ga:
                    continue
                gs, stg, comp, price = SIMS[sim]
                doc = "Passport" if gs == "TOURIST" else "Pink Card" if gs == "MIGRANT" else ("Thai ID" if rng.random() < 0.93 else "Other")
                ap1d = sum(1 for _ in range(ga) if rng.random() < (0.55 if gs == "TOURIST" else 0.75))
                row = dict(day=key, store=i, gs=d["gs"](gs), st=d["st"](sim), stg=d["stg"](stg), doc=d["doc"](doc),
                           dist=d["dist"](o["dist"]), sub=d["sub"](o["sub"]), comp=d["comp"](comp))
                for k, v in row.items():
                    F[k].append(v)
                F["ga"].append(ga); F["m1"].append(round(ga * price * rng.uniform(0.85, 1.1), 2)); F["ap1d"].append(ap1d)
                if ap1d:
                    for k, v in row.items():
                        B[k].append(v)
                    B["band"].append(d["band"](band_of(price))); B["q"].append(ap1d)
        day += dt.timedelta(days=1)

    # ---- planning tables for the current month ----
    cur_m = AS_OF.year * 100 + AS_OF.month
    exp = {i: sum(daily_mix(o, dt.date(2026, 7, 15)).values()) * 31 for i, o in enumerate(outlets)}
    stores = [[" / ".join(map(str, o["codes"])), o["name"], o["owner"], o["seg"], "Open", o["lat"], o["lng"], o["zone"],
               o["type_sim"], o["priority"], o["dist"], o["channel"], 1 if o["channel"] != "Other" else 0, o["sub"]] for o in outlets]
    targets, tat, closegap, gameplan, market = [], [], [], [], []
    for owner in sorted({o["owner"] for o in outlets if o["owner"]}):
        ch = "7-11" if owner.startswith("AE") else "RTR"
        mine = [i for i, o in enumerate(outlets) if o["owner"] == owner]
        tour = sum(exp[i] * outlets[i]["mix"][0] for i in mine)
        rest = sum(exp[i] for i in mine) - tour
        targets += [[owner, "Tourist", int(tour * 1.08), round(tour * 1.08 * 520, 2), ch],
                    [owner, "Mass&Migrant", int(rest * 1.08), round(rest * 1.08 * 170, 2), ch]]
    for i, o in enumerate(outlets):
        if o["channel"] == "7-11":
            t = int(exp[i] * 1.1) + 1
            tat.append([i, t, round(t * 260.0, 2), math.ceil(t / 30), round(math.ceil(t / 30) * 260.0, 2)])
            if rng.random() < 0.3:
                closegap.append([i, max(1, int(exp[i] * 0.12))])
        if o["channel"] == "RTR":
            share = min(0.92, max(0.18, rng.gauss(0.62, 0.15) - (0.15 if PLANS[3][0] in o["plans"] else 0)))
            size = int(exp[i] / share)
            market.append([i, round(share, 4), size, int(size * share), int(size * (1 - share) * 0.8), o["sub"]])
        for p in sorted(o["plans"]):
            ch, rule = next((c, r) for n, c, r in PLANS if n == p)
            commit = max(3, int(exp[i] * (0.11 if rule != "ALL" else 1.12)))   # ARPU plans commit only their target SIM
            gameplan.append([i, p, commit, max(1, round(commit / 31)), ch, rule, "2026-07-01", "2026-07-31"])

    data = {"version": 3, "generated": f"{DEMO_MARK} (synthetic)", "asOfKey": AS_OF.year * 10000 + AS_OF.month * 100 + AS_OF.day,
            "channels": CHANNELS, "dict": {k: v.items for k, v in d.items()}, "stores": stores, "f": F, "b": B,
            "plans": [[n, c, r] for n, c, r in PLANS], "targets": targets, "tat": tat, "gameplan": gameplan,
            "closegap": closegap, "market": market, "months": {"all": cur_m}}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write(f"/* {DEMO_MARK}: synthetic stores and sales generated by etl/make_demo_data.py - not real figures. */\n")
        fh.write("window.DASH_DATA = ")
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")
    by_ch = {c: sum(1 for o in outlets if o["channel"] == c) for c in CHANNELS}
    print(f"Demo outlets : {len(outlets)} ({', '.join(f'{k} {v}' for k, v in by_ch.items())})")
    print(f"Sales rows   : {len(F['day']):,}   band rows {len(B['day']):,}   plan rows {len(gameplan)}")
    print(f"File         : {a.out} ({os.path.getsize(a.out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
