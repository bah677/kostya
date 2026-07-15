"""Тесты экрана согласия с юридическими документами."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.features.legal_consent import (
    CALLBACK_ACCEPT,
    CALLBACK_DOC_POLICY,
    LegalConsentFeature,
)


class _ConsentStorage:
    def __init__(self, *, has_consent: bool = False):
        self._has = has_consent
        self.recorded = []

    async def has_user_legal_consent(self, user_id: int) -> bool:
        return self._has

    async def record_user_legal_consent(self, user_id: int, **kwargs) -> bool:
        self.recorded.append((user_id, kwargs))
        self._has = True
        return True


def test_configured_legal_pdf_policy_from_media_ids():
    from bot.services.legal_documents import configured_legal_pdf_file_id

    fid = configured_legal_pdf_file_id("policy")
    assert fid
    assert fid.startswith("BQAC")


@pytest.mark.asyncio
async def test_ensure_consent_skips_when_already_recorded():
    storage = _ConsentStorage(has_consent=True)
    feature = LegalConsentFeature(storage, feature_manager=None)
    message = MagicMock()
    message.from_user.id = 1
    state = AsyncMock()
    state.get_state = AsyncMock(return_value=None)

    ok = await feature.ensure_consent_or_prompt(
        message, state, source="onboarding_start", resume={"action": "onboarding"}
    )
    assert ok is True
    message.answer.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_consent_shows_screen_when_missing():
    storage = _ConsentStorage(has_consent=False)
    feature = LegalConsentFeature(storage, feature_manager=None)
    message = MagicMock()
    message.from_user.id = 42
    message.answer = AsyncMock()
    state = AsyncMock()
    state.get_state = AsyncMock(return_value=None)
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()

    ok = await feature.ensure_consent_or_prompt(
        message,
        state,
        source="payment",
        resume={"action": "payment", "tariff_type": "base"},
    )
    assert ok is False
    message.answer.assert_awaited_once()
    args, kwargs = message.answer.await_args
    assert "Перед началом" in args[0]
    kb = kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == CALLBACK_ACCEPT
    assert kb.inline_keyboard[0][0].style == "success"
    assert kb.inline_keyboard[1][1].callback_data == CALLBACK_DOC_POLICY


@pytest.mark.asyncio
async def test_post_payment_consent_sends_message_once():
    storage = _ConsentStorage(has_consent=False)
    bot = MagicMock()
    bot.send_message = AsyncMock()
    feature = LegalConsentFeature(storage, feature_manager=None, bot=bot)

    sent = await feature.prompt_after_successful_payment(user_id=42)

    assert sent is True
    bot.send_message.assert_awaited_once()
    args, kwargs = bot.send_message.await_args
    assert args[0] == 42
    assert "Спасибо за оплату" in args[1]
    kb = kwargs["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == CALLBACK_ACCEPT


@pytest.mark.asyncio
async def test_post_payment_consent_skips_when_already_recorded():
    storage = _ConsentStorage(has_consent=True)
    bot = MagicMock()
    bot.send_message = AsyncMock()
    feature = LegalConsentFeature(storage, feature_manager=None, bot=bot)

    sent = await feature.prompt_after_successful_payment(user_id=42)

    assert sent is False
    bot.send_message.assert_not_called()


@pytest.mark.asyncio
async def test_record_consent_stores_user_fields():
    storage = _ConsentStorage()
    feature = LegalConsentFeature(storage, feature_manager=None)
    user = MagicMock()
    user.id = 99
    user.username = "tester"
    user.first_name = "Test"
    user.last_name = "User"
    user.language_code = "ru"
    user.is_premium = False
    user.is_bot = False
    user.model_dump.return_value = {"id": 99, "username": "tester"}

    callback = MagicMock()
    callback.from_user = user
    callback.id = "cb-1"
    callback.inline_message_id = None
    callback.message = MagicMock()
    callback.message.chat.id = 99
    callback.message.chat.type = "private"
    callback.message.chat.model_dump.return_value = {"id": 99, "type": "private"}
    callback.message.message_id = 10

    await feature._record_consent(user, callback, source="onboarding_start")
    assert len(storage.recorded) == 1
    uid, fields = storage.recorded[0]
    assert uid == 99
    assert fields["source"] == "onboarding_start"
    assert fields["username"] == "tester"
    assert fields["callback_query_id"] == "cb-1"
