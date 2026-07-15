#!/usr/bin/env python3
"""Выгрузка сообщений клубной группы из БД для анализа в DeepSeek / других LLM."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.services.report_exclude import sql_exclude_users
from config import Config, config as default_config
from storage.user_storage import UserStorage

logger = logging.getLogger("export_club_group_messages")
MSK = ZoneInfo("Europe/Moscow")

_MEDIA_LABEL = {
    "photo": "[фото]",
    "voice": "[голосовое]",
    "video": "[видео]",
    "video_note": "[кружок]",
    "audio": "[аудио]",
    "document": "[документ]",
    "sticker": "[стикер]",
    "animation": "[gif]",
}


def _load_config(env_file: Optional[str]) -> Config:
    if not env_file:
        return default_config
    from dotenv import load_dotenv

    load_dotenv(env_file, override=True)
    from config import load_config

    return load_config()


def _mention(user_id: int, username: Optional[str], first_name: Optional[str]) -> str:
    fn = (first_name or "").strip()
    un = (username or "").strip().lstrip("@")
    if un and fn:
        return f"@{un} ({fn})"
    if un:
        return f"@{un}"
    if fn:
        return fn
    return f"id{user_id}"


def _display_content(content: str, message_type: str) -> str:
    text = (content or "").strip()
    if text:
        return text.replace("\r\n", "\n")
    return _MEDIA_LABEL.get(message_type, f"[{message_type}]")


async def fetch_admin_ids(pool) -> List[int]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT telegram_user_id FROM admins")
    return [int(r["telegram_user_id"]) for r in rows]


def _resolve_export_chat_ids(cfg: Config, *, all_public_groups: bool) -> List[int]:
    """Чаты для выгрузки: клубная группа или все group/supergroup кроме служебных."""
    skip = {
        int(getattr(cfg, "DIALOG_FORUM_GROUP_ID", 0) or 0),
        int(getattr(cfg, "ADMIN_GROUP_ID", 0) or 0),
        int((cfg.ADMIN_CHANNEL_ID or "").strip() or 0) if str(cfg.ADMIN_CHANNEL_ID or "").lstrip("-").isdigit() else 0,
    }
    skip.discard(0)
    if not all_public_groups:
        gid = int(cfg.CLUB_GROUP_ID or 0)
        return [gid] if gid else []
    return []  # filled from DB when all_public_groups


async def fetch_export_chat_ids(pool, cfg: Config, *, all_public_groups: bool) -> List[int]:
    explicit = _resolve_export_chat_ids(cfg, all_public_groups=all_public_groups)
    if explicit:
        return explicit
    skip = {
        int(getattr(cfg, "DIALOG_FORUM_GROUP_ID", 0) or 0),
        int(getattr(cfg, "ADMIN_GROUP_ID", 0) or 0),
    }
    skip.discard(0)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT chat_id
            FROM messages
            WHERE chat_type IN ('group', 'supergroup')
              AND role = 'user'
              AND chat_id < 0
            ORDER BY chat_id
            """
        )
    out: List[int] = []
    for r in rows:
        cid = int(r["chat_id"])
        if cid in skip:
            continue
        out.append(cid)
    return out


