"""Тесты бенефитов и promo_test1week_benefit* колбэков."""

from bot.features.benefit import build_benefit3_deeplink
from bot.features.payment import (
    resolve_promo_tariff_type_from_payment_start_suffix,
    resolve_promo_tariff_type_from_start_param,
)
from bot.texts import ru_benefit as benefit_txt


def test_benefit3_deeplink():
    assert build_benefit3_deeplink("Talk_God_Bot") == (
        "https://t.me/Talk_God_Bot?start=benefit3"
    )
    assert benefit_txt.START_PARAM_BENEFIT3 == "benefit3"


def test_gratitude_payment_callback_resolves_to_promo_test1week():
    cb = benefit_txt.PROMO_PAYMENT_CALLBACK_GRATITUDE
    assert cb == "payment_start_promo_test1week_benefit3"
    promo_full = cb.replace("payment_start_", "")
    assert promo_full == "promo_test1week_benefit3"
    assert resolve_promo_tariff_type_from_payment_start_suffix(promo_full) == (
        "promo_test1week"
    )
    assert resolve_promo_tariff_type_from_start_param("promo_test1week_benefit3") == (
        "promo_test1week"
    )


def test_benefit_menu_has_gratitude_callback():
    assert benefit_txt.CALLBACK_PRAYER_GRATITUDE == "benefit_prayer_gratitude"
    assert benefit_txt.PROMO_AFTER_AUDIO_DELAY_GRATITUDE_SECONDS == 10
