#!/usr/bin/env python3
"""
Выгрузка пакета: как устроен онбординг новых пользователей клуба + данные для LLM.

Пишет exports/onboarding_llm_pack_YYYYMMDD_HHMM/:
  README.md
  dictionary.md
  product_flow.md          — подробное описание процесса (as-is)
  summary.json             — воронки и разрезы
  member_profiles.jsonl    — 1 строка = 1 профиль участника
  member_events.jsonl      — события профиля (subscription/goals/proactive…)
  followup_status.json     — снимок followup_states (лиды до оплаты)
  texts/
    ru_onboarding_default.py excerpt via txt
    twin_ru_onboarding.txt
    followup_map.md        — копия docs/FOLLOWUP_MAP.md
    member_proactive_hints.txt

Пример:
  cd /home/appuser/dev/kostya/club
  .venv/bin/python scripts/export_onboarding_llm_pack.py --env /home/appuser/club/.env
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config, config as default_config
from storage.user_storage import UserStorage

ROOT = Path(__file__).resolve().parents[1]


def _load_config(env_file: Optional[str]) -> Config:
    if not env_file:
        return default_config
    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)
    from config import load_config

    return load_config()


def _json_dump(path: Path, obj: Any) -> None:
    path.write_text(
        json.dumps(obj, ensure_ascii=False, default=str, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


async def _fetch_all(pool, query: str, *args: Any) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]


async def _fetch_scalar(pool, query: str, *args: Any) -> Any:
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)


PRODUCT_FLOW_MD = r'''# Как сейчас организован онбординг новых пользователей клуба (as-is)

Документ для анализа в LLM. Описывает **фактическое** поведение кода и данных на проде клуба
(` /home/appuser/dev/kostya/club ` → prod), не «идеальный» продукт.

Есть **две оси**, которые часто путают:

1. **Дожим лида (followup)** — до оплаты, таблица `followup_states`.
2. **Онбординг участника (member)** — после первой выдачи участия, таблица `member_profiles.onboarding_stage`.

Плюс отдельная ветка бота **Настя** (`BOT_VARIANT=nastya`) — упрощённый подарочный сценарий.

---

## 1. Главный путь участника клуба (стандартный бот)

```
/start
  → кружки/тексты онбординга (OnboardingFeature)
  → диалог с sales-агентом в личке (если нет лицензии)
  → параллельно: followup-дожим (холодный / корзина / stuck)
  → оплата
  → license + member_profiles.stage=started + инвайт в группу
  → вступление в группу (welcome в чате)
  → первое DM при активной лицензии → stage=active
  → дальше: member-агент + proactive (в т.ч. goal=onboarding) + digest/scripture
```

### 1.1. `/start`

- Хендлер: `CommandHandlers` → `OnboardingFeature.start_onboarding` / `cmd_start`.
- Сохраняет пользователя, пишет attribution / ref / promo по deep link.
- Шлёт video notes и тексты из `bot/texts/ru_onboarding.py`
  (у twin nastya — overlay `twin_texts/nastya/ru_onboarding.py`).
- В дефолтном клубе флаги welcome после кружка часто **выключены** —
  сразу идёт ожидание диалога/оплаты.

### 1.2. Followup (до оплаты)

Фича: `bot/features/followup.py`. Карта текстов: `docs/FOLLOWUP_MAP.md` (в пакете `texts/followup_map.md`).

Типичные статусы:

| status | Смысл |
|--------|--------|
| 101→102→103 | холодный заход (мало писал) |
| 201→202→203 | корзина (заказ без оплаты) |
| 120→121→122 | застрял в диалоге |
| 110→112 | старый engaged-пинг |
| **901** | оплатил / есть лицензия — дожим стоп |
| 997/998/999 | sensitive / отказ / блок |

Триггеры: `/start`, создание заказа, оплата, loop ~60с, вечерний cron ~21:04 МСК.

### 1.3. Оплата → «участник начался»

`OrderFulfillment._deliver_subscription_order`:

1. `create_or_extend_license`
2. `on_member_subscription_started(..., is_first_join=True)` →
   `member_profiles.onboarding_stage = **started**` (только если раньше не было активной лицензии)
3. уведомление пользователю + post-payment legal consent
4. **одноразовый инвайт** в группу (`ClubGroupFeature.send_group_invite`)
5. followup → **901**

Продление при уже активной лицензии: stage **не** сбрасывается, пишется event `subscription_renewed`.

### 1.4. Вступление в группу

`ClubGroupFeature` на `chat_member`: кэш + welcome в топик.
**Не** переводит `onboarding_stage` в `active`.

### 1.5. Stage `active` = «уже общается в личке»

Первое входящее DM при активной лицензии → member-агент →
`prepare_member_dm_turn` → `touch_member_dm`: `started` → **`active`**.

Пока `started`, в system prompt member-агента подсказка:
«только вступил — проведи мягкий онбординг»
(`build_member_profile_prompt_addon`).

Proactive planner может ставить `goal=onboarding`, если stage=`started` и тишина 1–2 дня.

---

## 2. State machine `member_profiles.onboarding_stage`

| Stage | Значение | Когда ставится |
|-------|----------|----------------|
| `not_started` | default при INSERT профиля | `ensure_member_profile` |
| `started` | только вступил (первая активация) | `on_member_subscription_started` при `is_first_join` |
| `active` | уже писал в личку как участник | `touch_member_dm` |

Поля профиля рядом: `joined_at`, `license_expires_at`, `stated_goals`, `topics_json`,
`materials_sent_json`, `last_dm_at`, `last_group_activity_at`, `renewal_state`,
`proactive_cooldown_until`.

События: таблица `member_profile_events`
(`subscription_started`, `subscription_renewed`, `stated_goals_updated`,
`proactive_sent`, `club_digest_dm_sent`, `club_scripture_dm_sent`, …).

**Трекинг профилей запущен ~2026-06-26.** Пользователи, ушедшие раньше, часто **без** строки профиля —
это не «не прошли онбординг», а отсутствие трекинга.

---

## 3. Ветка Настя (временный онбординг)

`NastyaTempOnboardingFeature` (`BOT_VARIANT=nastya`):

- кружок + hardcoded текст со ссылкой
- подарок лицензии на N дней
- `users.onboarding_complete = TRUE`
- **нет** followup `on_start`, **нет** member_profiles sync, **нет** AI-диалога

Twin-тексты `twin_texts/nastya/ru_onboarding.py` этим сценарием **не** используются
(см. docstring фичи).

---

## 4. Gaps / ловушки для аналитика

1. **`users.onboarding_complete` и `users.questions_asked`** — почти мёртвые для клуба;
   не использовать как метрику онбординга.
2. **Gift deep link / admin gift** — лицензия может выдаться **без**
   `on_member_subscription_started` (нет stage=`started`) или без инвайта — дыры.
3. Join группы ≠ `active`. Можно быть в чате и оставаться `started` / даже `not_started`.
4. Followup `901` ≠ member `active`. Человек мог оплатить и молчать в личке.
5. Outreach digest/scripture — не двигают stage; это удержание, не онбординг.
6. Topic-кнопки онбординга в коде есть, в текущем `/start` UI почти не показываются.

---

## 5. Ключевые файлы кода

- `bot/features/onboarding.py`
- `bot/features/nastya_temp_onboarding.py`
- `bot/features/followup.py`
- `bot/payments/fulfillment.py`
- `bot/features/club_group.py`
- `bot/services/member_profile_service.py`
- `storage/db/member_profiles.py`
- `bot/features/member_proactive.py`
- `docs/FOLLOWUP_MAP.md`
- `bot/texts/ru_onboarding.py`, `twin_texts/nastya/ru_onboarding.py`
'''


def write_readme(path: Path, *, generated_at: str, n_profiles: int) -> None:
    path.write_text(
        f"""# Пакет: онбординг клуба (для LLM)

