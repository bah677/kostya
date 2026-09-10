-- Ритм первой недели (НЕД-1…6): состояние новичка + шаги агента.

CREATE TABLE IF NOT EXISTS club_first_week (
    user_id       BIGINT PRIMARY KEY REFERENCES users (user_id),
    origin        TEXT NOT NULL DEFAULT 'payment',
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deadline_at   TIMESTAMPTZ NULL,
    msgs_group    INT NOT NULL DEFAULT 0,
    step          SMALLINT NOT NULL DEFAULT 0,
    last_step_at  TIMESTAMPTZ NULL,
    done_at       TIMESTAMPTZ NULL,
    ended_at      TIMESTAMPTZ NULL,
    step4_variant TEXT NULL,
    -- 'air' | 'holdout' для сплита шага 4; NULL = ещё не назначен
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_club_first_week_open
    ON club_first_week (started_at)
    WHERE ended_at IS NULL AND done_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_club_first_week_step
    ON club_first_week (step)
    WHERE ended_at IS NULL;
