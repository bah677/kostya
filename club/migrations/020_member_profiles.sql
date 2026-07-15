-- Профиль участника клуба для member-агента (онбординг, трекинг, проактив, продление).

CREATE TABLE IF NOT EXISTS member_profiles (
    user_id BIGINT PRIMARY KEY REFERENCES users (user_id) ON DELETE CASCADE,
    joined_at TIMESTAMPTZ,
    license_expires_at TIMESTAMPTZ,
    onboarding_stage VARCHAR(32) NOT NULL DEFAULT 'not_started',
  -- not_started | started | active
    stated_goals TEXT,
    topics_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    materials_sent_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_dm_at TIMESTAMPTZ,
    last_group_activity_at TIMESTAMPTZ,
    proactive_cooldown_until TIMESTAMPTZ,
    proactive_ignored_streak INT NOT NULL DEFAULT 0,
    renewal_state VARCHAR(32) NOT NULL DEFAULT 'none',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_member_profiles_onboarding
    ON member_profiles (onboarding_stage)
    WHERE onboarding_stage IN ('not_started', 'started');

CREATE INDEX IF NOT EXISTS idx_member_profiles_expires
    ON member_profiles (license_expires_at)
    WHERE license_expires_at IS NOT NULL;

CREATE TABLE IF NOT EXISTS member_profile_events (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    event_type VARCHAR(64) NOT NULL,
    meta JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_member_profile_events_user
    ON member_profile_events (user_id, created_at DESC);
