"""Справка по боту для черновиков поддержки — собранная из кода.

Раньше в системном промпте `support_ai_draft` лежал список путей, написанный
руками. Он разошёлся с ботом: в промпте значилось «/menu → Оплатить подписку»
и «/menu → Подарить продление», а в меню кнопки называются «💳 Оплата и тарифы»
и «🎁 Подарить продление в Клубе». Модель добросовестно повторяла шпаргалку, и
люди шли искать несуществующие пункты.

Теперь справка собирается из тех же констант, что рисуют меню
(`bot.texts.ru_user_menu`) и из каталога команд (`bot.texts.help_catalog`).
Переименуют кнопку — справка поедет следом сама.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Что делает каждая кнопка меню. Ключ — имя константы в ru_user_menu,
#: чтобы при переименовании подписи связь не рвалась.
_BUTTON_PURPOSE = {
    "BTN_PAYMENT": "оплата и продление подписки для СЕБЯ, выбор тарифа и валюты",
    "BTN_SUBS": "срок своей подписки",
    "BTN_CLUB": "ссылка-приглашение в закрытую группу клуба",
    "BTN_SUPPORT": "написать в поддержку",
    "BTN_FEEDBACK": "оставить отзыв",
    "BTN_AFFILIATE": "реферальная ссылка и бонусы за приглашённых",
    "BTN_BENEFIT": "бонусы и материалы по акциям",
    "BTN_WISH_BOARD": "просьбы о помощи, в том числе о продлении подписки: "
                      "можно попросить за себя или откликнуться на чужую просьбу",
    "BTN_MEMBER_GIFT": "подарить продление ДРУГОМУ участнику: "
                       "сначала указывается получатель, потом оплата",
}

#: Кнопки, которых в меню может не быть: показ зависит от настроек.
_CONDITIONAL = {"BTN_WISH_BOARD", "BTN_BENEFIT", "BTN_AFFILIATE"}


def _menu_lines() -> List[str]:
    from bot.texts import ru_user_menu as menu_txt

    out: List[str] = []
    for const, purpose in _BUTTON_PURPOSE.items():
        label = getattr(menu_txt, const, None)
        if not label:
            continue
        mark = " (может быть скрыта настройками)" if const in _CONDITIONAL else ""
        out.append(f'· /menu → «{label}» — {purpose}{mark}')
    return out


def _command_lines() -> List[str]:
    from bot.texts.help_catalog import HELP_CATALOG

    out: List[str] = []
    for e in HELP_CATALOG:
        if e.tier != "user":
            continue
        cmd = (e.command or "").strip()
        if not cmd.startswith("/"):
            continue
        out.append(f"· {cmd} — {e.description}")
    return out


def build_bot_reference() -> str:
    """Актуальные пути бота. Пустая строка, если собрать не удалось."""
    try:
        menu = _menu_lines()
        cmds = _command_lines()
    except Exception as e:
        logger.warning("support_bot_reference: сборка не удалась: %s", e)
        return ""
    if not menu and not cmds:
        return ""
    parts = [
        "СПРАВКА ПО БОТУ (собрана из кода, названия точные — "
        "других пунктов меню НЕ существует):",
    ]
    if menu:
        parts.append("\nПункты меню:")
        parts.extend(menu)
    if cmds:
        parts.append("\nКоманды пользователя:")
        parts.extend(cmds)
    parts.append(
        "\nЕсли нужного пути в этом списке нет — значит, в боте его нет. "
        "Не придумывай название пункта и не отправляй человека искать его: "
        "честно напиши, что уточнишь и вернёшься с ответом."
    )
    return "\n".join(parts)


def known_menu_labels() -> List[str]:
    """Подписи кнопок — для проверки, что черновик не выдумал путь."""
    try:
        from bot.texts import ru_user_menu as menu_txt
    except Exception:
        return []
    out: List[str] = []
    for const in _BUTTON_PURPOSE:
        label = getattr(menu_txt, const, None)
        if label:
            out.append(str(label))
    return out


def find_invented_menu_paths(draft: str) -> List[str]:
    """Пути вида «/menu → X», которых в боте нет.

    Сравниваем по словам без эмодзи и регистра: модель часто пишет подпись
    близко к правде, но не дословно («Подарить продление» вместо
    «🎁 Подарить продление в Клубе») — такое пропускаем, а выдуманное ловим.
    """
    import re

    text = draft or ""
    labels = [_normalize(x) for x in known_menu_labels()]
    if not labels:
        return []
    bad: List[str] = []
    for m in re.finditer(r"/menu\s*(?:→|->|—|-|»|,)?\s*[«\"']?([^«»\"'\n.;:()]{3,48})", text):
        raw = m.group(1).strip()
        norm = _normalize(raw)
        if not norm:
            continue
        if any(norm in lab or lab in norm for lab in labels):
            continue
        bad.append(raw)
    return bad


def _normalize(s: str) -> str:
    import re

    t = re.sub(r"[^\w\s]", " ", (s or "").casefold(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()
