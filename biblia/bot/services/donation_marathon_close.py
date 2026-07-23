"""Стороны эффекта при закрытии марафона: статистика в топик оплат + черновик благодарности."""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, TYPE_CHECKING

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.donation_marathon_progress import format_money
from bot.utils.admin_channel import send_admin_html_message
from bot.utils.mailing_llm_html_async import (
    STRICT_HTML_TAIL_FOR_PROMPT,
    ensure_llm_text_telegram_html,
)
from config import config
from storage.mailing_storage import CAMPAIGN_SOURCE_MANUAL, MailingStorage

if TYPE_CHECKING:
    from openai_client.agents_client import AgentsClient

logger = logging.getLogger(__name__)

_CB_THANKS_OK = "marathon_thanks_ok_"
_CB_THANKS_NO = "marathon_thanks_no_"
_PENDING_SCHEDULE_DAYS = 365


def thanks_campaign_name(marathon_id: int) -> str:
    return f"Марафон #{marathon_id}: благодарность"


def _payment_thread_id() -> Optional[int]:
    tid = getattr(config, "PAYMENT_THREAD_ID", None) or 0
    return tid if tid > 0 else None


def _fmt_duration(start: Any, end: Any) -> str:
    if not start or not end:
        return "—"
    try:
        delta = end - start
        total_sec = max(0, int(delta.total_seconds()))
    except Exception:
        return "—"
    days, rem = divmod(total_sec, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    parts = []
    if days:
        parts.append(f"{days} дн.")
    if hours:
        parts.append(f"{hours} ч.")
    if mins or not parts:
        parts.append(f"{mins} мин.")
    return " ".join(parts)


def format_marathon_close_stats_html(
    marathon: Dict[str, Any],
    stats: Dict[str, Any],
    *,
    close_reason: str,
) -> str:
    name = html.escape(str(marathon.get("name") or "Марафон"))
    mid = int(marathon.get("id") or 0)
    cur = str(marathon.get("goal_currency") or "USD").upper()
    goal = float(marathon.get("goal_amount") or 0)
    raised = float(stats.get("raised_amount") or 0)
    donors = int(stats.get("donors_count") or 0)
    contribs = int(stats.get("contributions_count") or 0)
    avg_amt = float(stats.get("avg_amount") or 0)
    max_amt = float(stats.get("max_amount") or 0)

    if close_reason == "goal_reached":
        title = f"🎉 Марафон <b>«{name}»</b> завершён по цели"
    elif close_reason == "forced":
        title = f"⏹ Марафон <b>«{name}»</b> остановлен админом"
    else:
        title = f"📊 Марафон <b>«{name}»</b> завершён"

    started = marathon.get("started_at") or marathon.get("created_at")
    closed = marathon.get("closed_at")
    duration = _fmt_duration(started, closed)

    lines = [
        title,
        f"<code>#{mid}</code>",
        "",
        f"• Собрано: <b>{html.escape(format_money(raised, cur))}</b> "
        f"из {html.escape(format_money(goal, cur))}",
        f"• Длительность: <b>{html.escape(duration)}</b>",
        f"• Участников: <b>{donors}</b>",
        f"• Взносов: <b>{contribs}</b>",
    ]
    if contribs > 0:
        lines.append(f"• Средний взнос: <b>{html.escape(format_money(avg_amt, cur))}</b>")
        lines.append(f"• Макс. взнос: <b>{html.escape(format_money(max_amt, cur))}</b>")
    return "\n".join(lines)


_THANKS_SYSTEM = (
    "Ты пишешь короткое благодарственное сообщение участникам марафона сбора средств "
    "в Telegram-боте о Библии. Тон тёплый, спокойный, без пафоса и без маркетинга. "
    "Опирайся на описание марафона: благодарность должна быть релевантна конкретной цели "
    "(что собирали и зачем), а не общей. Не копируй описание целиком и не повторяй "
    "призывы «пожертвовать ещё». "
    "Ответ только Telegram HTML: обязательно используй хотя бы один тег из "
    "<b>, <i>, <blockquote>. Без Markdown, без эмодзи-спама (допустимы 1–2 уместных эмодзи)."
)

_THANKS_USER_TMPL = (
    "Марафон «{name}» успешно завершён (цель достигнута).\n\n"
    "Описание марафона (контекст цели — опирайся на него, не копируй дословно):\n"
    "-----\n{description}\n-----\n\n"
    "Напиши участникам: поблагодари за участие и взносы; коротко напомни, ради чего "
    "шёл марафон (по описанию); скажи, что цель достигнута и будет доставлена / "
    "запущена в ближайшее время, о готовности сообщим отдельно. "
    "Без обращения по имени. 2–5 коротких абзацев, не длиннее ~900 символов."
)

_THANKS_FALLBACK = (
    "🙏 <b>Спасибо за участие в марафоне!</b>\n\n"
    "Благодаря вам цель достигнута — марафон успешно завершён.\n\n"
    "Цель будет доставлена в ближайшее время; мы сообщим вам, когда это произойдёт."
)


def _marathon_description_for_prompt(marathon: Dict[str, Any]) -> str:
    raw = (marathon.get("description_html") or marathon.get("description") or "").strip()
    if not raw:
        return "(описание не задано — поблагодари за участие в целом)"
    # Не раздуваем промпт: хватает начала описания для релевантности.
    if len(raw) > 2500:
        raw = raw[:2500] + "…"
    return raw


async def _generate_thanks_html(
    *,
    agents_client: "AgentsClient",
    marathon_name: str,
    description: str,
    admin_user_id: int,
) -> str:
    base_user = _THANKS_USER_TMPL.format(
        name=marathon_name,
        description=description or "(описание не задано)",
    )

    async def fetch_raw(strict: bool) -> Optional[str]:
        user_content = base_user + (STRICT_HTML_TAIL_FOR_PROMPT if strict else "")
        return await agents_client.complete(
            system_prompt=_THANKS_SYSTEM,
            user_content=user_content,
            user_id=admin_user_id,
            request_kind="marathon_thanks_mailing",
            temperature=0.7,
            max_tokens=900,
        )

    text = await ensure_llm_text_telegram_html(
        fetch_raw,
        agents_client=agents_client,
        log_context=f"marathon_thanks:{marathon_name}",
    )
    return (text or "").strip() or _THANKS_FALLBACK


def _thanks_confirm_kb(campaign_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Запустить рассылку",
                    callback_data=f"{_CB_THANKS_OK}{campaign_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=f"{_CB_THANKS_NO}{campaign_id}",
                ),
            ]
        ]
    )


