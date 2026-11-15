# Data quality

What is checked, where, and what happens. The principle: **never drop data silently** — fix it, map it to a visible
bucket, or log it.

| Issue | Where it is caught | Handling |
|---|---|---|
| Missing or renamed column in a sales file | `load_sales.read_file` reads the header first | Load stops with the list of missing columns; nothing is written |
| Non-numeric / out-of-range partner code or date | `load_sales.read_file` | Row dropped and **counted** (`bad_code`), count printed and logged |
| Rows outside the area | province filter | Excluded by design (only the 8 area districts are reported) |
| Unseen SIM type | `load_sales.write_month` | Added to `dim.SimType`; group from `etl.SimTypeGroupMap`, otherwise **`Other`** so it still counts and is easy to spot |
| Unseen partner code (not in any profile) | `load_sales.write_month` | New outlet in channel `7-11` (by the file's channel) or **`Other`** — sales are kept and visible under Channel = Other |
| RTR shop with two codes (True + Dtac) | `load_plan.store_profile_rtr` | Both codes linked to **one outlet**, so the shop is not double-counted |
| Plan / target row whose store name does not match | `load_plan` name matching (trimmed, case-insensitive) | Written to `logs/unmatched_<month>.csv` for follow-up |
| Store no longer in the RTR profile | `load_plan.finish_rtr` | Kept for history, flagged `in_profile = 0` |
| Outlets left with no codes or facts | `load_plan.cleanup` | Removed |
| Duplicate shops in the market survey | `load_plan.market_share` | Volumes summed, shares averaged |
| Share typed as 35 or 0.35 | `load_plan.share` | Both read as 35% |
| Store with no coordinates | dashboards | Still in all tables and totals; only left off the maps |
| Partial month (current) | metrics | Run rate scales GA to 31 days; week 4 and the current week are normalised to 7 days ([metrics](metrics.md)) |
| Re-running a load | all month loads | Delete + insert in one transaction → same result every time |

## Monitoring a load

```sql
-- last loads and their status
SELECT TOP (20) target_table, source_file, month_key, rows_loaded, [status], message, started_at, finished_at
FROM etl.LoadLog ORDER BY load_id DESC;

-- sales by channel for a month (should be close to the source total for the area)
SELECT o.channel, SUM(s.ga) AS ga
FROM fact.SalesDaily s
JOIN dim.Store st ON st.partner_code = s.partner_code
JOIN dim.Outlet o ON o.outlet_id = st.outlet_id
WHERE s.date_key / 100 = 202607
GROUP BY o.channel;

-- SIM types that fell into 'Other' (candidates for etl.SimTypeGroupMap)
SELECT sim_type FROM dim.SimType WHERE sim_type_group = 'Other';
```

Automated tests ([tests/test_etl.py](../tests/test_etl.py)) cover the cell cleaning rules, the area filter,
bad-code counting, aggregation and the missing-column error.
