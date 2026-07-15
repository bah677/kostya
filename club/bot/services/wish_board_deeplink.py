"""Deep link доски добрых дел: открытие в личке с ботом из клубного чата."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# /start=ddd — хаб; /start=ddd_don — пул дарителя; /start=ddd_angel — стать ангелом;
# /start=ddd_<id> — карточка просьбы
START_HUB = "ddd"
START_DONOR = "ddd_don"
START_ANGEL = "ddd_angel"
START_PREFIX_WISH = "ddd_"


@dataclass(frozen=True)
class WishBoardStartTarget:
    kind: str  # hub | donor | angel | wish
    wish_id: Optional[int] = None


def parse_wish_board_start_param(param: str) -> Optional[WishBoardStartTarget]:
    p = (param or "").strip()
    if not p:
        return None
    low = p.lower()
    if low in (START_HUB, "ddd_hub"):
        return WishBoardStartTarget(kind="hub")
    if low in (START_DONOR, "ddd_help", "ddd_pool"):
        return WishBoardStartTarget(kind="donor")
    if low in (START_ANGEL, "ddd_angel", "angel"):
        return WishBoardStartTarget(kind="angel")
    if low.startswith(START_PREFIX_WISH):
        tail = p[len(START_PREFIX_WISH) :]
        if tail.isdigit():
            return WishBoardStartTarget(kind="wish", wish_id=int(tail))
    return None


def build_wish_board_deeplink(
    bot_username: str,
    *,
    wish_id: Optional[int] = None,
    to_donor_pool: bool = False,
    to_angel: bool = False,
) -> str:
    """Ссылка ``https://t.me/<bot>?start=...`` — открывает личку с ботом."""
    username = (bot_username or "").strip().lstrip("@")
    if not username:
        return ""
    if wish_id is not None:
        payload = f"{START_PREFIX_WISH}{int(wish_id)}"
    elif to_angel:
        payload = START_ANGEL
    elif to_donor_pool:
        payload = START_DONOR
    else:
        payload = START_HUB
    return f"https://t.me/{username}?start={payload}"


def build_angel_pool_deeplink(bot_username: str) -> str:
    """Deep link сразу на экран «Стать ангелом»."""
    return build_wish_board_deeplink(bot_username, to_angel=True)
