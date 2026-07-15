"""Тексты (RU) для доски добрых дел."""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Dict, List, Optional

from bot.utils.telegram_html import sanitize_telegram_html

# --- Главный экран ---

HUB_TITLE_HTML = (
    "<b>💫 Доска добрых дел</b>\n\n"
    "Здесь участники клуба могут попросить поддержку, а любой пользователь бота — "
    "откликнуться и помочь.\n\n"
    "<b>Как это устроено:</b>\n"
    "• Просьбу может оставить только участник с <b>активной подпиской</b>\n"
    "• Откликнуться может <b>любой</b> пользователь бота\n"
    "• Каждая просьба проходит <b>модерацию</b>\n"
    "• Перевод денег через бота мы <b>не делаем</b> — если нужна сумма, "
    "напишите её в тексте и укажите реквизиты, даритель переведёт сам\n\n"
    "Просим относиться друг к другу с любовью и уважением 🙏\n\n"
    "<blockquote>Носите бремя друг друга, и таким образом исполните закон Христов.\n\n"
    "<i>(Гал. 6:2)</i></blockquote>"
)

BTN_REQUESTER = "🙏 Попросить помощь"
BTN_DONOR = "🎁 Откликнуться и помочь"
BTN_MY_WISHES = "📋 Мои просьбы"
BTN_MY_DONATIONS = "🤝 Мои отклики"
BTN_OTHER_WISHES = "🎁 Другие просьбы"
BTN_GENEROSITY = "⭐ Рейтинг щедрости"
BTN_BACK_HUB = "◀️ Назад"
BTN_MAIN_MENU = "🏠 Главное меню"

# --- Создание просьбы ---

CHOOSE_TYPE_HTML = (
    "<b>Что вам нужно?</b>\n\n"
    "• <b>Продление в клубе</b> — только подписка, даритель оплатит продление\n"
    "• <b>Другая помощь</b> — всё остальное (в т.ч. деньги на карту, если нужно)"
)

BTN_TYPE_SUBSCRIPTION = "📅 Продлить участие в клубе"
BTN_TYPE_OTHER = "💬 Другая просьба о помощи"

CHOOSE_ANON_HTML = (
    "<b>Показывать ваше имя дарителям?</b>\n\n"
    "Если выберете «Анонимно», в общем списке просьба будет без имени."
)

BTN_ANON_YES = "🕶 Анонимно"
BTN_ANON_NO = "👤 От своего имени"

MODERATION_PROMPT_HTML = (
    "<b>📝 Текст просьбы</b>\n\n"
    "Опишите, что вам нужно — <b>одним сообщением</b> (до 1500 символов).\n\n"
    "Можно указать сумму и номер карты, если нужна финансовая помощь — "
    "бот деньги не переводит, это сделает даритель сам.\n\n"
    "После отправки просьба уйдёт на модерацию."
)

MODERATION_PROMPT_SUBSCRIPTION_HTML = (
    "<b>📝 Просьба о продлении в клубе</b>\n\n"
    "Напишите <b>одним сообщением</b> (до 1500 символов):\n"
    "• на какой срок просите продлить участие (например, 1 месяц, 3 месяца)\n"
    "• почему сейчас нужна помощь — коротко и по делу\n\n"
    "После отправки просьба уйдёт на модерацию."
)

MODERATION_PROMPT_OTHER_HTML = MODERATION_PROMPT_HTML

BTN_GO_PAYMENT = "💳 Оплатить участие в клубе"

ERR_DESC_TOO_SHORT = "❌ Опишите просьбу чуть подробнее — минимум 10 символов."
ERR_DESC_TOO_LONG = "❌ Слишком длинный текст. Сократите до 1500 символов."
ERR_SESSION_EXPIRED = "❌ Сессия устарела. Начните снова: /menu → Доска добрых дел."
ERR_CREATE_FAILED = "❌ Не удалось создать просьбу. Попробуйте позже."

CREATED_PENDING_HTML = (
    "✅ <b>Просьба отправлена на модерацию</b>\n\n"
    "Мы проверим текст и опубликуем в общем списке — или напишем причину отказа.\n\n"
    "<blockquote>Всякое даяние доброе и всякий дар совершенный нисходит свыше, "
    "от Отца светов.\n\n"
    "<i>(Иак. 1:17)</i></blockquote>"
)

