#!/usr/bin/env bash
#
# Деплой только проекта Насти (club_nastya).
# После rsync накатывает bot/texts из twin_texts/nastya (как в deploy_prod для target=nastya).
#
# ⚠️  С июля 2026 новые фичи — только для основного клуба (deploy_prod.sh).
#     Настя будет вынесена в отдельный репозиторий с прода; этот скрипт — для хотфиксов.
#
# Запуск:
#   ./scripts/deploy_nastya_prod.sh
#
set -euo pipefail

export DEPLOY_TARGETS=nastya
export SKIP_TWIN_TEXTS=0

exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy_prod.sh" "$@"
