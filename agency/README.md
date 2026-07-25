# Agency — ИИ-агентство экосистемы kostya

Отдельный проект: потребитель данных `biblia` + `club` (+ RAG), советник с памятью и мульти-LLM панелью.

Агенты (ночью 03:00 МСК):
- **Bible Bot Manager** — KPI / рекомендации
- **QA Manager** — ERROR-логи club/biblia/avatar (все ротации) → короткие ТЗ

## Быстрый старт

### 1. PostgreSQL

```bash
cd /home/appuser/dev/kostya/agency
chmod +x scripts/bootstrap_db.sh
./scripts/bootstrap_db.sh   # sudo postgres: создаст agency + agency_user (+ agency_ro)
```

Миграции agency-схемы:

```bash
cd /home/appuser/dev/kostya/agency
.venv/bin/python main.py migrate
```

RO-гранты на `biblia_bot` / `club_db` делает `bootstrap_db.sh`. Пока можно временно читать prod-пользователями ботов (только SELECT-нагрузка ночью).

### 2. Env

```bash
cp .env.example .env
# AGENCY_BOT_TOKEN, AGENCY_*_DB_*, OPENAI/DEEPSEEK/ANTHROPIC
```

### 3. Venv

```bash
cd /home/appuser/dev/kostya/agency
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 4. Ручной прогон

```bash
.venv/bin/python main.py run --skip-llm          # все агенты, без LLM
.venv/bin/python main.py run                     # полный цикл с LLM
.venv/bin/python main.py run --agent qa          # только QA Manager
.venv/bin/python main.py run --day 2026-07-25
```

TG: `/agency_qa` — ручной прогон QA.

### 5. Бот + cron / «деплой»

Процесс крутится из dev (как avatar). После правок:

```bash
cd /home/appuser/dev/kostya/agency && ./scripts/deploy_prod.sh
# или только рестарт:
# SKIP_GIT_PUSH=1 ./scripts/deploy_prod.sh
```

Скрипт: retention (best-effort) → `sudo supervisorctl restart agency:agency` → git push монорепо.

Вручную бот:

```bash
.venv/bin/python main.py bot
```

Команды в TG: `/adm`, `/agency_run`, `/agency_run_nums`, `/agency_recs`, `/agency_gaps`,
`/admins`, `/admin_add`, `/admin_del`.

**Доступ:**
- `SUPER_ADMIN_ID` — полный доступ + управление админами
- таблица `admins` в БД agency — обычные админы (`/admin_add`)
- опционально `AGENCY_ADMIN_IDS` в .env как bootstrap

Supervisor-конфиг уже в `/etc/supervisor/conf.d/bots.conf` (группа `agency`).

## KPI Bible Bot Manager

| KPI | Источник |
|-----|----------|
| Stickiness DAU/MAU | biblia `messages` (user) |
| Донаты ₽/день | biblia `payments` (succeeded, order_id IS NULL) |
| Переходы в клуб | club `attribution_touches` + `biblia_bot` |

## Архитектура

- `collectors/` — SQL-агрегаты (истина по цифрам)
- `llm/panel.py` — Analyst / Researcher(web) / Critic / Alternative / Editor
- `db/` — своя PG: runs, shared_facts, recommendations, handoffs, …
- `agents/bible_bot_manager/` — дневной цикл
- Заглушки ролей в `agents` seed: copywriter, producer, …

## Полномочия

Разрешено: читать источники, писать в agency DB, предлагать, draft_local PR-текст.  
Запрещено: merge/deploy ботов, платежи, писать юзерам biblia/club.