LIMIT_REACHED_HTML = (
    "<b>Лимит активных просьб</b>\n\n"
    "У вас уже есть максимум активных заявок.\n\n"
    "Дождитесь завершения или отмените одну из них в разделе «Мои просьбы»."
)

NOT_MEMBER_HTML = (
    "<b>Только для участников клуба</b>\n\n"
    "Оставлять просьбы могут только действующие участники "
    "с <b>активной подпиской</b>.\n\n"
    "Вы можете <b>оплатить участие в клубе</b> — после активации подписки "
    "сможете попросить помощь здесь."
)

# --- Пул и список ---

POOL_TITLE_HTML = (
    "<b>🎁 Открытые просьбы</b>\n\n"
    "Выберите, кому хотите помочь:"
)

POOL_EMPTY_HTML = (
    "<b>🎁 Откликнуться и помочь</b>\n\n"
    "Сейчас нет открытых просьб.\n\n"
    "Загляните позже — или загляните в клуб, там тоже много добрых дел 🙏\n\n"
    "<blockquote>Кто имеет достаток в мире, но, видя брата своего в нужде, "
    "затворяет от него свое милосердие, — как пребывает в том любовь Божия?\n\n"
    "<i>(1 Ин. 3:17)</i></blockquote>"
)

MY_WISHES_EMPTY_HTML = (
    "<b>📋 Мои просьбы</b>\n\n"
    "У вас пока нет просьб.\n\n"
    "Чтобы создать — нажмите «Попросить помощь»."
)

MY_WISHES_TITLE_HTML = "<b>📋 Мои просьбы</b>\n\nВыберите, чтобы посмотреть статус:"

MY_DONATIONS_TITLE_HTML = (
    "<b>🤝 Мои отклики</b>\n\n"
    "Здесь просьбы, которые вы взяли в работу как даритель."
)

BTN_MY_DONATIONS_ACTIVE = "⏳ В работе"
BTN_MY_DONATIONS_DONE = "✅ Завершённые"

MY_DONATIONS_ACTIVE_TITLE_HTML = (
    "<b>⏳ В работе</b>\n\n"
    "Просьбы, которые вы сейчас помогаете выполнить:"
)

MY_DONATIONS_DONE_TITLE_HTML = (
    "<b>✅ Завершённые</b>\n\n"
    "Просьбы, по которым помощь подтверждена:"
)

MY_DONATIONS_EMPTY_ACTIVE_HTML = (
    "<b>⏳ В работе</b>\n\n"
    "Сейчас нет активных откликов.\n\n"
    "Откройте «Откликнуться и помочь» в меню доски."
)

MY_DONATIONS_EMPTY_DONE_HTML = (
    "<b>✅ Завершённые</b>\n\n"
    "Пока нет завершённых откликов."
)

ERR_WISH_NOT_FOUND = "❌ Просьба не найдена."

# --- Действия дарителя / просителя ---

BTN_TAKE = "🤝 Взять эту просьбу"
BTN_RELEASE = "↩️ Отказаться от просьбы"
BTN_GIFT_SUB = "🎁 Подарить продление"
BTN_MARK_DONE = "✅ Помощь оказана"
BTN_CONFIRM = "✅ Подтверждаю, помощь получена"
BTN_DISPUTE = "⚠️ Есть проблема"
BTN_CANCEL_WISH = "🗑 Отменить просьбу"
BTN_ASK_CLARIFY = "💬 Уточнить детали"
BTN_REPLY_CLARIFY = "💬 Ответить дарителю"

ERR_DONOR_BUSY = (
    "❌ У вас уже есть просьба в работе.\n\n"
    "Сначала завершите её или откажитесь от текущей."
)
ERR_TAKE_FAILED = (
    "❌ Не удалось взять просьбу.\n\n"
    "Возможно, её уже взял другой даритель."
)
ERR_RELEASE_FAILED = "❌ Не удалось отказаться. Попробуйте позже."
ERR_GIFT_UNAVAILABLE = "❌ Подарок продления временно недоступен. Попробуйте позже."
ERR_MARK_DONE_FAILED = "❌ Не удалось отметить выполнение. Попробуйте позже."
ERR_CONFIRM_FAILED = "❌ Не удалось подтвердить. Попробуйте позже."
ERR_CANCEL_FAILED = "❌ Не удалось отменить просьбу. Попробуйте позже."

