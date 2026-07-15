# kostya

Монорепозиторий трёх связанных проектов Константина:

| Каталог | Роль | Prod |
|---------|------|------|
| `club/` | клубный бот | `/home/appuser/club` через `club/scripts/deploy_prod.sh` |
| `biblia/` | библейский бот | `/home/appuser/biblia` через `biblia/scripts/deploy_prod.sh` |
| `avatar_kostya/` | ассистент + RAG/индексация | запускается из этого каталога (supervisor) |

## GitHub

- Remote: `git@github.com:bah677/kostya.git`
- Пуш после деплоя: `./scripts/git_push_deploy.sh` (или автоматически из deploy club/biblia)

## Важно

- Секреты (`.env`), `venv/`, `.venv/`, `chroma_data/`, логи — **не** в git
- Прод-пути club/biblia **не** менялись
- После переноса обновите supervisor для `avatar_kostya` (см. `scripts/supervisor_avatar_kostya_paths.sh`)
