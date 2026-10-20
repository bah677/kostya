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
from dataclasses import dataclass, field, replace
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

# Вторая героиня. Первая — про узнавание: такой зрительница видит себя в
# зеркале вечером. Эта — про то, какой она себя хочет: тот же возраст и тот
# же круг, но выспавшаяся и в порядке. Не моложе лет на двадцать и не
# модель — иначе узнавание ломается и остаётся чужая реклама.
WOMAN_BRIGHT = (
    "a Slavic woman in her mid-forties, calm well-rested face with smooth "
    "skin and only faint soft lines, warm grey-green eyes, thick chestnut "
    "hair neatly cut to the shoulders with a few lighter strands, barely "
    "visible natural makeup, wearing a soft ivory turtleneck under a light "
    "grey cardigan, small pearl earrings, a thin gold wedding band, "
    "unhurried and well kept, the way an ordinary woman looks on a good day, "
    "never glamorous, not a model"
)

JESUS = (
    "Jesus Christ depicted in the traditional reverent iconographic manner, "
    "a gentle man with long dark hair and a short beard, calm compassionate "
    "eyes, wearing a simple earth-toned linen robe with a deep red mantle, "
    "surrounded by soft warm light, dignified and serene, never theatrical"
)


@dataclass(frozen=True)
class Heroine:
    """Одна и та же роль, разные актрисы.

    Внутри одного ролика героиня обязана быть одна: кадры идут подряд в
    одном сюжете, и смена лица на третьей секунде читается как другой
    человек. Поэтому выбор делается на ролик целиком, а не на кадр.
    """

    key: str
    label: str
    note: str
    # Ключ в файле правок и имя папки в банке. У первой героини — «woman»
    # и папки без суффикса: её кадры уже нарисованы, переименовывать нельзя.
    store_key: str
    default_prompt: str


DEFAULT_HEROINE = "real"

