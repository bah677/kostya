#!/usr/bin/env python3
"""
Раскладка подарочной волны gift-2026-09 по тестовым когортам удержания.

Четыре варианта по 10 человек, остальные — контроль (цепочка как была):

  p1_discount     скидка 30% на месяц: 1200 → 840 ₽ (промо giftreturn30)
  p2_sell5d       письмо за 5 дней продаёт продление, а не реферальную ссылку
  p3_group_close  прямо сказано, что в дату окончания закроется доступ в группу
  p4_step299      ступенька: две недели за 299 ₽ вместо полного месяца

ПОЧЕМУ СО СТРАТАМИ. Когорта разнородная: четверть живёт в клубе, половина
не писала больше двух недель. Если набрать десятку случайно, в одну когорту
попадут живые, в другую ушедшие, и разница в оплатах покажет не вариант
письма, а состав группы. Поэтому люди сначала делятся на страты
(активность × волна истечения), и из каждой страты участники раздаются
вариантам по очереди.

Раскладка детерминированная: при одном и том же --seed результат совпадает.
Повторный запуск никого не переназначает (ON CONFLICT DO NOTHING).

  cd /home/appuser/dev/kostya/club
  python3 scripts/assign_gift_retention_cohorts.py --dry-run
  # на прод-БД (только после наката кода и миграции 039):
  python3 scripts/assign_gift_retention_cohorts.py --env-file /home/appuser/club/.env --dry-run
  python3 scripts/assign_gift_retention_cohorts.py --env-file /home/appuser/club/.env --apply
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_config
from storage.db.gift_retention_experiment import (
    CAMPAIGN_GIFT_2026_09,
    VARIANT_CONTROL,
    VARIANT_P1_DISCOUNT,
    VARIANT_P2_SELL_5D,
    VARIANT_P3_GROUP_CLOSE,
    VARIANT_P4_STEP_299,
)
from storage.user_storage import UserStorage

logger = logging.getLogger("assign_gift_retention_cohorts")

#: Порядок раздачи. Меняя его, меняешь раскладку — при повторном запуске
#: уже назначенные останутся на месте, но новые поедут иначе.
VARIANT_ORDER = (
    VARIANT_P1_DISCOUNT,
    VARIANT_P2_SELL_5D,
    VARIANT_P3_GROUP_CLOSE,
    VARIANT_P4_STEP_299,
)

GIFT_RETURN_PROMO_GUID = "giftreturn30"

#: Дата, по которой делим волну истечения: с 29 октября истекает основная масса.
WAVE_SPLIT = date(2026, 10, 29)

_CANDIDATES_SQL = """
SELECT
    l.user_id,
    (timezone('Europe/Moscow', l.expires_at))::date AS expires_on,
    (
        SELECT max(m.created_at) FROM messages m
        WHERE m.user_id = l.user_id AND m.sender_type = 'user'
    ) AS last_msg,
    (
        SELECT coalesce(max(f.msgs_group), 0) FROM club_first_week f
        WHERE f.user_id = l.user_id
    ) AS msgs_group,
    EXISTS (
        SELECT 1 FROM club_group_member_cache k WHERE k.user_id = l.user_id
    ) AS in_group
FROM license l
JOIN users u ON u.user_id = l.user_id
WHERE l.status = 'active'
  AND l.origin = 'gift'
  AND l.license_type = 'admin_grant'
  AND l.expires_at < $1::date
  AND u.is_active = TRUE