TAKEN_OK_SUBSCRIPTION_HTML = (
    "<b>🤝 Вы взяли просьбу о продлении</b>\n\n"
    "Оплатите подарок кнопкой ниже — после успешной оплаты просьба "
    "завершится автоматически, подтверждения не нужны."
)

TAKEN_OK_OTHER_HTML = (
    "<b>🤝 Вы взяли просьбу в работу</b>\n\n"
    "<b>Как дальше:</b>\n"
    "1. Свяжитесь с автором просьбы (личка или кнопка «Уточнить детали»)\n"
    "2. Окажите помощь\n"
    "3. Нажмите «Помощь оказана» — автор подтвердит получение\n\n"
    "Если нужны детали — используйте «Уточнить детали»."
)

MARK_DONE_OK_HTML = (
    "✅ <b>Отмечено</b>\n\n"
    "Ждём подтверждения от автора просьбы."
)

DONE_PENDING_REQUESTER_HTML = (
    "<b>Даритель отметил, что помощь оказана</b>\n\n"
    "Подтвердите, что всё получили — или сообщите, если есть проблема."
)

DISPUTE_OK_HTML = (
    "⚠️ <b>Просьба снова в общем списке</b>\n\n"
    "Мы уведомили администраторов. При необходимости напишите в поддержку."
)

ERR_DISPUTE_FAILED = "❌ Не удалось сообщить о проблеме. Попробуйте из карточки просьбы."

COMPLETED_HTML = (
    "<b>🎉 Просьба завершена</b>\n\n"
    "Спасибо, что делитесь добром! Оцените щедрость дарителя:\n\n"
    "<blockquote>Каждый уделяй по мере изобилия сердца своего, "
    "не с огорчением и не с принуждением; ибо доброхотно дающего любит Бог.\n\n"
    "<i>(2 Кор. 9:7)</i></blockquote>"
)

RATING_THANKS_HTML = "<b>Спасибо за оценку!</b> 🙏"

CLARIFY_DM_HTML = (
    "<b>💬 Уточнить детали</b>\n\n"
    "Автор просьбы не скрывал имя — лучше написать ему <b>напрямую в Telegram</b>:\n\n"
    "{contact}\n\n"
    "Так вы быстрее договоритесь о деталях помощи."
)

CLARIFY_ANON_PROMPT_HTML = (
    "<b>💬 Вопрос автору просьбы</b>\n\n"
    "Просьба анонимная — напишите <b>одним сообщением</b>, что нужно уточнить. "
    "Бот перешлёт автору <b>без вашего имени</b>."
)

CLARIFY_REPLY_PROMPT_HTML = (
    "<b>💬 Ответ дарителю</b>\n\n"
    "Напишите <b>одним сообщением</b> — бот перешлёт дарителю "
    "<b>без раскрытия вашего имени</b>."
)

CLARIFY_SENT_DONOR_HTML = "✅ Вопрос отправлен автору просьбы."
CLARIFY_SENT_REQUESTER_HTML = "✅ Ответ отправлен дарителю."

ERR_CLARIFY_TOO_SHORT = "❌ Напишите чуть подробнее — минимум 3 символа."
ERR_CLARIFY_NOT_ALLOWED = "❌ Сейчас нельзя отправить сообщение по этой просьбе."

ERR_RATING_ALREADY = "❌ Оценка уже была поставлена или просьба ещё не завершена."

# --- Уведомления пользователям ---

NOTIFY_TAKEN_REQUESTER_HTML = (
    "<b>🤝 Кто-то взял вашу просьбу в работу</b>\n\n"
    "{wish_summary}\n\n"
    "Скоро с вами свяжутся. Если в просьбе указаны контакты — "
    "даритель может написать напрямую."
)


def wish_summary_html(wish: Dict[str, Any], *, desc_max_len: int = 350) -> str:
    """Краткое описание просьбы для уведомлений и списков."""
    wid = int(wish["id"])
    gtype = GIFT_TYPE_LABELS.get(wish.get("gift_type") or "", wish.get("gift_type"))
    desc = sanitize_telegram_html(str(wish.get("description") or ""))
    if len(desc) > desc_max_len:
        desc = desc[: desc_max_len - 1] + "…"
    return (
        f"<b>Просьба #{wid}</b> — {escape(str(gtype))}\n"
        f"{desc}"
    )


def notify_dispute_donor_html(wish: Dict[str, Any]) -> str:
    return NOTIFY_DISPUTE_DONOR_HTML.format(
        wish_summary=wish_summary_html(wish),
    )


