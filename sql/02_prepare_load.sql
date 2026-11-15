/* =====================================================================
   BMA East - additions needed by the load scripts (run after 01).
   Safe to run again.
   ===================================================================== */
SET ANSI_NULLS ON;
SET QUOTED_IDENTIFIER ON;
GO
USE BMA_East;
GO

/* Store name exactly as it appears in the sales txt (PARTNER_NAME).
   The Game Plan / Close Gap sheets refer to stores by name, so we match on
   either the Store Profile name or this one. */
IF COL_LENGTH(N'dim.Store', N'source_partner_name') IS NULL
    ALTER TABLE dim.Store ADD source_partner_name NVARCHAR(200) NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Store_source_name')
    CREATE INDEX IX_Store_source_name ON dim.Store(source_partner_name);
GO

/* Remember which file version was loaded, so an unchanged file is skipped */
IF COL_LENGTH(N'etl.LoadLog', N'file_size') IS NULL
    ALTER TABLE etl.LoadLog ADD file_size BIGINT NULL, file_modified DATETIME2(0) NULL;
GO

/* SIM_TYPE -> SIM_TYPE_GROUP mapping (business grouping of SIM types).
   Edit this table when a new SIM type appears; the loader uses it for new SIM types
   and you can re-apply it with the UPDATE at the bottom. */
IF OBJECT_ID(N'etl.SimTypeGroupMap') IS NULL
CREATE TABLE etl.SimTypeGroupMap (
    sim_type        NVARCHAR(100) NOT NULL CONSTRAINT PK_SimTypeGroupMap PRIMARY KEY,
    sim_type_group  NVARCHAR(50)  NOT NULL
);
GO
MERGE etl.SimTypeGroupMap AS t
USING (VALUES
    (N'7-11 READY NET 49',                    N'Mass 49'),
    (N'HC_dtac 7-11 49B/7D',                  N'Mass 49'),
    (N'7-11 READY NET 99',                    N'Mass 99'),
    (N'7-11 READY NET 199',                   N'Mass 199'),
    (N'HC_dtac 7-11 199B/30D',                N'Mass 199'),
    (N'READY NET 350',                        N'Mass 350'),
    (N'MM 49B (7-11)',                        N'Migrant 49'),
    (N'CM 49B (7-11)',                        N'Migrant 49'),
    (N'MM 199B (7-11)',                       N'Migrant 199'),
    (N'CM 199B (7-11)',                       N'Migrant 199'),
    (N'dtac Myanmar 99 THB 7 days (7-11)',    N'Migrant 99'),
    (N'7-11 MYANMAR 5G SIM',                  N'Migrant 99'),
    (N'dtac Cambodia 99 THB 7 days (7-11)',   N'Migrant 99'),
    (N'7-11 MYANMAR 5G SIM 99',               N'Migrant 99'),
    (N'INFINITE SIM 8DAYS 7-11',              N'Tourist 399'),
    (N'Infinite 8D 399B (EN 7-11)',           N'Tourist 399'),
    (N'INFINITE SIM 15DAYS 7-11',             N'Tourist 699'),
    (N'Infinite 15D 699B (EN 7-11)',          N'Tourist 699'),
    (N'INFINITE SIM 30DAYS',                  N'Tourist 999'),
    (N'TRAVEL SIM',                           N'TRAVEL SIM'),
    (N'7-11 DEVICE BUNDLING',                 N'7-11 DEVICE BUNDLING'),
    (N'SOCIAL SIM',                           N'SOCIAL SIM')
) AS s (sim_type, sim_type_group)
ON t.sim_type = s.sim_type
WHEN MATCHED AND t.sim_type_group <> s.sim_type_group THEN UPDATE SET sim_type_group = s.sim_type_group
WHEN NOT MATCHED THEN INSERT (sim_type, sim_type_group) VALUES (s.sim_type, s.sim_type_group);
GO

/* Re-apply the mapping to SIM types already in dim.SimType (anything unmapped = 'Other'),
   and keep the game-plan flags in line with the groups (same SIM lists as the *GP measures). */
UPDATE st
SET sim_type_group = COALESCE(m.sim_type_group, N'Other'),
    is_gp_350 = CASE WHEN COALESCE(m.sim_type_group, N'') = N'Mass 350' THEN 1 ELSE 0 END,
    is_gp_199 = CASE WHEN COALESCE(m.sim_type_group, N'') = N'Mass 199' THEN 1 ELSE 0 END
FROM dim.SimType st
LEFT JOIN etl.SimTypeGroupMap m ON m.sim_type = st.sim_type;
GO

SELECT N'ready' AS [status],
       (SELECT COUNT(*) FROM etl.SimTypeGroupMap) AS sim_type_mappings,
       COL_LENGTH(N'dim.Store', N'source_partner_name') AS source_name_col;
GO
