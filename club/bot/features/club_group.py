# bot/features/club_group.py
import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from aiogram import Dispatcher, F
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.features.base import BaseFeature
from bot.texts import ru_club_group as club_txt
from bot.logging.message_copier import MessageCopier
from bot.logging.club_join_debug import (
    chat_member_status,
    is_club_member_join_transition,
    log_event,
)
from bot.services.club_removal_card import (
    REASON_NIGHTLY_AUDIT,
    build_club_removal_card_html,
)
from bot.utils.admin_channel import send_admin_html_message
from bot.utils.club_welcome import send_club_member_welcome
from bot.utils.user_ui import render_user_screen, with_main_menu
from config import config
from storage.user_storage import UserStorage

logger = logging.getLogger(__name__)

# Re-export для subscription_info и register_handlers
SUBS_CLUB_CALLBACK_DATA = club_txt.SUBS_CLUB_CALLBACK_DATA

_CLUB_AUDIT_NOTIFY_MAX = 3600


def _bullet_for_removed(profile: Optional[Dict[str, Any]], user_id: int) -> str:
    if not profile:
        return club_txt.audit_bullet_no_user_row(user_id)
    un = profile.get("username")
    username = f" @{html.escape(un)}" if un else ""
    fn = html.escape(profile.get("first_name") or "")
    ln = html.escape(profile.get("last_name") or "")
    name = (fn + (" " + ln if ln else "")).strip()
    name_part = f" — {name}" if name else ""
    return club_txt.audit_bullet_user(
        user_id=user_id, username=username, name_part=name_part
    )


def _telegram_cache_prune_note_plain(cm: Any) -> str:
    """Краткое пояснение для админ‑отчёта (plaintext, затем экранируется в HTML)."""
    st = getattr(cm, "status", None)
    if st == ChatMemberStatus.LEFT:
        return club_txt.telegram_cache_prune_note_left()
    if st == ChatMemberStatus.KICKED:
        return club_txt.telegram_cache_prune_note_kicked()
    if st == ChatMemberStatus.RESTRICTED:
        return club_txt.telegram_cache_prune_note_restricted()
    val = getattr(st, "value", None)
    tail = val if val is not None else repr(st)
    return club_txt.telegram_cache_prune_note_status(tail)


def _pack_audit_batches(first_intro: str, cont_intro: str, bullets: List[str]) -> List[str]:
    """Разбивает длинный отчёт на части под лимит Telegram."""
    batches: List[str] = []
    header = first_intro
    i = 0
    mx = _CLUB_AUDIT_NOTIFY_MAX

    while i < len(bullets):
        chunk_lines: List[str] = []
        while i < len(bullets):
            line = bullets[i]
            body = "\n".join(chunk_lines + [line]) if chunk_lines else line
            trial = header + body
            if len(trial) <= mx:
                chunk_lines.append(line)
                i += 1
                continue
            if chunk_lines:
                break
            logger.warning(
                "Строка отчёта длиннее лимита TG (%s симв.), условное усечение",
                mx,
            )
            chunk_lines.append(line[: max(200, mx - len(header) - 40)] + "…")
            i += 1
            break
        batches.append(header + ("\n".join(chunk_lines)))
        header = cont_intro

    return batches


def _still_in_supergroup_cm(cm) -> bool:
    if cm.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        return False
    if cm.status == ChatMemberStatus.RESTRICTED:
        return bool(getattr(cm, "is_member", False))
    return cm.status in (
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    )