Сгенерировано: `{generated_at}` (UTC)  
Профилей в `member_profiles.jsonl`: **{n_profiles}**

## Что внутри

| Файл | Содержание |
|------|------------|
| `product_flow.md` | **Как устроен онбординг** (процесс as-is) — читать первым |
| `dictionary.md` | Словарь полей и статусов |
| `summary.json` | Воронки stage / время до active / followup / события |
| `member_profiles.jsonl` | 1 JSON = 1 участник с профилем + engagement |
| `member_events.jsonl` | События профиля |
| `followup_status.json` | Распределение лидов до оплаты |
| `texts/` | Карта followup + фрагменты текстов |

## Как кормить LLM

1. `product_flow.md` + `dictionary.md`
2. `summary.json`
3. При углублении — `member_profiles.jsonl` и `member_events.jsonl`
4. `texts/followup_map.md` — для ветки до оплаты

Промпт-рамка:

> Опиши воронку онбординга участника после оплаты.  
> Где люди застревают (started без active, not_started при живой лицензии)?  
> Чем followup-дожим отличается от member-onboarding?  
> Какие дыры в продукте/данных? Не выдумывай числа.

## Приватность

Нет username / имени / телефона. Есть `user_id`. `stated_goals` может содержать личный текст —
при загрузке во внешние LLM при необходимости удалите это поле.
""",
        encoding="utf-8",
    )


def write_dictionary(path: Path) -> None:
    path.write_text(
        """# Словарь

