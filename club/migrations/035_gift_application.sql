-- Анкета подарочной волны (gift-2026-09).

CREATE TABLE IF NOT EXISTS gift_application (
    id                  BIGSERIAL PRIMARY KEY,
    campaign            TEXT NOT NULL DEFAULT 'gift-2026-09',
    user_id             BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    source              TEXT NOT NULL DEFAULT 'other'
                        CHECK (source = ANY (ARRAY[
                            'bot', 'bib', 'tg', 'ig', 'yt', 'other'
                        ])),
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_at        TIMESTAMPTZ,
    q1_about            TEXT,
    q2_why              TEXT,
    q3_ready            BOOLEAN,
    rules_accepted      BOOLEAN NOT NULL DEFAULT FALSE,
    eligible            BOOLEAN NOT NULL DEFAULT TRUE,
    ineligible_reason   TEXT,
    verdict             TEXT
                        CHECK (verdict IS NULL OR verdict = ANY (ARRAY[
                            'pass', 'reject', 'review', 'care'
                        ])),
    verdict_reason      TEXT,
    reviewed_by         BIGINT,
    reviewed_at         TIMESTAMPTZ,
    score               INT,
    score_parts         JSONB,
    status              TEXT NOT NULL DEFAULT 'draft'
                        CHECK (status = ANY (ARRAY[
                            'draft', 'submitted', 'screened', 'queued',
                            'selected', 'drawn', 'rejected', 'care',
                            'not_selected', 'expired', 'cancelled', 'ineligible'
                        ])),
    wave_id             BIGINT REFERENCES gift_wave (id) ON DELETE SET NULL,
    reminder_sent_at    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT gift_application_campaign_user_uniq UNIQUE (campaign, user_id)
);

CREATE INDEX IF NOT EXISTS idx_gift_application_status
    ON gift_application (campaign, status, submitted_at);
CREATE INDEX IF NOT EXISTS idx_gift_application_source
    ON gift_application (campaign, source, status);
CREATE INDEX IF NOT EXISTS idx_gift_application_review
    ON gift_application (campaign, status)
    WHERE status = 'submitted' OR verdict = 'review';

ALTER TABLE gift_wave
    ADD COLUMN IF NOT EXISTS draw_seed TEXT;
ALTER TABLE gift_wave
    ADD COLUMN IF NOT EXISTS campaign TEXT;
ALTER TABLE gift_wave
    ADD COLUMN IF NOT EXISTS stage TEXT;
ALTER TABLE gift_wave
    ADD COLUMN IF NOT EXISTS wave_index INT;

ALTER TABLE gift_wave_member
    ADD COLUMN IF NOT EXISTS application_id BIGINT
        REFERENCES gift_application (id) ON DELETE SET NULL;
ALTER TABLE gift_wave_member
    ADD COLUMN IF NOT EXISTS selection TEXT
        CHECK (selection IS NULL OR selection = ANY (ARRAY['score', 'draw']));
ALTER TABLE gift_wave_member
    ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE gift_wave_member
    ADD COLUMN IF NOT EXISTS score INT;
ALTER TABLE gift_wave_member
    ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ;
ALTER TABLE gift_wave_member
    ADD COLUMN IF NOT EXISTS expired_at TIMESTAMPTZ;

-- расширяем статусы волны-участника
ALTER TABLE gift_wave_member DROP CONSTRAINT IF EXISTS gift_wave_member_status_check;
ALTER TABLE gift_wave_member
    ADD CONSTRAINT gift_wave_member_status_check
    CHECK (status = ANY (ARRAY[
        'queued', 'granted', 'activated', 'joined', 'spoke',
        'declined', 'expired'
    ]));

-- промо: ограничение по тарифам (NULL = все)
ALTER TABLE promo_campaigns
    ADD COLUMN IF NOT EXISTS tariff_ids INT[];

CREATE TABLE IF NOT EXISTS gift_campaign_state (
    campaign            TEXT PRIMARY KEY,
    stage               INT NOT NULL DEFAULT 1
                        CHECK (stage BETWEEN 1 AND 3),
    club_cohort         TEXT,
    mailing_paused      BOOLEAN NOT NULL DEFAULT FALSE,
    waves_paused        BOOLEAN NOT NULL DEFAULT FALSE,
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    notes               TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gift_mailing_sent (
    campaign            TEXT NOT NULL,
    user_id             BIGINT NOT NULL REFERENCES users (user_id) ON DELETE CASCADE,
    cohort              TEXT NOT NULL,
    stage               INT NOT NULL DEFAULT 1,
    sent_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivery            TEXT NOT NULL DEFAULT 'sent'
                        CHECK (delivery = ANY (ARRAY['sent', 'blocked', 'failed'])),
    PRIMARY KEY (campaign, user_id)
);

CREATE INDEX IF NOT EXISTS idx_gift_mailing_sent_cohort
    ON gift_mailing_sent (campaign, cohort, sent_at);
-- Промо возвращения: −30% только на тариф «1 месяц» (id=1, если есть).
INSERT INTO promo_campaigns (guid, name, description, discount_percent, is_active, tariff_ids)
VALUES (
    'giftreturn30',
    'Возвращение: месяц −30%',
    'Скидка 30% на первый месяц для тех, у кого доступ уже был и закончился.',
    30,
    TRUE,
    ARRAY[1]::int[]
)
ON CONFLICT (guid) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    discount_percent = EXCLUDED.discount_percent,
    tariff_ids = EXCLUDED.tariff_ids,
    is_active = TRUE;
