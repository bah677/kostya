#!/usr/bin/env python3
"""Сборка статического сайта обновлений экосистемы из notes/*.md."""

from __future__ import annotations

import html
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "notes"
SITE = ROOT / "site"
ASSETS = SITE / "assets"

MONTHS_RU = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def date_label(d: date) -> str:
    return f"{d.day} {MONTHS_RU[d.month]} {d.year}"


def md_to_html_body(md: str) -> str:
    """Минимальный markdown → HTML (заголовки, списки, абзацы, bold/italic/links)."""
    lines = md.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    in_ul = False

    def close_ul() -> None:
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    def inline(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<!\*)\*(.+?)\*(?!\*)", r"<em>\1</em>", s)
        s = re.sub(
            r"\[([^\]]+)\]\((https?://[^)]+)\)",
            r'<a href="\2" rel="noopener noreferrer">\1</a>',
            s,
        )
        return s

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            close_ul()
            continue
        if line.startswith("### "):
            close_ul()
            out.append(f"<h3>{inline(line[4:].strip())}</h3>")
        elif line.startswith("## "):
            close_ul()
            out.append(f"<h2>{inline(line[3:].strip())}</h2>")
        elif line.startswith("# "):
            close_ul()
            # Заголовок дня уже в шаблоне страницы — пропускаем дубль H1.
            continue
        elif re.match(r"^[-*•]\s+", line):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            item = re.sub(r"^[-*•]\s+", "", line)
            out.append(f"<li>{inline(item)}</li>")
        else:
            close_ul()
            out.append(f"<p>{inline(line)}</p>")
    close_ul()
    return "\n".join(out)


def parse_note_date(path: Path) -> date | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})\.md", path.name)
    if not m:
        return None
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def page_shell(
    *,
    title: str,
    body: str,
    nav: str = "",
    brand: str = "Обновления экосистемы",
    brand_href: str = "/",
    tagline: str = "Только то, что видно пользователям",
) -> str:
    generated = datetime.now(MSK).strftime("%d.%m.%Y %H:%M МСК")
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <meta name="description" content="Обновления экосистемы Мирона — клуб, БиблияБот и связанные сервисы.">
  <link rel="stylesheet" href="/assets/style.css">
</head>
<body>
  <header class="site-header">
    <a class="brand" href="{html.escape(brand_href)}">{html.escape(brand)}</a>
    <p class="tagline">{html.escape(tagline)}</p>
  </header>
  <main>
    {nav}
    {body}
  </main>
  <footer class="site-footer">
    <p>mironbot.ru · страница собрана при деплое · {html.escape(generated)}</p>
  </footer>
