-- Agency core schema (PostgreSQL database: agency)

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    display_name    TEXT NOT NULL,
    role            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    schedule_cron   TEXT,
    config_json     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS runs (
    id              BIGSERIAL PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES agents(id),
    run_date        DATE NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'running',
    -- running | ok | degraded | failed | skipped
    error_text      TEXT,
    meta_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (agent_id, run_date)
);

CREATE TABLE IF NOT EXISTS shared_facts (
    id              BIGSERIAL PRIMARY KEY,
    fact_date       DATE NOT NULL,
    fact_key        TEXT NOT NULL,
    source_system   TEXT NOT NULL,
    -- biblia | club | agency | web | rag
    value_num       DOUBLE PRECISION,
    value_text      TEXT,
    value_json      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (fact_date, fact_key, source_system)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    agent_id        TEXT NOT NULL REFERENCES agents(id),
    kind            TEXT NOT NULL,
    -- brief_md | analysis_json | panel_json | pr_url
    title           TEXT,
    body_text       TEXT,
    body_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts (run_id);

CREATE TABLE IF NOT EXISTS recommendations (
    id              BIGSERIAL PRIMARY KEY,
    agent_id        TEXT NOT NULL REFERENCES agents(id),
    run_id          BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    created_on      DATE NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow')::date,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    evidence        TEXT NOT NULL DEFAULT '',
    target_system   TEXT NOT NULL DEFAULT 'biblia',
    -- biblia | club | content | agency
    status          TEXT NOT NULL DEFAULT 'proposed',
    -- proposed | accepted | rejected | shipped | measured | abandoned
    priority        INT NOT NULL DEFAULT 2,
    measure_after_days INT NOT NULL DEFAULT 7,
    shipped_at      TIMESTAMPTZ,
    measured_at     TIMESTAMPTZ,
    meta_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recs_status ON recommendations (status, created_on DESC);
CREATE INDEX IF NOT EXISTS idx_recs_agent ON recommendations (agent_id, created_on DESC);

CREATE TABLE IF NOT EXISTS recommendation_outcomes (
    id              BIGSERIAL PRIMARY KEY,
    recommendation_id BIGINT NOT NULL REFERENCES recommendations(id) ON DELETE CASCADE,
    measured_on     DATE NOT NULL,
    kpi_before_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    kpi_after_json  JSONB NOT NULL DEFAULT '{}'::jsonb,
    verdict         TEXT NOT NULL DEFAULT 'unknown',
    -- positive | negative | neutral | unknown
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS approvals (
    id              BIGSERIAL PRIMARY KEY,
    recommendation_id BIGINT NOT NULL REFERENCES recommendations(id) ON DELETE CASCADE,
    actor_user_id   BIGINT,
    action          TEXT NOT NULL,
    -- accept | reject | ship | abandon
    note            TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS data_gaps (
    id              BIGSERIAL PRIMARY KEY,
    agent_id        TEXT REFERENCES agents(id),
    gap_key         TEXT NOT NULL,
    description     TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'medium',
    status          TEXT NOT NULL DEFAULT 'open',
    -- open | mitigated | closed
    first_seen_on   DATE NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow')::date,
    last_seen_on    DATE NOT NULL DEFAULT (CURRENT_TIMESTAMP AT TIME ZONE 'Europe/Moscow')::date,
    meta_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (gap_key)
);

CREATE TABLE IF NOT EXISTS handoffs (
    id              BIGSERIAL PRIMARY KEY,
    from_agent_id   TEXT NOT NULL REFERENCES agents(id),
    to_agent_id     TEXT NOT NULL REFERENCES agents(id),
    run_id          BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    -- pending | acked | done | rejected
    payload_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_handoffs_to ON handoffs (to_agent_id, status);

CREATE TABLE IF NOT EXISTS external_signals (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    agent_id        TEXT REFERENCES agents(id),
    signal_date     DATE NOT NULL,
    source_url      TEXT,
    title           TEXT,
    summary         TEXT NOT NULL,
    relevance       TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS content_events (
    id              BIGSERIAL PRIMARY KEY,
    platform        TEXT NOT NULL,
    -- telegram | youtube | vk | other
    published_at    TIMESTAMPTZ,
    url             TEXT,
    title           TEXT,
    body_excerpt    TEXT,
    ref_key         TEXT,
    source          TEXT NOT NULL DEFAULT 'manual',
    -- manual | rag_meta | import
    meta_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_content_events_published
    ON content_events (published_at DESC NULLS LAST);

CREATE TABLE IF NOT EXISTS llm_calls (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    agent_id        TEXT,
    provider        TEXT NOT NULL,
    model           TEXT NOT NULL,
    role_in_panel   TEXT NOT NULL,
    -- analyst | researcher | critic | alternative | editor
    has_web         BOOLEAN NOT NULL DEFAULT FALSE,
    prompt_tokens   INT,
    completion_tokens INT,
    latency_ms      INT,
    ok              BOOLEAN NOT NULL DEFAULT TRUE,
    error_text      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS draft_prs (
    id              BIGSERIAL PRIMARY KEY,
    recommendation_id BIGINT REFERENCES recommendations(id) ON DELETE SET NULL,
    run_id          BIGINT REFERENCES runs(id) ON DELETE SET NULL,
    agent_id        TEXT NOT NULL REFERENCES agents(id),
    repo            TEXT NOT NULL DEFAULT 'biblia',
    branch_name     TEXT,
    pr_url          TEXT,
    status          TEXT NOT NULL DEFAULT 'draft_local',
    -- draft_local | branch_pushed | pr_open | closed
    patch_text      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
