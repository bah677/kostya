#!/usr/bin/env bash
#
# Ротация бэкапов / data / логов для club, biblia, avatar_kostya.
# Другие проекты не трогает.
#
# Примеры:
#   ./scripts/disk_retention.sh status
#   ./scripts/disk_retention.sh deploy --apply   # бэкапы 7д + data 7д + log/arc 30д
#   ./scripts/disk_retention.sh backups --apply
#   ./scripts/disk_retention.sh data --apply
#   ./scripts/disk_retention.sh logs --apply
#
# Политика:
#   BACKUP_DAYS=7   — дампы БД и tar-снимки кода
#   DATA_DAYS=7     — файлы в */data/ (кроме chroma*)
#   LOG_ARC_DAYS=30 — архивы логов (log/arc и ротированные *.log.*)
#
set -euo pipefail

MODE="${1:-status}"
APPLY=0
for a in "${@:2}"; do
  case "$a" in
    --apply) APPLY=1 ;;
    --dry-run) APPLY=0 ;;
  esac
done

BACKUP_DAYS="${BACKUP_DAYS:-7}"
DATA_DAYS="${DATA_DAYS:-7}"
LOG_ARC_DAYS="${LOG_ARC_DAYS:-30}"

BACKUPS_ROOT="${BACKUPS_ROOT:-/home/appuser/backups}"
# legacy (если ещё не удалили old_bots)
OLD_BOTS="${OLD_BOTS:-/home/appuser/old_bots}"

BIBLIA_PROD="${BIBLIA_PROD:-/home/appuser/biblia}"
BIBLIA_DEV="${BIBLIA_DEV:-/home/appuser/dev/kostya/biblia}"
CLUB_PROD="${CLUB_PROD:-/home/appuser/club}"
CLUB_DEV="${CLUB_DEV:-/home/appuser/dev/kostya/club}"
AVATAR_ROOT="${AVATAR_ROOT:-/home/appuser/dev/kostya/avatar_kostya}"

log() { printf '%s\n' "$*"; }
hr() { df -h / | tail -1; }

