-- Состояние клубных проактивных рассылок в личку (дайджест, цитаты, лимиты).

CREATE TABLE IF NOT EXISTS member_outreach_state (
    user_id BIGINT PRIMARY KEY REFERENCES users (user_id) ON DELETE CASCADE,
    outreach_paused_until TIMESTAMPTZ,
    suppression_level INT NOT NULL DEFAULT 0,
    complaints_detected INT NOT NULL DEFAULT 0,
    last_complaint_at TIMESTAMPTZ,
    pilot_cohort BOOLEAN NOT NULL DEFAULT FALSE,
    last_digest_dm_at TIMESTAMPTZ,
    last_scripture_dm_at TIMESTAMPTZ,
    proactive_sent_date DATE,
    proactive_sent_count INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_member_outreach_pilot
    ON member_outreach_state (pilot_cohort)
    WHERE pilot_cohort = TRUE;

CREATE INDEX IF NOT EXISTS idx_member_outreach_paused
    ON member_outreach_state (outreach_paused_until)
    WHERE outreach_paused_until IS NOT NULL;
