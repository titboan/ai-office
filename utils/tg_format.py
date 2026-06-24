"""Formatting helpers for Telegram messages (parse_mode='MarkdownV2').

Telegram MarkdownV2 supports: **bold**, _italic_, __underline__, ~strike~,
||spoiler||, `code`, ```pre```, [text](url), > blockquote,
# Heading (1-6), --- horizontal rule, | table |.

Special chars that MUST be escaped in plain text:
  _ * [ ] ( ) ~ ` # + - = | { } . !
"""

from __future__ import annotations

import html
import re

# Characters that need escaping in MarkdownV2 plain text segments
_MDV2_SPECIAL = r'\_*[]()~`>#+-=|{}.!'


# ── MarkdownV2 escape ─────────────────────────────────────────────────────────

def escape_mdv2(text: str) -> str:
    """Escape all MarkdownV2 special characters in a plain-text fragment.

    Use this when inserting user-supplied strings into a MarkdownV2 message
    so they are never misinterpreted as formatting.
    """
    return re.sub(r'([_*\[\]()~`>#+=|{}.!\-])', r'\\\1', str(text))


def strip_mdv2(text: str) -> str:
    """Remove MarkdownV2 formatting markers to produce readable plain text."""
    # remove escape backslashes
    text = re.sub(r'\\([_*\[\]()~`>#+=|{}.!\-])', r'\1', text)
    # remove inline markup: **bold**, *bold*, _italic_, `code`, ~strike~, ||spoiler||
    text = re.sub(r'\*\*(.+?)\*\*|\*(.+?)\*|__(.+?)__|_(.+?)_|`(.+?)`|~(.+?)~|\|\|(.+?)\|\|', lambda m: next(g for g in m.groups() if g is not None), text)
    # remove headings
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # remove horizontal rules
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)
    # remove blockquote markers
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    # remove table formatting
    text = re.sub(r'\|', ' ', text)
    return text.strip()


# ── HTML primitives (kept for Notion export and table() helper) ───────────────

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
    return f"{amount:,.0f} {symbol}".replace(",", " ")


# ── Notion helper ─────────────────────────────────────────────────────────────

def strip_html(text: str) -> str:
    """Strip HTML tags for saving to Notion (which expects plain text / Markdown)."""
    return re.sub(r"<[^>]+>", "", text)


# ── Formatting instructions for system prompts ────────────────────────────────

from utils.tg_rich import RICH_MARKDOWN_FORMAT_RULES  # noqa: F401 — re-export

MARKDOWN_FORMAT_RULES = """
Форматируй ответы в MarkdownV2 для Telegram:
- *текст* — жирный (заголовки разделов, ключевые числа)
- _текст_ — курсив (пояснения, уточнения)
- `текст` — моноширинный (артикулы, ID, команды)
- > текст — цитата (выводы, инсайты)
- # Заголовок — заголовок раздела
- --- — горизонтальный разделитель между крупными блоками
- | Колонка 1 | Колонка 2 | — таблица (с заголовком и строкой |---|---|)
- Эмодзи в начале разделов
- Спецсимволы . ! ( ) - + = внутри обычного текста экранируй обратным слешем: \\. \\! \\( \\) \\- \\+ \\=
- НЕ используй HTML-теги: никаких <b>, <i>, <code>
""".strip()

# Kept for backward compatibility — agents not yet migrated still import this
HTML_FORMAT_RULES = """
Форматируй ответы в HTML для Telegram:
- <b>текст</b> — жирный (заголовки разделов, ключевые числа)
- <i>текст</i> — курсив (пояснения, уточнения)
- <code>текст</code> — моноширинный (артикулы, ID, команды)
- <blockquote>текст</blockquote> — цитата (выводы, инсайты)
- Эмодзи в начале разделов
- НЕ используй Markdown: никаких *звёздочек*, ##заголовков, |таблиц|, ---разделителей
""".strip()


# ── Output post-processor ─────────────────────────────────────────────────────

def clean_agent_output(text: str) -> str:
    """Sanitise Claude's response before sending to Telegram.

    Removes stray HTML tags (if agent was supposed to use Markdown) and
    collapses excessive blank lines.
    """
    # strip any HTML tags Claude accidentally emitted
    text = re.sub(r"<[^>]+>", "", text)
    # collapse 3+ consecutive blank lines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
