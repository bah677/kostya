-- Момент исключения пользователя из закрытой группы клуба (для аналитики возобновлений).

CREATE TABLE IF NOT EXISTS club_member_exclusions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    excluded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason VARCHAR(64) NOT NULL DEFAULT 'unknown',
    source VARCHAR(64) NOT NULL DEFAULT 'unknown'
);

CREATE INDEX IF NOT EXISTS idx_club_member_exclusions_user_time
    ON club_member_exclusions (user_id, excluded_at DESC);
