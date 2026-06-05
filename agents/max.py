from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from loguru import logger
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

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
_ONBOARD_TTL = 60 * 60 * 24  # 24 часа


class MaxAgent(BaseAgent):
    name = "Макс"
    agent_key = "max"
    role = "Менеджер отзывов"
    emoji = "🛒"
    system_prompt = MAX_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.MAX_BOT_TOKEN)

    # ------------------------------------------------------------------ #
    #  handle_task (заглушка)                                              #
    # ------------------------------------------------------------------ #

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        return await self.think(task, chat_id=0, is_task=True)

    # ------------------------------------------------------------------ #
    #  handle_message — блокируем Claude во время онбординга              #
    # ------------------------------------------------------------------ #

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        state = await self._get_onboard(chat_id)
        if state and state.get("step") not in (None, "done"):
            # Онбординг в процессе — group=2 (_handle_onboard_text) сам обработает
            return
        await super().handle_message(update, context)

    # ------------------------------------------------------------------ #
    #  Онбординг — управление состоянием                                   #
    # ------------------------------------------------------------------ #

    def _onboard_key(self, chat_id: int) -> str:
        return f"max_onboard:{chat_id}"

    async def _get_onboard(self, chat_id: int) -> dict | None:
        raw = await self._redis_get(self._onboard_key(chat_id))
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    async def _set_onboard(self, chat_id: int, state: dict) -> None:
        await self._redis_set(self._onboard_key(chat_id), json.dumps(state), ttl=_ONBOARD_TTL)

    async def _clear_onboard(self, chat_id: int) -> None:
        await self._redis_set(self._onboard_key(chat_id), "", ttl=1)

    # ------------------------------------------------------------------ #
    #  Онбординг — вспомогательные отправки                               #
    # ------------------------------------------------------------------ #

    async def _send_platform_choice(self, chat_id: int) -> None:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🟣 Wildberries", callback_data="onboard:wb"),
            InlineKeyboardButton("🔵 Ozon",        callback_data="onboard:ozon"),
            InlineKeyboardButton("🟣+🔵 Обе",      callback_data="onboard:both"),
        ]])
        await self._notify_user(
            chat_id,
            "👋 Привет! Я Макс — слежу за отзывами на маркетплейсах и отвечаю за тебя.\n\n"
            "Какую площадку подключим?",
            reply_markup=keyboard,
        )

    async def _send_wb_prompt(self, chat_id: int) -> None:
        await self._notify_user(
            chat_id,
            "Отправь API токен Wildberries.\n\n"
            "📌 Где взять:\n"
            "seller.wildberries.ru → Настройки → Доступ к API → "
            "создать токен с категорией Отзывы",
        )

    async def _send_ozon_client_id_prompt(self, chat_id: int) -> None:
        await self._notify_user(
            chat_id,
            "Отправь Client-Id магазина Ozon.\n\n"
            "📌 Где взять:\n"
            "seller.ozon.ru → Настройки → API ключи",
        )

    async def _send_finish(self, chat_id: int, connected: list[str]) -> None:
        labels = " и ".join(_MP_LABELS.get(mp, mp) for mp in connected)
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("▶️ Запустить сейчас", callback_data="onboard:run_now"),
            InlineKeyboardButton("Позже",               callback_data="onboard:run_later"),
        ]])
        await self._notify_user(
            chat_id,
            f"✅ Готово! Подключено: {labels}\n\n"
            "Буду проверять отзывы в 09:00, 14:00 и 20:00 МСК.\n"
            "Запустить проверку прямо сейчас?",
            reply_markup=keyboard,
        )

    # ------------------------------------------------------------------ #
    #  /start — переопределяем базовый                                     #
    # ------------------------------------------------------------------ #

    async def _send_status_with_buttons(
        self, chat_id: int, shops: list[dict], message_method
    ) -> None:
        """Показать статус подключённых площадок с кнопками действий."""
        connected = {s["marketplace"] for s in shops}
        wb_ok   = "wb"   in connected
        ozon_ok = "ozon" in connected

        lines = ["👋 Привет! Вот твои магазины:"]
        lines.append(f"{'🟣 Wildberries — подключён' if wb_ok   else '🟣 Wildberries — не подключён'}")
        lines.append(f"{'🔵 Ozon — подключён'        if ozon_ok else '🔵 Ozon — не подключён'}")
        lines.append("\nЧто сделать?")

        buttons: list[InlineKeyboardButton] = []
        if not wb_ok:
            buttons.append(InlineKeyboardButton("🟣 Подключить WB",   callback_data="onboard:add_wb"))
        if not ozon_ok:
            buttons.append(InlineKeyboardButton("🔵 Подключить Ozon", callback_data="onboard:add_ozon"))

        keyboard_rows = []
        if buttons:
            keyboard_rows.append(buttons)
        keyboard_rows.append([
            InlineKeyboardButton("▶️ Проверить отзывы сейчас", callback_data="onboard:run_now"),
            InlineKeyboardButton("📊 Статистика",               callback_data="onboard:stats"),
        ])

        await message_method(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(keyboard_rows),
        )

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_user.id

        from db import get_marketplace_shops
        shops = await get_marketplace_shops(chat_id)
        if shops:
            # Сбрасываем незавершённый онбординг если был
            await self._clear_onboard(chat_id)
            await self._send_status_with_buttons(chat_id, shops, update.message.reply_text)
            return

        # Новый пользователь или сброс незавершённого онбординга — начинаем заново
        await self._set_onboard(chat_id, {"step": "choose_platform", "data": {}})
        await self._send_platform_choice(chat_id)

    # ------------------------------------------------------------------ #
    #  Callback — онбординг (выбор площадки, run_now, run_later)          #
    # ------------------------------------------------------------------ #

    async def _handle_onboard_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        action = query.data.split(":", 1)[1]  # wb / ozon / both / run_now / run_later

        if action == "run_now":
            await query.edit_message_text(query.message.text + "\n\n▶️ Запускаю проверку…")
            await self.process_reviews(chat_id)
            return

        if action == "run_later":
            await query.edit_message_text(query.message.text + "\n\n👍 Хорошо, проверю по расписанию.")
            return

        if action == "stats":
            from db import get_pool
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
            await query.answer(
                f"За сегодня: авто={row['auto_replied']} ручных={row['replied']} "
                f"pending={row['pending']} пропущено={row['skipped']}",
                show_alert=True,
            )
            return

        if action == "add_wb":
            await self._set_onboard(chat_id, {"step": "wb_token", "data": {}})
            await query.edit_message_text(
                "🟣 Подключаем Wildberries.\n\n"
                "Отправь API токен Wildberries.\n\n"
                "📌 Где взять:\n"
                "seller.wildberries.ru → Настройки → Доступ к API → "
                "создать токен с категорией Отзывы"
            )
            return

        if action == "add_ozon":
            await self._set_onboard(chat_id, {"step": "ozon_client_id", "data": {}})
            await query.edit_message_text(
                "🔵 Подключаем Ozon.\n\n"
                "Отправь Client-Id магазина Ozon.\n\n"
                "📌 Где взять:\n"
                "seller.ozon.ru → Настройки → API ключи"
            )
            return

        # Выбор площадки (choose_platform — для новых пользователей)
        state = await self._get_onboard(chat_id) or {"step": "choose_platform", "data": {}}
        if state.get("step") != "choose_platform":
            return

        if action == "wb":
            await self._set_onboard(chat_id, {"step": "wb_token", "data": {}})
            await query.edit_message_text(
                "🟣 Wildberries выбран.\n\n"
                "Отправь API токен Wildberries.\n\n"
                "📌 Где взять:\n"
                "seller.wildberries.ru → Настройки → Доступ к API → "
                "создать токен с категорией Отзывы"
            )

        elif action == "ozon":
            await self._set_onboard(chat_id, {"step": "ozon_client_id", "data": {}})
            await query.edit_message_text(
                "🔵 Ozon выбран.\n\n"
                "Отправь Client-Id магазина Ozon.\n\n"
                "📌 Где взять:\n"
                "seller.ozon.ru → Настройки → API ключи"
            )

        elif action == "both":
            await self._set_onboard(
                chat_id,
                {"step": "wb_token", "data": {"need_ozon": True}},
            )
            await query.edit_message_text(
                "🟣+🔵 Подключим обе площадки. Начнём с Wildberries.\n\n"
                "Отправь API токен Wildberries.\n\n"
                "📌 Где взять:\n"
                "seller.wildberries.ru → Настройки → Доступ к API → "
                "создать токен с категорией Отзывы"
            )

    # ------------------------------------------------------------------ #
    #  Text handler — онбординг (group=2)                                  #
    # ------------------------------------------------------------------ #

    async def _handle_onboard_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = update.effective_chat.id
        state = await self._get_onboard(chat_id)
        if not state or state.get("step") in (None, "done", "choose_platform"):
            return

        step = state["step"]
        data = state.get("data", {})
        text = update.message.text.strip()

        if step == "wb_token":
            await update.message.reply_text("🔍 Проверяю токен Wildberries…")
            from tools.marketplace import WBClient
            client = WBClient(text)
            ok = await client.check_connection()
            if not ok:
                await update.message.reply_text(
                    "❌ Токен не подходит. Проверь что токен создан с категорией «Отзывы» и попробуй ещё раз."
                )
                return

            from db import add_marketplace_shop
            await add_marketplace_shop(chat_id, "wb", text)
            data["wb_connected"] = True

            if data.get("need_ozon"):
                data["step_done"] = "wb"
                await self._set_onboard(chat_id, {"step": "ozon_client_id", "data": data})
                await update.message.reply_text(
                    "✅ Wildberries подключён!\n\n"
                    "Теперь Ozon. Отправь Client-Id магазина.\n\n"
                    "📌 Где взять:\n"
                    "seller.ozon.ru → Настройки → API ключи"
                )
            else:
                await self._clear_onboard(chat_id)
                await self._send_finish(chat_id, ["wb"])

        elif step == "ozon_client_id":
            data["client_id"] = text
            await self._set_onboard(chat_id, {"step": "ozon_api_key", "data": data})
            await update.message.reply_text("Теперь отправь Api-Key")

        elif step == "ozon_api_key":
            await update.message.reply_text("🔍 Проверяю подключение к Ozon…")
            from tools.marketplace import OzonClient
            client_id = data.get("client_id", "")
            client = OzonClient(text, client_id)
            ok = await client.check_connection()
            if not ok:
                await update.message.reply_text(
                    "❌ Не удалось подключиться. Проверь Client-Id и Api-Key и попробуй ещё раз.\n"
                    "Отправь Client-Id заново:"
                )
                await self._set_onboard(chat_id, {"step": "ozon_client_id", "data": data})
                return

            from db import add_marketplace_shop
            await add_marketplace_shop(chat_id, "ozon", text, client_id=client_id)
            data["ozon_connected"] = True

            connected = []
            if data.get("wb_connected"):
                connected.append("wb")
            connected.append("ozon")

            await self._clear_onboard(chat_id)
            await self._send_finish(chat_id, connected)

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
    #  Основная логика обработки отзывов                                   #
    # ------------------------------------------------------------------ #

    async def process_reviews(self, chat_id: int) -> None:
        from db import (
            get_marketplace_shops, save_review,
            update_review_status,
        )
        from tools.marketplace import make_client

        shops = await get_marketplace_shops(chat_id)
        if not shops:
            return

        since = datetime.now(_UTC) - timedelta(hours=8)

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
                    ok = await self._send_to_marketplace(shop, rv["review_id"], reply)
                    if ok:
                        await update_review_status(
                            shop["marketplace"], rv["review_id"],
                            status="auto_replied",
                            final_reply=reply,
                        )
                    else:
                        logger.error(
                            f"[Макс] send_reply failed: mp={shop['marketplace']} "
                            f"review={rv['review_id'][:8]} rating={rating} — статус остаётся 'new'"
                        )
                    logger.info(
                        f"[Макс] review={rv['review_id'][:8]} rating={rating} → {status}"
                    )

    async def _notify_pending(
        self, chat_id: int, shop: dict, rv: dict, generated_reply: str
    ) -> None:
        mp = shop["marketplace"]
        rating = rv.get("rating", 0)
        stars = "⭐️" * rating
        text = (
            f"{stars} ({rating}/5) — {rv.get('product_name', 'товар')}\n"
            f"👤 {rv.get('author', 'Покупатель')}\n\n"
            f"💬 {rv.get('text') or '(без текста)'}\n\n"
            f"📝 Предлагаемый ответ:\n{generated_reply}"
        )
        cb_base = f"rev:{mp}:{rv['review_id']}"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Отправить",      callback_data=f"{cb_base}:approve"),
            InlineKeyboardButton("✏️ Редактировать",  callback_data=f"{cb_base}:edit"),
            InlineKeyboardButton("🚫 Пропустить",     callback_data=f"{cb_base}:skip"),
        ]])
        await self._notify_user(chat_id, text, reply_markup=keyboard)

    # ------------------------------------------------------------------ #
    #  Callback — отзывы                                                   #
    # ------------------------------------------------------------------ #

    async def _handle_review_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()

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
            await query.edit_message_text(query.message.text + "\n\n✏️ Напишите ваш вариант ответа:")
            await self._redis_set(f"pending_edit:{chat_id}", f"{mp}:{review_id}", ttl=600)

        elif action == "skip":
            await update_review_status(mp, review_id, "skipped")
            await query.edit_message_text(query.message.text + "\n\n🚫 Пропущено.")

    async def _handle_edit_reply(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        chat_id = update.effective_chat.id
        pending = await self._redis_get(f"pending_edit:{chat_id}")
        if not pending:
            return

        await self._redis_set(f"pending_edit:{chat_id}", "", ttl=1)

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
    #  Команды                                                             #
    # ------------------------------------------------------------------ #

    async def cmd_add_shop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
        await update.message.reply_text(f"✅ Магазин {_MP_LABELS.get(mp, mp)} подключён.")

    async def cmd_shops(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            lines.append(f"• {s.get('shop_name') or label} ({label})")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def cmd_pending(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from db import get_pending_reviews
        reviews = await get_pending_reviews(update.effective_user.id)
        if not reviews:
            await update.message.reply_text("✅ Нет отзывов, ожидающих одобрения.")
            return
        for rv in reviews[:5]:
            await self._notify_pending(
                update.effective_user.id,
                {"marketplace": rv["marketplace"]},
                rv,
                rv.get("generated_reply", ""),
            )

    async def cmd_reviews(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
    #  Регистрация хендлеров                                               #
    # ------------------------------------------------------------------ #

    def _register_extra_handlers(self) -> None:
        # Переопределяем /start — регистрируем ДО базового (строится в build_app)
        self.app.add_handler(CommandHandler("start",      self.cmd_start))
        self.app.add_handler(CommandHandler("add_shop",   self.cmd_add_shop))
        self.app.add_handler(CommandHandler("shops",      self.cmd_shops))
        self.app.add_handler(CommandHandler("pending",    self.cmd_pending))
        self.app.add_handler(CommandHandler("reviews",    self.cmd_reviews))
        self.app.add_handler(
            CallbackQueryHandler(self._handle_onboard_callback, pattern=r"^onboard:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_review_callback, pattern=r"^rev:")
        )
        # group=1: обработка кастомного редактирования ответа
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_edit_reply),
            group=1,
        )
        # group=2: онбординг (ниже pending_edit)
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_onboard_text),
            group=2,
        )
