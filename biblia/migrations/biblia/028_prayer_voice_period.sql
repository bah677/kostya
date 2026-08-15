-- Период голосового лимита: фиксируется в 08:00 МСК от донатов за прошлые 24ч.

CREATE TABLE IF NOT EXISTS prayer_voice_period (
    quota_day DATE PRIMARY KEY,
    limit_slots INTEGER NOT NULL,
    computed_slots INTEGER NOT NULL DEFAULT 0,
    min_floor INTEGER NOT NULL DEFAULT 0,
    revenue_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    revenue_rub DOUBLE PRECISION NOT NULL DEFAULT 0,
    locked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DO $$
DECLARE
  roles text[] := ARRAY[
    'biblia_bot_user',
    'biblia_bot_user_dev',
    'bot_user'
  ];
  r text;
BEGIN
  FOREACH r IN ARRAY roles LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format(
        'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE prayer_voice_period TO %I', r
      );
    END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agency_ro') THEN
    GRANT SELECT ON TABLE prayer_voice_period TO agency_ro;
  END IF;
END$$;
