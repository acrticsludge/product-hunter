-- Wave 3 canonical opportunity identity: a stable, run-independent identity
-- for every opportunity so that repeated discovery of the same problem
-- resolves to one canonical row instead of a new opp_* identity per run.
--
-- canonical_id is the durable identity of one canonical opportunity. It is
-- derived from the cluster id when the pain points behind the opportunity
-- are clustered, otherwise from a stable hash of the normalized problem key.
-- run_id is deliberately NOT part of the identity, so the same problem
-- discovered in a later run lands on the same row.
--
-- merged_into records the audit trail when one opportunity is merged into
-- another during historical reconciliation. The merged row keeps its own
-- row (and its own id) for provenance; the survivor keeps the evidence,
-- claims and scores.

ALTER TABLE opportunities ADD COLUMN canonical_id TEXT;
ALTER TABLE opportunities ADD COLUMN merged_into TEXT REFERENCES opportunities(opportunity_id);

CREATE INDEX idx_opportunities_canonical ON opportunities(canonical_id);
-- A canonical id may name at most one living opportunity. Null canonical_id
-- rows (pre-migration, not yet reconciled) are excluded.
CREATE UNIQUE INDEX idx_opportunities_one_canonical
    ON opportunities(canonical_id) WHERE canonical_id IS NOT NULL;
