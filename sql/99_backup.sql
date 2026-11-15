/* Backup BMA_East to a dated .bak file in SQL Server's default backup folder
   (usually C:\Program Files\Microsoft SQL Server\MSSQL16.SQLEXPRESS\MSSQL\Backup).
   Run in SSMS after each monthly upload. Copy the .bak somewhere safe (e.g. OneDrive). */
DECLARE @file NVARCHAR(260) = N'BMA_East_' + FORMAT(SYSDATETIME(), 'yyyyMMdd_HHmm') + N'.bak';
BACKUP DATABASE BMA_East TO DISK = @file WITH INIT, CHECKSUM, STATS = 10;

-- where it was written:
SELECT TOP (1) bmf.physical_device_name AS backup_file, bs.backup_finish_date
FROM msdb.dbo.backupset bs
JOIN msdb.dbo.backupmediafamily bmf ON bmf.media_set_id = bs.media_set_id
WHERE bs.database_name = N'BMA_East'
ORDER BY bs.backup_finish_date DESC;
