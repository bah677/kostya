# kostya

Монорепозиторий трёх связанных проектов Константина:

| Каталог | Роль | Prod |
|---------|------|------|
| `club/` | клубный бот | `/home/appuser/club` через `club/scripts/deploy_prod.sh` |
| `biblia/` | библейский бот | `/home/appuser/biblia` через `biblia/scripts/deploy_prod.sh` |
| `avatar_kostya/` | ассистент + RAG/индексация | запускается из этого каталога (supervisor) |
