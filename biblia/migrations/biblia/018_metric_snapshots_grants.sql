-- Права bot_user на metric_snapshots (017 мог создать таблицу от postgres).
-- Выполнить от суперпользователя: sudo -u postgres psql -d biblia_bot -f ...

BEGIN;

GRANT ALL ON TABLE metric_snapshots TO bot_user;
GRANT USAGE, SELECT ON SEQUENCE metric_snapshots_id_seq TO bot_user;

COMMIT;
