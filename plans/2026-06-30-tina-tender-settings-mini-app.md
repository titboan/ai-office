# Настройки поиска тендеров через форму в Mini App

Статус: завершён

## Контекст

Параметры поиска тендеров Тины (ключевые слова, бюджет НМЦК, регион) были зашиты в `config.py` (CONSTANTS) — чтобы их поменять, требовалась правка кода и редеплой на Railway. Пользователь 99% времени с телефоном и хочет редактировать список интересующих направлений (категории товаров), бюджет и регион поиска сам — по аналогии с интерфейсом сервиса «ТендерПлан».

Решение: визуальная форма настроек внутри уже существующего React Mini App (`dashboard/`), который открывается из Telegram через `WebAppInfo`. Стек дашборда (React + Vite + Tailwind + Recharts, Vercel) не менялся — форма стала первым «write»-экраном поверх уже работающей инфраструктуры.

Настройки хранятся per-chat_id в уже существующей таблице `user_settings` (db.py:733-740, helpers `get_user_setting`/`set_user_setting`) — по тому же паттерну, что уже использует Питер/Макс для `supply_lead_days`. Новая таблица не понадобилась.

## Фазы

- [x] **Фаза 1**: Бэкенд — `main.py`
  - `_validate_any_bot_init_data()` — проверка initData по очереди через MARTA_BOT_TOKEN и TINA_BOT_TOKEN (Mini App теперь открывается и кнопкой Тины, initData подписан её токеном)
  - `_resolve_dashboard_chat_id()` — общая авторизация (token / initData) для всех dashboard-эндпоинтов
  - `GET /api/tender-settings` — читает настройки чата, fallback на config.py
  - `POST /api/tender-settings` — валидирует и сохраняет через `parse_tender_settings()` из `agents/tina.py`

- [x] **Фаза 2**: `agents/tina.py`
  - `parse_tender_settings()` (module-level) — валидация: 1-10 ключевых слов, min_nmck ≥ 0, max_nmck > min_nmck и ≤ 1 000 000 000, region_code непустой
  - `_get_tender_settings(chat_id)` / `_save_tender_settings(chat_id, settings)` — чтение/запись в `user_settings`
  - `send_daily_digest()`, `cmd_tenders()`, `scan_and_analyze()` — читают настройки из БД вместо `config.TENDER_*`
  - `_tool_search_tenders()` принимает `settings` как fallback для nmck_min/max/region_code
  - Новая команда `/tender_settings` — открывает Mini App кнопкой `WebAppInfo` без `?token=` (чтобы сохранения шли в chat_id реального пользователя)
  - `_bot_commands()` override — добавлена команда в меню бота

- [x] **Фаза 3**: Фронтенд — `dashboard/src/`
  - `api.ts`: `TenderSettings`, `fetchTenderSettings()`, `saveTenderSettings()`
  - `screens/TenderSettings.tsx` — форма (textarea ключевых слов, инпуты бюджета, поле региона), клиентская валидация, состояния idle/saving/success/error
  - `App.tsx`: переключение экрана по `?screen=tender_settings`, кнопка-переход из обычного дашборда

- [x] **Фаза 4**: Проверка
  - `python -m py_compile main.py agents/tina.py` — без ошибок
  - `npm run build` в `dashboard/` — TypeScript + Vite сборка без ошибок

- [x] **Фаза 5**: Главное меню на кнопках (по запросу — не запоминать команды)
  - `/start` присылает меню с тремя кнопками: 🔍 Найти тендеры, 📋 Сохранённые тендеры, ⚙️ Настройки поиска (последняя — сразу открывает Mini App)
  - Первые две кнопки — `CallbackQueryHandler` (`tina_menu:find` / `tina_menu:report`), переиспользуют ту же логику, что и `/tenders` и `/tenders_report`
  - Текстовые команды (`/tenders <слово>`, `/tender <ID>`) остались для продвинутых сценариев

## Файлы

| Файл | Изменение |
|---|---|
| `main.py` | + `_validate_any_bot_init_data()`, + `_resolve_dashboard_chat_id()`, + GET/POST `/api/tender-settings` |
| `agents/tina.py` | + `parse_tender_settings()`, `_get_tender_settings()`, `_save_tender_settings()`, обновлены `send_daily_digest()`, `cmd_tenders()`, `scan_and_analyze()`, `_tool_search_tenders()`, + `/tender_settings` команда, + `_bot_commands()` override |
| `dashboard/src/api.ts` | + `TenderSettings`, `fetchTenderSettings()`, `saveTenderSettings()` |
| `dashboard/src/screens/TenderSettings.tsx` | новый файл — форма настроек |
| `dashboard/src/App.tsx` | + переключение экрана по `?screen=`, кнопка перехода |
| `db.py` | без изменений — переиспользована `user_settings` |

## Известные ограничения / на будущее

- Список регионов не валидируется по справочнику ОКТМО — принимается любая непустая строка
- Кнопка `/tender_settings` без `?token=` означает, что настройки сохраняются только при открытии через initData (внутри Telegram). Доступ коллег по `?token=` к этому экрану не предусмотрен — это сознательное решение, т.к. иначе сохранения уходили бы в `OWNER_CHAT_ID` вместо chat_id реального пользователя
- Деплой на Railway и проверка через реальный Telegram-бот Тины — не выполнялись в рамках этой сессии (нет доступа к рантайму), нужна ручная проверка после `railway up`
