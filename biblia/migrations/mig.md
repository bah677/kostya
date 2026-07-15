Шаг A. Однократно: роль приложения для тестовой БД
sudo -u postgres psql -v ON_ERROR_STOP=1 -d postgres <<'SQL'
CREATE ROLE biblia_bot_user LOGIN PASSWORD 'gfhjkmgjrfntcnjdsq';
SQL
Если получишь already exists, пропускай создание или сделай переименование/другую роль:

sudo -u postgres psql -c "\du biblia_bot_user"
Шаг B. Удалить старую тестовую БД (если делал раньше) и заново создать пустышку
sudo -u postgres psql -d postgres -v ON_ERROR_STOP=1 -c "
  ALTER DATABASE biblia_test_upload WITH ALLOW_CONNECTIONS false;
  SELECT pg_terminate_backend(pid)
  FROM pg_stat_activity
  WHERE datname = 'biblia_test_upload' AND pid <> pg_backend_pid();
"

sudo -u postgres dropdb --if-exists biblia_test_upload
sudo -u postgres createdb biblia_test_upload

Шаг C. Снять дамп с боевой базы → восстановить в клон
DUMP="/tmp/telegram_bot_$(date -u +%Y%m%dT%H%MZ).Fc.dump"
sudo -u postgres pg_dump \
  --format=custom \
  --no-owner \
  --no-acl \
  --file="$DUMP" \
  telegram_bot
sudo -u postgres pg_restore \
  --dbname=biblia_bot \
  --no-owner \
  --no-acl \
  --verbose \
  "$DUMP"
telegram_bot при этом только читается, боевые данные в ней не меняются.






sudo -u postgres psql -v ON_ERROR_STOP=1 -d biblia_bot <<'SQL'

ALTER DATABASE biblia_bot OWNER TO biblia_bot_user;
ALTER SCHEMA public OWNER TO biblia_bot_user;

DO $$
DECLARE
  rec record;
BEGIN
  FOR rec IN
    SELECT c.oid AS roid, c.relkind
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_roles o ON o.oid = c.relowner
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
      AND o.rolname IN ('postgres', 'bot_user')
    ORDER BY CASE c.relkind WHEN 'r' THEN 1 WHEN 'p' THEN 1 ELSE 2 END
  LOOP
    EXECUTE format(
      CASE rec.relkind
        WHEN 'S' THEN 'ALTER SEQUENCE %s OWNER TO biblia_bot_user'
        WHEN 'v' THEN 'ALTER VIEW %s OWNER TO biblia_bot_user'
        WHEN 'm' THEN 'ALTER MATERIALIZED VIEW %s OWNER TO biblia_bot_user'
        ELSE 'ALTER TABLE %s OWNER TO biblia_bot_user'
      END,
      rec.roid::regclass::text
    );
  END LOOP;
END
$$;

DO $$
DECLARE
  fq regprocedure;
BEGIN
  FOR fq IN
    SELECT p.oid::regprocedure
    FROM pg_proc p
    JOIN pg_namespace n ON n.oid = p.pronamespace
    JOIN pg_roles o ON o.oid = p.proowner
    WHERE n.nspname = 'public'
      AND o.rolname IN ('postgres', 'bot_user')
  LOOP
    EXECUTE format('ALTER FUNCTION %s OWNER TO biblia_bot_user', fq::text);
  END LOOP;
END
$$;

SQL


Если на проде владельцем таблиц была bot_user, может понадобиться ещё (выполни и посмотри, будет ли ошибка «нет такого владельца» — если роль есть в кластере, сработает):


sudo -u postgres psql -v ON_ERROR_STOP=1 -d biblia_bot <<'SQL'
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bot_user') THEN
    REASSIGN OWNED BY bot_user TO biblia_bot_user;
  END IF;
END
$$;
SQL

sudo CLONE_DB=biblia_bot RUN_MAILING_OWNER_FIX=1 \
  bash migrations/biblia/run_legacy_clone_audit_migrate_audit.sh