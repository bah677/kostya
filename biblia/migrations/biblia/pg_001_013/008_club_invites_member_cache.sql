-- Приглашения в закрытый клуб + кэш участников для ночного аудита лицензий.

CREATE TABLE IF NOT EXISTS club_invites (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL,
    invite_link TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    used        BOOLEAN NOT NULL DEFAULT FALSE,
    revoked     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_club_invites_expired_unused
    ON club_invites (expires_at)
    WHERE used = FALSE AND revoked = FALSE;

CREATE TABLE IF NOT EXISTS club_group_member_cache (
    user_id    BIGINT PRIMARY KEY,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_club_group_member_cache_updated
    ON club_group_member_cache (updated_at DESC);