ORDER BY l.user_id
"""


def _load_config(env_file: str):
    if env_file:
        from dotenv import load_dotenv

        load_dotenv(env_file, override=True)
    return load_config()


def _segment(row: Dict[str, Any], today: date) -> str:
    """Активность — те же четыре группы, по которым смотрели когорту."""
    last = row.get("last_msg")
    days = (today - last.date()).days if last else 10_000
    msgs_group = int(row.get("msgs_group") or 0)
    if days <= 7 and msgs_group > 0:
        return "живые"
    if days <= 7:
        return "пишут-боту"
    if row.get("in_group") and days <= 14:
        return "притихли"
    return "ушли-молча"


def _stratum(row: Dict[str, Any], today: date) -> str:
    wave = "позже" if row["expires_on"] >= WAVE_SPLIT else "скоро"
    return f"{_segment(row, today)}|{wave}"


def _order_key(seed: str, user_id: int) -> str:
    """Детерминированный «случайный» порядок внутри страты."""
    return hashlib.md5(f"{seed}:{user_id}".encode()).hexdigest()


def plan_assignment(
    rows: List[Dict[str, Any]], *, seed: str, per_variant: int, today: date
) -> Dict[int, Dict[str, Any]]:
    """user_id → {variant, stratum, expires_on}. Чистая функция, её проверяет тест.

    Отбор пропорциональный: каждая страта отдаёт в тест свою долю людей
    (размер страты × доля теста), а не «кто попался первым». Иначе крупная
    страта выбирает все квоты целиком — первая версия скрипта так и делала,
    и все сорок человек приехали из одной страты «ушли молча».
    Остатки раздаются по наибольшей дробной части, чтобы сумма сошлась точно.
    """
    by_stratum: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_stratum[_stratum(r, today)].append(r)

    total_slots = per_variant * len(VARIANT_ORDER)
    total_people = len(rows)
    if total_people <= total_slots:
        # Людей не больше, чем мест: контроля не остаётся, берём всех подряд.
        quotas = {st: len(m) for st, m in by_stratum.items()}
    else:
        share = total_slots / total_people
        exact = {st: len(m) * share for st, m in by_stratum.items()}
        quotas = {st: int(v) for st, v in exact.items()}
        # Не даём страте больше людей, чем в ней есть.
        for st in quotas:
            quotas[st] = min(quotas[st], len(by_stratum[st]))
        left = total_slots - sum(quotas.values())
        order = sorted(
            by_stratum,
            key=lambda st: (-(exact[st] - int(exact[st])), -len(by_stratum[st]), st),
        )
        i = 0
        while left > 0 and order:
            st = order[i % len(order)]
            if quotas[st] < len(by_stratum[st]):
                quotas[st] += 1
                left -= 1
            elif all(quotas[k] >= len(by_stratum[k]) for k in order):
                break
            i += 1

    remaining = {v: per_variant for v in VARIANT_ORDER}
    plan: Dict[int, Dict[str, Any]] = {}
    turn = 0

    # Внутри страты — детерминированный порядок; варианты раздаются по
    # «карусели», общей для всех страт, поэтому каждый вариант получает
    # примерно одинаковый срез из каждой страты.
    for stratum in sorted(by_stratum, key=lambda k: (-len(by_stratum[k]), k)):
        members = sorted(
            by_stratum[stratum], key=lambda r: _order_key(seed, r["user_id"])
        )
        take = quotas.get(stratum, 0)
        for idx, r in enumerate(members):
            variant = VARIANT_CONTROL
            if idx < take:
                for step in range(len(VARIANT_ORDER)):
                    v = VARIANT_ORDER[(turn + step) % len(VARIANT_ORDER)]
                    if remaining[v] > 0:
                        remaining[v] -= 1
                        turn = (turn + step + 1) % len(VARIANT_ORDER)
                        variant = v
                        break
            plan[r["user_id"]] = {
                "variant": variant,
                "stratum": stratum,
                "expires_on": r["expires_on"],
            }
    return plan


def _print_plan(
    plan: Dict[int, Dict[str, Any]], rows: List[Dict[str, Any]], today: date
) -> None:
    by_variant: Dict[str, List[int]] = defaultdict(list)
    for uid, p in plan.items():
        by_variant[p["variant"]].append(uid)

    print(f"\nВсего кандидатов: {len(rows)}")
    print("\nРазмеры когорт:")
    for v in VARIANT_ORDER + (VARIANT_CONTROL,):
        print(f"  {v:<16} {len(by_variant.get(v, [])):>3}")

    print("\nБаланс по стратам (строка — страта, столбцы — варианты):")
    strata = sorted({p["stratum"] for p in plan.values()})
    head = "  " + f"{'страта':<22}" + "".join(f"{v[:12]:>14}" for v in VARIANT_ORDER)
    print(head + f"{'control':>10}")
    for st in strata:
        cells = []
        for v in VARIANT_ORDER + (VARIANT_CONTROL,):
            n = sum(1 for p in plan.values() if p["stratum"] == st and p["variant"] == v)
            cells.append(n)
        line = "  " + f"{st:<22}" + "".join(f"{c:>14}" for c in cells[:-1])
        print(line + f"{cells[-1]:>10}")

    print("\nДаты окончания по когортам:")
    for v in VARIANT_ORDER:
        ds = sorted(p["expires_on"] for p in plan.values() if p["variant"] == v)
        if ds:
            print(f"  {v:<16} {ds[0]} … {ds[-1]}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default="", help="путь к .env (для прод-БД)")
    ap.add_argument("--campaign", default=CAMPAIGN_GIFT_2026_09)
    ap.add_argument("--per-variant", type=int, default=10)
    ap.add_argument("--seed", default="gift-2026-09-retention")
    ap.add_argument("--horizon", default="2027-01-01",
                    help="верхняя граница даты окончания: отсекает служебные лицензии")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = _load_config(args.env_file)
    storage = UserStorage(cfg.database_url)
    await storage.initialize()
    today = date.today()

    try:
        async with storage.get_connection() as conn:
            horizon = datetime.strptime(args.horizon, "%Y-%m-%d").date()
            rows = [dict(r) for r in await conn.fetch(_CANDIDATES_SQL, horizon)]
        if not rows:
            print("Кандидатов нет — проверь origin/license_type и --horizon.")
            return 1

        plan = plan_assignment(
            rows, seed=args.seed, per_variant=args.per_variant, today=today
        )
        _print_plan(plan, rows, today)

        if args.dry_run:
            print("\n--dry-run: в базу ничего не записано.")
            return 0

        written = Counter()
        for uid, p in plan.items():
            ok = await storage.assign_gift_retention_variant(
                uid,
                campaign=args.campaign,
                variant=p["variant"],
                stratum=p["stratum"],
                expires_on=p["expires_on"],
                assign_seed=args.seed,
            )
            written[p["variant"]] += 1 if ok else 0

        print("\nЗаписано в gift_retention_experiment:")
        for v, n in sorted(written.items()):
            print(f"  {v:<16} {n:>3}")

        # п1 — промо назначаем сразу: скидка должна быть видна в /payment,
        # даже если человек придёт туда сам, не дожидаясь письма.
        print("\nНазначение промо giftreturn30 когорте p1_discount:")
        p1 = [uid for uid, p in plan.items() if p["variant"] == VARIANT_P1_DISCOUNT]
        assigned, skipped = 0, []
        for uid in sorted(p1):
            existing = await storage.get_active_user_promo_campaign(uid)
            if existing and existing.get("campaign_guid") != GIFT_RETURN_PROMO_GUID:
                # Чужую активную скидку не затираем: assign_user_promo_campaign
                # перезаписывает предыдущую, а у части людей это скидка
                # донора БиблияБота.
                skipped.append((uid, existing.get("campaign_guid")))
                continue
            if await storage.assign_user_promo_campaign(uid, GIFT_RETURN_PROMO_GUID):
                assigned += 1
        print(f"  назначено: {assigned} из {len(p1)}")
        for uid, guid in skipped:
            print(f"  пропущен {uid}: уже есть активное промо {guid}")
        return 0
    finally:
        await storage.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
