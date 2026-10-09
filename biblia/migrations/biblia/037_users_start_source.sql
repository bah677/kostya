-- Откуда человек пришёл в бота: содержимое /start <payload>.
--
-- Нужно для воронки с YouTube. Ссылка под роликом выглядит как
-- https://t.me/<bot>?start=yt_20261010_02, где хвост — день и номер ролика
-- в пачке. Без этой колонки видно только «пришли люди», но не видно, какой
-- ролик их привёл, а это разные ролики: набравший просмотры и приведший
-- человека — обычно не один и тот же.
--
-- Пишется один раз, при самом первом /start. Перезаписывать нельзя: иначе
-- повторный заход по другой ссылке сотрёт настоящий источник.

ALTER TABLE users ADD COLUMN IF NOT EXISTS start_source TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS start_source_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_users_start_source
    ON users (start_source)
    WHERE start_source IS NOT NULL;
