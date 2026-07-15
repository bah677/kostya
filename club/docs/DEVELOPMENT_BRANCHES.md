# Ветки разработки: основной клуб и Настя

## Текущая политика (с июля 2026)

| Проект | Разработка | Прод | Репозиторий |
|--------|------------|------|-------------|
| **Основной клуб** (`@Talk_God_Bot`) | `/home/appuser/dev/club` | `/home/appuser/club` | этот репозиторий |
| **Клуб Насти** | **заморожен** | `/home/appuser/club_nastya` | отдельный репо — позже, снимок с прода |

Все новые фичи, отчёты и правки делаем **только для основного клуба**.

Код Насти (`BOT_VARIANT=nastya`, `twin_texts/nastya/`, `core_nastya.py`) остаётся в репозитории для совместимости прода, но **не развиваем** до выноса в отдельный репозиторий.

## Деплой

```bash
# Основной клуб (по умолчанию)
./scripts/deploy_prod.sh

# Настя — только срочные хотфиксы прода, не для новых фич
./scripts/deploy_nastya_prod.sh
```

`DEPLOY_TARGETS` по умолчанию: `club` (не `club,nastya`).

## Где что менять

| Задача | Куда |
|--------|------|
| Тексты основного бота | `bot/texts/` |
| Тексты Насти (только хотфикс) | `twin_texts/nastya/` |
| Общая логика | `bot/features/`, с guard `config.BOT_VARIANT == "nastya"` только если без этого ломается прод Насти |
| Сквозные отчёты Библия→Клуб | только основной клуб, `BIBLIA_DB_*` в `.env` |

## Экосистема двух ботов

1. **Библия-бот** (бесплатный) — рассылки, аудитория в `biblia_bot` БД.
2. **Клубный бот** — подписка, воронка, `club_db`.

Сквозная аналитика: `bot/services/biblia_club_campaign_report.py`, команда `/biblia_club`, блок в ежедневном отчёте v2.

### Настройка БД Библии (prod `.env` клуба)

```env
BIBLIA_DB_HOST=localhost
BIBLIA_DB_PORT=5432
BIBLIA_DB_NAME=biblia_bot
BIBLIA_DB_USER=biblia_bot_user
BIBLIA_DB_PASSWORD=...
TELEGRAM_BOT_USERNAME=Talk_God_Bot
```

Пароль **не** коммитить в git.
