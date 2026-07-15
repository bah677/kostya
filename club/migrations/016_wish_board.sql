-- Доска желаний (благотворительные просьбы участников клуба).

CREATE TABLE IF NOT EXISTS wish_requests (
    id SERIAL PRIMARY KEY,
    requester_user_id BIGINT NOT NULL,
    is_anonymous BOOLEAN NOT NULL DEFAULT FALSE,
    gift_type VARCHAR(32) NOT NULL,
    description TEXT NOT NULL,
    urgency VARCHAR(16) NOT NULL DEFAULT 'normal',
    status VARCHAR(32) NOT NULL DEFAULT 'pending_moderation',
    donor_user_id BIGINT,
    moderator_user_id BIGINT,
    reject_reason TEXT,
    expires_at TIMESTAMPTZ NOT NULL,
    taken_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    confirmed_at TIMESTAMPTZ,
    donor_rating SMALLINT,
    admin_notice_message_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wish_requests_status ON wish_requests (status);
CREATE INDEX IF NOT EXISTS idx_wish_requests_requester ON wish_requests (requester_user_id);
CREATE INDEX IF NOT EXISTS idx_wish_requests_donor ON wish_requests (donor_user_id);
CREATE INDEX IF NOT EXISTS idx_wish_requests_expires ON wish_requests (expires_at)
    WHERE status IN ('open', 'pending_moderation');

CREATE TABLE IF NOT EXISTS wish_events (
    id SERIAL PRIMARY KEY,
    wish_id INT NOT NULL REFERENCES wish_requests(id) ON DELETE CASCADE,
    actor_user_id BIGINT,
    event_type VARCHAR(64) NOT NULL,
    meta JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_wish_events_wish ON wish_events (wish_id, created_at);

CREATE TABLE IF NOT EXISTS user_generosity_stats (
    user_id BIGINT PRIMARY KEY,
    wishes_completed_as_donor INT NOT NULL DEFAULT 0,
    wishes_completed_as_requester INT NOT NULL DEFAULT 0,
    rating_sum INT NOT NULL DEFAULT 0,
    rating_count INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
