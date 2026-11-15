/* =====================================================================
   BMA East - multi-channel model (7-11 + RTR in the same tables).
   Run after 01 and 02. Safe to run again.

   Key idea
     dim.Outlet  = one real shop (channel 7-11 / RTR / Other)
     dim.Store   = one PARTNER_CODE (a 7-11 shop has 1 code, an RTR shop has 1 or 2: True / Dtac)
     fact.SalesDaily keeps partner_code -> Store -> Outlet gives the channel, owner, map position.
   Targets, game plans, close gap and market share are kept per OUTLET, with a channel column
   where both channels share the table.

   This script rebuilds the plan/target tables, so it refuses to run once they contain data.
   ===================================================================== */
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO
USE BMA_East;
GO

/* ---------- 0. Safety check ----------
   Sales data (fact.SalesDaily) and the partner-code list (dim.Store) are kept as they are.
   The script only stops if it would throw away data: plan/target rows in the old layout,
   or shop details (owner, segment, map position ...) already stored on dim.Store. */
DECLARE @blocked BIT = 0;
IF OBJECT_ID(N'dim.Outlet') IS NULL
BEGIN
    IF EXISTS (SELECT 1 FROM fact.TargetOwner) OR EXISTS (SELECT 1 FROM fact.TargetStore)
       OR EXISTS (SELECT 1 FROM fact.GamePlan) OR EXISTS (SELECT 1 FROM fact.CloseGap)
        SET @blocked = 1;
    -- old shop columns on dim.Store (checked with dynamic SQL because this script removes them)
    IF @blocked = 0 AND COL_LENGTH(N'dim.Store', N'owner_id') IS NOT NULL
        EXEC sp_executesql
            N'IF EXISTS (SELECT 1 FROM dim.Store WHERE owner_id IS NOT NULL OR segment IS NOT NULL OR latitude IS NOT NULL
                                                    OR type_sim IS NOT NULL OR ranking_priority IS NOT NULL) SET @b = 1;',
            N'@b BIT OUTPUT', @b = @blocked OUTPUT;
END
IF @blocked = 1
BEGIN
    RAISERROR(N'03_channel_model: plan/target tables or shop details already contain data - ask for a migration script instead.', 16, 1);
    SET NOEXEC ON;     -- stop the rest of the script
END
GO

