"""Тест поста ангельского взноса в топик доски."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.services import wish_board_notify as wb_notify


@pytest.mark.asyncio
async def test_post_angel_pool_donation_passes_digest_topic_args():
    bot = MagicMock()
    with patch("bot.services.wish_board_notify.config") as cfg, patch(
        "bot.services.wish_board_notify.send_html_to_club_digest_topic",
        new_callable=AsyncMock,
        return_value=True,
    ) as send_mock:
        cfg.WISH_BOARD_DIGEST_TOPIC_ID = 10490
        cfg.CLUB_GROUP_ID = -1003882558802

        ok = await wb_notify.post_angel_pool_donation(
            bot,
            amount="100",
            currency_label="$",
            count=7,
        )

    assert ok is True
    send_mock.assert_awaited_once()
    kwargs = send_mock.await_args.kwargs
    assert kwargs["chat_id"] == -1003882558802
    assert kwargs["topic_id"] == 10490
    assert kwargs["html"]
    assert "100" in kwargs["html"]
    assert kwargs["log_prefix"] == "angel_pool_digest"
