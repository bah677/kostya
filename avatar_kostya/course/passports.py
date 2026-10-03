"""Живой сбор паспортов эксперта / продукта / запуска: слоты + ход LLM."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from course.products import EXPERT_PRODUCT_ID, active_product_id, product_display_name

PASSPORT_KINDS = ("expert", "product", "launch")

PASSPORT_SPEC: Dict[str, Dict[str, Any]] = {
    "expert": {
        "title": "Паспорт эксперта",
        "store_product": EXPERT_PRODUCT_ID,
        "setting_key": "passport_expert",
        "slots": {
            "who": "кто эксперт, роль, подход, ценности — своими словами",
            "audience": "с кем говорит, какой у этих людей язык",
            "tone": "как звучит в сторис: темп, тепло, жёсткость, юмор",
            "taboo": "чего не обещает, какие слова и позы запретны",
            "direct": "как зовёт в директ / на продукт, без канцелярита",
        },
        "rag_query": "об эксперте тон голос как говорит ценности табу",
    },
    "product": {
        "title": "Паспорт продукта",
        "store_product": None,
        "setting_key": "passport_product",
        "slots": {
            "for_whom": "для кого продукт и кому не подходит",
            "result": "какой результат обещает — только то, что эксперт подтвердил",
            "format": "формат: длительность, живое/записи, практики",
            "price": "цены, рассрочка, что входит — если эксперт подтвердил",
            "cta": "куда вести из сторис: слово в Direct, ссылка, квиз",
        },
        "rag_query": "о продукте программа аудитория результат цена CTA",
    },
    "launch": {
        "title": "Паспорт запуска",
        "store_product": None,
        "setting_key": "passport_launch",
        "slots": {
            "dates": "даты: прогрев, окно продаж, эфиры, когда закрываем",
            "offer": "оффер этого потока одним абзацем",
            "cta": "слово в директ / ссылка / куда писать прямо сейчас",
            "objections": "главные возражения и как на них отвечает",
            "proof": "соцдоказ: отзывы, разборы, «как было в прошлом потоке»",
            "forbidden": "что нельзя обещать в этом запуске",
        },
        "rag_query": "запуск набор поток продажи оффер даты CTA",
    },
}

# Короткие подписи слотов для UI студии.
SLOT_LABELS: Dict[str, str] = {
    "who": "Кто эксперт",
    "audience": "Аудитория",
    "tone": "Тон",
    "taboo": "Табу",
    "direct": "Директ / CTA",
    "for_whom": "Для кого",
    "result": "Результат",
    "format": "Формат",
    "price": "Цена",
    "cta": "CTA",
    "dates": "Даты",
    "offer": "Оффер",
    "objections": "Возражения",
    "proof": "Соцдоказ",
    "forbidden": "Нельзя обещать",
}


def spec_for(kind: str) -> Dict[str, Any]:
    k = (kind or "").strip()
    if k not in PASSPORT_SPEC:
        raise ValueError(f"unknown passport kind: {kind}")
    return PASSPORT_SPEC[k]


def store_product_id(kind: str) -> str:
    spec = spec_for(kind)
    return spec["store_product"] or active_product_id()


def empty_slots(kind: str) -> Dict[str, str]:
    return {key: "" for key in spec_for(kind)["slots"]}


def merge_slots(kind: str, current: Dict[str, str], patch: Dict[str, Any]) -> Dict[str, str]:
    out = empty_slots(kind)
    out.update({k: str(v or "").strip() for k, v in (current or {}).items() if k in out})
    for k, v in (patch or {}).items():
        if k not in out:
            continue
        text = str(v or "").strip()
        if text:
            out[k] = text
    return out


def filled_count(slots: Dict[str, str]) -> tuple[int, int]:
    keys = list(slots.keys())
    n = sum(1 for k in keys if (slots.get(k) or "").strip())
    return n, len(keys)


def missing_slots(kind: str, slots: Dict[str, str]) -> List[str]:
    spec = spec_for(kind)
    out = []
    for key, hint in spec["slots"].items():
        if not (slots.get(key) or "").strip():
            out.append(f"{key}: {hint}")
    return out


def slots_to_markdown(kind: str, slots: Dict[str, str]) -> str:
    spec = spec_for(kind)
    lines = [f"# {spec['title']} · {product_display_name(store_product_id(kind))}", ""]
    for key, hint in spec["slots"].items():
        val = (slots.get(key) or "").strip() or "—"
        lines.append(f"## {key}")
        lines.append(hint)
        lines.append("")
        lines.append(val)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def decode_setting(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, memoryview)):
        raw = bytes(raw).decode("utf-8")
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def load_passport(stor, kind: str) -> Dict[str, Any]:
    spec = spec_for(kind)
    pid = store_product_id(kind)
    raw = await stor.get_content_setting(pid, spec["setting_key"])
    data = decode_setting(raw)
    slots = merge_slots(kind, data.get("slots") or {}, {})
    filled, total = filled_count(slots)
    return {
        "kind": kind,
        "title": spec["title"],
        "slots": slots,
        "slot_meta": [
            {
                "key": key,
                "label": SLOT_LABELS.get(key, key),
                "hint": hint,
            }
            for key, hint in spec["slots"].items()
        ],
        "text": (data.get("text") or "").strip(),
        "done": bool(data.get("done")),
        "filled": filled,
        "total": total,
        "updated_at": str(data.get("updated_at") or ""),
    }


async def list_passports(stor) -> List[Dict[str, Any]]:
    return [await load_passport(stor, kind) for kind in PASSPORT_KINDS]


async def save_passport(
    stor,
    kind: str,
    slots: Dict[str, str],
    *,
    done: bool,
    user_id: int = 0,
) -> Dict[str, Any]:
    spec = spec_for(kind)
    pid = store_product_id(kind)
    text = slots_to_markdown(kind, slots)
    payload = {
        "slots": slots,
        "text": text,
        "done": bool(done),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": int(user_id or 0),
    }
    await stor.set_content_setting(pid, spec["setting_key"], payload)
    if kind == "expert" and any(slots.values()):
        await stor.insert_style_profile(
            product_id=active_product_id(),
            text=text,
            origin="manual",
            created_by=user_id or None,
            activate=True,
        )
    return payload


def build_turn_system(
    kind: str,
    slots: Dict[str, str],
    rag_digest: str,
    *,
    project_context: str = "",
    turn: int = 0,
    max_turns: int = 12,
    opening: bool = False,
) -> str:
    spec = spec_for(kind)
    missing = missing_slots(kind, slots)
    filled = {k: v for k, v in slots.items() if (v or "").strip()}
    filled_n, total_n = filled_count(slots)
    phase = "СТАРТ: разбор того, что уже есть" if opening else f"ход {turn} из {max_turns}"
    return f"""Ты — въедливый продюсер-редактор. В Telegram ведёшь мастер заполнения «{spec['title']}».

