-- Идемпотентность backfill и повторных касаний.

DELETE FROM attribution_touches a
USING attribution_touches b
WHERE a.id > b.id
  AND a.user_id = b.user_id
  AND a.touch_key = b.touch_key
  AND a.source_type = b.source_type
  AND a.created_at = b.created_at;

CREATE UNIQUE INDEX IF NOT EXISTS idx_attribution_touches_dedup
    ON attribution_touches (user_id, touch_key, source_type, created_at);
