"""Тексты (ES) для команды /menu.

Испанский нейтральный латиноамериканский: обращение на «tú», без «vosotros»
и кастильских оборотов. Основная аудитория — Мексика, Колумбия, Перу.

Кнопки отключённых на этом языке фич сюда не попадают: меню собирается по
тому, что реально поднято (см. bot/langs.py).
"""

from __future__ import annotations

MENU_TITLE_HTML = (
    "<b>📋 Menú</b>\n\n"
    "¿Qué quieres hacer?"
)

BTN_MORE = "📖 Preguntas frecuentes"
BTN_PRAYER = "🙏 Una oración para ti"
BTN_CHALLENGE = "📘 Reto de lectura"
BTN_PAYMENT = "💛 Apoyar el proyecto"
BTN_SUPPORT = "🆘 Escribir a soporte"
BTN_FEEDBACK = "💬 Dejar un comentario"
BTN_AFFILIATE = "🔗 Invitar amigos"
BTN_MAIN_MENU = "🏠 Menú principal"
