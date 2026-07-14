"""Rich Messages helper — Bot API 10.1 sendRichMessage + GFM Markdown."""
from __future__ import annotations

import html as _html
import re

import aiohttp
from loguru import logger

RICH_MARKDOWN_FORMAT_RULES = """
Форматируй ответы в Rich Markdown для Telegram:
- **текст** — жирный (заголовки разделов, ключевые числа, выводы)
- *текст* — курсив (пояснения, уточнения, вторичные данные)
- `текст` — моноширинный (артикулы, ID, команды, коды)
- > текст — цитата (инсайт, важный вывод)
- # Заголовок / ## Подраздел / ### Деталь — заголовки разделов
- --- — горизонтальный разделитель между крупными блоками
- Таблица: первая строка должна начинаться с `|`, перед ней — пустая строка.
  НЕ пиши текст перед `|` в той же строке (неверно: `Ozon: | col |`).
  Верно: заголовок на отдельной строке, затем пустая строка, затем таблица:
  `**Ozon**\n\n| Товар | Заказы |\n|---|---|\n| ... | ... |`
  До 20 колонок, строка-разделитель |---|---| обязательна.
- - пункт / 1. пункт — маркированные и нумерованные списки (до 500 строк)
- - [ ] задача / - [x] выполнено — чеклисты
- Эмодзи в начале разделов для навигации
- Спецсимволы экранировать НЕ нужно — пиши . ! ( ) - + = как есть
- Длина ответа до 30 000 символов — можно делать подробные отчёты и таблицы
- НЕ используй HTML-теги: никаких <b>, <i>, <code>
""".strip()

RICH_MESSAGE_CHUNK_SIZE = 30_000

_HTML_TAG_RE = re.compile(r"</?(b|i|u|s|code|pre|blockquote|a)\b", re.IGNORECASE)


def looks_like_html(text: str) -> bool:
    """True if text contains Telegram HTML tags (utils.tg_format style: bold/italic/code/pre/quote/link).

    Легитимный Rich Markdown от Клода никогда не содержит такие теги —
    RICH_MARKDOWN_FORMAT_RULES явно запрещает <b>, <i>, <code>.
    """
    if not text:
        return False
    return bool(_HTML_TAG_RE.search(text))


async def send_rich_message(
    bot_token: str,
    chat_id: int | str,
    markdown: str,
    reply_markup_dict: dict | None = None,
    reply_to_message_id: int | None = None,
) -> bool:
    """POST sendRichMessage via aiohttp. Returns True on success."""
    url = f"https://api.telegram.org/bot{bot_token}/sendRichMessage"
    payload: dict = {"chat_id": chat_id, "rich_message": {"markdown": markdown}}
    if reply_to_message_id:
        payload["reply_parameters"] = {"message_id": reply_to_message_id}
    if reply_markup_dict:
        payload["reply_markup"] = reply_markup_dict
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.warning(f"sendRichMessage failed: {data.get('description', 'unknown')}")
                return data.get("ok", False)
    except Exception as e:
        logger.warning(f"sendRichMessage exception: {e}")
        return False


