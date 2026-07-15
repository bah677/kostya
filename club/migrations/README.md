# Миграции БД

Состояние **club_db** (прод) и **club_db_dev** выровнено с кодом. В репозитории одна **базовая** миграция `001_baseline.sql` (идемпотентно повторяет уже накатанную схему для admins, `mailing_campaigns.attachments`, `club_report_snapshots`). Дальше добавляйте только **новые** шаги: **`002_*.sql`**, **`003_*.sql`**, … и регистрируйте их в `scripts/apply_all_db_migrations.sh`.

## Контур

* Прод: `club_db`. Dev: обычно `club_db_dev`, см. `00_setup_dev_db.sh`.
* Деплой кода dev → prod: **`scripts/deploy_prod.sh`** (после rsync при необходимости гоняет `apply_all_db_migrations.sh`; дамп БД и архив кода — шаги 1 и 4 скрипта).
* Отдельный бэкап: **`scripts/backup_club_db_prod.sh`** (если используете).

## Деплой на прод и логи

`deploy_prod.sh` копирует код **`rsync -a --delete`** из `CLUB_DEV_ROOT` в `CLUB_PROD_ROOT`, при этом **на проде не перезаписываются и не удаляются**:

| Путь | Поведение |
|------|-----------|
| `.env` | exclude + protect — локальный прод |
| `venv/` / `.venv/` | exclude + protect — виртуальное окружение прода |
| `data/` | exclude + protect — данные приложения |
| `log/` | exclude + protect — логи бота на проде не затираются дампом с дев |

Восстановление `log/` из снапшота архива: `scripts/restore_club_log_from_snapshot.sh`.

## Подключение `psql` (`.env`)

Пароль приложения — **`DB_PASSWORD`**; для `psql`: **`PGPASSWORD`**.

Из корня репозитория:

```bash
set -a && source .env && set +a
export PGPASSWORD="$DB_PASSWORD"
psql -h "${DB_HOST:-localhost}" -p "${DB_PORT:-5432}" -U "$DB_USER" -d "$DB_NAME" \
  -v ON_ERROR_STOP=1 -f migrations/NNN_imya.sql
```

## Файлы в этой папке

| Файл | Назначение |
|------|------------|
| `README.md` | Этот документ |
| `00_setup_dev_db.sh` | Разово: копия боевой БД → `club_db_dev` на той же машине |
| `00_isolate_dev_users.sql` | Разово: dev-only ограничение пользователей в тестовом контуре |
| `001_baseline.sql` | Текущая базовая линия схемы (идемпотентно) |
| `002_users_updated_at.sql` | Колонка `users.updated_at` (деактивация и т. п.) |
| `003_*.sql`, … | Следующие инкрементные миграции |
| `006_attribution.sql` | `attribution_touches`, first/last touch на users/orders |
| `007_attribution_dedup.sql` | Уникальный индекс касаний (идемпотентный backfill) |

После `006`/`007` при деплое автоматически: `scripts/backfill_attribution_touches.py` (см. `apply_all_db_migrations.sh`, `SKIP_ATTRIBUTION_BACKFILL=1` чтобы пропустить).

## Рекомендации для новых миграций

* Делать **идемпотентно** (`IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, и т.д.).
* Одна миграция — одна задача; добавьте вызов в `apply_all_db_migrations.sh`.
* Проверка схемы: `scripts/schema_snapshot.sh` / `schema_snapshot.sql` (если есть в `scripts/`).

## Отката DDL «автоматом» нет

Перед опасными изменениями — логический дамп (`pg_dump -Fc`). Точечный откат — вручную или восстановление из дампа.
