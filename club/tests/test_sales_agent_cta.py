"""Тесты CTA продающего агента."""

from bot.services.sales_agent_cta import (
    analyze_private_history_for_sales_cta,
    apply_sales_cta_policy,
    text_has_explicit_payment_intent,
    text_has_price_objection,
)
from bot.utils.telegram_html import strip_agent_cta


def test_first_reply_blocks_subscribe_without_payment_intent():
    body, sub, promo = strip_agent_cta(
        "Понимаю вас.<<<CTA_SUBSCRIBE>>>"
    )
    sub, promo = apply_sales_cta_policy(
        wants_subscribe=sub,
        wants_promo_week=promo,
        assistant_replies_in_history=0,
        first_user_message="Мне одиноко",
        current_user_message="Мне одиноко",
    )
    assert sub is False
    assert promo is False
    assert "CTA" not in body


def test_first_reply_allows_cta_when_user_asked_price():
    _, sub, _ = strip_agent_cta("Стоимость такая.<<<CTA_SUBSCRIBE>>>")
    sub, promo = apply_sales_cta_policy(
        wants_subscribe=sub,
        wants_promo_week=False,
        assistant_replies_in_history=0,
        first_user_message="Сколько стоит клуб?",
        current_user_message="Сколько стоит клуб?",
    )
    assert sub is True
    assert promo is False


def test_promo_week_only_on_explicit_objection():
    _, sub, promo = strip_agent_cta(
        "Можно неделю за 299.<<<CTA_PROMO_WEEK>>>"
    )
    sub, promo = apply_sales_cta_policy(
        wants_subscribe=sub,
        wants_promo_week=promo,
        assistant_replies_in_history=2,
        first_user_message="Расскажи про клуб",
        current_user_message="Дорого для меня",
    )
    assert promo is True
    assert sub is False


def test_promo_week_blocked_without_objection():
    _, sub, promo = strip_agent_cta(
        "Можно неделю за 299.<<<CTA_PROMO_WEEK>>>"
    )
    sub, promo = apply_sales_cta_policy(
        wants_subscribe=sub,
        wants_promo_week=promo,
        assistant_replies_in_history=2,
        first_user_message="Расскажи про клуб",
        current_user_message="Надо подумать",
    )
    assert promo is False


def test_price_objection_detection():
    assert text_has_price_objection("Мне дорого")
    assert not text_has_price_objection("Надо подумать")


def test_payment_intent_detection():
    assert text_has_explicit_payment_intent("Как вступить в клуб?")
    assert not text_has_explicit_payment_intent("Привет")


def test_history_analysis():
    n, first = analyze_private_history_for_sales_cta(
        [
            {"role": "user", "content": "/start"},
            {"role": "user", "content": "Устал"},
            {"role": "assistant", "content": "Понимаю"},
        ]
    )
    assert n == 1
    assert first == "Устал"
