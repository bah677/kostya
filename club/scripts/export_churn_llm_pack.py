#!/usr/bin/env python3
"""
Выгрузка пакета данных по оттоку клуба для анализа в LLM (ChatGPT / Claude / DeepSeek…).

Пишет каталог exports/churn_llm_pack_YYYYMMDD_HHMM/ с:
  README.md              — как читать пакет и определения когорт
  dictionary.md          — словарь полей
  summary.json           — агрегаты + разрез опросов
  cohorts.jsonl          — 1 строка = 1 пользователь (active | expired)
  survey_responses.jsonl — ответы на churn-опросы / тикеты
  about_club.txt         — контекст продукта

По умолчанию без ПИИ (username / имя / телефон). user_id оставляем как ключ корреляции.

Пример:
  cd /home/appuser/dev/kostya/club
  .venv/bin/python scripts/export_churn_llm_pack.py --env /home/appuser/club/.env
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.services.club_churn_report import ClubChurnReportCollector, load_aboutclub_text
from config import Config, config as default_config
from storage.user_storage import UserStorage

MSK = ZoneInfo("Europe/Moscow")

_CHURN18_RE = re.compile(
    r"выбрана причина ухода\s*[—\-:]\s*(.+?)\.?$",
    re.IGNORECASE | re.DOTALL,
)


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


def _parse_churn18_reason(msg: str) -> Optional[str]:
    m = _CHURN18_RE.search((msg or "").strip())
    if not m:
        return None
    return m.group(1).strip()


async def _fetch_all(pool, query: str, *args: Any) -> List[Dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]


async def _fetch_scalar(pool, query: str, *args: Any) -> Any:
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)


async def build_cohort_rows(pool, club_group_id: int) -> List[Dict[str, Any]]:
    """Пользовательские строки: active (как в отчёте) + все expired."""
    q = """
    WITH active_lic AS (
        SELECT DISTINCT ON (l.user_id)
            l.user_id,
            'active'::text AS cohort,
            l.license_type::text AS license_type,
            l.status::text AS license_status,
            l.created_at AS license_created_at,
            l.expires_at AS license_expires_at,
            l.updated_at AS license_updated_at
        FROM license l
        WHERE l.status = 'active'
          AND l.license_type <> 'bonus'
          AND l.expires_at > NOW()
        ORDER BY l.user_id, l.expires_at DESC, l.id DESC
    ),
    expired_lic AS (
        SELECT DISTINCT ON (l.user_id)
            l.user_id,
            'expired'::text AS cohort,
            l.license_type::text AS license_type,
            l.status::text AS license_status,
            l.created_at AS license_created_at,
            l.expires_at AS license_expires_at,
            l.updated_at AS license_updated_at
        FROM license l
        WHERE l.status = 'expired'
          AND NOT EXISTS (
              SELECT 1 FROM license a
              WHERE a.user_id = l.user_id
                AND a.status = 'active'
                AND a.license_type <> 'bonus'
                AND a.expires_at > NOW()
          )
        ORDER BY l.user_id, l.expires_at DESC, l.id DESC
    ),
    cohort AS (
        SELECT * FROM active_lic
        UNION ALL
        SELECT * FROM expired_lic
    ),
    pay AS (
        SELECT
            o.user_id,
            COUNT(*)::int AS paid_orders_n,
            COALESCE(SUM(p.amount_rub), 0)::float8 AS paid_rub_sum,
            MIN(o.paid_at) AS first_paid_at,
            MAX(o.paid_at) AS last_paid_at,
            BOOL_OR(COALESCE(t.type, '') = 'base') AS had_base,
            BOOL_OR(COALESCE(t.type, '') LIKE 'promo_test1week%') AS had_promo_week,
            BOOL_OR(
                COALESCE(t.type, '') NOT LIKE 'promo_test1week%'
                AND COALESCE(t.type, '') <> 'base'
            ) AS had_other_tariff,
            ARRAY_AGG(DISTINCT COALESCE(t.type, 'unknown') ORDER BY COALESCE(t.type, 'unknown'))
                AS tariff_types
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
    last_il AS (
        SELECT user_id, MAX(created_at) AS last_interaction_at
        FROM interaction_logs
        GROUP BY user_id
    ),
    first_ref AS (
        SELECT DISTINCT ON (user_id)
            user_id,
            COALESCE(ref_key, '') AS first_ref_key
        FROM attribution_touches
        WHERE ref_key IS NOT NULL AND btrim(ref_key) <> ''
        ORDER BY user_id, created_at ASC
    ),
    outreach AS (
        SELECT
            user_id,
            ARRAY_AGG(DISTINCT outreach_slug ORDER BY outreach_slug) AS outreach_slugs_sent,
            COUNT(*)::int AS outreach_sends_n
        FROM subscription_outreach_sent
        GROUP BY user_id
    ),
    survey AS (
        SELECT DISTINCT ON (user_id)
            user_id,
            topic,
            user_message,
            created_at AS survey_at
        FROM support_tickets
        WHERE topic ILIKE '%Причина ухода%'
           OR topic ILIKE '%после выхода%'
        ORDER BY user_id, created_at DESC
    )
    SELECT
        c.user_id,
        c.cohort,
        c.license_type,
        c.license_status,
        c.license_created_at,
        c.license_expires_at,
        c.license_updated_at,
        CASE
            WHEN c.cohort = 'expired' THEN
                GREATEST(0, EXTRACT(EPOCH FROM (NOW() - c.license_expires_at)) / 86400.0)
            ELSE NULL
        END AS days_since_expiry,
        CASE
            WHEN c.cohort = 'active' THEN
                EXTRACT(EPOCH FROM (c.license_expires_at - NOW())) / 86400.0
            ELSE NULL
        END AS days_until_expiry,
        u.created_at AS user_registered_at,
        u.last_activity AS user_last_activity,
        u.onboarding_complete,
        COALESCE(u.questions_asked, 0)::int AS questions_asked,
        u.is_banned,
        COALESCE(p.paid_orders_n, 0)::int AS paid_orders_n,
        COALESCE(p.paid_rub_sum, 0)::float8 AS paid_rub_sum,
        p.first_paid_at,
        p.last_paid_at,
        COALESCE(p.had_base, FALSE) AS had_base,
        COALESCE(p.had_promo_week, FALSE) AS had_promo_week,
        COALESCE(p.had_other_tariff, FALSE) AS had_other_tariff,
        COALESCE(p.tariff_types, ARRAY[]::text[]) AS tariff_types,
        CASE
            WHEN p.first_paid_at IS NULL THEN NULL
            WHEN c.cohort = 'expired' THEN
                EXTRACT(EPOCH FROM (c.license_expires_at - p.first_paid_at)) / 86400.0
            ELSE
                EXTRACT(EPOCH FROM (NOW() - p.first_paid_at)) / 86400.0
        END AS tenure_days_from_first_pay,
        CASE
            WHEN COALESCE(p.had_base, FALSE) THEN 'ever_base'
            WHEN COALESCE(p.had_promo_week, FALSE)
                 AND NOT COALESCE(p.had_other_tariff, FALSE)
                 AND NOT COALESCE(p.had_base, FALSE)
                THEN 'promo_week_only'
            WHEN COALESCE(p.paid_orders_n, 0) > 0 THEN 'paid_other_no_base'
            ELSE 'no_successful_payments'
        END AS payment_profile,
        COALESCE(g.club_group_msgs, 0)::int AS club_group_msgs,
        COALESCE(pr.private_user_msgs, 0)::int AS private_user_msgs,
        li.last_interaction_at,
        CASE
            WHEN li.last_interaction_at IS NULL THEN NULL
            ELSE EXTRACT(EPOCH FROM (NOW() - li.last_interaction_at)) / 86400.0
        END AS days_since_last_interaction,
        fr.first_ref_key,
        rk.type::text AS first_ref_type,
        rk.name AS first_ref_name,
        fs.status::text AS followup_status,
        mp.onboarding_stage AS member_onboarding_stage,
        mp.stated_goals AS member_stated_goals,
        mp.renewal_state AS member_renewal_state,
        mp.last_group_activity_at,
        mp.last_dm_at,
        o.outreach_slugs_sent,
        COALESCE(o.outreach_sends_n, 0)::int AS outreach_sends_n,
        s.topic AS last_churn_ticket_topic,
        s.user_message AS last_churn_ticket_message,
        s.survey_at AS last_churn_ticket_at
    FROM cohort c
    JOIN users u ON u.user_id = c.user_id
    LEFT JOIN pay p ON p.user_id = c.user_id
    LEFT JOIN grp g ON g.user_id = c.user_id
    LEFT JOIN prv pr ON pr.user_id = c.user_id
    LEFT JOIN last_il li ON li.user_id = c.user_id
    LEFT JOIN first_ref fr ON fr.user_id = c.user_id
    LEFT JOIN ref_keys rk ON rk.ref_key = fr.first_ref_key
    LEFT JOIN followup_states fs ON fs.user_id = c.user_id
    LEFT JOIN member_profiles mp ON mp.user_id = c.user_id
    LEFT JOIN outreach o ON o.user_id = c.user_id
    LEFT JOIN survey s ON s.user_id = c.user_id
    ORDER BY c.cohort DESC, c.user_id
    """
    raw = await _fetch_all(pool, q, int(club_group_id or 0))
    out: List[Dict[str, Any]] = []
    for r in raw:
        row = dict(r)
        # округления для LLM
        for k in (
            "days_since_expiry",
            "days_until_expiry",
            "tenure_days_from_first_pay",
            "days_since_last_interaction",
            "paid_rub_sum",
        ):
            if row.get(k) is not None:
                try:
                    row[k] = round(float(row[k]), 2)
                except (TypeError, ValueError):
                    pass
        msg = row.pop("last_churn_ticket_message", None)
        topic = row.get("last_churn_ticket_topic")
        reason = _parse_churn18_reason(msg or "") if msg else None
        row["churn18_reason"] = reason
        row["has_churn_feedback_ticket"] = bool(topic)
        if topic and "после выхода" in str(topic).lower():
            row["churn_freeform_intent"] = True
        else:
            row["churn_freeform_intent"] = False
        # не тащим сырой текст тикета в user-row (он в survey file)
        row.pop("last_churn_ticket_topic", None)
        if "tariff_types" in row and row["tariff_types"] is not None:
            row["tariff_types"] = list(row["tariff_types"])
        if "outreach_slugs_sent" in row and row["outreach_slugs_sent"] is not None:
            row["outreach_slugs_sent"] = list(row["outreach_slugs_sent"])
        # убрать возможный ПИИ из goals? stated_goals может содержать личное — оставляем
        # для анализа причин, но без имени/username уже ок
        out.append(row)
    return out


async def build_survey_rows(pool) -> List[Dict[str, Any]]:
    q = """
    SELECT
        t.ticket_id,
        t.ticket_number,
        t.user_id,
        t.topic,
        t.user_message,
        t.admin_response,
        t.status,
        t.created_at,
        EXISTS (
            SELECT 1 FROM license l
            WHERE l.user_id = t.user_id AND l.status = 'expired'
        ) AS user_has_expired_license,
        EXISTS (
            SELECT 1 FROM license l
            WHERE l.user_id = t.user_id
              AND l.status = 'active'
              AND l.license_type <> 'bonus'
              AND l.expires_at > NOW()
        ) AS user_has_active_license
    FROM support_tickets t
    WHERE t.topic ILIKE '%Причина ухода%'
       OR t.topic ILIKE '%после выхода%'
    ORDER BY t.created_at
    """
    rows = await _fetch_all(pool, q)
    out: List[Dict[str, Any]] = []
    for r in rows:
        msg = r.get("user_message") or ""
        reason = _parse_churn18_reason(msg)
        kind = "churn18_choice" if reason else (
            "freeform_offer" if "после выхода" in str(r.get("topic") or "").lower() else "other"
        )
        out.append(
            {
                "ticket_id": r["ticket_id"],
                "ticket_number": r["ticket_number"],
                "user_id": r["user_id"],
                "topic": r["topic"],
                "kind": kind,
                "churn18_reason": reason,
                "user_message": msg,
                "admin_response": r.get("admin_response"),
                "status": r.get("status"),
                "created_at": r.get("created_at"),
                "user_has_expired_license": bool(r.get("user_has_expired_license")),
                "user_has_active_license": bool(r.get("user_has_active_license")),
            }
        )
    return out


async def build_extra_slices(pool) -> Dict[str, Any]:
    exits_by_month = await _fetch_all(
        pool,
        """
        SELECT
            to_char(date_trunc('month', expires_at AT TIME ZONE 'Europe/Moscow'), 'YYYY-MM')
                AS month_msk,
            COUNT(DISTINCT user_id)::int AS expired_users
        FROM license
        WHERE status = 'expired'
        GROUP BY 1
        ORDER BY 1
        """,
    )
    exits_by_weekday = await _fetch_all(
        pool,
        """
        SELECT
            EXTRACT(ISODOW FROM expires_at AT TIME ZONE 'Europe/Moscow')::int AS isodow,
            COUNT(DISTINCT user_id)::int AS expired_users
        FROM license
        WHERE status = 'expired'
        GROUP BY 1
        ORDER BY 1
        """,
    )
    survey_breakdown = await _fetch_all(
        pool,
        """
        SELECT
            COALESCE(
                substring(user_message from 'выбрана причина ухода\\s*[—\\-:]\\s*(.+?)\\.?$'),
                '(не разобрано / freeform)'
            ) AS reason,
            COUNT(*)::int AS n
        FROM support_tickets
        WHERE topic ILIKE '%Причина ухода%'
        GROUP BY 1
        ORDER BY n DESC
        """,
    )
    renewals_after_expire = await _fetch_scalar(
        pool,
        """
        SELECT COUNT(DISTINCT e.user_id)::int
        FROM license e
        WHERE e.status = 'expired'
          AND EXISTS (
              SELECT 1 FROM license a
              WHERE a.user_id = e.user_id
                AND a.status = 'active'
                AND a.license_type <> 'bonus'
                AND a.expires_at > NOW()
                AND a.created_at > e.expires_at
          )
        """,
    )
    # люди, которые истекли и потом снова купили — не в expired-only cohort
    return {
        "exits_by_month_msk": exits_by_month,
        "exits_by_isodow_msk": exits_by_weekday,
        "churn18_reason_breakdown": survey_breakdown,
        "users_who_expired_then_returned_active_now": int(renewals_after_expire or 0),
    }


def write_readme(path: Path, *, generated_at: str, n_active: int, n_expired: int) -> None:
    path.write_text(
        f"""# Пакет данных: отток клуба (для LLM)

Сгенерировано: `{generated_at}` (UTC)

## Когорты (как в продуктовой статистике)

| Когорта | Определение | В этой выгрузке |
|---------|-------------|-----------------|
| **active** | `license.status=active`, `license_type <> bonus`, `expires_at > now()` | **{n_active}** |
| **expired** | есть `status=expired`, и нет активной платной/обычной лицензии сейчас | **{n_expired}** |

«Вышли после платного периода» ≈ когорта **expired** (включая тех, кто платил только тест-драйв, и тех, кто платил base).

## Файлы

1. `summary.json` — сводка + агрегаты (сравнение active vs expired) + разрезы опросов
2. `cohorts.jsonl` — **основной файл для анализа**: 1 JSON-объект на пользователя
3. `survey_responses.jsonl` — тикеты «причина ухода (+18д)» и «обратная связь после выхода»
4. `dictionary.md` — описание полей
5. `about_club.txt` — описание клуба (ценность / продукт)

## Как кормить LLM

Рекомендуемый порядок:

1. Этот README + `dictionary.md` + `about_club.txt`
2. `summary.json`
3. `survey_responses.jsonl` (качественные причины)
4. При необходимости — `cohorts.jsonl` целиком или выборка (файл ~сотни строк, обычно влезает)

Промпт-рамка:

> Сравни когорты active и expired. Найди факторы, отличающие ушедших.  
> Отдельно разбери ответы опроса причин ухода.  
> Сформулируй гипотезы оттока и конкретные действия по удержанию.  
> Не выдумывай числа — только из данных.

## Приватность

В выгрузке **нет** username / first_name / phone. Есть `user_id` (Telegram id) для склейки строк — при загрузке во внешние LLM можно удалить поле `user_id`, если нужна полная анонимизация.
""",
        encoding="utf-8",
    )


def write_dictionary(path: Path) -> None:
    path.write_text(
        """# Словарь полей

## cohorts.jsonl

| Поле | Смысл |
|------|--------|
| `cohort` | `active` или `expired` |
| `license_type` | тип строки лицензии (subscription / bonus / …) |
| `license_expires_at` | дата окончания доступа |
| `days_since_expiry` | сколько дней прошло после конца (только expired) |
| `days_until_expiry` | сколько дней до конца (только active) |
| `payment_profile` | `ever_base` / `promo_week_only` / `paid_other_no_base` / `no_successful_payments` |
| `had_base` | была ли успешная оплата тарифа `base` |
| `had_promo_week` | была ли оплата `promo_test1week*` |
| `paid_orders_n` / `paid_rub_sum` | число и сумма успешных оплат (₽) |
| `tenure_days_from_first_pay` | дни от первой оплаты до конца лицензии (expired) или до сейчас (active) |
| `club_group_msgs` | сообщений пользователя в группе клуба |
| `private_user_msgs` | входящих сообщений в личку боту |
| `questions_asked` | счётчик вопросов в профиле users |
| `days_since_last_interaction` | дней с последнего события interaction_logs |
| `first_ref_key` / `first_ref_type` / `first_ref_name` | первый источник прихода |
| `followup_status` | состояние воронки follow-up |
| `member_onboarding_stage` / `member_stated_goals` / `member_renewal_state` | member_profiles |
| `outreach_slugs_sent` | какие churn/reminder сообщения уже отправлялись |
| `churn18_reason` | последняя выбранная причина ухода (если есть) |
| `has_churn_feedback_ticket` | есть ли тикет обратной связи по churn |

## survey_responses.jsonl

| Поле | Смысл |
|------|--------|
| `kind` | `churn18_choice` (кнопка причины) / `freeform_offer` (нажал «написать ответ» +5д) / `other` |
| `churn18_reason` | текст выбранной причины |
| `user_message` | полный текст тикета |

## Важные оговорки

- Один пользователь не может быть одновременно в active и expired когортах этой выгрузки: если снова купил — он только в **active**.
- `club_group_msgs` зависит от `CLUB_GROUP_ID` в `.env`; если id неверный — счётчики группы будут 0.
- Агрегаты в `summary.json` → `aggregates_from_club_churn_report` совпадают с логикой команды `/churn` в админке.
""",
        encoding="utf-8",
    )


async def main() -> None:
    ap = argparse.ArgumentParser(description="Export club churn pack for LLM analysis")
    ap.add_argument("--env", default="/home/appuser/club/.env")
    ap.add_argument(
        "--out-dir",
        default="",
        help="Каталог выгрузки (по умолчанию club/exports/churn_llm_pack_TIMESTAMP)",
    )
    args = ap.parse_args()

    cfg = _load_config(args.env if Path(args.env).is_file() else None)
    storage = UserStorage(cfg.database_url)
    await storage.initialize()
    pool = storage.pool
    assert pool is not None

    club_group_id = int(getattr(cfg, "CLUB_GROUP_ID", 0) or 0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    out = Path(args.out_dir) if args.out_dir else (
        Path(__file__).resolve().parents[1] / "exports" / f"churn_llm_pack_{stamp}"
    )
    out.mkdir(parents=True, exist_ok=True)

    print(f"→ writing to {out}")
    print(f"  club_group_id={club_group_id}")

    collector = ClubChurnReportCollector(pool, club_group_id=club_group_id)
    aggregates = await collector.build_payload()
    print("  aggregates OK")

    cohorts = await build_cohort_rows(pool, club_group_id)
    n_active = sum(1 for r in cohorts if r["cohort"] == "active")
    n_expired = sum(1 for r in cohorts if r["cohort"] == "expired")
    print(f"  cohorts: active={n_active} expired={n_expired}")

    surveys = await build_survey_rows(pool)
    print(f"  survey tickets: {len(surveys)}")

    extra = await build_extra_slices(pool)

    # быстрые разрезы по cohort rows
    def _bucket_count(rows: List[Dict[str, Any]], key: str) -> Dict[str, int]:
        acc: Dict[str, int] = {}
        for r in rows:
            k = str(r.get(key) or "null")
            acc[k] = acc.get(k, 0) + 1
        return dict(sorted(acc.items(), key=lambda x: -x[1]))

    active_rows = [r for r in cohorts if r["cohort"] == "active"]
    expired_rows = [r for r in cohorts if r["cohort"] == "expired"]

    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "definitions": {
            "active": "license active, not bonus, expires_at > now()",
            "expired": "has expired license and no current active non-bonus license",
            "note": "Совпадает с продуктовой картиной ~150 активных / ~245 вышедших",
        },
        "counts": {
            "active": n_active,
            "expired": n_expired,
            "ratio_expired_to_active": round(n_expired / n_active, 3) if n_active else None,
            "survey_tickets": len(surveys),
            "churn18_with_parsed_reason": sum(
                1 for s in surveys if s.get("churn18_reason")
            ),
        },
        "cohort_payment_profile": {
            "active": _bucket_count(active_rows, "payment_profile"),
            "expired": _bucket_count(expired_rows, "payment_profile"),
        },
        "cohort_engagement_means": {
            "active": {
                "club_group_msgs_avg": round(
                    sum(r["club_group_msgs"] for r in active_rows) / max(len(active_rows), 1), 2
                ),
                "private_user_msgs_avg": round(
                    sum(r["private_user_msgs"] for r in active_rows) / max(len(active_rows), 1), 2
                ),
                "questions_asked_avg": round(
                    sum(r["questions_asked"] for r in active_rows) / max(len(active_rows), 1), 2
                ),
                "paid_rub_sum_avg": round(
                    sum(r["paid_rub_sum"] for r in active_rows) / max(len(active_rows), 1), 2
                ),
                "share_zero_group_msgs": round(
                    sum(1 for r in active_rows if r["club_group_msgs"] == 0)
                    / max(len(active_rows), 1),
                    3,
                ),
            },
            "expired": {
                "club_group_msgs_avg": round(
                    sum(r["club_group_msgs"] for r in expired_rows) / max(len(expired_rows), 1), 2
                ),
                "private_user_msgs_avg": round(
                    sum(r["private_user_msgs"] for r in expired_rows) / max(len(expired_rows), 1), 2
                ),
                "questions_asked_avg": round(
                    sum(r["questions_asked"] for r in expired_rows) / max(len(expired_rows), 1), 2
                ),
                "paid_rub_sum_avg": round(
                    sum(r["paid_rub_sum"] for r in expired_rows) / max(len(expired_rows), 1), 2
                ),
                "share_zero_group_msgs": round(
                    sum(1 for r in expired_rows if r["club_group_msgs"] == 0)
                    / max(len(expired_rows), 1),
                    3,
                ),
            },
        },
        "extra_slices": extra,
        "aggregates_from_club_churn_report": aggregates,
    }

    generated = summary["generated_at_utc"]
    write_readme(out / "README.md", generated_at=generated, n_active=n_active, n_expired=n_expired)
    write_dictionary(out / "dictionary.md")
    _json_dump(out / "summary.json", summary)
    _write_jsonl(out / "cohorts.jsonl", cohorts)
    _write_jsonl(out / "survey_responses.jsonl", surveys)

    about = load_aboutclub_text()
    (out / "about_club.txt").write_text(about or "(aboutclub.txt не найден)\n", encoding="utf-8")

    # манифест размеров
    manifest = {
        "path": str(out),
        "files": {
            p.name: p.stat().st_size
            for p in sorted(out.iterdir())
            if p.is_file()
        },
        "counts": summary["counts"],
    }
    _json_dump(out / "manifest.json", manifest)

    await storage.close()
    print("OK")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
