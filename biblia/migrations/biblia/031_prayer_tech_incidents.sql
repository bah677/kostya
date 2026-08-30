-- Инциденты с голосовыми молитвами (для рассылки «повторите запрос»).

BEGIN;

CREATE TABLE IF NOT EXISTS prayer_tech_incidents (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users (user_id),
    kind        VARCHAR(64) NOT NULL,
    detail      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_prayer_tech_incidents_created
    ON prayer_tech_incidents (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_prayer_tech_incidents_user_created
    ON prayer_tech_incidents (user_id, created_at DESC);

DO $body$
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
        'GRANT SELECT, INSERT ON TABLE prayer_tech_incidents TO %I', r
      );
      EXECUTE format(
        'GRANT USAGE, SELECT ON SEQUENCE prayer_tech_incidents_id_seq TO %I', r
      );
    END IF;
  END LOOP;
END $body$;

COMMIT;
