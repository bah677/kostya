"""Тексты (RU) для OnboardingFeature (/start)."""

# --- сценарий после /start (порядок: кружки из media_file_ids, затем эти сообщения) ---

# HTML-сообщения после video note. Пустой кортеж — только медиа.
# Важно: после текста нужна запятая — иначе это str, а не tuple, и уйдёт по символу.
ONBOARDING_START_MESSAGES: tuple[str, ...] = (
    """🙏 Привет ещё раз.

Я — ассистент клуба «Любящие Бога». Помогаю разобраться, о чём вообще этот клуб и кому он заходит.

Напиши, что тебя сейчас больше всего волнует — расскажу подробнее.""",
)

# После медиа/сообщений: приветствие с датой подписки и кнопкой в группу.
ONBOARDING_SEND_LICENSE_WELCOME = False

# После медиа/сообщений: текст для пользователя без активной подписки.
ONBOARDING_SEND_NO_LICENSE_WELCOME = False

TOPIC_BTN_MONEY = "про деньги"
TOPIC_BTN_RELATIONS = "про отношения"
CALLBACK_TOPIC_MONEY = "onboarding_topic_money"
CALLBACK_TOPIC_RELATIONS = "onboarding_topic_relations"

BTN_OPEN_GROUP = "📱 Открыть группу"
BTN_JOIN_GROUP = "🚪 Вступить в группу"

WELCOME_RETURN_TITLE = "✨ <b>С возвращением в клуб «Разговоры с Богом»!</b> ✨\n\n"

WELCOME_HINT_OPEN_POST = "Нажмите кнопку ниже, чтобы открыть группу 👇"
WELCOME_HINT_JOIN = "Нажмите кнопку ниже, чтобы вступить в группу 👇"
WELCOME_HINT_UNCONFIGURED = (
    "Ссылку на посты в клубе временно нужно получить через /support — "
    "кураторы подскажут. Также можно использовать команду /club."
)
WELCOME_HINT_NO_LINK = (
    "Ссылку в закрытую группу можно запросить через /support — кураторы помогут."
)

WELCOME_NO_LICENSE_HTML = (
    "🙏 <b>Привет ещё раз.</b>\n\n"
    "Я — ассистент клуба «Любящие Бога». Помогаю разобраться, о чём вообще этот клуб и кому он заходит.\n\n"
    "Коротко: говорят там не про «правильную веру», а про жизнь — деньги, отношения, усталость, поиск себя. Без фанатизма.\n\n"
    "Напиши, что тебя сейчас больше всего волнует — расскажу подробнее."
)


def welcome_subscribed_html(*, expires_str: str) -> str:
    return f"{WELCOME_RETURN_TITLE}📅 <b>Ваша подписка активна до {expires_str}</b>\n\n"
