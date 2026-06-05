from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from loguru import logger
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
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

# Клавиатура с двумя постоянными кнопками действий
def _static_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура без pending-кнопки (fallback когда chat_id недоступен)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("▶️ Проверить отзывы сейчас", callback_data="onboard:run_now"),
        InlineKeyboardButton("📊 Статистика",               callback_data="onboard:stats"),
    ]])


class MaxAgent(BaseAgent):
    name = "Макс"
    agent_key = "max"
    role = "Менеджер отзывов"
    emoji = "🛒"
    system_prompt = MAX_SYSTEM

    def __init__(self) -> None:
        super().__init__(config.MAX_BOT_TOKEN)

    # ------------------------------------------------------------------ #
    #  Запуск — устанавливаем меню бота только с /start                   #
    # ------------------------------------------------------------------ #

    async def start_polling_async(self) -> None:
        await super().start_polling_async()
        try:
            await self.app.bot.set_my_commands([
                BotCommand("start", "Управление отзывами"),
            ])
            logger.info("[Макс] BotCommand menu установлен: /start only")
        except Exception as e:
            logger.warning(f"[Макс] set_my_commands error: {e}")

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
    #  Статус + кнопки действий                                           #
    # ------------------------------------------------------------------ #

    def _status_text(self, shops: list[dict]) -> str:
        connected = {s["marketplace"] for s in shops}
        wb_ok   = "wb"   in connected
        ozon_ok = "ozon" in connected
        lines = [
            "🟣 Wildberries — подключён" if wb_ok   else "🟣 Wildberries — не подключён",
            "🔵 Ozon — подключён"        if ozon_ok else "🔵 Ozon — не подключён",
        ]
        return "\n".join(lines)

    async def _build_keyboard(self, chat_id: int) -> InlineKeyboardMarkup:
        """Динамическая клавиатура: кнопка pending появляется только если есть ожидающие."""
        from db import get_pending_reviews
        pending = await get_pending_reviews(chat_id)
        count = len(pending)
        row1 = [InlineKeyboardButton("▶️ Проверить отзывы сейчас", callback_data="onboard:run_now")]
        if count > 0:
            row1.append(InlineKeyboardButton(f"📬 Отзывы ({count})", callback_data="onboard:show_pending"))
        row2 = [InlineKeyboardButton("📊 Статистика", callback_data="onboard:stats")]
        return InlineKeyboardMarkup([row1, row2])

    async def _send_status_with_buttons(
        self, chat_id: int, shops: list[dict], message_method
    ) -> None:
        """Показать статус площадок + динамические кнопки действий."""
        text = "👋 Вот твои магазины:\n" + self._status_text(shops)
        keyboard = await self._build_keyboard(chat_id)
        await message_method(text, reply_markup=keyboard)

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

    async def _send_finish(self, chat_id: int, connected: list[str]) -> None:
        """Финал онбординга — показать статус с кнопками."""
        from db import get_marketplace_shops
        shops = await get_marketplace_shops(chat_id)
        labels = " и ".join(_MP_LABELS.get(mp, mp) for mp in connected)
        intro = f"✅ Готово! Подключено: {labels}\nБуду проверять отзывы в 09:00, 14:00 и 20:00 МСК.\n\n"
        status = self._status_text(shops)
        await self._notify_user(
            chat_id,
            intro + status,
            reply_markup=await self._build_keyboard(chat_id),
        )

    # ------------------------------------------------------------------ #
    #  /start                                                               #
    # ------------------------------------------------------------------ #

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_user.id

        from db import get_marketplace_shops
        shops = await get_marketplace_shops(chat_id)
        if shops:
            await self._clear_onboard(chat_id)
            await self._send_status_with_buttons(chat_id, shops, update.message.reply_text)
            return

        await self._set_onboard(chat_id, {"step": "choose_platform", "data": {}})
        await self._send_platform_choice(chat_id)

    # ------------------------------------------------------------------ #
    #  Callback — онбординг                                                #
    # ------------------------------------------------------------------ #

    async def _handle_onboard_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        chat_id = query.message.chat_id
        action = query.data.split(":", 1)[1]

        if action == "run_now":
            await query.answer()
            await query.edit_message_text("▶️ Запускаю проверку…")
            results = await self.process_reviews(chat_id)

            # Формируем итоговое сообщение
            _EMOJI = {"wb": "🟣 Wildberries", "ozon": "🔵 Ozon"}
            total_found = sum(s.get("found", 0) for s in results.values())

            if not results or total_found == 0:
                summary = "✅ Новых отзывов нет."
            else:
                lines = ["✅ Проверка завершена.\n"]
                any_pending = False
                for mp, s in results.items():
                    label = _EMOJI.get(mp, mp)
                    lines.append(f"{label}: найдено {s['found']}")
                    if s["found"]:
                        if s["auto_replied"]:
                            lines.append(f"  └ отвечено автоматически: {s['auto_replied']}")
                        if s["pending"]:
                            lines.append(f"  └ ждут одобрения: {s['pending']}")
                            any_pending = True
                        if s["errors"]:
                            lines.append(f"  └ ошибок: {s['errors']}")
                    lines.append("")
                if any_pending:
                    lines.append("📬 Отзывы ожидающие ответа отправлены выше ↑")
                summary = "\n".join(lines).rstrip()

            await self._notify_user(chat_id, summary, reply_markup=await self._build_keyboard(chat_id))
            return

        if action == "stats":
            await query.answer()
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
            stats_text = (
                f"📊 *Отзывы за сегодня:*\n\n"
                f"✅ Автоответ: {row['auto_replied']}\n"
                f"✅ Вручную: {row['replied']}\n"
                f"⏳ Ожидают: {row['pending']}\n"
                f"🚫 Пропущено: {row['skipped']}\n"
                f"📨 Всего: {row['total']}"
            )
            from db import get_marketplace_shops
            shops = await get_marketplace_shops(chat_id)
            status = self._status_text(shops) if shops else ""
            await query.edit_message_text(
                stats_text + ("\n\n" + status if status else ""),
                parse_mode="Markdown",
                reply_markup=await self._build_keyboard(chat_id),
            )
            return

        if action == "show_pending":
            await query.answer()
            from db import get_pending_reviews
            reviews = await get_pending_reviews(chat_id)
            if not reviews:
                await query.answer("Нет отзывов, ожидающих одобрения", show_alert=True)
                return
            for rv in reviews[:5]:
                await self._notify_pending(
                    chat_id,
                    {"marketplace": rv["marketplace"]},
                    rv,
                    rv.get("generated_reply", ""),
                )
            return

        if action == "add_wb":
            await query.answer()
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
            await query.answer()
            await self._set_onboard(chat_id, {"step": "ozon_client_id", "data": {}})
            await query.edit_message_text(
                "🔵 Подключаем Ozon.\n\n"
                "Отправь Client-Id магазина Ozon.\n\n"
                "📌 Где взять:\n"
                "seller.ozon.ru → Настройки → API ключи"
            )
            return

        if action == "run_later":
            await query.answer()
            await query.edit_message_text(query.message.text + "\n\n👍 Хорошо, проверю по расписанию.")
            return

        # Выбор площадки (choose_platform)
        await query.answer()
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
            await self._set_onboard(chat_id, {"step": "wb_token", "data": {"need_ozon": True}})
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
            ok = await WBClient(text).check_connection()
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
            ok = await OzonClient(text, client_id).check_connection()
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
            connected = (["wb"] if data.get("wb_connected") else []) + ["ozon"]
            await self._clear_onboard(chat_id)
            await self._send_finish(chat_id, connected)

    # ------------------------------------------------------------------ #
    #  Генерация ответа                                                    #
    # ------------------------------------------------------------------ #

    async def _generate_reply(self, product_name: str, rating: int, text: str, author: str) -> str:
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

    async def _send_to_marketplace(self, shop: dict, review_id: str, reply_text: str) -> bool:
        from tools.marketplace import make_client
        try:
            return await make_client(shop).send_reply(review_id, reply_text)
        except Exception as e:
            logger.error(f"[Макс] send_to_marketplace error: {e}")
            return False

    # ------------------------------------------------------------------ #
    #  Основная логика обработки отзывов                                   #
    # ------------------------------------------------------------------ #

    async def process_reviews(self, chat_id: int) -> dict:
        """Обработать отзывы. Возвращает итоги по каждой площадке."""
        from db import get_marketplace_shops, save_review, update_review_status
        from tools.marketplace import make_client

        shops = await get_marketplace_shops(chat_id)
        results: dict = {}
        if not shops:
            return results

        since = datetime.now(_UTC) - timedelta(hours=8)

        for shop in shops:
            mp = shop["marketplace"]
            mp_label = _MP_LABELS.get(mp, mp)
            stats = {"found": 0, "auto_replied": 0, "pending": 0, "errors": 0}

            try:
                reviews = await make_client(shop).get_new_reviews(since)
                logger.info(f"[Макс] {mp_label}: {len(reviews)} отзывов для chat={chat_id}")
            except Exception as e:
                logger.error(f"[Макс] get_new_reviews {mp_label}: {e}")
                stats["errors"] += 1
                results[mp] = stats
                continue

            for rv in reviews:
                is_new = await save_review(
                    marketplace=mp,
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

                stats["found"] += 1
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
                    stats["errors"] += 1

                await update_review_status(
                    mp, rv["review_id"],
                    status="pending_approval" if rating <= 2 else "new",
                    generated_reply=reply,
                )

                if rating <= 2:
                    await self._notify_pending(chat_id, shop, rv, reply)
                    stats["pending"] += 1
                else:
                    ok = await self._send_to_marketplace(shop, rv["review_id"], reply)
                    if ok:
                        await update_review_status(
                            mp, rv["review_id"],
                            status="auto_replied",
                            final_reply=reply,
                        )
                        stats["auto_replied"] += 1
                        logger.info(f"[Макс] review={rv['review_id'][:8]} rating={rating} → auto_replied")
                    else:
                        stats["errors"] += 1
                        logger.error(
                            f"[Макс] send_reply failed: mp={mp} "
                            f"review={rv['review_id'][:8]} rating={rating} — статус остаётся 'new'"
                        )

            results[mp] = stats

        return results

    async def _notify_pending(self, chat_id: int, shop: dict, rv: dict, generated_reply: str) -> None:
        mp = shop["marketplace"]
        rating = rv.get("rating", 0)
        text = (
            f"{'⭐️' * rating} ({rating}/5) — {rv.get('product_name', 'товар')}\n"
            f"👤 {rv.get('author', 'Покупатель')}\n\n"
            f"💬 {rv.get('text') or '(без текста)'}\n\n"
            f"📝 Предлагаемый ответ:\n{generated_reply}"
        )
        cb_base = f"rev:{mp}:{rv['review_id']}"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Отправить",     callback_data=f"{cb_base}:approve"),
            InlineKeyboardButton("✏️ Редактировать", callback_data=f"{cb_base}:edit"),
            InlineKeyboardButton("🚫 Пропустить",    callback_data=f"{cb_base}:skip"),
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
                shop = next(
                    (s for s in await get_marketplace_shops(chat_id) if s["marketplace"] == mp),
                    None,
                )
                if shop and await self._send_to_marketplace(shop, review_id, reply_text):
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
        shop = next(
            (s for s in await get_marketplace_shops(chat_id) if s["marketplace"] == mp),
            None,
        )
        if shop and await self._send_to_marketplace(shop, review_id, reply_text):
            await update_review_status(mp, review_id, "replied", final_reply=reply_text)
            await update.message.reply_text("✅ Ваш ответ отправлен на площадку.")
            return
        await update.message.reply_text("❌ Не удалось отправить ответ.")

    # ------------------------------------------------------------------ #
    #  Команды (рабочие, но не в BotCommand меню)                         #
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
        mp, token = args[0].lower(), args[1]
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
            await update.message.reply_text("Магазинов нет. Используй /start чтобы подключить.")
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
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_edit_reply),
            group=1,
        )
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_onboard_text),
            group=2,
        )
