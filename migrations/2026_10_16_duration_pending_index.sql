-- Run with psql autocommit enabled, outside an explicit transaction.
-- The dashboard must use the revised bounded duration query before the old index is removed.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_indexed_files_duration_pending_id
    ON public.indexed_files (id)
    INCLUDE (extension, duration_probe_attempts, duration_probe_at)
    WHERE duration_seconds IS NULL;

ANALYZE public.indexed_files;

-- Do not remove idx_indexed_files_duration_retry_queue until pg_stat_user_indexes
-- confirms it is unused by every other workload after the application rollout.
