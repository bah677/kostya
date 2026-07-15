# Миграции БД

В этой папке лежат **все** изменения, которые нужно сделать в БД, чтобы новая
версия кода (текущая разработка в `/home/appuser/club_ai`) могла работать.

## Контур

* Прод‑бот крутится на старом коде и смотрит в `localhost:5432/club_db`.
* Разработка ведётся в этом каталоге и смотрит в `localhost:5432/club_db_dev`
  — это снимок боевой БД, см. `00_setup_dev_db.sh`.
* Все DDL/DML‑правки тестируются на `club_db_dev`, фиксируются здесь
  пронумерованными SQL‑файлами и в момент релиза накатываются на `club_db`
  по описанному ниже порядку.

## Файлы

```
migrations/
  README.md                                — этот файл
  00_setup_dev_db.sh                       — РАЗОВЫЙ, dev-only. Создание копии club_db_dev.
  00_isolate_dev_users.sql                 — РАЗОВЫЙ, dev-only. Запечатывает dev-контур.
  001_messages_chat_type.sql               — pre-deploy. Колонка chat_type + бэкфилл + индексы.
  002_messages_dedupe.sql                  — pre-deploy. Удаление накопившихся дублей.
  003_conversation_history_legacy.sql      — POST-deploy. Переименование conversation_history.
  004_messages_unique_inbound.sql          — POST-deploy. Уникальный индекс от будущих дублей.
  005_interaction_logs_enrich.sql        — pre-deploy (с новым кодом). Колонки для трассировки и фильтров в interaction_logs.
  006_token_usage_analytics.sql         — расширение token_usage для мульти-провайдера и сырого billing JSON.
  007_media_inbound_archive.sql         — архив входящих медиа на диск + таблица media_inbound_files.
  008_club_invites_member_cache.sql     — club_invites + club_group_member_cache для инвайтов и ночного аудита.
  009_license_history.sql              — журнал изменений срока подписки (аудит; оперативное состояние в license).
  010_payments_provider_checkout_url.sql — URL страницы оплаты провайдера в `payments.provider_checkout_url`.
  011_subscription_outreach_sent.sql — анти-дубль рассылок `subscription_reminder` (`user_id` + slug + календарный день).
  012_mailing_audience_claimed_at.sql — `mailing_audience.claimed_at` для lease `processing`; статус `processing` (атомарный claim батча).

### Подготовка dev-окружения (один раз)

```bash
# 1. Создать dev-копию боевой БД
sudo bash migrations/00_setup_dev_db.sh

# 2. Подменить в .env: DB_NAME=club_db_dev и MIRON_BOT_TOKEN на токен тестового бота.
#    Также убедиться, что YOOKASSA_*, BZB_* подменены на тестовые ключи.

# 3. Применить рабочие миграции, как сделают на проде позже (плюс пост-deploy сразу,
#    т.к. новый код их выдержит):
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db_dev \
  -v ON_ERROR_STOP=1 -f migrations/001_messages_chat_type.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db_dev \
  -v ON_ERROR_STOP=1 -f migrations/002_messages_dedupe.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db_dev \
  -v ON_ERROR_STOP=1 -f migrations/003_conversation_history_legacy.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db_dev \
  -v ON_ERROR_STOP=1 -f migrations/004_messages_unique_inbound.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db_dev \
  -v ON_ERROR_STOP=1 -f migrations/005_interaction_logs_enrich.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db_dev \
  -v ON_ERROR_STOP=1 -f migrations/006_token_usage_analytics.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db_dev \
  -v ON_ERROR_STOP=1 -f migrations/007_media_inbound_archive.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db_dev \
  -v ON_ERROR_STOP=1 -f migrations/008_club_invites_member_cache.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db_dev \
  -v ON_ERROR_STOP=1 -f migrations/009_license_history.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db_dev \
  -v ON_ERROR_STOP=1 -f migrations/010_payments_provider_checkout_url.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db_dev \
  -v ON_ERROR_STOP=1 -f migrations/011_subscription_outreach_sent.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db_dev \
  -v ON_ERROR_STOP=1 -f migrations/012_mailing_audience_claimed_at.sql

# 4. Запечатать dev: оставить активными только тестировщиков, чтобы фоновые
#    процессы (followup, mailing, payment_checker, subscription_reminder)
#    не пытались стрелять в реальные telegram-id и реальные платёжки.
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db_dev \
  -v ON_ERROR_STOP=1 -f migrations/00_isolate_dev_users.sql
