import os
import sqlite3
import sys
from pathlib import Path

try:
    import psycopg2
    from psycopg2 import sql
    from psycopg2.extras import execute_values
except ImportError:
    print("Instale as dependencias antes: pip install -r requirements.txt")
    raise


SQLITE_DB = Path(os.getenv("IMAGE_DASHBOARD_DB", Path(__file__).resolve().parent / "db" / "dashboard_imagens.db"))
POSTGRES_DSN = os.getenv("IMAGE_DASHBOARD_POSTGRES_DSN", "").strip()
BATCH_SIZE = int(os.getenv("IMAGE_DASHBOARD_MIGRATION_BATCH_SIZE", "5000"))


def rows_in_batches(cursor, size):
    while True:
        rows = cursor.fetchmany(size)
        if not rows:
            break
        yield rows


def ensure_postgres_schema():
    os.environ["IMAGE_DASHBOARD_DB_ENGINE"] = "postgres"
    import app

    app.DB_ENGINE = "postgres"
    app.POSTGRES_DSN = POSTGRES_DSN
    app.ensure_database()


def copy_table(sqlite_conn, pg_conn, table, columns, conflict_target, update_columns):
    select_sql = f"SELECT {', '.join(columns)} FROM {table} ORDER BY 1"
    sqlite_cursor = sqlite_conn.execute(select_sql)
    placeholders = ", ".join(columns)
    conflict_sql = ", ".join(conflict_target)
    update_sql = ", ".join(f"{column} = excluded.{column}" for column in update_columns)
    insert_sql = f"""
        INSERT INTO {table} ({placeholders})
        VALUES %s
        ON CONFLICT ({conflict_sql}) DO UPDATE SET {update_sql}
    """
    total = 0
    with pg_conn.cursor() as pg_cursor:
        for batch in rows_in_batches(sqlite_cursor, BATCH_SIZE):
            values = [tuple(row[column] for column in columns) for row in batch]
            execute_values(pg_cursor, insert_sql, values, page_size=BATCH_SIZE)
            pg_conn.commit()
            total += len(values)
            print(f"{table}: {total} registros migrados")
    return total


def reset_sequence(pg_conn, table):
    with pg_conn.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                """
                SELECT setval(
                    pg_get_serial_sequence(%s, 'id'),
                    COALESCE((SELECT MAX(id) FROM {}), 1)
                )
                """
            ).format(sql.Identifier(table)),
            (table,),
        )
    pg_conn.commit()


def main():
    if not SQLITE_DB.exists():
        print(f"Banco SQLite nao encontrado: {SQLITE_DB}")
        return 1
    if not POSTGRES_DSN:
        print("Configure IMAGE_DASHBOARD_POSTGRES_DSN antes de migrar.")
        return 1

    ensure_postgres_schema()
    sqlite_conn = sqlite3.connect(SQLITE_DB)
    sqlite_conn.row_factory = sqlite3.Row
    pg_conn = psycopg2.connect(POSTGRES_DSN)

    try:
        copy_table(
            sqlite_conn,
            pg_conn,
            "indexed_files",
            [
                "id",
                "garage",
                "vehicle",
                "camera",
                "capture_date",
                "file_name",
                "extension",
                "relative_dir",
                "relative_file_path",
                "source_path",
                "real_path",
                "size_bytes",
                "duration_seconds",
                "modified_at",
                "indexed_at",
                "last_seen_scan_id",
            ],
            ["relative_file_path"],
            [
                "garage",
                "vehicle",
                "camera",
                "capture_date",
                "file_name",
                "extension",
                "relative_dir",
                "source_path",
                "real_path",
                "size_bytes",
                "duration_seconds",
                "modified_at",
                "indexed_at",
                "last_seen_scan_id",
            ],
        )
        copy_table(
            sqlite_conn,
            pg_conn,
            "camera_inventory",
            ["id", "garage", "vehicle", "camera", "source_dir", "real_dir", "indexed_at", "last_seen_scan_id"],
            ["source_dir"],
            ["garage", "vehicle", "camera", "real_dir", "indexed_at", "last_seen_scan_id"],
        )
        copy_table(
            sqlite_conn,
            pg_conn,
            "app_metadata",
            ["meta_key", "meta_value"],
            ["meta_key"],
            ["meta_value"],
        )
        copy_table(
            sqlite_conn,
            pg_conn,
            "vehicle_camera_day_summary",
            [
                "garage",
                "vehicle",
                "camera",
                "capture_date",
                "total_files",
                "total_size_bytes",
                "latest_file",
                "updated_at",
            ],
            ["garage", "vehicle", "camera", "capture_date"],
            ["total_files", "total_size_bytes", "latest_file", "updated_at"],
        )
        reset_sequence(pg_conn, "indexed_files")
        reset_sequence(pg_conn, "camera_inventory")
    finally:
        sqlite_conn.close()
        pg_conn.close()

    print("Migracao concluida.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
