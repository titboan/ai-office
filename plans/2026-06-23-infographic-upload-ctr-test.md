# Инфографика: загрузка через бота + тест CTR

**Дата:** 2026-06-23  
**Статус:** в работе

## Цель

Замкнуть петлю: Питер обнаружил низкий CTR → алерт с брифом для дизайнера → 
пользователь присылает новую инфографику в бот → Макс загружает на WB/Ozon.  
Через 2 недели Питер показывает CTR до/после.

## Фазы

### Фаза 1 — Питер: расширенный алерт с брифом [ ]
- `run_daily_digest` и `_check_ctr_for_alert` (или enqueue_elina): при CTR < 1%
  отправить отдельный алерт в Telegram с:
  - Артикул, название, маркетплейс
  - Текущий CTR и сколько дней наблюдения
  - Текст преимуществ от Элины (если уже есть из enqueue)
  - Инструкция: "Пришли новую инфографику в ответ на это сообщение"
- Сохранить в Redis: `pending_infographic:{chat_id}` → JSON {article, marketplace, ctr_before, task_message_id}  TTL 7 дней

### Фаза 2 — Марта: приём фото/документа [ ]
- В `handle_message` добавить обработку `message.photo` и `message.document`
- Проверить Redis `pending_infographic:{chat_id}` — если есть:
  - Показать превью и кнопки: [✅ Загрузить для {article} на {marketplace}] [❌ Отмена]
- Если Redis пустой: "Используй /upload_photo артикул" (fallback)
- Callback: подтверждение → enqueue задачу Максу с file_id + article + marketplace

### Фаза 3 — Макс: загрузка фото на маркетплейс [ ]
- `cmd_upload_product_photo(article, file_id, marketplace, chat_id)`
- Скачать файл из Telegram через bot.get_file() → bytes
- WB: `POST https://content-api.wildberries.ru/content/v3/media/save`
  multipart с nmID из product_mapping
- Ozon: скачать → `POST https://api-seller.ozon.ru/v1/product/pictures`  
  (требует URL — загрузить через temporary public URL или imgbb)
- После успеха: записать `infographic_updated_at = NOW()` в product_mapping
- Уведомить пользователя: "✅ Инфографика обновлена. Питер проверит CTR через 14 дней."

### Фаза 4 — Питер: отчёт CTR до/после [ ]
- В `/report` добавить блок "Эффект инфографики":
  SELECT из product_mapping WHERE infographic_updated_at IS NOT NULL
  JOIN с adv stats за 14 дней до и после обновления
  Показать дельту CTR: было X% → стало Y%

## Файлы

- `agents/peter.py` — Фаза 1
- `agents/marta.py` — Фаза 2
- `agents/max.py` — Фаза 3
- `db.py` — добавить колонку `infographic_updated_at` в product_mapping
- `tools/marketplace.py` — WB/Ozon photo upload методы

## Технические заметки

- WB photo upload: `POST /content/v3/media/save` + header `X-Nm-Id` + multipart file
- Ozon: нужен URL, а не прямой бинарный загрузить — можно через Telegram file URL
  (скачать → закодировать base64 или загрузить на imgbb бесплатный tier)
- MVP: сначала WB, Ozon v2
- product_mapping уже есть nmID для WB и product_id для Ozon
