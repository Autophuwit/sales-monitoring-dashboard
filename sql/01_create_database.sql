/* =====================================================================
   BMA East - database + star schema
   Server : localhost\SQLEXPRESS   (SQL Server Express)
   Run    : open in SSMS -> Execute (F5). Safe to run again: it only
            creates what does not exist yet and never deletes data.

   Layout
     dim.*   descriptive tables (one row per date / store / SIM type ...)
     fact.*  numbers (sales per day, targets, game plans, close gap)
     etl.*   load history for the monthly uploads
     rpt.*   views used by the export / reports (current month, as-of date)

   Relationships: every fact -> dim link is many-to-one on a CODE
   (partner_code, date_key, sim_type ...) - never on a store name.
   ===================================================================== */

SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;     -- required for the computed columns / columnstore index (SSMS default, sqlcmd is OFF)
GO

/* ---------- 1. Database ---------- */
IF DB_ID(N'BMA_East') IS NULL
BEGIN
    CREATE DATABASE BMA_East;
END
GO
ALTER DATABASE BMA_East SET RECOVERY SIMPLE;   -- keeps the log file small (no point-in-time restore needed)
GO
USE BMA_East;
GO

/* ---------- 2. Schemas ---------- */
IF SCHEMA_ID(N'dim')  IS NULL EXEC (N'CREATE SCHEMA dim');
IF SCHEMA_ID(N'fact') IS NULL EXEC (N'CREATE SCHEMA fact');
IF SCHEMA_ID(N'etl')  IS NULL EXEC (N'CREATE SCHEMA etl');
IF SCHEMA_ID(N'rpt')  IS NULL EXEC (N'CREATE SCHEMA rpt');
GO

/* =====================================================================
   3. DIMENSIONS
   ===================================================================== */

/* dim.[Date] - one row per day. Week rule ('Week of Month'):
   day 1-7 = 1, 8-14 = 2, 15-21 = 3, 22-end = 4 */
IF OBJECT_ID(N'dim.[Date]') IS NULL
CREATE TABLE dim.[Date] (
    date_key        INT          NOT NULL CONSTRAINT PK_Date PRIMARY KEY,   -- yyyymmdd e.g. 20260729
    [date]          DATE         NOT NULL CONSTRAINT UQ_Date_date UNIQUE,
    [year]          SMALLINT     NOT NULL,
    month_key       INT          NOT NULL,                                   -- yyyymm  e.g. 202607
    month_no        TINYINT      NOT NULL,
    month_name      VARCHAR(3)   NOT NULL,                                   -- Jan..Dec
    day_of_month    TINYINT      NOT NULL,
    days_in_month   TINYINT      NOT NULL,
    week_of_month   TINYINT      NOT NULL,                                   -- 1..4
    week_label      VARCHAR(5)   NOT NULL,                                   -- Week1..Week4
    week_days       TINYINT      NOT NULL,                                   -- 7, or days in month - 21 for week 4
    day_of_week     TINYINT      NOT NULL,                                   -- 1 = Monday ... 7 = Sunday
    day_name        VARCHAR(9)   NOT NULL
);
GO

/* dim.Owner - AE / owner of stores */
IF OBJECT_ID(N'dim.Owner') IS NULL
CREATE TABLE dim.Owner (
    owner_id        INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_Owner PRIMARY KEY,
    owner_name      NVARCHAR(50)  NOT NULL CONSTRAINT UQ_Owner_name UNIQUE,  -- e.g. P.Ty
    ae_full_name    NVARCHAR(100) NULL,
    is_active       BIT           NOT NULL CONSTRAINT DF_Owner_active DEFAULT (1)
);
GO