HEROINES: Tuple[Heroine, ...] = (
    Heroine(
        "real", "Как есть", "Та, в ком зрительница узнаёт себя.",
        "woman", WOMAN,
    ),
    Heroine(
        "bright", "Какой хочется быть", "Тот же возраст и круг, но в порядке.",
        "woman_bright", WOMAN_BRIGHT,
    ),
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
    # Что движется, если кадр оживляют. Пусто — модель решит сама, а это
    # всегда лишнее: она норовит двинуть камеру и повернуть героиню.
    motion: str = ""
    # Чья это сцена. Пусто — общая: кадры с Иисусом одни на всех.
    #
    # У героинь свои наборы сцен, а не один общий. Смысл слоя и темы
    # совпадают — беда, присутствие, утешение, — но интерьеры разные:
    # две героини у одного и того же окна это не разнообразие, а повтор.
    heroine: str = ""


# ── Кадры ────────────────────────────────────────────────────────────────────
# Три слоя: беда (героиня одна), присутствие (Иисус), утешение (вдвоём).
# В ролике они идут именно в этом порядке — так собирается маленькая история.

_REAL_SCENES: Tuple[Scene, ...] = (
    # — беда —
    Scene("kitchen_night", "woman",
          "medium shot, camera at eye level across the table",
          "sitting alone at a small kitchen table late in the evening, "
          "a cooling cup of tea in front of her, looking down at her hands, "
          "warm lamp light from above",
          ("тревога", "одиночество", "усталость", "долги"),
          motion=(
              "Steam drifts slowly up from the cooling tea. She breathes, blinks "
              "once, and her fingers shift slightly against each other. The lamp "
              "light stays steady."
          ),
          ),
    Scene("window_rain", "woman",
          "medium shot from the side, she is in profile against the window",
          "standing by a window, forehead almost touching the glass, warm "
          "light behind her, soft rain outside catching the golden light",
          ("тревога", "страх", "одиночество"),
          motion=(
              "Rain runs down the glass outside. She breathes slowly and blinks; a "
              "loose strand of hair settles back against her temple."
          ),
          ),
    Scene("bills_table", "woman",
          "medium shot slightly above, the table and papers visible",
          "at a table with papers and bills spread out, one hand on her "
          "forehead, warm desk lamp, quiet evening",
          ("долги", "деньги", "работа"),
          motion=(
              "She breathes out slowly, the hand at her forehead presses a little "
              "and then eases. The papers lie still, the lamp light stays steady."
          ),
          ),
    Scene("bed_sleepless", "woman",
          "wide shot from the side, she is small in the frame",
          "lying awake in bed in the early morning twilight, eyes open, warm "
          "amber light beginning to come through the curtains",
          ("бессонница", "тревога", "страх"),
          motion=(
              "She breathes slowly and blinks once. The curtain stirs faintly and "
              "the dawn light grows a shade warmer."
          ),
          ),
    Scene("bus_window", "woman",
          "close shot through the bus window from outside, reflections on glass",
          "sitting by a bus window on the way to work, soft morning light on "
          "her face, city blurred warmly outside",
          ("усталость", "работа", "дорога"),
          motion=(
              "The blurred warm city slides past outside the window. Her reflection "
              "shifts gently with the movement of the bus and she blinks."
          ),
          ),
    Scene("doorway_quarrel", "woman",
          "wide shot, she is small in a warm home interior, seen from behind",
          "standing in a doorway looking back into another room, quiet after "
          "an argument, soft evening light",
          ("семья", "ссора", "прощение"),
          motion=(
              "She breathes in and out once and her shoulders lower a little. The "
              "evening light on the walls stays steady."
          ),
          ),
    # — присутствие —
    Scene("jesus_light", "jesus",
          "wide shot from a respectful distance, full figure in the frame",
          "standing in a field at golden hour, light falling around him, "
          "arms open and welcoming",
          (),
          motion=(
              "His robe and hair move gently in a light breeze and the golden grass "
              "sways around him. He stays exactly where he is."
          ),
          ),
    Scene("jesus_hands", "jesus",
          "extreme close-up of hands only, his face is NOT in the frame",
          "open palms held gently forward in warm light, soft golden "
          "background, nothing else in the shot",
          (),
          motion=(
              "The open palms stay still; only the warm light shifts slowly across "
              "the skin and fine dust drifts through it."
          ),
          ),
    Scene("jesus_path", "jesus",
          "wide shot from directly BEHIND him, we see his back and the road "
          "ahead, his face is NOT visible",
          "walking away along a quiet path at sunrise, warm light ahead of him",
          (),
          motion=(
              "He takes a few slow steps away along the path and his robe moves "
              "with them. The camera stays where it is."
          ),
          ),
    # — утешение, главные кадры канала —
    Scene("hand_shoulder", "both",
          "medium shot, she is seated in the foreground, he stands behind her",
          "his hand resting gently on her shoulder, she does not see him, "
          "warm kitchen light, tender",
          (),
          motion=(
              "His hand stays resting on her shoulder. She breathes and lowers her "
              "eyes. Neither of them turns."
          ),
          ),
    Scene("walk_together", "both",
          "wide shot from BEHIND both of them, we see only their backs and "
          "the road ahead, no faces visible at all",
          "walking side by side along a quiet country road at golden hour, "
          "unhurried, warm light ahead of them",
          (),
          motion=(
              "They walk slowly away along the road, seen from behind, their "
              "clothes moving with their steps."
          ),
          ),
    Scene("sitting_beside", "both",
          "wide shot from the side, both seen in profile on the bench",
          "sitting beside each other on a wooden bench in a sunlit park, she "
          "is talking, he is listening, warm afternoon light",
          (),
          motion=(
              "She speaks quietly, her hands moving a little as she talks; he "
              "listens and nods once. Leaves move behind them."
          ),
          ),
    Scene("table_together", "both",
          "medium shot from the side, both at the table in profile",
          "sitting across a small kitchen table from each other, two cups of "
          "tea, warm morning light through the window, peaceful",
          (),
          motion=(
              "Steam rises from both cups. She breathes; he raises his eyes to her "
              "for a moment."
          ),
          ),
)

# Сцены второй героини. Слои и темы те же — беда, присутствие, утешение,
# — а места другие: балкон вместо окна, храм вместо кухни, машина вместо
# автобуса. Общее должно быть в смысле, а не в мебели.

_BRIGHT_SCENES: Tuple[Scene, ...] = (
    # — беда —
    Scene("balcony_dawn", "woman",
          "medium shot from the side, she is in profile against the open sky",
          "standing on a small balcony at dawn wrapped in a knitted blanket, "
          "a mug held in both hands, the sleeping city far below, first warm "
          "light on her face",
          ("тревога", "бессонница", "одиночество"),
          motion=(
              "Steam drifts up from the mug in her hands. She breathes slowly and "
              "blinks; the dawn light on her face grows a shade warmer."
          ),
          ),
    Scene("church_empty", "woman",
          "wide shot from behind and above, she is small in the empty nave",
          "sitting alone on a wooden pew in an empty church, hands in her "
          "lap, a tall shaft of warm light falling from a high window",
          ("страх", "вина", "покаяние", "одиночество"),
          motion=(
              "Fine dust drifts through the shaft of light. She breathes and lowers "
              "her eyes. Nothing else moves."
          ),
          ),
    Scene("car_parked", "woman",
          "medium shot through the windscreen from outside, soft reflections",
          "sitting in a parked car with both hands still on the wheel, not "
          "getting out yet, warm evening light across the dashboard",
          ("работа", "усталость", "решение"),
          motion=(
              "Her hands stay on the wheel, the fingers tighten slightly and ease "
              "again. She breathes and blinks once."
          ),
          ),
    Scene("hallway_mirror", "woman",
          "medium shot, her reflection in the hallway mirror, she is seen "
          "from slightly behind",
          "standing in the hallway already dressed to go out, looking at "
          "herself in the mirror and hesitating, warm lamp above the mirror",
          ("страх", "сомнение", "решение"),
          motion=(
              "She breathes once, looks a moment longer at her reflection and "
              "lowers her eyes. The lamp light stays steady."
          ),
          ),
    Scene("purse_counter", "woman",
          "close shot slightly above, her hands and the counter in frame",
          "counting the last banknotes from an open purse on the kitchen "
          "counter, warm light from the window behind, quiet concentration",
          ("долги", "деньги"),
          motion=(
              "Her fingers move over the banknotes, counting slowly, and she "
              "breathes out."
          ),
          ),
    Scene("stairwell_phone", "woman",
          "wide shot, she is small against the stairwell wall",
          "standing on a tiled landing with the phone lowered in her hand, "
          "the call just ended, warm light falling down the stairwell",
          ("семья", "ссора", "прощение"),
          motion=(
              "She lowers the phone a little further and breathes out slowly; her "
              "shoulders drop. The light stays steady."
          ),
          ),
    # — утешение —
    Scene("porch_evening", "both",
          "wide shot from the side, both seated on the steps in profile",
          "sitting side by side on wooden porch steps in the late evening "
          "light, unhurried, a garden softly out of focus behind them",
          (),
          motion=(
              "They sit still while the leaves behind them move in a light breeze. "
              "She breathes; he turns his head slightly toward her."
          ),
          ),
    Scene("colonnade_walk", "both",
          "wide shot from directly BEHIND both of them, only their backs and "
          "the sunlit arches ahead, no faces visible at all",
          "walking slowly along a sunlit stone colonnade, warm light pouring "
          "between the columns",
          (),
          motion=(
              "They walk slowly away between the columns, seen from behind, and the "
              "light shifts across them as they pass each arch."
          ),
          ),
    Scene("hands_over_hands", "both",
          "extreme close-up of hands only, NO faces in the frame at all",
          "his open palms resting over her folded hands, warm golden light, "
          "soft blurred background, nothing else in the shot",
          (),
          motion=(
              "The hands stay together and only breathing moves them; the warm "
              "light drifts slowly across the skin."
          ),
          ),
    Scene("garden_bed", "both",
          "medium shot from the side, both of them among the green",
          "kneeling together by a small garden bed, she is setting a young "
          "plant into the soil and he is steadying the stem, warm afternoon "
          "sun, green and gold",
          (),
          motion=(
              "She presses the soil gently around the young plant and its leaves "
              "tremble. He steadies the stem."
          ),
          ),
)


def _tagged(rows: Tuple[Scene, ...], key: str) -> Tuple[Scene, ...]:
    """Сцены героини. Кадры с Иисусом общие — их никому не приписываем."""
    return tuple(
        replace(s, heroine=("" if s.who == "jesus" else key)) for s in rows
    )


DEFAULT_SCENES: Tuple[Scene, ...] = _tagged(_REAL_SCENES, "real") + _tagged(
    _BRIGHT_SCENES, "bright"
)


# ── Правки из студии ─────────────────────────────────────────────────────────
# Константы выше — значения по умолчанию. Поверх них ложится то, что
# поправили в интерфейсе: ДНК героев и список сцен лежат в файле рядом с
# данными, и ближайший прогон читает уже их. Перезапуск не нужен.


def _text(name: str, default: str) -> str:
    try:
        from youtube_prayer.cast_store import text

        return text(name) or default
    except Exception:
        return default


def style() -> str:
    return _text("style", STYLE)


def heroine(key: str = "") -> Heroine:
    """Героиня по ключу. Неизвестный ключ — первая: лучше знакомое лицо."""
    want = (key or DEFAULT_HEROINE).strip().lower()
    for h in HEROINES:
        if h.key == want:
            return h
    return HEROINES[0]


def woman(key: str = "") -> str:
    h = heroine(key)
    return _text(h.store_key, h.default_prompt)


def jesus() -> str:
    return _text("jesus", JESUS)


def bank_folder(who: str, heroine_key: str = "") -> str:
    """Папка банка под эту роль и эту героиню.

    У первой героини суффикса нет: её кадры лежат в woman/ и both/ с лета,
    и переименование оторвало бы их от сцен. Кадры с Иисусом общие — он
    один на обеих.
    """
    h = heroine(heroine_key)
    if who == "jesus" or h.key == DEFAULT_HEROINE:
        return who
    return f"{who}_{h.key}"


def scenes() -> Tuple[Scene, ...]:
    """Набор кадров: из файла, если его правили, иначе из кода."""
    try:
        from youtube_prayer.cast_store import scenes_raw

        rows = scenes_raw()
    except Exception:
        rows = None
    if not rows:
        return DEFAULT_SCENES
    return tuple(
        Scene(
            key=r["key"],
            who=r["who"],
            shot=r["shot"],
            prompt=r["prompt"],
            topics=tuple(r.get("topics") or ()),
            heroine=str(r.get("heroine") or ""),
        )
        for r in rows
    )


def scenes_for(heroine_key: str = "") -> Tuple[Scene, ...]:
    """Сцены этой героини плюс общие."""
    want = heroine(heroine_key).key
    return tuple(s for s in scenes() if s.heroine in ("", want))


def _by_who(heroine_key: str = "") -> Dict[str, List[Scene]]:
    out: Dict[str, List[Scene]] = {}
    for s in scenes_for(heroine_key):
        out.setdefault(s.who, []).append(s)
    return out


def character_prompt(who: str, heroine_key: str = "") -> str:
    if who == "woman":
        return woman(heroine_key)
    if who == "jesus":
        return jesus()
    # Два персонажа описываем раздельно и проговариваем, чьи признаки чьи.
    # Иначе они перетекают: на первом же парном кадре обручальное кольцо
    # героини оказалось на руке Иисуса.
    return (
        f"TWO PEOPLE. First person: {woman(heroine_key)}. "
        f"Second person: {jesus()}. "
        "Only the woman wears a ring; his hands are bare. "
        "Their clothing never mixes: her knitted cardigan is hers alone, "
        "his linen robe and red mantle are his alone"
    )


def full_prompt(scene: Scene, heroine_key: str = "") -> str:
    """Сначала кадр, потом герои, потом действие.

    Порядок важен: если начинать с описания лиц, модель уходит в портрет и
    игнорирует ракурс. На кадре «идут по дороге со спины» так и вышло —
    получился фронтальный двойной портрет.
    """
    # Героиню берём из самой сцены, если не сказано иное: у сцен есть
    # хозяйка, и подставить чужое описание — значит нарисовать не ту.
    who = heroine_key or scene.heroine
    return (
        f"{scene.shot}. {character_prompt(scene.who, who)}. "
        f"{scene.prompt}. {style()}"
    )


def scenes_for_topic(
    topic: str, *, count: int = 8, heroine_key: str = ""
) -> List[Scene]:
    """Кадры под тему: сначала беда, потом присутствие, потом утешение.

    Порядок не случайный — он и есть маленькая история, ради которой зритель
    досматривает: узнал себя, увидел, что не один, ушёл с теплом.
    """
    t = (topic or "").lower()
    by_who = _by_who(heroine_key)

    def matches(s: Scene) -> bool:
        return not s.topics or any(k in t for k in s.topics)

    trouble = [s for s in by_who.get("woman", []) if matches(s)]
    if not trouble:
        trouble = list(by_who.get("woman", []))
    presence = list(by_who.get("jesus", []))
    comfort = list(by_who.get("both", []))

    out: List[Scene] = []
    # Примерно треть беды, треть присутствия, остальное — утешение.
    n_trouble = max(1, count // 3)
    n_presence = max(1, count // 4)
    out += trouble[:n_trouble]
    out += presence[:n_presence]
    out += comfort[: max(1, count - len(out))]
    return out[:count]


def all_scene_keys() -> List[str]:
    return [s.key for s in scenes()]


def scene_by_key(key: str) -> Scene | None:
    for s in scenes():
        if s.key == key:
            return s
    return None