## Две системы

| Система | Таблица | Вопрос |
|---------|--------|--------|
| Followup (лиды) | `followup_states` | Довёл ли бот до оплаты? |
| Member onboarding | `member_profiles.onboarding_stage` | Освоился ли участник после вступления? |

**Не путать** с `users.onboarding_complete` (legacy / nastya) — для анализа клуба не использовать.

## onboarding_stage

| Значение | Смысл |
|----------|--------|
| `not_started` | Профиль есть / default, первая активация через штатный payment path ещё не зафиксирована как started |
| `started` | Первая активация участия; мягкий онбординг в DM ещё не «закрыт» первым сообщением |
| `active` | Уже писал в личку боту как участник с лицензией |

## Полезные производные в profiles.jsonl

| Поле | Смысл |
|------|--------|
| `has_active_license_now` | Сейчас есть active non-bonus license |
| `days_in_started_without_dm` | Если stage=started и last_dm пуст — сколько дней «висит» |
| `hours_start_to_active` | От `subscription_started` до перехода в active (если есть) |
| `club_group_msgs` / `private_user_msgs` | Активность в группе / личке |
| `payment_profile` | ever_base / promo_week_only / … |

## followup status (кратко)

101–103 холодный · 201–203 корзина · 120–122 stuck · 901 оплатил · 997/998/999 стоп-ветки.
Полная карта — `texts/followup_map.md`.
""",
        encoding="utf-8",
    )


async def build_profiles(pool, club_group_id: int) -> List[Dict[str, Any]]:
    q = """
    WITH pay AS (
        SELECT
            o.user_id,
            COUNT(*)::int AS paid_orders_n,
            COALESCE(SUM(p.amount_rub), 0)::float8 AS paid_rub_sum,
            MIN(o.paid_at) AS first_paid_at,
            MAX(o.paid_at) AS last_paid_at,
            BOOL_OR(COALESCE(t.type, '') = 'base') AS had_base,
            BOOL_OR(COALESCE(t.type, '') LIKE 'promo_test1week%') AS had_promo_week
        FROM orders o
        JOIN payments p ON p.order_id = o.id AND p.status = 'succeeded'
        LEFT JOIN tariffs t ON t.id = o.tariff_id
        WHERE o.status = 'paid'
        GROUP BY o.user_id
    ),
    grp AS (
        SELECT m.user_id, COUNT(*)::int AS club_group_msgs
        FROM messages m
        WHERE m.chat_id = $1
          AND m.sender_type = 'user'
          AND m.deleted_at IS NULL
        GROUP BY m.user_id
    ),
    prv AS (
        SELECT m.user_id, COUNT(*)::int AS private_user_msgs
        FROM messages m
        WHERE m.sender_type = 'user'
          AND m.chat_type = 'private'
          AND m.deleted_at IS NULL
        GROUP BY m.user_id
    ),
    started_ev AS (
        SELECT DISTINCT ON (user_id)
            user_id, created_at AS subscription_started_at
        FROM member_profile_events
        WHERE event_type = 'subscription_started'
        ORDER BY user_id, created_at ASC
    ),
    active_lic AS (
        SELECT DISTINCT user_id
        FROM license
        WHERE status = 'active'
          AND license_type <> 'bonus'
          AND expires_at > NOW()
    )
    SELECT
        mp.user_id,
        mp.onboarding_stage,
        mp.joined_at,
        mp.license_expires_at,
        mp.stated_goals,
        mp.topics_json,
        mp.materials_sent_json,
        mp.last_dm_at,
        mp.last_group_activity_at,
        mp.proactive_ignored_streak,
        mp.renewal_state,
        mp.created_at AS profile_created_at,
        mp.updated_at AS profile_updated_at,
        (al.user_id IS NOT NULL) AS has_active_license_now,
        se.subscription_started_at,
        CASE
            WHEN mp.onboarding_stage = 'started'
                 AND mp.last_dm_at IS NULL
                 AND se.subscription_started_at IS NOT NULL
            THEN EXTRACT(EPOCH FROM (NOW() - se.subscription_started_at)) / 86400.0
            WHEN mp.onboarding_stage = 'started'
                 AND mp.last_dm_at IS NULL
            THEN EXTRACT(EPOCH FROM (NOW() - mp.created_at)) / 86400.0
            ELSE NULL
        END AS days_started_without_dm,
        CASE
            WHEN mp.onboarding_stage = 'active'
                 AND se.subscription_started_at IS NOT NULL
                 AND mp.last_dm_at IS NOT NULL
            THEN EXTRACT(EPOCH FROM (mp.last_dm_at - se.subscription_started_at)) / 3600.0
            ELSE NULL
        END AS hours_start_to_first_dm_proxy,
        COALESCE(g.club_group_msgs, 0)::int AS club_group_msgs,
        COALESCE(pr.private_user_msgs, 0)::int AS private_user_msgs,
        COALESCE(p.paid_orders_n, 0)::int AS paid_orders_n,
        COALESCE(p.paid_rub_sum, 0)::float8 AS paid_rub_sum,
        p.first_paid_at,
        p.last_paid_at,
        COALESCE(p.had_base, FALSE) AS had_base,
        COALESCE(p.had_promo_week, FALSE) AS had_promo_week,
        CASE
            WHEN COALESCE(p.had_base, FALSE) THEN 'ever_base'
            WHEN COALESCE(p.had_promo_week, FALSE) THEN 'promo_week_only'
            WHEN COALESCE(p.paid_orders_n, 0) > 0 THEN 'paid_other'
            ELSE 'no_successful_payments'
        END AS payment_profile,
        fs.status::text AS followup_status,
        u.created_at AS user_registered_at,
        u.onboarding_complete AS users_onboarding_complete_flag_dead
    FROM member_profiles mp
    LEFT JOIN active_lic al ON al.user_id = mp.user_id
    LEFT JOIN started_ev se ON se.user_id = mp.user_id
    LEFT JOIN grp g ON g.user_id = mp.user_id
    LEFT JOIN prv pr ON pr.user_id = mp.user_id
    LEFT JOIN pay p ON p.user_id = mp.user_id
    LEFT JOIN followup_states fs ON fs.user_id = mp.user_id
    LEFT JOIN users u ON u.user_id = mp.user_id
    ORDER BY mp.onboarding_stage, mp.user_id
    """
    rows = await _fetch_all(pool, q, int(club_group_id or 0))
    out: List[Dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        for k in (
            "days_started_without_dm",
            "hours_start_to_first_dm_proxy",
            "paid_rub_sum",
        ):
            if row.get(k) is not None:
                try:
                    row[k] = round(float(row[k]), 2)
                except (TypeError, ValueError):
                    pass
        # json fields
        for jk in ("topics_json", "materials_sent_json"):
            v = row.get(jk)
            if isinstance(v, str):
                try:
                    row[jk] = json.loads(v)
                except json.JSONDecodeError:
                    pass
        out.append(row)
    return out


async def build_events(pool) -> List[Dict[str, Any]]:
    q = """
    SELECT
        e.id,
        e.user_id,
        e.event_type,
        e.meta,
        e.created_at,
        mp.onboarding_stage AS stage_now
    FROM member_profile_events e
    LEFT JOIN member_profiles mp ON mp.user_id = e.user_id
    WHERE e.event_type IN (
        'subscription_started',
        'subscription_renewed',
        'stated_goals_updated',
        'proactive_sent'
    )
    ORDER BY e.created_at
    """
    rows = await _fetch_all(pool, q)
    out = []
    for r in rows:
        row = dict(r)
        meta = row.get("meta")
        if isinstance(meta, str):
            try:
                row["meta"] = json.loads(meta)
            except json.JSONDecodeError:
                pass
        out.append(row)
    return out


async def build_summary(pool, profiles: List[Dict[str, Any]]) -> Dict[str, Any]:
    def cnt(stage: str) -> int:
        return sum(1 for p in profiles if p.get("onboarding_stage") == stage)

    active_lic = [p for p in profiles if p.get("has_active_license_now")]
    started_stuck = [
        p
        for p in profiles
        if p.get("onboarding_stage") == "started"
        and p.get("has_active_license_now")
        and not p.get("last_dm_at")
    ]
    not_started_but_licensed = [
        p
        for p in profiles
        if p.get("onboarding_stage") == "not_started" and p.get("has_active_license_now")
    ]

    hours = [
        float(p["hours_start_to_first_dm_proxy"])
        for p in profiles
        if p.get("hours_start_to_first_dm_proxy") is not None
        and float(p["hours_start_to_first_dm_proxy"]) >= 0
    ]
    hours_sorted = sorted(hours)

    def pctile(vals: List[float], q: float) -> Optional[float]:
        if not vals:
            return None
        i = int(round((len(vals) - 1) * q))
        return round(vals[i], 2)

    followup = await _fetch_all(
        pool,
        """
        SELECT status::text AS status, COUNT(*)::int AS n
        FROM followup_states
        GROUP BY 1
        ORDER BY n DESC
        """,
    )
    events_agg = await _fetch_all(
        pool,
        """
        SELECT event_type, COUNT(*)::int AS n,
               MIN(created_at)::date AS first_day,
               MAX(created_at)::date AS last_day
        FROM member_profile_events
        GROUP BY 1
        ORDER BY n DESC
        """,
    )
    funnel_paid_to_stages = await _fetch_all(
        pool,
        """
        WITH started AS (
            SELECT DISTINCT ON (user_id) user_id, created_at AS t0
            FROM member_profile_events
            WHERE event_type = 'subscription_started'
            ORDER BY user_id, created_at ASC
        )
        SELECT
            mp.onboarding_stage,
            COUNT(*)::int AS n,
            COUNT(*) FILTER (WHERE mp.last_dm_at IS NOT NULL)::int AS with_dm,
            COUNT(*) FILTER (WHERE al.user_id IS NOT NULL)::int AS still_licensed
        FROM started s
        JOIN member_profiles mp ON mp.user_id = s.user_id
        LEFT JOIN (
            SELECT DISTINCT user_id FROM license
            WHERE status='active' AND license_type<>'bonus' AND expires_at>NOW()
        ) al ON al.user_id = s.user_id
        GROUP BY 1
        ORDER BY 1
        """,
    )

    profiles_launch = await _fetch_scalar(
        pool, "SELECT MIN(created_at) FROM member_profiles"
    )

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "member_profiles_tracking_since": profiles_launch,
        "stage_counts_all_profiles": {
            "not_started": cnt("not_started"),
            "started": cnt("started"),
            "active": cnt("active"),
            "total": len(profiles),
        },
        "among_currently_licensed": {
            "n": len(active_lic),
            "by_stage": {
                s: sum(1 for p in active_lic if p.get("onboarding_stage") == s)
                for s in ("not_started", "started", "active")
            },
            "started_without_any_dm": len(started_stuck),
            "not_started_but_has_license": len(not_started_but_licensed),
        },
        "time_subscription_started_to_first_dm_hours": {
            "n": len(hours),
            "avg": round(sum(hours) / len(hours), 2) if hours else None,
            "p50": pctile(hours_sorted, 0.5),
            "p90": pctile(hours_sorted, 0.9),
            "note": "proxy: last_dm_at - first subscription_started event; only stage=active",
        },
        "funnel_after_subscription_started_event": funnel_paid_to_stages,
        "member_profile_events_agg": events_agg,
        "followup_status_distribution": followup,
        "caveats": [
            "users.onboarding_complete / questions_asked — dead fields for club analysis",
            "member_profiles exist only since ~2026-06-26",
            "gift/admin paths may skip stage=started",
            "join group does not set stage=active",
        ],
    }


def copy_texts(out_texts: Path) -> None:
    out_texts.mkdir(parents=True, exist_ok=True)
    src_map = ROOT / "docs" / "FOLLOWUP_MAP.md"
    if src_map.is_file():
        shutil.copy2(src_map, out_texts / "followup_map.md")

    # snippets from onboarding texts
    for src, dst in (
        (ROOT / "bot" / "texts" / "ru_onboarding.py", "ru_onboarding_default.py"),
        (ROOT / "twin_texts" / "nastya" / "ru_onboarding.py", "twin_nastya_ru_onboarding.py"),
        (
            ROOT / "twin_texts" / "nastya" / "prompts" / "member_proactive.py",
            "member_proactive_prompts.py",
        ),
    ):
        if src.is_file():
            shutil.copy2(src, out_texts / dst)

    (out_texts / "README_texts.md").write_text(
        "Фрагменты продуктовых текстов онбординга/followup/proactive для контекста LLM.\n"
        "followup_map.md — каноническая карта дожима до оплаты.\n",
        encoding="utf-8",
    )


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="/home/appuser/club/.env")
    ap.add_argument("--out-dir", default="")
    args = ap.parse_args()

    cfg = _load_config(args.env if Path(args.env).is_file() else None)
    storage = UserStorage(cfg.database_url)
    await storage.initialize()
    pool = storage.pool
    assert pool is not None

    club_group_id = int(getattr(cfg, "CLUB_GROUP_ID", 0) or 0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    out = Path(args.out_dir) if args.out_dir else (
        ROOT / "exports" / f"onboarding_llm_pack_{stamp}"
    )
    out.mkdir(parents=True, exist_ok=True)
    print(f"→ {out}")

    profiles = await build_profiles(pool, club_group_id)
    print(f"  profiles={len(profiles)}")
    events = await build_events(pool)
    print(f"  events={len(events)}")
    summary = await build_summary(pool, profiles)

    write_readme(
        out / "README.md",
        generated_at=summary["generated_at_utc"],
        n_profiles=len(profiles),
    )
    write_dictionary(out / "dictionary.md")
    (out / "product_flow.md").write_text(PRODUCT_FLOW_MD, encoding="utf-8")
    _json_dump(out / "summary.json", summary)
    _write_jsonl(out / "member_profiles.jsonl", profiles)
    _write_jsonl(out / "member_events.jsonl", events)
    _json_dump(
        out / "followup_status.json",
        {
            "note": "Снимок followup_states — дожим до оплаты, не member stage",
            "distribution": summary["followup_status_distribution"],
        },
    )
    copy_texts(out / "texts")

    manifest = {
        "path": str(out),
        "files": {p.name: p.stat().st_size for p in sorted(out.iterdir()) if p.is_file()},
        "counts": summary["stage_counts_all_profiles"],
        "licensed_now": summary["among_currently_licensed"],
    }
    # texts dir size note
    texts_dir = out / "texts"
    if texts_dir.is_dir():
        manifest["texts_files"] = {
            p.name: p.stat().st_size for p in sorted(texts_dir.iterdir()) if p.is_file()
        }
    _json_dump(out / "manifest.json", manifest)

    await storage.close()
    print("OK")
    print(json.dumps(manifest, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