/* dim.Store - replaces 'Store Profile' (and the district columns that were repeated in every sales row) */
IF OBJECT_ID(N'dim.Store') IS NULL
CREATE TABLE dim.Store (
    partner_code     INT           NOT NULL CONSTRAINT PK_Store PRIMARY KEY,
    partner_name     NVARCHAR(200) NOT NULL,
    owner_id         INT           NULL CONSTRAINT FK_Store_Owner REFERENCES dim.Owner(owner_id),
    segment          NVARCHAR(50)  NULL,          -- AEC, Asia, condo, Europe ...
    [status]         NVARCHAR(30)  NULL,          -- Open / Closed
    type_sim         NVARCHAR(50)  NULL,          -- Mass-Migrant / Mass-Tourist / Tourist
    ranking_priority NVARCHAR(100) NULL,          -- 1) High Sale & Negative MOM ...
    zone_name        NVARCHAR(100) NULL,
    district_en      NVARCHAR(100) NULL,
    sub_district_en  NVARCHAR(100) NULL,
    latitude         DECIMAL(9,6)  NULL,
    longitude        DECIMAL(9,6)  NULL,
    updated_at       DATETIME2(0)  NOT NULL CONSTRAINT DF_Store_updated DEFAULT (SYSDATETIME())
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Store_owner')
    CREATE INDEX IX_Store_owner ON dim.Store(owner_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Store_name')
    CREATE INDEX IX_Store_name ON dim.Store(partner_name);       -- used once, to match the old name-based files
GO

/* dim.SimType - replaces the SWITCH formulas (SIM_TYPE_GROUP, Group SIM2) and the SIM lists inside the *GP measures */
IF OBJECT_ID(N'dim.SimType') IS NULL
CREATE TABLE dim.SimType (
    sim_type         NVARCHAR(100) NOT NULL CONSTRAINT PK_SimType PRIMARY KEY,   -- e.g. 7-11 READY NET 199
    sim_type_group   NVARCHAR(50)  NOT NULL,      -- Mass 199, Mass 49, Mass 350, Tourist 399 ...
    group_sim        NVARCHAR(20)  NOT NULL,      -- MASS / MIGRANT / TOURIST
    target_type      NVARCHAR(30)  NULL,          -- Mass&Migrant / Tourist   (old 'Group SIM2')
    company          NVARCHAR(20)  NULL,          -- TRUE / DTAC
    is_gp_350        BIT           NOT NULL CONSTRAINT DF_SimType_gp350 DEFAULT (0),  -- counted in game plan 1 (199 -> 350)
    is_gp_199        BIT           NOT NULL CONSTRAINT DF_SimType_gp199 DEFAULT (0)   -- counted in game plan 2 (49 -> 199)
);
GO

/* dim.DocType - Thai ID / Others */
IF OBJECT_ID(N'dim.DocType') IS NULL
CREATE TABLE dim.DocType (
    doc_type         NVARCHAR(30) NOT NULL CONSTRAINT PK_DocType PRIMARY KEY,
    is_thai_id       BIT          NOT NULL CONSTRAINT DF_DocType_thai DEFAULT (0)
);
GO

/* dim.GamePlan - the 3 plans and which SIM types each one counts */
IF OBJECT_ID(N'dim.GamePlan') IS NULL
CREATE TABLE dim.GamePlan (
    game_plan_id     TINYINT       NOT NULL CONSTRAINT PK_GamePlan PRIMARY KEY,
    game_plan_name   NVARCHAR(100) NOT NULL CONSTRAINT UQ_GamePlan_name UNIQUE,
    sim_rule         VARCHAR(10)   NOT NULL CONSTRAINT CK_GamePlan_rule CHECK (sim_rule IN ('GP350', 'GP199', 'ALL'))
);
GO

/* =====================================================================
   4. FACTS
   ===================================================================== */

/* fact.SalesDaily - replaces Prepay_T&D. Grain: day x store x SIM type x doc type.
   Only the columns the dashboards use; add more AP columns later if needed. */
IF OBJECT_ID(N'fact.SalesDaily') IS NULL
CREATE TABLE fact.SalesDaily (
    date_key         INT           NOT NULL CONSTRAINT FK_Sales_Date    REFERENCES dim.[Date](date_key),
    partner_code     INT           NOT NULL CONSTRAINT FK_Sales_Store   REFERENCES dim.Store(partner_code),
    sim_type         NVARCHAR(100) NOT NULL CONSTRAINT FK_Sales_SimType REFERENCES dim.SimType(sim_type),
    doc_type         NVARCHAR(30)  NOT NULL CONSTRAINT FK_Sales_DocType REFERENCES dim.DocType(doc_type),
    ga               INT           NOT NULL CONSTRAINT DF_Sales_ga DEFAULT (0),
    m1_amount        DECIMAL(18,2) NOT NULL CONSTRAINT DF_Sales_m1 DEFAULT (0),   -- REV_AMT
    ap1d             INT           NULL,
    ap1d_amount      DECIMAL(18,2) NULL,
    load_id          INT           NULL,          -- etl.LoadLog row that loaded it
    CONSTRAINT PK_SalesDaily PRIMARY KEY CLUSTERED (date_key, partner_code, sim_type, doc_type)
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Sales_store')
    CREATE INDEX IX_Sales_store ON fact.SalesDaily(partner_code, date_key) INCLUDE (ga, m1_amount);
-- columnstore copy: fast sums/aggregations for the HTML export and reports (supported in Express 2016 SP1+)
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'NCCI_SalesDaily')
    CREATE NONCLUSTERED COLUMNSTORE INDEX NCCI_SalesDaily
        ON fact.SalesDaily (date_key, partner_code, sim_type, doc_type, ga, m1_amount, ap1d, ap1d_amount);
GO

/* fact.TargetOwner - replaces 'Target By AE Name', now kept per month */
IF OBJECT_ID(N'fact.TargetOwner') IS NULL
CREATE TABLE fact.TargetOwner (
    month_key        INT           NOT NULL,
    owner_id         INT           NOT NULL CONSTRAINT FK_TgtOwner_Owner REFERENCES dim.Owner(owner_id),
    target_type      NVARCHAR(30)  NOT NULL,      -- Mass&Migrant / Tourist
    target_ga        INT           NOT NULL CONSTRAINT DF_TgtOwner_ga DEFAULT (0),
    target_m1        DECIMAL(18,2) NOT NULL CONSTRAINT DF_TgtOwner_m1 DEFAULT (0),
    CONSTRAINT PK_TargetOwner PRIMARY KEY (month_key, owner_id, target_type)
);
GO

/* fact.TargetStore - replaces 'Target All Type' (store level), per month */
IF OBJECT_ID(N'fact.TargetStore') IS NULL
CREATE TABLE fact.TargetStore (
    month_key        INT           NOT NULL,
    partner_code     INT           NOT NULL CONSTRAINT FK_TgtStore_Store REFERENCES dim.Store(partner_code),
    target_ga        INT           NOT NULL CONSTRAINT DF_TgtStore_ga DEFAULT (0),
    target_m1        DECIMAL(18,2) NOT NULL CONSTRAINT DF_TgtStore_m1 DEFAULT (0),
    -- daily targets exactly like the old calculated columns: ROUNDUP(target / 30)
    target_ga_daily  AS CAST(CEILING(target_ga / 30.0) AS INT) PERSISTED,
    target_m1_daily  AS CAST(CEILING(target_m1 / 30.0) AS DECIMAL(18,0)) PERSISTED,
    CONSTRAINT PK_TargetStore PRIMARY KEY (month_key, partner_code)
);
GO

/* fact.GamePlan - replaces 'Game Plan' (was joined on store NAME, now on partner_code), per month */
IF OBJECT_ID(N'fact.GamePlan') IS NULL
CREATE TABLE fact.GamePlan (
    month_key        INT           NOT NULL,
    partner_code     INT           NOT NULL CONSTRAINT FK_GP_Store REFERENCES dim.Store(partner_code),
    game_plan_id     TINYINT       NOT NULL CONSTRAINT FK_GP_Plan  REFERENCES dim.GamePlan(game_plan_id),
    commit_ga        INT           NOT NULL CONSTRAINT DF_GP_commit DEFAULT (0),
    commit_ga_daily  INT           NULL,
    start_date       DATE          NULL,
    end_date         DATE          NULL,
    CONSTRAINT PK_GamePlan PRIMARY KEY (month_key, partner_code, game_plan_id)
);
GO

/* fact.CloseGap - replaces 'Close Gap Store'. Only the commitment is stored;
   Pre GA / Cur GA / %MOM are calculated live from fact.SalesDaily. */
IF OBJECT_ID(N'fact.CloseGap') IS NULL
CREATE TABLE fact.CloseGap (
    month_key        INT           NOT NULL,
    partner_code     INT           NOT NULL CONSTRAINT FK_CG_Store REFERENCES dim.Store(partner_code),
    commit_ga        INT           NOT NULL CONSTRAINT DF_CG_commit DEFAULT (0),
    CONSTRAINT PK_CloseGap PRIMARY KEY (month_key, partner_code)
);
GO

/* =====================================================================
   5. LOAD HISTORY (used by the upload scripts later)
   ===================================================================== */
IF OBJECT_ID(N'etl.LoadLog') IS NULL
CREATE TABLE etl.LoadLog (
    load_id          INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_LoadLog PRIMARY KEY,
    target_table     NVARCHAR(100) NOT NULL,
    source_file      NVARCHAR(260) NULL,
    month_key        INT           NULL,
    rows_loaded      INT           NULL,
    [status]         VARCHAR(20)   NOT NULL CONSTRAINT DF_LoadLog_status DEFAULT ('started'),
    message          NVARCHAR(1000) NULL,
    started_at       DATETIME2(0)  NOT NULL CONSTRAINT DF_LoadLog_start DEFAULT (SYSDATETIME()),
    finished_at      DATETIME2(0)  NULL
);
GO

/* =====================================================================
   6. SEED DATA (fixed lists)
   ===================================================================== */

/* dates 2025-01-01 .. 2030-12-31 */
DECLARE @from DATE = '2025-01-01', @to DATE = '2030-12-31';
;WITH n AS (
    SELECT TOP (DATEDIFF(DAY, @from, @to) + 1)
           ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) - 1 AS i
    FROM sys.all_objects a CROSS JOIN sys.all_objects b
), d AS (
    SELECT DATEADD(DAY, i, @from) AS dt FROM n
)
INSERT INTO dim.[Date] (date_key, [date], [year], month_key, month_no, month_name, day_of_month, days_in_month,
                        week_of_month, week_label, week_days, day_of_week, day_name)
