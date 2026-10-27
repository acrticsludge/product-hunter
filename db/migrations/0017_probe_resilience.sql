-- Wave 4c: probe resilience counters.
-- A probe that cannot progress pauses itself and records why, so an operator
-- can resume instead of watching it spin in backoff. Additive only: two new
-- columns on probes. No existing column changes meaning.

ALTER TABLE probes ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0;

ALTER TABLE probes ADD COLUMN paused_reason TEXT;

-- Backfill: a probe that is already PAUSED has paused because it could not
-- progress; record a reason so the cause is visible without digging into logs.
UPDATE probes
   SET paused_reason = COALESCE(last_error, 'paused')
 WHERE status = 'PAUSED' AND paused_reason IS NULL;
