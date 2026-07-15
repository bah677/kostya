"""Тексты (RU) для OnboardingFeature (/start)."""

ONBOARDING_START_MESSAGES: tuple[str, ...] = ()

ONBOARDING_SEND_LICENSE_WELCOME = True
ONBOARDING_SEND_NO_LICENSE_WELCOME = True

TOPIC_BTN_MONEY = "про деньги"
TOPIC_BTN_RELATIONS = "про отношения"
CALLBACK_TOPIC_MONEY = "onboarding_topic_money"
CALLBACK_TOPIC_RELATIONS = "onboarding_topic_relations"

BTN_OPEN_GROUP = "📱 Открыть группу"
BTN_JOIN_GROUP = "🚪 Вступить в группу"

WELCOME_RETURN_TITLE = "✨ <b>С возвращением в клуб «Настоящая Я»!</b> ✨\n\n"

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
    "🙏 <b>Привет!</b>\n\n"
    "Я — ассистент клуба «Настоящая Я». Помогаю разобраться, о чём вообще этот клуб и кому он заходит.\n\n"
    "Коротко: говорят там не про «правильную веру», а про жизнь — деньги, отношения, усталость, поиск себя. Без фанатизма.\n\n"
    "Напиши, что тебя сейчас больше всего волнует — расскажу подробнее."
)


def welcome_subscribed_html(*, expires_str: str) -> str:
    return f"{WELCOME_RETURN_TITLE}📅 <b>Ваша подписка активна до {expires_str}</b>\n\n"
