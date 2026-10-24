"""Прозрачность Доски добрых дел — тексты и подсказки.

Повод — аудит docs/wish_board_audit.md: человек не понимал, какая просьба его
блокирует, не узнавал об отказе дарителя и об истечении срока, а выполненные
просьбы продолжали висеть в группе как живые.
"""

from __future__ import annotations

from bot.texts import ru_wish_board as wb


def _wish(**kw):
    base = {"id": 54, "button_title": "Продление участия в клубе на месяц"}
    base.update(kw)
    return base


def test_donor_busy_names_the_request():
    """Текст обязан назвать номер и суть — иначе человек её не найдёт."""
    html = wb.err_donor_busy_html(_wish(), taken_days=5)
    assert "№54" in html
    assert "Продление участия в клубе на месяц" in html
    assert "5 дн. назад" in html


def test_donor_busy_without_taken_date():
    html = wb.err_donor_busy_html(_wish(), taken_days=0)
    assert "№54" in html
    assert "дн. назад" not in html


def test_donor_busy_escapes_title():
    html = wb.err_donor_busy_html(_wish(button_title="<b>взлом</b> & co"))
    assert "<b>взлом</b>" not in html
    assert "&lt;b&gt;" in html


def test_taken_before_hint_only_for_repeats():
    assert wb.wish_taken_before_hint(0) == ""
    assert wb.wish_taken_before_hint(1) == ""
    assert "4" in wb.wish_taken_before_hint(4)
    assert "7" in wb.wish_taken_before_hint(7)


def test_self_payment_confirmation_warns_clearly():
    html = wb.confirm_self_payment_html(_wish(id=52))
    assert "№52" in html
    # Главное предупреждение: деньги зачтутся плательщику, а не просителю
    assert "зачтётся вам" in html


def test_self_payment_confirmation_escapes_title():
    html = wb.confirm_self_payment_html(_wish(button_title='сломать "кавычки" <i>'))
    assert "<i>" not in html.split("Сейчас оформляется")[0].replace("<b>", "")


def test_notifications_exist_and_are_human():
    for text in (
        wb.NOTIFY_DONOR_RELEASED_HTML,
        wb.NOTIFY_WISH_EXPIRED_HTML,
        wb.NOTIFY_AUTOCONFIRMED_REQUESTER_HTML,
        wb.NOTIFY_AUTOCONFIRMED_DONOR_HTML,
    ):
        assert len(text) > 40
        # без канцелярита и без обвинений в адрес человека
        assert "обязан" not in text.lower()
        assert "вы не" not in text.lower()


def test_released_notice_says_request_is_open_again():
    assert "снова открыта" in wb.NOTIFY_DONOR_RELEASED_HTML


def test_buttons_present():
    assert wb.BTN_OPEN_BLOCKING
    assert wb.BTN_PAY_FOR_WISH
    assert wb.BTN_PAY_FOR_SELF


# ── Автозакрытие «ждёт подтверждения» ────────────────────────────────────────
#
# Просьба №51 провисела в этом состоянии три недели, и автозакрытие её не
# видело: wish_mark_done ставил только status, а completed_at оставался пустым.
# Отсчитывать было не от чего.


def test_mark_done_records_the_moment():
    """В SQL отметки выполнения должен проставляться completed_at."""
    import inspect

    from storage.db.wish_board import WishBoardMixin

    src = inspect.getsource(WishBoardMixin.wish_mark_done)
    assert "completed_at" in src, "без completed_at состоянию нечем отсчитывать срок"
    assert "done_pending" in src


def test_autoconfirm_falls_back_to_updated_at():
    """Строки, отмеченные до исправления, тоже должны закрываться."""
    import inspect

    from storage.db.wish_board import WishBoardMixin

    src = inspect.getsource(WishBoardMixin.wish_autoconfirm_stale_done_pending)
    assert "COALESCE(completed_at, updated_at)" in src


# ── Напоминания дарителю ─────────────────────────────────────────────────────
#
# Было одно напоминание через 6 часов после отклика — и всё. При недельном
# сроке человек успевал забыть: 26 откликов из 44 кончились молчаливым
# таймаутом, просьбы стояли замороженными 90% своей жизни.


def test_final_remind_names_the_deadline():
    html = wb.donor_final_remind_html(_wish(), hours_left=4)
    assert "через 4 ч" in html
    assert "вернётся в общую ленту" in html


def test_final_remind_without_known_hours():
    html = wb.donor_final_remind_html(_wish(), hours_left=0)
    assert "совсем скоро" in html


def test_final_remind_does_not_shame():
    """Вернуть просьбу — нормально, а не провал."""
    html = wb.donor_final_remind_html(_wish(), hours_left=2)
    assert "нормально" in html
    for bad in ("обязаны", "подвели", "не выполнили"):
        assert bad not in html.lower()


def test_final_remind_escapes_title():
    html = wb.donor_final_remind_html(
        _wish(button_title="<b>взлом</b>"), hours_left=3
    )
    assert "<b>взлом</b>" not in html


def test_final_remind_window_is_before_timeout():
    """Окно предупреждения считается от таймаута, а не от момента отклика."""
    import inspect

    from storage.db.wish_board import WishBoardMixin

    src = inspect.getsource(WishBoardMixin.wish_list_taken_needing_final_remind)
    assert "make_interval(hours => $1 - $2)" in src
    assert "donor_final_reminded_at IS NULL" in src