</body>
</html>
"""


PROJECTS = (
    {
        "slug": "julia_avatar",
        "title": "Контент-аватар Юлии",
        "tagline": "Редакция: материалы, банк идей, черновики",
    },
)


def strip_md(text: str) -> str:
    """Убирает разметку из короткого анонса на списке.

    Анонс раньше экранировался как есть, и на главной было видно
    «**1000 озвученных молитв**» вместе со звёздочками. Рендерить теги в
    превью не нужно — достаточно снять маркеры.
    """
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    t = re.sub(r"(?<!\*)\*(.+?)\*(?!\*)", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", t)
    t = re.sub(r"`([^`]+)`", r"\1", t)
    return t


def collect_notes(notes_dir: Path) -> list[tuple[date, Path]]:
    notes: list[tuple[date, Path]] = []
    if not notes_dir.is_dir():
        return notes
    for path in sorted(notes_dir.glob("????-??-??.md"), reverse=True):
        d = parse_note_date(path)
        if d is None:
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text or text.startswith("<!-- empty"):
            continue
        notes.append((d, path))
    return notes


def build_project(slug: str, title: str, tagline: str) -> None:
    notes_dir = ROOT / "projects" / slug / "notes"
    out_dir = SITE / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    notes = collect_notes(notes_dir)
    href_base = f"/{slug}"
    nav_home = (
        f'<p class="nav"><a href="/">← Экосистема</a>'
        f' · <a href="{href_base}/">{html.escape(title)}</a></p>'
    )
    for d, path in notes:
        body_md = path.read_text(encoding="utf-8")
        body = f"<article class=\"day\">\n<h1>{html.escape(date_label(d))}</h1>\n"
        body += md_to_html_body(body_md)
        body += "\n</article>"
        nav = nav_home + f'<p class="nav"><a href="{href_base}/">← Все обновления</a></p>'
        html_page = page_shell(
            title=f"{date_label(d)} — {title}",
            body=body,
            nav=nav,
            brand=title,
            brand_href=f"{href_base}/",
            tagline=tagline,
        )
        (out_dir / f"{d.isoformat()}.html").write_text(html_page, encoding="utf-8")

    if notes:
        items = []
        for d, path in notes:
            preview = path.read_text(encoding="utf-8").strip().splitlines()
            blurb = ""
            for line in preview:
                t = line.strip()
                if not t or t.startswith("#"):
                    continue
                if t.startswith("-") or t.startswith("*") or t.startswith("•"):
                    t = re.sub(r"^[-*•]\s+", "", t)
                blurb = strip_md(t)[:160]
                break
            items.append(
                "<li>"
                f'<a href="{href_base}/{d.isoformat()}.html"><time datetime="{d.isoformat()}">'
                f"{html.escape(date_label(d))}</time></a>"
                + (f"<span class=\"blurb\">{html.escape(blurb)}</span>" if blurb else "")
                + "</li>"
            )
        latest_d, latest_path = notes[0]
        latest = (
            f'<article class="day latest">\n'
            f"<h2>Сегодня / последнее: {html.escape(date_label(latest_d))}</h2>\n"
            f"{md_to_html_body(latest_path.read_text(encoding='utf-8'))}\n"
            f"</article>\n"
        )
        body = latest + (
            "<section class=\"index\">\n"
            f"<h1>{html.escape(title)}</h1>\n"
            f"<p class=\"lead\">{html.escape(tagline)}</p>\n"
            "<ol class=\"days\">\n"
            + "\n".join(items)
            + "\n</ol>\n</section>"
        )
    else:
        body = (
            "<section class=\"index\">"
            f"<h1>{html.escape(title)}</h1>"
            "<p>Пока нет опубликованных обновлений.</p></section>"
        )
    (out_dir / "index.html").write_text(
        page_shell(
            title=f"{title} — обновления",
            body=body,
            nav=f'<p class="nav"><a href="/">← Экосистема</a></p>',
            brand=title,
            brand_href=f"{href_base}/",
            tagline=tagline,
        ),
        encoding="utf-8",
    )


def build() -> None:
    NOTES.mkdir(parents=True, exist_ok=True)
    ASSETS.mkdir(parents=True, exist_ok=True)

    notes = collect_notes(NOTES)

    for d, path in notes:
        body_md = path.read_text(encoding="utf-8")
        body = f"<article class=\"day\">\n<h1>{html.escape(date_label(d))}</h1>\n"
        body += md_to_html_body(body_md)
        body += "\n</article>"
        nav = '<p class="nav"><a href="/">← Все обновления</a></p>'
        html_page = page_shell(title=f"{date_label(d)} — обновления", body=body, nav=nav)
        (SITE / f"{d.isoformat()}.html").write_text(html_page, encoding="utf-8")

    project_links = "".join(
        f'<li><a href="/{html.escape(p["slug"])}/">{html.escape(p["title"])}</a>'
        f'<span class="blurb">{html.escape(p["tagline"])}</span></li>'
        for p in PROJECTS
    )

    if notes:
        items = []
        for d, path in notes:
            preview = path.read_text(encoding="utf-8").strip().splitlines()
            blurb = ""
            for line in preview:
                t = line.strip()
                if not t or t.startswith("#"):
                    continue
                if t.startswith("-") or t.startswith("*") or t.startswith("•"):
                    t = re.sub(r"^[-*•]\s+", "", t)
                blurb = strip_md(t)[:160]
                break
            items.append(
                "<li>"
                f'<a href="/{d.isoformat()}.html"><time datetime="{d.isoformat()}">'
                f"{html.escape(date_label(d))}</time></a>"
                + (f"<span class=\"blurb\">{html.escape(blurb)}</span>" if blurb else "")
                + "</li>"
            )
        body = (
            "<section class=\"index\">\n"
            "<h1>Что нового</h1>\n"
            "<p class=\"lead\">Кратко о изменениях для участников клуба, "
            "пользователей БиблияБота и связанных сервисов.</p>\n"
            "<p class=\"lead\">Разделы: </p>\n"
            f"<ol class=\"days\">{project_links}</ol>\n"
            "<ol class=\"days\">\n"
            + "\n".join(items)
            + "\n</ol>\n</section>"
        )
        latest_d, latest_path = notes[0]
        latest = (
            f'<article class="day latest">\n'
            f"<h2>Сегодня / последнее: {html.escape(date_label(latest_d))}</h2>\n"
            f"{md_to_html_body(latest_path.read_text(encoding='utf-8'))}\n"
            f"</article>\n"
        )
        body = latest + body
    else:
        body = (
            "<section class=\"index\"><h1>Что нового</h1>"
            "<p>Пока нет опубликованных обновлений.</p>"
            f"<ol class=\"days\">{project_links}</ol></section>"
        )

    (SITE / "index.html").write_text(
        page_shell(title="Обновления экосистемы — mironbot.ru", body=body),
        encoding="utf-8",
    )

    for p in PROJECTS:
        build_project(p["slug"], p["title"], p["tagline"])


if __name__ == "__main__":
    build()
    print(f"Published {len(list(NOTES.glob('????-??-??.md')))} note file(s) → {SITE}")
