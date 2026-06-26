from __future__ import annotations

import re
from datetime import datetime, timezone
from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from config import config
from utils.tg_rich import send_rich_or_fallback as _send_rich
from .base_agent import BaseAgent


ELINA_SYSTEM = """Ты — Элина, копирайтер ИИ-офиса.

Создаёшь тексты, посты (Telegram/Instagram/LinkedIn), email-рассылки, статьи, сценарии.
Пиши живо, адаптируй тон под платформу, предлагай несколько вариантов заголовков.

Форматируй ответы в Rich Markdown для Telegram:
- **текст** — заголовки и акценты
- *текст* — подзаголовки и пояснения
- > текст — готовые тексты для публикации
- Эмодзи по тематике контента
- Спецсимволы . ! ( ) - = писать как есть, без экранирования
- Длина ответа до 30 000 символов
- НЕ используй HTML-теги: никаких <b>, <i>, <code>

Отвечай по-русски, творчески."""


# ── Определение типа контента по тексту задачи ────────────────────────────────

_POST_KEYWORDS    = ("пост", "telegram", "тг", "инстаграм", "instagram", "соцсет", "vk", "вконтакте", "linkedin")
_LETTER_KEYWORDS  = ("письмо", "email", "рассылк", "newsletter", "e-mail", "почт")
_ARTICLE_KEYWORDS = ("статья", "блог", "blog", "seo", "лендинг", "landing", "сценари", "скрипт")

# Ключевые слова для автодетекции SEO-запроса в handle_task
_SEO_INTENT_KEYWORDS = ("seo", "карточк", "оптимизир", "заголовок товар", "описание товар", "артикул")
# Числовой product_id (WB nm_id — 7-10 цифр)
_PRODUCT_ID_RE = re.compile(r'\b(\d{7,10})\b')
# Свежесть данных: синкаем если старше 12 часов
_CARDS_MAX_AGE_HOURS = 12


def _detect_content_type(task: str) -> str:
    t = task.lower()
    if any(kw in t for kw in _POST_KEYWORDS):
        return "Пост"
    if any(kw in t for kw in _LETTER_KEYWORDS):
        return "Письмо"
    if any(kw in t for kw in _ARTICLE_KEYWORDS):
        return "Статья"
    return "Идея"