def _seconds_until_next_audit_hour_utc(hour_utc: int) -> float:
    hour_utc %= 24
    now = datetime.now(timezone.utc)
    target = now.replace(hour=hour_utc, minute=5, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    # Не крутиться в tight loop при сбое планировщика
    return max(60.0, (target - now).total_seconds())


class ClubGroupFeature(BaseFeature):
    """
    Доступ к закрытому чату клуба: инвайты, ссылка на пост, ночной аудит членов.

    Исключение из чата без активной лицензии — только если истечение (MAX(expires_at))
    старше окна отсрочки ``CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS`` (по умолчанию 3 дня).
    """

    name = "club_group"

    def __init__(
        self,
        user_storage: UserStorage,
        bot,
        message_copier: Optional[MessageCopier] = None,
    ):
        super().__init__()
        self.user_storage = user_storage
        self.bot = bot
        self.message_copier = message_copier
        self._audit_task: Optional[asyncio.Task] = None
        self._api_pause_sec = 0.04

    async def initialize(self) -> None:
        logger.info(f"[{self.name}] Фича инициализирована")

        if config.CLUB_GROUP_ID == 0:
            logger.warning(f"[{self.name}] CLUB_GROUP_ID не настроен в .env")

    async def teardown(self) -> None:
        await self.stop_background_tasks()
        logger.info(f"[{self.name}] Фича остановлена")

    async def start_background_tasks(self) -> None:
        if not config.CLUB_GROUP_NIGHTLY_AUDIT_ENABLED:
            logger.info(
                "[%s] Ночной аудит выключен (CLUB_GROUP_NIGHTLY_AUDIT_ENABLED)",
                self.name,
            )
            return
        if config.CLUB_GROUP_ID == 0:
            return
        if self._audit_task and not self._audit_task.done():
            return
        self._audit_task = asyncio.create_task(
            self._nightly_auditor(), name="club_group_nightly"
        )
        logger.info(f"[{self.name}] Ночной аудит группы запланирован (UTC {config.CLUB_GROUP_AUDIT_HOUR_UTC}:05)")

    async def stop_background_tasks(self) -> None:
        if self._audit_task:
            self._audit_task.cancel()
            try:
                await self._audit_task
            except asyncio.CancelledError:
                pass
            self._audit_task = None

    def register_handlers(self, dp: Dispatcher) -> None:
        if config.CLUB_GROUP_ID:
            dp.chat_member.register(self._on_chat_member_updated, F.chat.id == config.CLUB_GROUP_ID)
            dp.message.register(
                self._on_club_group_member_activity,
                F.chat.id == config.CLUB_GROUP_ID,
                F.from_user,
            )
        dp.message.register(self.cmd_club, Command("club"))
        dp.callback_query.register(
            self._cb_club_from_subs_screen,
            F.data == SUBS_CLUB_CALLBACK_DATA,
        )

    async def _cb_club_from_subs_screen(self, callback: CallbackQuery) -> None:
        await self.present_club_access(
            callback.from_user.id, callback.message, edit=True
        )
        await callback.answer()

    async def _on_club_group_member_activity(self, message: Message) -> None:
        """Трекинг активности участников в группе для member-агента."""
        user = message.from_user
        if not user or user.is_bot:
            return
        if message.new_chat_members or message.left_chat_member:
            return
        asyncio.create_task(
            self._touch_group_activity_safe(user.id),
            name=f"club_group_activity_{user.id}",
        )

    async def _touch_group_activity_safe(self, user_id: int) -> None:
        try:
            from bot.services.member_profile_service import maybe_touch_member_group_activity

            await maybe_touch_member_group_activity(self.user_storage, user_id)
        except Exception as e:
            logger.debug("[%s] group activity uid=%s: %s", self.name, user_id, e)

    async def _on_chat_member_updated(self, event: ChatMemberUpdated) -> None:
        if config.CLUB_GROUP_ID == 0 or event.chat.id != config.CLUB_GROUP_ID:
            return
        new = event.new_chat_member
        user = new.user
        old_st = chat_member_status(event.old_chat_member)
        new_st = chat_member_status(new)

        if user.is_bot:
            log_event(
                "chat_member",
                chat_id=event.chat.id,
                user_id=user.id,
                old_status=old_st,
                new_status=new_st,
                cache_action="skip_bot",
            )
            return

        uid = user.id
        status = new.status

        left_or_kicked = status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED)
        restricted_out = status == ChatMemberStatus.RESTRICTED and not getattr(
            new, "is_member", False
        )

        if left_or_kicked or restricted_out:
            await self.user_storage.delete_club_member_cache(uid)
            log_event(
                "chat_member",
                chat_id=event.chat.id,
                user_id=uid,
                old_status=old_st,
                new_status=new_st,
                cache_action="delete",
            )
            return

        keep = status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        ) or (status == ChatMemberStatus.RESTRICTED and getattr(new, "is_member", False))

        cache_action = "noop"
        if keep:
            await self.user_storage.upsert_club_member_cache(uid)
            cache_action = "upsert"
            if is_club_member_join_transition(event.old_chat_member, new):
                forum = bool(getattr(event.chat, "is_forum", False))
                tid = int(getattr(config, "WELCOME_TOPIC_ID", 0) or 0)
                try:
                    await send_club_member_welcome(
                        self.bot,
                        event.chat.id,
                        user,
                        welcome_topic_id=tid,
                        is_forum=forum,
                        telegram_message_id_in=None,
                    )
                except Exception as exc:
                    logger.error(
                        "[%s] приветствие по chat_member uid=%s: %s",
                        self.name,
                        uid,
                        exc,
                        exc_info=True,
                    )
                    log_event(
                        "welcome_error",
                        chat_id=event.chat.id,
                        user_id=uid,
                        error=str(exc),
                    )
        log_event(
            "chat_member",
            chat_id=event.chat.id,
            user_id=uid,
            old_status=old_st,
            new_status=new_st,
            cache_action=cache_action,
        )

    async def cmd_club(self, message: Message) -> None:
        """Публичная команда: пост в группе или одноразовый инвайт по логике клуба."""
        await self.present_club_access(message.from_user.id, message, edit=False)

    async def present_club_access(
        self, user_id: int, anchor_message: Message, *, edit: bool = False
    ) -> None:
        """Текст + кнопка со ссылкой в клуб (инвайт — отдельным сообщением)."""
        kind = await self.resolve_club_access_kind(user_id)

        if kind == "no_club":
            await render_user_screen(
                anchor_message,
                text=club_txt.CLUB_NOT_CONFIGURED_HTML,
                edit=edit,
            )
            return
        if kind == "no_license":
            await render_user_screen(
                anchor_message,
                text=club_txt.CLUB_NO_LICENSE_HTML,
                edit=edit,
            )
            return
        if kind == "unconfigured":
            await render_user_screen(
                anchor_message,
                text=club_txt.CLUB_LINK_UNCONFIGURED_HTML,
                edit=edit,
            )
            return
        if kind == "error":
            await render_user_screen(
                anchor_message,
                text=club_txt.CLUB_LINK_ERROR_HTML,
                edit=edit,
            )
            return

        if kind == "post":
            group_link = (config.CLUB_POST_LINK or "").strip()
            if not group_link:
                await render_user_screen(
                    anchor_message,
                    text=club_txt.CLUB_LINK_UNCONFIGURED_HTML,
                    edit=edit,
                )
                return
            btn = club_txt.BTN_OPEN_CLUB
            lead = club_txt.CLUB_ALREADY_IN_LEAD_HTML
            kb = with_main_menu(
                [[InlineKeyboardButton(text=btn, url=group_link)]]
            )
            await render_user_screen(
                anchor_message,
                text=f"{lead}{club_txt.CLUB_ACCESS_FOOTER}",
                reply_markup=kb,
                edit=edit,
                add_main_menu=False,
            )
            return

        sent = await self.send_fresh_club_invite(user_id)
        if not sent:
            await render_user_screen(
                anchor_message,
                text=club_txt.CLUB_LINK_ERROR_HTML,
                edit=edit,
            )
            return
        await render_user_screen(
            anchor_message,
            text=club_txt.CLUB_FRESH_INVITE_ACK_HTML,
            edit=edit,
            add_main_menu=True,
        )

    async def user_needs_club_invite(self, user_id: int) -> bool:
        """Есть лицензия, но человек ещё не в закрытой группе."""
        if config.CLUB_GROUP_ID == 0:
            return False
        if not await self.user_storage.get_user_active_license(user_id):
            return False
        return not await self._still_in_supergroup_membership(user_id)

    async def _nightly_auditor(self) -> None:
        while True:
            try:
                delay = _seconds_until_next_audit_hour_utc(config.CLUB_GROUP_AUDIT_HOUR_UTC)
                await asyncio.sleep(delay)
                await self._run_nightly_maintenance()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("[%s] Nightly auditor error: %s", self.name, e)
                await asyncio.sleep(300)

    async def _post_html_admin_support_topic(self, text: str) -> bool:
        """Одно сообщение в канал админки; при заданном SUPPORT_THREAD_ID — в топик тикетов."""
        if not config.ADMIN_CHANNEL_ID:
            logger.warning(
                "[%s] ADMIN_CHANNEL_ID не задан — отчёт об исключениях не отправлен",
                self.name,
            )
            return False

        thread_kw = (
            config.SUPPORT_THREAD_ID if config.SUPPORT_THREAD_ID and config.SUPPORT_THREAD_ID > 0 else None
        )
        if thread_kw is None:
            logger.warning(
                "[%s] SUPPORT_THREAD_ID=0 — отчёт уйдёт в корень ADMIN_CHANNEL без топика ТП",
                self.name,
            )

        ok = await send_admin_html_message(self.bot, text, thread_id=thread_kw)
        if not ok:
            logger.error("[%s] sendMessage admin topic failed", self.name)
        return ok

    async def _send_audit_message_batches(self, batches: List[str], log_kind: str) -> None:
        total = len(batches)
        for j, body in enumerate(batches):
            if total > 1:
                body = club_txt.AUDIT_BATCH_PART_PREFIX.format(
                    part=j + 1, total=total
                ) + body
            ok = await self._post_html_admin_support_topic(body)
            if ok:
                logger.info(
                    "[%s] Отчёт «%s» отправлен в админский канал (%s/%s)",
                    self.name,
                    log_kind,
                    j + 1,
                    total,
                )
            await asyncio.sleep(0.3)

    async def _notify_support_topic_club_removals(
        self,
        removed: List[Tuple[int, Optional[Dict[str, Any]]]],
    ) -> None:
        """Карточка на каждого исключённого + краткая сводка в топик поддержки."""
        if not removed:
            return

        ts = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        intro = club_txt.AUDIT_REMOVAL_INTRO_TEMPLATE.format(
            ts=html.escape(ts),
            club_group_id=config.CLUB_GROUP_ID,
            count=len(removed),
        )
        await self._post_html_admin_support_topic(intro)

        for uid, prof in removed:
            try:
                card = await build_club_removal_card_html(
                    self.user_storage,
                    uid,
                    reason=REASON_NIGHTLY_AUDIT,
                )
                await self._post_html_admin_support_topic(card)
            except Exception as e:
                logger.error(
                    "[%s] removal card uid=%s: %s", self.name, uid, e, exc_info=True
                )
                fallback = club_txt.AUDIT_REMOVAL_CARD_FALLBACK_TEMPLATE.format(
                    bullet=_bullet_for_removed(prof, uid)
                )
                await self._post_html_admin_support_topic(
                    fallback
                )
            await asyncio.sleep(0.35)

    async def _notify_support_topic_club_cache_pruned_only(
        self,
        pruned: List[Tuple[int, Optional[Dict[str, Any]], str]],
    ) -> None:
        """Уведомление: запись удалена только из БД‑кэша, kick бота не было."""
        if not pruned:
            return

        bullets = []
        for uid, prof, note in pruned:
            base = _bullet_for_removed(prof, uid)
            bullets.append(f"{base} <i>({html.escape(note)})</i>")

        ts = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        summary = club_txt.AUDIT_CACHE_PRUNE_SUMMARY

        first_intro = club_txt.AUDIT_CACHE_FIRST_INTRO_TEMPLATE.format(
            ts=html.escape(ts),
            club_group_id=config.CLUB_GROUP_ID,
            count=len(pruned),
            summary=summary,
        )

        cont_intro = club_txt.AUDIT_CACHE_CONT_INTRO_TEMPLATE.format(
            ts=html.escape(ts),
        )

        batches = _pack_audit_batches(first_intro, cont_intro, bullets)
        await self._send_audit_message_batches(batches, "очистка кэша участников")

    async def _run_nightly_maintenance(self) -> None:
        if config.CLUB_GROUP_ID == 0:
            return
        logger.info("[%s] Регламент клуба (отзывы + аудит членов)", self.name)
        stale_fixed = await self.user_storage.expire_stale_active_licenses(
            grace_days=config.CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS,
        )
        if stale_fixed:
            logger.info(
                "[%s] Устаревшие active-лицензии переведены в expired: %s",
                self.name,
                stale_fixed,
            )
        await self.revoke_expired_links()
        await self._reload_member_cache_from_licensees()

        try:
            admins = await self.bot.get_chat_administrators(chat_id=config.CLUB_GROUP_ID)
        except TelegramBadRequest as e:
            if "chat not found" in (str(e) or "").lower():
                logger.warning(
                    "[%s] get_chat_administrators: chat not found (CLUB_GROUP_ID=%s). "
                    "Ночной аудит пропущен.",
                    self.name,
                    config.CLUB_GROUP_ID,
                )
            else:
                logger.error("[%s] get_chat_administrators: %s", self.name, e)
            return
        except Exception as e:
            logger.error("[%s] get_chat_administrators: %s", self.name, e)
            return

        admin_ids = {m.user.id for m in admins}
        cached = await self.user_storage.list_club_member_cache_user_ids()
        removed_audit: List[Tuple[int, Optional[Dict[str, Any]]]] = []
        cache_pruned_only: List[Tuple[int, Optional[Dict[str, Any]], str]] = []

        for uid in cached:
            if uid in admin_ids:
                continue

            await asyncio.sleep(self._api_pause_sec)
            try:
                cm = await self.bot.get_chat_member(chat_id=config.CLUB_GROUP_ID, user_id=uid)
            except Exception as e:
                logger.warning("[%s] audit get_chat_member uid=%s: %s", self.name, uid, e)
                continue

            if not _still_in_supergroup_cm(cm):
                await self.user_storage.delete_club_member_cache(uid)
                note = _telegram_cache_prune_note_plain(cm)
                profile = await self.user_storage.get_user(uid)
                cache_pruned_only.append((uid, profile, note))
                continue

            if cm.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR):
                continue

            grace = config.CLUB_GROUP_EXPIRED_LICENSE_GRACE_DAYS
            if not await self.user_storage.club_nightly_audit_should_remove_member(
                uid, grace_days=grace
            ):
                continue

            logger.info(
                "[%s] Удаление из клуба (нет действующей подписки или истекла > %s дн.) user_id=%s",
                self.name,
                grace,
                uid,
            )
            try:
                await self.bot.ban_chat_member(chat_id=config.CLUB_GROUP_ID, user_id=uid)
                await self.bot.unban_chat_member(chat_id=config.CLUB_GROUP_ID, user_id=uid)
            except Exception as e:
                logger.error("[%s] kick uid=%s: %s", self.name, uid, e)
                continue

            await self.user_storage.delete_club_member_cache(uid)
            profile = await self.user_storage.get_user(uid)
            removed_audit.append((uid, profile))
            await self.user_storage.record_club_member_exclusion(
                uid, reason="nightly_audit", source="club_group"
            )
            await self.user_storage.mark_license_expired(uid)

        if removed_audit:
            await self._notify_support_topic_club_removals(removed_audit)
            await asyncio.sleep(0.5)
        if cache_pruned_only:
            await self._notify_support_topic_club_cache_pruned_only(cache_pruned_only)

        logger.info("[%s] Регламент клуба завершён", self.name)

    async def _reload_member_cache_from_licensees(self) -> None:
        """Дополнить кэш: у кого есть лицензия — проверяем факт членства через API."""
        for uid in await self.user_storage.list_user_ids_with_active_license():
            await asyncio.sleep(self._api_pause_sec)
            try:
                cm = await self.bot.get_chat_member(chat_id=config.CLUB_GROUP_ID, user_id=uid)
            except Exception:
                continue
            if getattr(cm.user, "is_bot", False):
                continue
            if _still_in_supergroup_cm(cm):
                await self.user_storage.upsert_club_member_cache(uid)

    async def send_group_invite(self, user_id: int) -> bool:
        link = await self._create_fresh_invite_link(user_id)
        if not link:
            return False
        message_text = club_txt.payment_invite_html(
            inside_block=club_txt.club_inside_block(),
            invite_footer=club_txt.invite_link_footer(
                ttl_hours=config.CLUB_INVITE_TTL_HOURS
            ),
        )
        try:
            await self._send_invite_message(
                user_id,
                link,
                message_text=message_text,
                log_source="club_invite",
                log_subtype="payment",
            )
            logger.info(f"✅ Invite message sent to user {user_id}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to send invite message for user {user_id}: {e}")
            return False

    async def send_fresh_club_invite(self, user_id: int) -> bool:
        """Новая одноразовая ссылка: старые инвайты отзываются, сообщение — отдельным постом."""
        if not await self.user_needs_club_invite(user_id):
            return False
        link = await self._create_fresh_invite_link(user_id)
        if not link:
            return False
        message_text = club_txt.fresh_invite_html(
            inside_block=club_txt.club_inside_block(),
            invite_footer=club_txt.invite_link_footer(
                ttl_hours=config.CLUB_INVITE_TTL_HOURS
            ),
        )
        try:
            await self._send_invite_message(
                user_id,
                link,
                message_text=message_text,
                log_source="club_invite",
                log_subtype="fresh",
            )
            logger.info("[%s] fresh club invite sent uid=%s", self.name, user_id)
            return True
        except Exception as e:
            logger.error("[%s] fresh club invite failed uid=%s: %s", self.name, user_id, e)
            return False

    async def send_admin_gift_invite(self, user_id: int, *, expires_str: str) -> bool:
        """Одноразовый инвайт после админской выдачи /gift (клуб «Любящие Бога»)."""
        link = await self._create_fresh_invite_link(user_id)
        if not link:
            return False
        message_text = club_txt.admin_gift_invite_html(
            expires_str=expires_str,
            inside_block=club_txt.club_inside_block(),
            invite_footer=club_txt.invite_link_footer(
                ttl_hours=config.CLUB_INVITE_TTL_HOURS
            ),
        )
        try:
            await self._send_invite_message(
                user_id,
                link,
                message_text=message_text,
                log_source="admin_gift",
                log_subtype="club_invite",
            )
            logger.info(f"✅ Admin gift invite sent to user {user_id}")
            return True
        except Exception as e:
            logger.error(f"❌ Admin gift invite message failed for user {user_id}: {e}")
            return False

    async def _revoke_user_unused_invites(self, user_id: int) -> int:
        """Отозвать все неиспользованные инвайты пользователя перед выдачей новой ссылки."""
        if config.CLUB_GROUP_ID == 0:
            return 0
        rows = await self.user_storage.fetch_revokable_club_invites_for_user(user_id)
        revoked = 0
        for row in rows:
            invite_id = int(row["id"])
            link = row["invite_link"]
            try:
                await self.bot.revoke_chat_invite_link(
                    chat_id=config.CLUB_GROUP_ID,
                    invite_link=link,
                )
            except TelegramBadRequest as e:
                logger.debug(
                    "[%s] revoke invite id=%s uid=%s (already dead): %s",
                    self.name,
                    invite_id,
                    user_id,
                    e,
                )
            except Exception as e:
                logger.warning(
                    "[%s] revoke invite id=%s uid=%s: %s",
                    self.name,
                    invite_id,
                    user_id,
                    e,
                )
            try:
                await self.user_storage.mark_club_invite_revoked(invite_id)
                revoked += 1
            except Exception as e:
                logger.error(
                    "[%s] mark revoked invite id=%s uid=%s: %s",
                    self.name,
                    invite_id,
                    user_id,
                    e,
                )
        if revoked:
            logger.info(
                "[%s] revoked %s unused invite(s) for uid=%s",
                self.name,
                revoked,
                user_id,
            )
        return revoked

    async def _ensure_user_unbanned_for_invite(self, user_id: int) -> None:
        """Снять ban в группе, чтобы инвайт работал после kick (если unban при исключении не прошёл)."""
        if config.CLUB_GROUP_ID == 0:
            return
        try:
            await self.bot.unban_chat_member(
                chat_id=config.CLUB_GROUP_ID,
                user_id=user_id,
                only_if_banned=True,
            )
            logger.info("[%s] unban before club invite uid=%s", self.name, user_id)
        except TelegramBadRequest as e:
            logger.debug(
                "[%s] unban before invite uid=%s (bad request): %s",
                self.name,
                user_id,
                e,
            )
        except Exception as e:
            logger.warning(
                "[%s] unban before invite uid=%s: %s",
                self.name,
                user_id,
                e,
            )

    async def _create_fresh_invite_link(self, user_id: int) -> Optional[str]:
        """Отозвать старые инвайты пользователя и создать новую одноразовую ссылку."""
        try:
            if config.CLUB_GROUP_ID == 0:
                logger.error("CLUB_GROUP_ID не настроен")
                return None

            await self._ensure_user_unbanned_for_invite(user_id)
            await self._revoke_user_unused_invites(user_id)

            expire_date = datetime.now() + timedelta(hours=config.CLUB_INVITE_TTL_HOURS)
            link_obj = await self.bot.create_chat_invite_link(
                chat_id=config.CLUB_GROUP_ID,
                member_limit=1,
                expire_date=expire_date,
            )
            logger.info("✅ Fresh invite link created for user %s", user_id)
            try:
                await self.user_storage.insert_club_invite(
                    user_id=user_id,
                    invite_link=link_obj.invite_link,
                    expires_at=expire_date,
                )
            except Exception as e:
                logger.error("❌ Failed to save invite record for user %s: %s", user_id, e)
            return link_obj.invite_link
        except Exception as e:
            logger.error("❌ Failed to create invite link for user %s: %s", user_id, e)
            return None

    async def revoke_expired_links(self) -> None:
        if config.CLUB_GROUP_ID == 0:
            return
        try:
            await self.bot.get_chat(config.CLUB_GROUP_ID)
        except Exception as e:
            logger.warning(
                "[%s] revoke_expired_links: чат %s недоступен (%s). "
                "Проверьте CLUB_GROUP_ID и что бот состоит в группе — отзыв инвайтов пропущен.",
                self.name,
                config.CLUB_GROUP_ID,
                e,
            )
            return
        rows = await self.user_storage.fetch_expired_unused_club_invites()
        for row in rows:
            try:
                await self.bot.revoke_chat_invite_link(
                    chat_id=config.CLUB_GROUP_ID,
                    invite_link=row["invite_link"],
                )
                await self.user_storage.mark_club_invite_revoked(row["id"])
                logger.info(f"✅ Revoked expired invite id={row['id']}")
            except TelegramBadRequest as e:
                if "chat not found" in (str(e) or "").lower():
                    logger.warning(
                        "[%s] revoke: chat not found, прекращаем цикл (id инвайта=%s)",
                        self.name,
                        row["id"],
                    )
                    break
                logger.error(f"❌ Failed to revoke invite {row['id']}: {e}")
            except Exception as e:
                logger.error(f"❌ Failed to revoke invite {row['id']}: {e}")

    async def resolve_club_access_kind(self, user_id: int) -> str:
        """post | invite | no_license | no_club | unconfigured (без создания инвайта)."""
        if config.CLUB_GROUP_ID == 0:
            return "no_club"
        if await self._still_in_supergroup_membership(user_id):
            if not (config.CLUB_POST_LINK or "").strip():
                return "unconfigured"
            return "post"
        if not await self.user_storage.get_user_active_license(user_id):
            return "no_license"
        return "invite"

    async def get_group_link_for_user(self, user_id: int) -> Tuple[Optional[str], str]:
        """Ссылку на пост клуба или одноразовый инвайт.

        Returns:
            (url | None, kind) где kind: post | invite | unconfigured | no_license | no_club | error.
        """
        try:
            kind = await self.resolve_club_access_kind(user_id)
            if kind == "post":
                return (config.CLUB_POST_LINK or "").strip(), "post"
            if kind == "invite":
                invite_url = await self._create_fresh_invite_link(user_id)
                if not invite_url:
                    return None, "error"
                return invite_url, "invite"
            return None, kind

        except Exception as e:
            logger.error(f"❌ get_group_link_for_user user {user_id}: {e}")
            return None, "error"

    async def _still_in_supergroup_membership(self, user_id: int) -> bool:
        try:
            cm = await self.bot.get_chat_member(chat_id=config.CLUB_GROUP_ID, user_id=user_id)
            return _still_in_supergroup_cm(cm)
        except Exception:
            return False

    async def _send_invite_message(
        self,
        user_id: int,
        invite_link: str,
        *,
        message_text: str,
        log_source: str = "club_invite",
        log_subtype: Optional[str] = None,
    ) -> None:
        if not invite_link:
            logger.error(f"❌ Invite link is empty for user {user_id}")
            return

        logger.info(f"📤 Sending invite to user {user_id}, link: {invite_link[:50]}...")

        keyboard = with_main_menu(
            [
                [
                    InlineKeyboardButton(
                        text=club_txt.BTN_JOIN_CLOSED_CLUB,
                        url=invite_link,
                    )
                ]
            ]
        )

        result = await self.bot.send_message(
            chat_id=user_id,
            text=message_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
        )
        if self.message_copier:
            row_id = await self.message_copier.save_outgoing(
                message=result,
                source=log_source,
                subtype=log_subtype,
            )
            if row_id is None:
                logger.warning(
                    "[%s] invite message not in messages table uid=%s mid=%s",
                    self.name,
                    user_id,
                    result.message_id,
                )
        logger.info(
            f"✅ Invite message sent to user {user_id}, message_id: {result.message_id}"
        )