def clarify_to_requester_html(wish_id: int, text: str) -> str:
    body = sanitize_telegram_html(text)
    return (
        f"<b>💬 Вопрос по просьбе #{wish_id}</b>\n\n"
        f"Даритель спрашивает <b>анонимно</b>:\n\n"
        f"{body}\n\n"
        f"Ответьте кнопкой ниже — бот перешлёт дарителю без вашего имени."
    )


def clarify_to_donor_html(wish_id: int, text: str) -> str:
    body = sanitize_telegram_html(text)
    return (
        f"<b>💬 Ответ по просьбе #{wish_id}</b>\n\n"
        f"{body}"
    )


def clarify_dm_contact_html(requester: Dict[str, Any]) -> str:
    un = (requester.get("username") or "").strip()
    fn = (requester.get("first_name") or "").strip()
    if un:
        return f"<a href=\"https://t.me/{escape(un)}\">@{escape(un)}</a>"
    if fn:
        return f"<b>{escape(fn)}</b> (напишите в личку по имени в Telegram)"
    return "<b>автору просьбы</b> (напишите в личку в Telegram)"


def notify_taken_requester_html(wish: Dict[str, Any]) -> str:
    return NOTIFY_TAKEN_REQUESTER_HTML.format(
        wish_summary=wish_summary_html(wish),
    )


NOTIFY_APPROVED_REQUESTER_HTML = (
    "✅ <b>Просьба одобрена</b>\n\n"
    "Она появилась в общем списке — дарители смогут откликнуться."
)

NOTIFY_DONOR_CONFIRMED_HTML = (
    "<b>🎉 Автор просьбы подтвердил получение помощи</b>\n\n"
    "Спасибо за доброе дело! 🙏"
)

NOTIFY_DONOR_SUB_COMPLETED_HTML = (
    "<b>🎉 Продление в клубе оплачено</b>\n\n"
    "Просьба о продлении завершена автоматически. Спасибо за доброе дело! 🙏"
)

NOTIFY_DISPUTE_DONOR_HTML = (
    "<b>⚠️ Автор просьбы сообщил о проблеме</b>\n\n"
    "{wish_summary}\n\n"
    "Просьба снова в общем списке. При необходимости свяжитесь с поддержкой."
)

NOTIFY_TAKEN_TIMEOUT_HTML = (
    "<b>⏳ Даритель не завершил просьбу в срок</b>\n\n"
    "Просьба снова доступна в общем списке для других дарителей."
)

REJECT_NOTIFY_HTML = (
    "<b>❌ Просьба не прошла модерацию</b>\n\n"
    "<b>Комментарий:</b> {reason}"
)


def reject_notify_html(reason: str) -> str:
    return REJECT_NOTIFY_HTML.format(reason=escape(reason))


# --- Админ ---

BTN_ADM_APPROVE = "✅ Одобрить"
BTN_ADM_REJECT = "❌ Отказать"

MODERATION_NEW_HEADER_HTML = "<b>🆕 Новая просьба на модерацию</b>"

ADM_RESOLVED_APPROVED = "✅ Одобрено"
ADM_RESOLVED_REJECTED = "❌ Отклонено"

ADM_EVENT_TAKEN = "Взята дарителем"
ADM_EVENT_RELEASED = "Даритель отказался"
ADM_EVENT_DONE_PENDING = "Даритель отметил выполнение"
ADM_EVENT_COMPLETED = "Завершена"
ADM_EVENT_DISPUTE = "Автор просьбы сообщил о проблеме — снова в пуле"
ADM_EVENT_CANCELLED = "Отменена автором просьбы"
ADM_EVENT_APPROVED = "Одобрена → в общем списке"
ADM_EVENT_REJECTED = "Отклонена"
ADM_EVENT_EXPIRED = "Истекла (авто)"


def admin_event_taken_timeout(timeout_days: int) -> str:
    return f"Таймаут {timeout_days} дн. → снова в пуле"


ADM_NO_ACCESS = "⛔ Нет доступа"
ADM_APPROVED_OK = "✅ Одобрено"
ADM_ALREADY_HANDLED = "Уже обработано"
ADM_REJECT_PROMPT = (
    "Напишите <b>причину отказа</b> для просьбы #{wish_id} одним сообщением:"
)
ADM_REJECT_REASON_SHORT = "❌ Причина слишком короткая. Напишите подробнее."
ADM_REJECT_FAILED = "❌ Не удалось отклонить — возможно, заявка уже обработана."
ADM_REJECT_DONE = "✅ Просьба #{wish_id} отклонена, автор уведомлён."