class ElinaAgent(BaseAgent):
    name = "Элина"
    agent_key = "elina"
    role = "Копирайтер"
    emoji = "✍️"
    system_prompt = ELINA_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.ELINA_BOT_TOKEN)

    # ------------------------------------------------------------------ #
    #  SEO helpers                                                         #
    # ------------------------------------------------------------------ #

    async def _auto_sync_cards(self, chat_id: int) -> None:
        """Синхронизирует product_cards если данные старше 12 часов или отсутствуют."""
        from db import get_pool, upsert_product_card, get_marketplace_shops
        from tools.marketplace import WBClient, OzonClient

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT MAX(fetched_at) AS last_sync FROM product_cards WHERE chat_id=$1",
                chat_id,
            )
        last_sync = row["last_sync"] if row else None

        if last_sync:
            age_hours = (datetime.now(timezone.utc) - last_sync).total_seconds() / 3600
            if age_hours < _CARDS_MAX_AGE_HOURS:
                logger.debug(f"[Элина/_auto_sync_cards] данные свежие ({age_hours:.1f}ч), пропускаем")
                return

        logger.info(f"[Элина/_auto_sync_cards] запускаем синк для chat_id={chat_id}")
        shops = await get_marketplace_shops(chat_id)

        for shop in shops:
            mp = shop["marketplace"]
            if mp == "wb":
                try:
                    cards = await WBClient(shop["api_token"]).get_nm_ids()
                    for _, info in cards.items():
                        await upsert_product_card(
                            chat_id, "wb", info["nm_id"],
                            info.get("title"), info.get("description"),
                            info.get("characteristics"), info.get("category"),
                        )
                    logger.info(f"[Элина/_auto_sync_cards] WB: {len(cards)} карточек")
                except Exception as e:
                    logger.warning(f"[Элина/_auto_sync_cards] WB: {e}")

            if mp == "ozon":
                try:
                    async with pool.acquire() as conn:
                        rows = await conn.fetch(
                            "SELECT ozon_offer_id FROM product_mapping WHERE ozon_offer_id IS NOT NULL"
                        )
                    offer_ids = [r["ozon_offer_id"] for r in rows]
                    if offer_ids:
                        content = await OzonClient(
                            shop["api_token"], shop.get("client_id", "")
                        ).get_product_content(offer_ids)
                        for oid, info in content.items():
                            await upsert_product_card(
                                chat_id, "ozon", oid,
                                info.get("title"), info.get("description"),
                                info.get("characteristics"), None,
                            )
                        logger.info(f"[Элина/_auto_sync_cards] Ozon: {len(content)} карточек")
                except Exception as e:
                    logger.warning(f"[Элина/_auto_sync_cards] Ozon: {e}")

    def _build_seo_brief(self, product_id: str, ctx: dict) -> str:
        """Формирует бриф для Claude из SEO-контекста карточки."""
        import json as _json

        card     = ctx.get("card") or {}
        reviews  = ctx.get("reviews") or []
        funnel   = ctx.get("funnel") or {}
        keywords = ctx.get("keywords") or []

        # Текущая карточка
        card_section = ""
        if card:
            title = card.get("title") or ""
            desc  = card.get("description") or ""
            chars = card.get("characteristics")
            if isinstance(chars, str):
                try:
                    chars = _json.loads(chars)
                except Exception:
                    chars = []
            chars_lines = ""
            if chars:
                chars_lines = "\n".join(
                    f"  • {c.get('name', '')}: {', '.join(str(v) for v in (c.get('value') or []))}"
                    for c in (chars or [])[:10]
                )
            marketplace = card.get("marketplace", "wb").upper()
            card_section = (
                f"Текущий контент карточки ({marketplace}):\n"
                f"  Заголовок ({len(title)} симв.): {title or '—'}\n"
                f"  Описание ({len(desc)} симв.): {desc[:300] or '—'}{'…' if len(desc) > 300 else ''}\n"
                + (f"  Характеристики:\n{chars_lines}\n" if chars_lines else "")
            )

        # Воронка
        funnel_section = ""
        total_views = funnel.get("total_views")
        if total_views:
            ctr    = funnel.get("avg_ctr")
            pos    = funnel.get("avg_position")
            orders = funnel.get("total_orders")
            funnel_section = (
                f"Воронка за 30 дней:\n"
                f"  Просмотры: {int(total_views or 0):,}\n"
                f"  CTR в корзину: {float(ctr or 0):.1f}%\n"
                f"  Заказов: {int(orders or 0):,}\n"
                + (f"  Средняя позиция в поиске: {float(pos):.1f}\n" if pos else "")
            )

        # Отзывы → живой язык покупателей
        reviews_section = ""
        if reviews:
            texts = [r["text"] for r in reviews if r.get("text")]
            sample = " | ".join(texts[:15])[:1500]
            neg_count = sum(1 for r in reviews if (r.get("rating") or 5) <= 2)
            reviews_section = (
                f"Отзывы покупателей (последние {len(reviews)}, негативных: {neg_count}):\n"
                f"  {sample}\n"
            )

        # Исторические ключи WB
        kw_section = ""
        if keywords:
            kw_lines = "\n".join(
                f"  • {k['keyword']} (позиция: {k['position'] or '?'}, охват: {k['search_count'] or '?'})"
                for k in keywords[:20]
            )
            kw_section = f"Исторические поисковые запросы WB:\n{kw_lines}\n"

        product_name = card.get("title") or (keywords[0].get("product_name") if keywords else None) or product_id

        return (
            f"Напиши SEO-оптимизированную карточку товара для маркетплейса.\n\n"
            f"Товар: {product_name} (ID: {product_id})\n\n"
            + (card_section + "\n" if card_section else "")
            + (funnel_section + "\n" if funnel_section else "")
            + (reviews_section + "\n" if reviews_section else "")
            + (kw_section + "\n" if kw_section else "")
            + "Задача:\n"
            "1. Заголовок (до 60 символов) — включи 2-3 сильных ключа из данных выше\n"
            "2. Описание (300-500 символов) — естественно вписать топ-5 запросов покупателей\n"
            "3. Характеристики (5-7 пунктов) — использовать формулировки из отзывов\n"
            "4. Текст должен читаться естественно, не как набор ключей\n"
            "5. Если текущий CTR низкий — объясни что конкретно слабо в заголовке/описании"
        )

    async def _do_seo_task(self, product_id: str, chat_id: int) -> str:
        """Полный SEO-пайплайн: авто-синк → контекст → бриф → Claude → Notion."""
        from db import get_seo_context

        await self._auto_sync_cards(chat_id)
        ctx = await get_seo_context(chat_id, product_id)
        brief = self._build_seo_brief(product_id, ctx)

        answer = await self.think(
            f"Задача на создание SEO-контента: {brief}",
            chat_id=0,
            is_task=True,
        )

        await self.post_to_group(f"📝 SEO-карточка готова: {answer[:200]}…")
        return answer

    # ------------------------------------------------------------------ #
    #  Выполнение задачи                                                   #
    # ------------------------------------------------------------------ #

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        """Создать контент по заданию и сохранить в Notion Content DB.

        Если в задаче обнаружен SEO-запрос с числовым product_id —
        автоматически запускает sync + context и передаёт обогащённый бриф Claude.
        """
        logger.info(f"[{self.name}] Задача от {from_agent}: {task!r}")

        # Автодетекция SEO-запроса через очередь (цепочка Марты)
        task_lower = task.lower()
        if any(kw in task_lower for kw in _SEO_INTENT_KEYWORDS):
            chat_id = getattr(self, "_current_chat_id", None)
            if chat_id:
                # 1. Числовой nm_id в тексте (приоритет)
                match = _PRODUCT_ID_RE.search(task)
                product_id: str | None = match.group(1) if match else None

                # 2. Артикул / display_name из product_mapping
                if not product_id:
                    from db import find_product_id_in_text
                    product_id = await find_product_id_in_text(task)

                if product_id:
                    logger.info(f"[Элина] SEO auto-detect → product_id={product_id}, chat_id={chat_id}")
                    return await self._do_seo_task(product_id, chat_id)

        # Обычная задача на контент
        answer = await self.think(
            f"Задача на создание контента от {from_agent}: {task}",
            chat_id=0,
            is_task=True,
        )

        content_type = _detect_content_type(task)
        await self.post_to_group(f"📝 {content_type} готов: {answer[:200]}…")
        return answer

    # ------------------------------------------------------------------ #
    #  Команды                                                             #
    # ------------------------------------------------------------------ #

    async def cmd_write(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/write <бриф> — написать текст по брифу."""
        brief = " ".join(context.args) if context.args else ""
        if not brief:
            await update.message.reply_text(
                "Использование: /write <бриф на текст>\n"
                "Пример: /write статья о пользе утренней зарядки, 500 слов"
            )
            return
        await update.message.reply_text("✍️ Пишу текст…")
        result = await self.handle_task(brief, from_agent="команды /write")
        await _send_rich(self.bot_token, update.effective_chat.id, result)

    @staticmethod
    def _extract_description_from_seo(text: str) -> str | None:
        """Извлекает секцию описания из SEO-текста Элины (выход Claude)."""
        # Ищем раздел "2. Описание" или "> Описание" в форматированном тексте
        patterns = [
            r"(?:\*\*2\.?\s*Описание:?\*\*)\s*\n(.*?)(?=\n\*\*3\.|\n##|\Z)",
            r"(?:^(?:2\.\s+)?Описание:?)\s*\n(.*?)(?=\n\d+\.|\n##|\Z)",
        ]
        for pat in patterns:
            m = re.search(pat, text, re.DOTALL | re.IGNORECASE | re.MULTILINE)
            if m:
                desc = re.sub(r"\*\*|\*|^>\s*", "", m.group(1), flags=re.MULTILINE).strip()
                if len(desc) >= 50:
                    return desc[:1000]
        # Fallback: самый длинный параграф (описание обычно самое длинное)
        paras = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 100]
        if paras:
            raw = max(paras, key=len)
            return re.sub(r"\*\*|\*|^>\s*", "", raw, flags=re.MULTILINE).strip()[:1000]
        return None

    async def cmd_seo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/seo <product_id> — SEO-карточка (авто-синк + контент + отзывы + воронка)."""
        product_id = " ".join(context.args).strip() if context.args else ""
        if not product_id:
            await update.message.reply_text(
                "Использование: /seo <product_id>\n"
                "Пример: /seo 12345678\n\n"
                "product_id — nm_id WB или offer_id Ozon.\n"
                "Данные карточки синхронизируются автоматически."
            )
            return

        chat_id = update.effective_user.id

        # Если не числовой nm_id — пробуем резолюцию через product_mapping
        if not _PRODUCT_ID_RE.fullmatch(product_id):
            from db import find_product_id_in_text
            resolved = await find_product_id_in_text(product_id)
            if resolved:
                product_id = resolved
            else:
                await update.message.reply_text(
                    f"❌ Артикул «{product_id}» не найден в реестре товаров.\n"
                    "Проверь /products у Макса — нужен wb_article, ozon_offer_id или display_name."
                )
                return

        await update.message.reply_text("🔍 Синхронизирую карточки и собираю данные…")
        result = await self._do_seo_task(product_id, chat_id)
        await _send_rich(self.bot_token, update.effective_chat.id, result)

        # Если Ozon-продукт — предложить применить описание одной кнопкой
        from db import get_seo_context, get_marketplace_shops
        ctx   = await get_seo_context(chat_id, product_id)
        card  = ctx.get("card") or {}
        if card.get("marketplace") == "ozon":
            shops = await get_marketplace_shops(chat_id)
            ozon  = next((s for s in shops if s["marketplace"] == "ozon"), None)
            if ozon:
                desc = self._extract_description_from_seo(result)
                if desc:
                    import hashlib, json as _json
                    offer_hash = hashlib.md5(product_id.encode()).hexdigest()[:8]
                    redis = await self._get_redis()
                    await redis.set(
                        f"seo_apply:{chat_id}:{offer_hash}",
                        _json.dumps({"offer_id": product_id, "description": desc, "shop_id": ozon["id"]}),
                        ex=86400,
                    )
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Применить описание к Ozon", callback_data=f"seoapp:apply:{chat_id}:{offer_hash}"),
                        InlineKeyboardButton("❌ Пропустить", callback_data=f"seoapp:skip:{chat_id}:{offer_hash}"),
                    ]])
                    await self.app.bot.send_message(
                        chat_id=chat_id,
                        text=(
                            "💡 <b>Применить SEO-описание к карточке Ozon?</b>\n\n"
                            f"<i>Предпросмотр ({len(desc)} симв.):</i>\n{desc[:300]}{'…' if len(desc) > 300 else ''}"
                        ),
                        parse_mode="HTML",
                        reply_markup=kb,
                    )

    async def _handle_seo_apply_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Обработка seoapp:apply/skip:{chat_id}:{offer_hash}."""
        import json as _json
        query = update.callback_query
        await query.answer()
        parts = query.data.split(":", 3)
        if len(parts) != 4:
            return
        _, action, chat_id_str, offer_hash = parts

        if action == "skip":
            await query.edit_message_text(query.message.text + "\n\n⏭️ Пропущено", parse_mode="HTML", reply_markup=None)
            return

        redis  = await self._get_redis()
        raw    = await redis.get(f"seo_apply:{chat_id_str}:{offer_hash}")
        if not raw:
            await query.edit_message_text(
                query.message.text + "\n\n⚠️ Сессия истекла. Запусти /seo снова.",
                parse_mode="HTML", reply_markup=None,
            )
            return

        plan     = _json.loads(raw)
        offer_id = plan["offer_id"]
        desc     = plan["description"]
        shop_id  = plan["shop_id"]

        from db import get_marketplace_shops
        from tools.marketplace import OzonClient
        chat_id = int(chat_id_str)
        shops   = await get_marketplace_shops(chat_id)
        shop    = next((s for s in shops if str(s["id"]) == str(shop_id)), None)
        if not shop:
            await query.edit_message_text(query.message.text + "\n\n❌ Магазин не найден", parse_mode="HTML", reply_markup=None)
            return

        await query.edit_message_text(query.message.text + "\n\n⏳ Применяю описание…", parse_mode="HTML", reply_markup=None)
        client = OzonClient(shop["api_token"], shop.get("client_id") or "")
        ok     = await client.update_product_description(offer_id, desc)
        await redis.delete(f"seo_apply:{chat_id_str}:{offer_hash}")

        result = "✅ Описание обновлено на Ozon!" if ok else "❌ Не удалось обновить — проверь логи или обнови вручную в кабинете."
        await query.edit_message_text(
            query.message.text.replace("⏳ Применяю описание…", "") + f"\n\n{result}",
            parse_mode="HTML",
        )
        logger.info(f"[Элина/seoapp] chat={chat_id} offer={offer_id} ok={ok}")

    async def cmd_post(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/post <тема> — написать пост для Telegram."""
        topic = " ".join(context.args) if context.args else ""
        if not topic:
            await update.message.reply_text(
                "Использование: /post <тема поста>\n"
                "Пример: /post запуск нового продукта — стартап в сфере AI"
            )
            return
        await update.message.reply_text("✍️ Пишу пост…")
        result = await self.handle_task(
            f"Напиши Telegram-пост на тему: {topic}",
            from_agent="команды /post",
        )
        await _send_rich(self.bot_token, update.effective_chat.id, result)

    def _help_text(self) -> str:
        return (
            "✍️ **Элина** — копирайтер\n\n"
            "Пишу тексты для карточек товаров, посты и рекламные тексты.\n\n"
            "📌 **Команды:**\n"
            "/write <бриф> — написать текст по заданию\n"
            "/post <тема> — написать пост для Telegram\n"
            "/seo <product_id> — SEO-карточка (авто-синк + кнопка применить к Ozon)\n"
            "/reset — очистить историю\n\n"
            "💡 Для Ozon: /seo покажет кнопку «Применить описание» — одним кликом обновит карточку."
        )

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start", "Запуск и помощь"),
            BotCommand("write", "Написать текст по брифу"),
            BotCommand("post", "Написать пост для Telegram"),
            BotCommand("seo", "SEO-карточка товара (авто-синк)"),
            BotCommand("reset", "Очистить историю диалога"),
        ]

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("write", self.cmd_write))
        self.app.add_handler(CommandHandler("post",  self.cmd_post))
        self.app.add_handler(CommandHandler("seo",   self.cmd_seo))
        self.app.add_handler(
            CallbackQueryHandler(self._handle_seo_apply_callback, pattern=r"^seoapp:")
        )
