"""HTML formatting helpers for Telegram messages (parse_mode='HTML').

Telegram supports: <b>, <i>, <u>, <s>, <code>, <pre>, <a href>,
<blockquote>, <blockquote expandable>, <tg-spoiler>.

All text helpers escape special HTML characters so callers don't have to.
"""

from __future__ import annotations

import html
import re


# ── Primitives ────────────────────────────────────────────────────────────────

def escape(text: str) -> str:
    return html.escape(str(text))


def bold(text: str) -> str:
    return f"<b>{escape(text)}</b>"


def italic(text: str) -> str:
    return f"<i>{escape(text)}</i>"


def code(text: str) -> str:
    return f"<code>{escape(text)}</code>"


def pre(text: str) -> str:
    return f"<pre>{escape(text)}</pre>"


def quote(text: str) -> str:
    return f"<blockquote>{escape(text)}</blockquote>"


def quote_expandable(text: str) -> str:
    return f"<blockquote expandable>{escape(text)}</blockquote>"


def link(text: str, url: str) -> str:
    return f'<a href="{url}">{escape(text)}</a>'


# ── Composites ────────────────────────────────────────────────────────────────

def section(emoji: str, title: str, body: str) -> str:
    """Standard agent section block: emoji + bold title + body."""
    return f"{emoji} <b>{escape(title)}</b>\n\n{body}"


def table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a table as monospace text inside <pre>."""
    all_rows = [headers] + rows
    col_widths = [
        max(len(str(r[i])) for r in all_rows if i < len(r))
        for i in range(len(headers))
    ]
    lines: list[str] = []
    lines.append("  ".join(str(h).ljust(w) for h, w in zip(headers, col_widths)))
    lines.append("-" * (sum(col_widths) + 2 * (len(col_widths) - 1)))
    for row in rows:
        lines.append("  ".join(str(c).ljust(w) for c, w in zip(row, col_widths)))
    return pre("\n".join(lines))


# ── Money formatting ─────────────────────────────────────────────────────────

def format_money(amount: float | int, *, symbol: str = "₽") -> str:
    """Format a monetary amount with space-separated thousands: '1 234 ₽'."""
    return f"{amount:,.0f} {symbol}".replace(",", " ")


# ── Notion helper ─────────────────────────────────────────────────────────────

def strip_html(text: str) -> str:
    """Strip HTML tags for saving to Notion (which expects plain text / Markdown)."""
    return re.sub(r"<[^>]+>", "", text)


# ── Formatting instruction for system prompts ─────────────────────────────────

HTML_FORMAT_RULES = """
Форматируй ответы в HTML для Telegram:
- <b>текст</b> — жирный (заголовки разделов, ключевые числа)
- <i>текст</i> — курсив (пояснения, уточнения)
- <code>текст</code> — моноширинный (артикулы, ID, команды)
- <blockquote>текст</blockquote> — цитата (выводы, инсайты)
- <blockquote expandable>длинный текст</blockquote> — раскрываемая цитата для больших блоков
- Эмодзи в начале разделов
- НЕ используй Markdown: никаких *звёздочек*, ##заголовков, |таблиц|
- Спецсимволы < > & внутри текста не нужно экранировать — выводи их буквально внутри тегов
""".strip()
