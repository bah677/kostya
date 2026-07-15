"""Тесты unban перед выдачей инвайта в клуб."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from bot.features.club_group import ClubGroupFeature


def test_create_fresh_invite_unbans_before_link():
    async def _run():
        storage = MagicMock()
        storage.fetch_revokable_club_invites_for_user = AsyncMock(return_value=[])
        storage.insert_club_invite = AsyncMock()

        feature = ClubGroupFeature(user_storage=storage, bot=AsyncMock())
        feature.bot.create_chat_invite_link = AsyncMock(
            return_value=MagicMock(invite_link="https://t.me/+test")
        )

        with patch("bot.features.club_group.config") as cfg:
            cfg.CLUB_GROUP_ID = -100123
            cfg.CLUB_INVITE_TTL_HOURS = 24
            link = await feature._create_fresh_invite_link(42)

        assert link == "https://t.me/+test"
        feature.bot.unban_chat_member.assert_awaited_once_with(
            chat_id=-100123,
            user_id=42,
            only_if_banned=True,
        )
        feature.bot.create_chat_invite_link.assert_awaited_once()

    asyncio.run(_run())