def gfm_to_html_fallback(text: str) -> str:
    """Convert GFM Rich Markdown to Telegram HTML for fallback."""
    # Strip table rows (| ... |)
    text = re.sub(r"^\|.*\|[ \t]*$", "", text, flags=re.MULTILINE)
    # Strip horizontal rules
    text = re.sub(r"^---+[ \t]*$", "", text, flags=re.MULTILINE)
    # Headings → <b>
    text = re.sub(
        r"^#{1,6}[ \t]+(.+)$",
        lambda m: f"<b>{_html.escape(m.group(1).strip())}</b>",
        text,
        flags=re.MULTILINE,
    )
    # **bold** → <b>
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: f"<b>{_html.escape(m.group(1))}</b>", text)
    # *italic* → <i>
    text = re.sub(
        r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)",
        lambda m: f"<i>{_html.escape(m.group(1))}</i>",
        text,
    )
    # `code` → <code>
    text = re.sub(r"`([^`]+)`", lambda m: f"<code>{_html.escape(m.group(1))}</code>", text)
    # [text](url) → <a>
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{m.group(2)}">{_html.escape(m.group(1))}</a>',
        text,
    )
    # > quote → <blockquote>
    text = re.sub(r"^>[ \t]?(.*)$", r"<blockquote>\1</blockquote>", text, flags=re.MULTILINE)
    # Collapse extra blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def send_rich_or_fallback(
    bot_token: str,
    chat_id: int | str,
    markdown: str,
    reply_markup_dict: dict | None = None,
    reply_to_message_id: int | None = None,
) -> bool:
    """Send Rich Message; on error fall back to sendMessage with HTML, then plain text.

    Возвращает True только если каждый чанк реально доставлен хотя бы одним
    из трёх способов — вызывающая сторона (например, ретрай логика уведомлений
    о вопросах покупателей) полагается на этот результат, чтобы не считать
    сообщение отправленным, если оно тихо не дошло ни одним из способов.
    """
    if not markdown:
        return False
    chunks = [
        markdown[i : i + RICH_MESSAGE_CHUNK_SIZE]
        for i in range(0, len(markdown), RICH_MESSAGE_CHUNK_SIZE)
    ]
    all_ok = True
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        markup = reply_markup_dict if is_last else None
        reply_id = reply_to_message_id if i == 0 else None
        success = await send_rich_message(
            bot_token, chat_id, chunk,
            reply_markup_dict=markup,
            reply_to_message_id=reply_id,
        )
        if not success:
            success = await _html_fallback(bot_token, chat_id, chunk, markup, reply_id)
        all_ok = all_ok and success
    return all_ok


async def _html_fallback(
    bot_token: str,
    chat_id: int | str,
    chunk: str,
    reply_markup_dict: dict | None,
    reply_to_message_id: int | None,
) -> bool:
    html = gfm_to_html_fallback(chunk)
    return await _send_html_chunks(bot_token, chat_id, html, reply_markup_dict, reply_to_message_id)


async def _send_html_chunks(
    bot_token: str,
    chat_id: int | str,
    html: str,
    reply_markup_dict: dict | None,
    reply_to_message_id: int | None,
) -> bool:
    html_chunks = [html[j : j + 4096] for j in range(0, max(len(html), 1), 4096)]
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    all_ok = True
    for j, hchunk in enumerate(html_chunks):
        h_is_last = j == len(html_chunks) - 1
        payload: dict = {"chat_id": chat_id, "text": hchunk, "parse_mode": "HTML"}
        if reply_to_message_id and j == 0:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        if reply_markup_dict and h_is_last:
            payload["reply_markup"] = reply_markup_dict
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    data = await resp.json()
                    if data.get("ok"):
                        continue
                    logger.warning(
                        f"HTML fallback failed: {data.get('description')}, trying plain"
                    )
                    plain: dict = {
                        "chat_id": chat_id,
                        "text": re.sub(r"<[^>]+>", "", hchunk),
                    }
                    if reply_markup_dict and h_is_last:
                        plain["reply_markup"] = reply_markup_dict
                    async with session.post(url, json=plain) as plain_resp:
                        plain_data = await plain_resp.json()
                        if not plain_data.get("ok"):
                            logger.error(
                                f"send_rich_or_fallback plain fallback failed: "
                                f"{plain_data.get('description')}"
                            )
                            all_ok = False
        except Exception as e:
            logger.error(f"send_rich_or_fallback HTML fallback exception: {e}")
            all_ok = False
    return all_ok


async def send_html_message(
    bot_token: str,
    chat_id: int | str,
    html_text: str,
    reply_markup_dict: dict | None = None,
    reply_to_message_id: int | None = None,
) -> bool:
    """Send already-built Telegram HTML text (e.g. Max's hardcoded <pre> reports).

    Chunk by 4096 chars, sendMessage with parse_mode="HTML"; on API error per chunk,
    fall back to the same chunk without tags (no parse_mode).
    """
    if not html_text:
        return False
    return await _send_html_chunks(
        bot_token, chat_id, html_text, reply_markup_dict, reply_to_message_id
    )
