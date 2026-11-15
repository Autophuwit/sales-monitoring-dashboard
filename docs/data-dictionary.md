# Data dictionary

Keys: **PK** primary key · **FK** foreign key · `month_key` = yyyymm · `date_key` = yyyymmdd.

## Dimensions

### dim.Date — one row per day (2025–2030)
| Column | Type | Description |
|---|---|---|
| date_key **PK** | int | yyyymmdd |
| date | date | calendar date |
| year, month_key, month_no, month_name | | calendar parts |
| day_of_month, days_in_month | tinyint | |
| week_of_month, week_label | tinyint, varchar | 1–4 / Week1–Week4 (days 1–7, 8–14, 15–21, 22–end) |
| week_days | tinyint | 7, or days in month − 21 for week 4 |
| day_of_week, day_name | tinyint, varchar | 1 = Monday … 7 = Sunday |

### dim.Outlet — one row per physical shop
| Column | Type | Description |
|---|---|---|
| outlet_id **PK** | int | surrogate key |
| channel | varchar | `7-11`, `RTR` or `Other` |
| outlet_key | nvarchar, unique | `7-11:<code>` / `RTR:<first code>` / `Other:<code>` — stable across reloads |
| outlet_name | nvarchar | display name |
| owner_id **FK** | int | account executive responsible (dim.Owner) |
| status | nvarchar | open / closed |
| segment | nvarchar | customer segment (7-11 profile): Office, Asia, Europe, condo, … |
| type_sim | nvarchar | target customer type (7-11 *Type Sim*, RTR *TARGET*) |
| ranking_priority | nvarchar | priority group (7-11), e.g. *High Sale & Negative MoM* |
| zone_name | nvarchar | sales zone (7-11) — peer group for alerts |
| rs_name | nvarchar | RTR reseller name |
| district_en, sub_district_en | nvarchar | area |
| latitude, longitude | decimal(9,6) | map position (may be empty) |
| in_profile | bit | 1 = listed in a current profile sheet |

### dim.Store — one row per partner code
| Column | Type | Description |
|---|---|---|
| partner_code **PK** | int | code used in the sales files |
| partner_name, source_partner_name | nvarchar | name as cleaned / as received |
| outlet_id **FK** | int | the shop this code belongs to (RTR shops can have two codes) |
| company | nvarchar | `TRUE` / `DTAC` (which RTR code column it came from) |
| sales_channel | nvarchar | channel given in the sales file |
| district_en, sub_district_en | nvarchar | area from the sales file |

### dim.Owner
| Column | Type | Description |
|---|---|---|
| owner_id **PK** | int | |
| owner_name | nvarchar, unique | short AE name |
| ae_full_name | nvarchar | |
| channel | varchar | channel the owner covers |
| is_active | bit | |

### dim.SimType
| Column | Type | Description |
|---|---|---|
| sim_type **PK** | nvarchar | product name from the sales file |
| sim_type_group | nvarchar | business group: Mass 49 / 199 / 350, Tourist 399 / 699 / 999, Other … (from `etl.SimTypeGroupMap`) |
| group_sim | nvarchar | MASS / MIGRANT / TOURIST |
| target_type | nvarchar | target bucket: Mass&Migrant / Tourist |
| company | nvarchar | TRUE / DTAC |
| is_gp_350, is_gp_199 | bit | counted in game plan 1 / 2 |

### dim.DocType
| Column | Type | Description |
|---|---|---|
| doc_type **PK** | nvarchar | registration document type (Thai ID, Passport, …) |
| is_thai_id | bit | used for %Thai |

### dim.GamePlan
| Column | Type | Description |
|---|---|---|
| game_plan_id **PK** | smallint | |
| channel | varchar | `7-11` / `RTR` |
| game_plan_name | nvarchar | unique per channel |
| sim_rule | varchar | `GP350` (Mass 350 SIMs only), `GP199` (Mass 199 only), `ALL` |

## Facts

| Table | Grain (PK) | Measures |
|---|---|---|
| **fact.SalesDaily** | date × partner code × SIM type × doc type | `ga`, `m1_amount`, `ap1d`, `ap1d_amount`, `load_id` |
| **fact.SalesAP1DBand** | date × partner code × SIM type × doc type × price band | `ap1d`, `ap1d_amount` |
| **fact.TargetOwner** | month × channel × owner × target type | `target_ga`, `target_m1` |
| **fact.TargetStore** | month × outlet | `target_ga`, `target_m1`, `target_ga_daily` / `target_m1_daily` = CEILING(target ÷ 30) (computed) |
| **fact.GamePlan** | month × outlet × game plan | `commit_ga`, `commit_ga_daily`, `start_date`, `end_date` |
| **fact.CloseGap** | month × outlet | `commit_ga` |
| **fact.MarketShare** | month × outlet | `market_size`, `total_sale`, `ga_true`, `ga_ais`, `share_true`, `share_ais`, `visibility_share`, `shelf_share`, `tumbon`, `key_location` |

## ETL tables

| Table | Description |
|---|---|
| **etl.LoadLog** | one row per load: target table, source file, month, file size / modified time, rows, status, message, start / finish |
| **etl.SimTypeGroupMap** | SIM type → business group mapping |

## Export file (`data/dashboard-data.js`)

| Key | Content |
|---|---|
| `asOfKey`, `generated`, `channels` | as-of day, export time, channel list |
| `stores` | outlets: codes, name, owner, segment, status, lat, lng, zone, type, priority, district, channel, in_profile, sub-district |
| `dict` | lookup lists for dictionary-encoded columns (SIM types, doc types, districts, price bands, …) |
| `f` | daily sales, columnar: day, store, group SIM, SIM type, SIM group, doc, district, sub-district, company, GA, M1, AP1D |
| `b` | AP1D by price band, columnar |
| `plans`, `gameplan`, `closegap` | game plans and commitments |
| `targets`, `tat` | owner targets and store targets |
| `market` | market share per outlet |