async def fetch_messages(
    pool,
    *,
    chat_ids: Sequence[int],
    since: datetime,
    until: datetime,
    exclude_topic_id: int = 0,
    exclude_user_ids: Sequence[int] = (),
) -> List[Dict[str, Any]]:
    if not chat_ids:
        return []
    topic_filter = ""
    args: list = [list(chat_ids), since, until]
    n = 4
    if exclude_topic_id > 0:
        topic_filter = (
            f" AND COALESCE((m.metadata->>'message_thread_id')::bigint, 0) <> ${n}"
        )
        args.append(exclude_topic_id)
        n += 1
    exclude_sql, exclude_ids = sql_exclude_users(
        "m.user_id", start_param=n, extra_ids=exclude_user_ids
    )
    args.extend(exclude_ids)

    sql = f"""
        SELECT
            m.id,
            m.chat_id,
            m.user_id,
            u.username,
            u.first_name,
            u.last_name,
            m.content,
            m.message_type,
            m.created_at,
            COALESCE((m.metadata->>'message_thread_id')::bigint, 0) AS topic_id
        FROM messages m
        LEFT JOIN users u ON u.user_id = m.user_id
        WHERE m.chat_id = ANY($1::bigint[])
          AND m.chat_type IN ('group', 'supergroup')
          AND m.role = 'user'
          AND m.deleted_at IS NULL
          AND m.created_at >= $2
          AND m.created_at < $3
          {topic_filter}
          {exclude_sql}
        ORDER BY m.chat_id ASC, m.created_at ASC
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


def _row_to_record(row: Dict[str, Any]) -> Dict[str, Any]:
    ts = row["created_at"]
    if hasattr(ts, "astimezone"):
        ts = ts.astimezone(MSK)
    uid = int(row.get("user_id") or 0)
    content = _display_content(row.get("content") or "", row.get("message_type") or "text")
    return {
        "id": row.get("id"),
        "chat_id": int(row.get("chat_id") or 0),
        "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
        "date_msk": ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else "",
        "time_msk": ts.strftime("%H:%M") if hasattr(ts, "strftime") else "",
        "user_id": uid,
        "username": row.get("username"),
        "first_name": row.get("first_name"),
        "last_name": row.get("last_name"),
        "author": _mention(uid, row.get("username"), row.get("first_name")),
        "topic_id": int(row.get("topic_id") or 0),
        "message_type": row.get("message_type") or "text",
        "text": content,
    }


def _stats(rows: Sequence[Dict[str, Any]]) -> Tuple[int, int, Dict[str, int]]:
    authors: set[int] = set()
    by_type: Dict[str, int] = defaultdict(int)
    for row in rows:
        rec = _row_to_record(row)
        if rec["user_id"]:
            authors.add(rec["user_id"])
        by_type[rec["message_type"]] += 1
    return len(rows), len(authors), dict(by_type)


def _write_jsonl(path: Path, records: Sequence[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _known_topic_labels(cfg: Config) -> Dict[int, str]:
    """Подписи веток из .env (остальные — по id)."""
    labels: Dict[int, str] = {}
    mapping = (
        (getattr(cfg, "WELCOME_TOPIC_ID", 0), "Приветствие / основной чат"),
        (getattr(cfg, "REACT_TOPIC_ID", 0), "Реакции"),
        (getattr(cfg, "CLUB_DIGEST_TOPIC_ID", 0), "Дайджест"),
        (getattr(cfg, "WISH_BOARD_DIGEST_TOPIC_ID", 0), "Доска желаний"),
    )
    for topic_id, title in mapping:
        if topic_id and topic_id not in labels:
            labels[int(topic_id)] = title
    return labels


def _topic_label(topic_id: int, known: Dict[int, str]) -> str:
    if topic_id == 0:
        return "Общий чат (без ветки)"
    return known.get(topic_id, f"Ветка {topic_id}")


def _topic_slug(topic_id: int, label: str) -> str:
    import re

    base = re.sub(r"[^\w\s-]", "", label.lower(), flags=re.UNICODE)
    base = re.sub(r"[\s_]+", "-", base.strip())[:40].strip("-") or "chat"
    return f"topic_{topic_id:05d}_{base}"


def _topic_stats(records: Sequence[Dict[str, Any]]) -> Tuple[int, int, Dict[str, int]]:
    authors: set[int] = set()
    by_type: Dict[str, int] = defaultdict(int)
    for rec in records:
        if rec["user_id"]:
            authors.add(rec["user_id"])
        by_type[rec["message_type"]] += 1
    return len(records), len(authors), dict(by_type)


def _append_transcript_lines(
    lines: List[str],
    records: Sequence[Dict[str, Any]],
    *,
    include_topic: bool,
) -> None:
    current_day: Optional[date] = None
    for rec in records:
        day = date.fromisoformat(rec["date_msk"]) if rec["date_msk"] else None
        if day and day != current_day:
            current_day = day
            lines.append(f"### {day.strftime('%d.%m.%Y')}")
            lines.append("")
        topic = ""
        if include_topic and rec["topic_id"]:
            topic = f" [topic {rec['topic_id']}]"
        lines.append(f"- [{rec['time_msk']}] {rec['author']}{topic}: {rec['text']}")


def _write_markdown(
    path: Path,
    *,
    title: str,
    records: Sequence[Dict[str, Any]],
    club_group_id: int,
    since: datetime,
    until: datetime,
    message_count: int,
    author_count: int,
    by_type: Dict[str, int],
    exclude_topic_id: int = 0,
    topic_id: Optional[int] = None,
    topic_label: Optional[str] = None,
    include_topic: bool = True,
) -> None:
    since_msk = since.astimezone(MSK)
    until_msk = until.astimezone(MSK)
    type_line = ", ".join(f"{k}: {v}" for k, v in sorted(by_type.items(), key=lambda x: -x[1]))

    lines: List[str] = [f"# {title}", "", "## Мета"]
    if topic_id is not None:
        lines.append(f"- Ветка: **{topic_label}**")
        lines.append(f"- message_thread_id: `{topic_id}`")
    lines.extend(
        [
            f"- Период (МСК): **{since_msk.strftime('%d.%m.%Y %H:%M')}** — **{until_msk.strftime('%d.%m.%Y %H:%M')}**",
            f"- Chat ID: `{club_group_id}`",
            f"- Сообщений: **{message_count}**",
            f"- Уникальных авторов: **{author_count}**",
            f"- Типы: {type_line}",
        ]
    )
    if exclude_topic_id:
        lines.append(f"- Исключён топик (message_thread_id): `{exclude_topic_id}`")
    lines.extend(
        [
            "",
            "## Формат",
            "- Каждая строка: `[время] автор: текст`",
            "- Медиа без подписи: `[фото]`, `[голосовое]`, `[стикер]` и т.д.",
            "",
            "## Переписка",
            "",
        ]
    )
    _append_transcript_lines(lines, records, include_topic=include_topic)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _chat_label(chat_id: int, cfg: Config) -> str:
    if chat_id == int(cfg.CLUB_GROUP_ID or 0):
        return "Клубная группа"
    return f"Группа {chat_id}"


def _write_llm_bundle(
    path: Path,
    *,
    records: Sequence[Dict[str, Any]],
    since: datetime,
    until: datetime,
    chat_ids: Sequence[int],
    known_labels: Dict[int, str],
    excluded_admin_count: int,
    cfg: Config,
) -> None:
    """Один файл для загрузки в DeepSeek: все ветки подряд с явными разделителями."""
    since_msk = since.astimezone(MSK)
    until_msk = until.astimezone(MSK)
    by_chat: Dict[int, Dict[int, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for rec in records:
        by_chat[int(rec["chat_id"])][int(rec["topic_id"])].append(rec)

    lines = [
        "# Экспорт сообщений участников в групповых чатах",
        "",
        "## Мета",
        f"- Период (МСК): {since_msk.strftime('%d.%m.%Y %H:%M')} — {until_msk.strftime('%d.%m.%Y %H:%M')}",
        f"- Чатов: {len(chat_ids)}",
        f"- Сообщений участников (без админов): {len(records)}",
        f"- Исключено telegram id из таблицы admins: {excluded_admin_count}",
        "",
        "## Инструкция для анализа",
        "Ниже переписка участников клуба по веткам форума. Сообщения админов бота исключены.",
        "Медиа без текста помечены как [фото], [голосовое] и т.д.",
        "",
    ]

    for chat_id in sorted(by_chat.keys()):
        lines.append(f"# Чат: {_chat_label(chat_id, cfg)} (`{chat_id}`)")
        lines.append("")
        topics = by_chat[chat_id]
        for topic_id in sorted(topics.keys(), key=lambda x: (-len(topics[x]), x)):
            topic_records = topics[topic_id]
            label = _topic_label(topic_id, known_labels)
            lines.append(f"## Ветка: {label} (topic_id={topic_id})")
            lines.append(f"Сообщений: {len(topic_records)}")
            lines.append("")
            _append_transcript_lines(lines, topic_records, include_topic=False)
            lines.append("")
            lines.append("---")
            lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _write_topics_bundle(
    out_dir: Path,
    *,
    stem: str,
    records: Sequence[Dict[str, Any]],
    chat_ids: Sequence[int],
    since: datetime,
    until: datetime,
    known_labels: Dict[int, str],
    exclude_topic_id: int,
    cfg: Config,
) -> Path:
    topics_dir = out_dir / f"{stem}_by_topics"
    topics_dir.mkdir(parents=True, exist_ok=True)

    by_topic: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for rec in records:
        by_topic[int(rec["topic_id"])].append(rec)

    index_lines = [
        "# Экспорт клубной группы по веткам (форум Telegram)",
        "",
        "## Мета",
        f"- Период (МСК): **{since.astimezone(MSK).strftime('%d.%m.%Y %H:%M')}** — **{until.astimezone(MSK).strftime('%d.%m.%Y %H:%M')}**",
        f"- Чаты: {', '.join(f'`{c}`' for c in chat_ids)}",
        f"- Всего сообщений: **{len(records)}**",
        f"- Веток: **{len(by_topic)}**",
        "",
        "## Содержание",
        "",
        "| Ветка | ID | Сообщений | Авторов | Файл |",
        "|-------|-----|-----------|---------|------|",
    ]

    topic_files: List[Tuple[int, str, Path, int, int]] = []

    for topic_id in sorted(by_topic.keys(), key=lambda x: (-len(by_topic[x]), x)):
        topic_records = by_topic[topic_id]
        label = _topic_label(topic_id, known_labels)
        slug = _topic_slug(topic_id, label)
        fname = f"{slug}.md"
        fpath = topics_dir / fname
        msg_n, auth_n, by_type = _topic_stats(topic_records)
        _write_markdown(
            fpath,
            title=label,
            records=topic_records,
            club_group_id=int(chat_ids[0]) if len(chat_ids) == 1 else 0,
            since=since,
            until=until,
            message_count=msg_n,
            author_count=auth_n,
            by_type=by_type,
            exclude_topic_id=exclude_topic_id,
            topic_id=topic_id,
            topic_label=label,
            include_topic=False,
        )
        topic_files.append((topic_id, label, fpath, msg_n, auth_n))
        index_lines.append(
            f"| {label} | `{topic_id}` | {msg_n} | {auth_n} | [{fname}]({fname}) |"
        )

    index_lines.extend(
        [
            "",
            "## Как анализировать в DeepSeek",
            "- **Рекомендуется:** файл `*_LLM_BUNDLE.md` в родительской папке exports — всё в одном документе.",
            "- Или загрузите **INDEX.md** + нужные файлы веток.",
            "- Дополнительно: `.jsonl` — структурированные записи для скриптов.",
            "- Каждый `.md` ветки — отдельная тема форума; переписка хронологическая.",
            "- Неизвестные ветки названы «Ветка N» — это `message_thread_id` в Telegram.",
            "",
        ]
    )
    if int(cfg.CLUB_GROUP_ID or 0) in chat_ids:
        index_lines.insert(
            4,
            f"- Основной чат: **{_chat_label(int(cfg.CLUB_GROUP_ID), cfg)}** (`{cfg.CLUB_GROUP_ID}`)",
        )
    index_path = topics_dir / "INDEX.md"
    index_path.write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    return topics_dir


async def run(args: argparse.Namespace) -> Path:
    cfg = _load_config(args.env_file)
    if not cfg.CLUB_GROUP_ID and not args.all_public_groups:
        raise SystemExit("CLUB_GROUP_ID не задан в конфиге")

    now = datetime.now(MSK)
    until = now if args.until == "now" else datetime.fromisoformat(args.until).replace(tzinfo=MSK)
    since = until - timedelta(days=args.days)

    storage = UserStorage(cfg.database_url)
    await storage.initialize()
    try:
        chat_ids = await fetch_export_chat_ids(
            storage.db.pool,
            cfg,
            all_public_groups=args.all_public_groups,
        )
        if not chat_ids:
            raise SystemExit("Нет chat_id для выгрузки")

        admin_ids: List[int] = []
        if args.exclude_admins:
            admin_ids = await fetch_admin_ids(storage.db.pool)

        rows = await fetch_messages(
            storage.db.pool,
            chat_ids=chat_ids,
            since=since,
            until=until,
            exclude_topic_id=args.exclude_topic_id,
            exclude_user_ids=admin_ids,
        )
    finally:
        await storage.close()

    records = [_row_to_record(r) for r in rows]
    message_count, author_count, by_type = _stats(rows)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = (
        f"group_messages_{since.astimezone(MSK).strftime('%Y%m%d')}_"
        f"{until.astimezone(MSK).strftime('%Y%m%d')}_{args.days}d"
    )
    jsonl_path = out_dir / f"{stem}.jsonl"

    _write_jsonl(jsonl_path, records)

    known_labels = _known_topic_labels(cfg)
    topics_dir = _write_topics_bundle(
        out_dir,
        stem=stem,
        records=records,
        chat_ids=chat_ids,
        since=since,
        until=until,
        known_labels=known_labels,
        exclude_topic_id=args.exclude_topic_id,
        cfg=cfg,
    )

    llm_path = out_dir / f"{stem}_LLM_BUNDLE.md"
    _write_llm_bundle(
        llm_path,
        records=records,
        since=since,
        until=until,
        chat_ids=chat_ids,
        known_labels=known_labels,
        excluded_admin_count=len(admin_ids),
        cfg=cfg,
    )

    md_path = topics_dir / "INDEX.md"
    if args.include_combined:
        combined_path = out_dir / f"{stem}.md"
        _write_markdown(
            combined_path,
            title="Экспорт переписки клубной группы (все ветки)",
            records=records,
            club_group_id=int(chat_ids[0]) if chat_ids else 0,
            since=since,
            until=until,
            message_count=message_count,
            author_count=author_count,
            by_type=by_type,
            exclude_topic_id=args.exclude_topic_id,
            include_topic=True,
        )
        logger.info("  Combined MD: %s", combined_path)

    logger.info(
        "Экспорт готов: %s сообщений, %s авторов\n  LLM bundle: %s\n  Topics: %s\n  JSONL: %s",
        message_count,
        author_count,
        llm_path,
        topics_dir,
        jsonl_path,
    )
    return llm_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Выгрузка сообщений клубной группы")
    parser.add_argument("--days", type=int, default=14, help="Сколько дней назад (по умолчанию 14)")
    parser.add_argument(
        "--until",
        default="now",
        help="Конец периода: now или ISO-дата (МСК)",
    )
    parser.add_argument(
        "--output-dir",
        default="exports",
        help="Каталог для файлов (по умолчанию exports/)",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Путь к .env (например /home/appuser/club/.env для prod)",
    )
    parser.add_argument(
        "--exclude-topic-id",
        type=int,
        default=0,
        help="Не включать сообщения из этого message_thread_id (например топик дайджеста)",
    )
    parser.add_argument(
        "--exclude-admins",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Не включать сообщения telegram id из таблицы admins (по умолчанию: да)",
    )
    parser.add_argument(
        "--all-public-groups",
        action="store_true",
        help="Все group/supergroup из БД, кроме DIALOG_FORUM и ADMIN_GROUP",
    )
    parser.add_argument(
        "--include-combined",
        action="store_true",
        help="Дополнительно сохранить один общий .md со всеми ветками",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    ns = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if ns.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    md_path = asyncio.run(run(ns))
    print(md_path)


if __name__ == "__main__":
    main()
