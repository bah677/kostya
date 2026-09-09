-- Подарочная волна (ВОЛ-1…6): партии подарков, встречающие, origin лицензии.

-- ---------------------------------------------------------------------------
-- Лицензия: откуда доступ (ВОЛ-3)
-- ---------------------------------------------------------------------------
ALTER TABLE license
    ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'payment';

ALTER TABLE license
    ADD COLUMN IF NOT EXISTS wave_id BIGINT NULL;

COMMENT ON COLUMN license.origin IS
    'payment | gift | trial | bonus — источник доступа; для серий прогрева';
COMMENT ON COLUMN license.wave_id IS
    'gift_wave.id при выдаче в рамках волны; иначе NULL';

CREATE INDEX IF NOT EXISTS idx_license_origin ON license (origin);
CREATE INDEX IF NOT EXISTS idx_license_wave_id ON license (wave_id)
    WHERE wave_id IS NOT NULL;

-- Разовая доразметка существующих (идемпотентна по смыслу origin='payment' default).
-- Подарок: subscription без успешной оплаты до выдачи.
UPDATE license l
SET origin = 'gift'
WHERE l.license_type = 'subscription'
  AND COALESCE(l.origin, 'payment') = 'payment'
  AND NOT EXISTS (
        SELECT 1
        FROM payments p
        WHERE p.user_id = l.user_id
          AND p.status IN ('succeeded', 'paid')
          AND p.created_at <= COALESCE(l.updated_at, l.expires_at, NOW())
    );

UPDATE license
SET origin = 'bonus'
WHERE license_type = 'bonus_extension'
  AND COALESCE(origin, 'payment') = 'payment';

-- Триал: первый успешный платёж <= 350 ₽ и тип subscription.
UPDATE license l
SET origin = 'trial'
WHERE l.license_type = 'subscription'
  AND COALESCE(l.origin, 'payment') = 'payment'
  AND EXISTS (
        SELECT 1
        FROM payments p
        WHERE p.user_id = l.user_id
          AND p.status IN ('succeeded', 'paid')
          AND COALESCE(p.amount_rub, p.amount, 0) <= 350
          AND p.created_at = (
              SELECT MIN(p2.created_at)
              FROM payments p2
              WHERE p2.user_id = l.user_id
                AND p2.status IN ('succeeded', 'paid')
          )
    );

-- ---------------------------------------------------------------------------
-- Волна
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gift_wave (
    id              BIGSERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_size      INT NOT NULL DEFAULT 25 CHECK (batch_size >= 1 AND batch_size <= 25),
    interval_hours  INT NOT NULL DEFAULT 48 CHECK (interval_hours >= 1),
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'running', 'paused', 'done')),
    gift_days       INT NOT NULL DEFAULT 30 CHECK (gift_days >= 1 AND gift_days <= 365),
    last_batch_at   TIMESTAMPTZ NULL,
    paused_reason   TEXT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gift_wave_member (
    wave_id         BIGINT NOT NULL REFERENCES gift_wave (id) ON DELETE CASCADE,
    user_id         BIGINT NOT NULL REFERENCES users (user_id),
    queued_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    granted_at      TIMESTAMPTZ NULL,
    joined_at       TIMESTAMPTZ NULL,
    first_msg_at    TIMESTAMPTZ NULL,
    first_msg_id    BIGINT NULL,
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'granted', 'joined', 'spoke', 'declined')),
    PRIMARY KEY (wave_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_gift_wave_member_status
    ON gift_wave_member (wave_id, status);
CREATE INDEX IF NOT EXISTS idx_gift_wave_member_user
    ON gift_wave_member (user_id);

ALTER TABLE license
    DROP CONSTRAINT IF EXISTS license_wave_id_fkey;
ALTER TABLE license
    ADD CONSTRAINT license_wave_id_fkey
    FOREIGN KEY (wave_id) REFERENCES gift_wave (id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------------
-- Встречающие
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS club_greeter (
    user_id         BIGINT PRIMARY KEY REFERENCES users (user_id),
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    capacity        INT NOT NULL DEFAULT 3 CHECK (capacity >= 1 AND capacity <= 20),
    added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    paused_until    TIMESTAMPTZ NULL,
    miss_streak     INT NOT NULL DEFAULT 0,
    notes           TEXT NULL
);

CREATE TABLE IF NOT EXISTS club_greeter_assignment (
    id              BIGSERIAL PRIMARY KEY,
    greeter_id      BIGINT NOT NULL REFERENCES club_greeter (user_id),
    newcomer_id     BIGINT NOT NULL REFERENCES users (user_id),
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    replied_at      TIMESTAMPTZ NULL,
    reassigned_at   TIMESTAMPTZ NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'replied', 'reassigned', 'bot_followup', 'cancelled')),
    newcomer_msg_id BIGINT NULL,
    attempt         INT NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_greeter_assignment_pending
    ON club_greeter_assignment (status, assigned_at)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_greeter_assignment_newcomer
    ON club_greeter_assignment (newcomer_id, assigned_at DESC);
CREATE INDEX IF NOT EXISTS idx_greeter_assignment_day
    ON club_greeter_assignment (greeter_id, assigned_at);
