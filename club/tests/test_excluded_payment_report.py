"""Тесты отчёта по отвалившимся."""

from bot.services.excluded_payment_report import (
    ExcludedPaymentReport,
    NoPayUserDetail,
    PaymentProfileBucket,
    _aggregate_profiles,
    _resolve_no_pay_access_source,
    format_excluded_payment_html,
    format_profile_label,
)


def test_format_profile_label_mixed():
    assert format_profile_label({"td": 1, "1m": 2}) == "ТД ×1 + 1 мес ×2"


def test_aggregate_mixed_combinations():
    rows = [
        {"profile": {"td": 1, "1m": 1}},
        {"profile": {"td": 1, "1m": 1}},
        {"profile": {"td": 1, "1m": 2}},
        {"profile": {"td": 1}},
    ]
    counts = _aggregate_profiles(rows)
    assert sum(counts.values()) == 4
    assert max(counts.values()) == 2


def test_format_excluded_payment_html():
    report = ExcludedPaymentReport(
        period_days=None,
        total_churned=126,
        kicked_from_group=37,
        no_payments=5,
        buckets=[
            PaymentProfileBucket(profile={"td": 1}, users=11),
            PaymentProfileBucket(profile={"td": 1, "1m": 1}, users=3),
            PaymentProfileBucket(profile={"1m": 2}, users=3),
        ],
        no_pay_details=[
            NoPayUserDetail(
                user_id=439494208,
                first_name="Honey_LH",
                username="khanin_honey",
                access_source="/gift админом Admin (@admin) (31 дн.)",
            ),
        ],
    )
    html = format_excluded_payment_html(report)
    assert "126 чел." in html
    assert "37 чел." in html
    assert "ТД ×1 + 1 мес ×1" in html
    assert "Без оплат — расшифровка:" in html
    assert "439494208" in html
    assert "khanin_honey" in html
    assert "Смешанные профили" not in html


def test_resolve_no_pay_access_source_gift():
    row = {
        "gift_donor_id": 311129899,
        "gift_donor_first_name": "Таня",
        "gift_donor_username": "tanya",
    }
    assert _resolve_no_pay_access_source(row) == "Подарок от Таня (@tanya)"


def test_resolve_no_pay_access_source_admin_gift():
    row = {
        "admin_id": 304631563,
        "admin_first_name": "Admin",
        "admin_username": "admin",
        "admin_days": 7,
    }
    assert _resolve_no_pay_access_source(row) == "/gift админом Admin (@admin) (7 дн.)"
