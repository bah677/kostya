"""Read-only viewer: список users + история private-сообщений с ботом."""

from __future__ import annotations

import json
import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

BotKey = Literal["club", "biblia"]

_CB_CONTENT_RE = re.compile(r"^\[нажата кнопка:\s*(.+?)\]\s*$", re.IGNORECASE)

# Фоллбек, если в raw_data нет reply_markup (старые записи / урезанный json).
_CB_LABEL_FALLBACK: Dict[str, str] = {
    "menu_act:home": "🏠 Главное меню",
    "menu_act:payment": "💳 Оплата и тарифы",
    "menu_act:subs": "📅 Моя подписка",
    "menu_act:club": "🔗 Ссылка на Клуб",
    "menu_act:support": "🆘 Написать в техподдержку",
    "menu_act:feedback": "💬 Написать отзыв",
    "menu_act:affiliate": "🔗 Реферальная программа",
    "menu_act:benefit": "🎁 Бонусы",
    "menu_act:wish_board": "💫 Доска добрых дел",
    "menu_act:member_gift": "🎁 Подарить продление в Клубе",
    "payment_start": "💳 Оплатить / Продлить",
    "payment_gift_start": "🎁 Подарить подписку",
    "payment_gift_continue": "Продолжить",
    "payment_back_to_tariffs": "◀️ Назад",
    "payment_offer_pdf": "📄 Оферта",
    "legal:accept": "✅ Принимаю",
    "legal:doc:consent": "Согласие",
    "legal:doc:offer": "Оферта",
    "wb:hub": "◀️ Назад",
    "wb:don": "🎁 Откликнуться и помочь",
    "wb:req": "🙏 Попросить помощь",
    "wb:my": "📋 Мои просьбы",
    "followup_stuck_get_answer": "Получить ответ",
    "onboarding_topic_relations": "про отношения",
    "onboarding_topic_money": "про деньги",
    "benefit_prayer260408": "🙏 Молитва",
    "benefit_prayer260425": "🙏 Молитва",
    # Biblia
    "prayer_start": "🙏 Молитва",
    "payment_cancel": "Отмена",
    "payment_currency_rub": "₽ Рубли",
    "marathon_open": "Марафон",
    "challenge_start": "Челлендж",
    "challenge_time_custom": "Своё время",
    "payment_rub_custom": "Своя сумма",
}


