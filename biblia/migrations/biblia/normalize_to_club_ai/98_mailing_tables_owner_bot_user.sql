-- Выполнить под суперпользователем Postgres (локально часто postgres), один раз после клонирования,
-- если владельцем клубных/легаси таблиц рассылок остался role postgres, а бот подключается как bot_user.
--
-- psql -U postgres -d biblia_db_dev -v ON_ERROR_STOP=1 -f ...

ALTER TABLE IF EXISTS mailing_audience OWNER TO bot_user;
ALTER TABLE IF EXISTS mailing_campaigns OWNER TO bot_user;
ALTER TABLE IF EXISTS mailing_logs OWNER TO bot_user;
ALTER TABLE IF EXISTS mailing_schedules OWNER TO bot_user;
