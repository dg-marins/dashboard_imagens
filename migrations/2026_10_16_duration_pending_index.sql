-- Run with psql autocommit enabled, outside an explicit transaction.
-- The dashboard must use the revised bounded duration query before the old index is removed.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_indexed_files_duration_attempt_id
    ON public.indexed_files (duration_probe_attempts, id)
    INCLUDE (extension, duration_probe_at)
    WHERE duration_seconds IS NULL;

CREATE STATISTICS IF NOT EXISTS indexed_files_duration_extension_stats
    ON (lower(extension)) FROM public.indexed_files;

ANALYZE public.indexed_files;

-- Do not remove idx_indexed_files_duration_retry_queue until pg_stat_user_indexes
-- confirms it is unused by every other workload after the application rollout.
