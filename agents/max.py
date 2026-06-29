from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from loguru import logger
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import config
from utils.tg_format import bold, escape
from utils.tg_rich import send_rich_or_fallback as _send_rich
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

_QUESTION_PROMPT = """\
Ты — вежливый менеджер магазина на маркетплейсе. Напиши ответ на вопрос покупателя о товаре.

Товар: {product_name}
Вопрос: {question_text}

Требования:
- Ответь по существу вопроса, информативно и честно
- Если вопрос о характеристиках — укажи конкретные данные
- Если вопрос о доставке/возврате — направь к политике площадки
- Тон: дружелюбный, живой, профессиональный
- Длина: 2-4 предложения
- Язык: русский
- НЕ используй: "команда магазина", "мы рады"

Ответь только текстом ответа, без кавычек и пояснений."""

_HELP_TEXT = """\
🤖 <b>Что я умею:</b>

🔔 <b>Отзывы и вопросы</b>
— /pending — ждут ответа (1–2★ и вопросы)
— /reviews — статистика отзывов за сегодня
— "Макс, покажи негативные отзывы за 7 дней"
— "Макс, перепиши этот ответ более извиняющимся тоном: [текст]"

📊 <b>Аналитика</b>
— /dashboard — открыть дашборд продаж
— /shop_kpi — рейтинг продавца, возвраты, штрафы
— /data_status — свежесть данных в БД

🔄 <b>Синхронизация</b>
— /sync — заказы, остатки и отзывы (полный синк)
— /sync_adv — рекламная статистика
— /sync_fin — финансовые отчёты (комиссии, выплаты)
— /sync_funnel — воронка конверсии карточек
— /sync_returns — аналитика возвратов WB + Ozon
— /sync_promotions — акции и акционные кампании

📦 <b>Каталог товаров</b>
— /products — список товаров и себестоимость
— /add — добавить товар в реестр
— /cost — задать себестоимость (WB и Ozon раздельно)
— /map — добавить/обновить маппинг артикулов
— /camp — задать товары для WB рекламной кампании вручную
— /sync_sku — подтянуть Ozon SKU в реестр

⚙️ <b>Управление</b>
— /start — статус магазинов и главное меню
— /cancel — отменить текущее действие\
"""

MAX_SYSTEM = """Ты — Макс, менеджер маркетплейсов WB и Ozon.

Отвечаешь за:
- Отзывы и вопросы покупателей: автоматические и ручные ответы, модерация
- Синхронизацию данных: заказы, остатки, продажи, реклама, финотчёт, воронка, возвраты, карточки, ключевые слова, акции
- Каталог: товары, себестоимость, маппинг артикулов WB/Ozon
- Ценообразование: маржа товаров, рекомендованные цены, применение изменений
- Реклама Ozon: кампании, ставки, акции, создание новых кампаний
- SEO: мониторинг позиций ключевых слов, алерты при падении
- KPI магазина, статус данных, подключение магазинов

Отвечай по-русски, кратко и по делу.

ПРАВИЛА ФОРМАТИРОВАНИЯ (обязательно):
- Форматируй в Rich Markdown для Telegram
- **текст** — заголовки разделов (Статистика, Отзывы, Итого)
- `артикул` — артикулы и ID товаров
- > текст — краткий итог
- Эмодзи в начале разделов (🟣 🔵 📊 ⭐)
- Спецсимволы . ! ( ) - = писать как есть, без экранирования
- Списки через эмодзи или дефис
- Длина ответа до 30 000 символов, можно делать подробные таблицы
- НЕ используй HTML-теги: никаких <b>, <i>, <code>

Пример правильного формата статистики:
📊 **Статистика за 7 дней**

🟣 **Wildberries**
\- Отзывов: 10
\- Средний рейтинг: ⭐ 4\.50
\- Автоответов: 7
\- Ручных: 3
\- Без ответа: 0

🔵 **Ozon** — нет данных

Пример списка отзывов:
🟣 **Последние отзывы WB**

⭐⭐⭐⭐⭐ Корм сухой — 🤖 Автоответ
⭐⭐ Лакомства для животных — ✍️ Ручной

Если пользователь спрашивает что ты умеешь, какие команды, помощь — \
отвечай следующим текстом без использования инструментов:

""" + _HELP_TEXT + '"""'

_MP_LABELS = {"wb": "Wildberries", "ozon": "Ozon"}
_ONBOARD_TTL = 60 * 60 * 24  # 24 часа

WB_CLUSTERS: dict[str, list[str]] = {
    "Центральный": [
        "Москва", "МО", "Коледино", "Подольск", "Электросталь", "Краснодар",
        "Тула", "Владимир", "Иваново", "Ярославль", "Рязань", "Калуга",
        "Брянск", "Орёл", "Смоленск", "Тверь", "Белгород", "Воронеж",
        "Липецк", "Курск", "Тамбов", "Внуково", "Истра", "Коломна",
    ],
    "Северо-Западный": [
        "Санкт-Петербург", "СПб", "Петербург", "Шушары", "Красный Бор",
        "Мурманск", "Архангельск", "Вологда", "Псков", "Новгород",
        "Петрозаводск", "Калининград", "Сыктывкар",
    ],
    "Южный и Северо-Кавказский": [
        "Ростов", "Невинномысск", "Ставрополь", "Астрахань",
        "Волгоград", "Махачкала", "Владикавказ", "Нальчик", "Майкоп",
        "Элиста", "Симферополь",
    ],
    "Приволжский": [
        "Казань", "Нижний", "Самара", "Уфа", "Пермь", "Саратов",
        "Ульяновск", "Оренбург", "Киров", "Чебоксары", "Ижевск",
        "Йошкар", "Пенза", "Тольятти",
    ],
    "Уральский": [
        "Екатеринбург", "Челябинск", "Тюмень", "Курган", "Сургут",
        "Нижневартовск", "Магнитогорск",
    ],
    "Дальневосточный и Сибирский": [
        "Новосибирск", "Омск", "Красноярск", "Иркутск", "Кемерово",
        "Новокузнецк", "Барнаул", "Томск", "Хабаровск", "Владивосток",
        "Артём", "Якутск", "Благовещенск", "Чита", "Юрга", "Улан-Удэ",
    ],
    "Беларусь": ["Минск"],
    "Казахстан": ["Алматы", "Астана", "Алмата"],
    "Армения": ["Ереван"],
    "Грузия": ["Тбилиси"],
    "Киргизия": ["Бишкек"],
}


def _get_cluster(warehouse_name: str) -> str:
    wh = warehouse_name or ""
    for cluster, keywords in WB_CLUSTERS.items():
        if any(kw.lower() in wh.lower() for kw in keywords):
            return cluster
    return "Прочие"


# Маппинг регионов ПОКУПАТЕЛЕЙ (marketplace_orders.region) → кластер WB.
# Отличается от WB_CLUSTERS (маппинг складов): один кластер обслуживает
# складами ряд регионов-покупателей. Кластеры те же, что и в WB_CLUSTERS,
# чтобы можно было сопоставить спрос по кластеру с остатком по кластеру.
WB_REGION_CLUSTERS: dict[str, list[str]] = {
    "Центральный": [
        "Москв", "Московск", "Краснодарск", "Тульск", "Владимирск",
        "Ивановск", "Ярославск", "Рязанск", "Калужск", "Брянск",
        "Орловск", "Смоленск", "Тверск", "Белгородск", "Воронежск",
        "Липецк", "Курск", "Тамбов",
    ],
    "Северо-Западный": [
        "Санкт-Петербург", "Ленинградск", "Мурманск", "Архангельск",
        "Вологодск", "Псковск", "Новгородск", "Карели", "Коми",
        "Калинингр",
    ],
    "Южный и Северо-Кавказский": [
        "Ростовск", "Ставропольск", "Астраханск", "Волгоградск",
        "Дагестан", "Осети", "Кабардин", "Адыге", "Калмык",
        "Симферополь", "Крым", "Чечен", "Ингушети", "Майкоп",
    ],
    "Приволжский": [
        "Татарстан", "Нижегородск", "Самарск", "Башкорт", "Пермск",
        "Саратовск", "Ульяновск", "Оренбургск", "Кировск", "Чуваш",
        "Удмуртск", "Пензенск", "Мордови", "Марий", "Тольятти",
    ],
    "Уральский": [
        "Свердловск", "Челябинск", "Тюменск", "Курганск", "Ханты", "Ямало",
    ],
    "Дальневосточный и Сибирский": [
        "Новосибирск", "Омск", "Красноярск", "Иркутск", "Кемеровск",
        "Кузбасс", "Алтайск", "Томск", "Хабаровск", "Приморск",
        "Якути", "Амурск", "Забайкальск", "Бурятия", "Сахалинск",
        "Магаданск", "Камчатск",
    ],
    "Беларусь": ["Беларусь", "Минск"],
    "Казахстан": ["Казахстан", "Алматы", "Астана"],
    "Армения": ["Армени", "Ереван"],
    "Грузия": ["Грузи", "Тбилиси"],
    "Киргизия": ["Киргизи", "Бишкек"],
}


def _get_cluster_from_region(region: str) -> str:
    """Маппинг региона покупателя → кластер WB (для расчёта per-cluster спроса)."""
    r = region or ""
    for cluster, keywords in WB_REGION_CLUSTERS.items():
        if any(kw.lower() in r.lower() for kw in keywords):
            return cluster
    return "Прочие"


OZON_CLUSTERS: dict[str, list[str]] = {
    "Москва и МО": ["Москва", "МО", "Жуковский", "Ногинск", "Пушкино", "Тверь", "Хоругвино"],
    "Северо-Западный": ["Санкт-Петербург", "СПб", "Петербург", "Шушары", "Калининград", "Мурманск", "Архангельск"],
    "Центральный": ["Воронеж", "Белгород", "Курск", "Тула", "Рязань", "Ярославль", "Иваново"],
    "Южный": ["Краснодар", "Ростов", "Астрахань", "Волгоград"],
    "Кавказский": ["Ставрополь", "Махачкала", "Владикавказ", "Нальчик"],
    "Приволжский": ["Казань", "Нижний", "Самара", "Уфа", "Пермь", "Саратов", "Ульяновск", "Оренбург"],
    "Уральский": ["Екатеринбург", "Челябинск", "Тюмень"],
    "Сибирский": ["Новосибирск", "Омск", "Красноярск", "Иркутск", "Кемерово", "Барнаул"],
    "Дальневосточный": ["Хабаровск", "Владивосток", "Якутск"],
}


