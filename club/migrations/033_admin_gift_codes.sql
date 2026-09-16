-- Одноразовые админские gift-ссылки (deep link ?start=agift_CODE).
-- Срок days — длительность лицензии с момента активации;
-- expires_at — до какого момента ссылку можно активировать.

CREATE TABLE IF NOT EXISTS admin_gift_codes (
    id              BIGSERIAL PRIMARY KEY,
    gift_code       TEXT NOT NULL,
    days            INT NOT NULL CHECK (days >= 1 AND days <= 3650),
    created_by      BIGINT NOT NULL,
    note            TEXT,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status = ANY (ARRAY['active', 'used', 'revoked'])),
    expires_at      TIMESTAMPTZ NOT NULL,
    activated_by    BIGINT REFERENCES users (user_id) ON DELETE SET NULL,
    activated_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT admin_gift_codes_code_uniq UNIQUE (gift_code)
);

CREATE INDEX IF NOT EXISTS idx_admin_gift_codes_status_expires
    ON admin_gift_codes (status, expires_at)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_admin_gift_codes_created_by
    ON admin_gift_codes (created_by, created_at DESC);