prune_files_mtime() {
  local dir="$1" days="$2" label="$3"
  shift 3 || true
  local -a find_args=("$@")
  [[ -d "$dir" ]] || return 0

  local cnt sz
  if ((${#find_args[@]})); then
    cnt=$(find "$dir" -type f "${find_args[@]}" -mtime +"$days" 2>/dev/null | wc -l)
    sz=$(find "$dir" -type f "${find_args[@]}" -mtime +"$days" -printf '%s\n' 2>/dev/null \
      | awk '{s+=$1} END{printf "%.1fM", (s?s:0)/1024/1024}')
  else
    cnt=$(find "$dir" -type f -mtime +"$days" 2>/dev/null | wc -l)
    sz=$(find "$dir" -type f -mtime +"$days" -printf '%s\n' 2>/dev/null \
      | awk '{s+=$1} END{printf "%.1fM", (s?s:0)/1024/1024}')
  fi
  log "  $label: >${days}d → $cnt ($sz) in $dir"
  if (( APPLY )) && (( cnt > 0 )); then
    if ((${#find_args[@]})); then
      find "$dir" -type f "${find_args[@]}" -mtime +"$days" -delete
    else
      find "$dir" -type f -mtime +"$days" -delete
    fi
    find "$dir" -type d -empty -delete 2>/dev/null || true
  fi
}

prune_data_tree() {
  local dir="$1" label="$2"
  [[ -d "$dir" ]] || return 0
  local cnt sz
  cnt=$(find "$dir" -type f \
    ! -path '*/chroma_data/*' ! -path '*/chroma/*' ! -path '*/.chromadb/*' \
    -mtime +"$DATA_DAYS" 2>/dev/null | wc -l)
  sz=$(find "$dir" -type f \
    ! -path '*/chroma_data/*' ! -path '*/chroma/*' ! -path '*/.chromadb/*' \
    -mtime +"$DATA_DAYS" -printf '%s\n' 2>/dev/null \
    | awk '{s+=$1} END{printf "%.1fM", (s?s:0)/1024/1024}')
  log "  $label data: >${DATA_DAYS}d → $cnt ($sz) in $dir"
  if (( APPLY )) && (( cnt > 0 )); then
    find "$dir" -type f \
      ! -path '*/chroma_data/*' ! -path '*/chroma/*' ! -path '*/.chromadb/*' \
      -mtime +"$DATA_DAYS" -delete
    find "$dir" -type d -empty \
      ! -path '*/chroma_data*' ! -path '*/chroma*' \
      -delete 2>/dev/null || true
  fi
}

prune_project_logs() {
  local root="$1" label="$2"
  local logdir="$root/log"
  [[ -d "$logdir" ]] || return 0

  # архив log/arc
  if [[ -d "$logdir/arc" ]]; then
    prune_files_mtime "$logdir/arc" "$LOG_ARC_DAYS" "$label log/arc"
  fi

  # ротированные рядом с активными (не трогаем bot.log / bot-errors.log / err.log / biblia_bot.log)
  local cnt sz
  cnt=$(find "$logdir" -maxdepth 1 -type f \
    \( -name '*.gz' -o -name '*-*.log' -o -name '*_*.log' -o -name '*.log.[0-9]*' \) \
    ! -name 'bot.log' ! -name 'bot-errors.log' ! -name 'err.log' \
    ! -name 'biblia_bot.log' ! -name 'biblia_bot_errors.log' \
    -mtime +"$LOG_ARC_DAYS" 2>/dev/null | wc -l)
  sz=$(find "$logdir" -maxdepth 1 -type f \
    \( -name '*.gz' -o -name '*-*.log' -o -name '*_*.log' -o -name '*.log.[0-9]*' \) \
    ! -name 'bot.log' ! -name 'bot-errors.log' ! -name 'err.log' \
    ! -name 'biblia_bot.log' ! -name 'biblia_bot_errors.log' \
    -mtime +"$LOG_ARC_DAYS" -printf '%s\n' 2>/dev/null \
    | awk '{s+=$1} END{printf "%.1fM", (s?s:0)/1024/1024}')
  log "  $label log rotated: >${LOG_ARC_DAYS}d → $cnt ($sz)"
  if (( APPLY )) && (( cnt > 0 )); then
    find "$logdir" -maxdepth 1 -type f \
      \( -name '*.gz' -o -name '*-*.log' -o -name '*_*.log' -o -name '*.log.[0-9]*' \) \
      ! -name 'bot.log' ! -name 'bot-errors.log' ! -name 'err.log' \
      ! -name 'biblia_bot.log' ! -name 'biblia_bot_errors.log' \
      -mtime +"$LOG_ARC_DAYS" -delete
  fi
}

cmd_status() {
  log "==> disk"
  hr
  log ""
  log "==> backups ($BACKUPS_ROOT)"
  du -sh "$BACKUPS_ROOT"/biblia "$BACKUPS_ROOT"/club 2>/dev/null || log "  (пусто / нет каталога)"
  if [[ -d "$OLD_BOTS" ]]; then
    log ""
    log "==> legacy old_bots (можно удалить)"
    du -sh "$OLD_BOTS" 2>/dev/null || true
  fi
  log ""
  log "==> data"
  du -sh "$BIBLIA_PROD/data" "$CLUB_PROD/data" "$AVATAR_ROOT/data" 2>/dev/null || true
  log ""
  log "==> logs"
  du -sh "$BIBLIA_PROD/log" "$CLUB_PROD/log" "$AVATAR_ROOT/log" 2>/dev/null || true
}

cmd_backups() {
  log "==> backups retention >${BACKUP_DAYS}d (apply=$APPLY)"
  mkdir -p "$BACKUPS_ROOT/biblia/db" "$BACKUPS_ROOT/biblia/code" \
           "$BACKUPS_ROOT/club/db" "$BACKUPS_ROOT/club/code" 2>/dev/null || true

  prune_files_mtime "$BACKUPS_ROOT/biblia/db" "$BACKUP_DAYS" "biblia db dumps"
  prune_files_mtime "$BACKUPS_ROOT/biblia/code" "$BACKUP_DAYS" "biblia code snaps"
  prune_files_mtime "$BACKUPS_ROOT/club/db" "$BACKUP_DAYS" "club db dumps"
  prune_files_mtime "$BACKUPS_ROOT/club/code" "$BACKUP_DAYS" "club code snaps"

  # если старые пути ещё живы — тоже подчистим (перед полным rm -rf old_bots)
  if [[ -d "$OLD_BOTS" ]]; then
    prune_files_mtime "$OLD_BOTS/biblia_db_dumps" "$BACKUP_DAYS" "legacy biblia dumps"
    prune_files_mtime "$OLD_BOTS/biblia_deploy_snapshots" "$BACKUP_DAYS" "legacy biblia code"
    prune_files_mtime "$OLD_BOTS/club_db_dumps" "$BACKUP_DAYS" "legacy club dumps"
    prune_files_mtime "$OLD_BOTS/club_deploy_snapshots" "$BACKUP_DAYS" "legacy club code"
  fi
  hr
}

cmd_data() {
  log "==> data retention >${DATA_DAYS}d (apply=$APPLY) — только club/biblia/avatar_kostya"
  prune_data_tree "$BIBLIA_PROD/data" "biblia-prod"
  prune_data_tree "$BIBLIA_DEV/data" "biblia-dev"
  prune_data_tree "$CLUB_PROD/data" "club-prod"
  prune_data_tree "$CLUB_DEV/data" "club-dev"
  prune_data_tree "$AVATAR_ROOT/data" "avatar_kostya"
  hr
}

cmd_logs() {
  log "==> logs archive >${LOG_ARC_DAYS}d (apply=$APPLY)"
  prune_project_logs "$BIBLIA_PROD" "biblia-prod"
  prune_project_logs "$BIBLIA_DEV" "biblia-dev"
  prune_project_logs "$CLUB_PROD" "club-prod"
  prune_project_logs "$CLUB_DEV" "club-dev"
  prune_project_logs "$AVATAR_ROOT" "avatar_kostya"
  hr
}

cmd_deploy() {
  # вызывается из deploy_prod.sh после бэкапа
  cmd_backups
  cmd_data
  cmd_logs
}

case "$MODE" in
  status) cmd_status ;;
  backups|keep-dumps) cmd_backups ;;
  data) cmd_data ;;
  logs) cmd_logs ;;
  deploy|all)
    cmd_backups
    cmd_data
    cmd_logs
    ;;
  *)
    echo "usage: $0 {status|deploy|backups|data|logs} [--dry-run|--apply]"
    exit 1
    ;;
esac