def _parse_raw(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except Exception:
            return None
        return data if isinstance(data, dict) else None
    return None


def _label_from_markup(raw: Optional[Dict[str, Any]], callback_data: str) -> Optional[str]:
    if not raw or not callback_data:
        return None
    msg = raw.get("message") or {}
    if not isinstance(msg, dict):
        return None
    markup = msg.get("reply_markup") or {}
    if not isinstance(markup, dict):
        return None
    rows = markup.get("inline_keyboard") or []
    for row in rows:
        if not isinstance(row, list):
            continue
        for btn in row:
            if not isinstance(btn, dict):
                continue
            if (btn.get("callback_data") or "") == callback_data:
                text = (btn.get("text") or "").strip()
                if text:
                    return text
    return None


def _fallback_label(callback_data: str) -> Optional[str]:
    if callback_data in _CB_LABEL_FALLBACK:
        return _CB_LABEL_FALLBACK[callback_data]
    # payment_select_1 / payment_currency_rub_2 / wb:view:11 — без точного текста
    if callback_data.startswith("payment_select_"):
        return "Выбрать тариф"
    if callback_data.startswith("payment_currency_rub_"):
        return "₽ Рубли"
    if callback_data.startswith("payment_currency_usd_"):
        return "$ Доллары"
    if callback_data.startswith("payment_select_gift_"):
        return "Подарочный тариф"
    if callback_data.startswith("wb:view:"):
        return "Открыть просьбу"
    if callback_data.startswith("wb:type:"):
        return "Тип просьбы"
    if callback_data.startswith("wb:rate:"):
        return "Оценка"
    if callback_data.startswith("payment_rub_amount_"):
        return f"₽ {callback_data.rsplit('_', 1)[-1]}"
    if callback_data.startswith("challenge_time_"):
        return "Время челленджа"
    if callback_data.startswith("more_button_"):
        return "Ещё"
    if callback_data.startswith("ap:"):
        return "Ангельский пул"
    return None


def display_message_content(content: str, raw: Any = None) -> str:
    """Для callback — подпись кнопки, как её видел пользователь."""
    text = content or ""
    m = _CB_CONTENT_RE.match(text.strip())
    if not m:
        return text
    cb = m.group(1).strip()
    parsed = _parse_raw(raw)
    label = _label_from_markup(parsed, cb) or _fallback_label(cb)
    if label:
        return f"кнопка [{label}]"
    return f"кнопка [{cb}]"


FilterTag = Literal[
    "all",
    "active",
    "expired",
    "no_license",
    "banned",
    "blocked",
    "has_dm",
    "onboarding",
    "greeter",
    # biblia
    "alive",
    "donor",
    "no_donor",
    "mail_off",
]

SortKey = Literal[
    "last_activity",
    "last_dm",
    "created_at",
    "name",
    "expires_at",
]


class UserRow(BaseModel):
    user_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    is_banned: bool = False
    is_active: bool = True
    bot_blocked_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    last_activity: Optional[datetime] = None
    first_touch_key: Optional[str] = None
    license_status: str = "none"
    license_expires_at: Optional[datetime] = None
    license_origin: Optional[str] = None
    onboarding_stage: Optional[str] = None
    last_dm_at: Optional[datetime] = None
    dm_count: int = 0
    is_donor: bool = False
    mailing_consent: Optional[bool] = None
    display_name: str = ""
    tags: List[str] = Field(default_factory=list)


class UsersResponse(BaseModel):
    total: int
    offset: int
    limit: int
    users: List[UserRow]


class MessageRow(BaseModel):
    id: int
    created_at: datetime
    sender: Literal["user", "bot"]
    message_type: str
    content: str
    telegram_message_id: Optional[int] = None


class MessagesResponse(BaseModel):
    user_id: int
    messages: List[MessageRow]
    has_more: bool
    next_before_id: Optional[int] = None


def _display_name(row: asyncpg.Record) -> str:
    for key in ("name", "first_name", "username"):
        val = row.get(key)
        if val and str(val).strip():
            return str(val).strip()
    last = (row.get("last_name") or "").strip()
    if last:
        return last
    return str(row["user_id"])


def _license_status(row: asyncpg.Record) -> str:
    raw = (row.get("license_status_sql") or "").strip().lower()
    if raw in ("active", "expired", "none"):
        return raw
    status = (row.get("lic_status") or "").strip().lower()
    expires = row.get("lic_expires_at")
    if not row.get("lic_user_id"):
        return "none"
    if status == "active" and expires is not None:
        # fallback; предпочтительно license_status_sql из SELECT
        return "active" if expires > datetime.now() else "expired"
    if status == "active" and expires is None:
        return "active"
    return "expired"


def _tags_for(row: asyncpg.Record, lic: str, bot: BotKey = "club") -> List[str]:
    tags: List[str] = []
    if bot == "club":
        if lic == "active":
            tags.append("active")
        elif lic == "expired":
            tags.append("expired")
        else:
            tags.append("no_license")
    else:
        if row.get("is_active") and row.get("bot_blocked_at") is None:
            tags.append("alive")
        if row.get("is_donor"):
            tags.append("donor")
        elif row.get("is_donor") is False:
            tags.append("no_donor")
        if row.get("mailing_consent") is False:
            tags.append("mail_off")
    if row.get("is_banned"):
        tags.append("banned")
    if row.get("bot_blocked_at") is not None or row.get("is_active") is False:
        tags.append("blocked")
    if (row.get("dm_count") or 0) > 0:
        tags.append("has_dm")
    stage = (row.get("onboarding_stage") or "").strip()
    if stage and stage not in ("not_started", "", "done"):
        tags.append("onboarding")
    if row.get("is_greeter"):
        tags.append("greeter")
    origin = (row.get("lic_origin") or "").strip()
    if origin:
        tags.append(f"origin:{origin}")
    touch = (row.get("first_touch_key") or "").strip()
    if touch:
        tags.append(f"touch:{touch}")
    return tags


def _row_to_user(row: asyncpg.Record, bot: BotKey = "club") -> UserRow:
    lic = _license_status(row)
    return UserRow(
        user_id=int(row["user_id"]),
        username=row.get("username"),
        first_name=row.get("first_name"),
        last_name=row.get("last_name"),
        name=row.get("name"),
        phone=row.get("phone"),
        email=row.get("email"),
        is_banned=bool(row.get("is_banned")),
        is_active=bool(row.get("is_active")),
        bot_blocked_at=row.get("bot_blocked_at"),
        created_at=row.get("created_at"),
        last_activity=row.get("last_activity"),
        first_touch_key=row.get("first_touch_key"),
        license_status=lic,
        license_expires_at=row.get("lic_expires_at"),
        license_origin=row.get("lic_origin"),
        onboarding_stage=row.get("onboarding_stage"),
        last_dm_at=row.get("last_dm_at"),
        dm_count=int(row.get("dm_count") or 0),
        is_donor=bool(row.get("is_donor")),
        mailing_consent=row.get("mailing_consent"),
        display_name=_display_name(row),
        tags=_tags_for(row, lic, bot),
    )


def _base_from_sql(bot: BotKey, *, need_dm: bool) -> str:
    mp = (
        "LEFT JOIN member_profiles mp ON mp.user_id = u.user_id\n"
        if bot == "club"
        else ""
    )
    dm = ""
    if need_dm:
        dm = """
LEFT JOIN (
  SELECT
    m.user_id,
    MAX(m.created_at) AS last_dm_at,
    COUNT(*)::int AS dm_count
  FROM messages m
  WHERE COALESCE(m.chat_type, 'private') = 'private'
  GROUP BY m.user_id
) dm ON dm.user_id = u.user_id
"""
    return f"""
FROM users u
LEFT JOIN license l ON l.user_id = u.user_id
{mp}{dm}
"""


def _select_cols(bot: BotKey, *, with_dm_join: bool) -> str:
    name_phone = (
        "u.name, u.phone, u.email, u.first_touch_key,"
        if bot == "club"
        else "NULL::text AS name, NULL::text AS phone, NULL::text AS email, NULL::text AS first_touch_key,"
    )
    origin = "l.origin AS lic_origin," if bot == "club" else "NULL::text AS lic_origin,"
    onboarding = (
        "mp.onboarding_stage,"
        if bot == "club"
        else (
            "CASE WHEN COALESCE(u.onboarding_complete, false) "
            "THEN 'done' ELSE 'not_started' END AS onboarding_stage,"
        )
    )
    greeter = (
        """EXISTS(
    SELECT 1 FROM club_greeter g
    WHERE g.user_id = u.user_id AND g.active = true
  ) AS is_greeter"""
        if bot == "club"
        else "false AS is_greeter"
    )
    if bot == "biblia":
        donor = """EXISTS(
    SELECT 1 FROM donations d
    WHERE d.user_id = u.user_id AND d.status = 'succeeded'
  ) AS is_donor,
  u.mailing_consent,"""
    else:
        donor = "false AS is_donor, NULL::boolean AS mailing_consent,"
    if with_dm_join:
        dm_cols = "dm.last_dm_at, COALESCE(dm.dm_count, 0)::int AS dm_count,"
    else:
        # Без тяжёлого GROUP BY: счётчики добираем LATERAL только на странице
        dm_cols = "NULL::timestamptz AS last_dm_at, 0::int AS dm_count,"
    return f"""
SELECT
  u.user_id,
  u.username,
  u.first_name,
  u.last_name,
  {name_phone}
  u.is_banned,
  u.is_active,
  u.bot_blocked_at,
  u.created_at,
  u.last_activity,
  l.user_id AS lic_user_id,
  l.status AS lic_status,
  l.expires_at AS lic_expires_at,
  {origin}
  CASE
    WHEN l.user_id IS NULL THEN 'none'
    WHEN l.status = 'active' AND l.expires_at IS NOT NULL AND l.expires_at > NOW()
      THEN 'active'
    ELSE 'expired'
  END AS license_status_sql,
  {onboarding}
  {dm_cols}
  {donor}
  {greeter}
"""


def _filter_sql(tag: FilterTag, bot: BotKey) -> str:
    if tag == "all":
        return "TRUE"
    if tag == "active":
        return (
            "l.status = 'active' AND l.expires_at IS NOT NULL "
            "AND l.expires_at > NOW()"
        )
    if tag == "expired":
        return (
            "l.user_id IS NOT NULL AND NOT ("
            "l.status = 'active' AND l.expires_at IS NOT NULL "
            "AND l.expires_at > NOW()"
            ")"
        )
    if tag == "no_license":
        return "l.user_id IS NULL"
    if tag == "banned":
        return "u.is_banned = true"
    if tag == "blocked":
        return "(u.bot_blocked_at IS NOT NULL OR u.is_active IS FALSE)"
    if tag == "has_dm":
        return """EXISTS (
          SELECT 1 FROM messages m
          WHERE m.user_id = u.user_id
            AND COALESCE(m.chat_type, 'private') = 'private'
          LIMIT 1
        )"""
    if tag == "onboarding":
        if bot == "biblia":
            return "COALESCE(u.onboarding_complete, false) = false"
        return (
            "mp.onboarding_stage IS NOT NULL "
            "AND mp.onboarding_stage NOT IN ('not_started', '')"
        )
    if tag == "greeter":
        if bot != "club":
            return "FALSE"
        return (
            "EXISTS (SELECT 1 FROM club_greeter g "
            "WHERE g.user_id = u.user_id AND g.active = true)"
        )
    if tag == "alive":
        return "u.is_active IS TRUE AND u.bot_blocked_at IS NULL"
    if tag == "donor":
        if bot != "biblia":
            return "FALSE"
        return (
            "EXISTS (SELECT 1 FROM donations d "
            "WHERE d.user_id = u.user_id AND d.status = 'succeeded')"
        )
    if tag == "no_donor":
        if bot != "biblia":
            return "FALSE"
        return (
            "NOT EXISTS (SELECT 1 FROM donations d "
            "WHERE d.user_id = u.user_id AND d.status = 'succeeded')"
        )
    if tag == "mail_off":
        if bot != "biblia":
            return "FALSE"
        return "COALESCE(u.mailing_consent, true) = false"
    return "TRUE"


def _sort_sql(sort: SortKey, bot: BotKey, *, need_dm: bool) -> str:
    name_expr = (
        "LOWER(COALESCE(NULLIF(u.name, ''), NULLIF(u.first_name, ''), "
        "NULLIF(u.username, ''), u.user_id::text)) ASC"
        if bot == "club"
        else (
            "LOWER(COALESCE(NULLIF(u.first_name, ''), NULLIF(u.username, ''), "
            "u.user_id::text)) ASC"
        )
    )
    last_dm = (
        "dm.last_dm_at DESC NULLS LAST, u.user_id DESC"
        if need_dm
        else (
            "(SELECT MAX(m.created_at) FROM messages m "
            "WHERE m.user_id = u.user_id AND COALESCE(m.chat_type, 'private') = 'private') "
            "DESC NULLS LAST, u.user_id DESC"
        )
    )
    mapping = {
        "last_activity": "u.last_activity DESC NULLS LAST, u.user_id DESC",
        "last_dm": last_dm,
        "created_at": "u.created_at DESC NULLS LAST, u.user_id DESC",
        "name": name_expr,
        "expires_at": "l.expires_at DESC NULLS LAST, u.user_id DESC",
    }
    return mapping.get(sort, mapping["last_activity"])


def _search_clause(q: str, bot: BotKey) -> tuple[str, List[Any]]:
    q = (q or "").strip()
    if not q:
        return "TRUE", []
    like = f"%{q}%"
    if bot == "club":
        clause = """(
          u.user_id::text ILIKE $__q
          OR COALESCE(u.username, '') ILIKE $__q
          OR COALESCE(u.first_name, '') ILIKE $__q
          OR COALESCE(u.last_name, '') ILIKE $__q
          OR COALESCE(u.name, '') ILIKE $__q
          OR COALESCE(u.phone, '') ILIKE $__q
          OR COALESCE(u.email, '') ILIKE $__q
          OR COALESCE(u.profile, '') ILIKE $__q
          OR COALESCE(u.first_touch_key, '') ILIKE $__q
          OR COALESCE(u.first_touch_kind, '') ILIKE $__q
          OR COALESCE(u.language_code, '') ILIKE $__q
          OR COALESCE(u.openai_thread_id, '') ILIKE $__q
          OR COALESCE(u.agent_session_id, '') ILIKE $__q
          OR COALESCE(u.feedback_text, '') ILIKE $__q
        )"""
    else:
        clause = """(
          u.user_id::text ILIKE $__q
          OR COALESCE(u.username, '') ILIKE $__q
          OR COALESCE(u.first_name, '') ILIKE $__q
          OR COALESCE(u.last_name, '') ILIKE $__q
          OR COALESCE(u.profile, '') ILIKE $__q
          OR COALESCE(u.language_code, '') ILIKE $__q
          OR COALESCE(u.openai_thread_id, '') ILIKE $__q
          OR COALESCE(u.agent_session_id, '') ILIKE $__q
          OR COALESCE(u.feedback_text, '') ILIKE $__q
          OR COALESCE(u.prayer_last_text, '') ILIKE $__q
        )"""
    return clause, [like]


async def _create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=dsn,
        min_size=1,
        max_size=3,
        command_timeout=30,
    )