async def _find_campaign_id_by_name(mstore: MailingStorage, name: str) -> Optional[int]:
    try:
        async with mstore.db.get_connection() as conn:
            return await conn.fetchval(
                """
                SELECT id FROM mailing_campaigns
                 WHERE name = $1 AND status = 'planned'
                 ORDER BY id DESC LIMIT 1
                """,
                name,
            )
    except Exception as e:
        logger.error("❌ find campaign by name: %s", e)
        return None


async def _create_thanks_draft_and_preview(
    bot,
    user_storage,
    marathon: Dict[str, Any],
    *,
    agents_client: "AgentsClient",
    force: bool = False,
) -> None:
    mid = int(marathon["id"])
    name = str(marathon.get("name") or "Марафон")
    camp_name = thanks_campaign_name(mid)
    mstore = MailingStorage(user_storage.db)

    existing = await _find_campaign_id_by_name(mstore, camp_name)
    if existing:
        if not force:
            logger.info(
                "🎙️ Marathon %s thanks campaign already exists id=%s — skip",
                mid,
                existing,
            )
            return
        await cancel_thanks_campaign(mstore, int(existing))
        logger.info(
            "🎙️ Marathon %s: cancelled old thanks campaign id=%s (force)",
            mid,
            existing,
        )

    uids = await user_storage.list_marathon_participant_user_ids([mid])
    if not uids:
        logger.warning("🎙️ Marathon %s: no participants for thanks mailing", mid)
        await send_admin_html_message(
            bot,
            f"ℹ️ Марафон <b>«{html.escape(name)}»</b>: нет участников для благодарственной рассылки.",
            thread_id=_payment_thread_id(),
        )
        return

    super_id = int(getattr(config, "SUPER_ADMIN_ID", 0) or 0)
    if super_id <= 0:
        logger.error("SUPER_ADMIN_ID не задан — нельзя отправить превью благодарности")
        await send_admin_html_message(
            bot,
            "⚠️ Марафон завершён, но <code>SUPER_ADMIN_ID</code> не задан в .env — "
            "благодарственную рассылку не создал.",
            thread_id=_payment_thread_id(),
        )
        return

    body = await _generate_thanks_html(
        agents_client=agents_client,
        marathon_name=name,
        description=_marathon_description_for_prompt(marathon),
        admin_user_id=super_id,
    )
    scheduled_at = datetime.now(timezone.utc) + timedelta(days=_PENDING_SCHEDULE_DAYS)
    campaign_id = await mstore.create_campaign(
        {
            "name": camp_name,
            "text": body,
            "parse_mode": "HTML",
            "scheduled_at": scheduled_at,
            "has_ref_link": False,
            "buttons": [],
            "created_by": super_id,
            "media_type": None,
            "media_file_id": None,
            "attachments": None,
            "campaign_source": CAMPAIGN_SOURCE_MANUAL,
        }
    )
    if not campaign_id:
        logger.error("🎙️ Failed to create thanks campaign for marathon %s", mid)
        return

    added = await mstore.add_audience_batch(int(campaign_id), uids)
    from bot.texts import ru_admin_mailing as aml_txt

    preview = aml_txt.confirm_blob_html(
        name=camp_name,
        text=body,
        when="после вашего подтверждения",
        parse_mode="HTML",
        has_ref_link=False,
        attachments=None,
        buttons=None,
        segment="marathon",
        recipient_hint=added,
        aud_marathon_ids=[mid],
    )
    header = (
        f"🎙️ <b>Авто-рассылка после марафона #{mid}</b>\n"
        f"Черновик ждёт вашего подтверждения "
        f"(запуск отложен на {_PENDING_SCHEDULE_DAYS} дн., пока не нажмёте «Создать»).\n\n"
    )
    try:
        await bot.send_message(
            super_id,
            header + preview,
            parse_mode="HTML",
            reply_markup=_thanks_confirm_kb(int(campaign_id)),
        )
    except Exception as e:
        logger.error(
            "❌ Не удалось отправить превью благодарности SUPER_ADMIN_ID=%s: %s",
            super_id,
            e,
        )
        await send_admin_html_message(
            bot,
            f"⚠️ Черновик благодарности <code>{campaign_id}</code> создан, "
            f"но DM суперадмину не ушёл: {html.escape(str(e)[:200])}",
            thread_id=_payment_thread_id(),
        )