Задача выполняется один раз и должна дать качественный паспорт для генерации контента клуба.
Это НЕ анкета и НЕ список вопросов подряд. Ты анализируешь, переспрашиваешь, ловишь дыры и размытость.

Контекст проекта / соседние паспорта:
{project_context or '(нет)'}

Уже в слотах ({filled_n}/{total_n}):
{json.dumps(filled, ensure_ascii=False, indent=2) or '{}'}

Ещё пусто / слабо:
{json.dumps(missing, ensure_ascii=False) if missing else 'критичных пустот нет'}

Фрагменты из базы RAG (могут врать — сверяй с ответом человека):
{rag_digest or '(пусто)'}

Фаза: {phase}.

Правила качества:
- Сначала коротко покажи, что понял из ответа/текущего паспорта (1–2 фразы), потом максимум ОДИН вопрос.
- Вопрос только про самое важное для генерации контента из ещё пустого или размытого.
- Если ответ общий («ну, про любовь») — переспроси конкретику: пример, формулировку, табу, цифру, куда вести.
- Если человек уже закрыл слот ясно — запиши своими словами в slots и не долби то же.
- Не выдумывай цены, даты, обещания, офферы. Нет в речи и надёжном RAG — слот пустой.
- Не превращай диалог в допрос: тон тёплый, деловой, по-клубному.
- Когда материала хватает для уверенной генерации (не обязательно 100% слотов) — done=true и коротко скажи, что сохраняешь.
- Если ходов почти не осталось (turn близко к max_turns) — добивай самое критичное и ставь done=true.

Верни строго JSON:
{{"reply":"текст человеку","slots":{{"ключ":"новое значение только если появилось"}},"done":false}}
Ключи slots — только: {", ".join(spec["slots"])}.
"""