def _get_ozon_cluster(warehouse_name: str) -> str:
    wh = warehouse_name or ""
    for cluster, keywords in OZON_CLUSTERS.items():
        if any(kw.lower() in wh.lower() for kw in keywords):
            return cluster
    return "Прочие"

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

    # start_polling_async не переопределяем — BaseAgent._post_init уже вызывает _bot_commands()

    # ------------------------------------------------------------------ #
    #  handle_task (заглушка)                                              #
    # ------------------------------------------------------------------ #

    async def handle_task(self, task: str, from_agent: str = "user") -> str:
        import json as _json
        try:
            cmd = _json.loads(task)
            if isinstance(cmd, dict) and cmd.get("action") == "upload_photo":
                return await self._upload_infographic(
                    article=cmd["article"],
                    marketplace=cmd["marketplace"],
                    file_id=cmd["file_id"],
                    name=cmd.get("name", cmd["article"]),
                )
        except (ValueError, TypeError, KeyError):
            pass
        if task.startswith("__"):
            return await self._dispatch_queue_task(task, self._current_chat_id or 0)

        # Маппинг натуральных фраз (от Марты/цепочки) на keyword-команды
        t = task.lower()
        chat_id = self._current_chat_id or 0
        if any(w in t for w in ("отзыв", "review", "pending", "feedback", "модерац")):
            return await self._dispatch_queue_task("__pending__", chat_id)
        if any(w in t for w in ("синхрониз", "sync", "заказ", "остатк", "обнов")):
            return await self._dispatch_queue_task("__sync__", chat_id)
        if any(w in t for w in ("статус данных", "data_status", "свежесть данных")):
            return await self._dispatch_queue_task("__data_status__", chat_id)
        if any(w in t for w in ("маржа", "margin", "рентабельность")):
            return await self._dispatch_queue_task("__margin__", chat_id)
        if any(w in t for w in ("товар", "каталог", "артикул", "себестоимость")):
            return await self._dispatch_queue_task("__products__", chat_id)
        if any(w in t for w in ("kpi", "рейтинг магазина", "рейтинг продавца", "штраф")):
            return await self._dispatch_queue_task("__shop_kpi__", chat_id)

        return await self.think(task, chat_id=chat_id, is_task=True)

    async def _dispatch_queue_task(self, task: str, chat_id: int) -> str:
        """Dispatch __keyword__ prefixed tasks coming from Marta proxy."""
        if task == "__shops__":
            return await self._shops_text(chat_id)
        if task == "__products__":
            return await self._catalog_text(chat_id)
        if task == "__data_status__":
            return await self._data_status_text(chat_id)
        if task == "__shop_kpi__":
            return await self._shop_kpi_text(chat_id)
        if task == "__seo_check__":
            return await self._seo_check_text(chat_id)
        if task == "__bid_adjust__":
            return await self._bid_adjust_text(chat_id)
        if task == "__campaigns__":
            return await self._campaigns_text(chat_id)
        if task == "__promotions__":
            return "📌 Используй /promotions для анализа акций Ozon с кнопками."
        if task == "__new_campaign__":
            return "📌 Используй /new_campaign для создания кампании из топ-товаров."
        if task == "__margin__" or task.startswith("__margin__ "):
            return await self._margin_check_text(chat_id)
        if task == "__sync__":
            await self.sync_marketplace_data(chat_id)
            return "✅ Данные синхронизированы: заказы, остатки, продажи. Запусти /report у Питера для анализа."
        if task == "__sync_fin__":
            res = await self.sync_financial_report(chat_id)
            return f"✅ Финансовый отчёт: WB {res.get('wb', 0)} зап., Ozon {res.get('ozon', 0)} зап."
        if task == "__sync_adv__":
            await self.sync_ad_stats(chat_id)
            await self._check_drr_alerts(chat_id)
            return "✅ Рекламная статистика обновлена. Если ДРР > 25% — алерт уже отправлен."
        if task == "__sync_funnel__":
            res = await self.sync_funnel(chat_id)
            return f"✅ Воронка: WB {res.get('wb', 0)} зап., Ozon {res.get('ozon', 0)} зап."
        if task == "__sync_returns__":
            res = await self.sync_returns(chat_id)
            return f"✅ Возвраты: WB {res.get('wb', 0)} зап., Ozon {res.get('ozon', 0)} зап."
        if task == "__sync_cards__":
            res = await self.sync_cards(chat_id)
            return f"✅ Карточки: WB {res.get('wb', 0)} зап., Ozon {res.get('ozon', 0)} зап."
        if task == "__sync_keywords__":
            return "⚠️ WB закрыл API ключевых слов (404). Позиции недоступны — следи вручную в WB Analytics."
        if task == "__sync_sku__":
            return "🔄 /sync_sku требует мастера настройки — запусти напрямую у Макса."
        if task == "__questions__":
            results = await self.process_questions(chat_id)
            if not results:
                return "❓ Нет магазинов для проверки вопросов."
            _MP = {"wb": "WB", "ozon": "Ozon"}
            parts = [
                f"{_MP.get(mp, mp)}: {s.get('found', 0)} новых, {s.get('errors', 0)} ошибок"
                for mp, s in results.items()
            ]
            total_new = sum(s.get("found", 0) for s in results.values())
            total_pending = sum(s.get("pending", 0) for s in results.values())
            # Дополнительно: force-отправить pending из БД, которые Redis мог заблокировать
            from db import get_pending_questions, get_marketplace_shops
            pending_db = await get_pending_questions(chat_id)
            if pending_db:
                shops = await get_marketplace_shops(chat_id)
                for q in pending_db:
                    mp = q["marketplace"]
                    shop = next((s for s in shops if s["marketplace"] == mp), None)
                    if not shop:
                        continue
                    await self._notify_pending_question(
                        chat_id, shop,
                        {"question_id": q["question_id"], "question_text": q.get("question_text"),
                         "product_name": q.get("product_name"), "created_at": q.get("created_at")},
                        q.get("generated_answer", ""),
                    )
                    notif_key = f"q_notified:{mp}:{q['question_id']}"
                    await self._redis_set(notif_key, "1", ttl=7200)
                    total_pending += 1
            prefix = f"❓ {total_new} новых вопросов" if total_new else "✅ Новых вопросов нет"
            suffix = f", {total_pending} ожидают ответа" if total_pending else ""
            return f"{prefix}{suffix}. {' | '.join(parts)}"
        if task == "__pending__":
            from db import get_pending_reviews, get_marketplace_shops
            reviews = await get_pending_reviews(chat_id)
            if not reviews:
                return "✅ Нет отзывов, ожидающих одобрения."
            shops = await get_marketplace_shops(chat_id)
            shown = 0
            for rv in reviews[:5]:
                mp = rv["marketplace"]
                shop = next((s for s in shops if s["marketplace"] == mp), None)
                if shop:
                    await self._notify_pending(chat_id, shop, rv, rv.get("generated_reply", ""))
                    shown += 1
            return f"⏳ Показал {shown} отзывов на модерацию. Всего ожидает: {len(reviews)}."
        return await self.think(task, chat_id=0, is_task=True)

    async def _upload_infographic(
        self, article: str, marketplace: str, file_id: str, name: str
    ) -> str:
        """Скачать фото из Telegram и загрузить инфографику на маркетплейс."""
        import aiohttp as _aio
        from config import config
        from db import get_pool, get_marketplace_shops
        from tools.marketplace import WBClient

        chat_id = self._current_chat_id or 0
        bot_token = config.MARTA_BOT_TOKEN
        if not bot_token:
            return "❌ MARTA_BOT_TOKEN не настроен"

        # Скачать из Telegram
        try:
            async with _aio.ClientSession() as session:
                async with session.get(
                    f"https://api.telegram.org/bot{bot_token}/getFile",
                    params={"file_id": file_id},
                ) as r:
                    data = await r.json()
                file_path = data["result"]["file_path"]
                async with session.get(
                    f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
                ) as r:
                    photo_bytes = await r.read()
        except Exception as e:
            return f"❌ Ошибка скачивания из Telegram: {e}"

        if marketplace == "wb":
            shops = await get_marketplace_shops(chat_id)
            wb_shop = next((s for s in shops if s["marketplace"] == "wb"), None)
            if not wb_shop:
                return "❌ Магазин WB не найден — добавь токен через /setup"

            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT wb_nm_id FROM product_mapping
                    WHERE chat_id = $1
                      AND (wb_article = $2 OR display_name ILIKE $3)
                    LIMIT 1
                """, chat_id, article, f"%{article}%")

            if not row or not row["wb_nm_id"]:
                return (
                    f"❌ nm_id не найден для артикула «{article}».\n"
                    f"Запусти /sync чтобы обновить маппинг товаров."
                )

            nm_id = row["wb_nm_id"]
            wb = WBClient(wb_shop["api_token"])
            ok = await wb.upload_product_photo(nm_id, photo_bytes)
            if not ok:
                return f"❌ WB вернул ошибку при загрузке фото для «{name}»"

            async with pool.acquire() as conn:
                await conn.execute("""
                    UPDATE product_mapping
                    SET infographic_updated_at = NOW()
                    WHERE chat_id = $1 AND wb_nm_id = $2
                """, chat_id, nm_id)

            return (
                f"✅ Инфографика загружена на WB: *{name}*\n"
                f"Питер отслеживает CTR — результат через 14 дней."
            )

        return f"❌ Маркетплейс {marketplace} пока не поддерживается для авто-загрузки"

    # ------------------------------------------------------------------ #
    #  handle_message — блокируем Claude во время онбординга              #
    # ------------------------------------------------------------------ #

    async def handle_voice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
        from telegram import Chat
        msg = update.message
        if not msg:
            return None

        is_group = (
            update.effective_chat
            and update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP)
        )
        if is_group:
            reply = msg.reply_to_message
            is_reply_to_bot = bool(
                reply and reply.from_user and reply.from_user.id == context.bot.id
            )
            if not is_reply_to_bot:
                logger.debug("[max:voice] group voice — not reply-to-bot, skip transcription")
                return None

        transcribed = await super().handle_voice(update, context)
        if transcribed and is_group:
            await self._handle_group_message(update, context, override_text=transcribed)
        return transcribed

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *, override_text: str | None = None) -> None:
        from telegram import Chat
        if update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            return
        chat_id = update.effective_chat.id
        if await self._redis_get(f"catalog_add:{chat_id}"):
            await self._handle_catalog_add_text(update, context)
            return
        if await self._redis_get(f"catalog_cost:{chat_id}"):
            await self._handle_catalog_cost_text(update, context)
            return
        state = await self._get_onboard(chat_id)
        if state and state.get("step") not in (None, "done"):
            return
        await super().handle_message(update, context, override_text=override_text)

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
        if not shops:
            return "Магазинов нет. Нажми /start чтобы подключить."
        lines = []
        for s in shops:
            emoji = "🟣" if s["marketplace"] == "wb" else "🔵"
            name = s.get("shop_name") or _MP_LABELS.get(s["marketplace"], s["marketplace"])
            lines.append(f"{emoji} {name} — подключён")
        return "\n".join(lines)

    async def _build_keyboard(self, chat_id: int) -> InlineKeyboardMarkup:
        """Главная клавиатура — та же структура что и _MENU_MAIN_KEYBOARD, но с живым счётчиком отзывов."""
        from db import get_pending_reviews, get_marketplace_shops
        pending = await get_pending_reviews(chat_id)
        count = len(pending)
        shops = await get_marketplace_shops(chat_id)
        connected = {s["marketplace"] for s in shops}

        review_label = (
            f"🔔 Отзывы — {count} ждут ответа" if count > 0
            else "🔔 Проверить отзывы"
        )
        _dash_url = (
            f"{config.DASHBOARD_URL}?token={config.DASHBOARD_TOKEN}"
            if config.DASHBOARD_TOKEN else config.DASHBOARD_URL
        )
        _dash_btn = (
            InlineKeyboardButton("📊 Дашборд", web_app=WebAppInfo(url=_dash_url))
            if _dash_url else
            InlineKeyboardButton("📊 Дашборд", callback_data="menu_cat:analytics")
        )
        rows = [
            [InlineKeyboardButton(review_label, callback_data="onboard:run_now")],
            [_dash_btn],
            [
                InlineKeyboardButton("🔄 Синхронизация ▸", callback_data="menu_cat:sync"),
                InlineKeyboardButton("📈 Аналитика ▸",     callback_data="menu_cat:analytics"),
            ],
            [
                InlineKeyboardButton("📦 Товары ▸",        callback_data="menu_cat:products"),
                InlineKeyboardButton("❓ Справка",          callback_data="menu_help"),
            ],
        ]
        rows.append([InlineKeyboardButton("➕ Добавить магазин", callback_data="onboard:add_shop")])
        update_row = []
        if "wb" in connected:
            update_row.append(InlineKeyboardButton("🔑 Токен WB",   callback_data="onboard:update_wb"))
        if "ozon" in connected:
            update_row.append(InlineKeyboardButton("🔑 Токен Ozon", callback_data="onboard:update_ozon"))
        if update_row:
            rows.append(update_row)
        return InlineKeyboardMarkup(rows)

    async def _send_status_with_buttons(
        self, chat_id: int, shops: list[dict], message_method
    ) -> None:
        """Показать статус площадок + динамические кнопки действий."""
        count = len(shops)
        shop_word = "магазин" if count == 1 else "магазина" if count < 5 else "магазинов"
        text = f"👋 Активных {shop_word}: {count}\n\n" + self._status_text(shops)
        keyboard = await self._build_keyboard(chat_id)
        await message_method(text, reply_markup=keyboard)

    # ------------------------------------------------------------------ #
    #  Онбординг — вспомогательные отправки                               #
    # ------------------------------------------------------------------ #

    async def _send_platform_choice(self, chat_id: int) -> None:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🟣 Wildberries", callback_data="onboard:wb"),
                InlineKeyboardButton("🔵 Ozon",        callback_data="onboard:ozon"),
            ],
            [InlineKeyboardButton("🟣+🔵 Подключить обе", callback_data="onboard:both")],
        ])
        await self._notify_user(
            chat_id,
            "👋 Привет! Я **Макс** — AI-менеджер маркетплейсов.\n\n"
            "**Что делаю автоматически:**\n"
            "• Отвечаю на отзывы 3 раза в день (09:00, 14:00, 20:00 МСК)\n"
            "• На негативные 1–2⭐ — жду твоего одобрения, остальные публикую сам\n"
            "• Слежу за остатками, продажами и рекламной статистикой\n\n"
            "На подключение — 2 минуты. Какую площадку начнём?",
            reply_markup=keyboard,
        )

    async def _send_finish(self, chat_id: int, connected: list[str]) -> None:
        """Финал онбординга — показать статус с кнопками."""
        from db import get_marketplace_shops
        shops = await get_marketplace_shops(chat_id)
        labels = " и ".join(_MP_LABELS.get(mp, mp) for mp in connected)
        status = self._status_text(shops)
        text = (
            f"🎉 **{labels} подключён!**\n\n"
            f"{status}\n\n"
            "**Что происходит автоматически:**\n"
            "• Отзывы проверяются в 09:00, 14:00 и 20:00 МСК\n"
            "• 3–5⭐ — публикую ответ сам\n"
            "• 1–2⭐ — пришлю тебе на одобрение\n\n"
            "**С чего начать:**\n"
            "1. /sync — загрузить данные магазина\n"
            "2. /products — добавить себестоимость товаров\n"
            "3. /dashboard — посмотреть аналитику"
        )
        await self._notify_user(
            chat_id,
            text,
            reply_markup=await self._build_keyboard(chat_id),
        )

    # ------------------------------------------------------------------ #
    #  /start                                                               #
    # ------------------------------------------------------------------ #

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from telegram import Chat, MenuButtonCommands
        if update.effective_chat and update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            logger.debug(f"[max:handler] cmd_start вызван из группы — текст: {update.message.text[:50] if update.message and update.message.text else '?'}")
        chat_id = update.effective_user.id

        # Сброс per-chat override MenuButtonWebApp → стандартный список команд
        try:
            await context.bot.set_chat_menu_button(
                chat_id=chat_id,
                menu_button=MenuButtonCommands(),
            )
        except Exception:
            pass

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
                f"📊 <b>Отзывы за сегодня:</b>\n\n"
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
                parse_mode="HTML",
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

        if action == "help":
            await query.answer()
            await query.message.reply_text(_HELP_TEXT, parse_mode="HTML")
            return

        if action == "daily_summary":
            await query.answer()
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("📊 Продажи",        callback_data="onboard:summary_sales"),
                InlineKeyboardButton("🟣 Остатки WB",     callback_data="onboard:summary_wb_stocks"),
            ], [
                InlineKeyboardButton("🔵 Остатки Ozon",   callback_data="onboard:summary_ozon_stocks"),
                InlineKeyboardButton("📋 Всё сразу",      callback_data="onboard:summary_all"),
            ]])
            await query.message.reply_text("📦 <b>Сводка магазина</b>\nЧто показать?", parse_mode="HTML", reply_markup=keyboard)
            return

        if action in ("summary_sales", "summary_wb_stocks", "summary_ozon_stocks", "summary_all"):
            await query.answer()
            from db import get_marketplace_shops
            owner_shops = await get_marketplace_shops(query.from_user.id)
            owner_chat_id = owner_shops[0]["chat_id"] if owner_shops else query.from_user.id
            target_chat_id = query.message.chat.id

            await query.message.reply_text("⏳ Собираю данные…")
            await self.sync_marketplace_data(owner_chat_id)

            if action in ("summary_sales", "summary_all"):
                await self._send_sales_summary(owner_chat_id, target_chat_id, context.bot)
            if action in ("summary_wb_stocks", "summary_all"):
                await self._send_stocks("wb", owner_chat_id, target_chat_id, context.bot)
            if action in ("summary_ozon_stocks", "summary_all"):
                await self._send_stocks("ozon", owner_chat_id, target_chat_id, context.bot)
            return

        if action == "sync":
            await query.answer()
            await query.message.reply_text("🔄 Синхронизирую данные…")
            await self.sync_marketplace_data(chat_id)
            await self._notify_user(
                chat_id,
                "✅ Синхронизация завершена.",
                reply_markup=await self._build_keyboard(chat_id),
            )
            return

        if action == "update_wb":
            await query.answer()
            await self._set_onboard(chat_id, {"step": "wb_token", "data": {"updating": True}})
            await query.edit_message_text("Отправь новый API токен Wildberries:")
            return

        if action == "update_ozon":
            await query.answer()
            await self._set_onboard(chat_id, {"step": "ozon_client_id", "data": {"updating": True}})
            await query.edit_message_text("Отправь Client-Id магазина Ozon:")
            return

        if action == "add_shop":
            await query.answer()
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🟣 Wildberries", callback_data="onboard:add_wb"),
                InlineKeyboardButton("🔵 Ozon",        callback_data="onboard:add_ozon"),
            ]])
            await query.message.reply_text("Какую площадку добавить?", reply_markup=keyboard)
            return

        if action == "add_wb":
            await query.answer()
            await self._set_onboard(chat_id, {"step": "wb_token", "data": {}})
            await query.edit_message_text(
                "🟣 Подключаем Wildberries.\n\n"
                "Шаг 1 из 2 — API токен\n\n"
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
                "Шаг 1 из 3 — Client-Id\n\n"
                "Отправь Client-Id магазина Ozon.\n\n"
                "📌 Где взять:\n"
                "seller.ozon.ru → Настройки → API ключи → Seller API"
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
                "Шаг 1 из 2 — API токен\n\n"
                "Отправь API токен.\n\n"
                "📌 Где взять:\n"
                "seller.wildberries.ru → Настройки → Доступ к API → "
                "создать токен с категорией Отзывы"
            )
        elif action == "ozon":
            await self._set_onboard(chat_id, {"step": "ozon_client_id", "data": {}})
            await query.edit_message_text(
                "🔵 Ozon выбран.\n\n"
                "Шаг 1 из 3 — Client-Id\n\n"
                "Отправь Client-Id магазина Ozon.\n\n"
                "📌 Где взять:\n"
                "seller.ozon.ru → Настройки → API ключи → Seller API"
            )
        elif action == "both":
            await self._set_onboard(chat_id, {"step": "wb_token", "data": {"need_ozon": True}})
            await query.edit_message_text(
                "🟣+🔵 Подключим обе. Начнём с Wildberries.\n\n"
                "Шаг 1 из 4 — API токен WB\n\n"
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
        from telegram import Chat
        if update.effective_chat and update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            logger.debug(f"[max:handler] _handle_onboard_text вызван из группы — текст: {update.message.text[:50] if update.message and update.message.text else '?'}")
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
            data["wb_token"] = text
            await self._set_onboard(chat_id, {"step": "wb_statistics_token", "data": data})
            step_label = "Шаг 2 из 4" if data.get("need_ozon") else "Шаг 2 из 2"
            await update.message.reply_text(
                f"✅ Wildberries подключён!\n\n"
                f"{step_label} — Statistics API\n\n"
                "Отправь токен для <b>Statistics API</b> (остатки и продажи).\n\n"
                "📌 Где взять:\n"
                "seller.wildberries.ru → Настройки → Доступ к API → "
                "создать токен с категорией <b>Статистика</b>\n\n"
                "Если не нужно — отправь /skip",
                parse_mode="HTML",
            )

        elif step == "wb_statistics_token":
            from db import get_pool
            stat_token = None if text == "/skip" else text
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE marketplace_shops SET statistics_token = $1 WHERE chat_id = $2 AND marketplace = 'wb'",
                    stat_token, chat_id,
                )
            if data.get("need_ozon"):
                data["step_done"] = "wb"
                await self._set_onboard(chat_id, {"step": "ozon_client_id", "data": data})
                await update.message.reply_text(
                    "✅ Сохранено!\n\n"
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
            total = "3" if not data.get("updating") else "2"
            await update.message.reply_text(
                f"Шаг 2 из {total} — Api-Key\n\n"
                "Теперь отправь Api-Key магазина.\n\n"
                "📌 Где взять:\n"
                "seller.ozon.ru → Настройки → API ключи → Seller API"
            )

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
            if data.get("updating"):
                from db import add_marketplace_shop
                await add_marketplace_shop(chat_id, "ozon", text, client_id=client_id)
                await self._clear_onboard(chat_id)
                await update.message.reply_text("✅ Токен Ozon обновлён.")
                return
            data["ozon_api_key"] = text
            await self._set_onboard(chat_id, {"step": "ozon_shop_name", "data": data})
            await update.message.reply_text(
                "✅ Проверка прошла!\n\n"
                "Шаг 3 из 3 — Название магазина\n\n"
                "Как назовём этот магазин?\n"
                "Например: «Основной Ozon», «Ozon Premium», «Новый»\n\n"
                "Имя видно только тебе — помогает различать магазины в отчётах.\n"
                "Если не важно — отправь /skip"
            )

        elif step == "ozon_shop_name":
            shop_name = None if text.strip() == "/skip" else text.strip()
            from db import add_marketplace_shop
            client_id = data.get("client_id", "")
            api_key = data.get("ozon_api_key", "")
            await add_marketplace_shop(chat_id, "ozon", api_key, client_id=client_id, shop_name=shop_name)
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
            model=config.CLAUDE_HAIKU_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    async def _generate_question_answer(self, product_name: str, question_text: str) -> str:
        prompt = _QUESTION_PROMPT.format(
            product_name=product_name or "товар",
            question_text=question_text or "(без текста)",
        )
        response = await self.claude.messages.create(
            model=config.CLAUDE_HAIKU_MODEL,
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

        for shop in shops:
            mp = shop["marketplace"]
            mp_label = _MP_LABELS.get(mp, mp)
            stats = {"found": 0, "auto_replied": 0, "pending": 0, "errors": 0}

            last_checked = shop.get("last_checked_at")
            if last_checked is None:
                since = datetime.now(_UTC) - timedelta(days=7)
            else:
                since = last_checked if last_checked.tzinfo else last_checked.replace(tzinfo=_UTC)
            logger.info(
                f"[Макс] {mp_label} since={since} (last_checked_at={last_checked})"
            )

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
                    try:
                        await self._notify_pending(chat_id, shop, rv, reply)
                    except Exception as e:
                        logger.error(f"[Макс] _notify_pending failed review={rv['review_id'][:8]}: {e}")
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

    async def process_questions(self, chat_id: int) -> dict:
        """Обработать неотвеченные вопросы покупателей. Возвращает итоги по площадкам."""
        from db import get_marketplace_shops, save_question, update_question_status
        from tools.marketplace import make_client

        shops = await get_marketplace_shops(chat_id)
        results: dict = {}
        if not shops:
            return results

        for shop in shops:
            mp = shop["marketplace"]
            stats = {"found": 0, "pending": 0, "errors": 0}
            try:
                questions = await make_client(shop).get_questions()
                logger.info(f"[Макс/questions] {mp}: {len(questions)} вопросов для chat={chat_id}")
            except Exception as e:
                logger.error(f"[Макс/questions] get_questions {mp}: {e}")
                stats["errors"] += 1
                results[mp] = stats
                continue

            for q in questions:
                try:
                    created = None
                    if q.get("created_at"):
                        try:
                            from datetime import datetime as _dt
                            raw_ts = str(q["created_at"]).rstrip("Z")
                            created = _dt.fromisoformat(raw_ts).replace(tzinfo=_UTC)
                        except Exception:
                            pass
                    is_new = await save_question(
                        chat_id=chat_id,
                        marketplace=mp,
                        question_id=q["question_id"],
                        product_id=q.get("product_id"),
                        product_name=q.get("product_name"),
                        question_text=q.get("question_text"),
                        created_at=created,
                    )
                    notif_key = f"q_notified:{mp}:{q['question_id']}"
                    if not is_new:
                        # Проверяем статус в БД
                        from db import get_pool as _gp
                        async with (await _gp()).acquire() as _conn:
                            _row = await _conn.fetchrow(
                                "SELECT status, generated_answer FROM marketplace_questions "
                                "WHERE marketplace=$1 AND question_id=$2",
                                mp, q["question_id"],
                            )
                        db_status = _row["status"] if _row else "NOT_IN_DB"
                        _redis_val = await self._redis_get(notif_key)
                        logger.info(
                            f"[Макс/questions] {mp} q={q['question_id'][:8]} "
                            f"status={db_status} redis_key={'SET' if _redis_val else 'EMPTY'}"
                        )
                        # WB вернул вопрос как неотвеченный, но наша БД говорит answered/skipped —
                        # значит ответ не дошёл до WB. Сбрасываем статус и переотправляем.
                        if db_status in ("answered", "skipped"):
                            _sent_key = f"q_answer_sent:{mp}:{q['question_id']}"
                            if await self._redis_get(_sent_key):
                                # Ответ отправлен менее 2 часов назад — даём WB время обработать
                                logger.info(
                                    f"[Макс/questions] {mp} q={q['question_id'][:8]} "
                                    f"статус={db_status}, ответ отправлен недавно — ждём WB"
                                )
                                continue
                            logger.warning(
                                f"[Макс/questions] {mp} q={q['question_id'][:8]} "
                                f"WB считает неотвеченным но статус={db_status} — сбрасываем в pending_approval"
                            )
                            await update_question_status(
                                mp, q["question_id"], status="pending_approval",
                                generated_answer=_row.get("generated_answer") if _row else None,
                            )
                            db_status = "pending_approval"
                            await self._redis_set(notif_key, "", ttl=1)  # сбросить Redis-ключ
                        # Уже в БД — повторить нотификацию если pending и не отправляли в последние 2ч
                        if db_status == "pending_approval" and not await self._redis_get(notif_key):
                            from db import get_pending_questions as _gpq
                            pending_db = await _gpq(chat_id)
                            pending_q = next(
                                (p for p in pending_db if p["question_id"] == q["question_id"]), None
                            )
                            if pending_q:
                                q["question_text"] = pending_q.get("question_text") or q.get("question_text", "")
                                await self._notify_pending_question(
                                    chat_id, shop, q, pending_q.get("generated_answer", "")
                                )
                                await self._redis_set(notif_key, "1", ttl=7200)
                                stats["pending"] += 1
                        continue

                    stats["found"] += 1
                    try:
                        answer = await self._generate_question_answer(
                            product_name=q.get("product_name", ""),
                            question_text=q.get("question_text", ""),
                        )
                    except Exception as e:
                        logger.error(f"[Макс/questions] generate_answer error: {e}")
                        answer = ""
                        stats["errors"] += 1

                    await update_question_status(
                        mp, q["question_id"],
                        status="pending_approval",
                        generated_answer=answer,
                    )
                    await self._notify_pending_question(chat_id, shop, q, answer)
                    await self._redis_set(notif_key, "1", ttl=7200)
                    stats["pending"] += 1
                except Exception as e:
                    logger.error(f"[Макс/questions] обработка вопроса {q.get('question_id', '?')[:8]}: {e}")
                    stats["errors"] += 1

            results[mp] = stats

        return results

    async def _notify_pending_question(
        self, chat_id: int, shop: dict, q: dict, generated_answer: str
    ) -> None:
        mp = shop["marketplace"]
        mp_label = _MP_LABELS.get(mp, mp)
        text = (
            f"❓ Вопрос [{mp_label}] — {q.get('product_name', 'товар')}\n\n"
            f"💬 {q.get('question_text') or '(без текста)'}\n\n"
            f"📝 Предлагаемый ответ:\n{generated_answer}"
        )
        cb_base = f"qrev:{mp}:{q['question_id']}"
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Отправить",     callback_data=f"{cb_base}:approve"),
            InlineKeyboardButton("✏️ Редактировать", callback_data=f"{cb_base}:edit"),
            InlineKeyboardButton("🚫 Пропустить",    callback_data=f"{cb_base}:skip"),
        ]])
        # Основной канал — через Марту в личный чат пользователя
        marta_token = getattr(getattr(self, '_marta_agent', None), 'bot_token', None)
        await self._notify_user(chat_id, text, reply_markup=keyboard,
                                bot_token=marta_token or self.bot_token)
        # Дополнительно — в группу партнёров если задана
        if config.PARTNERS_GROUP_ID:
            await self._notify_user(config.PARTNERS_GROUP_ID, text, reply_markup=keyboard)

    async def cmd_questions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/questions — показать и повторно отправить все неотвеченные вопросы из БД."""
        chat_id = update.effective_user.id
        from db import get_pending_questions, get_marketplace_shops
        pending = await get_pending_questions(chat_id)
        if not pending:
            await update.message.reply_text(
                "✅ Неотвеченных вопросов нет.\n\n"
                "Если ожидаешь вопрос — проверь синхронизацию: /sync"
            )
            return

        await update.message.reply_text(
            f"❓ <b>{len(pending)} неотвеченных вопросов</b> — повторно отправляю…",
            parse_mode="HTML",
        )
        shops = await get_marketplace_shops(chat_id)
        for q in pending:
            mp = q["marketplace"]
            shop = next((s for s in shops if s["marketplace"] == mp), None)
            if not shop:
                continue
            notif_key = f"q_notified:{mp}:{q['question_id']}"
            await self._redis_set(notif_key, "1", ttl=7200)
            await self._notify_pending_question(
                chat_id, shop,
                {"question_id": q["question_id"], "question_text": q.get("question_text"),
                 "product_name": q.get("product_name"), "created_at": q.get("created_at")},
                q.get("generated_answer", ""),
            )

    async def sync_marketplace_data(self, chat_id: int) -> None:
        """Синхронизировать остатки, продажи и заказы для всех магазинов пользователя."""
        from db import get_marketplace_shops, upsert_stock, save_sale, save_order, upsert_in_transit
        from tools.marketplace import make_client

        shops = await get_marketplace_shops(chat_id)
        since = datetime.now(_UTC) - timedelta(days=2)

        for shop in shops:
            mp = shop["marketplace"]
            mp_label = _MP_LABELS.get(mp, mp)
            stats_token = shop.get("statistics_token") or ""
            client = make_client(shop)

            # Остатки
            try:
                stocks = await client.get_stocks(statistics_token=stats_token)
                ozon_in_transit: dict[str, int] = {}
                for s in stocks:
                    if not s.get("product_id"):
                        continue
                    await upsert_stock(
                        chat_id=chat_id, marketplace=mp,
                        product_id=s["product_id"], product_name=s.get("product_name"),
                        warehouse_name=s.get("warehouse_name", ""), stock=s["stock"],
                        reserved=s.get("reserved", 0), shop_id=shop["id"],
                    )
                    # Ozon: incoming_amount агрегируем по товару (несколько складов)
                    if mp == "ozon" and s.get("in_transit", 0):
                        pid = s["product_id"]
                        ozon_in_transit[pid] = ozon_in_transit.get(pid, 0) + s["in_transit"]
                logger.info(f"[Макс/sync] {mp_label}: {len(stocks)} позиций остатков")
                if mp == "wb":
                    from db import cleanup_old_stocks
                    deleted = await cleanup_old_stocks(chat_id, "wb")
                    if deleted:
                        logger.info(f"[Макс/sync] WB: удалено {deleted} старых записей с nmId")
                if mp == "ozon":
                    from db import clear_ozon_numeric_stocks
                    deleted = await clear_ozon_numeric_stocks(chat_id)
                    if deleted:
                        logger.info(f"[Макс/sync] Ozon: удалено {deleted} старых записей с числовым SKU")
                    for pid, qty in ozon_in_transit.items():
                        await upsert_in_transit(chat_id, "ozon", pid, qty)
                    if ozon_in_transit:
                        logger.info(f"[Макс/sync] Ozon: {len(ozon_in_transit)} товаров в пути на склад")
            except Exception as e:
                logger.error(f"[Макс/sync] get_stocks {mp_label}: {e}")

            # WB: синхронизируем поставки в пути через Marketplace API
            if mp == "wb":
                try:
                    in_transit_items = await client.get_in_transit()
                    for item in in_transit_items:
                        await upsert_in_transit(chat_id, "wb", item["product_id"], item["qty"])
                    if in_transit_items:
                        logger.info(f"[Макс/sync] WB: {len(in_transit_items)} артикулов в пути на склад")
                except Exception as e:
                    logger.warning(f"[Макс/sync] WB get_in_transit: {e}")

            # Синхронизация статусов поставок на МП
            try:
                from db import upsert_supply_orders
                supply_rows = await client.get_supply_statuses()
                if supply_rows:
                    await upsert_supply_orders(chat_id, mp, supply_rows)
                    logger.info(f"[Макс/sync] {mp_label}: {len(supply_rows)} позиций поставок синхронизировано")
            except AttributeError:
                pass  # get_supply_statuses не реализован для этого клиента
            except Exception as e:
                logger.warning(f"[Макс/sync] {mp_label} get_supply_statuses: {e}")

            # Продажи (включая возвраты с is_return=True)
            try:
                sales = await client.get_sales(date_from=since, statistics_token=stats_token)
                new_sales = 0
                new_returns = 0
                for s in sales:
                    sale_date = None
                    if s.get("sale_date"):
                        try:
                            from datetime import datetime as _dt
                            sale_date = _dt.fromisoformat(str(s["sale_date"]).rstrip("Z")).replace(tzinfo=_UTC)
                        except Exception:
                            pass
                    is_ret = s.get("is_return", False)
                    is_new = await save_sale(
                        chat_id=chat_id, marketplace=mp,
                        order_id=s["order_id"], product_id=s.get("product_id"),
                        product_name=s.get("product_name"), quantity=s.get("quantity", 1),
                        price=s.get("price"), commission=s.get("commission"),
                        sale_date=sale_date, is_return=is_ret,
                    )
                    if is_new:
                        if is_ret:
                            new_returns += 1
                        else:
                            new_sales += 1
                logger.info(f"[Макс/sync] {mp_label}: {new_sales} новых продаж, {new_returns} возвратов")
            except Exception as e:
                logger.error(f"[Макс/sync] get_sales {mp_label}: {e}")

            # Заказы WB — get_orders_all (Statistics API, flag=0, за 7 дней)
            # Заказы Ozon — только через get_orders_analytics (агрегат по SKU),
            #   get_orders (активные постинги) НЕ сохраняем в marketplace_orders во избежание двойного счёта
            if mp == "wb":
                try:
                    since_orders = datetime.now(_UTC) - timedelta(days=14)
                    orders = await client.get_orders_all(date_from=since_orders, statistics_token=stats_token)
                    new_count = 0
                    for o in orders:
                        order_date = None
                        if o.get("order_date"):
                            try:
                                from datetime import datetime as _dt
                                order_date = _dt.fromisoformat(str(o["order_date"]).rstrip("Z")).replace(tzinfo=_UTC)
                            except Exception:
                                pass
                        is_new = await save_order(
                            chat_id=chat_id, marketplace="wb",
                            order_id=o["order_id"], product_id=o.get("product_id"),
                            product_name=o.get("product_name"), quantity=o.get("quantity", 1),
                            order_date=order_date, seller_price=o.get("seller_price"),
                            region=o.get("region", ""), shop_id=shop["id"],
                        )
                        if is_new:
                            new_count += 1
                    logger.info(f"[Макс/sync] WB: {new_count} новых заказов")
                except Exception as e:
                    logger.error(f"[Макс/sync] get_orders WB: {e}")

            # Ozon аналитика — агрегат заказов по SKU за каждый из последних 14 дней → marketplace_orders
            # Перед сохранением чистим старые аналитические записи за тот же период чтобы избежать дублей
            # Delivered постинги идут в marketplace_sales через get_sales (выше)
            if mp == "ozon":
                from db import clear_ozon_analytics
                analytics_date_from = datetime.now(_UTC) - timedelta(days=14)
                analytics_date_to   = datetime.now(_UTC)
                deleted = await clear_ozon_analytics(chat_id, analytics_date_from, analytics_date_to)
                if deleted:
                    logger.info(f"[Макс/sync] Ozon analytics: удалено {deleted} старых записей перед обновлением")
                total_new = 0
                for day_offset in range(14):
                    day = datetime.now(_UTC) - timedelta(days=day_offset)
                    df_str = day.strftime("%Y-%m-%d")
                    order_date = day.replace(hour=0, minute=0, second=0, microsecond=0)
                    try:
                        analytics_rows = await client.get_orders_analytics(
                            date_from=day,
                            date_to=day,
                        )
                        new_count = 0
                        for row in analytics_rows:
                            order_id = f"ozon_analytics_{shop['id']}_{row['product_id']}_{df_str}"
                            is_new = await save_order(
                                chat_id=chat_id, marketplace="ozon",
                                order_id=order_id, product_id=row.get("product_id"),
                                product_name=row.get("product_name"), quantity=row.get("quantity", 1),
                                order_date=order_date, seller_price=row.get("seller_price"),
                                shop_id=shop["id"],
                            )
                            if is_new:
                                new_count += 1
                        if new_count:
                            logger.info(f"[Макс/sync] Ozon analytics {df_str}: {new_count} новых записей")
                        total_new += new_count
                    except Exception as e:
                        logger.error(f"[Макс/sync] get_orders_analytics Ozon {df_str}: {e}")
                logger.info(f"[Макс/sync] Ozon analytics итого: {total_new} новых записей за 14 дней")

                # Авто-категории Ozon: description-category/tree + product/info/list
                try:
                    from db import get_pool as _get_pool
                    _pool = await _get_pool()
                    async with _pool.acquire() as conn:
                        uncategorized = await conn.fetch(
                            "SELECT ozon_offer_id FROM product_mapping "
                            "WHERE ozon_offer_id IS NOT NULL AND category IS NULL"
                        )
                    if uncategorized:
                        offer_ids = [r["ozon_offer_id"] for r in uncategorized]
                        cat_map = await client.get_product_categories(offer_ids)
                        if cat_map:
                            async with _pool.acquire() as conn:
                                for offer_id, cat_name in cat_map.items():
                                    await conn.execute(
                                        "UPDATE product_mapping SET category = $1 "
                                        "WHERE ozon_offer_id = $2 AND category IS NULL",
                                        cat_name, offer_id,
                                    )
                            logger.info(f"[Макс/sync] Ozon категории: обновлено {len(cat_map)} товаров")
                except Exception as e:
                    logger.error(f"[Макс/sync] Ozon категории ошибка: {e}")

        await self.sync_prices(chat_id)

    async def sync_prices(self, chat_id: int) -> None:
        """Синхронизировать текущие цены товаров из WB и Ozon API → product_mapping."""
        from db import get_marketplace_shops, get_pool
        from tools.marketplace import WBClient, OzonClient

        pool = await get_pool()
        shops = await get_marketplace_shops(chat_id)

        for shop in shops:
            mp = shop["marketplace"]
            try:
                if mp == "wb":
                    client = WBClient(shop["api_token"])
                    prices = await client.get_current_prices()
                    updated = 0
                    async with pool.acquire() as conn:
                        for p in prices:
                            result = await conn.execute(
                                """UPDATE product_mapping
                                   SET wb_price = $1, prices_updated_at = NOW()
                                   WHERE LOWER(REPLACE(wb_article, ',', '.')) = LOWER(REPLACE($2, ',', '.'))""",
                                p["price"], p["product_id"],
                            )
                            if result.split()[-1] != "0":
                                updated += 1
                    logger.info(f"[Макс/sync_prices] WB: обновлено {updated} цен")

                elif mp == "ozon":
                    async with pool.acquire() as conn:
                        rows = await conn.fetch(
                            "SELECT ozon_sku FROM product_mapping WHERE ozon_sku IS NOT NULL"
                        )
                    skus = [int(r["ozon_sku"]) for r in rows if r["ozon_sku"]]
                    if skus:
                        client = OzonClient(shop["api_token"], shop["client_id"])
                        prices = await client.get_current_prices(skus)
                        updated = 0
                        async with pool.acquire() as conn:
                            for p in prices:
                                result = await conn.execute(
                                    """UPDATE product_mapping
                                       SET ozon_price = $1, prices_updated_at = NOW()
                                       WHERE ozon_offer_id = $2""",
                                    p["price"], p["product_id"],
                                )
                                if result.split()[-1] != "0":
                                    updated += 1
                        logger.info(f"[Макс/sync_prices] Ozon: обновлено {updated} цен")
            except Exception as e:
                logger.error(f"[Макс/sync_prices] {mp}: {e}", exc_info=True)

    async def sync_ad_stats(self, chat_id: int) -> None:
        """Синхронизация рекламной статистики WB + Ozon. Вызывается отдельно от основного sync."""
        from db import get_marketplace_shops, upsert_ad_stat, upsert_product_ad_stat, upsert_fin_adv
        from tools.marketplace import WBClient, OzonPerformanceClient, OzonClient
        import os
        from datetime import date as _date

        shops = await get_marketplace_shops(chat_id)
        date_to_adv      = datetime.now(_UTC).strftime("%Y-%m-%d")
        date_from_adv_wb = (datetime.now(_UTC) - timedelta(days=7)).strftime("%Y-%m-%d")
        # Ozon Performance возвращает агрегат за период одним CSV (не по дням),
        # поэтому синкаем 30 дней — стоимость API та же, зато покрываем полное окно
        # дашборда и перезаписываем любые устаревшие строки из старого формата.
        date_from_adv_ozon = (datetime.now(_UTC) - timedelta(days=30)).strftime("%Y-%m-%d")

        for shop in shops:
            mp = shop["marketplace"]

            if mp == "wb":
                try:
                    stats_token = shop.get("statistics_token") or ""
                    client = WBClient(shop["api_token"])
                    ad_stats = await client.get_ad_stats(date_from=date_from_adv_wb, date_to=date_to_adv)
                    prod_count = 0
                    for s in ad_stats:
                        stat_date = _date.fromisoformat(s["stat_date"]) if isinstance(s["stat_date"], str) else s["stat_date"]
                        await upsert_ad_stat(
                            chat_id=chat_id, marketplace="wb",
                            campaign_id=s["campaign_id"], campaign_name=s["campaign_name"],
                            stat_date=stat_date, views=s["views"],
                            clicks=s["clicks"], ctr=s["ctr"], spend=s["spend"],
                        )
                        product_stats = s.get("product_stats") or []
                        for ps in product_stats:
                            await upsert_product_ad_stat(
                                chat_id=chat_id, marketplace="wb",
                                product_id=ps["product_id"], campaign_id=s["campaign_id"],
                                stat_date=stat_date, views=ps["views"],
                                clicks=ps["clicks"], ctr=ps["ctr"], spend=ps["spend"],
                                orders_count=ps.get("orders_count", 0),
                            )
                            prod_count += 1
                    logger.info(f"[Макс/adv] WB реклама: {len(ad_stats)} записей, {prod_count} nm-записей")

                    # Для кампаний без nm-данных: цепочка fallback:
                    # 1) GET /api/advert/v2/adverts (новый API, возвращает nm_settings + названия)
                    # 2) POST /adv/v2/promotion/adverts (старый v2, возможно мёртв)
                    # 3) ручной маппинг из wb_campaigns (/camp команда)
                    campaigns_no_nm = [
                        s for s in ad_stats if not s.get("product_stats") and s.get("spend", 0) > 0
                    ]
                    if campaigns_no_nm:
                        from db import get_wb_campaign_nm_ids, set_wb_campaign_nm_ids
                        camp_ids = [s["campaign_id"] for s in campaigns_no_nm]

                        # Шаг 1: новый API /api/advert/v2/adverts — возвращает nm_settings и название
                        details = await client.get_campaign_details(camp_ids)
                        v3_nms = {cid: info["nm_ids"] for cid, info in details.items() if info["nm_ids"]}
                        # Автоматически сохраняем названия и маппинги из нового API
                        for cid, info in details.items():
                            if info["nm_ids"] or info["name"]:
                                await set_wb_campaign_nm_ids(cid, info["nm_ids"], info["name"])

                        # Шаг 2: старый v2 API для тех, кого новый не вернул
                        missing_v3 = [cid for cid in camp_ids if cid not in v3_nms]
                        v2_nms = await client.get_campaign_products_v2(missing_v3) if missing_v3 else {}

                        # Шаг 3: ручной маппинг для оставшихся
                        missing_all = [cid for cid in camp_ids if cid not in v3_nms and cid not in v2_nms]
                        manual_nms = await get_wb_campaign_nm_ids(missing_all) if missing_all else {}

                        # Объединяем: новый API > старый v2 > ручной маппинг
                        nm_source = {**manual_nms, **v2_nms, **v3_nms}

                        for s in campaigns_no_nm:
                            nms = nm_source.get(s["campaign_id"]) or []
                            if not nms:
                                continue
                            n = len(nms)
                            stat_date = _date.fromisoformat(s["stat_date"]) if isinstance(s["stat_date"], str) else s["stat_date"]
                            spend_each = round(s["spend"] / n, 4) if n else 0
                            for nm_id in nms:
                                await upsert_product_ad_stat(
                                    chat_id=chat_id, marketplace="wb",
                                    product_id=nm_id, campaign_id=s["campaign_id"],
                                    stat_date=stat_date,
                                    views=s["views"] // n if n else 0,
                                    clicks=s["clicks"] // n if n else 0,
                                    ctr=s["ctr"], spend=spend_each,
                                    orders_count=0,
                                )
                                prod_count += 1
                        src = "новый API" if v3_nms else ("v2 API" if v2_nms else ("ручной маппинг" if manual_nms else "нет"))
                        logger.info(
                            f"[Макс/adv] WB nm fallback: {len(campaigns_no_nm)} кампаний без nm, "
                            f"источник={src}, добавлено nm-записей: {prod_count}"
                        )

                    # Заполняем wb_nm_id в product_mapping через Statistics stocks API.
                    # Content API требует разрешение "Контент" — stocks работает на том же токене.
                    try:
                        from db import get_pool
                        pool = await get_pool()
                        async with pool.acquire() as conn:
                            needs_sync = await conn.fetch(
                                "SELECT id, wb_article FROM product_mapping "
                                "WHERE wb_article IS NOT NULL AND wb_nm_id IS NULL"
                            )
                        if needs_sync:
                            nm_map = await client.get_nm_id_mapping(stats_token)
                            logger.info(f"[Макс/adv] Statistics stocks вернул {len(nm_map)} артикулов. "
                                        f"Нужно заполнить: {[r['wb_article'] for r in needs_sync]}")
                            updated_count = 0
                            async with pool.acquire() as conn:
                                for row in needs_sync:
                                    article_key = (row["wb_article"] or "").lower().replace(",", ".")
                                    nm_id = nm_map.get(article_key)
                                    if not nm_id:
                                        logger.error(
                                            f"[Макс/adv] wb_nm_id: артикул '{row['wb_article']}' "
                                            f"не найден в Statistics stocks — нет остатков за 30 дней? "
                                            f"Доступные артикулы: {list(nm_map.keys())[:10]}"
                                        )
                                        continue
                                    result = await conn.execute(
                                        "UPDATE product_mapping SET wb_nm_id = $1 WHERE id = $2 AND wb_nm_id IS NULL",
                                        nm_id, row["id"],
                                    )
                                    if result.split()[-1] != "0":
                                        updated_count += 1
                            logger.info(f"[Макс/adv] wb_nm_id: обновлено {updated_count} из {len(needs_sync)} товаров")
                        else:
                            logger.info("[Макс/adv] wb_nm_id: все товары уже имеют nmId")
                    except Exception as e:
                        logger.error(f"[Макс/adv] wb_nm_id sync ошибка: {e}", exc_info=True)
                except Exception as e:
                    logger.error(f"[Макс/adv] WB реклама: {e}")

            if mp == "ozon":
                try:
                    # Per-shop credentials (приоритет), иначе глобальные env vars
                    ozon_perf_client_id     = shop.get("performance_client_id") or os.getenv("OZON_PERFORMANCE_CLIENT_ID")
                    ozon_perf_client_secret = shop.get("performance_client_secret") or os.getenv("OZON_PERFORMANCE_CLIENT_SECRET")
                    if ozon_perf_client_id and ozon_perf_client_secret:
                        redis = await self._get_redis()
                        perf_client = OzonPerformanceClient(ozon_perf_client_id, ozon_perf_client_secret, redis)
                        ad_stats = await perf_client.get_ad_stats(date_from=date_from_adv_ozon, date_to=date_to_adv)
                        prod_count = 0
                        for s in ad_stats:
                            stat_date = _date.fromisoformat(s["stat_date"]) if isinstance(s["stat_date"], str) else s["stat_date"]
                            await upsert_ad_stat(
                                chat_id=chat_id, marketplace="ozon",
                                campaign_id=s["campaign_id"], campaign_name=s["campaign_name"],
                                stat_date=stat_date, views=s["views"],
                                clicks=s["clicks"], ctr=s["ctr"], spend=s["spend"],
                            )
                            for ps in (s.get("product_stats") or []):
                                await upsert_product_ad_stat(
                                    chat_id=chat_id, marketplace="ozon",
                                    product_id=ps["product_id"], campaign_id=s["campaign_id"],
                                    stat_date=stat_date, views=ps["views"],
                                    clicks=ps["clicks"], ctr=ps["ctr"], spend=ps["spend"],
                                    orders_count=ps.get("orders_count", 0),
                                )
                                prod_count += 1
                        logger.info(f"[Макс/adv] Ozon реклама (client_id={shop.get('client_id')}): {len(ad_stats)} записей, {prod_count} sku-записей")
                    else:
                        logger.warning(f"[Макс/adv] Ozon Performance credentials не настроены для client_id={shop.get('client_id')}")
                except Exception as e:
                    logger.error(f"[Макс/adv] Ozon реклама: {e}")

                # ВРЕМЕННО диагностика: ищем Premium/бренд/CPO в финтранзакциях Ozon —
                # пока не находим (это не в services[] заказов), возвращает [] и логирует
                # operation_type верхнего уровня для дальнейшего разбора. См. get_fin_adv_spend.
                try:
                    fin_client = OzonClient(shop["api_token"], shop.get("client_id", ""))
                    date_from_fin = (datetime.now(_UTC) - timedelta(days=30)).strftime("%Y-%m-%d")
                    fin_adv = await fin_client.get_fin_adv_spend(
                        date_from=date_from_fin,
                        date_to=date_to_adv,
                    )
                    for row in fin_adv:
                        await upsert_fin_adv(
                            chat_id=chat_id, marketplace="ozon",
                            stat_date=_date.fromisoformat(row["date"]), adv_spend=row["adv_spend"],
                            shop_id=shop["id"],
                        )
                    logger.info(f"[Макс/adv] Ozon финотчёт рекламы: {len(fin_adv)} дней")
                except Exception as e:
                    logger.error(f"[Макс/adv] Ozon финотчёт рекламы: {e}")

    async def sync_financial_report(self, chat_id: int, days: int = 90) -> dict:
        """Синхронизация финансовых отчётов WB + Ozon за N дней.

        Сохраняет реальные выплаты, комиссии, логистику и штрафы в
        marketplace_financial_report для расчёта NET-маржи у Питера.
        """
        from db import get_marketplace_shops, upsert_financial_report
        from tools.marketplace import WBClient, OzonClient

        shops = await get_marketplace_shops(chat_id)
        date_to   = datetime.now(_UTC).strftime("%Y-%m-%d")
        date_from = (datetime.now(_UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
        counts = {"wb": 0, "ozon": 0}

        for shop in shops:
            mp = shop["marketplace"]

            if mp == "wb":
                stats_token = shop.get("statistics_token") or ""
                if not stats_token:
                    logger.warning("[Макс/fin] WB: statistics_token не задан, пропускаю")
                    continue
                try:
                    client = WBClient(shop["api_token"])
                    rows = await client.get_financial_report(
                        date_from=date_from, date_to=date_to,
                        statistics_token=stats_token,
                    )
                    for r in rows:
                        await upsert_financial_report(
                            chat_id=chat_id, marketplace="wb",
                            product_id=r["product_id"],
                            report_date=r["report_date"],
                            quantity=r["quantity"],
                            revenue=r["revenue"],
                            payout=r["payout"],
                            commission=r["commission"],
                            logistics=r["logistics"],
                            storage=r["storage"],
                            penalty=r["penalty"],
                            shop_id=shop["id"],
                        )
                    counts["wb"] = len(rows)
                    logger.info(f"[Макс/fin] WB: {len(rows)} записей финотчёта")
                except Exception as e:
                    logger.error(f"[Макс/fin] WB: {e}", exc_info=True)

            if mp == "ozon":
                try:
                    client = OzonClient(shop["api_token"], shop.get("client_id", ""))
                    rows = await client.get_financial_report(date_from=date_from, date_to=date_to)
                    for r in rows:
                        await upsert_financial_report(
                            chat_id=chat_id, marketplace="ozon",
                            product_id=r["product_id"],
                            report_date=r["report_date"],
                            quantity=r["quantity"],
                            revenue=r["revenue"],
                            payout=r["payout"],
                            commission=r["commission"],
                            logistics=r["logistics"],
                            storage=r["storage"],
                            penalty=r["penalty"],
                            shop_id=shop["id"],
                        )
                    counts["ozon"] = len(rows)
                    logger.info(f"[Макс/fin] Ozon: {len(rows)} записей финотчёта")

                    real_rows = await client.get_realization_quantity_revenue(date_from=date_from, date_to=date_to)
                    for r in real_rows:
                        await upsert_financial_report(
                            chat_id=chat_id, marketplace="ozon",
                            product_id=r["product_id"],
                            report_date=r["report_date"],
                            quantity=r["quantity"],
                            revenue=r["revenue"],
                            shop_id=shop["id"],
                        )
                    logger.info(f"[Макс/fin] Ozon: {len(real_rows)} записей quantity/revenue из realization")
                except Exception as e:
                    logger.error(f"[Макс/fin] Ozon: {e}", exc_info=True)

        return counts

    async def cmd_sync_fin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync_fin [дней=90] — синхронизация финансовых отчётов (комиссии, логистика, выплаты)."""
        chat_id = update.effective_user.id
        days = 90
        if context.args:
            try:
                days = int(context.args[0])
            except ValueError:
                pass
        await update.message.reply_text(f"💰 Синхронизирую финансовые отчёты за {days} дней…")
        try:
            counts = await self.sync_financial_report(chat_id, days=days)
            await update.message.reply_text(
                f"✅ Финансовые отчёты синхронизированы\n"
                f"WB: {counts.get('wb', 0)} агрегатов\n"
                f"Ozon: {counts.get('ozon', 0)} агрегатов\n\n"
                f"Теперь /report у Питера покажет реальную NET-маржу."
            )
        except Exception as e:
            logger.error(f"[Макс/sync_fin] {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def sync_funnel(self, chat_id: int) -> dict:
        """Синхронизация воронки конверсии карточки WB + Ozon за последние 30 дней."""
        from db import get_marketplace_shops, upsert_funnel_stat
        from tools.marketplace import WBClient, OzonClient
        from datetime import date as _date

        shops = await get_marketplace_shops(chat_id)
        date_to   = datetime.now(_UTC).strftime("%Y-%m-%d")
        date_from = (datetime.now(_UTC) - timedelta(days=30)).strftime("%Y-%m-%d")
        counts = {"wb": 0, "ozon": 0}

        for shop in shops:
            mp = shop["marketplace"]

            if mp == "wb":
                try:
                    client = WBClient(shop["api_token"])
                    rows = await client.get_funnel_stats(date_from=date_from, date_to=date_to)
                    for r in rows:
                        stat_date = _date.fromisoformat(r["stat_date"]) if isinstance(r["stat_date"], str) else r["stat_date"]
                        await upsert_funnel_stat(
                            chat_id=chat_id, marketplace="wb",
                            product_id=r["product_id"], stat_date=stat_date,
                            views=r["views"], add_to_cart=r["add_to_cart"],
                            orders_count=r["orders_count"], buyouts=r["buyouts"],
                            avg_position=r["avg_position"],
                            conv_view_to_cart=r["conv_view_to_cart"],
                            conv_cart_to_order=r["conv_cart_to_order"],
                        )
                    counts["wb"] = len(rows)
                    logger.info(f"[Макс/funnel] WB: {len(rows)} записей воронки")
                except Exception as e:
                    logger.error(f"[Макс/funnel] WB: {e}")

            if mp == "ozon":
                try:
                    client = OzonClient(shop["api_token"], shop.get("client_id", ""))
                    rows = await client.get_funnel_stats(date_from=date_from, date_to=date_to)
                    for r in rows:
                        stat_date = _date.fromisoformat(r["stat_date"]) if isinstance(r["stat_date"], str) else r["stat_date"]
                        await upsert_funnel_stat(
                            chat_id=chat_id, marketplace="ozon",
                            product_id=r["product_id"], stat_date=stat_date,
                            views=r["views"], add_to_cart=r["add_to_cart"],
                            orders_count=r["orders_count"], buyouts=r["buyouts"],
                            avg_position=r["avg_position"],
                            conv_view_to_cart=r["conv_view_to_cart"],
                            conv_cart_to_order=r["conv_cart_to_order"],
                        )
                    counts["ozon"] = len(rows)
                    logger.info(f"[Макс/funnel] Ozon: {len(rows)} записей воронки")
                except Exception as e:
                    logger.error(f"[Макс/funnel] Ozon: {e}")

        return counts

    async def cmd_sync_funnel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync_funnel — синхронизация воронки конверсии карточки."""
        chat_id = update.effective_user.id
        await update.message.reply_text("🔄 Синхронизирую воронку конверсии (30 дней)…")
        try:
            counts = await self.sync_funnel(chat_id)
            wb_cnt   = counts.get("wb", 0)
            ozon_cnt = counts.get("ozon", 0)
            await update.message.reply_text(
                f"✅ Воронка синхронизирована\n"
                f"WB: {wb_cnt} записей\n"
                f"Ozon: {ozon_cnt} записей\n\n"
                f"Запусти /funnel у Питера для анализа."
            )
        except Exception as e:
            logger.error(f"[Макс/sync_funnel] {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def sync_promotions(self, chat_id: int) -> dict:
        """Синхронизация акционных кампаний WB + Ozon."""
        from db import get_marketplace_shops, upsert_promotion
        from tools.marketplace import WBClient, OzonClient

        shops = await get_marketplace_shops(chat_id)
        counts = {"wb": 0, "ozon": 0}

        for shop in shops:
            mp = shop["marketplace"]

            if mp == "wb":
                try:
                    client = WBClient(shop["api_token"])
                    promos = await client.get_promotions()
                    for p in promos:
                        if not p.get("promotion_id"):
                            continue
                        start = p["start_date"] or None
                        end   = p["end_date"] or None
                        await upsert_promotion(
                            chat_id=chat_id, marketplace="wb",
                            promotion_id=p["promotion_id"],
                            title=p["title"],
                            discount_pct=p["discount_pct"],
                            start_date=start, end_date=end,
                            product_ids=p["product_ids"],
                        )
                    counts["wb"] = len(promos)
                    logger.info(f"[Макс/promos] WB: {len(promos)} акций")
                except Exception as e:
                    logger.error(f"[Макс/promos] WB: {e}", exc_info=True)

            if mp == "ozon":
                try:
                    client = OzonClient(shop["api_token"], shop.get("client_id", ""))
                    promos = await client.get_promotions()
                    for p in promos:
                        if not p.get("promotion_id"):
                            continue
                        start = p["start_date"] or None
                        end   = p["end_date"] or None
                        await upsert_promotion(
                            chat_id=chat_id, marketplace="ozon",
                            promotion_id=p["promotion_id"],
                            title=p["title"],
                            discount_pct=p["discount_pct"],
                            start_date=start, end_date=end,
                            product_ids=p["product_ids"],
                        )
                    counts["ozon"] = len(promos)
                    logger.info(f"[Макс/promos] Ozon: {len(promos)} акций")
                except Exception as e:
                    logger.error(f"[Макс/promos] Ozon: {e}", exc_info=True)

        return counts

    async def _send_promotions_summary(self, chat_id: int) -> None:
        """Еженедельный саммари акций: синхронизирует и шлёт сводку в Telegram."""
        import datetime as _dt_mod
        from db import get_pool
        await self.sync_promotions(chat_id)

        today = _dt_mod.date.today()
        pool = await get_pool()
        async with pool.acquire() as conn:
            active = await conn.fetch("""
                SELECT title, marketplace, discount_pct, start_date, end_date, product_ids
                FROM marketplace_promotions
                WHERE chat_id = $1 AND (end_date IS NULL OR end_date >= $2)
                ORDER BY marketplace, discount_pct DESC NULLS LAST
            """, chat_id, today)
            mapping = await conn.fetch(
                "SELECT wb_article, ozon_offer_id, display_name FROM product_mapping"
            )

        if not active:
            await self.app.bot.send_message(
                chat_id=chat_id,
                text="🎁 Нет активных акций WB/Ozon на сегодня.",
                parse_mode="HTML",
            )
            return

        # Все product_ids, участвующие хоть в одной акции
        promo_pids: set[str] = set()
        for row in active:
            pids = row["product_ids"] or []
            promo_pids.update(str(p) for p in pids)

        lines = ["🎁 <b>Акции WB / Ozon:</b>\n"]
        for row in active[:10]:
            mp_label = "🟣 WB" if row["marketplace"] == "wb" else "🔵 Ozon"
            end_str  = row["end_date"].strftime("%d.%m") if row["end_date"] else "—"
            pids     = row["product_ids"] or []
            discount = f" -{int(row['discount_pct'])}%" if row.get("discount_pct") else ""
            lines.append(f"{mp_label} <b>{row['title']}</b>{discount} · до {end_str} · {len(pids)} тов.")
        if len(active) > 10:
            lines.append(f"…и ещё {len(active) - 10} акций")

        # Определяем наши товары вне акций
        not_in_promo = []
        for m in mapping:
            wb_id   = str(m["wb_article"] or "")
            ozon_id = str(m["ozon_offer_id"] or "")
            if wb_id and wb_id not in promo_pids and ozon_id not in promo_pids:
                not_in_promo.append(m["display_name"])

        if not_in_promo:
            lines.append("")
            lines.append("📦 <b>Ваши товары вне акций:</b>")
            for name in not_in_promo[:8]:
                lines.append(f"  · {name}")
            if len(not_in_promo) > 8:
                lines.append(f"  …и ещё {len(not_in_promo) - 8}")
            lines.append("\n💡 <i>Участие в акции = +30-50% видимости в выдаче</i>")

        await self.app.bot.send_message(
            chat_id=chat_id,
            text="\n".join(lines),
            parse_mode="HTML",
        )
        logger.info(f"[Макс/promotions_summary] chat={chat_id} акций={len(active)} вне_акций={len(not_in_promo)}")

    async def cmd_sync_promotions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync_promotions — синхронизация акционных кампаний WB + Ozon."""
        chat_id = update.effective_user.id
        await update.message.reply_text("🏷 Синхронизирую акционные кампании…")
        try:
            counts = await self.sync_promotions(chat_id)
            await update.message.reply_text(
                f"✅ Акции синхронизированы\n"
                f"WB: {counts.get('wb', 0)} акций\n"
                f"Ozon: {counts.get('ozon', 0)} акций"
            )
        except Exception as e:
            logger.error(f"[Макс/sync_promotions] {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def sync_cards(self, chat_id: int) -> dict:
        """Синхронизация контента карточек (title, description, characteristics) в product_cards."""
        from db import get_marketplace_shops, upsert_product_card, get_pool
        from tools.marketplace import WBClient, OzonClient

        shops = await get_marketplace_shops(chat_id)
        counts: dict = {"wb": 0, "ozon": 0}

        for shop in shops:
            mp = shop["marketplace"]

            if mp == "wb":
                try:
                    client = WBClient(shop["api_token"])
                    cards = await client.get_nm_ids()
                    for _vendor, info in cards.items():
                        await upsert_product_card(
                            chat_id=chat_id,
                            marketplace="wb",
                            product_id=info["nm_id"],
                            title=info.get("title"),
                            description=info.get("description"),
                            characteristics=info.get("characteristics"),
                            category=info.get("category"),
                        )
                    counts["wb"] = len(cards)
                    logger.info(f"[Макс/sync_cards] WB: {len(cards)} карточек")
                except Exception as e:
                    logger.error(f"[Макс/sync_cards] WB: {e}", exc_info=True)

            if mp == "ozon":
                try:
                    client = OzonClient(shop["api_token"], shop.get("client_id", ""))
                    pool = await get_pool()
                    async with pool.acquire() as conn:
                        rows = await conn.fetch(
                            "SELECT ozon_offer_id FROM product_mapping WHERE ozon_offer_id IS NOT NULL"
                        )
                    offer_ids = [r["ozon_offer_id"] for r in rows]
                    if offer_ids:
                        content = await client.get_product_content(offer_ids)
                        for oid, info in content.items():
                            await upsert_product_card(
                                chat_id=chat_id,
                                marketplace="ozon",
                                product_id=oid,
                                title=info.get("title"),
                                description=info.get("description"),
                                characteristics=info.get("characteristics"),
                                category=None,
                            )
                        counts["ozon"] = len(content)
                        logger.info(f"[Макс/sync_cards] Ozon: {len(content)} карточек")
                except Exception as e:
                    logger.error(f"[Макс/sync_cards] Ozon: {e}", exc_info=True)

        return counts

    async def cmd_sync_cards(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync_cards — скачать контент карточек WB + Ozon (title, description, characteristics)."""
        chat_id = update.effective_user.id
        await update.message.reply_text("📦 Синхронизирую контент карточек…")
        try:
            counts = await self.sync_cards(chat_id)
            wb_cnt   = counts.get("wb", 0)
            ozon_cnt = counts.get("ozon", 0)
            await update.message.reply_text(
                f"✅ Карточки синхронизированы\n"
                f"WB: {wb_cnt} карточек\n"
                f"Ozon: {ozon_cnt} карточек\n\n"
                f"Теперь /seo у Элины покажет текущий контент карточки."
            )
        except Exception as e:
            logger.error(f"[Макс/sync_cards] {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def cmd_sync_keywords(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync_keywords — синхронизация ключевых слов и позиций WB."""
        await update.message.reply_text(
            "⚠️ <b>Функция временно недоступна</b>\n\n"
            "WB закрыл публичный API для ключевых слов (/api/v1/analytics/search-keywords → 404).\n"
            "Данные о позициях доступны только через дашборд WB Analytics в браузере.\n\n"
            "Следим за обновлениями документации WB API.",
            parse_mode="HTML",
        )
        return

        # ──────── код ниже сохранён до появления рабочего endpoint ────────
        chat_id = update.effective_user.id
        await update.message.reply_text("🔑 Синхронизирую ключевые слова WB…")
        from db import get_marketplace_shops, upsert_search_keyword, get_pool
        from tools.marketplace import WBClient
        from datetime import date as _date, timedelta as _td

        shops = await get_marketplace_shops(chat_id)
        wb_shops = [s for s in shops if s["marketplace"] == "wb"]
        if not wb_shops:
            await update.message.reply_text("⚠️ WB магазин не подключён.")
            return

        date_to   = _date.today().strftime("%Y-%m-%d")
        date_from = (_date.today() - _td(days=7)).strftime("%Y-%m-%d")
        total = 0

        last_status = 0
        for shop in wb_shops:
            try:
                client = WBClient(shop["api_token"])
                stats_token = shop.get("statistics_token") or ""
                pool = await get_pool()
                async with pool.acquire() as conn:
                    # marketplace_sales хранит nmId как строку — надёжный источник
                    rows = await conn.fetch(
                        """SELECT DISTINCT product_id FROM marketplace_sales
                           WHERE chat_id=$1 AND marketplace='wb'
                             AND product_id ~ E'^\\\\d+$'
                           LIMIT 50""",
                        chat_id,
                    )
                nm_ids = [int(r["product_id"]) for r in rows if r["product_id"]]
                if not nm_ids:
                    await update.message.reply_text(
                        "⚠️ Нет данных о продажах WB в базе. Сначала запусти /sync."
                    )
                    return
                keywords, last_status = await client.get_search_keywords(
                    nm_ids, date_from, date_to, statistics_token=stats_token
                )
                for kw in keywords:
                    await upsert_search_keyword(
                        chat_id=chat_id, marketplace="wb",
                        product_id=kw["product_id"],
                        keyword=kw["keyword"],
                        position=kw.get("position"),
                        search_count=kw.get("search_count"),
                        ctr=kw.get("ctr"),
                        conv_rate=kw.get("conv_rate"),
                        stat_date=kw.get("stat_date") or date_to,
                    )
                total += len(keywords)
                logger.info(f"[Макс/sync_keywords] WB: {len(keywords)} ключей для {len(nm_ids)} товаров")
            except Exception as e:
                logger.error(f"[Макс/sync_keywords] WB: {e}", exc_info=True)
                await update.message.reply_text(f"❌ Ошибка: {e}")
                return

        if total == 0:
            if last_status in (401, 403):
                hint = (
                    "⚠️ WB вернул 403 — нет доступа к Analytics API.\n\n"
                    "В ЛК WB создай токен с категорией <b>Аналитика</b> "
                    "и добавь его через /start → «Подключить магазин» (поле Statistics token)."
                )
            elif last_status == 404:
                hint = "⚠️ WB Analytics API недоступен (404). Попробуй позже."
            elif last_status == 0:
                hint = "⚠️ Не удалось подключиться к WB Analytics API (timeout или сеть)."
            else:
                hint = (
                    f"⚠️ Ключевые слова не получены (HTTP {last_status}).\n"
                    "Возможно, нет данных за выбранный период."
                )
            await update.message.reply_text(hint, parse_mode="HTML")
        else:
            await update.message.reply_text(
                f"✅ Ключевые слова синхронизированы: {total} записей\n"
                f"Период: {date_from} — {date_to}"
            )

    async def _check_seo_drops(self, chat_id: int) -> list[dict]:
        """Сравнить две последних даты в product_search_keywords, вернуть дропы позиций."""
        from db import get_pool
        threshold = getattr(config, "SEO_POSITION_DROP_THRESHOLD", 10)
        pool = await get_pool()
        async with pool.acquire() as conn:
            dates = await conn.fetch(
                """SELECT DISTINCT stat_date FROM product_search_keywords
                   WHERE chat_id=$1 AND marketplace='wb' AND position IS NOT NULL
                   ORDER BY stat_date DESC LIMIT 2""",
                chat_id,
            )
            if len(dates) < 2:
                return []
            date_new, date_old = dates[0]["stat_date"], dates[1]["stat_date"]
            rows = await conn.fetch(
                """SELECT n.keyword, n.product_id,
                          COALESCE(m.display_name, n.product_id::text) AS name,
                          o.position AS pos_old, n.position AS pos_new,
                          (n.position - o.position) AS drop
                   FROM product_search_keywords n
                   JOIN product_search_keywords o
                     ON o.chat_id=n.chat_id AND o.marketplace=n.marketplace
                        AND o.product_id=n.product_id AND o.keyword=n.keyword
                        AND o.stat_date=$3
                   LEFT JOIN product_mapping m ON m.wb_nm_id = n.product_id::bigint
                   WHERE n.chat_id=$1 AND n.marketplace='wb' AND n.stat_date=$2
                     AND (n.position - o.position) >= $4
                   ORDER BY drop DESC""",
                chat_id, date_new, date_old, threshold,
            )
            return [dict(r) | {"date_old": str(date_old), "date_new": str(date_new)}
                    for r in rows]

    async def _seo_check_text(self, chat_id: int) -> str:
        from db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            cnt = await conn.fetchval(
                "SELECT COUNT(*) FROM product_search_keywords WHERE chat_id=$1", chat_id
            )
        if not cnt:
            return (
                "⚠️ <b>Нет данных о позициях</b>\n\n"
                "WB закрыл API ключевых слов (404). Данные появятся после возобновления.\n"
                "Следи за позициями вручную в WB Analytics."
            )
        drops = await self._check_seo_drops(chat_id)
        threshold = getattr(config, "SEO_POSITION_DROP_THRESHOLD", 10)
        if not drops:
            return f"✅ <b>Позиции стабильны</b>\n\nПадений ≥{threshold} мест не обнаружено."
        lines = [f"📉 <b>Падения позиций WB (≥{threshold} мест)</b>\n"]
        for d in drops[:20]:
            lines.append(
                f"• <b>{d['name']}</b> — «{d['keyword']}»: "
                f"{d['pos_old']} → {d['pos_new']} (<b>−{d['drop']}</b>)"
            )
        if len(drops) > 20:
            lines.append(f"\n…и ещё {len(drops) - 20} ключевых слов")
        lines.append(f"\n<i>Сравнение: {drops[0]['date_old']} → {drops[0]['date_new']}</i>")
        return "\n".join(lines)

    async def cmd_seo_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/seo_check — алерт при падении позиций ключевых слов WB."""
        await update.message.reply_text("🔍 Проверяю позиции ключевых слов…")
        text = await self._seo_check_text(update.effective_user.id)
        await update.message.reply_text(text, parse_mode="HTML")

    async def cmd_apply_prices(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/apply_prices — показать рекомендованные цены от Питера и применить."""
        chat_id = update.effective_user.id
        from db import get_price_recommendations
        recs = await get_price_recommendations(chat_id)
        if not recs:
            await update.message.reply_text(
                "ℹ️ <b>Нет рекомендаций цен</b>\n\n"
                "Запусти <code>/report</code> у Питера — он посчитает целевые цены "
                "для достижения нужной маржи.",
                parse_mode="HTML",
            )
            return

        lines = ["💰 <b>Рекомендованные цены от Питера</b>\n"]
        has_wb = any(r["recommended_price_wb"] for r in recs)
        has_ozon = any(r["recommended_price_ozon"] for r in recs)

        for r in recs:
            name = r["name"] or r["wb_article"] or r["ozon_sku"] or "?"
            parts = [f"<b>{name}</b>"]
            if r["recommended_price_wb"]:
                cur = f"{r['wb_price']:,.0f}₽" if r["wb_price"] else "?"
                parts.append(f"WB: {cur} → <b>{r['recommended_price_wb']:,.0f}₽</b>")
            if r["recommended_price_ozon"]:
                cur = f"{r['ozon_price']:,.0f}₽" if r["ozon_price"] else "?"
                parts.append(f"Ozon: {cur} → <b>{r['recommended_price_ozon']:,.0f}₽</b>")
            lines.append("• " + " | ".join(parts))

        lines.append("\nВыбери действие:")

        buttons = []
        if has_wb:
            buttons.append(InlineKeyboardButton("✅ Применить WB", callback_data="price_apply:wb"))
        if has_ozon:
            buttons.append(InlineKeyboardButton("✅ Применить Ozon", callback_data="price_apply:ozon"))
        if has_wb and has_ozon:
            buttons.append(InlineKeyboardButton("✅ Применить всё", callback_data="price_apply:all"))
        buttons.append(InlineKeyboardButton("❌ Отмена", callback_data="price_apply:cancel"))

        keyboard = InlineKeyboardMarkup([buttons[:2], buttons[2:]] if len(buttons) > 2 else [buttons])
        await update.message.reply_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=keyboard
        )

    async def _handle_price_apply_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        chat_id = update.effective_user.id
        action = query.data.split(":", 1)[1]  # wb | ozon | all | cancel

        if action == "cancel":
            await query.edit_message_text("❌ Отменено.")
            return

        from db import get_price_recommendations, get_marketplace_shops, get_pool, clear_price_recommendations
        from tools.marketplace import WBClient, OzonClient

        recs = await get_price_recommendations(chat_id)
        shops = await get_marketplace_shops(chat_id)
        pool = await get_pool()

        results = []

        if action in ("wb", "all"):
            wb_items = [
                {"nm_id": r["wb_nm_id"], "price": int(r["recommended_price_wb"])}
                for r in recs
                if r["recommended_price_wb"] and r["wb_nm_id"]
            ]
            wb_shops = [s for s in shops if s["marketplace"] == "wb"]
            for shop in wb_shops:
                try:
                    client = WBClient(shop["api_token"])
                    res = await client.update_prices(wb_items)
                    ok = res.get("success")
                    results.append(f"WB: {'✅' if ok else '❌'} {len(wb_items)} товаров")
                except Exception as e:
                    results.append(f"WB: ❌ {e}")
            if wb_items:
                await clear_price_recommendations(chat_id, "wb")
                # Обновляем wb_price в product_mapping
                async with pool.acquire() as conn:
                    for item in wb_items:
                        await conn.execute(
                            "UPDATE product_mapping SET wb_price=$1, prices_updated_at=NOW() "
                            "WHERE chat_id=$2 AND wb_nm_id=$3",
                            float(item["price"]), chat_id, str(item["nm_id"]),
                        )

        if action in ("ozon", "all"):
            ozon_items = [
                {"offer_id": r["ozon_sku"], "price": r["recommended_price_ozon"]}
                for r in recs
                if r["recommended_price_ozon"] and r["ozon_sku"]
            ]
            ozon_shops = [s for s in shops if s["marketplace"] == "ozon"]
            for shop in ozon_shops:
                try:
                    client = OzonClient(shop["api_token"], shop.get("client_id", ""))
                    res = await client.update_prices(ozon_items)
                    ok = res.get("success")
                    results.append(f"Ozon: {'✅' if ok else '❌'} {len(ozon_items)} товаров")
                except Exception as e:
                    results.append(f"Ozon: ❌ {e}")
            if ozon_items:
                await clear_price_recommendations(chat_id, "ozon")
                async with pool.acquire() as conn:
                    for item in ozon_items:
                        await conn.execute(
                            "UPDATE product_mapping SET ozon_price=$1, prices_updated_at=NOW() "
                            "WHERE chat_id=$2 AND ozon_sku=$3",
                            float(item["price"]), chat_id, item["offer_id"],
                        )

        if not results:
            await query.edit_message_text("⚠️ Нет товаров для обновления (не хватает данных маппинга).")
            return

        text = "💰 <b>Цены обновлены</b>\n\n" + "\n".join(results)
        await query.edit_message_text(text, parse_mode="HTML")

    async def sync_returns(self, chat_id: int, days: int = 30) -> dict:
        """Синхронизация аналитики возвратов WB + Ozon. Возвращает {mp: count}."""
        from db import get_marketplace_shops, upsert_returns_analytics
        from tools.marketplace import WBClient, OzonClient
        from datetime import date as _date, timedelta as _td, date as _dt_date

        shops = await get_marketplace_shops(chat_id)
        date_to   = _date.today().strftime("%Y-%m-%d")
        date_from = (_date.today() - _td(days=days)).strftime("%Y-%m-%d")
        totals: dict = {}

        for shop in shops:
            mp = shop["marketplace"]
            try:
                if mp == "wb":
                    stats_token = shop.get("statistics_token") or ""
                    if not stats_token:
                        logger.warning("[Макс/sync_returns] WB: нет statistics_token")
                        continue
                    client = WBClient(shop["api_token"])
                    returns = await client.get_returns_analytics(date_from, date_to, stats_token)
                elif mp == "ozon":
                    client = OzonClient(shop["api_token"], shop.get("client_id", ""))
                    returns = await client.get_returns_analytics(date_from, date_to)
                else:
                    continue

                for r in returns:
                    stat_date = r.get("stat_date") or date_to
                    try:
                        if isinstance(stat_date, str):
                            stat_date = _dt_date.fromisoformat(stat_date[:10])
                    except Exception:
                        pass
                    await upsert_returns_analytics(
                        chat_id=chat_id, marketplace=mp,
                        product_id=r["product_id"],
                        product_name=r.get("product_name"),
                        stat_date=stat_date,
                        returns_count=r.get("returns_count", 0),
                        return_amount=r.get("return_amount", 0.0),
                        return_rate=r.get("return_rate"),
                    )
                totals[mp] = len(returns)
                logger.info(f"[Макс/sync_returns] {mp}: {len(returns)} записей")
            except Exception as e:
                logger.error(f"[Макс/sync_returns] {mp}: {e}", exc_info=True)

        return totals

    async def cmd_sync_returns(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync_returns — синхронизация аналитики возвратов WB + Ozon."""
        chat_id = update.effective_user.id
        await update.message.reply_text("📦 Синхронизирую аналитику возвратов…")
        totals = await self.sync_returns(chat_id)
        if not totals:
            await update.message.reply_text("⚠️ Данные о возвратах не получены.")
        else:
            from datetime import date as _date, timedelta as _td
            date_to   = _date.today().strftime("%Y-%m-%d")
            date_from = (_date.today() - _td(days=30)).strftime("%Y-%m-%d")
            lines = ["✅ Возвраты синхронизированы"]
            for mp, cnt in totals.items():
                label = "🟣 WB" if mp == "wb" else "🔵 Ozon"
                lines.append(f"{label}: {cnt} записей")
            lines.append(f"Период: {date_from} — {date_to}")
            await update.message.reply_text("\n".join(lines))

    async def sync_shop_kpi(self, chat_id: int) -> dict:
        """Снимок рейтинга и KPI продавца WB + Ozon."""
        from db import get_marketplace_shops, upsert_shop_kpi
        from tools.marketplace import WBClient, OzonClient
        from datetime import date as _date

        shops = await get_marketplace_shops(chat_id)
        today = _date.today()
        results = {}

        for shop in shops:
            mp = shop["marketplace"]

            if mp == "wb":
                try:
                    client = WBClient(shop["api_token"])
                    kpi = await client.get_shop_kpi()
                    if kpi:
                        await upsert_shop_kpi(
                            chat_id=chat_id, marketplace="wb",
                            snapshot_date=today,
                            rating=kpi.get("rating"),
                            return_pct=kpi.get("return_pct"),
                            cancellation_pct=kpi.get("cancellation_pct"),
                            penalty_count=kpi.get("penalty_count", 0),
                            extra_data=kpi.get("extra_data", {}),
                        )
                        logger.info(f"[Макс/kpi] WB: рейтинг {kpi.get('rating')}")
                    else:
                        from db import get_wb_proxy_kpi
                        kpi = await get_wb_proxy_kpi(chat_id)
                        logger.info(f"[Макс/kpi] WB: proxy из БД, рейтинг {kpi.get('rating')}")
                    results["wb"] = kpi
                except Exception as e:
                    logger.error(f"[Макс/kpi] WB: {e}", exc_info=True)
                    results["wb"] = {}

            if mp == "ozon":
                try:
                    client = OzonClient(shop["api_token"], shop.get("client_id", ""))
                    kpi = await client.get_shop_kpi()
                    if kpi:
                        await upsert_shop_kpi(
                            chat_id=chat_id, marketplace="ozon",
                            snapshot_date=today,
                            rating=kpi.get("rating"),
                            return_pct=kpi.get("return_pct"),
                            cancellation_pct=kpi.get("cancellation_pct"),
                            penalty_count=kpi.get("penalty_count", 0),
                            extra_data=kpi.get("extra_data", {}),
                        )
                        logger.info(f"[Макс/kpi] Ozon: рейтинг {kpi.get('rating')}")
                    results["ozon"] = kpi  # всегда, даже пустой — для отображения
                except Exception as e:
                    logger.error(f"[Макс/kpi] Ozon: {e}", exc_info=True)
                    results["ozon"] = {}

        return results

    async def _shop_kpi_text(self, chat_id: int) -> str:
        results = await self.sync_shop_kpi(chat_id)
        if not results:
            return "⚠️ Данные KPI недоступны (магазины не подключены или API не поддерживается)."
        lines = ["<b>Рейтинг продавца</b>"]
        for mp, kpi in results.items():
            label = "🟣 WB" if mp == "wb" else "🔵 Ozon"
            if not kpi:
                lines.append(f"\n{label}\n<i>данные временно недоступны</i>")
                continue
            is_proxy = kpi.get("_proxy")
            source = " <i>(по данным за 30 дн)</i>" if is_proxy else ""
            rating  = kpi.get("rating") or 0
            ret     = kpi.get("return_pct") or 0
            cancel  = kpi.get("cancellation_pct")
            penalty = kpi.get("penalty_count") or 0
            cancel_str = f"{cancel:.1f}%" if cancel is not None else "—"
            lines.append(
                f"\n{label}{source}\n"
                f"⭐ Рейтинг: <b>{rating:.1f}</b>\n"
                f"↩️ Возвраты: {ret:.1f}%\n"
                f"🚫 Отмены: {cancel_str}\n"
                f"⚠️ Штрафы: {penalty}"
            )
        return "\n".join(lines)

    async def cmd_shop_kpi(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/shop_kpi — рейтинг и KPI продавца на маркетплейсах."""
        chat_id = update.effective_user.id
        await update.message.reply_text("📊 Получаю рейтинг продавца…")
        try:
            await update.message.reply_text(await self._shop_kpi_text(chat_id), parse_mode="HTML")
        except Exception as e:
            logger.error(f"[Макс/shop_kpi] {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {e}")

    # ------------------------------------------------------------------ #
    #  Вспомогательные методы для сводки                                  #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _group_by_sku(rows: list[dict], cluster_fn) -> dict:
        """Группировка по display_name → {регион: шт}."""
        grouped: dict = {}
        for s in rows:
            name   = s.get("display_name") or s.get("product_name") or s["product_id"]
            region = cluster_fn(s.get("warehouse_name", ""))
            entry  = grouped.setdefault(name, {"regions": {}})
            entry["regions"][region] = entry["regions"].get(region, 0) + s["stock"]
        return grouped

    @staticmethod
    def _render_low(grouped: dict) -> list[str]:
        """Таблица: Товар | Регион | Шт — несколько строк на товар если несколько регионов."""
        rows = ["| Товар | Регион | Шт |", "|---|---|---|"]
        for name, info in grouped.items():
            first = True
            for region, qty in sorted(info["regions"].items()):
                rows.append(f"| {'**' + name + '**' if first else ''} | {region} | {qty} |")
                first = False
        return rows

    @staticmethod
    def _render_zero(grouped: dict) -> list[str]:
        """Список товаров с нулевыми остатками."""
        return ["- " + name for name in sorted(grouped.keys())]

    @staticmethod
    def _split_message(text: str, max_len: int = 4000) -> list[str]:
        parts: list[str] = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > max_len and current:
                if line.startswith("📦") or line.startswith("⚠️") or line.startswith("❌"):
                    parts.append(current.rstrip("\n"))
                    current = line + "\n"
                else:
                    current += line + "\n"
            else:
                current += line + "\n"
        if current.strip():
            parts.append(current.rstrip("\n"))
        return parts or [text]

    async def _send_sales_summary(self, owner_chat_id: int, target_chat_id: int, bot=None) -> None:
        from db import get_orders_summary, get_orders_total, get_orders_days_count, get_sales_period, get_sales_total
        from zoneinfo import ZoneInfo
        _bot = bot if bot is not None else self.app.bot
        _SHORT = {"wb": "🟣 WB", "ozon": "🔵 Ozon"}

        from zoneinfo import ZoneInfo as _ZI
        now_utc       = datetime.now(_UTC)
        today_start   = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)
        yesterday_end   = today_start
        week_ago_start  = today_start - timedelta(days=7)
        week_ago_end    = week_ago_start + timedelta(days=1)
        this_week_start = today_start - timedelta(days=7)
        prev_week_start = today_start - timedelta(days=14)
        prev_week_end   = today_start - timedelta(days=7)

        # Для отображения дат в МСК
        msk = _ZI("Europe/Moscow")
        now_msk = datetime.now(msk)

        def _fmt_date(dt) -> str:
            return dt.astimezone(msk).strftime("%d.%m")

        logger.info(f"[sales_summary] today: {today_start} - {now_utc}")
        logger.info(f"[sales_summary] yesterday: {yesterday_start} - {yesterday_end}")
        logger.info(f"[sales_summary] week_ago_day: {week_ago_start} - {week_ago_end}")
        logger.info(f"[sales_summary] this_week: {this_week_start} - {now_utc}")
        logger.info(f"[sales_summary] prev_week: {prev_week_start} - {prev_week_end}")

        ord_today     = {r["marketplace"]: r for r in await get_orders_summary(owner_chat_id, today_start, now_utc)}
        sal_today     = {r["marketplace"]: r for r in await get_sales_period(owner_chat_id, today_start, now_utc)}
        ord_yday      = {r["marketplace"]: r for r in await get_orders_summary(owner_chat_id, yesterday_start, yesterday_end)}
        sal_yday      = {r["marketplace"]: r for r in await get_sales_period(owner_chat_id, yesterday_start, yesterday_end)}
        ord_wago      = {r["marketplace"]: r for r in await get_orders_summary(owner_chat_id, week_ago_start, week_ago_end)}
        sal_wago      = {r["marketplace"]: r for r in await get_sales_period(owner_chat_id, week_ago_start, week_ago_end)}
        ord_week      = {r["marketplace"]: r for r in await get_orders_total(owner_chat_id, days=7)}
        sal_week      = {r["marketplace"]: r for r in await get_sales_total(owner_chat_id, days=7)}
        ord_prev_week = {r["marketplace"]: r for r in await get_orders_summary(owner_chat_id, prev_week_start, prev_week_end)}
        sal_prev_week = {r["marketplace"]: r for r in await get_sales_period(owner_chat_id, prev_week_start, prev_week_end)}

        tw_wb  = ord_week.get("wb",   {})
        tw_oz  = ord_week.get("ozon", {})
        pw_wb  = ord_prev_week.get("wb",   {})
        pw_oz  = ord_prev_week.get("ozon", {})
        logger.info(f"[sales_summary] this_week WB: orders={int(tw_wb.get('orders') or 0)}, revenue={float(tw_wb.get('revenue') or 0):.2f}")
        logger.info(f"[sales_summary] prev_week WB: orders={int(pw_wb.get('orders') or 0)}, revenue={float(pw_wb.get('revenue') or 0):.2f}")
        logger.info(f"[sales_summary] this_week Ozon: orders={int(tw_oz.get('orders') or 0)}, revenue={float(tw_oz.get('revenue') or 0):.2f}")
        logger.info(f"[sales_summary] prev_week Ozon: orders={int(pw_oz.get('orders') or 0)}, revenue={float(pw_oz.get('revenue') or 0):.2f}")

        def _format_delta(cur_cnt: int, cur_rev: float, prev_cnt: int, prev_rev: float, vs_label: str) -> str | None:
            if prev_cnt == 0 and prev_rev == 0:
                return None
            d_cnt = cur_cnt - prev_cnt
            d_rev = cur_rev - prev_rev
            parts = []
            if d_cnt != 0:
                sign = "▲+" if d_cnt > 0 else "▼"
                parts.append(f"{sign}{d_cnt} зак.")
            if abs(d_rev) >= 1:
                sign = "▲+" if d_rev > 0 else "▼"
                parts.append(f"{sign}{d_rev:,.0f} ₽")
            if not parts:
                return None
            return f"({' '.join(parts)} к {vs_label})"

        def _mp_line(mp: str, orders_map: dict, sales_map: dict,
                     cmp_orders: dict | None = None, cmp_sales: dict | None = None,
                     cmp_label: str = "") -> str:
            label = _SHORT.get(mp, mp)
            o = orders_map.get(mp)
            s = sales_map.get(mp)
            if not o and not s:
                return f"{label}: нет данных"
            parts = []
            if o:
                parts.append(f"📥 {int(o['orders'])} зак. — {float(o['revenue'] or 0):,.0f} ₽")
            if s:
                sal_label = "дост." if mp == "ozon" else "выкуп."
                parts.append(f"✅ {int(s['orders'])} {sal_label} — {float(s['revenue'] or 0):,.0f} ₽")
            line = f"{label}: " + " | ".join(parts)
            if cmp_orders is not None and cmp_label:
                cur_cnt = int(o["orders"]) if o else 0
                cur_rev = float(o["revenue"] or 0) if o else 0.0
                prev    = cmp_orders.get(mp)
                prev_cnt = int(prev["orders"]) if prev else 0
                prev_rev = float(prev["revenue"] or 0) if prev else 0.0
                delta = _format_delta(cur_cnt, cur_rev, prev_cnt, prev_rev, cmp_label)
                if delta:
                    line += f" {delta}"
            return line

        date_str = now_msk.strftime("%d.%m.%Y")

        def _fmt(n: float) -> str:
            return f"{int(round(n)):,}".replace(",", " ") + " ₽"

        def _get_o(m: dict, mp: str) -> tuple[int, float]:
            r = m.get(mp, {})
            return int(r.get("orders") or 0), float(r.get("revenue") or 0)

        def _get_s(m: dict, mp: str) -> tuple[int, float]:
            r = m.get(mp, {})
            return int(r.get("orders") or 0), float(r.get("revenue") or 0)

        def _delta(cc: int, cr: float, pc: int, pr: float) -> str:
            if pc == 0 and pr == 0:
                return "—"
            parts = []
            dc, dr = cc - pc, cr - pr
            if dc != 0:
                parts.append(f"{'▲+' if dc > 0 else '▼'}{abs(dc)}")
            if abs(dr) >= 1:
                parts.append(f"{'▲+' if dr > 0 else '▼'}{_fmt(abs(dr))}")
            return " ".join(parts) or "="

        _MP = [("wb", "🟣 WB"), ("ozon", "🔵 Ozon")]
        lines = [f"# 💰 Статистика — {date_str}", ""]

        # Сегодня
        lines.append(f"## 📅 Сегодня ({_fmt_date(today_start)})")
        lines.append("")
        lines.append("| | Заказы | Выручка | Δ к вчера |")
        lines.append("|---|---|---|---|")
        for mp, emoji in _MP:
            cnt, rev = _get_o(ord_today, mp)
            pc, pr = _get_o(ord_yday, mp)
            lines.append(f"| {emoji} | {cnt} | {_fmt(rev)} | {_delta(cnt, rev, pc, pr)} |")

        # Вчера
        lines.append("")
        lines.append(f"## 📅 Вчера ({_fmt_date(yesterday_start)})")
        lines.append("")
        lines.append("| | Заказы | Выручка | Выкуп / Дост. | Выручка |")
        lines.append("|---|---|---|---|---|")
        for mp, emoji in _MP:
            cnt, rev = _get_o(ord_yday, mp)
            sc, sr = _get_s(sal_yday, mp)
            sal_lbl = "дост." if mp == "ozon" else "выкуп."
            lines.append(
                f"| {emoji} | {cnt} | {_fmt(rev)} "
                f"| {f'{sc} {sal_lbl}' if sc else '—'} | {_fmt(sr) if sc else '—'} |"
            )

        # Неделю назад
        lines.append("")
        lines.append(f"## 📅 Неделю назад ({_fmt_date(week_ago_start)})")
        lines.append("")
        lines.append("| | Заказы | Выручка |")
        lines.append("|---|---|---|")
        for mp, emoji in _MP:
            cnt, rev = _get_o(ord_wago, mp)
            lines.append(f"| {emoji} | {cnt} | {_fmt(rev)} |")

        # За 7 дней
        prev_week_days = await get_orders_days_count(owner_chat_id, prev_week_start, prev_week_end)
        prev_week_has_data = prev_week_days >= 5
        logger.info(f"[sales_summary] prev_week_days={prev_week_days}, show_delta={prev_week_has_data}")
        lines.append("")
        lines.append("## 📈 За 7 дней")
        lines.append("")
        if prev_week_has_data:
            lines.append("| | Заказы | Выручка | Выкуп / Дост. | Δ к пред. неделе |")
            lines.append("|---|---|---|---|---|")
        else:
            lines.append("| | Заказы | Выручка | Выкуп / Дост. |")
            lines.append("|---|---|---|---|")
        for mp, emoji in _MP:
            cnt, rev = _get_o(ord_week, mp)
            sc, sr = _get_s(sal_week, mp)
            sal_lbl = "дост." if mp == "ozon" else "выкуп."
            s_str = f"{sc} / {_fmt(sr)}" if sc else "—"
            row = f"| {emoji} | {cnt} | {_fmt(rev)} | {s_str} |"
            if prev_week_has_data:
                pc, pr = _get_o(ord_prev_week, mp)
                row += f" {_delta(cnt, rev, pc, pr)} |"
            lines.append(row)

        await _send_rich(config.MAX_BOT_TOKEN, target_chat_id, "\n".join(lines))

    async def _send_stocks(
        self, marketplace: str, owner_chat_id: int, target_chat_id: int, bot=None
    ) -> None:
        from db import get_low_stocks
        _bot = bot if bot is not None else self.app.bot

        is_wb = marketplace == "wb"
        emoji = "🟣" if is_wb else "🔵"
        label = "WB" if is_wb else "Ozon"
        get_cluster = _get_cluster if is_wb else _get_ozon_cluster

        low_stocks = await get_low_stocks(owner_chat_id, threshold=20)
        mp_low  = [s for s in low_stocks if s["marketplace"] == marketplace and 0 < s["stock"] <= 20]
        mp_zero = [s for s in low_stocks if s["marketplace"] == marketplace and s["stock"] == 0]

        lines = [f"# {emoji} {label} — остатки\n"]
        if not mp_low and not mp_zero:
            lines.append("✅ Остатки в норме")
        else:
            if mp_low:
                lines.append("## ⚠️ Заканчиваются (0 < stock ≤ 20)\n")
                lines.extend(self._render_low(self._group_by_sku(mp_low, get_cluster)))
            if mp_zero:
                lines.append("\n## ❌ Закончились на складах\n")
                lines.extend(self._render_zero(self._group_by_sku(mp_zero, get_cluster)))

        await _send_rich(config.MAX_BOT_TOKEN, target_chat_id, "\n".join(lines))

    async def send_daily_summary(self, owner_chat_id: int, target_chat_id: int, bot=None) -> None:
        """Синхронизировать данные и отправить ежедневную сводку тремя сообщениями."""
        logger.info(f"[Макс/sync] send_daily_summary старт для owner={owner_chat_id} target={target_chat_id}")
        try:
            await self.sync_marketplace_data(owner_chat_id)
            await self._send_sales_summary(owner_chat_id, target_chat_id, bot)
            await self._send_stocks("wb", owner_chat_id, target_chat_id, bot)
            await self._send_stocks("ozon", owner_chat_id, target_chat_id, bot)
            logger.info("[Макс/sync] сводка отправлена")
        except Exception as e:
            logger.error(f"[Макс/sync] ошибка: {e}", exc_info=True)

    async def check_negative_reviews(self, chat_id: int) -> None:
        """Быстрый polling: только 1-2★, использует last_checked_negative."""
        from db import get_marketplace_shops, save_review, update_review_status
        from tools.marketplace import make_client

        shops = await get_marketplace_shops(chat_id)
        if not shops:
            return

        now = datetime.now(_UTC)

        for shop in shops:
            mp = shop["marketplace"]
            mp_label = _MP_LABELS.get(mp, mp)

            last = shop.get("last_checked_negative")
            since = (
                last if last and last.tzinfo else
                (last.replace(tzinfo=_UTC) if last else now - timedelta(hours=1))
            )

            try:
                client = make_client(shop)
                if mp == "wb":
                    reviews = await client.get_new_reviews(since=since, max_rating=2)
                else:
                    reviews = await client.get_new_reviews(since=since)
                    reviews = [r for r in reviews if r.get("rating", 5) <= 2]
                logger.info(f"[Макс/neg] {mp_label}: {len(reviews)} neg отзывов для chat={chat_id}")
            except Exception as e:
                logger.error(f"[Макс/neg] get_new_reviews {mp_label}: {e}")
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

                try:
                    reply = await self._generate_reply(
                        product_name=rv.get("product_name", ""),
                        rating=rv.get("rating", 0),
                        text=rv.get("text", ""),
                        author=rv.get("author", ""),
                    )
                except Exception as e:
                    logger.error(f"[Макс/neg] generate_reply: {e}")
                    reply = ""

                await update_review_status(mp, rv["review_id"], "pending_approval", generated_reply=reply)
                try:
                    await self._notify_pending(chat_id, shop, rv, reply)
                except Exception as e:
                    logger.error(f"[Макс/neg] _notify_pending failed review={rv['review_id'][:8]}: {e}")

            # Обновляем last_checked_negative
            from db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE marketplace_shops SET last_checked_negative = $1 WHERE chat_id = $2 AND marketplace = $3",
                    now, chat_id, mp,
                )

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
        # Основной канал — через Марту в личный чат пользователя
        marta_token = getattr(getattr(self, '_marta_agent', None), 'bot_token', None)
        await self._notify_user(chat_id, text, reply_markup=keyboard,
                                bot_token=marta_token or self.bot_token)
        # Дополнительно — в группу партнёров если задана
        if config.PARTNERS_GROUP_ID:
            await self._notify_user(config.PARTNERS_GROUP_ID, text, reply_markup=keyboard)

    # ------------------------------------------------------------------ #
    #  Callback — отзывы                                                   #
    # ------------------------------------------------------------------ #

    async def _handle_review_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query

        parts = query.data.split(":", 3)
        if len(parts) != 4:
            await query.answer()
            return
        _, mp, review_id, action = parts

        _orig_lines = (query.message.text or "").split('\n')
        _first_line = _orig_lines[0]
        _rv_line = next((l for l in _orig_lines if l.startswith('💬')), '')

        # Защита от двойного нажатия
        lock_key = f"review_lock:{review_id}"
        locked = await self._redis_get(lock_key)
        if locked:
            await query.answer(f"✅ Уже обработал: {locked}", show_alert=False)
            try:
                await query.edit_message_text(
                    f"✅ Обработано — {locked}\n{_first_line}",
                    reply_markup=None,
                )
            except Exception:
                pass
            return

        user = query.from_user
        first_name = (user.first_name if user else None) or "Участник"
        await self._redis_set(lock_key, first_name, ttl=300)
        await query.answer()

        # Если кнопка нажата в группе — pending_edit ставим на group_id
        msg_chat_id = query.message.chat_id
        # Для получения магазина используем chat_id владельца из БД (marketplace_reviews)
        from db import get_pending_reviews, update_review_status, get_marketplace_shops
        from db import get_pool

        # Ищем владельца отзыва
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT chat_id FROM marketplace_reviews WHERE marketplace=$1 AND review_id=$2",
                mp, review_id,
            )
        owner_chat_id = row["chat_id"] if row else msg_chat_id

        if action in ("approve", "retry"):
            reviews = await get_pending_reviews(owner_chat_id)
            rv = next((r for r in reviews if r["review_id"] == review_id), None)
            reply_text = (rv or {}).get("generated_reply", "")
            if rv and reply_text:
                shop = next(
                    (s for s in await get_marketplace_shops(owner_chat_id) if s["marketplace"] == mp),
                    None,
                )
                if shop and await self._send_to_marketplace(shop, review_id, reply_text):
                    await update_review_status(mp, review_id, "replied", final_reply=reply_text)
                    from datetime import datetime as _dt
                    _now = _dt.now(_UTC).strftime("%d %b, %H:%M")
                    await query.edit_message_text(
                        f"✅ Ответ отправлен — {first_name}\n{_first_line}\n🕐 {_now}",
                        reply_markup=None,
                    )
                    return
            # Ответ не ушёл — освобождаем лок и даём повторить
            await self._redis_set(lock_key, "", ttl=1)
            retry_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Повторить", callback_data=f"rev:{mp}:{review_id}:retry"),
            ]])
            await query.edit_message_text(
                f"❌ Не удалось отправить\n{_first_line}",
                reply_markup=retry_kb,
            )

        elif action == "edit":
            edit_text = f"✏️ {first_name} редактирует\n{_first_line}"
            if _rv_line:
                edit_text += f"\n\n{_rv_line}"
            edit_text += "\n\nНапишите ваш вариант ответа:"
            await query.edit_message_text(edit_text, reply_markup=None)
            # pending_edit привязан к чату где нажали кнопку (личка или группа)
            await self._redis_set(f"pending_edit:{msg_chat_id}", f"{mp}:{review_id}:{owner_chat_id}", ttl=600)

        elif action == "skip":
            await update_review_status(mp, review_id, "skipped")
            await query.edit_message_text(
                f"🚫 Пропущено — {first_name}\n{_first_line}",
                reply_markup=None,
            )

    async def _handle_edit_reply(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        from telegram import Chat
        if update.effective_chat and update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            logger.debug(f"[max:handler] _handle_edit_reply вызван из группы — текст: {update.message.text[:50] if update.message and update.message.text else '?'}")
        chat_id = update.effective_chat.id

        # Сначала проверяем ожидание кастомной цены от репрайсера
        if await self._handle_reprice_text(update, context):
            return

        pending = await self._redis_get(f"pending_edit:{chat_id}")
        if not pending:
            return

        await self._redis_set(f"pending_edit:{chat_id}", "", ttl=1)
        # Формат вопроса: q:mp:question_id:owner_chat_id
        # Формат отзыва:  mp:review_id:owner_chat_id
        parts = pending.split(":", 3)
        reply_text = update.message.text.strip()
        first_name = (update.effective_user.first_name if update.effective_user else None) or "Участник"

        if parts[0] == "q":
            if len(parts) < 3:
                return
            mp, question_id = parts[1], parts[2]
            owner_chat_id = int(parts[3]) if len(parts) == 4 else chat_id
            from db import get_marketplace_shops, update_question_status
            shop = next(
                (s for s in await get_marketplace_shops(owner_chat_id) if s["marketplace"] == mp),
                None,
            )
            if shop:
                from tools.marketplace import make_client
                ok = await make_client(shop).answer_question(question_id, reply_text)
                if ok:
                    from datetime import datetime as _dt2
                    await update_question_status(
                        mp, question_id, "answered",
                        final_answer=reply_text,
                        answered_at=_dt2.now(_UTC),
                    )
                    await update.message.reply_text(f"✅ Ответ отредактирован и отправлен — {first_name}")
                    return
            await update.message.reply_text("❌ Не удалось отправить ответ.")
            return

        # Отзыв: формат mp:review_id или mp:review_id:owner_chat_id
        if len(parts) < 2:
            return
        mp, review_id = parts[0], parts[1]
        owner_chat_id = int(parts[2]) if len(parts) >= 3 else chat_id
        from db import get_marketplace_shops, update_review_status
        shop = next(
            (s for s in await get_marketplace_shops(owner_chat_id) if s["marketplace"] == mp),
            None,
        )
        if shop and await self._send_to_marketplace(shop, review_id, reply_text):
            await update_review_status(mp, review_id, "replied", final_reply=reply_text)
            await update.message.reply_text(f"✅ Ответ отредактирован и отправлен — {first_name}")
            return
        await update.message.reply_text("❌ Не удалось отправить ответ.")

    # ------------------------------------------------------------------ #
    #  Callback — вопросы покупателей                                      #
    # ------------------------------------------------------------------ #

    async def _handle_question_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query

        parts = query.data.split(":", 3)
        if len(parts) != 4:
            await query.answer()
            return
        _, mp, question_id, action = parts

        _orig_lines = (query.message.text or "").split('\n')
        _first_line = _orig_lines[0]
        _q_line = next((l for l in _orig_lines if l.startswith('💬')), '')

        lock_key = f"question_lock:{question_id}"
        locked = await self._redis_get(lock_key)
        if locked:
            await query.answer(f"✅ Уже обработал: {locked}", show_alert=False)
            try:
                await query.edit_message_text(
                    f"✅ Обработано — {locked}\n{_first_line}",
                    reply_markup=None,
                )
            except Exception:
                pass
            return

        user = query.from_user
        first_name = (user.first_name if user else None) or "Участник"
        await self._redis_set(lock_key, first_name, ttl=300)
        await query.answer()

        msg_chat_id = query.message.chat_id
        from db import get_pool, get_marketplace_shops, get_pending_questions, update_question_status

        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT chat_id FROM marketplace_questions WHERE marketplace=$1 AND question_id=$2",
                mp, question_id,
            )
        owner_chat_id = row["chat_id"] if row else msg_chat_id

        if action in ("approve", "retry"):
            questions = await get_pending_questions(owner_chat_id)
            q = next((r for r in questions if r["question_id"] == question_id), None)
            if q is None:
                # Вопрос мог быть уже помечен answered/skipped, но WB до сих пор отдаёт его
                async with pool.acquire() as _conn:
                    _qrow = await _conn.fetchrow(
                        "SELECT generated_answer FROM marketplace_questions "
                        "WHERE marketplace=$1 AND question_id=$2",
                        mp, question_id,
                    )
                if _qrow:
                    q = {"generated_answer": _qrow["generated_answer"]}
                    logger.info(f"[Макс/retry] q={question_id[:8]} не в pending — берём answer из БД напрямую")
            answer_text = (q or {}).get("generated_answer", "")
            if answer_text:
                shop = next(
                    (s for s in await get_marketplace_shops(owner_chat_id) if s["marketplace"] == mp),
                    None,
                )
                if shop:
                    from tools.marketplace import make_client
                    ok = await make_client(shop).answer_question(question_id, answer_text)
                    if ok:
                        from datetime import datetime as _dt
                        await update_question_status(
                            mp, question_id,
                            status="answered",
                            final_answer=answer_text,
                            answered_at=_dt.now(_UTC),
                        )
                        # Защита от спам-петли: планировщик не будет сбрасывать статус 2 часа
                        await self._redis_set(f"q_answer_sent:{mp}:{question_id}", "1", ttl=7200)
                        _now = _dt.now(_UTC).strftime("%d %b, %H:%M")
                        await query.edit_message_text(
                            f"✅ Ответ отправлен — {first_name}\n{_first_line}\n🕐 {_now}",
                            reply_markup=None,
                        )
                        return
            # Ответ не ушёл — освобождаем лок и даём повторить
            await self._redis_set(lock_key, "", ttl=1)
            retry_kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Повторить", callback_data=f"qrev:{mp}:{question_id}:retry"),
            ]])
            await query.edit_message_text(
                f"❌ Не удалось отправить\n{_first_line}",
                reply_markup=retry_kb,
            )

        elif action == "edit":
            edit_text = f"✏️ {first_name} редактирует\n{_first_line}"
            if _q_line:
                edit_text += f"\n\n{_q_line}"
            edit_text += "\n\nНапишите ваш вариант ответа:"
            await query.edit_message_text(edit_text, reply_markup=None)
            await self._redis_set(
                f"pending_edit:{msg_chat_id}",
                f"q:{mp}:{question_id}:{owner_chat_id}",
                ttl=600,
            )

        elif action == "skip":
            await update_question_status(mp, question_id, "skipped")
            await query.edit_message_text(
                f"🚫 Пропущено — {first_name}\n{_first_line}",
                reply_markup=None,
            )

    # ------------------------------------------------------------------ #
    #  Команды (рабочие, но не в BotCommand меню)                         #
    # ------------------------------------------------------------------ #

    async def cmd_add_shop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from telegram import Chat
        if update.effective_chat and update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            logger.debug(f"[max:handler] cmd_add_shop вызван из группы — текст: {update.message.text[:50] if update.message and update.message.text else '?'}")
        args = context.args or []
        if len(args) < 2:
            await update.message.reply_text(
                "Использование:\n"
                "  /add_shop wb <api_token> [название]\n"
                "  /add_shop ozon <api_token> <client_id> [название]"
            )
            return
        mp, token = args[0].lower(), args[1]
        chat_id = update.effective_user.id
        if mp not in ("wb", "ozon"):
            await update.message.reply_text("Поддерживается: wb, ozon")
            return
        if mp == "ozon":
            if len(args) < 3:
                await update.message.reply_text("Для Ozon нужен client_id: /add_shop ozon <token> <client_id> [название]")
                return
            client_id = args[2]
            shop_name = args[3] if len(args) > 3 else None
        else:
            client_id = None
            shop_name = args[2] if len(args) > 2 else None
        from db import add_marketplace_shop
        await add_marketplace_shop(chat_id, mp, token, client_id=client_id, shop_name=shop_name)
        label = shop_name or _MP_LABELS.get(mp, mp)
        await update.message.reply_text(f"✅ Магазин <b>{label}</b> подключён.", parse_mode="HTML")

    async def _shops_text(self, chat_id: int) -> str:
        from db import get_marketplace_shops
        shops = await get_marketplace_shops(chat_id)
        if not shops:
            return "Магазинов нет. Используй /start чтобы подключить."
        lines = ["🛒 <b>Ваши магазины:</b>\n"]
        for s in shops:
            mp_label = _MP_LABELS.get(s["marketplace"], s["marketplace"])
            name = s.get("shop_name") or mp_label
            client = f" · client_id: {s['client_id']}" if s.get("client_id") else ""
            lines.append(f"• <b>{name}</b> ({mp_label}{client}) — ID {s['id']}")
        lines.append("\nДобавить: /add_shop ozon &lt;token&gt; &lt;client_id&gt; [название]")
        lines.append("Реклама Ozon: /set_performance &lt;client_id&gt; &lt;perf_id&gt; &lt;perf_secret&gt;")
        return "\n".join(lines)

    async def cmd_shops(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from telegram import Chat
        if update.effective_chat and update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            logger.debug(f"[max:handler] cmd_shops вызван из группы — текст: {update.message.text[:50] if update.message and update.message.text else '?'}")
        await update.message.reply_text(
            await self._shops_text(update.effective_user.id), parse_mode="HTML"
        )

    async def cmd_set_performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/set_performance <ozon_client_id> <perf_client_id> <perf_secret> — привязать Ozon Performance credentials к магазину."""
        from telegram import Chat
        if update.effective_chat and update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            logger.debug(f"[max:handler] cmd_set_performance вызван из группы")
        args = context.args or []
        if len(args) < 3:
            await update.message.reply_text(
                "Использование:\n"
                "<code>/set_performance &lt;ozon_client_id&gt; &lt;perf_client_id&gt; &lt;perf_secret&gt;</code>\n\n"
                "ozon_client_id — ID магазина из /shops\n"
                "perf_client_id и perf_secret — из кабинета Ozon Performance",
                parse_mode="HTML",
            )
            return
        ozon_client_id, perf_client_id, perf_secret = args[0], args[1], args[2]
        chat_id = update.effective_user.id
        from db import set_performance_credentials
        found = await set_performance_credentials(chat_id, ozon_client_id, perf_client_id, perf_secret)
        if found:
            await update.message.reply_text(
                f"✅ Performance credentials сохранены для магазина с client_id <code>{ozon_client_id}</code>.\n"
                "Реклама будет синхронизироваться при следующем /sync_adv",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                f"Магазин Ozon с client_id <code>{ozon_client_id}</code> не найден.\n"
                "Проверь список через /shops",
                parse_mode="HTML",
            )

    async def cmd_pending(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from telegram import Chat
        if update.effective_chat and update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            logger.debug(f"[max:handler] cmd_pending вызван из группы — текст: {update.message.text[:50] if update.message and update.message.text else '?'}")
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
        from telegram import Chat
        if update.effective_chat and update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            logger.debug(f"[max:handler] cmd_reviews вызван из группы — текст: {update.message.text[:50] if update.message and update.message.text else '?'}")
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
            f"📊 <b>Отзывы за сегодня:</b>\n\n"
            f"✅ Автоответ: {row['auto_replied']}\n"
            f"✅ Отправлено вручную: {row['replied']}\n"
            f"⏳ Ожидают одобрения: {row['pending']}\n"
            f"🚫 Пропущено: {row['skipped']}\n"
            f"📨 Всего новых: {row['total']}",
            parse_mode="HTML",
        )

    # ------------------------------------------------------------------ #
    #  Регистрация хендлеров                                               #
    # ------------------------------------------------------------------ #

    async def cmd_reset_checked(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/reset_checked — сбросить last_checked_at для всех магазинов (для отладки)."""
        from telegram import Chat
        if update.effective_chat and update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            logger.debug(f"[max:handler] cmd_reset_checked вызван из группы — текст: {update.message.text[:50] if update.message and update.message.text else '?'}")
        from db import reset_last_checked, get_marketplace_shops
        chat_id = update.effective_user.id
        await reset_last_checked(chat_id)
        shops = await get_marketplace_shops(chat_id)
        for s in shops:
            logger.info(
                f"[reset_checked] shop={s['marketplace']} last_checked_at={s.get('last_checked_at')}"
            )
        await update.message.reply_text("✅ last_checked_at сброшен для всех магазинов.")

    async def cmd_reset_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/reset_orders — очистить и пересинхронизировать заказы."""
        chat_id = update.effective_user.id
        from db import clear_orders
        wb_count   = await clear_orders(chat_id, "wb")
        ozon_count = await clear_orders(chat_id, "ozon")
        logger.info(f"[reset_orders] удалено WB: {wb_count}, Ozon: {ozon_count}")
        await self.sync_marketplace_data(chat_id)
        await update.message.reply_text("✅ Данные по заказам пересинхронизированы")

    async def cmd_sync(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync — вручную запустить синхронизацию данных и отправить сводку."""
        chat_id = update.effective_user.id
        target = update.effective_chat.id
        logger.info(f"[Макс/sync] команда получена от {update.effective_user.id}")
        await update.message.reply_text("⏳ Синхронизирую данные…")
        await self.send_daily_summary(owner_chat_id=chat_id, target_chat_id=target, bot=context.bot)
        logger.info("[Макс/sync] send_daily_summary завершён")
        await self._check_stock_alerts(chat_id)
        await update.message.reply_text(
            "💡 Данные обновлены — готов к анализу!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📊 Отчёт у Питера", callback_data="menu_c3:report"),
                InlineKeyboardButton("🔻 Воронка",        callback_data="menu_c3:funnel"),
            ]]),
        )

    async def cmd_sync_adv(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync_adv — вручную запустить синхронизацию рекламной статистики (WB + Ozon)."""
        chat_id = update.effective_user.id
        logger.info(f"[Макс/adv] команда /sync_adv получена от {chat_id}")
        await update.message.reply_text("⏳ Синхронизирую рекламную статистику…")
        try:
            await self.sync_ad_stats(chat_id)
            await update.message.reply_text(
                "✅ Реклама обновлена — данные в marketplace_adv_stats.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("💰 Смотреть ДРР у Питера", callback_data="menu_c3:drr"),
                ]]),
            )
            await self._check_drr_alerts(chat_id)
        except Exception as e:
            logger.error(f"[Макс/adv] /sync_adv ошибка: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка синка рекламы: {e}")

    async def cmd_sync_sku(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/sync_sku — подтянуть Ozon SKU в реестр товаров."""
        await update.message.reply_text("⏳ Запрашиваю Ozon SKU…")
        try:
            from db import get_pool
            from tools.marketplace import OzonClient
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT id, ozon_offer_id FROM product_mapping
                    WHERE ozon_offer_id IS NOT NULL AND (ozon_sku IS NULL OR ozon_sku = '')
                """)
            if not rows:
                await update.message.reply_text("✅ Все товары уже имеют Ozon SKU.")
                return

            offer_ids = [r["ozon_offer_id"] for r in rows]
            id_map = {r["ozon_offer_id"]: r["id"] for r in rows}

            # Запрос к Ozon API: offer_id → sku
            import aiohttp, json as _json
            from db import get_marketplace_shops
            shops = await get_marketplace_shops(update.effective_user.id)
            ozon_shop = next((s for s in shops if s["marketplace"] == "ozon"), None)
            if not ozon_shop:
                await update.message.reply_text("❌ Ozon-магазин не подключён. Настрой через /start")
                return
            headers = {
                "Client-Id":    ozon_shop["client_id"],
                "Api-Key":      ozon_shop["api_token"],
                "Content-Type": "application/json",
            }
            url = "https://api-seller.ozon.ru/v3/product/info/list"
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, headers=headers,
                    json={"offer_id": offer_ids},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    raw = await resp.text()
                    if resp.status != 200:
                        await update.message.reply_text(f"❌ Ozon API HTTP {resp.status}: {raw[:200]}")
                        return
                    data = _json.loads(raw)

            updated = 0
            async with pool.acquire() as conn:
                for item in (data.get("items") or []):
                    sku = str(item.get("sku") or "").strip()
                    offer_id = str(item.get("offer_id") or "").strip()
                    mapping_id = id_map.get(offer_id)
                    if sku and mapping_id:
                        await conn.execute(
                            "UPDATE product_mapping SET ozon_sku = $1 WHERE id = $2",
                            sku, mapping_id,
                        )
                        updated += 1

            logger.info(f"[Макс/sync_sku] обновлено: {updated}/{len(rows)}")
            await update.message.reply_text(
                f"✅ Ozon SKU обновлены: {updated} из {len(rows)}\n"
                f"Проверь /products — SKU=✓"
            )
        except Exception as e:
            logger.error(f"[Макс/sync_sku] ошибка: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def _catalog_text(self, chat_id: int) -> str:
        """Каталог товаров: с/с и текущие цены в виде <pre>-таблицы."""
        from db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT m.display_name,
                       m.wb_price, m.ozon_price, m.prices_updated_at,
                       MAX(c.cost) FILTER (WHERE c.marketplace = 'wb')   AS cost_wb,
                       MAX(c.cost) FILTER (WHERE c.marketplace = 'ozon') AS cost_ozon
                FROM product_mapping m
                LEFT JOIN product_costs c ON c.mapping_id = m.id
                GROUP BY m.id, m.display_name, m.wb_price, m.ozon_price, m.prices_updated_at
                ORDER BY m.display_name
            """)
        if not rows:
            return "📦 Реестр пуст.\nДобавь товар: <code>/map name=КБ50 wb=БК50гр ozon=КБ50</code>"

        def _fmt(price, cost):
            p = f"{price:.0f}₽" if price else "н/д"
            c = f"{cost:.0f}₽"  if cost  else "н/д"
            return p, c

        w = max(len(r["display_name"]) for r in rows)
        hdr = f"{'Товар':<{w}}  {'Цена':>5}  {'С/С':>5}  {'Цена':>5}  {'С/С':>5}"
        sub = f"{'':>{w}}  {'WB':>5}  {'WB':>5}  {'OZ':>5}  {'OZ':>5}"
        sep = "─" * len(hdr)
        table_lines = [hdr, sub, sep]
        no_cost = 0
        for r in rows:
            name = r["display_name"]
            wb_p, wb_c = _fmt(r["wb_price"],   r["cost_wb"])
            oz_p, oz_c = _fmt(r["ozon_price"], r["cost_ozon"])
            if not r["cost_wb"] and not r["cost_ozon"]:
                no_cost += 1
            table_lines.append(f"{name:<{w}}  {wb_p:>5}  {wb_c:>5}  {oz_p:>5}  {oz_c:>5}")

        # Метка обновления цен
        upd = None
        for r in rows:
            if r["prices_updated_at"]:
                upd = r["prices_updated_at"]
                break

        lines = [
            f"📦 <b>Каталог товаров</b>  ·  {len(rows)} позиций",
            "",
            "<pre>" + "\n".join(table_lines) + "</pre>",
        ]
        if upd:
            from datetime import timezone
            msk = upd.astimezone(timezone.utc).strftime("%d.%m %H:%M") + " UTC"
            lines.append(f"<i>Цены обновлены: {msk}</i>")
        else:
            lines.append("<i>Цены не загружены — запусти /sync</i>")
        if no_cost:
            lines.append(f"⚠️ Нет с/с у {no_cost} товаров → /cost")
        return "\n".join(lines)

    async def cmd_products(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/products — каталог товаров: цены и себестоимость."""
        chat_id = update.effective_chat.id
        try:
            text = await self._catalog_text(chat_id)
            await update.message.reply_text(
                text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📊 Маржа и рекомендации", callback_data="menu_cmd:cost"),
                ]]),
            )
        except Exception as e:
            logger.error(f"[Макс/products] ошибка: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def _margin_check_text(self, chat_id: int) -> str:
        """Маржинальность всех товаров с с/с — текст для /margin и menu_cmd:cost.

        WB: скользящие 35 дней — каждая строка финотчёта уже содержит qty+payout,
            поэтому данные актуальны (включает неполный текущий месяц).
        Ozon: последний полный календарный месяц — qty приходит только в строке
            реализации (последний день месяца), поэтому нельзя брать неполный месяц:
            payout есть, qty=0, маржа не считается.
        """
        from db import get_pool
        from config import config as cfg
        from datetime import date, timedelta

        TAX_RATE = cfg.NET_MARGIN_TAX_RATE
        TARGET   = cfg.TARGET_NET_MARGIN_PCT / 100.0
        denom    = (1 - TAX_RATE) - TARGET

        today = date.today()
        month_end   = today.replace(day=1) - timedelta(days=1)
        month_start = month_end.replace(day=1)
        wb_since    = today - timedelta(days=35)

        _MONTHS_RU = ["", "январь", "февраль", "март", "апрель", "май", "июнь",
                      "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
        ozon_label = f"{_MONTHS_RU[month_end.month]} {month_end.year}"
        wb_label   = f"{wb_since.strftime('%d.%m')}–{today.strftime('%d.%m')}"
        period_label = f"WB: {wb_label} / Ozon: {ozon_label}"

        pool = await get_pool()
        async with pool.acquire() as conn:
            cost_rows = await conn.fetch("""
                SELECT m.id, m.display_name, m.wb_article, m.ozon_sku,
                       m.wb_price, m.ozon_price,
                       MAX(c.cost) FILTER (WHERE c.marketplace = 'wb')   AS cost_wb,
                       MAX(c.cost) FILTER (WHERE c.marketplace = 'ozon') AS cost_ozon
                FROM product_mapping m
                JOIN product_costs c ON c.mapping_id = m.id
                GROUP BY m.id, m.display_name, m.wb_article, m.ozon_sku,
                         m.wb_price, m.ozon_price
                ORDER BY m.display_name
            """)
            if not cost_rows:
                return (
                    "💲 Себестоимость не задана ни для одного товара.\n"
                    "Добавь: <code>/cost КБ50 wb 541</code>"
                )
            fin_rows = await conn.fetch("""
                SELECT
                    COALESCE(m.display_name, f.product_id) AS name,
                    SUM(f.quantity) FILTER (WHERE f.marketplace = 'wb')::int             AS qty_wb,
                    SUM(f.payout)   FILTER (WHERE f.marketplace = 'wb')::numeric(12,2)   AS payout_wb,
                    SUM(f.quantity) FILTER (WHERE f.marketplace = 'ozon')::int           AS qty_ozon,
                    SUM(f.payout)   FILTER (WHERE f.marketplace = 'ozon')::numeric(12,2) AS payout_ozon
                FROM marketplace_financial_report f
                LEFT JOIN product_mapping m
                    ON (f.marketplace = 'wb'   AND LOWER(REPLACE(m.wb_article, ',', '.')) = LOWER(REPLACE(f.product_id, ',', '.')))
                    OR (f.marketplace = 'ozon' AND m.ozon_sku = f.product_id)
                WHERE f.chat_id = $1 AND (
                    (f.marketplace = 'wb'   AND f.report_date >= $2)
                    OR
                    (f.marketplace = 'ozon' AND f.report_date >= $3 AND f.report_date <= $4)
                )
                GROUP BY COALESCE(m.display_name, f.product_id)
            """, chat_id, wb_since, month_start, month_end)

        fin_by_name = {r["name"]: r for r in fin_rows}
        # Цены из product_mapping (обновляются автоматически при каждом /sync)
        price_by: dict[tuple, float] = {}
        for r in cost_rows:
            name = r["display_name"]
            if r["wb_price"]:
                price_by[(name, "wb")] = float(r["wb_price"])
            if r["ozon_price"]:
                price_by[(name, "ozon")] = float(r["ozon_price"])

        def _rec(name, mp, qty, payout, cost):
            if qty <= 0 or payout <= 0 or cost <= 0 or denom <= 0:
                return None
            avg_p = price_by.get((name, mp))
            if not avg_p:
                return None
            ppu = payout / qty
            if ppu <= 0:
                return None
            take_home = ppu / avg_p
            if take_home <= 0:
                return None
            return round((cost / denom) / take_home)

        target_pct = cfg.TARGET_NET_MARGIN_PCT

        def _cell(name, mp, cost, fin):
            """(price_str, margin_str, icon, rec_str) для одной ячейки WB или Ozon."""
            if cost is None:
                return None, None, None, None
            avg_p    = price_by.get((name, mp))
            price_str = f"{avg_p:.0f}₽" if avg_p else "н/д"
            if fin is None:
                return price_str, "—", "", ""
            qty    = int(fin[f"qty_{mp}"] or 0)
            payout = float(fin[f"payout_{mp}"] or 0)
            if qty <= 0 or payout <= 0:
                return price_str, "—", "", ""
            profit     = payout * (1 - TAX_RATE) - qty * cost
            margin_pct = round(profit / payout * 100, 1)
            icon    = "🟢" if margin_pct >= target_pct else ("🟡" if margin_pct >= 30 else "🔴")
            rec     = _rec(name, mp, qty, payout, cost)
            rec_str = f"→{rec}₽" if (rec and margin_pct < target_pct) else ""
            return price_str, f"{margin_pct}%", icon, rec_str

        # Одна строка на товар: WB и Ozon — отдельные ячейки
        rows: list[tuple] = []
        missing_fin = False

        for r in cost_rows:
            name      = r["display_name"]
            cost_wb   = float(r["cost_wb"])   if r["cost_wb"]   is not None else None
            cost_ozon = float(r["cost_ozon"]) if r["cost_ozon"] is not None else None
            fin       = fin_by_name.get(name)

            wb_c, wb_m, wb_i, wb_r = _cell(name, "wb",   cost_wb,   fin)
            oz_c, oz_m, oz_i, oz_r = _cell(name, "ozon", cost_ozon, fin)

            if fin is None and (cost_wb is not None or cost_ozon is not None):
                missing_fin = True
            elif fin is not None:
                if cost_wb   is not None and int(fin["qty_wb"]   or 0) <= 0: missing_fin = True
                if cost_ozon is not None and int(fin["qty_ozon"] or 0) <= 0: missing_fin = True

            rows.append((name, wb_c, wb_m, wb_i, wb_r, oz_c, oz_m, oz_i, oz_r))

        if not rows:
            return f"💲 Нет данных за {period_label}. Запусти /sync_fin."

        w_name  = max(len(r[0]) for r in rows)
        prices  = [c for r in rows for c in [r[1], r[5]] if c]
        margins = [m for r in rows for m in [r[2], r[6]] if m and m != "—"]
        recs    = [rc for r in rows for rc in [r[4], r[8]] if rc]
        w_price  = max((len(p) for p in prices),  default=5)
        w_margin = max((len(m) for m in margins), default=5)
        w_rec    = max((len(rc) for rc in recs),  default=0)
        blank    = " " * w_name

        def fmt_side(price, margin, icon, rec):
            p  = f"{(price  or 'н/д'):>{w_price}}"
            m  = f"{(margin or   '—'):>{w_margin}}"
            rc = (f"  {rec:>{w_rec}}" if rec else ("  " + " " * w_rec)) if w_rec else ""
            ic = f" {icon}" if icon else ""
            return f"{p}  {m}{rc}{ic}"

        hdr_fields = f"{'Цена':>{w_price}}  {'Маржа':>{w_margin}}"
        if w_rec:
            hdr_fields += f"  {'→Цель':>{w_rec}}"
        hdr = f"{'Товар':<{w_name}}  Пл  {hdr_fields}"
        sep = "─" * len(hdr)

        table_lines = [hdr, sep]
        for r in rows:
            name, wb_c, wb_m, wb_i, wb_r, oz_c, oz_m, oz_i, oz_r = r
            table_lines.append(f"{name:<{w_name}}  WB  {fmt_side(wb_c, wb_m, wb_i, wb_r)}")
            if oz_c is not None:
                table_lines.append(f"{blank}  OZ  {fmt_side(oz_c, oz_m, oz_i, oz_r)}")

        out = [
            f"💲 <b>Себестоимость и маржа</b>  ·  {period_label}",
            "",
            "<pre>" + "\n".join(table_lines) + "</pre>",
            "",
            f"Цель: NET-маржа ≥ {int(target_pct)}%",
        ]
        if missing_fin:
            out.append(f"Нет данных за {period_label} → запусти /sync_fin")
        return "\n".join(out)

    async def cmd_margin_check(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/margin — маржинальность по всем товарам с рекомендованными ценами."""
        chat_id = update.effective_chat.id
        try:
            text = await self._margin_check_text(chat_id)
            await update.message.reply_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"[Макс/margin] ошибка: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def cmd_map(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/map name=X wb=Y ozon=Z [category=корм] — добавить/обновить товар в реестре.
        name обязателен. wb/ozon/category — опционально.
        Пример: /map name=КБ50 wb=БК50гр ozon=КБ50 category=корм"""
        params = {}
        for tok in (update.message.text or "").split()[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                params[k.strip().lower()] = v.strip()
        name = params.get("name")
        if not name:
            await update.message.reply_text(
                "Формат: /map name=КБ50 wb=БК50гр ozon=КБ50 category=корм\n"
                "name обязателен, остальное — опционально. Значения без пробелов."
            )
            return
        wb = params.get("wb") or None
        ozon = params.get("ozon") or None
        category = params.get("category") or None
        try:
            from db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO product_mapping (display_name, wb_article, ozon_offer_id, category)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (display_name)
                    DO UPDATE SET wb_article    = EXCLUDED.wb_article,
                                  ozon_offer_id = EXCLUDED.ozon_offer_id,
                                  category      = COALESCE(EXCLUDED.category, product_mapping.category)
                """, name, wb, ozon, category)
            cat_str = f" | Категория: {category}" if category else ""
            await update.message.reply_text(
                f"✅ Товар '{name}' сохранён.\nWB={wb or '—'} OZ={ozon or '—'}{cat_str}\n"
                f"Себестоимость: /cost {name} <сумма>"
            )
        except Exception as e:
            logger.error(f"[Макс/map] ошибка: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def cmd_camp(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/camp wb CAMPAIGN_ID ТОВАР1 ТОВАР2 ... — задать товары для WB рекламной кампании.

        WB API не возвращает список товаров для кампаний типов 4/5/6 (поиск/каталог/карточка).
        Эта команда позволяет вручную указать, какие товары рекламируются в кампании,
        чтобы Питер мог рассчитать ДРР по товарам.

        Пример: /camp wb 31530289 КБ50 ИМ0,7 ГБ1,5
        Пример с одним товаром: /camp wb 36201888 БК50гр"""
        args = (update.message.text or "").split()
        if len(args) < 4 or args[1].lower() != "wb":
            await update.message.reply_text(
                "Формат: /camp wb CAMPAIGN_ID ТОВАР1 ТОВАР2 ...\n"
                "Пример: /camp wb 31530289 КБ50 ИМ0,7\n\n"
                "ID кампании можно посмотреть в /logs или в ЛК WB."
            )
            return
        campaign_id = args[2].strip()
        product_names = args[3:]
        chat_id = update.effective_chat.id
        try:
            from db import get_pool, set_wb_campaign_nm_ids
            pool = await get_pool()
            # Ищем nm_id для каждого товара через product_mapping
            nm_ids: list[str] = []
            not_found: list[str] = []
            async with pool.acquire() as conn:
                for name in product_names:
                    row = await conn.fetchrow(
                        """SELECT wb_nm_id FROM product_mapping
                           WHERE wb_nm_id IS NOT NULL
                             AND (display_name ILIKE $1 OR wb_article ILIKE $1)
                           LIMIT 1""",
                        name,
                    )
                    if row and row["wb_nm_id"]:
                        nm_ids.append(row["wb_nm_id"])
                    else:
                        not_found.append(name)
            if not nm_ids:
                await update.message.reply_text(
                    f"❌ Ни один товар не найден в реестре с заполненным wb_nm_id.\n"
                    f"Сначала запусти /sync_adv чтобы заполнить nm_id, затем повтори /camp."
                )
                return
            await set_wb_campaign_nm_ids(campaign_id, nm_ids)
            ok_names = [n for n in product_names if n not in not_found]
            msg = (
                f"✅ Кампания {campaign_id} → {len(nm_ids)} товаров: {', '.join(ok_names)}\n"
                f"При следующем /sync_adv расход будет разбит по этим товарам."
            )
            if not_found:
                msg += f"\n⚠️ Не найдены (нет nm_id): {', '.join(not_found)}"
            await update.message.reply_text(msg)
        except Exception as e:
            logger.error(f"[Макс/camp] ошибка: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def cmd_cost(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/cost <идентификатор> <wb|ozon> <сумма> — задать себестоимость.
        Пример: /cost КБ50 wb 136.3"""
        args = (update.message.text or "").split()
        if len(args) != 4:
            await update.message.reply_text(
                "Формат: /cost <идентификатор> <wb|ozon> <себестоимость>\n"
                "Пример: /cost КБ50 wb 136.3"
            )
            return
        ident, mp, cost_str = args[1].strip(), args[2].strip().lower(), args[3].strip()
        if mp not in ("wb", "ozon"):
            await update.message.reply_text("Площадка: wb или ozon")
            return
        try:
            cost = float(cost_str.replace(",", "."))
        except ValueError:
            await update.message.reply_text(f"❌ '{cost_str}' — не число")
            return
        try:
            from db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow("""
                    SELECT id, display_name FROM product_mapping
                    WHERE display_name = $1 OR wb_article = $1 OR ozon_offer_id = $1
                """, ident)
                if not row:
                    await update.message.reply_text(
                        f"❌ Товар '{ident}' не найден. Добавь: /add"
                    )
                    return
                await conn.execute("""
                    INSERT INTO product_costs (mapping_id, marketplace, cost, updated_at)
                    VALUES ($1, $2, $3, now())
                    ON CONFLICT (mapping_id, marketplace)
                    DO UPDATE SET cost = $3, updated_at = now()
                """, row["id"], mp, cost)
            await update.message.reply_text(
                f"✅ {row['display_name']} [{mp.upper()}]: с/с = {cost} ₽"
            )
        except Exception as e:
            logger.error(f"[Макс/cost] ошибка: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def cmd_cost_wizard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup
        if update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            await update.message.reply_text("Управление каталогом — только в личке.")
            return
        args = (update.message.text or "").split()
        if len(args) == 4:
            await self.cmd_cost(update, context)
            return
        chat_id = update.effective_chat.id
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("WB",   callback_data="costpick:mp:wb"),
            InlineKeyboardButton("Ozon", callback_data="costpick:mp:ozon"),
            InlineKeyboardButton("❌ Отмена", callback_data="costpick:cancel"),
        ]])
        await update.message.reply_text("💰 Себестоимость — выбери площадку:", reply_markup=kb)

    async def _handle_catalog_cost_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        import json as _json
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        data = query.data

        if data == "costpick:cancel":
            await self._redis_set(f"catalog_cost:{chat_id}", "", ttl=1)
            await query.edit_message_text("Отменено.")
            return

        # Шаг 1: выбор площадки → показать товары
        if data.startswith("costpick:mp:"):
            mp = data.split(":")[-1]  # wb или ozon
            try:
                from db import get_pool
                pool = await get_pool()
                async with pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT id, display_name FROM product_mapping ORDER BY display_name"
                    )
                if not rows:
                    await query.edit_message_text("Реестр пуст. Добавь товар: /add")
                    return
                kb_rows = []
                row_pair = []
                for r in rows:
                    row_pair.append(InlineKeyboardButton(
                        r["display_name"],
                        callback_data=f"costpick:item:{mp}:{r['id']}"
                    ))
                    if len(row_pair) == 2:
                        kb_rows.append(row_pair)
                        row_pair = []
                if row_pair:
                    kb_rows.append(row_pair)
                kb_rows.append([InlineKeyboardButton("❌ Отмена", callback_data="costpick:cancel")])
                await query.edit_message_text(
                    f"💰 {mp.upper()} — выбери товар:",
                    reply_markup=InlineKeyboardMarkup(kb_rows)
                )
            except Exception as e:
                logger.error(f"[Макс/cost_callback] ошибка: {e}", exc_info=True)
                await query.edit_message_text(f"❌ Ошибка: {e}")
            return

        # Шаг 2: выбор товара → запросить сумму
        if data.startswith("costpick:item:"):
            _, _, mp, mid = data.split(":", 3)
            mapping_id = int(mid)
            try:
                from db import get_pool
                pool = await get_pool()
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT display_name FROM product_mapping WHERE id = $1", mapping_id
                    )
                if not row:
                    await query.edit_message_text("Товар не найден.")
                    return
                state = _json.dumps({
                    "mapping_id": mapping_id,
                    "marketplace": mp,
                    "name": row["display_name"]
                })
                await self._redis_set(f"catalog_cost:{chat_id}", state, ttl=300)
                logger.info(f"[Макс/cost_cb] Redis set catalog_cost:{chat_id} = {state!r}")
                await query.edit_message_text(
                    f"Товар: {row['display_name']} [{mp.upper()}]\n"
                    f"Введи себестоимость (₽):\n\n/cancel — отмена"
                )
            except Exception as e:
                logger.error(f"[Макс/cost_callback] ошибка: {e}", exc_info=True)
                await query.edit_message_text(f"❌ Ошибка: {e}")

    async def _handle_catalog_cost_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from telegram import Chat
        if update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            return
        chat_id = update.effective_chat.id
        raw = await self._redis_get(f"catalog_cost:{chat_id}")
        logger.info(f"[Макс/cost_text] chat={chat_id} raw={raw!r}")
        if not raw:
            return
        import json as _json
        state = _json.loads(raw)
        text = update.message.text.strip()
        try:
            cost = float(text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("Введи число, например 136.3")
            return
        await self._redis_set(f"catalog_cost:{chat_id}", "", ttl=1)
        try:
            from db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO product_costs (mapping_id, marketplace, cost, updated_at)
                    VALUES ($1, $2, $3, now())
                    ON CONFLICT (mapping_id, marketplace)
                    DO UPDATE SET cost = $3, updated_at = now()
                """, state["mapping_id"], state["marketplace"], cost)
                total = await conn.fetchval("SELECT COUNT(*) FROM product_costs")
            await update.message.reply_text(
                f"✅ {state['name']} [{state['marketplace'].upper()}]: с/с = {cost} ₽\n"
                f"Записей с себестоимостью: {total}"
            )
        except Exception as e:
            logger.error(f"[Макс/cost_text] ошибка: {e}", exc_info=True)
            await update.message.reply_text(f"❌ Ошибка: {e}")

    async def cmd_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from telegram import Chat
        if update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            await update.message.reply_text("Управление каталогом — только в личке.")
            return
        chat_id = update.effective_chat.id
        import json as _json
        await self._redis_set(f"catalog_add:{chat_id}", _json.dumps({"step": "name"}), ttl=300)
        await update.message.reply_text("📦 Добавление товара\n\nВведи название:\nНапример: КБ50\n\n/cancel — отмена")

    async def _handle_catalog_add_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from telegram import Chat, InlineKeyboardButton, InlineKeyboardMarkup
        if update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            return
        chat_id = update.effective_chat.id
        raw = await self._redis_get(f"catalog_add:{chat_id}")
        if not raw:
            return
        import json as _json
        state = _json.loads(raw)
        step = state.get("step")
        text = update.message.text.strip()

        if step == "name":
            state["name"] = text
            state["step"] = "category"
            await self._redis_set(f"catalog_add:{chat_id}", _json.dumps(state), ttl=300)
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("корм",      callback_data="addcat:корм"),
                    InlineKeyboardButton("лакомства", callback_data="addcat:лакомства"),
                ],
                [
                    InlineKeyboardButton("лёгкое",   callback_data="addcat:лёгкое"),
                    InlineKeyboardButton("корень",    callback_data="addcat:корень"),
                ],
                [
                    InlineKeyboardButton("✏️ Другое", callback_data="addcat:other"),
                    InlineKeyboardButton("Пропустить", callback_data="addcat:skip"),
                ],
            ])
            await update.message.reply_text(
                f"Товар: {text}\nКатегория товара?", reply_markup=kb
            )

        elif step == "category_text":
            state["category"] = text
            state["step"] = "platform"
            await self._redis_set(f"catalog_add:{chat_id}", _json.dumps(state), ttl=300)
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("WB",   callback_data="addmp:wb"),
                InlineKeyboardButton("Ozon", callback_data="addmp:ozon"),
                InlineKeyboardButton("Обе",  callback_data="addmp:both"),
            ]])
            await update.message.reply_text(
                f"Категория: {text}\nНа какой площадке?", reply_markup=kb
            )

        elif step == "wb_article":
            state["wb"] = text
            if state.get("need_ozon"):
                state["step"] = "ozon_offer"
                await self._redis_set(f"catalog_add:{chat_id}", _json.dumps(state), ttl=300)
                await update.message.reply_text("Артикул Ozon (offer_id)?")
            else:
                await self._ask_cost(update, chat_id, state)

        elif step == "ozon_offer":
            state["ozon"] = text
            await self._ask_cost(update, chat_id, state)

        elif step == "cost":
            try:
                cost = float(text.replace(",", "."))
            except ValueError:
                await update.message.reply_text("Введи число, например 136.3")
                return
            state["cost"] = cost
            await self._save_product(update, chat_id, state)

    async def _ask_cost(self, update: Update, chat_id: int, state: dict) -> None:
        import json as _json
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        state["step"] = "cost"
        await self._redis_set(f"catalog_add:{chat_id}", _json.dumps(state), ttl=300)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить", callback_data="addmp:skip_cost")]])
        await update.message.reply_text("Себестоимость (₽)? Или пропусти — задашь позже через /cost", reply_markup=kb)

    async def _save_product(self, update_or_query, chat_id: int, state: dict, edit: bool = False) -> None:
        name     = state.get("name")
        wb       = state.get("wb") or None
        ozon     = state.get("ozon") or None
        cost     = state.get("cost") or None
        category = state.get("category") or None
        await self._redis_set(f"catalog_add:{chat_id}", "", ttl=1)
        try:
            from db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow("""
                    INSERT INTO product_mapping (display_name, wb_article, ozon_offer_id, category)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (display_name)
                    DO UPDATE SET wb_article    = EXCLUDED.wb_article,
                                  ozon_offer_id = EXCLUDED.ozon_offer_id,
                                  category      = COALESCE(EXCLUDED.category, product_mapping.category)
                    RETURNING id
                """, name, wb, ozon, category)
                if cost is not None:
                    await conn.execute("""
                        INSERT INTO product_costs (mapping_id, cost, updated_at)
                        VALUES ($1, $2, now())
                        ON CONFLICT (mapping_id)
                        DO UPDATE SET cost = $2, updated_at = now()
                    """, row["id"], cost)
            cost_str = f", с/с {cost} ₽" if cost else " (с/с не задана)"
            cat_str  = f", категория: {category}" if category else ""
            text = f"✅ {name} сохранён{cost_str}{cat_str}\nWB={wb or '—'} OZ={ozon or '—'}"
            if edit:
                await update_or_query.edit_message_text(text)
            else:
                await update_or_query.message.reply_text(text)
        except Exception as e:
            logger.error(f"[Макс/add] ошибка: {e}", exc_info=True)
            err = f"❌ Ошибка: {e}"
            if edit:
                await update_or_query.edit_message_text(err)
            else:
                await update_or_query.message.reply_text(err)

    async def _handle_catalog_add_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        import json as _json
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        raw = await self._redis_get(f"catalog_add:{chat_id}")
        if not raw:
            await query.edit_message_text("Сессия истекла. Начни заново: /add")
            return
        state = _json.loads(raw)
        data  = query.data

        if data.startswith("addcat:"):
            cat_val = data.split(":", 1)[1]
            if cat_val == "skip":
                state["category"] = None
            elif cat_val == "other":
                state["step"] = "category_text"
                await self._redis_set(f"catalog_add:{chat_id}", _json.dumps(state), ttl=300)
                await query.edit_message_text("Введи категорию (например: добавки, корень, сухой корм):")
                return
            else:
                state["category"] = cat_val
            state["step"] = "platform"
            await self._redis_set(f"catalog_add:{chat_id}", _json.dumps(state), ttl=300)
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("WB",   callback_data="addmp:wb"),
                InlineKeyboardButton("Ozon", callback_data="addmp:ozon"),
                InlineKeyboardButton("Обе",  callback_data="addmp:both"),
            ]])
            cat_label = state.get("category") or "без категории"
            await query.edit_message_text(
                f"Товар: {state['name']} | Категория: {cat_label}\nНа какой площадке?",
                reply_markup=kb,
            )

        elif data == "addmp:wb":
            state["step"] = "wb_article"
            state["need_ozon"] = False
            await self._redis_set(f"catalog_add:{chat_id}", _json.dumps(state), ttl=300)
            await query.edit_message_text(f"Товар: {state['name']}\nАртикул WB?")

        elif data == "addmp:ozon":
            state["step"] = "ozon_offer"
            state["need_ozon"] = False
            await self._redis_set(f"catalog_add:{chat_id}", _json.dumps(state), ttl=300)
            await query.edit_message_text(f"Товар: {state['name']}\nАртикул Ozon (offer_id)?")

        elif data == "addmp:both":
            state["step"] = "wb_article"
            state["need_ozon"] = True
            await self._redis_set(f"catalog_add:{chat_id}", _json.dumps(state), ttl=300)
            await query.edit_message_text(f"Товар: {state['name']}\nАртикул WB?")

        elif data == "addmp:skip_cost":
            await self._save_product(query, chat_id, state, edit=True)

    async def _handle_catalog_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        from telegram import Chat
        if update.effective_chat.type in (Chat.GROUP, Chat.SUPERGROUP):
            return
        chat_id = update.effective_chat.id
        add_val = await self._redis_get(f"catalog_add:{chat_id}")
        cost_val = await self._redis_get(f"catalog_cost:{chat_id}")
        logger.info(f"[Макс/catalog_text] chat={chat_id} add={add_val!r} cost={cost_val!r}")
        if await self._redis_get(f"catalog_add:{chat_id}"):
            await self._handle_catalog_add_text(update, context)
        elif await self._redis_get(f"catalog_cost:{chat_id}"):
            await self._handle_catalog_cost_text(update, context)

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        await self._redis_set(f"catalog_add:{chat_id}",  "", ttl=1)
        await self._redis_set(f"catalog_cost:{chat_id}", "", ttl=1)
        await update.message.reply_text("Отменено.")

    # ------------------------------------------------------------------ #
    #  ИИ-агент в группе                                                  #
    # ------------------------------------------------------------------ #

    _AGENT_TOOLS = [
        {
            "name": "get_stats",
            "description": "Статистика по отзывам за N дней, по каждой площадке",
            "input_schema": {
                "type": "object",
                "properties": {"days": {"type": "integer", "default": 7}},
            },
        },
        {
            "name": "get_reviews",
            "description": "Список отзывов с фильтрами по площадке, рейтингу, периоду",
            "input_schema": {
                "type": "object",
                "properties": {
                    "marketplace": {"type": "string", "description": "wb или ozon"},
                    "min_rating":  {"type": "integer"},
                    "max_rating":  {"type": "integer"},
                    "days":        {"type": "integer", "default": 7},
                    "limit":       {"type": "integer", "default": 20},
                },
            },
        },
        {
            "name": "get_top_negative",
            "description": "Товары с наибольшим количеством отзывов 1-2★",
            "input_schema": {
                "type": "object",
                "properties": {"days": {"type": "integer", "default": 30}},
            },
        },
    ]

    async def _run_agent_tool(self, tool_name: str, tool_input: dict, owner_chat_id: int) -> str:
        """Выполнить инструмент агента и вернуть строку результата."""
        import json as _json
        from db import get_reviews_stats, get_reviews_by_filter, get_top_negative_products

        try:
            if tool_name == "get_stats":
                rows = await get_reviews_stats(owner_chat_id, days=tool_input.get("days", 7))
                return _json.dumps(rows, default=str, ensure_ascii=False) if rows else "Данных нет"

            if tool_name == "get_reviews":
                rows = await get_reviews_by_filter(
                    owner_chat_id,
                    marketplace=tool_input.get("marketplace"),
                    min_rating=tool_input.get("min_rating"),
                    max_rating=tool_input.get("max_rating"),
                    days=tool_input.get("days", 7),
                    limit=tool_input.get("limit", 20),
                )
                return _json.dumps(rows, default=str, ensure_ascii=False) if rows else "Данных нет"

            if tool_name == "get_top_negative":
                rows = await get_top_negative_products(
                    owner_chat_id, days=tool_input.get("days", 30)
                )
                return _json.dumps(rows, default=str, ensure_ascii=False) if rows else "Данных нет"

        except Exception as e:
            logger.error(f"[Макс/tool] {tool_name}: {e}")
            return f"Ошибка: {e}"

        return "Неизвестный инструмент"

    async def _handle_group_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE,
        override_text: str | None = None,
    ) -> None:
        """Реагировать на упоминание в группе — agentic loop с Claude.

        override_text — транскрибированный текст голосового (msg.text недоступен для voice).
        """
        msg = update.message
        if not msg:
            return
        if msg.from_user and msg.from_user.is_bot:
            return

        # Текст: явный override (голосовое) или msg.text
        text_to_use = override_text or msg.text or ""
        if not text_to_use:
            return

        logger.debug(
            f"[max:group] msg='{text_to_use[:50]}' "
            f"from={msg.from_user.first_name if msg.from_user else '?'}"
        )

        # Триггер — хотя бы одно из трёх условий:
        bot_username = (context.bot.username or "").lower()

        # 1. @mention бота в entities (только для текстовых — у voice entities нет)
        has_mention = any(
            e.type == "mention"
            and (msg.text or "")[e.offset:e.offset + e.length].lstrip("@").lower() == bot_username
            for e in (msg.entities or [])
        )

        # 2. Текст начинается с "макс" / "макс," / "макс!" / "@макс"
        stripped = text_to_use.strip().lower()
        starts_with_max = any(
            stripped.startswith(prefix)
            for prefix in ("макс ", "макс,", "макс!", "макс\n", "@макс")
        ) or stripped == "макс"

        # 3. Reply на сообщение самого бота
        reply = msg.reply_to_message
        is_reply_to_bot = bool(
            reply and reply.from_user and reply.from_user.id == context.bot.id
        )

        # Голосовое с override_text считается уже прошедшим проверку триггера
        triggered = has_mention or starts_with_max or is_reply_to_bot

        logger.debug(
            f"[max:group] trigger check: mention={has_mention}, starts_with={starts_with_max}, "
            f"reply={is_reply_to_bot}, voice_override={bool(override_text)}"
        )

        if not triggered:
            logger.debug("[max:group] no trigger — ignoring")
            return

        first_name = msg.from_user.first_name if msg.from_user else "?"
        logger.info(f"[max:group] triggered by {first_name}: {text_to_use[:50]}")

        chat_id = msg.chat_id
        user_name = (msg.from_user.first_name if msg.from_user else None) or "Участник"
        user_text = text_to_use.strip()

        await context.bot.send_chat_action(chat_id, "typing")

        # История Redis
        import json as _json
        history_key = f"max_chat:{chat_id}"
        raw_hist = await self._redis_get(history_key)
        history: list[dict] = []
        try:
            history = _json.loads(raw_hist) if raw_hist else []
        except Exception:
            history = []

        history.append({"role": "user", "content": user_text, "name": user_name})
        history = history[-10:]

        # owner_chat_id — первый активный магазин
        from db import get_all_active_shops
        shops = await get_all_active_shops()
        owner_chat_id = shops[0]["chat_id"] if shops else chat_id

        # System prompt
        from datetime import date
        system = (
            "Ты — Макс, ИИ-ассистент по управлению отзывами на маркетплейсах Wildberries и Ozon.\n"
            "Ты работаешь в команде продавца. Отвечай по-русски, кратко и по делу.\n"
            "Можешь получать данные об отзывах через инструменты.\n"
            f"Текущая дата: {date.today().isoformat()}"
        )

        # Claude messages (без поля name — API его не принимает)
        messages = [{"role": m["role"], "content": m["content"]} for m in history]

        reply_text = ""
        try:
            for _ in range(3):  # максимум 3 итерации
                response = await self.claude.messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=1024,
                    system=system,
                    tools=self._AGENT_TOOLS,
                    messages=messages,
                )

                if response.stop_reason == "end_turn":
                    for block in response.content:
                        if hasattr(block, "text"):
                            reply_text = block.text
                    break

                if response.stop_reason == "tool_use":
                    tool_results = []
                    for block in response.content:
                        if block.type == "tool_use":
                            result = await self._run_agent_tool(block.name, block.input, owner_chat_id)
                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            })
                    messages.append({"role": "assistant", "content": response.content})
                    messages.append({"role": "user", "content": tool_results})
                else:
                    break
        except Exception as e:
            logger.error(f"[Макс/agent] Claude error: {e}")
            reply_text = "⚠️ Не удалось получить ответ."

        if not reply_text:
            reply_text = "⚠️ Не удалось получить ответ."

        # Отправляем reply на сообщение пользователя
        try:
            await msg.reply_text(reply_text)
        except Exception:
            await context.bot.send_message(chat_id, reply_text)

        # Обновляем историю
        history.append({"role": "assistant", "content": reply_text, "name": "Макс"})
        history = history[-10:]
        await self._redis_set(history_key, _json.dumps(history, ensure_ascii=False), ttl=3600)

    def _help_text(self) -> str:
        return (
            "🛒 **Макс** — менеджер маркетплейсов WB и Ozon\n\n"
            "Отзывы, вопросы, синхронизация данных, SEO-мониторинг, управление ценами и ставками.\n\n"
            "📊 **Дашборд и статус:**\n"
            "/dashboard — открыть дашборд аналитики\n"
            "/data_status — когда последний раз синхронизировались данные\n"
            "/shop_kpi — KPI магазинов (выручка, заказы, конверсия)\n\n"
            "🔄 **Синхронизация данных:**\n"
            "/sync — заказы, остатки, отзывы (основная)\n"
            "/sync_adv — рекламная статистика\n"
            "/sync_fin — финансовые операции\n"
            "/sync_funnel — воронка конверсии (показы→корзина→заказ)\n"
            "/sync_cards — карточки товаров и фото\n"
            "/sync_keywords — поисковые запросы\n"
            "/sync_returns — возвраты\n"
            "/sync_sku — соответствие SKU/артикулов\n"
            "/sync_promotions — акции и скидки\n\n"
            "💬 **Отзывы и вопросы:**\n"
            "/reviews — вручную запустить обработку отзывов\n"
            "/pending — отзывы и вопросы, ожидающие ответа\n"
            "/questions — вопросы покупателей без ответа\n\n"
            "📦 **Товары и цены:**\n"
            "/products — список товаров и себестоимость\n"
            "/cost <артикул> <сумма> — задать себестоимость\n"
            "/margin — проверка маржи по всем товарам\n"
            "/reprice — предложения по изменению цен\n"
            "/apply_prices — применить рекомендации Питера\n"
            "/map name=X wb=Y ozon=Z — добавить товар в реестр\n\n"
            "🔍 **SEO и реклама:**\n"
            "/seo_check — алерты по падению позиций ≥10 мест\n"
            "/bid_adjust — корректировка рекламных ставок\n"
            "/campaigns — управление кампаниями Ozon\n"
            "/promotions — акции Ozon\n"
            "/new_campaign — создать кампанию Ozon\n"
            "/camp — детали кампании\n\n"
            "⚙️ **Настройка:**\n"
            "/shops — список подключённых магазинов\n"
            "/add — подключить новый магазин\n"
            "/cancel — отменить активный мастер\n\n"
            "💡 Фоновые задачи (авто): отзывы 09:00/14:00/20:00 МСК, "
            "ставки 06:30 МСК, KPI/воронка/возвраты — ежедневно ночью"
        )

    async def _check_stock_alerts(self, chat_id: int, *, deduplicate: bool = False) -> None:
        """Проверяет остатки и шлёт алерт если stock_days < threshold или stock = 0.

        Показывает ВСЕ позиции. lead_time и safety_days берутся из настроек пользователя.
        deduplicate=True: пропускает товары, по которым алерт уже отправлялся сегодня.
        """
        from config import config as _cfg
        import datetime
        from db import get_pool, get_user_setting
        from telegram import Bot as _TGBot

        # Читаем настройки пользователя (те же что и у Питера)
        raw_lead   = await get_user_setting(chat_id, "supply_lead_days")
        raw_safety = await get_user_setting(chat_id, "supply_safety_days")
        try:
            lead_time = int(raw_lead) if raw_lead else getattr(_cfg, "SUPPLY_LEAD_TIME_DAYS", 21)
        except ValueError:
            lead_time = getattr(_cfg, "SUPPLY_LEAD_TIME_DAYS", 21)
        try:
            safety_days = int(raw_safety) if raw_safety else getattr(_cfg, "SUPPLY_SAFETY_STOCK_DAYS", 14)
        except ValueError:
            safety_days = getattr(_cfg, "SUPPLY_SAFETY_STOCK_DAYS", 14)

        threshold = lead_time + safety_days  # порог алерта = срок поставки + страховой запас

        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT s.marketplace, s.product_id,
                           COALESCE(m.display_name, s.product_id) AS name,
                           SUM(s.stock)::int AS total_stock,
                           COALESCE(
                               (SELECT qty FROM marketplace_in_transit it
                                WHERE it.chat_id = $1
                                  AND it.marketplace = s.marketplace
                                  AND it.product_id = s.product_id
                                LIMIT 1), 0
                           )::int AS in_transit,
                           COALESCE(
                               (SELECT SUM(o.quantity) / 14.0
                                FROM marketplace_orders o
                                WHERE o.chat_id = $1
                                  AND o.marketplace = s.marketplace
                                  AND o.product_id  = CASE WHEN s.marketplace = 'ozon'
                                                            THEN COALESCE(m.ozon_sku, s.product_id)
                                                            ELSE s.product_id END
                                  AND o.order_date >= NOW() - INTERVAL '14 days'),
                               0
                           ) AS daily_velocity
                    FROM marketplace_stocks s
                    LEFT JOIN product_mapping m ON (
                        (s.marketplace = 'wb'   AND m.wb_article    = s.product_id) OR
                        (s.marketplace = 'ozon' AND m.ozon_offer_id = s.product_id)
                    )
                    WHERE s.chat_id = $1
                    GROUP BY s.marketplace, s.product_id, m.display_name, m.ozon_sku
                """, chat_id)

            today = datetime.date.today()
            critical_items: list[str] = []
            low_items: list[str] = []

            for r in rows:
                stock      = int(r["total_stock"] or 0)
                in_transit = int(r["in_transit"] or 0)
                vel        = float(r["daily_velocity"] or 0)
                days_left  = (stock / vel) if vel > 0 else (999 if stock > 0 else 0)
                mp_label   = "🟣 WB" if r["marketplace"] == "wb" else "🔵 Ozon"
                name       = r["name"]

                if stock == 0:
                    severity = "critical"
                    if vel > 0:
                        raw_order    = int((lead_time + safety_days) * vel)
                        qty_to_order = max(0, raw_order - in_transit)
                        if in_transit > 0:
                            line = (
                                f"{mp_label}  <b>{name}</b>\n"
                                f"  ❌ Нет стока · <code>{vel:.1f}</code> шт/день\n"
                                f"  🚚 В пути: <b>{in_transit} шт</b>\n"
                                f"  📦 Заказать: <b>{qty_to_order} шт</b> <i>(с учётом в пути)</i>"
                            )
                        else:
                            line = (
                                f"{mp_label}  <b>{name}</b>\n"
                                f"  ❌ Нет стока · <code>{vel:.1f}</code> шт/день\n"
                                f"  📦 Заказать: <b>{raw_order} шт</b>"
                            )
                    else:
                        line = f"{mp_label}  <b>{name}</b>\n  ❌ Нет стока · продаж нет"
                    target_list = critical_items

                elif days_left < threshold:
                    severity      = "low"
                    days_to_order = max(0, int(days_left) - lead_time)
                    stockout_date = today + datetime.timedelta(days=int(days_left))
                    order_by_date = today + datetime.timedelta(days=days_to_order)
                    raw_order     = int((lead_time + safety_days) * vel) if vel > 0 else 0
                    qty_to_order  = max(0, raw_order - in_transit)

                    if raw_order > 0 and in_transit >= raw_order:
                        line = (
                            f"{mp_label}  <b>{name}</b>\n"
                            f"  📦 <code>{stock} шт</code> · <code>{int(days_left)} дн</code> · стокаут <b>{stockout_date.strftime('%d.%m')}</b>\n"
                            f"  🚚 В пути {in_transit} шт — покроет потребность ✅"
                        )
                    elif qty_to_order > 0:
                        urgency = "❗ Заказать сейчас" if days_to_order == 0 else f"🗓 До {order_by_date.strftime('%d.%m')}"
                        transit_note = f" <i>(с учётом {in_transit} в пути)</i>" if in_transit > 0 else ""
                        transit_line = f"\n  🚚 В пути: <b>{in_transit} шт</b>" if in_transit > 0 else ""
                        line = (
                            f"{mp_label}  <b>{name}</b>\n"
                            f"  📦 <code>{stock} шт</code> · <code>{int(days_left)} дн</code> · стокаут <b>{stockout_date.strftime('%d.%m')}</b>\n"
                            f"  📊 <code>{vel:.1f}</code> шт/день"
                            f"{transit_line}\n"
                            f"  {urgency}: <b>{qty_to_order} шт</b>{transit_note}"
                        )
                    else:
                        line = (
                            f"{mp_label}  <b>{name}</b>\n"
                            f"  📦 <code>{stock} шт</code> · <code>{int(days_left)} дн</code> (нет данных о продажах)"
                        )
                    target_list = low_items
                else:
                    continue

                if deduplicate:
                    rkey = f"stock_alert:{chat_id}:{r['marketplace']}:{r['product_id']}:{severity}"
                    if await self._redis_get(rkey):
                        continue
                    await self._redis_set(rkey, "1", ttl=23 * 3600)

                target_list.append(line)

            all_items = critical_items + low_items
            if not all_items:
                return

            # Строим сообщение — все позиции без пагинации
            parts: list[str] = [
                f"📦 <b>Сток-алерт</b>  <i>{today.strftime('%d.%m')}</i>\n"
                f"<i>Доставка {lead_time} дн · запас {safety_days} дн</i>"
            ]

            if critical_items:
                parts.append(
                    f"🔴 <b>НЕТ ОСТАТКОВ</b> — {len(critical_items)} поз.\n"
                    + "━━━━━━━━━━━━━━━━\n"
                    + "\n\n".join(critical_items)
                )

            if low_items:
                parts.append(
                    f"⚠️ <b>ЗАКАНЧИВАЕТСЯ</b> — {len(low_items)} поз.\n"
                    + "━━━━━━━━━━━━━━━━\n"
                    + "\n\n".join(low_items)
                )

            text = "\n\n".join(parts)

            marta_bot = _TGBot(token=_cfg.MARTA_BOT_TOKEN)
            await marta_bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            logger.info(f"[Макс/stock_alerts] chat={chat_id} алертов: {len(all_items)} (lead={lead_time}, safety={safety_days})")
        except Exception as e:
            logger.error(f"[Макс/stock_alerts] ошибка: {e}", exc_info=True)

    # ─── Репрайсинг ───────────────────────────────────────────────────────────

    async def _collect_reprice_suggestions(self, chat_id: int) -> list[dict]:
        """Собирает данные по каждому товару/маркетплейсу и формирует рекомендации по ценам.

        Сигналы:
          1. ДРР > 35% И маржа > 40% → поднять +10% (реклама дорогая, маржа позволяет)
          2. Дней остатков < 14 → поднять +7% (товар популярен)
          3. NET-маржа < 20% → только алерт, без изменения цены
        """
        from db import get_pool
        from config import config as _cfg

        TAX = _cfg.NET_MARGIN_TAX_RATE

        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    m.display_name      AS name,
                    m.wb_article,
                    m.wb_nm_id,
                    m.ozon_offer_id,
                    m.wb_price,
                    m.ozon_price,
                    MAX(c.cost) FILTER (WHERE c.marketplace = 'wb')   AS cost_wb,
                    MAX(c.cost) FILTER (WHERE c.marketplace = 'ozon') AS cost_ozon,

                    /* DRR WB за 7 дней */
                    -- marketplace_orders.product_id для WB = wb_article, а product_adv_stats.product_id = nm_id
                    -- используем m.wb_article из внешнего запроса (m.wb_nm_id = a.product_id гарантирует маппинг)
                    (SELECT CASE WHEN COALESCE(SUM(o.seller_price * o.quantity), 0) > 0
                                 THEN ROUND(SUM(a.spend) /
                                            SUM(o.seller_price * o.quantity) * 100, 1)
                                 ELSE NULL END
                     FROM product_adv_stats a
                     LEFT JOIN marketplace_orders o
                            ON o.chat_id = $1 AND o.marketplace = 'wb'
                           AND o.product_id = m.wb_article
                           AND o.order_date >= NOW() - INTERVAL '7 days'
                     WHERE a.chat_id = $1 AND a.marketplace = 'wb'
                       AND a.stat_date >= NOW() - INTERVAL '7 days'
                       AND a.product_id = m.wb_nm_id
                    ) AS drr_wb,

                    /* DRR Ozon за 7 дней */
                    (SELECT CASE WHEN COALESCE(SUM(o.seller_price * o.quantity), 0) > 0
                                 THEN ROUND(SUM(a.spend) /
                                            SUM(o.seller_price * o.quantity) * 100, 1)
                                 ELSE NULL END
                     FROM product_adv_stats a
                     LEFT JOIN marketplace_orders o
                            ON o.chat_id = $1 AND o.marketplace = 'ozon'
                           AND o.product_id = a.product_id
                           AND o.order_date >= NOW() - INTERVAL '7 days'
                     WHERE a.chat_id = $1 AND a.marketplace = 'ozon'
                       AND a.stat_date >= NOW() - INTERVAL '7 days'
                       AND a.product_id = m.ozon_sku
                    ) AS drr_ozon,

                    /* Дней остатков WB */
                    (SELECT CASE WHEN COALESCE(SUM(o2.quantity), 0) > 0
                                 THEN FLOOR(COALESCE(SUM(s.stock), 0)::float
                                            / (SUM(o2.quantity) / 14.0))
                                 ELSE NULL END
                     FROM marketplace_stocks s
                     LEFT JOIN marketplace_orders o2
                            ON o2.chat_id = $1 AND o2.marketplace = 'wb'
                           AND o2.product_id = m.wb_article
                           AND o2.order_date >= NOW() - INTERVAL '14 days'
                     WHERE s.chat_id = $1 AND s.marketplace = 'wb'
                       AND s.product_id = m.wb_article
                    ) AS days_wb,

                    /* Дней остатков Ozon */
                    (SELECT CASE WHEN COALESCE(SUM(o2.quantity), 0) > 0
                                 THEN FLOOR(COALESCE(SUM(s.stock), 0)::float
                                            / (SUM(o2.quantity) / 14.0))
                                 ELSE NULL END
                     FROM marketplace_stocks s
                     LEFT JOIN marketplace_orders o2
                            ON o2.chat_id = $1 AND o2.marketplace = 'ozon'
                           AND o2.product_id = COALESCE(m.ozon_sku, s.product_id)
                           AND o2.order_date >= NOW() - INTERVAL '14 days'
                     WHERE s.chat_id = $1 AND s.marketplace = 'ozon'
                       AND s.product_id = m.ozon_offer_id
                    ) AS days_ozon
                FROM product_mapping m
                JOIN product_costs c ON c.mapping_id = m.id
                GROUP BY m.id, m.display_name, m.wb_article, m.wb_nm_id,
                         m.ozon_offer_id, m.ozon_sku, m.wb_price, m.ozon_price
            """, chat_id)

        suggestions = []

        for r in rows:
            name = r["name"]

            for mp in ("wb", "ozon"):
                price = float(r[f"{mp}_price"] or 0)
                cost  = float(r[f"cost_{mp}"] or 0)
                drr   = float(r[f"drr_{mp}"] or 0) if r[f"drr_{mp}"] is not None else None
                days  = float(r[f"days_{mp}"] or 0) if r[f"days_{mp}"] is not None else None

                # Нет цены или себестоимости — пропустить
                if price <= 0 or cost <= 0:
                    continue

                # Маржа (упрощённо: без финотчёта, только по цене и себестоимости)
                margin_pct = (price - cost) / price * (1 - TAX) * 100 if price > 0 else 0

                delta_pct = 0
                reasons: list[str] = []

                if drr is not None and drr > 35 and margin_pct > 40:
                    delta_pct = max(delta_pct, 10)
                    reasons.append(f"ДРР {drr:.0f}% высокий, маржа {margin_pct:.0f}% позволяет")

                if days is not None and days < 14:
                    delta_pct = max(delta_pct, 7)
                    reasons.append(f"остаток {int(days)} дн — товар популярен")

                low_margin = margin_pct < 20

                if not reasons and not low_margin:
                    continue  # всё в норме — не предлагать

                mp_label = "🟣 WB" if mp == "wb" else "🔵 Ozon"
                product_id = r["wb_article"] if mp == "wb" else r["ozon_offer_id"]
                nm_id      = r["wb_nm_id"]   if mp == "wb" else None
                offer_id   = r["ozon_offer_id"] if mp == "ozon" else None

                if not product_id:
                    continue

                new_price = round(price * (1 + delta_pct / 100)) if delta_pct > 0 else price

                suggestions.append({
                    "marketplace": mp,
                    "mp_label":    mp_label,
                    "name":        name,
                    "product_id":  product_id,
                    "nm_id":       nm_id,
                    "offer_id":    offer_id,
                    "current_price": price,
                    "new_price":   new_price,
                    "delta_pct":   delta_pct,
                    "cost":        cost,
                    "margin_pct":  round(margin_pct, 1),
                    "drr":         drr,
                    "days_left":   days,
                    "low_margin":  low_margin,
                    "reason":      "; ".join(reasons) if reasons else "маржа ниже 20%",
                    "actionable":  delta_pct > 0,
                })

        return suggestions

    async def cmd_reprice(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/reprice — рекомендации по изменению цен с подтверждением."""
        chat_id = update.effective_user.id

        lock_key = f"reprice_lock:{chat_id}"
        if await self._redis_get(lock_key):
            await update.message.reply_text(
                "⏳ Уже есть активные предложения по ценам.\n"
                "Ответь на них или подожди 10 минут."
            )
            return

        await update.message.reply_text("🔍 Анализирую цены и сигналы…")

        suggestions = await self._collect_reprice_suggestions(chat_id)
        if not suggestions:
            await update.message.reply_text(
                "✅ Все цены выглядят нормально — сигналов для изменения нет.\n\n"
                "Проверь что заданы себестоимости (/cost) и синхронизированы финотчёты (/sync_fin)."
            )
            return

        await self._redis_set(lock_key, "1", ttl=600)

        sent = 0
        for s in suggestions[:10]:
            lines = [
                f"{s['mp_label']} <b>{s['name']}</b>",
                f"Текущая цена: <b>{s['current_price']:.0f} ₽</b>  |  Себестоим.: {s['cost']:.0f} ₽",
                f"Маржа: {s['margin_pct']:.0f}%"
                + (f"  |  ДРР: {s['drr']:.0f}%" if s["drr"] else "")
                + (f"  |  Остаток: {s['days_left']:.0f} дн." if s["days_left"] else ""),
            ]
            if s["actionable"]:
                lines += [
                    "",
                    f"💡 Рекомендация: +{s['delta_pct']}% → <b>{s['new_price']:.0f} ₽</b>",
                    f"Причина: {s['reason']}",
                ]
                pid   = s["product_id"][:20]  # не превышать лимит callback_data
                price = int(s["new_price"])
                mp    = s["marketplace"]
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Применить", callback_data=f"rp:{mp}:{pid}:{price}:apply"),
                    InlineKeyboardButton("❌ Пропустить", callback_data=f"rp:{mp}:{pid}:{price}:skip"),
                    InlineKeyboardButton("✏️ Изменить",  callback_data=f"rp:{mp}:{pid}:{price}:edit"),
                ]])
            else:
                lines += ["", f"⚠️ {s['reason']} — цену менять не рекомендую, проверь себестоимость"]
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Понял", callback_data=f"rp:{mp}:{pid}:{int(s['current_price'])}:skip"),
                ]])

            await self.app.bot.send_message(
                chat_id=chat_id,
                text="\n".join(lines),
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            sent += 1

        if sent == 0:
            await self.app.bot.send_message(chat_id=chat_id, text="✅ Предложений нет.")

    async def _handle_reprice_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()

        # Формат: rp:{mp}:{product_id}:{new_price}:{action}
        parts = query.data.split(":", 4)
        if len(parts) != 5:
            return
        _, mp, product_id, price_str, action = parts
        chat_id = query.from_user.id
        new_price = int(price_str)

        lock_key = f"reprice_lock:{chat_id}"

        if action == "skip":
            await query.edit_message_text(
                query.message.text + "\n\n🚫 Пропущено",
                reply_markup=None,
            )
            return

        if action == "edit":
            await query.edit_message_text(
                query.message.text + "\n\n✏️ Введи новую цену (только цифры):",
                reply_markup=None,
            )
            await self._redis_set(
                f"pending_reprice:{chat_id}",
                f"{mp}:{product_id}:{new_price}",
                ttl=300,
            )
            return

        if action == "apply":
            lock = f"reprice_apply:{mp}:{product_id}"
            if await self._redis_get(lock):
                await query.answer("Уже применяется…", show_alert=False)
                return
            await self._redis_set(lock, "1", ttl=60)

            ok = await self._apply_price(chat_id, mp, product_id, new_price)
            suffix = f"\n\n{'✅ Цена обновлена → {new_price} ₽'.format(new_price=new_price) if ok else '❌ Ошибка при обновлении цены — проверь логи'}"
            await query.edit_message_text(
                query.message.text + suffix,
                reply_markup=None,
                parse_mode="HTML",
            )
            # Снимаем lock репрайсера если все карточки обработаны
            await self._redis_set(lock_key, "", ttl=1)

    async def _handle_reprice_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> bool:
        """Обрабатывает ввод кастомной цены после нажатия ✏️ Изменить.

        Возвращает True если сообщение было обработано как ввод репрайсера.
        """
        chat_id = update.effective_user.id
        raw = (update.message.text or "").strip()
        pending = await self._redis_get(f"pending_reprice:{chat_id}")
        if not pending:
            return False

        if not raw.isdigit():
            await update.message.reply_text("❌ Введи только число (цену в рублях).")
            return True

        mp, product_id, _ = pending.split(":", 2)
        new_price = int(raw)
        await self._redis_set(f"pending_reprice:{chat_id}", "", ttl=1)

        ok = await self._apply_price(chat_id, mp, product_id, new_price)
        if ok:
            await update.message.reply_text(f"✅ Цена обновлена → {new_price} ₽")
        else:
            await update.message.reply_text("❌ Ошибка при обновлении цены — проверь логи")
        return True

    async def _apply_price(self, chat_id: int, mp: str, product_id: str, new_price: int) -> bool:
        """Отправляет новую цену на WB или Ozon. Возвращает True при успехе."""
        from db import get_marketplace_shops, get_pool
        from tools.marketplace import WBClient, OzonClient

        shops = await get_marketplace_shops(chat_id)
        shop  = next((s for s in shops if s["marketplace"] == mp), None)
        if not shop:
            logger.error(f"[Макс/reprice] магазин {mp} не найден для chat_id={chat_id}")
            return False

        try:
            if mp == "wb":
                # Нужен wb_nm_id для WB price API
                pool = await get_pool()
                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        "SELECT wb_nm_id FROM product_mapping WHERE wb_article = $1",
                        product_id,
                    )
                if not row or not row["wb_nm_id"]:
                    logger.error(f"[Макс/reprice] wb_nm_id не найден для {product_id}")
                    return False
                client = WBClient(shop["api_token"])
                result = await client.update_prices([{"nm_id": int(row["wb_nm_id"]), "price": new_price}])
                ok = result.get("success", False)

            else:  # ozon
                client = OzonClient(shop["api_token"], shop["client_id"])
                result = await client.update_prices([{"offer_id": product_id, "price": new_price}])
                ok = result.get("success", False)

            if ok:
                # Обновить локально чтобы margin_check сразу показал новую цену
                pool = await get_pool()
                price_col = "wb_price" if mp == "wb" else "ozon_price"
                id_col    = "wb_article" if mp == "wb" else "ozon_offer_id"
                async with pool.acquire() as conn:
                    await conn.execute(
                        f"UPDATE product_mapping SET {price_col} = $1, prices_updated_at = NOW() WHERE {id_col} = $2",
                        float(new_price), product_id,
                    )
                logger.info(f"[Макс/reprice] {mp} {product_id} → {new_price} ₽ ✓")
            return ok

        except Exception as e:
            logger.error(f"[Макс/reprice] ошибка: {e}", exc_info=True)
            return False

    async def _check_drr_alerts(self, chat_id: int) -> None:
        """Проверяет ДРР по товарам за 7 дней и шлёт алерт если ДРР > 25% при расходе > 500₽."""
        try:
            from db import get_pool
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT
                        p.marketplace,
                        p.product_id,
                        COALESCE(m.display_name, p.product_id) AS name,
                        p.spend,
                        COALESCE(s.buyouts, 0)::numeric(12,2)  AS revenue,
                        CASE WHEN COALESCE(s.buyouts, 0) > 0
                             THEN ROUND(p.spend / s.buyouts * 100, 1)
                             ELSE NULL END                      AS drr
                    FROM (
                        -- Агрегируем расходы заранее, чтобы не было умножения строк при JOIN
                        SELECT marketplace, product_id,
                               SUM(spend)::numeric(12,2) AS spend
                        FROM product_adv_stats
                        WHERE chat_id = $1 AND stat_date >= NOW() - INTERVAL '7 days'
                        GROUP BY marketplace, product_id
                        HAVING SUM(spend) > 500
                    ) p
                    LEFT JOIN product_mapping m ON (
                        (p.marketplace = 'wb'   AND m.wb_nm_id  = p.product_id)
                        OR (p.marketplace = 'ozon' AND m.ozon_sku = p.product_id)
                    )
                    LEFT JOIN (
                        -- Выкупы (не заказы): WB хранит wb_article, Ozon — ozon_offer_id
                        -- транслируем в nm_id / ozon_sku через product_mapping
                        SELECT
                            sl.marketplace,
                            CASE WHEN sl.marketplace = 'wb'
                                 THEN COALESCE(mm.wb_nm_id, sl.product_id)
                                 WHEN sl.marketplace = 'ozon'
                                 THEN COALESCE(mm.ozon_sku, sl.product_id)
                                 ELSE sl.product_id END AS product_id,
                            SUM(sl.price * sl.quantity)::numeric(12,2) AS buyouts
                        FROM marketplace_sales sl
                        LEFT JOIN product_mapping mm ON (
                            (sl.marketplace = 'wb'   AND mm.wb_article    = sl.product_id)
                            OR (sl.marketplace = 'ozon' AND mm.ozon_offer_id = sl.product_id)
                        )
                        WHERE sl.chat_id = $1
                          AND sl.sale_date >= NOW() - INTERVAL '7 days'
                          AND sl.is_return = FALSE
                        GROUP BY sl.marketplace,
                                 CASE WHEN sl.marketplace = 'wb'
                                      THEN COALESCE(mm.wb_nm_id, sl.product_id)
                                      WHEN sl.marketplace = 'ozon'
                                      THEN COALESCE(mm.ozon_sku, sl.product_id)
                                      ELSE sl.product_id END
                    ) s ON s.marketplace = p.marketplace AND s.product_id = p.product_id
                    ORDER BY drr DESC NULLS LAST
                """, chat_id)

            import config as _cfg
            drr_alert_threshold = getattr(_cfg, "DRR_ALERT_THRESHOLD", 25)

            def _fmt(v: float) -> str:
                if v >= 1_000_000:
                    return f"{v/1_000_000:.1f}М₽"
                if v >= 1_000:
                    return f"{v/1_000:.1f}К₽"
                return f"{v:.0f}₽"

            wb_lines, ozon_lines = [], []
            for r in rows:
                drr = float(r["drr"] or 0)
                if drr <= drr_alert_threshold:
                    continue
                spend   = float(r["spend"]   or 0)
                revenue = float(r["revenue"] or 0)
                line = (
                    f"  • <b>{r['name']}</b> — {drr:.1f}%"
                    f"  (расход {_fmt(spend)} / выкупы {_fmt(revenue)})"
                )
                if r["marketplace"] == "wb":
                    wb_lines.append(line)
                else:
                    ozon_lines.append(line)

            if not wb_lines and not ozon_lines:
                return

            parts = ["🔴 <b>Высокий ДРР — последние 7 дней</b>"]
            if wb_lines:
                parts.append("\n🟣 <b>WB</b>")
                parts.extend(wb_lines[:5])
            if ozon_lines:
                parts.append("\n🔵 <b>Ozon</b>")
                parts.extend(ozon_lines[:5])

            total = len(wb_lines) + len(ozon_lines)
            shown = min(len(wb_lines), 5) + min(len(ozon_lines), 5)
            if total > shown:
                parts.append(f"\n…и ещё {total - shown} позиций")

            peter = getattr(self, "_peter_agent", None)
            parts.append(
                "\n📊 Питер анализирует — ответ придёт сейчас"
                if peter else
                "\n💡 Запроси /drr у Питера для детального анализа"
            )

            await self.app.bot.send_message(
                chat_id=chat_id, text="\n".join(parts), parse_mode="HTML"
            )
            logger.info(f"[Макс/drr_alerts] chat={chat_id} алертов: {len(alerts)}")

            if peter is not None:
                import asyncio as _asyncio
                _asyncio.create_task(peter.run_drr_for_chat(chat_id, days=7))
        except Exception as e:
            logger.error(f"[Макс/drr_alerts] ошибка: {e}", exc_info=True)

    # ------------------------------------------------------------------ #
    #  Авто-управление рекламными ставками                                #
    # ------------------------------------------------------------------ #

    async def _collect_bid_suggestions(self, chat_id: int) -> list[dict]:
        """Собирает кампании с аномальным ДРР и формирует рекомендации по ставкам."""
        from db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    p.marketplace,
                    p.campaign_id,
                    COALESCE(m.display_name, MAX(o.product_name), p.product_id) AS name,
                    SUM(p.spend)::numeric(12,2)                                 AS spend_7d,
                    COALESCE(SUM(o.seller_price * o.quantity), 0)::numeric(12,2) AS revenue_7d,
                    CASE WHEN COALESCE(SUM(o.seller_price * o.quantity), 0) > 0
                         THEN ROUND(SUM(p.spend) /
                              SUM(o.seller_price * o.quantity) * 100, 1)
                         ELSE NULL END AS drr
                FROM product_adv_stats p
                LEFT JOIN product_mapping m ON (
                    m.wb_nm_id = p.product_id OR m.ozon_sku = p.product_id
                )
                LEFT JOIN marketplace_orders o ON (
                    o.chat_id = p.chat_id
                    AND o.marketplace = p.marketplace
                    -- WB: marketplace_orders.product_id = wb_article, product_adv_stats.product_id = nm_id
                    AND (
                        (p.marketplace = 'wb'   AND o.product_id = m.wb_article)
                        OR (p.marketplace = 'ozon' AND o.product_id = p.product_id)
                    )
                    AND o.order_date >= NOW() - INTERVAL '7 days'
                )
                WHERE p.chat_id = $1
                  AND p.stat_date >= NOW() - INTERVAL '7 days'
                  AND p.campaign_id IS NOT NULL
                GROUP BY p.marketplace, p.campaign_id, p.product_id, m.display_name
                HAVING SUM(p.spend) > 200
                ORDER BY drr DESC NULLS LAST
            """, chat_id)

        import config as _cfg
        drr_pause_ozon = getattr(_cfg, "DRR_PAUSE_THRESHOLD_OZON", 40)

        suggestions = []
        seen_campaigns: set[str] = set()
        for r in rows:
            cid = r["campaign_id"]
            if cid in seen_campaigns:
                continue
            mp      = r["marketplace"]
            drr     = float(r["drr"] or 0)
            spend   = float(r["spend_7d"] or 0)
            revenue = float(r["revenue_7d"] or 0)
            # Определяем рекомендацию
            if mp == "ozon":
                if drr > 60:
                    direction, delta_pct, reason = "down", 0, f"ДРР {drr:.0f}% — критически высокий, рекомендую паузу"
                elif drr > drr_pause_ozon:
                    direction, delta_pct, reason = "down", 20, f"ДРР {drr:.0f}% — высокий, снизить ставки на 20%"
                elif 0 < drr < 8 and spend > 500:
                    direction, delta_pct, reason = "up", 15, f"ДРР {drr:.0f}% — низкий, есть запас для роста"
                else:
                    continue
            elif drr > 60:
                direction, delta_pct, reason = "down", 30, f"ДРР {drr:.0f}% — критически высокий"
            elif drr > 40:
                direction, delta_pct, reason = "down", 20, f"ДРР {drr:.0f}% — выше нормы"
            elif 0 < drr < 8 and spend > 500:
                direction, delta_pct, reason = "up", 15, f"ДРР {drr:.0f}% — низкий, есть запас"
            else:
                continue  # норма, не трогаем
            seen_campaigns.add(cid)
            suggestions.append({
                "marketplace":   r["marketplace"],
                "campaign_id":   cid,
                "name":          r["name"],
                "spend_7d":      spend,
                "revenue_7d":    revenue,
                "drr":           drr,
                "direction":     direction,
                "delta_pct":     delta_pct,
                "reason":        reason,
            })
        return suggestions

    async def auto_bid_suggest(self, chat_id: int) -> int:
        """Автоматический анализ ставок — вызывается планировщиком.

        WB: предлагает скорректировать CPM.
        Ozon: предлагает поставить кампанию на паузу (camp: callback).
        Возвращает количество отправленных предложений.
        """
        lock_key = f"bid_lock:{chat_id}"
        if await self._redis_get(lock_key):
            return 0

        suggestions = await self._collect_bid_suggestions(chat_id)
        if not suggestions:
            return 0

        # Для Ozon нужен shop_id чтобы корректно вызвать pause_campaign через camp: callback
        from db import get_marketplace_shops
        shops = await get_marketplace_shops(chat_id)
        ozon_shop = next((s for s in shops if s["marketplace"] == "ozon"), None)
        ozon_shop_id = str(ozon_shop["id"]) if ozon_shop else None

        await self._redis_set(lock_key, "1", ttl=600)
        await self.app.bot.send_message(
            chat_id=chat_id,
            text="🤖 <b>Авто-анализ ставок</b>\nПо данным за 7 дней есть предложения по корректировке:",
            parse_mode="HTML",
        )

        sent = 0
        for s in suggestions[:8]:
            mp = s["marketplace"]
            mp_label = "🟣 WB" if mp == "wb" else "🔵 Ozon"
            cid = s["campaign_id"]
            d   = s["direction"]
            dp  = s["delta_pct"]

            if mp == "ozon":
                if not ozon_shop_id:
                    continue
                if d == "down" and dp == 0:
                    # Критический ДРР → пауза кампании
                    text = (
                        f"{mp_label} <b>{s['name']}</b>\n"
                        f"ДРР за 7д: <b>{s['drr']:.0f}%</b>  "
                        f"(расход {s['spend_7d']:,.0f}₽ / выручка {s['revenue_7d']:,.0f}₽)\n\n"
                        f"💡 Рекомендую поставить на паузу\n"
                        f"Причина: {s['reason']}"
                    )
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("⏸️ Поставить на паузу", callback_data=f"camp:pause:{ozon_shop_id}:{cid}"),
                        InlineKeyboardButton("🗑️ Удалить", callback_data=f"camp:delete:{ozon_shop_id}:{cid}"),
                        InlineKeyboardButton("❌ Пропустить", callback_data=f"ozbid:{ozon_shop_id}:{cid[:20]}:{d}:{dp}:skip"),
                    ]])
                else:
                    # Корректировка ставок per-SKU
                    arrow = "📉 Снизить" if d == "down" else "📈 Поднять"
                    text = (
                        f"{mp_label} <b>{s['name']}</b>\n"
                        f"ДРР за 7д: <b>{s['drr']:.0f}%</b>  "
                        f"(расход {s['spend_7d']:,.0f}₽ / выручка {s['revenue_7d']:,.0f}₽)\n\n"
                        f"💡 {arrow} ставки на <b>{dp}%</b> по всем SKU\n"
                        f"Причина: {s['reason']}"
                    )
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton(f"✅ {arrow} ставки", callback_data=f"ozbid:{ozon_shop_id}:{cid[:20]}:{d}:{dp}:apply"),
                        InlineKeyboardButton("❌ Пропустить", callback_data=f"ozbid:{ozon_shop_id}:{cid[:20]}:{d}:{dp}:skip"),
                    ]])
            else:
                # WB: корректировка ставок
                arrow = "📉 Снизить" if d == "down" else "📈 Поднять"
                text = (
                    f"{mp_label} <b>{s['name']}</b>\n"
                    f"ДРР за 7д: <b>{s['drr']:.0f}%</b>  "
                    f"(расход {s['spend_7d']:,.0f}₽ / выручка {s['revenue_7d']:,.0f}₽)\n\n"
                    f"💡 {arrow} ставку на <b>{dp}%</b>\n"
                    f"Причина: {s['reason']}"
                )
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"✅ {arrow}", callback_data=f"bid:{mp}:{cid[:20]}:{d}:{dp}:apply"),
                    InlineKeyboardButton("❌ Пропустить", callback_data=f"bid:{mp}:{cid[:20]}:{d}:{dp}:skip"),
                ]])

            await self.app.bot.send_message(
                chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=keyboard
            )
            sent += 1

        return sent

    async def _campaigns_text(self, chat_id: int) -> str:
        """Список рекламных кампаний Ozon с кнопками управления."""
        from db import get_marketplace_shops
        from tools.marketplace import OzonPerformanceClient

        shops = await get_marketplace_shops(chat_id)
        ozon_shops = [s for s in shops if s["marketplace"] == "ozon" and s.get("ozon_client_id") and s.get("ozon_perf_secret")]
        if not ozon_shops:
            return "⚠️ Нет Ozon-магазинов с подключённым Performance API.\nДобавь через /add_shop ozon."

        redis = await self._get_redis()
        lines = ["🎯 <b>Кампании Ozon Performance</b>\n"]
        has_campaigns = False

        for shop in ozon_shops:
            client = OzonPerformanceClient(shop["ozon_client_id"], shop["ozon_perf_secret"], redis)
            campaigns = await client.get_campaigns()
            if not campaigns:
                continue

            shop_name = shop.get("shop_name") or "Ozon"
            lines.append(f"\n🏪 <b>{shop_name}</b>")

            STATE_LABEL = {
                "CAMPAIGN_STATE_RUNNING":  "▶️ Активна",
                "CAMPAIGN_STATE_STOPPED":  "⏸️ Остановлена",
                "CAMPAIGN_STATE_INACTIVE": "⏸️ Неактивна",
                "CAMPAIGN_STATE_FINISHED": "✅ Завершена",
            }
            for c in campaigns[:15]:
                state_label = STATE_LABEL.get(c["state"], c["state"])
                budget_str  = f" · {c['budget']:,.0f}₽/день" if c["budget"] else ""
                lines.append(f"• {state_label} <b>{c['title'][:40]}</b>{budget_str}")
                has_campaigns = True

        if not has_campaigns:
            return "📭 Кампаний нет или Performance API не настроен."

        lines.append("\n<i>Для управления кампанией используй /campaigns в боте Макса.</i>")
        return "\n".join(lines)

    async def _get_campaign_cards(self, chat_id: int) -> list[tuple[str, object]]:
        """Возвращает карточки кампаний: list[(text, InlineKeyboardMarkup|None)].

        Используется как cmd_campaigns, так и Мартой для отправки через свой бот.
        """
        from db import get_marketplace_shops
        from tools.marketplace import OzonPerformanceClient

        shops = await get_marketplace_shops(chat_id)
        ozon_shops = [s for s in shops if s["marketplace"] == "ozon" and s.get("ozon_client_id") and s.get("ozon_perf_secret")]
        if not ozon_shops:
            return [("⚠️ Нет Ozon-магазинов с Performance API.\nДобавь через /add_shop ozon.", None)]

        redis = await self._get_redis()
        STATE_ICON = {
            "CAMPAIGN_STATE_RUNNING":  "▶️",
            "CAMPAIGN_STATE_STOPPED":  "⏸️",
            "CAMPAIGN_STATE_INACTIVE": "⏸️",
            "CAMPAIGN_STATE_FINISHED": "✅",
        }
        cards = []
        for shop in ozon_shops:
            client = OzonPerformanceClient(shop["ozon_client_id"], shop["ozon_perf_secret"], redis)
            campaigns = await client.get_campaigns()
            if not campaigns:
                continue
            shop_id   = shop["id"]
            shop_name = shop.get("shop_name") or "Ozon"
            for c in campaigns[:10]:
                state      = c["state"]
                icon       = STATE_ICON.get(state, "❓")
                is_active  = (state == "CAMPAIGN_STATE_RUNNING")
                budget_str = f"\nБюджет: {c['budget']:,.0f}₽/день" if c["budget"] else ""
                text = (
                    f"🏪 <b>{shop_name}</b>\n"
                    f"{icon} <b>{c['title']}</b>{budget_str}\n"
                    f"<code>{c['id']}</code> · {c['type']}"
                )
                if is_active:
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("⏸️ Поставить на паузу", callback_data=f"camp:pause:{shop_id}:{c['id']}"),
                    ]])
                elif state in ("CAMPAIGN_STATE_STOPPED", "CAMPAIGN_STATE_INACTIVE"):
                    kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("▶️ Запустить", callback_data=f"camp:activate:{shop_id}:{c['id']}"),
                        InlineKeyboardButton("🗑️ Удалить", callback_data=f"camp:delete:{shop_id}:{c['id']}"),
                    ]])
                else:
                    kb = None
                cards.append((text, kb))

        if not cards:
            cards = [("📭 Кампаний нет или Performance API не настроен.", None)]
        return cards

    async def _execute_camp_action(self, shop_id_str: str, action: str, campaign_id: str, chat_id: int) -> tuple[bool, str]:
        """Выполнить действие над кампанией Ozon. Возвращает (ok, result_label)."""
        from db import get_marketplace_shops
        from tools.marketplace import OzonPerformanceClient

        shops = await get_marketplace_shops(chat_id)
        shop  = next((s for s in shops if str(s["id"]) == shop_id_str), None)
        if not shop or not shop.get("ozon_client_id"):
            return False, "❌ Магазин не найден"

        redis = await self._get_redis()
        client = OzonPerformanceClient(shop["ozon_client_id"], shop["ozon_perf_secret"], redis)

        if action == "pause":
            ok = await client.pause_campaign(campaign_id)
            label = "⏸️ Поставлена на паузу" if ok else "❌ Ошибка паузы"
        elif action == "activate":
            ok = await client.activate_campaign(campaign_id)
            label = "▶️ Запущена" if ok else "❌ Ошибка запуска"
        elif action == "delete":
            ok = await client.delete_campaign(campaign_id)
            label = "🗑️ Удалена" if ok else "❌ Ошибка удаления"
        else:
            return False, "❌ Неизвестное действие"

        logger.info(f"[Макс/camp] chat={chat_id} action={action} campaign={campaign_id} ok={ok}")
        return ok, label

    async def cmd_campaigns(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/campaigns — управление рекламными кампаниями Ozon с кнопками."""
        chat_id = update.effective_user.id
        await update.message.reply_text("⏳ Загружаю кампании…")
        cards = await self._get_campaign_cards(chat_id)
        for text, kb in cards:
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    async def _handle_camp_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Обработка кнопок camp:pause/activate/delete."""
        query = update.callback_query
        await query.answer()
        parts = query.data.split(":", 3)
        if len(parts) != 4:
            return
        _, action, shop_id_str, campaign_id = parts

        if action == "delete":
            # Сначала показываем подтверждение
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("⚠️ Да, удалить", callback_data=f"camp:delete_ok:{shop_id_str}:{campaign_id}"),
                InlineKeyboardButton("❌ Отмена", callback_data=f"camp:delete_cancel:{shop_id_str}:{campaign_id}"),
            ]])
            await query.edit_message_text(
                query.message.text + "\n\n⚠️ <b>Удалить кампанию?</b> Это действие необратимо.",
                parse_mode="HTML",
                reply_markup=kb,
            )
            return

        if action == "delete_cancel":
            await query.edit_message_text(query.message.text.split("\n\n⚠️")[0], parse_mode="HTML", reply_markup=None)
            return

        if action == "delete_ok":
            action = "delete"

        _, label = await self._execute_camp_action(shop_id_str, action, campaign_id, query.from_user.id)
        await query.edit_message_text(
            query.message.text.split("\n\n⚠️")[0] + f"\n\n{label}",
            parse_mode="HTML",
            reply_markup=None,
        )

    async def _handle_ozbid_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Обработка ozbid:{shop_id}:{campaign_id}:{direction}:{delta_pct}:{action} — корректировка ставок Ozon per-SKU."""
        query = update.callback_query
        await query.answer()
        # ozbid:{shop_id}:{campaign_id}:{direction}:{delta_pct}:{action}
        parts = query.data.split(":", 5)
        if len(parts) != 6:
            return
        _, shop_id_str, campaign_id, direction, delta_str, cb_action = parts
        chat_id = query.from_user.id

        if cb_action == "skip":
            await query.edit_message_text(query.message.text + "\n\n⏭️ Пропущено", parse_mode="HTML", reply_markup=None)
            return

        try:
            delta_pct = int(delta_str)
        except ValueError:
            delta_pct = 0

        from db import get_marketplace_shops
        from tools.marketplace import OzonPerformanceClient

        shops = await get_marketplace_shops(chat_id)
        shop  = next((s for s in shops if str(s["id"]) == shop_id_str), None)
        if not shop or not shop.get("ozon_client_id"):
            await query.edit_message_text(query.message.text + "\n\n❌ Магазин не найден", parse_mode="HTML", reply_markup=None)
            return

        redis  = await self._get_redis()
        client = OzonPerformanceClient(shop["ozon_client_id"], shop["ozon_perf_secret"], redis)
        bids   = await client.get_campaign_bids(campaign_id)

        if not bids:
            await query.edit_message_text(
                query.message.text + "\n\n⚠️ Ставки не найдены или кампания не поддерживает per-SKU ставки.",
                parse_mode="HTML", reply_markup=None,
            )
            return

        multiplier = (1 - delta_pct / 100) if direction == "down" else (1 + delta_pct / 100)
        new_bids   = [
            {"product_id": b["product_id"], "bid": max(1.0, round(b["bid"] * multiplier, 2))}
            for b in bids
        ]
        ok = await client.update_campaign_bids(campaign_id, new_bids)

        arrow = "📉 Снижены" if direction == "down" else "📈 Повышены"
        if ok:
            result = f"✅ Ставки {arrow} на {delta_pct}% ({len(new_bids)} SKU)"
            logger.info(f"[Макс/ozbid] chat={chat_id} campaign={campaign_id} {direction} {delta_pct}% ok")
        else:
            result = "❌ Не удалось обновить ставки — проверь логи или измени вручную в кабинете."
        await query.edit_message_text(query.message.text + f"\n\n{result}", parse_mode="HTML", reply_markup=None)

    async def _get_unadvertised_products(self, chat_id: int) -> list[dict]:
        """Продукты Ozon с продажами за 30 дней, у которых нет активной рекламы за 7 дней."""
        from db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT
                    pm.ozon_offer_id,
                    pm.ozon_sku,
                    COALESCE(pm.display_name, pm.ozon_offer_id) AS display_name,
                    SUM(o.quantity)::int               AS qty_30d,
                    SUM(o.seller_price * o.quantity)::numeric(12,2) AS revenue_30d
                FROM product_mapping pm
                JOIN marketplace_orders o ON (
                    -- marketplace_orders для Ozon хранит ozon_sku (число), не ozon_offer_id
                    o.product_id = pm.ozon_sku
                    AND o.chat_id = $1
                    AND o.marketplace = 'ozon'
                    AND o.order_date >= NOW() - INTERVAL '30 days'
                )
                WHERE pm.chat_id  = $1
                  AND pm.ozon_sku IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM product_adv_stats adv
                      -- product_adv_stats для Ozon хранит ozon_sku, не ozon_offer_id
                      WHERE adv.product_id = pm.ozon_sku::text
                        AND adv.chat_id    = $1
                        AND adv.stat_date >= NOW() - INTERVAL '7 days'
                        AND adv.marketplace = 'ozon'
                  )
                GROUP BY pm.ozon_offer_id, pm.ozon_sku, pm.display_name
                ORDER BY revenue_30d DESC
                LIMIT 20
            """, chat_id)
        return [dict(r) for r in rows]

    async def cmd_new_campaign(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/new_campaign — предложить создание новой рекламной кампании Ozon из топ-продуктов."""
        import json as _json
        from db import get_marketplace_shops
        from tools.marketplace import OzonPerformanceClient

        chat_id = update.effective_user.id
        shops   = await get_marketplace_shops(chat_id)
        ozon_perf = next(
            (s for s in shops if s["marketplace"] == "ozon" and s.get("ozon_client_id") and s.get("ozon_perf_secret")),
            None,
        )
        if not ozon_perf:
            await update.message.reply_text("⚠️ Нет Ozon-магазина с Performance API.\nДобавь через /add_shop ozon.", parse_mode="HTML")
            return

        await update.message.reply_text("⏳ Анализирую товары без рекламы…")
        products = await self._get_unadvertised_products(chat_id)

        if not products:
            await update.message.reply_text(
                "✅ Все продаваемые товары Ozon уже охвачены рекламой за последние 7 дней.\n\n"
                "<i>Если хочешь создать кампанию вручную, зайди в Ozon Performance кабинет.</i>",
                parse_mode="HTML",
            )
            return

        import config as _cfg
        initial_bid = getattr(_cfg, "OZON_CAMPAIGN_INITIAL_BID", 30)
        default_budget = getattr(_cfg, "OZON_CAMPAIGN_DEFAULT_BUDGET", 500)
        top = products[:10]
        shop_id = str(ozon_perf["id"])

        # Сохраняем план в Redis
        from datetime import date as _date
        title = f"AI — {_date.today().strftime('%d.%m.%Y')}"
        plan = {
            "title": title,
            "budget": default_budget,
            "initial_bid": initial_bid,
            "products": [
                {"ozon_sku": p["ozon_sku"], "offer_id": p["ozon_offer_id"], "name": p["display_name"]}
                for p in top
            ],
        }
        redis = await self._get_redis()
        redis_key = f"campnew:{chat_id}:{shop_id}"
        await redis.set(redis_key, _json.dumps(plan), ex=86400)

        total_rev = sum(float(p["revenue_30d"] or 0) for p in top)
        product_lines = "\n".join(
            f"• <b>{p['display_name'][:40]}</b> — {float(p['revenue_30d'] or 0):,.0f}₽/мес"
            for p in top[:8]
        )
        text = (
            f"🎯 <b>Новая кампания Ozon Search</b>\n\n"
            f"📛 Название: <code>{title}</code>\n"
            f"💰 Бюджет: <b>{default_budget:,.0f}₽/день</b>\n"
            f"🎯 Начальная ставка: <b>{initial_bid}₽</b> per SKU\n\n"
            f"<b>Топ-{len(top)} товаров без рекламы (выручка за 30д):</b>\n"
            f"{product_lines}\n\n"
            f"Суммарная выручка выбранных: <b>{total_rev:,.0f}₽</b>\n\n"
            f"<i>После создания можно скорректировать ставки через /campaigns.</i>"
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Создать кампанию", callback_data=f"campnew:create:{shop_id}"),
            InlineKeyboardButton("❌ Отмена", callback_data=f"campnew:cancel:{shop_id}"),
        ]])
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    async def _handle_campnew_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка campnew:create/cancel:{shop_id}."""
        import json as _json
        query = update.callback_query
        await query.answer()
        parts = query.data.split(":", 2)
        if len(parts) != 3:
            return
        _, action, shop_id_str = parts
        chat_id = query.from_user.id

        if action == "cancel":
            await query.edit_message_text(query.message.text + "\n\n❌ Отменено", parse_mode="HTML", reply_markup=None)
            return

        redis = await self._get_redis()
        redis_key = f"campnew:{chat_id}:{shop_id_str}"
        raw = await redis.get(redis_key)
        if not raw:
            await query.edit_message_text(
                query.message.text + "\n\n⚠️ Сессия истекла. Запусти /new_campaign снова.",
                parse_mode="HTML", reply_markup=None,
            )
            return

        plan = _json.loads(raw)
        await redis.delete(redis_key)

        from db import get_marketplace_shops
        from tools.marketplace import OzonPerformanceClient

        shops = await get_marketplace_shops(chat_id)
        shop  = next((s for s in shops if str(s["id"]) == shop_id_str), None)
        if not shop or not shop.get("ozon_client_id"):
            await query.edit_message_text(query.message.text + "\n\n❌ Магазин не найден", parse_mode="HTML", reply_markup=None)
            return

        perf_redis = await self._get_redis()
        client = OzonPerformanceClient(shop["ozon_client_id"], shop["ozon_perf_secret"], perf_redis)

        await query.edit_message_text(
            query.message.text + "\n\n⏳ Создаю кампанию…",
            parse_mode="HTML", reply_markup=None,
        )

        campaign_id = await client.create_campaign(
            title=plan["title"],
            daily_budget=plan["budget"],
            adv_type="SKU_SEARCH",
        )
        if not campaign_id:
            await query.edit_message_text(
                query.message.text.replace("⏳ Создаю кампанию…", "")
                + "\n\n❌ Не удалось создать кампанию. Проверь логи или создай вручную в кабинете.",
                parse_mode="HTML",
            )
            return

        # Добавляем товары в кампанию через ставки
        initial_bid = float(plan.get("initial_bid") or 30)
        bids = [
            {"product_id": p["ozon_sku"], "bid": initial_bid}
            for p in plan["products"]
            if p.get("ozon_sku")
        ]
        bids_ok = False
        if bids:
            bids_ok = await client.update_campaign_bids(campaign_id, bids)

        result_lines = [
            f"✅ Кампания <b>{plan['title']}</b> создана!",
            f"ID: <code>{campaign_id}</code>",
            f"Бюджет: {plan['budget']:,.0f}₽/день",
        ]
        if bids_ok:
            result_lines.append(f"Добавлено товаров: {len(bids)} с начальной ставкой {initial_bid:.0f}₽")
        elif bids:
            result_lines.append("⚠️ Товары не добавлены автоматически — добавь вручную в кабинете.")
        result_lines.append("\n<i>Статус кампании: /campaigns</i>")

        await query.edit_message_text(
            query.message.text.replace("⏳ Создаю кампанию…", "")
            + "\n\n" + "\n".join(result_lines),
            parse_mode="HTML",
        )
        logger.info(f"[Макс/campnew] chat={chat_id} campaign={campaign_id} bids_ok={bids_ok}")

    async def _bid_adjust_text(self, chat_id: int) -> str:
        suggestions = await self._collect_bid_suggestions(chat_id)
        if not suggestions:
            return (
                "✅ Все ставки выглядят нормально — ДРР в допустимых пределах.\n\n"
                "Убедись что синхронизирована реклама (/sync_adv)."
            )
        lines = ["🎯 <b>Рекомендации по ставкам</b>\n"]
        for s in suggestions[:8]:
            arrow = "📉 Снизить" if s["direction"] == "down" else "📈 Поднять"
            mp_label = "🟣 WB" if s["marketplace"] == "wb" else "🔵 Ozon"
            lines.append(
                f"• {mp_label} <b>{s['name']}</b> — ДРР {s['drr']:.0f}%: {arrow} ставку на {s['delta_pct']}%\n"
                f"  {s['reason']}"
            )
        lines.append("\n<i>Для применения: /bid_adjust (кнопки выйдут через Марту в Phase 2).</i>")
        return "\n".join(lines)

    async def _analyze_promotion_margin(self, chat_id: int, shop, action: dict) -> list[dict]:
        """Расчёт влияния акции на маржу по каждому товару.

        Возвращает список с полями: offer_id, name, price, action_price,
        cost, gross_margin_now, gross_margin_promo, margin_delta_pp, recommend_join.
        """
        from db import get_pool
        from tools.marketplace import OzonClient

        client = OzonClient(shop["api_token"], shop.get("client_id") or "")
        products = await client.get_action_products(action["action_id"])
        if not products:
            return []

        offer_ids = [p["offer_id"] for p in products if p["offer_id"]]

        pool = await get_pool()
        async with pool.acquire() as conn:
            cost_rows = await conn.fetch("""
                SELECT pm.ozon_offer_id, pc.cost
                FROM product_mapping pm
                JOIN product_costs pc ON pc.mapping_id = pm.id AND pc.marketplace = 'ozon'
                WHERE pm.chat_id = $1 AND pm.ozon_offer_id = ANY($2::text[])
            """, chat_id, offer_ids)

        costs = {r["ozon_offer_id"]: float(r["cost"]) for r in cost_rows}

        results = []
        for p in products[:20]:
            oid   = p["offer_id"]
            price = p["price"] or p["action_price"] or 0
            aprx  = p["action_price"] or p["max_action_price"] or 0
            cost  = costs.get(oid, 0)
            if price <= 0 or aprx <= 0:
                continue

            # Комиссия Ozon ≈ 15% (грубая оценка; точная — из финотчёта)
            OZON_FEE = 0.15
            payout_now   = price  * (1 - OZON_FEE)
            payout_promo = aprx   * (1 - OZON_FEE)
            margin_now   = (payout_now   - cost) / payout_now   * 100 if payout_now   > 0 else 0
            margin_promo = (payout_promo - cost) / payout_promo * 100 if payout_promo > 0 else 0
            delta        = round(margin_promo - margin_now, 1)

            results.append({
                "offer_id":          oid,
                "name":              p["name"][:50] or oid,
                "price":             price,
                "action_price":      aprx,
                "cost":              cost,
                "gross_margin_now":  round(margin_now, 1),
                "gross_margin_promo":round(margin_promo, 1),
                "margin_delta_pp":   delta,
                "recommend_join":    delta > -15 and margin_promo > 10,
            })
        return results

    async def cmd_promotions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/promotions — доступные акции Ozon с анализом маржи."""
        from db import get_marketplace_shops
        from tools.marketplace import OzonClient

        chat_id = update.effective_user.id
        shops   = await get_marketplace_shops(chat_id)
        ozon    = next((s for s in shops if s["marketplace"] == "ozon"), None)
        if not ozon:
            await update.message.reply_text("⚠️ Магазин Ozon не подключён.", parse_mode="HTML")
            return

        await update.message.reply_text("⏳ Загружаю акции Ozon…")
        client  = OzonClient(ozon["api_token"], ozon.get("client_id") or "")
        actions = await client.get_available_promotions()

        if not actions:
            await update.message.reply_text("📭 Доступных акций нет.", parse_mode="HTML")
            return

        for a in actions[:6]:
            margin_items = await self._analyze_promotion_margin(chat_id, ozon, a)
            if margin_items:
                avg_delta = sum(i["margin_delta_pp"] for i in margin_items) / len(margin_items)
                join_all  = all(i["recommend_join"] for i in margin_items)
                rec_icon  = "✅" if join_all else ("⚠️" if avg_delta > -20 else "❌")
                rec_text  = "Участвовать выгодно" if join_all else (
                    f"Маржа снизится на {abs(avg_delta):.0f} п.п. — взвесьте объём")

                lines = [
                    f"🎁 <b>{a['title']}</b>",
                    f"Скидка: <b>{a['discount_pct']:.0f}%</b>  ·  {a['start_date']} — {a['end_date']}",
                    "",
                ]
                for it in margin_items[:5]:
                    lines.append(
                        f"• {it['name']}\n"
                        f"  {it['price']:,.0f}₽ → {it['action_price']:,.0f}₽  "
                        f"| Маржа: {it['gross_margin_now']:.0f}% → {it['gross_margin_promo']:.0f}% "
                        f"({it['margin_delta_pp']:+.0f} п.п.)"
                    )
                lines += ["", f"{rec_icon} Рекомендация: {rec_text}"]
                text = "\n".join(lines)
            else:
                text = f"🎁 <b>{a['title']}</b>\nСкидка: {a['discount_pct']:.0f}%  ·  {a['start_date']} — {a['end_date']}\n\n<i>Данных о себестоимости нет — расчёт маржи недоступен</i>"

            shop_id  = ozon["id"]
            act_id   = a["action_id"]
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Войти в акцию", callback_data=f"promo:join:{shop_id}:{act_id}"),
                InlineKeyboardButton("❌ Пропустить",    callback_data=f"promo:skip:{shop_id}:{act_id}"),
            ]])
            await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

    async def _handle_promo_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработка кнопок promo:join/skip."""
        query = update.callback_query
        await query.answer()
        parts = query.data.split(":", 3)
        if len(parts) != 4:
            return
        _, action_str, shop_id_str, act_id = parts
        chat_id = query.from_user.id

        if action_str == "skip":
            await query.edit_message_text(
                query.message.text + "\n\n⏭️ Акция пропущена", parse_mode="HTML", reply_markup=None
            )
            return

        from db import get_marketplace_shops
        from tools.marketplace import OzonClient

        shops = await get_marketplace_shops(chat_id)
        shop  = next((s for s in shops if str(s["id"]) == shop_id_str), None)
        if not shop:
            await query.edit_message_text(query.message.text + "\n\n❌ Магазин не найден", parse_mode="HTML")
            return

        client   = OzonClient(shop["api_token"], shop.get("client_id") or "")
        products = await client.get_action_products(act_id)
        if not products:
            await query.edit_message_text(query.message.text + "\n\n❌ Нет товаров для вступления", parse_mode="HTML")
            return

        items = [{"product_id": int(p["product_id"]), "action_price": p["action_price"]} for p in products if p["product_id"]]
        added, rejected = await client.join_promotion(act_id, items)

        label = f"✅ Вступили в акцию: {added} товаров"
        if rejected:
            label += f", отклонено: {rejected}"
        await query.edit_message_text(
            query.message.text + f"\n\n{label}", parse_mode="HTML", reply_markup=None
        )
        logger.info(f"[Макс/promo] chat={chat_id} action={act_id} added={added} rejected={rejected}")

    async def cmd_bid_adjust(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/bid_adjust — рекомендации по рекламным ставкам на основе ДРР."""
        chat_id = update.effective_user.id
        lock_key = f"bid_lock:{chat_id}"
        if await self._redis_get(lock_key):
            await update.message.reply_text(
                "⏳ Уже есть активные предложения по ставкам. Ответь на них или подожди 10 минут."
            )
            return

        await update.message.reply_text("📊 Анализирую ДРР по кампаниям за 7 дней…")
        sent = await self.auto_bid_suggest(chat_id)
        if not sent:
            await update.message.reply_text(
                "✅ Все ставки выглядят нормально — ДРР в допустимых пределах.\n\n"
                "Убедись что синхронизирована реклама (/sync_adv)."
            )

    async def _handle_bid_callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        await query.answer()
        # Формат: bid:{mp}:{campaign_id}:{direction}:{delta_pct}:{action}
        parts = query.data.split(":", 5)
        if len(parts) != 6:
            return
        _, mp, campaign_id, direction, delta_str, action = parts
        chat_id = query.from_user.id

        if action == "skip":
            await query.edit_message_reply_markup(reply_markup=None)
            await query.edit_message_text(query.message.text + "\n\n⏭️ Пропущено", parse_mode="HTML")
            return

        # Применяем
        if mp != "wb":
            await query.edit_message_text(
                query.message.text + f"\n\n⚠️ Авто-ставки для {mp.upper()} пока не поддерживаются",
                parse_mode="HTML",
            )
            return

        delta_pct = int(delta_str)
        from db import get_marketplace_shops
        from tools.marketplace import WBClient
        shops = await get_marketplace_shops(chat_id)
        wb_shop = next((s for s in shops if s["marketplace"] == "wb"), None)
        if not wb_shop:
            await query.edit_message_text(
                query.message.text + "\n\n❌ Магазин WB не найден", parse_mode="HTML"
            )
            return

        wb = WBClient(wb_shop["api_token"])
        info = await wb.get_campaign_cpm(campaign_id)
        if not info or not info.get("cpm"):
            await query.edit_message_text(
                query.message.text
                + f"\n\n⚠️ Не удалось получить текущую ставку.\n"
                  f"Измени вручную: кампания <code>{campaign_id}</code>, "
                  f"{'снизь' if direction == 'down' else 'подними'} на {delta_pct}%",
                parse_mode="HTML",
            )
            return

        current_cpm = info["cpm"]
        if direction == "down":
            new_cpm = max(50, int(current_cpm * (1 - delta_pct / 100)))
        else:
            new_cpm = int(current_cpm * (1 + delta_pct / 100))

        ok = await wb.update_campaign_cpm(
            campaign_id, info["type"], info["subject_id"], new_cpm
        )
        if ok:
            await query.edit_message_text(
                query.message.text
                + f"\n\n✅ Ставка изменена: {current_cpm} → <b>{new_cpm} ₽</b>",
                parse_mode="HTML",
            )
            logger.info(f"[Макс/bid] chat={chat_id} кампания={campaign_id} {current_cpm}→{new_cpm}")
        else:
            await query.edit_message_text(
                query.message.text
                + f"\n\n⚠️ API вернул ошибку.\n"
                  f"Измени вручную: кампания <code>{campaign_id}</code> → {new_cpm} ₽",
                parse_mode="HTML",
            )

    _MENU_SUBMENUS: dict[str, tuple[str, list]] = {
        "sync": ("🔄 Синхронизация", [
            [InlineKeyboardButton("🔄 Полный синк — данные и остатки", callback_data="menu_cmd:sync")],
            [
                InlineKeyboardButton("📣 Реклама",  callback_data="menu_cmd:sync_adv"),
                InlineKeyboardButton("💰 Финотчёт", callback_data="menu_cmd:sync_fin"),
            ],
            [
                InlineKeyboardButton("🎯 Воронка",  callback_data="menu_cmd:sync_funnel"),
                InlineKeyboardButton("↩️ Возвраты", callback_data="menu_cmd:sync_returns"),
                InlineKeyboardButton("🎁 Акции",    callback_data="menu_cmd:sync_promotions"),
            ],
            [InlineKeyboardButton("◀️ Назад", callback_data="menu_back")],
        ]),
        "analytics": ("📈 Аналитика", [
            [InlineKeyboardButton("⭐ Статистика отзывов",       callback_data="menu_cmd:reviews")],
            [InlineKeyboardButton("🏆 KPI магазина — рейтинг",   callback_data="menu_cmd:shop_kpi")],
            [InlineKeyboardButton("🗄️ Состояние данных в БД",    callback_data="menu_cmd:data_status")],
            [InlineKeyboardButton("◀️ Назад",                    callback_data="menu_back")],
        ]),
        "products": ("📦 Товары", [
            [InlineKeyboardButton("📦 Каталог  (цены · с/с)", callback_data="menu_cmd:products")],
            [InlineKeyboardButton("💲 Маржа и рекомендации",  callback_data="menu_cmd:cost")],
            [
                InlineKeyboardButton("🗺️ Маппинг артикулов", callback_data="menu_cmd:map"),
                InlineKeyboardButton("🔄 Синк SKU",          callback_data="menu_cmd:sync_sku"),
            ],
            [InlineKeyboardButton("◀️ Назад", callback_data="menu_back")],
        ]),
    }

    async def cmd_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/menu — главное меню Макса."""
        await update.message.reply_text(
            "Выбери действие:",
            reply_markup=await self._build_keyboard(update.effective_chat.id),
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/help — справочник всех команд."""
        await update.message.reply_text(_HELP_TEXT, parse_mode="HTML")

    async def _data_status_text(self, chat_id: int) -> str:
        from db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT 'Заказы'        AS label, COUNT(*)          AS cnt,
                       MAX(created_at) AS last_ts
                FROM marketplace_orders WHERE chat_id = $1
                UNION ALL
                SELECT 'Остатки',       COUNT(*), MAX(created_at)
                FROM marketplace_stocks WHERE chat_id = $1
                UNION ALL
                SELECT 'Отзывы',        COUNT(*), MAX(created_at)
                FROM marketplace_reviews WHERE chat_id = $1
                UNION ALL
                SELECT 'Реклама',       COUNT(*), MAX(stat_date)
                FROM marketplace_adv_stats WHERE chat_id = $1
                UNION ALL
                SELECT 'Финотчёт',      COUNT(*), MAX(report_date)
                FROM marketplace_financial_report WHERE chat_id = $1
                UNION ALL
                SELECT 'Воронка',       COUNT(*), MAX(stat_date)
                FROM product_funnel_stats WHERE chat_id = $1
            """, chat_id, chat_id, chat_id, chat_id, chat_id, chat_id)
        now = datetime.now(_UTC)
        lines = ["<b>📊 Состояние данных</b>\n"]
        for r in rows:
            last = r["last_ts"]
            if last is None:
                age = "нет данных"
            else:
                if hasattr(last, "tzinfo") and last.tzinfo is None:
                    last = last.replace(tzinfo=_UTC)
                delta = now - last
                if delta.days > 0:
                    age = f"{delta.days}д назад"
                else:
                    hours = delta.seconds // 3600
                    age = f"{hours}ч назад" if hours else "только что"
            lines.append(f"• {r['label']}: <b>{r['cnt']}</b> записей — {age}")
        return "\n".join(lines)

    async def _send_data_status(self, chat_id: int, msg) -> None:
        """Отправить состояние данных в msg (Message или query.message)."""
        try:
            text = await self._data_status_text(chat_id)
            await msg.reply_text(text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"[Макс/data_status] ошибка: {e}", exc_info=True)
            await msg.reply_text(f"❌ Ошибка получения статуса: {e}")

    async def cmd_data_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/data_status — состояние данных в БД по таблицам."""
        chat_id = update.effective_user.id
        await self._send_data_status(chat_id, update.message)

    async def _handle_menu_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик inline-кнопок меню (menu_cat:*, menu_cmd:*, menu_back, menu_help)."""
        query = update.callback_query
        await query.answer()
        data = query.data
        chat_id = query.from_user.id
        msg = query.message

        if data == "menu_back":
            await query.edit_message_text(
                "👋 Вот твои магазины. Выбери действие:",
                reply_markup=await self._build_keyboard(query.message.chat_id),
            )
            return

        if data == "menu_help":
            await query.edit_message_text(
                _HELP_TEXT,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ В меню", callback_data="menu_back")
                ]]),
            )
            return

        if data.startswith("menu_cat:"):
            cat = data.split(":", 1)[1]
            submenu = self._MENU_SUBMENUS.get(cat)
            if not submenu:
                return
            title, buttons = submenu
            await query.edit_message_text(
                f"<b>{title}</b>\nВыбери действие:",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return

        if data.startswith("menu_c3:"):
            key = data.split(":", 1)[1]
            peter = getattr(self, "_peter_agent", None)
            if key == "drr" and peter is not None:
                await query.answer()
                import asyncio as _asyncio
                _asyncio.create_task(peter.run_drr_for_chat(chat_id, days=30))
                await query.message.reply_text(
                    "💰 ДРР запускается у Питера — результат появится в его чате…"
                )
            elif key == "report" and peter is not None:
                await query.answer()
                await query.message.reply_text(
                    "📊 Перейди к Питеру и запроси /report"
                )
            elif key == "funnel" and peter is not None:
                await query.answer()
                await query.message.reply_text(
                    "🔻 Перейди к Питеру и запроси /funnel"
                )
            else:
                hints = {
                    "report": "📊 Перейди к Питеру и запроси /report",
                    "drr":    "💰 Перейди к Питеру и запроси /drr",
                    "funnel": "🔻 Перейди к Питеру и запроси /funnel",
                }
                await query.answer(hints.get(key, "Следующий шаг"), show_alert=True)
            return

        if data.startswith("menu_cmd:"):
            cmd = data.split(":", 1)[1]

            if cmd == "data_status":
                await self._send_data_status(chat_id, msg)

            elif cmd == "sync":
                await msg.reply_text("⏳ Синхронизирую данные…")
                await self.send_daily_summary(
                    owner_chat_id=chat_id,
                    target_chat_id=msg.chat_id,
                    bot=context.bot,
                )

            elif cmd == "sync_adv":
                await msg.reply_text("⏳ Синхронизирую рекламу…")
                try:
                    await self.sync_ad_stats(chat_id)
                    await msg.reply_text("✅ Синк рекламы завершён.")
                except Exception as e:
                    await msg.reply_text(f"❌ Ошибка: {e}")

            elif cmd == "sync_fin":
                await msg.reply_text("⏳ Загружаю финотчёт…")
                try:
                    await self.sync_financial_report(chat_id, days=90)
                    await msg.reply_text("✅ Финотчёт синхронизирован.")
                except Exception as e:
                    await msg.reply_text(f"❌ Ошибка: {e}")

            elif cmd == "sync_funnel":
                await msg.reply_text("⏳ Синхронизирую воронку…")
                try:
                    counts = await self.sync_funnel(chat_id)
                    await msg.reply_text(
                        f"✅ Воронка синхронизирована\n"
                        f"WB: {counts.get('wb', 0)} записей\n"
                        f"Ozon: {counts.get('ozon', 0)} записей"
                    )
                except Exception as e:
                    await msg.reply_text(f"❌ Ошибка: {e}")

            elif cmd == "shop_kpi":
                await msg.reply_text("⏳ Получаю KPI магазина…")
                _back_kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("◀️ В меню", callback_data="menu_back"),
                ]])
                try:
                    results = await self.sync_shop_kpi(chat_id)
                    if not results:
                        await msg.reply_text("⚠️ Данные KPI недоступны.", reply_markup=_back_kb)
                        return
                    lines = ["<b>Рейтинг продавца</b>"]
                    for mp, kpi in results.items():
                        label = "🟣 WB" if mp == "wb" else "🔵 Ozon"
                        rating = kpi.get("rating") or 0
                        ret    = kpi.get("return_pct") or 0
                        cancel = kpi.get("cancellation_pct") or 0
                        lines.append(f"{label}: ⭐ {rating:.2f} | Возврат: {ret:.1f}% | Отмена: {cancel:.1f}%")
                    await msg.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=_back_kb)
                except Exception as e:
                    await msg.reply_text(f"❌ Ошибка: {e}", reply_markup=_back_kb)

            elif cmd == "sync_returns":
                await msg.reply_text("⏳ Загружаю аналитику возвратов…")
                try:
                    from db import get_marketplace_shops, upsert_returns_analytics
                    from tools.marketplace import WBClient, OzonClient
                    from datetime import date as _dt, timedelta as _td
                    shops = await get_marketplace_shops(chat_id)
                    date_to   = _dt.today().strftime("%Y-%m-%d")
                    date_from = (_dt.today() - _td(days=30)).strftime("%Y-%m-%d")
                    totals: dict = {}
                    for shop in shops:
                        mp = shop["marketplace"]
                        try:
                            if mp == "wb":
                                stats_token = shop.get("statistics_token") or ""
                                if not stats_token:
                                    continue
                                returns = await WBClient(shop["api_token"]).get_returns_analytics(date_from, date_to, stats_token)
                            elif mp == "ozon":
                                returns = await OzonClient(shop["api_token"], shop.get("client_id", "")).get_returns_analytics(date_from, date_to)
                            else:
                                continue
                            for r in returns:
                                sd = r.get("stat_date") or date_to
                                try:
                                    if isinstance(sd, str):
                                        sd = _dt.fromisoformat(sd[:10])
                                except Exception:
                                    pass
                                await upsert_returns_analytics(
                                    chat_id=chat_id, marketplace=mp,
                                    product_id=r["product_id"], product_name=r.get("product_name"),
                                    stat_date=sd, returns_count=r.get("returns_count", 0),
                                    return_amount=r.get("return_amount", 0.0), return_rate=r.get("return_rate"),
                                )
                            totals[mp] = len(returns)
                        except Exception as e:
                            logger.error(f"[Макс/menu sync_returns] {mp}: {e}", exc_info=True)
                    if not totals:
                        await msg.reply_text("⚠️ Данные о возвратах не получены.")
                    else:
                        lines = ["✅ Возвраты синхронизированы"]
                        for mp, cnt in totals.items():
                            lines.append(f"{'🟣 WB' if mp == 'wb' else '🔵 Ozon'}: {cnt} записей")
                        await msg.reply_text("\n".join(lines))
                except Exception as e:
                    await msg.reply_text(f"❌ Ошибка: {e}")

            elif cmd == "sync_promotions":
                await msg.reply_text("⏳ Синхронизирую акции…")
                try:
                    counts = await self.sync_promotions(chat_id)
                    await msg.reply_text(
                        f"✅ Акции синхронизированы\n"
                        f"WB: {counts.get('wb', 0)} акций\n"
                        f"Ozon: {counts.get('ozon', 0)} акций"
                    )
                except Exception as e:
                    await msg.reply_text(f"❌ Ошибка: {e}")

            elif cmd == "cost":
                try:
                    text = await self._margin_check_text(chat_id)
                    await msg.reply_text(
                        text,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("◀️ В меню", callback_data="menu_back"),
                        ]]),
                    )
                except Exception as e:
                    logger.error(f"[Макс/menu_cost] ошибка: {e}", exc_info=True)
                    await msg.reply_text(f"❌ Ошибка: {e}")

            elif cmd == "products":
                try:
                    text = await self._catalog_text(chat_id)
                    await msg.reply_text(
                        text,
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("📊 Маржа и рекомендации", callback_data="menu_cmd:cost")],
                            [InlineKeyboardButton("◀️ В меню", callback_data="menu_back")],
                        ]),
                    )
                except Exception as e:
                    logger.error(f"[Макс/menu_products] ошибка: {e}", exc_info=True)
                    await msg.reply_text(f"❌ Ошибка: {e}")

            elif cmd in ("reviews", "pending", "map", "sync_sku"):
                hints = {
                    "reviews":  "⭐ Используй команду /reviews",
                    "pending":  "🔔 Используй команду /pending",
                    "map":      "🗺️ Используй команду /map name=X wb=Y ozon=Z",
                    "sync_sku": "🔄 Используй команду /sync_sku",
                }
                await msg.reply_text(
                    hints[cmd],
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("◀️ В меню", callback_data="menu_back"),
                    ]]),
                )

    async def cmd_dashboard(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/dashboard — открыть дашборд аналитики."""
        url = f"{config.DASHBOARD_URL}?token={config.DASHBOARD_TOKEN}" if config.DASHBOARD_TOKEN else config.DASHBOARD_URL
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📊 Открыть дашборд", web_app=WebAppInfo(url=url))]])
        await update.message.reply_text("Аналитика продаж WB + Ozon:", reply_markup=keyboard)

    def _bot_commands(self) -> list:
        from telegram import BotCommand
        return [
            BotCommand("start",            "🏠 Главное меню магазина"),
            BotCommand("dashboard",        "📊 Открыть дашборд аналитики"),
            # Отзывы
            BotCommand("pending",          "🔔 Отзывы и вопросы — ждут ответа"),
            BotCommand("reviews",          "⭐ Статистика отзывов за сегодня"),
            # Синхронизация
            BotCommand("sync",             "🔄 Полная синхронизация — заказы, остатки"),
            BotCommand("sync_adv",         "📣 Рекламная статистика"),
            BotCommand("sync_fin",         "💰 Финансовые отчёты — комиссии, выплаты"),
            BotCommand("sync_funnel",      "🎯 Воронка конверсии карточек"),
            BotCommand("sync_cards",       "📝 Контент карточек — заголовок, описание"),
            BotCommand("sync_returns",     "↩️ Аналитика возвратов"),
            BotCommand("sync_promotions",  "🎁 Акции и кампании"),
            # Аналитика
            BotCommand("shop_kpi",         "🏆 KPI магазина — рейтинг, штрафы"),
            BotCommand("data_status",      "🗄️ Состояние данных в БД"),
            # Каталог
            BotCommand("products",         "📦 Каталог товаров и себестоимость"),
            BotCommand("cost",             "💲 Задать себестоимость товара"),
            BotCommand("margin",           "📊 Маржинальность и рекомендованные цены"),
            BotCommand("reprice",          "💡 Предложения по изменению цен"),
            BotCommand("apply_prices",     "✅ Применить рекомендации Питера"),
            BotCommand("map",              "🗺️ Добавить товар в реестр"),
            BotCommand("camp",             "📣 Задать товары WB кампании для ДРР"),
            # SEO и реклама
            BotCommand("seo_check",        "🔍 Алерты по падению SEO-позиций"),
            BotCommand("bid_adjust",       "📣 Корректировка рекламных ставок"),
            # Отзывы/вопросы
            BotCommand("questions",        "💬 Вопросы покупателей без ответа"),
            # Утилиты
            BotCommand("help",             "❓ Справочник команд"),
            BotCommand("cancel",           "✖️ Отменить активный мастер"),
        ]

    def _register_extra_handlers(self) -> None:
        self.app.add_handler(CommandHandler("dashboard",     self.cmd_dashboard))
        self.app.add_handler(CommandHandler("start",         self.cmd_start))
        self.app.add_handler(CommandHandler("menu",          self.cmd_menu))
        self.app.add_handler(CommandHandler("help",          self.cmd_help))
        self.app.add_handler(CommandHandler("data_status",   self.cmd_data_status))
        self.app.add_handler(CommandHandler("add_shop",          self.cmd_add_shop))
        self.app.add_handler(CommandHandler("shops",             self.cmd_shops))
        self.app.add_handler(CommandHandler("set_performance",   self.cmd_set_performance))
        self.app.add_handler(CommandHandler("pending",       self.cmd_pending))
        self.app.add_handler(CommandHandler("reviews",       self.cmd_reviews))
        self.app.add_handler(CommandHandler("reset_checked", self.cmd_reset_checked))
        self.app.add_handler(CommandHandler("reset_orders",  self.cmd_reset_orders))
        self.app.add_handler(CommandHandler("sync",          self.cmd_sync))
        self.app.add_handler(CommandHandler("sync_adv",      self.cmd_sync_adv))
        self.app.add_handler(CommandHandler("sync_fin",      self.cmd_sync_fin))
        self.app.add_handler(CommandHandler("sync_funnel",      self.cmd_sync_funnel))
        self.app.add_handler(CommandHandler("sync_cards",       self.cmd_sync_cards))
        self.app.add_handler(CommandHandler("sync_promotions", self.cmd_sync_promotions))
        self.app.add_handler(CommandHandler("sync_keywords",   self.cmd_sync_keywords))
        self.app.add_handler(CommandHandler("seo_check",       self.cmd_seo_check))
        self.app.add_handler(CommandHandler("sync_returns",    self.cmd_sync_returns))
        self.app.add_handler(CommandHandler("shop_kpi",        self.cmd_shop_kpi))
        self.app.add_handler(CommandHandler("sync_sku",        self.cmd_sync_sku))
        self.app.add_handler(CommandHandler("products",      self.cmd_products))
        self.app.add_handler(CommandHandler("map",           self.cmd_map))
        self.app.add_handler(CommandHandler("camp",          self.cmd_camp))
        self.app.add_handler(CommandHandler("cost",          self.cmd_cost_wizard))
        self.app.add_handler(CommandHandler("margin",        self.cmd_margin_check))
        self.app.add_handler(CommandHandler("reprice",       self.cmd_reprice))
        self.app.add_handler(CommandHandler("apply_prices",  self.cmd_apply_prices))
        self.app.add_handler(CommandHandler("questions",     self.cmd_questions))
        self.app.add_handler(CommandHandler("bid_adjust",    self.cmd_bid_adjust))
        self.app.add_handler(CommandHandler("add",           self.cmd_add))
        self.app.add_handler(CommandHandler("cancel",        self.cmd_cancel))
        self.app.add_handler(
            CallbackQueryHandler(self._handle_menu_callback, pattern=r"^menu_")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_onboard_callback, pattern=r"^onboard:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_review_callback,   pattern=r"^rev:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_question_callback, pattern=r"^qrev:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_catalog_add_callback,  pattern=r"^(addmp|addcat):")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_catalog_cost_callback, pattern=r"^costpick:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_price_apply_callback, pattern=r"^price_apply:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_reprice_callback, pattern=r"^rp:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_bid_callback, pattern=r"^bid:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_camp_callback, pattern=r"^camp:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_ozbid_callback, pattern=r"^ozbid:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_campnew_callback, pattern=r"^campnew:")
        )
        self.app.add_handler(
            CallbackQueryHandler(self._handle_promo_callback, pattern=r"^promo:")
        )
        self.app.add_handler(CommandHandler("campaigns",    self.cmd_campaigns))
        self.app.add_handler(CommandHandler("promotions",   self.cmd_promotions))
        self.app.add_handler(CommandHandler("new_campaign", self.cmd_new_campaign))

        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_edit_reply),
            group=1,
        )
        self.app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_onboard_text),
            group=2,
        )
        # group=3: ИИ-агент в группе (ниже всех личных хендлеров)
        self.app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
                self._handle_group_message,
            ),
            group=3,
        )
