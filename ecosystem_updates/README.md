# Обновления экосистемы (updates.mironbot.ru)

Публичный статический сайт: **что изменилось для пользователей** (клуб, БиблияБот, связанные сервисы). Без внутренних багов, миграций и техдолга.

## Как писать заметки

1. Файл дня: `notes/YYYY-MM-DD.md` (дата по Москве).
2. Пишите **только user-facing** изменения.
3. Если за день правок несколько — **переписывайте файл целиком**, чтобы за день читался один цельный апдейт, а не лог «сделал / переделал».
4. Черновики можно копить в `inbox/` и перед деплоем свести в файл дня.

## Публикация

При любом `./scripts/deploy_prod.sh` в club / biblia / avatar / agency вызывается:

```bash
ecosystem_updates/scripts/publish.sh
```

Скрипт пересобирает `site/*.html` из `notes/`. Сайт отдаётся nginx с корнем `ecosystem_updates/site`.

Вручную:

```bash
/home/appuser/dev/kostya/ecosystem_updates/scripts/publish.sh
```

Отключить хук на деплое: `SKIP_ECOSYSTEM_UPDATES=1`.

## Первый запуск nginx + DNS

См. `scripts/setup_updates_web.sh` и блок DNS в ответе/README ниже.
