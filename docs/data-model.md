# Data model

Database **BMA_East** on SQL Server Express. Star schema in four schemas:

| Schema | Holds |
|---|---|
| `dim` | descriptive tables: date, outlet (shop), partner code, owner, SIM type, document type, game plan |
| `fact` | measurable events: daily sales, AP1D by price band, targets, game plan commitments, close gap, market share |
| `rpt` | read-only views for exports and reports |
| `etl` | load log and mapping tables used by the loaders |

Built by [sql/01_create_database.sql](../sql/01_create_database.sql), [02_prepare_load.sql](../sql/02_prepare_load.sql) and
[03_channel_model.sql](../sql/03_channel_model.sql) (run once, in order).

## ERD

```mermaid
erDiagram
  dim_Date    ||--o{ fact_SalesDaily     : date_key
  dim_Date    ||--o{ fact_SalesAP1DBand  : date_key
  dim_Store   ||--o{ fact_SalesDaily     : partner_code
  dim_Store   ||--o{ fact_SalesAP1DBand  : partner_code
  dim_SimType ||--o{ fact_SalesDaily     : sim_type
  dim_SimType ||--o{ fact_SalesAP1DBand  : sim_type
  dim_DocType ||--o{ fact_SalesDaily     : doc_type
  dim_DocType ||--o{ fact_SalesAP1DBand  : doc_type
  dim_Outlet  ||--|{ dim_Store           : "outlet_id (1 shop : 1-2 codes)"
  dim_Owner   ||--o{ dim_Outlet          : owner_id
  dim_Owner   ||--o{ fact_TargetOwner    : owner_id
  dim_Outlet  ||--o{ fact_TargetStore    : outlet_id
  dim_Outlet  ||--o{ fact_GamePlan       : outlet_id
  dim_GamePlan ||--o{ fact_GamePlan      : game_plan_id
  dim_Outlet  ||--o{ fact_CloseGap       : outlet_id
  dim_Outlet  ||--o{ fact_MarketShare    : outlet_id

  dim_Outlet {
    int outlet_id PK
    varchar channel "7-11 / RTR / Other"
    nvarchar outlet_key UK
    nvarchar outlet_name
    int owner_id FK
    nvarchar segment
    nvarchar ranking_priority
    nvarchar zone_name
    decimal latitude
    decimal longitude
    bit in_profile
  }
  dim_Store {
    int partner_code PK
    nvarchar partner_name
    int outlet_id FK
    nvarchar company "TRUE / DTAC"
    nvarchar sales_channel
  }
  fact_SalesDaily {
    int date_key PK,FK
    int partner_code PK,FK
    nvarchar sim_type PK,FK
    nvarchar doc_type PK,FK
    int ga
    decimal m1_amount
    int ap1d
    decimal ap1d_amount
    int load_id
  }
  fact_SalesAP1DBand {
    int date_key PK,FK
    int partner_code PK,FK
    nvarchar sim_type PK,FK
    nvarchar doc_type PK,FK
    varchar price_band PK
    int ap1d
    decimal ap1d_amount
  }
  fact_TargetOwner {
    int month_key PK
    varchar channel PK
    int owner_id PK,FK
    nvarchar target_type PK
    int target_ga
  }
  fact_TargetStore {
    int month_key PK
    int outlet_id PK,FK
    int target_ga
    int target_ga_daily "computed"
  }
  fact_GamePlan {
    int month_key PK
    int outlet_id PK,FK
    smallint game_plan_id PK,FK
    int commit_ga
    int commit_ga_daily
    date start_date
    date end_date
  }
  fact_CloseGap {
    int month_key PK
    int outlet_id PK,FK
    int commit_ga
  }
  fact_MarketShare {
    int month_key PK
    int outlet_id PK,FK
    int market_size
    int ga_true
    decimal share_true
  }
  dim_GamePlan {
    smallint game_plan_id PK
    varchar channel
    nvarchar game_plan_name
    varchar sim_rule "GP350 / GP199 / ALL"
  }
  dim_SimType {
    nvarchar sim_type PK
    nvarchar sim_type_group
    nvarchar group_sim
    nvarchar target_type
    nvarchar company
  }
```
`dim.Date`, `dim.Owner` and `dim.DocType` are omitted from the diagram body for space — see the [data dictionary](data-dictionary.md).

## Design decisions

**Grain of the sales fact.** One row per **day × partner code × SIM type × document type**. This is the lowest level any
dashboard needs (daily charts, SIM and document breakdowns, store detail), and it shrinks the raw transaction files to
~96k rows for 7 months.

**Outlet vs. partner code.** Sales arrive by *partner code*. A 7-11 store has one code; many RTR stores have two (one per
operator, True and Dtac). Facts stay at partner-code grain (as delivered), and `dim.Store.outlet_id` maps each code to
one `dim.Outlet` — the physical shop that owns the profile, owner, location, targets and game plans. Every report
aggregates by outlet, so a dual-code RTR shop is counted once everywhere.
`outlet_key` (`7-11:<code>`, `RTR:<first code>`, `Other:<code>`) keeps the outlet stable across reloads.

**Three channels in the same tables.** `dim.Outlet.channel` is `7-11`, `RTR` or `Other` (codes in the area that are in
neither profile, so no sales are lost). Targets, game plans and plans' SIM rules carry the channel, so one set of tables
and one dashboard per topic serves both channels with a Channel filter.

**Monthly planning facts.** Targets, game plans, close gap and market share are keyed by `month_key` and replaced per
month, which keeps history and makes re-loads idempotent.

**Business rules as data.** SIM type → group mapping lives in `etl.SimTypeGroupMap`; each game plan's SIM rule
(`GP350` = Mass 350 SIMs only, `GP199` = Mass 199 only, `ALL`) lives in `dim.GamePlan.sim_rule` — not in code.

**Computed columns.** `fact.TargetStore.target_ga_daily = CEILING(target_ga / 30)` is persisted, so every consumer uses
the same daily target.

## Views (`rpt`)

| View | Purpose |
|---|---|
| `rpt.AsOf` | as-of date (latest sales day), current / previous month, run-rate day (as-of day − 1), current week |
| `rpt.DateFlags` | `dim.Date` plus current / previous month flags |
| `rpt.StoreOutlet` | partner code → outlet → channel, owner, location (convenience join) |
| `rpt.OutletMarketShare` | latest market share per outlet; 7-11 outlets = 100% |