```

После этого можно запускать `python main.py` — старт будет тихим.

| Файл | Этап | Что делает | Что будет если запустить раньше времени |
|---|---|---|---|
| `001_messages_chat_type.sql` | pre-deploy | добавляет колонку `chat_type` (nullable), бэкфилл по `raw_data` и знаку `chat_id`, ставит частичные индексы | безопасно — старый код не знает о колонке |
| `002_messages_dedupe.sql` | pre-deploy | удаляет уже накопившиеся дубли в `messages` | безопасно — но если на проде ещё крутится старый код, новые дубли продолжат появляться |
| `003_conversation_history_legacy.sql` | POST-deploy | переименовывает `conversation_history` → `conversation_history_legacy` | **СЛОМАЕТ** старый код, который продолжает писать в `conversation_history` |
| `004_messages_unique_inbound.sql` | POST-deploy | ставит частичный уникальный индекс `(chat_id, telegram_message_id, version)` для всех не-callback | **СЛОМАЕТ** старый код, который пишет дубли — INSERT'ы будут валиться по `unique violation` |
| `005_interaction_logs_enrich.sql` | до/вместе с релизом слоя middleware | топ-поля в `interaction_logs` + частичные индексы | код с новым INSERT без миграции упадёт; старый однострочный INSERT со старым числом колонок можно накатывать раньше, но толку мало без нового кода |
| `006_token_usage_analytics.sql` | с релизом LLM-логирования | `provider`, `request_kind`, `raw_usage` JSONB, cache/reasoning | новый INSERT в `add_token_usage_with_metadata` без колонок упадёт |
| `007_media_inbound_archive.sql` | с релизом архива медиа | таблица `media_inbound_files` + индексы | без таблицы вставки из `MediaArchiveMixin` упадут |
| `008_club_invites_member_cache.sql` | с `club_group` (инвайты + аудит членов) | `club_invites`, `club_group_member_cache` | без таблиц запись инвайтов и кэш «пустой», ночная очистка бессмысленна |
| `009_license_history.sql` | с выдачей подписки / реф-бонусом / подарками через слой **`license_history`** | `license_history` + индекс | без таблицы вставки из `append_license_history` / audit в `create_or_extend_license` упадут |
| `010_payments_provider_checkout_url.sql` | с кодом сохранения ссылки оплаты | колонка `payments.provider_checkout_url` | `create_payment` без колонки упадёт после деплоя |
| `011_subscription_outreach_sent.sql` | до/вместе с `subscription_reminder` и `try_claim_subscription_outreach` | таблица `subscription_outreach_sent` + уникальный ключ | без таблицы `INSERT … ON CONFLICT` в коде упадёт |
| `012_mailing_audience_claimed_at.sql` | до/вместе с актуальным `MailingFeature` / `mailing_storage` | колонка `mailing_audience.claimed_at` | нужна только для lease/меток; код использует статус **`processing`** — при типе ENUM в БД см. заметку в SQL |

## Порядок раскатки на прод

```bash
# 0. Бэкап ОБЯЗАТЕЛЕН
sudo -u postgres pg_dump -Fc club_db > /backup/club_db_$(date +%F_%H%M).dump

# 1. PRE-deploy (можно прогнать ЗАРАНЕЕ, до релиза кода — ничего не сломает)
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db \
  -v ON_ERROR_STOP=1 -f migrations/001_messages_chat_type.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db \
  -v ON_ERROR_STOP=1 -f migrations/002_messages_dedupe.sql

# 2. ДЕПЛОЙ нового кода (новый bot/* и storage/db/*)
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db \
  -v ON_ERROR_STOP=1 -f migrations/005_interaction_logs_enrich.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db \
  -v ON_ERROR_STOP=1 -f migrations/009_license_history.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db \
  -v ON_ERROR_STOP=1 -f migrations/010_payments_provider_checkout_url.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db \
  -v ON_ERROR_STOP=1 -f migrations/011_subscription_outreach_sent.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db \
  -v ON_ERROR_STOP=1 -f migrations/012_mailing_audience_claimed_at.sql
#    После деплоя новый бот пишет messages с chat_type, не пишет conversation_history,
#    выводит исходящие через OutgoingLoggingMiddleware; license_history нужна кодом PaidOrderFulfillment/licenses.

# 3. POST-deploy (после успешного релиза, как только убедились, что новый бот работает)
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db \
  -v ON_ERROR_STOP=1 -f migrations/002_messages_dedupe.sql   # на случай новых дублей
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db \
  -v ON_ERROR_STOP=1 -f migrations/003_conversation_history_legacy.sql
PGPASSWORD=... psql -h localhost -U club_db_user -d club_db \
  -v ON_ERROR_STOP=1 -f migrations/004_messages_unique_inbound.sql
```

## Откат (rollback)

* `001` — удалить колонку и индексы:
  ```sql
  DROP INDEX IF EXISTS idx_messages_user_private_chrono;
  DROP INDEX IF EXISTS idx_messages_user_groups_chrono;
  DROP INDEX IF EXISTS idx_messages_chat_type_created_at;
  ALTER TABLE messages DROP COLUMN IF EXISTS chat_type;
  ```
* `002` — DELETE необратим без бэкапа. Восстанавливать из дампа.
* `003` — `ALTER TABLE conversation_history_legacy RENAME TO conversation_history;`
  и обратно для sequence.
* `004` — `DROP INDEX IF EXISTS messages_inbound_unique_idx;`
* `005` — удалить тематические индексы из файла миграции и `ALTER TABLE interaction_logs DROP COLUMN IF EXISTS outcome, ...`; проще восстановить из бэкапа.
* `009` — `DROP INDEX IF EXISTS idx_license_history_user_created; DROP TABLE IF EXISTS license_history;` (теряется аудит изменений подписки).
* `010` — `ALTER TABLE payments DROP COLUMN IF EXISTS provider_checkout_url;`.

* Все миграции **идемпотентные** (`IF NOT EXISTS` / `IF EXISTS`).
* Все pre-deploy миграции **обратно совместимы** со старым кодом.
* Ни одной операции не делается без транзакции, кроме `CREATE INDEX CONCURRENTLY`
  (его сейчас не требуется, БД маленькая — 49 MB).
