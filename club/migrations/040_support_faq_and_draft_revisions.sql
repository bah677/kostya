-- Петля обратной связи на черновики поддержки.
--
-- support_faq — вопрос-ответ, одобренные админом. Наполняется само: когда
-- админ отправляет ответ по тикету, пара «обращение → ответ» ложится сюда и
-- дальше подаётся в промпт следующих черновиков. Так ответы на повторяющиеся
-- вопросы перестают сочиняться заново.
CREATE TABLE IF NOT EXISTS support_faq (
    id            SERIAL      PRIMARY KEY,
    ticket_number TEXT,
    question      TEXT        NOT NULL,
    answer        TEXT        NOT NULL,
    approved_by   BIGINT,
    approved_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- draft_sent — админ отправил черновик как есть (значит, он верный);
    -- admin_reply — админ написал свой текст (значит, черновик был не тот);
    -- manual — занесено руками.
    source        TEXT        NOT NULL DEFAULT 'admin_reply',
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS support_faq_active_approved
    ON support_faq (is_active, approved_at DESC);

-- support_draft_revisions — что админ просил поправить в черновике.
-- Нужны дважды: внутри тикета (модель переписывает черновик с учётом правки)
-- и across тикетов (повторяющиеся замечания идут в промпт как постоянные
-- указания — иначе одну и ту же правку придётся диктовать каждый раз).
CREATE TABLE IF NOT EXISTS support_draft_revisions (
    id            SERIAL      PRIMARY KEY,
    ticket_number TEXT        NOT NULL,
    round         INTEGER     NOT NULL DEFAULT 1,
    instruction   TEXT        NOT NULL,
    draft_before  TEXT,
    draft_after   TEXT,
    admin_id      BIGINT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS support_draft_revisions_ticket
    ON support_draft_revisions (ticket_number, round);
CREATE INDEX IF NOT EXISTS support_draft_revisions_recent
    ON support_draft_revisions (created_at DESC);
