"""Устойчивость цепочки subscription_reminder."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from bot.features.subscription_reminder import SubscriptionReminderFeature
from bot.texts import ru_subscription_reminder as sub_txt
from config import config


def test_process_all_continues_after_step_failure():
    async def _run():
        feature = SubscriptionReminderFeature(AsyncMock(), AsyncMock())
        feature._process_reminders = AsyncMock(side_effect=RuntimeError("llm down"))
        feature._process_bonus_extensions = AsyncMock()
        feature._process_expired_and_remove = AsyncMock()
        feature._process_churn_outreach = AsyncMock()

        await feature._process_all()

        feature._process_bonus_extensions.assert_awaited_once()
        feature._process_expired_and_remove.assert_awaited_once()
        feature._process_churn_outreach.assert_awaited_once()

    asyncio.run(_run())


def test_reminder_body_falls_back_to_template_on_llm_error():
    async def _run():
        storage = AsyncMock()
        feature = SubscriptionReminderFeature(storage, AsyncMock())
        feature._llm_client = MagicMock()
        reminder = sub_txt.REMINDER_CONFIG[0]

        with patch(
            "bot.services.member_renewal_outreach.generate_renewal_outreach_html",
            new_callable=AsyncMock,
            side_effect=RuntimeError("402"),
        ):
            body = await feature._reminder_body(
                uid=1,
                first_name="Анна",
                reminder=reminder,
                license_expires_at=None,
            )

        if config.MEMBER_RENEWAL_AI_ENABLED:
            assert "7 дней" in body
            storage.set_member_renewal_state.assert_not_awaited()
        else:
            assert "7 дней" in body

    asyncio.run(_run())


def test_post_bonus_uses_lookback_list():
    async def _run():
        storage = AsyncMock()
        storage.list_pending_post_bonus_removals = AsyncMock(return_value=[])
        feature = SubscriptionReminderFeature(storage, AsyncMock())
        feature.feature_manager = None

        await feature._process_expired_and_remove(date(2026, 8, 26))

        storage.list_pending_post_bonus_removals.assert_awaited_once()
        kwargs = storage.list_pending_post_bonus_removals.await_args.kwargs
        assert kwargs["lookback_days"] == config.SUBSCRIPTION_POST_BONUS_LOOKBACK_DAYS

    asyncio.run(_run())


def test_post_bonus_skips_duplicate_dm_but_still_kicks():
    async def _run():
        storage = AsyncMock()
        expires = datetime(2026, 8, 24, 6, 8, 39)
        storage.list_pending_post_bonus_removals = AsyncMock(
            return_value=[{"user_id": 42, "expires_at": expires}]
        )
        storage.try_claim_subscription_outreach = AsyncMock(return_value=True)
        storage.has_subscription_outreach_since = AsyncMock(return_value=True)
        storage.is_telegram_admin_id = AsyncMock(return_value=False)
        storage.get_user = AsyncMock(return_value={"first_name": "Анжела"})
        storage.mark_license_expired = AsyncMock(return_value=True)
        storage.record_club_member_exclusion = AsyncMock()

        bot = AsyncMock()
        feature = SubscriptionReminderFeature(storage, bot)
        feature.feature_manager = MagicMock()
        feature.feature_manager.get.return_value = object()
        feature._send_html = AsyncMock(return_value=True)
        feature._notify_admin = AsyncMock()

        with patch(
            "bot.features.subscription_reminder.build_club_removal_card_html",
            new_callable=AsyncMock,
            return_value="card",
        ):
            await feature._process_expired_and_remove(date(2026, 8, 26))

        feature._send_html.assert_not_awaited()
        bot.ban_chat_member.assert_awaited_once()
        storage.mark_license_expired.assert_awaited_once_with(42)

    asyncio.run(_run())
