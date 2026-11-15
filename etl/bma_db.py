"""Shared helpers for the BMA_East load scripts (SQL Server Express, Windows login)."""
import datetime as dt
import pyodbc

DEFAULT_SERVER = r"localhost\SQLEXPRESS"
DEFAULT_DB = "BMA_East"


def connect(server=DEFAULT_SERVER, database=DEFAULT_DB):
    drivers = [d for d in pyodbc.drivers() if "ODBC Driver" in d and "SQL Server" in d]
    if not drivers:
        raise SystemExit("No 'ODBC Driver xx for SQL Server' installed.")
    driver = sorted(drivers)[-1]                       # newest, e.g. ODBC Driver 18 for SQL Server
    conn = pyodbc.connect(
        f"DRIVER={{{driver}}};SERVER={server};DATABASE={database};"
        "Trusted_Connection=yes;TrustServerCertificate=yes;",
        autocommit=False,
    )
    conn.cursor().execute("SET QUOTED_IDENTIFIER ON; SET ANSI_NULLS ON;")
    return conn


def temp_table(cur, name, columns_sql, rows):
    """Create #name with the given column definitions and bulk-insert rows (list of tuples)."""
    cur.execute(f"IF OBJECT_ID('tempdb..#{name}') IS NOT NULL DROP TABLE #{name}; CREATE TABLE #{name} ({columns_sql});")
    if rows:
        ncol = len(rows[0])
        cur.fast_executemany = True
        cur.executemany(f"INSERT INTO #{name} VALUES ({','.join('?' * ncol)})", rows)
        cur.fast_executemany = False


def log_start(conn, table, source_file=None, month_key=None, file_size=None, file_modified=None):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO etl.LoadLog (target_table, source_file, month_key, file_size, file_modified) "
        "OUTPUT INSERTED.load_id VALUES (?, ?, ?, ?, ?)",
        table, source_file, month_key, file_size, file_modified,
    )
    load_id = cur.fetchone()[0]
    conn.commit()
    return load_id


def log_end(conn, load_id, status, rows=None, message=None):
    conn.rollback()                                    # in case the caller failed mid-transaction
    conn.cursor().execute(
        "UPDATE etl.LoadLog SET [status] = ?, rows_loaded = ?, message = ?, finished_at = SYSDATETIME() WHERE load_id = ?",
        status, rows, (message or "")[:1000], load_id,
    )
    conn.commit()


def mtime(path):
    import os
    return dt.datetime.fromtimestamp(int(os.path.getmtime(path)))


def norm_name(s):
    """Normalise a store name for matching: trim, collapse spaces, lower-case."""
    if s is None:
        return ""
    return " ".join(str(s).split()).lower()