SELECT
    YEAR(dt) * 10000 + MONTH(dt) * 100 + DAY(dt),
    dt,
    YEAR(dt),
    YEAR(dt) * 100 + MONTH(dt),
    MONTH(dt),
    LEFT(DATENAME(MONTH, dt), 3),
    DAY(dt),
    DAY(EOMONTH(dt)),
    w.wk,
    'Week' + CAST(w.wk AS VARCHAR(1)),
    CASE WHEN w.wk = 4 THEN DAY(EOMONTH(dt)) - 21 ELSE 7 END,
    dw.dow,
    CHOOSE(dw.dow, 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
FROM d
CROSS APPLY (SELECT CASE WHEN DAY(dt) <= 7 THEN 1 WHEN DAY(dt) <= 14 THEN 2 WHEN DAY(dt) <= 21 THEN 3 ELSE 4 END AS wk) w
CROSS APPLY (SELECT DATEDIFF(DAY, '19000101', dt) % 7 + 1 AS dow) dw     -- 1900-01-01 was a Monday; independent of DATEFIRST
WHERE NOT EXISTS (SELECT 1 FROM dim.[Date] x WHERE x.[date] = d.dt);
GO

/* game plans - names exactly as used by the sales team */
MERGE dim.GamePlan AS t
USING (VALUES
    (1, N'1. Increase ARPU (199 ->350)', 'GP350'),
    (2, N'2. Increase ARPU (49 ->199)',  'GP199'),
    (3, N'3. Increase GA',               'ALL')
) AS s (game_plan_id, game_plan_name, sim_rule)
ON t.game_plan_id = s.game_plan_id
WHEN NOT MATCHED THEN INSERT (game_plan_id, game_plan_name, sim_rule) VALUES (s.game_plan_id, s.game_plan_name, s.sim_rule);
GO

/* doc types */
MERGE dim.DocType AS t
USING (VALUES (N'Thai ID', 1), (N'Others', 0), (N'', 0)) AS s (doc_type, is_thai_id)
ON t.doc_type = s.doc_type
WHEN NOT MATCHED THEN INSERT (doc_type, is_thai_id) VALUES (s.doc_type, s.is_thai_id);
GO

/* =====================================================================
   7. REPORT VIEWS - replace the hard-coded file names in the measures
   ===================================================================== */

/* rpt.AsOf - one row: the latest day in the sales data and the dates that depend on it
   (run-rate day = as-of day - 1) */
CREATE OR ALTER VIEW rpt.AsOf AS
SELECT
    a.as_of_key,
    d.[date]                                    AS as_of_date,
    d.month_key                                 AS current_month_key,
    p.month_key                                 AS previous_month_key,
    d.day_of_month - 1                          AS run_rate_day,          -- _DAY_NO
    CASE WHEN d.day_of_month - 1 <= 7  THEN 1
         WHEN d.day_of_month - 1 <= 14 THEN 2
         WHEN d.day_of_month - 1 <= 21 THEN 3 ELSE 4 END AS current_week
FROM (SELECT MAX(date_key) AS as_of_key FROM fact.SalesDaily) a
JOIN dim.[Date] d ON d.date_key = a.as_of_key
JOIN dim.[Date] p ON p.[date] = DATEADD(MONTH, -1, DATEFROMPARTS(d.[year], d.month_no, 1));
GO

/* rpt.DateFlags - dim.Date + flags for the current / previous month (date table for reports) */
CREATE OR ALTER VIEW rpt.DateFlags AS
SELECT
    d.*,
    CAST(CASE WHEN d.month_key = a.current_month_key  THEN 1 ELSE 0 END AS BIT) AS is_current_month,
    CAST(CASE WHEN d.month_key = a.previous_month_key THEN 1 ELSE 0 END AS BIT) AS is_previous_month,
    CAST(CASE WHEN d.month_key = a.current_month_key AND d.day_of_month <= a.run_rate_day THEN 1 ELSE 0 END AS BIT) AS is_in_run_rate
FROM dim.[Date] d
CROSS JOIN rpt.AsOf a;
GO

/* =====================================================================
   8. Done - quick summary
   ===================================================================== */
SELECT s.name AS [schema], t.name AS [table],
       SUM(p.rows) AS [rows]
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1)
GROUP BY s.name, t.name
ORDER BY s.name, t.name;
GO
