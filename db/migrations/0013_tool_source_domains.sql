-- Persist the verified hosts touched by each tool call. Search adapters use
-- query text as query_or_operation, so source-domain evidence must travel in
-- its own field instead of being guessed from that text at gate time.
ALTER TABLE tool_calls
    ADD COLUMN source_domains_json TEXT NOT NULL DEFAULT '[]';
