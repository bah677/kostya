#!/usr/bin/env bash
# Нормализация БД Biblia → схема club_ai (идемпотентные SQL).
# Пример: PGDATABASE=biblia_db_dev PGUSER=postgres bash apply_normalize_biblia_to_club_ai.sh
set -euo pipefail

NORM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$NORM_DIR/../../.." && pwd)"

PSQL=(psql -v ON_ERROR_STOP=1)
if [[ -n "${PGHOST:-}" ]]; then PSQL+=(-h "$PGHOST"); fi
if [[ -n "${PGPORT:-}" ]]; then PSQL+=(-p "$PGPORT"); fi
if [[ -n "${PGUSER:-}" ]]; then PSQL+=(-U "$PGUSER"); fi
if [[ -n "${PGDATABASE:-}" ]]; then PSQL+=(-d "$PGDATABASE"); fi

run() {
  local label="$1"
  local file="$2"
  echo ""
  echo "=============================="
  echo "==> $label"
  echo "=============================="
  "${PSQL[@]}" -f "$file"
}

if [[ -z "${PGDATABASE:-}" ]]; then
  echo "Задайте PGDATABASE (например biblia_db_dev)." >&2
  exit 1
fi

run "10 users"                    "$NORM_DIR/10_users_club_ai.sql"
run "11 payments + donations"    "$NORM_DIR/11_payments_core_and_donations.sql"
run "12 referrals + ref_keys"    "$NORM_DIR/12_referrals_ref_keys.sql"
run "13 license / orders stub"   "$NORM_DIR/13_license_and_orders_stub.sql"
run "20 messages"               "$NORM_DIR/20_messages_club_ai.sql"
run "15 messages id PK"       "$NORM_DIR/15_messages_add_surrogate_id_pk.sql"

run "bootstrap club runtime minimal" "$REPO_ROOT/migrations/biblia/bootstrap_club_runtime_minimal.sql"
run "001 messages chat_type"           "$REPO_ROOT/migrations/001_messages_chat_type.sql"
run "002 messages dedupe"           "$REPO_ROOT/migrations/002_messages_dedupe.sql"
run "003 conversation_history legacy" "$REPO_ROOT/migrations/003_conversation_history_legacy.sql"
run "004 messages unique inbound"   "$REPO_ROOT/migrations/004_messages_unique_inbound.sql"

run "005 interaction_logs enrich"  "$REPO_ROOT/migrations/005_interaction_logs_enrich.sql"
run "006 token_usage analytics"      "$REPO_ROOT/migrations/006_token_usage_analytics.sql"
run "007 media inbound archive"       "$REPO_ROOT/migrations/007_media_inbound_archive.sql"

run "009 license_history"          "$REPO_ROOT/migrations/009_license_history.sql"

run "010 payments checkout url"       "$REPO_ROOT/migrations/010_payments_provider_checkout_url.sql"
run "011 subscription outreach" "$REPO_ROOT/migrations/011_subscription_outreach_sent.sql"
run "012 mailing_audience claimed" "$REPO_ROOT/migrations/012_mailing_audience_claimed_at.sql"

run "013 messages processing_time_ms" "$REPO_ROOT/migrations/013_messages_processing_time_ms.sql"

run "biblia 002 daily mailing cols" "$REPO_ROOT/migrations/biblia/002_daily_mailing_and_donation_counters.sql"

echo ""
echo "Готово. Проверьте подключение бота к PGDATABASE=$PGDATABASE."