/* ---------- 1. dim.Outlet - one row per real shop ---------- */
IF OBJECT_ID(N'dim.Outlet') IS NULL
CREATE TABLE dim.Outlet (
    outlet_id        INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_Outlet PRIMARY KEY,
    channel          VARCHAR(10)   NOT NULL CONSTRAINT CK_Outlet_channel CHECK (channel IN ('7-11', 'RTR', 'Other')),
    outlet_key       NVARCHAR(60)  NOT NULL CONSTRAINT UQ_Outlet_key UNIQUE,   -- '7-11:<code>' / 'RTR:<first code>' / 'Other:<code>'
    outlet_name      NVARCHAR(200) NOT NULL,
    owner_id         INT           NULL CONSTRAINT FK_Outlet_Owner REFERENCES dim.Owner(owner_id),
    [status]         NVARCHAR(30)  NULL,
    segment          NVARCHAR(50)  NULL,     -- 7-11 Store Profile
    type_sim         NVARCHAR(50)  NULL,     -- 7-11 'Type Sim'  /  RTR 'TARGET'
    ranking_priority NVARCHAR(100) NULL,     -- 7-11
    zone_name        NVARCHAR(100) NULL,     -- 7-11
    rs_name          NVARCHAR(100) NULL,     -- RTR (Target RTR sheet)
    district_en      NVARCHAR(100) NULL,
    sub_district_en  NVARCHAR(100) NULL,
    latitude         DECIMAL(9,6)  NULL,
    longitude        DECIMAL(9,6)  NULL,
    in_profile       BIT           NOT NULL CONSTRAINT DF_Outlet_profile DEFAULT (0),  -- 1 = listed in a profile sheet
    updated_at       DATETIME2(0)  NOT NULL CONSTRAINT DF_Outlet_updated DEFAULT (SYSDATETIME())
);
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Outlet_channel_owner')
    CREATE INDEX IX_Outlet_channel_owner ON dim.Outlet(channel, owner_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Outlet_name')
    CREATE INDEX IX_Outlet_name ON dim.Outlet(outlet_name);
GO

/* ---------- 2. dim.Owner - remember which channel an owner works in ---------- */
IF COL_LENGTH(N'dim.Owner', N'channel') IS NULL
    ALTER TABLE dim.Owner ADD channel VARCHAR(10) NULL;
GO

/* ---------- 3. dim.Store becomes the partner-code level ----------
   shop attributes move to dim.Outlet; the code keeps its link, company and sales-file details */
IF COL_LENGTH(N'dim.Store', N'outlet_id') IS NULL
    ALTER TABLE dim.Store ADD outlet_id INT NULL CONSTRAINT FK_Store_Outlet REFERENCES dim.Outlet(outlet_id),
                              company NVARCHAR(20) NULL,          -- TRUE / DTAC (from the RTR code column it came from)
                              sales_channel NVARCHAR(50) NULL;    -- TDS_CHANNEL in the sales file
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Store_outlet')
    CREATE INDEX IX_Store_outlet ON dim.Store(outlet_id);
GO
-- drop the shop attributes that now live in dim.Outlet
IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Store_owner')       DROP INDEX IX_Store_owner ON dim.Store;
IF OBJECT_ID(N'dim.FK_Store_Owner', N'F') IS NOT NULL                     ALTER TABLE dim.Store DROP CONSTRAINT FK_Store_Owner;
GO
DECLARE @c NVARCHAR(128);
DECLARE cols CURSOR LOCAL FAST_FORWARD FOR
    SELECT name FROM sys.columns WHERE object_id = OBJECT_ID(N'dim.Store')
      AND name IN (N'owner_id', N'segment', N'status', N'type_sim', N'ranking_priority', N'zone_name', N'latitude', N'longitude');
OPEN cols; FETCH NEXT FROM cols INTO @c;
WHILE @@FETCH_STATUS = 0
BEGIN
    EXEC (N'ALTER TABLE dim.Store DROP COLUMN ' + @c);
    FETCH NEXT FROM cols INTO @c;
END
CLOSE cols; DEALLOCATE cols;
GO

/* ---------- 4. Game plans for both channels ---------- */
IF OBJECT_ID(N'fact.GamePlan') IS NOT NULL AND COL_LENGTH(N'fact.GamePlan', N'outlet_id') IS NULL
    DROP TABLE fact.GamePlan;                                  -- old shape (by partner_code), empty
IF OBJECT_ID(N'dim.GamePlan') IS NOT NULL AND COL_LENGTH(N'dim.GamePlan', N'channel') IS NULL
    DROP TABLE dim.GamePlan;                                   -- old shape (7-11 only)
GO
IF OBJECT_ID(N'dim.GamePlan') IS NULL
CREATE TABLE dim.GamePlan (
    game_plan_id     SMALLINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_GamePlan PRIMARY KEY,
    channel          VARCHAR(10)   NOT NULL CONSTRAINT CK_GamePlan_channel CHECK (channel IN ('7-11', 'RTR')),
    game_plan_name   NVARCHAR(100) NOT NULL,
    sim_rule         VARCHAR(10)   NOT NULL CONSTRAINT DF_GamePlan_rule DEFAULT ('ALL')
                                   CONSTRAINT CK_GamePlan_rule CHECK (sim_rule IN ('GP350', 'GP199', 'ALL')),
    CONSTRAINT UQ_GamePlan UNIQUE (channel, game_plan_name)
);
GO
/* 7-11 plans (count only certain SIM types - same as the *GP measures).
   RTR plans are added by the loader from the RTR Gameplan sheet (they count all SIM types, like *TD RTR Diff Commit). */
MERGE dim.GamePlan AS t
USING (VALUES ('7-11', N'1. Increase ARPU (199 ->350)', 'GP350'),
              ('7-11', N'2. Increase ARPU (49 ->199)',  'GP199'),
              ('7-11', N'3. Increase GA',               'ALL')) AS s (channel, game_plan_name, sim_rule)
ON t.channel = s.channel AND t.game_plan_name = s.game_plan_name
WHEN NOT MATCHED THEN INSERT (channel, game_plan_name, sim_rule) VALUES (s.channel, s.game_plan_name, s.sim_rule);
GO
IF OBJECT_ID(N'fact.GamePlan') IS NULL
CREATE TABLE fact.GamePlan (
    month_key        INT           NOT NULL,
    outlet_id        INT           NOT NULL CONSTRAINT FK_GP_Outlet REFERENCES dim.Outlet(outlet_id),
    game_plan_id     SMALLINT      NOT NULL CONSTRAINT FK_GP_Plan   REFERENCES dim.GamePlan(game_plan_id),
    commit_ga        INT           NOT NULL CONSTRAINT DF_GP_commit DEFAULT (0),
    commit_ga_daily  INT           NULL,
    start_date       DATE          NULL,
    end_date         DATE          NULL,
    CONSTRAINT PK_GamePlan PRIMARY KEY (month_key, outlet_id, game_plan_id)
);
GO

/* ---------- 5. Targets ---------- */
-- owner level: 'Target By AE Name' (7-11) + 'RTR Target By AE Name' (RTR) in one table
IF OBJECT_ID(N'fact.TargetOwner') IS NOT NULL AND COL_LENGTH(N'fact.TargetOwner', N'channel') IS NULL
    DROP TABLE fact.TargetOwner;
GO
IF OBJECT_ID(N'fact.TargetOwner') IS NULL
CREATE TABLE fact.TargetOwner (
    month_key        INT           NOT NULL,
    channel          VARCHAR(10)   NOT NULL CONSTRAINT CK_TgtOwner_channel CHECK (channel IN ('7-11', 'RTR')),
    owner_id         INT           NOT NULL CONSTRAINT FK_TgtOwner_Owner REFERENCES dim.Owner(owner_id),
    target_type      NVARCHAR(30)  NOT NULL,      -- Mass&Migrant / Tourist
    target_ga        INT           NOT NULL CONSTRAINT DF_TgtOwner_ga DEFAULT (0),
    target_m1        DECIMAL(18,2) NOT NULL CONSTRAINT DF_TgtOwner_m1 DEFAULT (0),
    CONSTRAINT PK_TargetOwner PRIMARY KEY (month_key, channel, owner_id, target_type)
);
GO
-- shop level: 'Target All Type' (7-11)
IF OBJECT_ID(N'fact.TargetStore') IS NOT NULL AND COL_LENGTH(N'fact.TargetStore', N'outlet_id') IS NULL
    DROP TABLE fact.TargetStore;
GO
IF OBJECT_ID(N'fact.TargetStore') IS NULL
CREATE TABLE fact.TargetStore (
    month_key        INT           NOT NULL,
    outlet_id        INT           NOT NULL CONSTRAINT FK_TgtStore_Outlet REFERENCES dim.Outlet(outlet_id),
    target_ga        INT           NOT NULL CONSTRAINT DF_TgtStore_ga DEFAULT (0),
    target_m1        DECIMAL(18,2) NOT NULL CONSTRAINT DF_TgtStore_m1 DEFAULT (0),
    target_ga_daily  AS CAST(CEILING(target_ga / 30.0) AS INT) PERSISTED,              -- ROUNDUP(target / 30)
    target_m1_daily  AS CAST(CEILING(target_m1 / 30.0) AS DECIMAL(18,0)) PERSISTED,
    CONSTRAINT PK_TargetStore PRIMARY KEY (month_key, outlet_id)
);
GO

/* ---------- 6. Close Gap (7-11) per outlet ---------- */
IF OBJECT_ID(N'fact.CloseGap') IS NOT NULL AND COL_LENGTH(N'fact.CloseGap', N'outlet_id') IS NULL
    DROP TABLE fact.CloseGap;
GO
IF OBJECT_ID(N'fact.CloseGap') IS NULL
CREATE TABLE fact.CloseGap (
    month_key        INT           NOT NULL,
    outlet_id        INT           NOT NULL CONSTRAINT FK_CG_Outlet REFERENCES dim.Outlet(outlet_id),
    commit_ga        INT           NOT NULL CONSTRAINT DF_CG_commit DEFAULT (0),
    CONSTRAINT PK_CloseGap PRIMARY KEY (month_key, outlet_id)
);
GO

/* ---------- 7. Market share (RTR, from 'Battle field.xlsx' Sheet5). 7-11 = 100% in the view below ---------- */
IF OBJECT_ID(N'fact.MarketShare') IS NULL
CREATE TABLE fact.MarketShare (
    month_key        INT           NOT NULL,
    outlet_id        INT           NOT NULL CONSTRAINT FK_MS_Outlet REFERENCES dim.Outlet(outlet_id),
    tumbon           NVARCHAR(100) NULL,
    key_location     NVARCHAR(100) NULL,
    ga_true          INT           NULL,          -- 'GA M+M T'
    ga_ais           INT           NULL,          -- 'GA Ais'
    market_size      INT           NULL,          -- 'Market Size'
    total_sale       INT           NULL,          -- 'Total Sale'
    share_true       DECIMAL(9,6)  NULL,          -- '%True'  (0..1)
    share_ais        DECIMAL(9,6)  NULL,          -- '%AIS'
    visibility_share DECIMAL(9,6)  NULL,
    shelf_share      DECIMAL(9,6)  NULL,
    CONSTRAINT PK_MarketShare PRIMARY KEY (month_key, outlet_id)
);
GO

/* ---------- 8. AP1D by price band (RTR 'AP1D Top' cards; later also commission) ---------- */
IF OBJECT_ID(N'fact.SalesAP1DBand') IS NULL
CREATE TABLE fact.SalesAP1DBand (
    date_key         INT           NOT NULL CONSTRAINT FK_Band_Date    REFERENCES dim.[Date](date_key),
    partner_code     INT           NOT NULL CONSTRAINT FK_Band_Store   REFERENCES dim.Store(partner_code),
    sim_type         NVARCHAR(100) NOT NULL CONSTRAINT FK_Band_SimType REFERENCES dim.SimType(sim_type),
    doc_type         NVARCHAR(30)  NOT NULL CONSTRAINT FK_Band_DocType REFERENCES dim.DocType(doc_type),
    price_band       VARCHAR(10)   NOT NULL CONSTRAINT CK_Band CHECK (price_band IN ('1-49','50-99','100-119','120-149','150-199','200-249','250-299','300UP')),
    ap1d             INT           NOT NULL,
    ap1d_amount      DECIMAL(18,2) NOT NULL,
    load_id          INT           NULL,
    CONSTRAINT PK_SalesAP1DBand PRIMARY KEY CLUSTERED (date_key, partner_code, sim_type, doc_type, price_band)
);
GO

/* ---------- 9. Views ---------- */
/* one row per partner code with its outlet + channel (handy join for exports / reports) */
CREATE OR ALTER VIEW rpt.StoreOutlet AS
SELECT s.partner_code, s.company, s.sales_channel, s.source_partner_name,
       o.outlet_id, o.channel, o.outlet_name, o.owner_id, ow.owner_name,
       o.segment, o.type_sim, o.ranking_priority, o.zone_name, o.rs_name, o.[status],
       COALESCE(o.district_en, s.district_en) AS district_en,
       COALESCE(o.sub_district_en, s.sub_district_en) AS sub_district_en,
       o.latitude, o.longitude, o.in_profile
FROM dim.Store s
LEFT JOIN dim.Outlet o  ON o.outlet_id = s.outlet_id
LEFT JOIN dim.Owner  ow ON ow.owner_id = o.owner_id;
GO
/* latest market share per outlet; 7-11 outlets are always 100% True */
CREATE OR ALTER VIEW rpt.OutletMarketShare AS
SELECT o.outlet_id, o.channel,
       CASE WHEN o.channel = '7-11' THEN NULL ELSE m.month_key END AS month_key,
       m.tumbon, m.key_location,
       m.ga_true, m.ga_ais, m.market_size, m.total_sale,
       CASE WHEN o.channel = '7-11' THEN CAST(1 AS DECIMAL(9,6)) ELSE m.share_true END AS share_true,
       CASE WHEN o.channel = '7-11' THEN CAST(0 AS DECIMAL(9,6)) ELSE m.share_ais  END AS share_ais,
       m.visibility_share, m.shelf_share
FROM dim.Outlet o
OUTER APPLY (SELECT TOP (1) * FROM fact.MarketShare x WHERE x.outlet_id = o.outlet_id ORDER BY x.month_key DESC) m
WHERE o.channel = '7-11' OR m.outlet_id IS NOT NULL;
GO

SET NOEXEC OFF;
GO
SELECT s.name AS [schema], o.name AS [object], o.type_desc
FROM sys.objects o JOIN sys.schemas s ON s.schema_id = o.schema_id
WHERE o.type IN ('U', 'V') AND s.name IN ('dim', 'fact', 'etl', 'rpt')
ORDER BY s.name, o.type, o.name;
GO
