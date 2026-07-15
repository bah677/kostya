"""Справочник request_kind для token_usage (клубные рассылки и batch LLM)."""

from __future__ import annotations

# Batch (system user_id=0)
CLUB_DIGEST_BASE = "club_digest_base"
CLUB_SCRIPTURE_BASE = "club_scripture_base"
CLUB_REPORT_GROUP_DAY = "club_report_group_day"
CLUB_REPORT_LEAD_DIALOGS = "club_report_lead_dialogs"
CLUB_ENGAGEMENT_REPORT = "club_engagement_report_insights"
CLUB_CHURN_ANALYSIS = "club_churn_analysis"

# Per-user club outreach
CLUB_DIGEST_PERSONALIZE = "club_digest_personalize"
CLUB_SCRIPTURE_PERSONALIZE = "club_scripture_personalize"
CLUB_OUTREACH_POLICY = "club_outreach_policy"

# Прочие (должны логироваться при рефакторинге)
WISH_BUTTON_TITLE = "wish_button_title"
CLUB_SCHEDULE_EXTRACT = "club_schedule_extract"
