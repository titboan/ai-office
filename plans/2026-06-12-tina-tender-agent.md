# Тендерный агент Тина (44-ФЗ, Краснодарский край)

Статус: в работе

## Контекст

Автоматизация участия в госзакупках по 44-ФЗ в Краснодарском крае. Вместо ручного мониторинга нужна система, которая сама находит тендеры, ищет оптовые цены, считает маржу и даёт рекомендацию: участвовать / пропустить. Новый агент Тина — 10-й член команды, работает по расписанию (08:00 МСК) и по команде /tenders.

**API**: ГосПлан API v2 (https://v2.gosplan.info) — бесплатно до 01.08.2026, REST, 600 req/min.
**Демпинг**: средний по 44-ФЗ ~28%. При снижении ≥25% — антидемпинговые меры ст. 37.

---

## Фазы

- [x] **Фаза 1**: `tools/gosplan_api.py` — async-клиент ГосПлан API v2
  - GosplanClient: search_tenders, get_tender_detail, get_lot_documents, get_tender_participants
  - format_tender_summary() для Telegram HTML

- [x] **Фаза 2**: `agents/tina.py` — агент Тина
  - Tool-use loop с 5 инструментами: search_tenders, get_tender_details, research_supplier_prices, calculate_economics, save_tender_opportunity
  - _calc_economics(): ожидаемая цена победы, маржа, рекомендация (≥25% УЧАСТВОВАТЬ, ≥15% АНАЛИЗИРОВАТЬ, <15% ПРОПУСТИТЬ)
  - Команды: /tenders [ключевое слово], /tender <lot_id>, /tenders_report
  - send_daily_digest(chat_id) — публичный метод для планировщика
  - handle_task() — интеграция с worker loop

- [x] **Фаза 3**: `db.py` — таблица `tender_opportunities`
  - Поля: tender_id, title, nmck, region, status, submission_deadline, lot_description, supplier_price_estimate, expected_winning_price, margin_estimate, recommendation, analysis_json, chat_id
  - Уникальный индекс по tender_id (UPSERT при повторном анализе)
  - Создаётся автоматически в _create_schema() при старте

- [x] **Фаза 4**: `config.py` — CONSTANTS секция
  - TENDER_REGION_CODE = "23" (Краснодарский край)
  - TENDER_MIN_NMCK = 100_000, TENDER_MAX_NMCK = 5_000_000
  - TENDER_AVG_PRICE_REDUCTION = 0.28
  - TENDER_SCAN_HOUR_UTC = 5 (08:00 МСК)
  - TENDER_KEYWORDS = ["матрасы", "постельное белье", "мебель", "текстиль"]
  - TINA_BOT_TOKEN, GOSPLAN_API_KEY (опционально до 01.08.2026)

- [x] **Фаза 5**: `agents/__init__.py` + `main.py`
  - TinaAgent добавлен в реестр AGENTS["tina"]
  - _tender_digest_loop() — ежедневно 05:00 UTC, рассылает get_all_active_shops() пользователям

- [ ] **Фаза 6**: Деплой и проверка
  - Добавить TINA_BOT_TOKEN в Railway env
  - Проверить /tenders через Telegram
  - Убедиться что tender_opportunities создаётся в БД
  - Подтвердить дайджест в 08:00 МСК

---

## Новые переменные окружения (Railway)

| Переменная | Обязательно | Описание |
|---|---|---|
| TINA_BOT_TOKEN | ✅ | Токен нового Telegram-бота Тины |
| GOSPLAN_API_KEY | ❌ (до 01.08.2026) | API-ключ ГосПлан v2 после августа 2026 |

---

## Изменённые файлы

| Файл | Изменение |
|---|---|
| `tools/gosplan_api.py` | Создан — API клиент |
| `agents/tina.py` | Создан — новый агент |
| `config.py` | +TINA_BOT_TOKEN, +GOSPLAN_API_KEY, +CONSTANTS секция |
| `db.py` | +CREATE TABLE tender_opportunities в _create_schema() |
| `agents/__init__.py` | +TinaAgent |
| `main.py` | +TinaAgent в AGENTS, +_tender_digest_loop() |
