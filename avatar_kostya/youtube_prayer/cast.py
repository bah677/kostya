"""Постоянные герои роликов: банк кадров вместо случайного стока.

Сток даёт приличную картинку, но безликую: двенадцать чужих клипов без связи
между собой. Зритель не возвращается к каналу, потому что возвращаться не к
кому. Здесь — двое героев, которых узнают со второго ролика.

Героиня — женщина около пятидесяти, та самая целевая зрительница: она должна
узнать в кадре себя, а не модель. Поэтому обычное лицо, обычная одежда,
обычная кухня. Второй герой — Иисус, в иконографической традиции, без
портретных крупных планов в упор: чаще рядом, со спины, рука на плече.

Сильнее всего работает третий тип кадра — они вдвоём. Это и есть обещание
канала: ты не одна.

Банк собирается один раз и переиспользуется: тем у нас конечное число,
рисовать каждый ролик заново незачем.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)

# ── Общая гамма ──────────────────────────────────────────────────────────────
# Тёплая и позитивная: золотистый свет, янтарь, мягкие тени. Ни синевы, ни
# холодного контраста — канал про утешение, а не про драму.

STYLE = (
    "cinematic film still, vertical 9:16 composition, shot on 50mm lens, "
    "shallow depth of field, warm golden hour light, amber and honey tones, "
    "soft diffused glow, gentle film grain, hopeful and tender mood, "
    "natural skin tones, no harsh shadows, no cold blue cast, "
    "photorealistic, high detail, no text, no letters, no logos, no watermark"
)

# ── Герои ────────────────────────────────────────────────────────────────────
# Описание подробное и неизменное: именно оно держит узнаваемость от кадра к
# кадру. Менять его после сборки банка нельзя — героиня перестанет быть собой.

WOMAN = (
    "a Slavic woman around 50 years old, kind tired face with soft wrinkles, "
    "warm hazel eyes, shoulder-length ash-brown hair with grey strands loosely "
    "pinned back, no makeup, wearing a simple cream knitted cardigan over a "
    "soft blouse, a thin gold wedding band, ordinary and real, not a model"
)

JESUS = (
    "Jesus Christ depicted in the traditional reverent iconographic manner, "
    "a gentle man with long dark hair and a short beard, calm compassionate "
    "eyes, wearing a simple earth-toned linen robe with a deep red mantle, "
    "surrounded by soft warm light, dignified and serene, never theatrical"
)


@dataclass(frozen=True)
class Scene:
    key: str
    # Кого показываем: woman | jesus | both
    who: str
    # Ракурс и крупность — идут ПЕРВЫМИ в промпте, иначе модель их теряет.
    shot: str
    prompt: str
    # Темы, к которым кадр подходит. Пусто — подходит к любой.
    topics: Tuple[str, ...] = field(default_factory=tuple)


# ── Кадры ────────────────────────────────────────────────────────────────────
# Три слоя: беда (героиня одна), присутствие (Иисус), утешение (вдвоём).
# В ролике они идут именно в этом порядке — так собирается маленькая история.

SCENES: Tuple[Scene, ...] = (
    # — беда —
    Scene("kitchen_night", "woman",
          "medium shot, camera at eye level across the table",
          "sitting alone at a small kitchen table late in the evening, "
          "a cooling cup of tea in front of her, looking down at her hands, "
          "warm lamp light from above",
          ("тревога", "одиночество", "усталость", "долги")),
    Scene("window_rain", "woman",
          "medium shot from the side, she is in profile against the window",
          "standing by a window, forehead almost touching the glass, warm "
          "light behind her, soft rain outside catching the golden light",
          ("тревога", "страх", "одиночество")),
    Scene("bills_table", "woman",
          "medium shot slightly above, the table and papers visible",
          "at a table with papers and bills spread out, one hand on her "
          "forehead, warm desk lamp, quiet evening",
          ("долги", "деньги", "работа")),
    Scene("bed_sleepless", "woman",
          "wide shot from the side, she is small in the frame",
          "lying awake in bed in the early morning twilight, eyes open, warm "
          "amber light beginning to come through the curtains",
          ("бессонница", "тревога", "страх")),
    Scene("bus_window", "woman",
          "close shot through the bus window from outside, reflections on glass",
          "sitting by a bus window on the way to work, soft morning light on "
          "her face, city blurred warmly outside",
          ("усталость", "работа", "дорога")),
    Scene("doorway_quarrel", "woman",
          "wide shot, she is small in a warm home interior, seen from behind",
          "standing in a doorway looking back into another room, quiet after "
          "an argument, soft evening light",
          ("семья", "ссора", "прощение")),
    # — присутствие —
    Scene("jesus_light", "jesus",
          "wide shot from a respectful distance, full figure in the frame",
          "standing in a field at golden hour, light falling around him, "
          "arms open and welcoming",
          ()),
    Scene("jesus_hands", "jesus",
          "extreme close-up of hands only, his face is NOT in the frame",
          "open palms held gently forward in warm light, soft golden "
          "background, nothing else in the shot",
          ()),
    Scene("jesus_path", "jesus",
          "wide shot from directly BEHIND him, we see his back and the road "
          "ahead, his face is NOT visible",
          "walking away along a quiet path at sunrise, warm light ahead of him",
          ()),
    # — утешение, главные кадры канала —
    Scene("hand_shoulder", "both",
          "medium shot, she is seated in the foreground, he stands behind her",
          "his hand resting gently on her shoulder, she does not see him, "
          "warm kitchen light, tender",
          ()),
    Scene("walk_together", "both",
          "wide shot from BEHIND both of them, we see only their backs and "
          "the road ahead, no faces visible at all",
          "walking side by side along a quiet country road at golden hour, "
          "unhurried, warm light ahead of them",
          ()),
    Scene("sitting_beside", "both",
          "wide shot from the side, both seen in profile on the bench",
          "sitting beside each other on a wooden bench in a sunlit park, she "
          "is talking, he is listening, warm afternoon light",
          ()),
    Scene("table_together", "both",
          "medium shot from the side, both at the table in profile",
          "sitting across a small kitchen table from each other, two cups of "
          "tea, warm morning light through the window, peaceful",
          ()),
)

_BY_WHO: Dict[str, List[Scene]] = {}
for _s in SCENES:
    _BY_WHO.setdefault(_s.who, []).append(_s)


def character_prompt(who: str) -> str:
    if who == "woman":
        return WOMAN
    if who == "jesus":
        return JESUS
    # Два персонажа описываем раздельно и проговариваем, чьи признаки чьи.
    # Иначе они перетекают: на первом же парном кадре обручальное кольцо
    # героини оказалось на руке Иисуса.
    return (
        f"TWO PEOPLE. First person: {WOMAN}. "
        f"Second person: {JESUS}. "
        "Only the woman wears a ring; his hands are bare. "
        "Their clothing never mixes: her knitted cardigan is hers alone, "
        "his linen robe and red mantle are his alone"
    )


def full_prompt(scene: Scene) -> str:
    """Сначала кадр, потом герои, потом действие.

    Порядок важен: если начинать с описания лиц, модель уходит в портрет и
    игнорирует ракурс. На кадре «идут по дороге со спины» так и вышло —
    получился фронтальный двойной портрет.
    """
    return f"{scene.shot}. {character_prompt(scene.who)}. {scene.prompt}. {STYLE}"


def scenes_for_topic(topic: str, *, count: int = 8) -> List[Scene]:
    """Кадры под тему: сначала беда, потом присутствие, потом утешение.

    Порядок не случайный — он и есть маленькая история, ради которой зритель
    досматривает: узнал себя, увидел, что не один, ушёл с теплом.
    """
    t = (topic or "").lower()

    def matches(s: Scene) -> bool:
        return not s.topics or any(k in t for k in s.topics)

    trouble = [s for s in _BY_WHO.get("woman", []) if matches(s)]
    if not trouble:
        trouble = list(_BY_WHO.get("woman", []))
    presence = list(_BY_WHO.get("jesus", []))
    comfort = list(_BY_WHO.get("both", []))

    out: List[Scene] = []
    # Примерно треть беды, треть присутствия, остальное — утешение.
    n_trouble = max(1, count // 3)
    n_presence = max(1, count // 4)
    out += trouble[:n_trouble]
    out += presence[:n_presence]
    out += comfort[: max(1, count - len(out))]
    return out[:count]


def all_scene_keys() -> List[str]:
    return [s.key for s in SCENES]


def scene_by_key(key: str) -> Scene | None:
    for s in SCENES:
        if s.key == key:
            return s
    return None