# --- Дайджест ---

DIGEST_HEADER_HTML = "<b>💫 Новые просьбы на доске добрых дел</b> ({count})"
DIGEST_SINGLE_POST_HEADER_HTML = "<b>💫 Новая просьба на доске добрых дел</b>"
DIGEST_LINK_BOARD = "💫 Открыть доску добрых дел"
DIGEST_LINK_RESPOND = "Откликнуться в боте"
DIGEST_RESPOND_HINT = "/menu → Доска добрых дел → Откликнуться и помочь"

PASSIVE_COMPLETED_BANNER_HTML = (
    "<b>✨ Это желание уже исполнил кто-то другой</b>\n\n"
    "Просьба завершена — можно откликнуться на другие открытые просьбы."
)

PASSIVE_TAKEN_BANNER_HTML = (
    "<b>🤝 С этой просьбой уже помогает другой даритель</b>\n\n"
    "Откликнуться на неё уже нельзя — выберите другую из списка."
)

PASSIVE_DONE_PENDING_BANNER_HTML = (
    "<b>⏳ Помощь по этой просьбе уже оказывается</b>\n\n"
    "Ждём подтверждения от автора — пока можно помочь с другими просьбами."
)

GROUP_FULFILLED_REPLY_INTRO_HTML = (
    "<b>✨ Чья-то мечта исполнилась.</b>\n\n"
    "Просьба выполнена. Спасибо всем, кто откликается и помогает!"
)

GROUP_FULFILLED_SCRIPTURE_QUOTES = (
    (
        "<blockquote>Блажен человек, который умный, и бедного судит, "
        "нежели того, кто тщеславится и в то же время опустошает себя.\n\n"
        "<i>(Притч. 28:27)</i></blockquote>"
    ),
    (
        "<blockquote>Просите, и дано будет вам; ищите, и найдёте; стучите, "
        "и отворят вам.\n\n"
        "<i>(Мф. 7:7)</i></blockquote>"
    ),
    (
        "<blockquote>Ибо Бог не неправеден, чтобы забыть дело ваше и любовь, "
        "какую вы оказали во имя Его, послужив святым и ещё служа.\n\n"
        "<i>(Евр. 6:10)</i></blockquote>"
    ),
    (
        "<blockquote>Раздели хлеб твой с голодным, и бездомных и странников "
        "введи в дом; когда увидишь нагого, одень его, и от единокровного "
        "твоего не отвращайся.\n\n"
        "<i>(Ис. 58:7)</i></blockquote>"
    ),
)

# --- Карточка просьбы ---

GIFT_TYPE_LABELS = {
    "subscription": "Продление участия в клубе",
    "other": "Другая просьба о помощи",
    "immaterial": "Другая просьба о помощи",
}

STATUS_LABELS = {
    "pending_moderation": "На модерации",
    "rejected": "Отклонена",
    "open": "В общем списке",
    "taken": "Кто-то другой помогает",
    "done_pending": "Ждёт подтверждения",
    "completed": "Завершена",
    "cancelled": "Отменена",
    "expired": "Истекла",
}

STATUS_TAKEN_BY_YOU = "Вы помогаете"
STATUS_TAKEN_BY_OTHER = "Кто-то другой помогает"
STATUS_DONE_PENDING_YOURS = "Ждёт вашего подтверждения"
STATUS_DONE_PENDING_WAIT = "Ждём подтверждения автора"

CARD_LABEL_TYPE = "Тип"
CARD_LABEL_STATUS = "Статус"
CARD_LABEL_REQUESTER = "Автор просьбы"
CARD_LABEL_REQUESTER_ANON = "анонимно"
CARD_LABEL_LIST_UNTIL = "В общем списке до"
CARD_TITLE = "Просьба #{id}"
GENEROSITY_NEW_DONOR = "Новый даритель"

