from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from config import config
from .base_agent import BaseAgent

_UTC = timezone.utc

_REPLY_PROMPT = """\
Ты — вежливый менеджер магазина на маркетплейсе. Напиши ответ на отзыв покупателя.

Товар: {product_name}
Оценка: {rating}/5
Отзыв: {text}
Автор: {author}

Требования:
- Обращайся по имени если оно есть
- Для 5★: поблагодари, упомяни товар, пожелай приятного использования
- Для 3-4★: поблагодари за честность, отметь что учтёшь замечания
- Для 1-2★: поблагодари за отзыв, извинись, предложи решение проблемы
- Длина: 2-4 предложения
- Тон: дружелюбный, живой, не шаблонный
- Язык: русский
- НЕ используй: "команда магазина", "мы рады", слова "искренне"

Ответь только текстом ответа, без кавычек и пояснений."""

MAX_SYSTEM = """Ты — Макс, менеджер по работе с отзывами на маркетплейсах.
Помогаешь продавцам на Wildberries и Ozon автоматически отвечать на отзывы.
Отвечай по-русски, кратко и по делу."""

_MP_LABELS = {"wb": "Wildberries", "ozon": "Ozon"}


class MaxAgent(BaseAgent):
    name = "Макс"
    agent_key = "max"
    role = "Менеджер отзывов"
    emoji = "🛒"
    system_prompt = MAX_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.MAX_BOT_TOKEN)

    # ------------------------------------------------------------------ #
    #  handle_task (заглушка — Макс работает через команды и scheduled)   #
    # ------------------------------------------------------------------ #

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        return await self.think(task, chat_id=0, is_task=True)

    # ------------------------------------------------------------------ #
    #  Генерация ответа                                                    #
    # ------------------------------------------------------------------ #

    async def _generate_reply(
        self,
        product_name: str,
        rating: int,
        text: str,
        author: str,
    ) -> str:
        prompt = _REPLY_PROMPT.format(
            product_name=product_name or "товар",
            rating=rating,
            text=text or "(без текста)",
            author=author or "покупатель",
        )
        response = await self.claude.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    # ------------------------------------------------------------------ #
    #  Отправка ответа на площадку                                         #
    # ------------------------------------------------------------------ #

    async def _send_to_marketplace(
        self, shop: dict, review_id: str, reply_text: str
    ) -> bool:
        from tools.marketplace import make_client
        try:
            client = make_client(shop)
            return await client.send_reply(review_id, reply_text)
        except Exception as e:
            logger.error(f"[Макс] send_to_marketplace error: {e}")
            return False

    # ------------------------------------------------------------------ #
    #  Основная логика обработки отзывов                                  #
    # ------------------------------------------------------------------ #

    async def process_reviews(self, chat_id: int) -> None:
        from db import (
            get_marketplace_shops, save_review,
            update_review_status, get_pending_reviews,
        )
        from tools.marketplace import make_client

        shops = await get_marketplace_shops(chat_id)
        if not shops:
            return

        since = datetime.now(_UTC) - timedelta(hours=8)  # буфер перекрытия между запусками

        for shop in shops:
            mp_label = _MP_LABELS.get(shop["marketplace"], shop["marketplace"])
            try:
                client = make_client(shop)
                reviews = await client.get_new_reviews(since)
                logger.info(f"[Макс] {mp_label}: {len(reviews)} отзывов для chat={chat_id}")
            except Exception as e:
                logger.error(f"[Макс] get_new_reviews {mp_label}: {e}")
                continue

            for rv in reviews:
                is_new = await save_review(
                    marketplace=shop["marketplace"],
                    review_id=rv["review_id"],
                    product_id=rv.get("product_id"),
                    product_name=rv.get("product_name"),
                    rating=rv.get("rating", 0),
                    text=rv.get("text"),
                    author=rv.get("author"),
                    chat_id=chat_id,
                )
                if not is_new:
                    continue

                rating = rv.get("rating", 0)
                try:
                    reply = await self._generate_reply(
                        product_name=rv.get("product_name", ""),
                        rating=rating,
                        text=rv.get("text", ""),
                        author=rv.get("author", ""),
                    )
                except Exception as e:
                    logger.error(f"[Макс] generate_reply error: {e}")
                    reply = ""

                await update_review_status(
                    shop["marketplace"], rv["review_id"],
                    status="pending_approval" if rating <= 2 else "new",
                    generated_reply=reply,
                )

                if rating <= 2:
                    await self._notify_pending(chat_id, shop, rv, reply)
                else:
                    # Автоответ для рейтинга 3-5
                    ok = await self._send_to_marketplace(shop, rv["review_id"], reply)
                    status = "auto_replied" if ok else "pending_approval"
                    await update_review_status(
                        shop["marketplace"], rv["review_id"],
                        status=status,
                        final_reply=reply if ok else None,
                    )
                    if not ok and rating <= 4:
                        await self._notify_pending(chat_id, shop, rv, reply)
                    logger.info(
                        f"[Макс] review={rv['review_id'][:8]} rating={rating} → {status}"
                    )

    async def _notify_pending(
        self, chat_id: int, shop: dict, rv: dict, generated_reply: str
    ) -> None:
        """Отправить уведомление о негативном отзыве с кнопками одобрения."""
        mp = shop["marketplace"]
        stars = "⭐️" * rv.get("rating", 0)
        text = (
            f"{stars} ({rv.get('rating', '?')}/5) — {rv.get('product_name', 'товар')}\n"
            f"👤 {rv.get('author', 'Покупатель')}\n\n"
            f"💬 {rv.get('text') or '(без текста)'}\n\n"
            f"📝 Предлагаемый ответ:\n{generated_reply}"
        )
        cb_base = f"rev:{mp}:{rv['review_id']}"
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Отправить",     callback_data=f"{cb_base}:approve"),
                InlineKeyboardButton("✏️ Редактировать", callback_data=f"{cb_base}:edit"),
                InlineKeyboardButton("🚫 Пропустить",    callback_data=f"{cb_base}:skip"),
            ]
        ])
        await self._notify_user(chat_id, text, reply_markup=keyboard)

    # ------------------------------------------------------------------ #
    #  Callback handler                                                    #
    # ------------------------------------------------------------------ #

    async def _handle_review_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()

        # callback_data: rev:{mp}:{review_id}:{action}
        parts = query.data.split(":", 3)
        if len(parts) != 4:
            return
        _, mp, review_id, action = parts
        chat_id = query.message.chat_id

        from db import get_pending_reviews, update_review_status

        if action == "approve":
            reviews = await get_pending_reviews(chat_id)
            rv = next((r for r in reviews if r["review_id"] == review_id), None)
            reply_text = (rv or {}).get("generated_reply", "")
            if rv and reply_text:
                from db import get_marketplace_shops
                shops = await get_marketplace_shops(chat_id)
                shop = next((s for s in shops if s["marketplace"] == mp), None)
                if shop:
                    ok = await self._send_to_marketplace(shop, review_id, reply_text)
                    if ok:
                        await update_review_status(mp, review_id, "replied", final_reply=reply_text)
                        await query.edit_message_text(query.message.text + "\n\n✅ Ответ отправлен.")
                        return
            await query.edit_message_text(query.message.text + "\n\n❌ Не удалось отправить ответ.")

        elif action == "edit":
            await query.edit_message_text(
                query.message.text + "\n\n✏️ Напишите ваш вариант ответа:"
            )
            # Сохраняем в Redis что ждём редактирование
            await self._redis_set(
                f"pending_edit:{chat_id}",
                f"{mp}:{review_id}",
                ttl=600,
            )

        elif action == "skip":
            await update_review_status(mp, review_id, "skipped")
            await query.edit_message_text(query.message.text + "\n\n🚫 Пропущено.")

    async def _handle_edit_reply(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Получить текст пользователя и отправить как кастомный ответ."""
        chat_id = update.effective_chat.id
        pending = await self._redis_get(f"pending_edit:{chat_id}")
        if not pending:
            return  # не наш апдейт — пропускаем (передаётся базовому handle_message)

        await self._redis_set(f"pending_edit:{chat_id}", "", ttl=1)  # сбрасываем

        parts = pending.split(":", 1)
        if len(parts) != 2:
            return
        mp, review_id = parts
        reply_text = update.message.text.strip()

        from db import get_marketplace_shops, update_review_status
        shops = await get_marketplace_shops(chat_id)
        shop = next((s for s in shops if s["marketplace"] == mp), None)

        if shop:
            ok = await self._send_to_marketplace(shop, review_id, reply_text)
            if ok:
                await update_review_status(mp, review_id, "replied", final_reply=reply_text)
                await update.message.reply_text("✅ Ваш ответ отправлен на площадку.")
                return
        await update.message.reply_text("❌ Не удалось отправить ответ.")

    # ------------------------------------------------------------------ #
    #  Команды бота                                                        #
    # ------------------------------------------------------------------ #

    async def cmd_add_shop(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/add_shop wb <token> | /add_shop ozon <token> <client_id>"""
        args = context.args or []
        if len(args) < 2:
            await update.message.reply_text(
                "Использование:\n"
                "  /add_shop wb <api_token>\n"
                "  /add_shop ozon <api_token> <client_id>"
            )
            return

        mp = args[0].lower()
        token = args[1]
        client_id = args[2] if len(args) > 2 else None
        chat_id = update.effective_user.id

        if mp not in ("wb", "ozon"):
            await update.message.reply_text("Поддерживается: wb, ozon")
            return
        if mp == "ozon" and not client_id:
            await update.message.reply_text("Для Ozon нужен client_id: /add_shop ozon <token> <client_id>")
            return

        from db import add_marketplace_shop
        await add_marketplace_shop(chat_id, mp, token, client_id=client_id)
        label = _MP_LABELS.get(mp, mp)
        await update.message.reply_text(f"✅ Магазин {label} подключён.")
        logger.info(f"[Макс] add_shop mp={mp} chat={chat_id}")

    async def cmd_shops(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/shops — список подключённых магазинов."""
        from db import get_marketplace_shops
        shops = await get_marketplace_shops(update.effective_user.id)
        if not shops:
            await update.message.reply_text(
                "Магазинов нет. Добавь:\n/add_shop wb <token>\n/add_shop ozon <token> <client_id>"
            )
            return
        lines = ["🛒 *Ваши магазины:*\n"]
        for s in shops:
            label = _MP_LABELS.get(s["marketplace"], s["marketplace"])
            name = s.get("shop_name") or label
            lines.append(f"• {name} ({label})")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def cmd_pending(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/pending — отзывы ожидающие одобрения."""
        from db import get_pending_reviews
        reviews = await get_pending_reviews(update.effective_user.id)
        if not reviews:
            await update.message.reply_text("✅ Нет отзывов, ожидающих одобрения.")
            return
        await update.message.reply_text(
            f"📋 Отзывов на одобрении: {len(reviews)}. Используй /pending для просмотра каждого."
        )
        for rv in reviews[:5]:  # показываем не более 5 за раз
            await self._notify_pending(
                update.effective_user.id,
                {"marketplace": rv["marketplace"]},
                rv,
                rv.get("generated_reply", ""),
            )

    async def cmd_reviews(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/reviews — статистика за сегодня."""
        from db import get_pool
        chat_id = update.effective_user.id
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'auto_replied')    AS auto_replied,
                    COUNT(*) FILTER (WHERE status = 'replied')          AS replied,
                    COUNT(*) FILTER (WHERE status = 'pending_approval') AS pending,
                    COUNT(*) FILTER (WHERE status = 'skipped')          AS skipped,
                    COUNT(*)                                             AS total
                FROM marketplace_reviews
                WHERE chat_id = $1
                  AND created_at >= CURRENT_DATE::timestamptz
                """,
                chat_id,
            )
        await update.message.reply_text(
            f"📊 *Отзывы за сегодня:*\n\n"
            f"✅ Автоответ: {row['auto_replied']}\n"
            f"✅ Отправлено вручную: {row['replied']}\n"
            f"⏳ Ожидают одобрения: {row['pending']}\n"
            f"🚫 Пропущено: {row['skipped']}\n"
            f"📨 Всего новых: {row['total']}",
            parse_mode="Markdown",
        )

    # ------------------------------------------------------------------ #
    #  Регистрация хендлеров                                              #
    # ------------------------------------------------------------------ #

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("add_shop", self.cmd_add_shop))
        self.app.add_handler(CommandHandler("shops",    self.cmd_shops))
        self.app.add_handler(CommandHandler("pending",  self.cmd_pending))
        self.app.add_handler(CommandHandler("reviews",  self.cmd_reviews))
        self.app.add_handler(
            CallbackQueryHandler(self._handle_review_callback, pattern=r"^rev:")
        )
        # Перехватываем сообщения если ждём кастомный ответ (pending_edit)
        self.app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self._handle_edit_reply,
            ),
            group=1,  # до базового handle_message (group=0)
        )