def dsn_from_env_file(path: str) -> str:
    from dotenv import dotenv_values

    vals = dotenv_values(path)
    host = vals.get("DB_HOST") or "127.0.0.1"
    port = vals.get("DB_PORT") or "5432"
    name = vals.get("DB_NAME") or ""
    user = vals.get("DB_USER") or ""
    password = vals.get("DB_PASSWORD") or ""
    if not name or not user:
        raise SystemExit(f"DB_* не заданы в {path}")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


def create_app(
    *,
    club_dsn: Optional[str] = None,
    biblia_dsn: Optional[str] = None,
) -> FastAPI:
    state: Dict[str, Any] = {
        "pools": {},
        "dsns": {
            "club": club_dsn,
            "biblia": biblia_dsn,
        },
    }

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        pools: Dict[str, asyncpg.Pool] = {}
        for key, dsn in state["dsns"].items():
            if not dsn:
                logger.warning("dm_viewer: skip bot=%s (no dsn)", key)
                continue
            pools[key] = await _create_pool(dsn)
            logger.info("dm_viewer pool ready bot=%s", key)
        state["pools"] = pools
        try:
            yield
        finally:
            for key, pool in list(pools.items()):
                await pool.close()
                logger.info("dm_viewer pool closed bot=%s", key)

    app = FastAPI(
        title="DM viewer",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    def pool_for(bot: BotKey) -> asyncpg.Pool:
        p = state["pools"].get(bot)
        if p is None:
            raise HTTPException(503, f"DB pool not ready for bot={bot}")
        return p

    @app.get("/api/health")
    async def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "bots": sorted(state["pools"].keys()),
        }

    @app.get("/api/bots")
    async def list_bots() -> Dict[str, Any]:
        return {
            "bots": [
                {"id": "club", "label": "Клуб", "available": "club" in state["pools"]},
                {
                    "id": "biblia",
                    "label": "БиблияБот",
                    "available": "biblia" in state["pools"],
                },
            ]
        }

    @app.get("/api/users", response_model=UsersResponse)
    async def list_users(
        bot: BotKey = Query("club"),
        q: str = Query("", max_length=200),
        tag: FilterTag = Query("all"),
        sort: SortKey = Query("last_activity"),
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
    ) -> UsersResponse:
        # Полный GROUP BY по messages только если сортируем по last_dm
        need_dm_join = sort == "last_dm"
        search_sql, search_params = _search_clause(q, bot)
        params: List[Any] = list(search_params)
        if search_params:
            search_sql = search_sql.replace("$__q", f"${len(params)}")
        filter_sql = _filter_sql(tag, bot)
        order_sql = _sort_sql(sort, bot, need_dm=need_dm_join)
        from_sql = _base_from_sql(bot, need_dm=need_dm_join)
        select_sql = _select_cols(bot, with_dm_join=need_dm_join)

        # has_dm: точный COUNT(*)/DISTINCT по 20k+ дорогой — отдаём нижнюю оценку
        approx_has_dm = tag == "has_dm"
        list_sql = f"""
        WITH page AS (
          SELECT
            inner_page.*,
            ROW_NUMBER() OVER () AS _ord
          FROM (
            {select_sql}
            {from_sql}
            WHERE ({search_sql}) AND ({filter_sql})
            ORDER BY {order_sql}
            LIMIT ${len(params) + 1} OFFSET ${len(params) + 2}
          ) inner_page
        )
        SELECT
          page.user_id,
          page.username,
          page.first_name,
          page.last_name,
          page.name,
          page.phone,
          page.email,
          page.is_banned,
          page.is_active,
          page.bot_blocked_at,
          page.created_at,
          page.last_activity,
          page.first_touch_key,
          page.lic_user_id,
          page.lic_status,
          page.lic_expires_at,
          page.lic_origin,
          page.license_status_sql,
          page.onboarding_stage,
          page.is_greeter,
          page.is_donor,
          page.mailing_consent,
          COALESCE(dm.last_dm_at, page.last_dm_at) AS last_dm_at,
          COALESCE(dm.dm_count, page.dm_count, 0)::int AS dm_count
        FROM page
        LEFT JOIN LATERAL (
          SELECT
            MAX(m.created_at) AS last_dm_at,
            COUNT(*)::int AS dm_count
          FROM messages m
          WHERE m.user_id = page.user_id
            AND COALESCE(m.chat_type, 'private') = 'private'
        ) dm ON true
        ORDER BY page._ord
        """
        # +1 чтобы понять, есть ли ещё страница
        list_params = params + [limit + 1, offset]

        async with pool_for(bot).acquire() as conn:
            rows = await conn.fetch(list_sql, *list_params)
            has_more_page = len(rows) > limit
            rows = rows[:limit]
            if approx_has_dm:
                total = offset + len(rows) + (1 if has_more_page else 0)
            else:
                count_sql = f"""
                SELECT COUNT(*)::int
                {from_sql}
                WHERE ({search_sql}) AND ({filter_sql})
                """
                total = await conn.fetchval(count_sql, *params)

        return UsersResponse(
            total=int(total or 0),
            offset=offset,
            limit=limit,
            users=[_row_to_user(r, bot) for r in rows],
        )

    @app.get("/api/users/{user_id}", response_model=UserRow)
    async def get_user(
        user_id: int,
        bot: BotKey = Query("club"),
    ) -> UserRow:
        sql = f"""
        WITH one AS (
          {_select_cols(bot, with_dm_join=False)}
          {_base_from_sql(bot, need_dm=False)}
          WHERE u.user_id = $1
        )
        SELECT
          one.user_id,
          one.username,
          one.first_name,
          one.last_name,
          one.name,
          one.phone,
          one.email,
          one.is_banned,
          one.is_active,
          one.bot_blocked_at,
          one.created_at,
          one.last_activity,
          one.first_touch_key,
          one.lic_user_id,
          one.lic_status,
          one.lic_expires_at,
          one.lic_origin,
          one.license_status_sql,
          one.onboarding_stage,
          one.is_greeter,
          one.is_donor,
          one.mailing_consent,
          dm.last_dm_at,
          COALESCE(dm.dm_count, 0)::int AS dm_count
        FROM one
        LEFT JOIN LATERAL (
          SELECT
            MAX(m.created_at) AS last_dm_at,
            COUNT(*)::int AS dm_count
          FROM messages m
          WHERE m.user_id = one.user_id
            AND COALESCE(m.chat_type, 'private') = 'private'
        ) dm ON true
        """
        async with pool_for(bot).acquire() as conn:
            row = await conn.fetchrow(sql, user_id)
        if not row:
            raise HTTPException(404, "user not found")
        return _row_to_user(row, bot)

    @app.get("/api/users/{user_id}/messages", response_model=MessagesResponse)
    async def get_messages(
        user_id: int,
        bot: BotKey = Query("club"),
        before_id: Optional[int] = Query(None, ge=1),
        limit: int = Query(20, ge=1, le=100),
    ) -> MessagesResponse:
        if before_id is None:
            sql = """
            SELECT id, created_at, sender_type, role, message_type,
                   content, telegram_message_id, raw_data
            FROM messages
            WHERE user_id = $1 AND COALESCE(chat_type, 'private') = 'private'
            ORDER BY created_at DESC, id DESC
            LIMIT $2
            """
            args: List[Any] = [user_id, limit + 1]
        else:
            sql = """
            SELECT id, created_at, sender_type, role, message_type,
                   content, telegram_message_id, raw_data
            FROM messages
            WHERE user_id = $1 AND COALESCE(chat_type, 'private') = 'private'
              AND id < $2
            ORDER BY created_at DESC, id DESC
            LIMIT $3
            """
            args = [user_id, before_id, limit + 1]

        async with pool_for(bot).acquire() as conn:
            rows = await conn.fetch(sql, *args)

        has_more = len(rows) > limit
        rows = rows[:limit]
        rows = list(reversed(rows))

        messages: List[MessageRow] = []
        for r in rows:
            sender: Literal["user", "bot"] = "user"
            st = (r.get("sender_type") or "").lower()
            role = (r.get("role") or "").lower()
            if st == "bot" or role in ("assistant", "bot", "system"):
                sender = "bot"
            content = display_message_content(r.get("content") or "", r.get("raw_data"))
            mtype = r.get("message_type") or "text"
            if not content.strip() and mtype and mtype != "text":
                content = f"[{mtype}]"
            messages.append(
                MessageRow(
                    id=int(r["id"]),
                    created_at=r["created_at"],
                    sender=sender,
                    message_type=mtype,
                    content=content,
                    telegram_message_id=r.get("telegram_message_id"),
                )
            )

        next_before_id = messages[0].id if (messages and has_more) else None

        return MessagesResponse(
            user_id=user_id,
            messages=messages,
            has_more=has_more,
            next_before_id=next_before_id,
        )

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app
