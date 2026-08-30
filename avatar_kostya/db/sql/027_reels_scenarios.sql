-- Сценарии Reels + обратная связь (👍 хороший / 👎 не подходит).

CREATE TABLE IF NOT EXISTS reels_scenarios (
    id                  UUID PRIMARY KEY,
    pending_id          UUID REFERENCES telemost_mail_pending (id) ON DELETE SET NULL,
    ref_code            TEXT NOT NULL UNIQUE,
    air_title           TEXT NOT NULL DEFAULT '',
    idea_title          TEXT NOT NULL DEFAULT '',
    idea_score          REAL NOT NULL DEFAULT 0,
    anchor_sec          REAL NOT NULL DEFAULT 0,
    scenario_text       TEXT NOT NULL DEFAULT '',
    transcript_window   TEXT NOT NULL DEFAULT '',
    hook_alternatives   TEXT NOT NULL DEFAULT '',
    plan_json           JSONB NOT NULL DEFAULT '{}'::jsonb,
    rubric_json         JSONB NOT NULL DEFAULT '{}'::jsonb,
    status              TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'approved', 'rejected', 'published')),
    reach               INT,
    feedback_user_id    BIGINT,
    chat_id             BIGINT NOT NULL DEFAULT 0,
    message_id          BIGINT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reels_scenarios_status_reach
    ON reels_scenarios (status, reach DESC NULLS LAST, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_reels_scenarios_pending
    ON reels_scenarios (pending_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_reels_scenarios_ref
    ON reels_scenarios (ref_code);