GENEROSITY_TITLE_HTML = (
    "<b>⭐ Рейтинг щедрости</b>\n\n"
    "После завершённой просьбы автор может оценить дарителя от 1 до 5 звёзд.\n"
    "Ниже — обобщённая статистика <b>без имён</b>.\n\n"
    "<blockquote>Каждый уделяй по мере изобилия сердца своего, "
    "не с огорчением и не с принуждением; ибо доброхотно дающего любит Бог.\n\n"
    "<i>(2 Кор. 9:7)</i></blockquote>"
)

GENEROSITY_EMPTY_HTML = (
    "<b>⭐ Рейтинг щедрости</b>\n\n"
    "Пока нет завершённых добрых дел с оценками — загляните позже."
)


def escape(text: str) -> str:
    return html.escape(text or "", quote=False)


def display_name(user: Dict[str, Any]) -> str:
    fn = (user.get("first_name") or "").strip()
    ln = (user.get("last_name") or "").strip()
    full = f"{fn} {ln}".strip()
    un = (user.get("username") or "").strip()
    if full and un:
        return f"{full} (@{un})"
    if full:
        return full
    if un:
        return f"@{un}"
    return f"ID {user.get('user_id')}"


def generosity_line(stats: Dict[str, Any]) -> str:
    count = int(stats.get("wishes_completed_as_donor") or 0)
    rating_count = int(stats.get("rating_count") or 0)
    rating_sum = int(stats.get("rating_sum") or 0)
    if count == 0 and rating_count == 0:
        return GENEROSITY_NEW_DONOR
    parts = [f"помог {count} раз"]
    if rating_count > 0:
        avg = rating_sum / rating_count
        parts.append(f"⭐ {avg:.1f} ({rating_count})")
    return ", ".join(parts)


def status_label_for_viewer(
    wish: Dict[str, Any], viewer_user_id: Optional[int] = None
) -> str:
    """Статус с учётом того, кто смотрит (вы помогаете / кто-то другой)."""
    status = wish.get("status") or ""
    viewer = int(viewer_user_id or 0)
    donor_id = wish.get("donor_user_id")
    requester_id = int(wish.get("requester_user_id") or 0)

    if status == "taken":
        if donor_id and int(donor_id) == viewer:
            return STATUS_TAKEN_BY_YOU
        return STATUS_TAKEN_BY_OTHER

    if status == "done_pending":
        if viewer == requester_id:
            return STATUS_DONE_PENDING_YOURS
        if donor_id and int(donor_id) == viewer:
            return STATUS_DONE_PENDING_WAIT

    return STATUS_LABELS.get(status, status)


def is_passive_wish_viewer(
    wish: Dict[str, Any], viewer_user_id: Optional[int] = None
) -> bool:
    """Сторонний зритель: просьба уже не в пуле и он не автор и не даритель."""
    status = wish.get("status") or ""
    if status == "open":
        return False
    viewer = int(viewer_user_id or 0)
    requester_id = int(wish.get("requester_user_id") or 0)
    donor_id = wish.get("donor_user_id")
    if viewer == requester_id:
        return False
    if donor_id and int(donor_id) == viewer:
        return False
    return status in ("taken", "done_pending", "completed")


def passive_wish_banner_html(wish: Dict[str, Any]) -> str:
    status = wish.get("status") or ""
    if status == "completed":
        return PASSIVE_COMPLETED_BANNER_HTML
    if status == "taken":
        return PASSIVE_TAKEN_BANNER_HTML
    if status == "done_pending":
        return PASSIVE_DONE_PENDING_BANNER_HTML
    return ""


def wish_card_with_passive_banner(
    wish: Dict[str, Any],
    card_html: str,
    *,
    viewer_user_id: Optional[int] = None,
) -> str:
    if not is_passive_wish_viewer(wish, viewer_user_id):
        return card_html
    banner = passive_wish_banner_html(wish)
    if not banner:
        return card_html
    return f"{banner}\n\n━━━━━━━━━━━━━━━━━━━━━\n\n{card_html}"


def group_fulfilled_reply_html(wish_id: int) -> str:
    quotes = GROUP_FULFILLED_SCRIPTURE_QUOTES
    quote = quotes[wish_id % len(quotes)] if quotes else ""
    if quote:
        return f"{GROUP_FULFILLED_REPLY_INTRO_HTML}\n\n{quote}"
    return GROUP_FULFILLED_REPLY_INTRO_HTML


def digest_single_post_html(
    wish: Dict[str, Any], *, respond_url: str = ""
) -> str:
    return (
        f"{DIGEST_SINGLE_POST_HEADER_HTML}\n\n"
        f"{digest_item_html(wish, respond_url=respond_url)}"
    )


