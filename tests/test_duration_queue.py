import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import app as dashboard


class DurationQueueTests(unittest.TestCase):
    def test_bounded_selection_respects_retries_extensions_and_reports_lower_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "queue.sqlite"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.executescript(
                    """
                    CREATE TABLE indexed_files (
                        id INTEGER PRIMARY KEY, source_path TEXT, file_name TEXT,
                        extension TEXT, duration_seconds REAL,
                        duration_probe_attempts INTEGER NOT NULL DEFAULT 0,
                        duration_probe_error TEXT, duration_probe_at TEXT
                    );
                    CREATE TABLE app_metadata (meta_key TEXT PRIMARY KEY, meta_value TEXT);
                    CREATE INDEX idx_indexed_files_duration_attempt_id
                        ON indexed_files (duration_probe_attempts,id) WHERE duration_seconds IS NULL;
                    """
                )
                connection.executemany(
                    "INSERT INTO indexed_files (id,source_path,file_name,extension,duration_probe_attempts,duration_probe_at) VALUES (?,?,?,?,?,?)",
                    [
                        (1, "first.mp4", "first.mp4", ".mp4", 0, None),
                        (2, "second.mp4", "second.mp4", ".MP4", 1, "2020-01-01"),
                        (3, "future.mp4", "future.mp4", ".mp4", 1, "9999-01-01"),
                        (4, "exhausted.mp4", "exhausted.mp4", ".mp4", 3, None),
                        (5, "image.jpg", "image.jpg", ".jpg", 0, None),
                        (6, "third.mp4", "third.mp4", ".mp4", 0, None),
                        (7, "fourth.mp4", "fourth.mp4", ".mp4", 0, None),
                    ],
                )
                connection.commit()
                plan = connection.execute(
                    "EXPLAIN QUERY PLAN SELECT id,source_path,file_name FROM indexed_files "
                    "WHERE duration_seconds IS NULL AND LOWER(extension) IN ('.mp4') "
                    "AND duration_probe_attempts<3 "
                    "AND (duration_probe_at IS NULL OR duration_probe_at<='2026-10-16') "
                    "ORDER BY duration_probe_attempts,id LIMIT 2"
                ).fetchall()
                self.assertIn("idx_indexed_files_duration_attempt_id", str(plan))

            opened = []

            def connect(*_args, **_kwargs):
                connection = sqlite3.connect(database_path)
                connection.row_factory = sqlite3.Row
                opened.append(connection)
                return connection

            processed = []

            def probe(path):
                processed.append(path.name)
                return 12.0, None

            with patch.object(dashboard, "ensure_database"), patch.object(dashboard, "open_db", side_effect=connect), \
                 patch.object(dashboard, "has_ffprobe", return_value=True), \
                 patch.object(dashboard, "probe_duration_seconds", side_effect=probe), \
                 patch.object(dashboard, "ENABLE_VIDEO_DURATION", True), \
                 patch.object(dashboard, "DURATION_MAX_FILES_PER_RUN", 2), \
                 patch.object(dashboard, "DURATION_MAX_ATTEMPTS", 3), \
                 patch.object(dashboard, "DURATION_UPDATE_BATCH_SIZE", 1), \
                 patch.object(dashboard, "VIDEO_EXTENSIONS", {".mp4"}):
                result = dashboard.hydrate_missing_durations()
                with connect() as connection:
                    connection.execute("UPDATE indexed_files SET duration_probe_attempts=3 WHERE id IN (2,7)")
                no_due_rows = dashboard.hydrate_missing_durations()
                with connect() as connection:
                    connection.execute("UPDATE indexed_files SET duration_probe_attempts=3 WHERE id=3")
                empty = dashboard.hydrate_missing_durations()

            self.assertEqual(["first.mp4", "third.mp4"], processed)
            self.assertEqual(2, result["processed_files"])
            self.assertEqual(3, result["pending_files"])
            self.assertFalse(result["pending_files_exact"])
            self.assertEqual(0, no_due_rows["processed_files"])
            self.assertEqual(1, no_due_rows["pending_files"])
            self.assertTrue(no_due_rows["pending_files_exact"])
            self.assertEqual(0, empty["pending_files"])
            self.assertTrue(empty["pending_files_exact"])
            with connect() as connection:
                rows = connection.execute("SELECT id,duration_seconds FROM indexed_files ORDER BY id").fetchall()
                self.assertEqual([12.0, None, None, None, None, 12.0, None],
                                 [row["duration_seconds"] for row in rows])
            for connection in opened:
                connection.close()


if __name__ == "__main__":
    unittest.main()