async def handle_marathon_closed(
    bot,
    user_storage,
    marathon_id: int,
    *,
    agents_client: Optional["AgentsClient"] = None,
    create_thanks: Optional[bool] = None,
    skip_stats: bool = False,
    force_thanks: bool = False,
) -> None:
    """
    После закрытия марафона в БД: статистика в топик оплат;
    при ``goal_reached`` — черновик благодарственной рассылки + превью суперадмину.
    """
    if not bot:
        return
    marathon = await user_storage.get_donation_marathon(marathon_id)
    if not marathon:
        return
    close_reason = str(marathon.get("close_reason") or "")
    if not skip_stats:
        stats = await user_storage.get_marathon_stats(marathon_id)
        text = format_marathon_close_stats_html(
            marathon, stats, close_reason=close_reason
        )
        try:
            await send_admin_html_message(bot, text, thread_id=_payment_thread_id())
        except Exception as e:
            logger.error("❌ marathon close stats notify: %s", e)

    do_thanks = create_thanks
    if do_thanks is None:
        do_thanks = close_reason == "goal_reached" and str(
            marathon.get("status") or ""
        ) == "completed"
    if not do_thanks:
        return

    client = agents_client
    if client is None:
        try:
            from openai_client.agents_client import AgentsClient

            client = AgentsClient(user_storage)
        except Exception as e:
            logger.error("❌ AgentsClient for marathon thanks: %s", e)
            return
    try:
        await _create_thanks_draft_and_preview(
            bot,
            user_storage,
            marathon,
            agents_client=client,
            force=force_thanks,
        )
    except Exception as e:
        logger.error("❌ marathon thanks mailing: %s", e, exc_info=True)


async def approve_thanks_campaign(mstore: MailingStorage, campaign_id: int) -> bool:
    """Переводит черновик в ближайший запуск (scheduled_at = now)."""
    now = datetime.now(timezone.utc)
    return await mstore.update_campaign_scheduled_at(campaign_id, now)


async def cancel_thanks_campaign(mstore: MailingStorage, campaign_id: int) -> bool:
    return await mstore.update_campaign_status(campaign_id, "cancelled")