def generosity_leaderboard_html(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return GENEROSITY_EMPTY_HTML
    lines = [GENEROSITY_TITLE_HTML, "", "<b>Топ дарителей:</b>"]
    for i, row in enumerate(rows, start=1):
        lines.append(f"{i}. {escape(generosity_line(row))}")
    return "\n".join(lines)


def format_wish_card(
    wish: Dict[str, Any],
    *,
    requester: Optional[Dict[str, Any]] = None,
    viewer_user_id: Optional[int] = None,
    for_admin: bool = False,
) -> str:
    wid = int(wish["id"])
    gtype = GIFT_TYPE_LABELS.get(wish.get("gift_type") or "", wish.get("gift_type"))
    if for_admin:
        status = STATUS_LABELS.get(wish.get("status") or "", wish.get("status"))
    else:
        status = status_label_for_viewer(wish, viewer_user_id)
    desc = sanitize_telegram_html(str(wish.get("description") or ""))

    lines = [
        f"<b>{CARD_TITLE.format(id=wid)}</b>",
        f"{CARD_LABEL_TYPE}: {escape(str(gtype))}",
        f"{CARD_LABEL_STATUS}: {escape(str(status))}",
    ]

    if for_admin:
        if not wish.get("is_anonymous"):
            if requester:
                lines.append(
                    f"{CARD_LABEL_REQUESTER}: {escape(display_name(requester))} "
                    f"(<code>{requester.get('user_id')}</code>)"
                )
            else:
                lines.append(
                    f"{CARD_LABEL_REQUESTER}: <code>{wish.get('requester_user_id')}</code>"
                )
        else:
            lines.append(
                f"{CARD_LABEL_REQUESTER}: {CARD_LABEL_REQUESTER_ANON} "
                f"(<code>{wish.get('requester_user_id')}</code>)"
            )
        if wish.get("donor_user_id"):
            lines.append(
                f"Даритель: <code>{wish['donor_user_id']}</code>"
            )
    else:
        if not wish.get("is_anonymous"):
            if requester:
                lines.append(
                    f"{CARD_LABEL_REQUESTER}: {escape(display_name(requester))}"
                )
            else:
                lines.append(f"{CARD_LABEL_REQUESTER}: участник клуба")
        else:
            lines.append(f"{CARD_LABEL_REQUESTER}: {CARD_LABEL_REQUESTER_ANON}")

    if wish.get("status") in ("open", "pending_moderation"):
        exp = wish.get("expires_at")
        if isinstance(exp, datetime):
            lines.append(
                f"{CARD_LABEL_LIST_UNTIL}: {exp.strftime('%d.%m.%Y')} "
                f"(потом снимется из списка)"
            )

    lines.append("")
    lines.append(desc)
    return "\n".join(lines)


def digest_header_html(count: int, *, board_url: str = "") -> str:
    base = DIGEST_HEADER_HTML.format(count=count)
    url = (board_url or "").strip()
    if url:
        base += f'\n\n<a href="{escape(url)}">{DIGEST_LINK_BOARD}</a>'
    return base


def digest_item_html(wish: Dict[str, Any], *, respond_url: str = "") -> str:
    wid = int(wish["id"])
    gtype = GIFT_TYPE_LABELS.get(wish.get("gift_type") or "", wish.get("gift_type"))
    desc = sanitize_telegram_html(str(wish.get("description") or ""))
    if len(desc) > 400:
        desc = desc[:397] + "…"
    anon = CARD_LABEL_REQUESTER_ANON if wish.get("is_anonymous") else "от имени участника"
    url = (respond_url or "").strip()
    if url:
        respond_line = f'<a href="{escape(url)}">{escape(DIGEST_LINK_RESPOND)}</a>'
    else:
        respond_line = f"Откликнуться: {escape(DIGEST_RESPOND_HINT)}"
    return (
        f"<b>#{wid}</b> — {escape(str(gtype))} ({anon})\n"
        f"{desc}\n"
        f"{respond_line}"
    )


def admin_event_html(
    event: str,
    wish: Dict[str, Any],
    *,
    extra: str = "",
) -> str:
    wid = int(wish["id"])
    base = f"<b>Доска #{wid}</b> — {escape(event)}"
    if extra:
        base += f"\n{sanitize_telegram_html(extra)}"
    return base
