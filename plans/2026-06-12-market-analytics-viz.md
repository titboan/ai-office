# 2026-06-12-market-analytics-viz.md

Статус: в работе

## Контекст

DataLens (Yandex) подключён к Railway Postgres через TCP-прокси (`maglev.proxy.rlwy.net:12614`). Проблема: много лишних шагов, непонятный UI, нет интеграции в Telegram. Питер генерирует text-only отчёты без графиков.

Цель: красивый и простой визуал с минимальным трением. Делаем параллельно Grafana Cloud (быстрый win) и Telegram Mini App (правильное решение).

**Данные для визуализации:**
- `marketplace_orders` — выручка, заказы по дням/товарам/маркетплейсам
- `product_adv_stats` — CTR, ROAS, spend по товарам (daily, D-1, auto-sync 06:00 МСК)
- `marketplace_adv_stats` — spend по кампаниям
- `marketplace_stocks` — остатки по складам
- `product_mapping` — display_name ("КБ50", "ТГ100")
- `product_costs` — себестоимость (маржа GROSS, без комиссии МП и логистики)

---

## Фаза 1 — Grafana Cloud (нулевой код, ~2 часа)

- [ ] Зарегистрироваться на cloud.grafana.com (free tier, 3 юзера)
- [ ] Добавить Data Source → PostgreSQL, хост `maglev.proxy.rlwy.net:12614`, SSL require
- [ ] Панель: Выручка WB vs Ozon (Time Series, 30 дней)
- [ ] Панель: Топ-10 товаров по выручке (Horizontal Bar, 14 дней)
- [ ] Панель: ДРР по площадкам (Gauge, пороги 20% жёлтый / 30% красный)
- [ ] Панель: CTR по товарам (Bar, <1% красный / >3% зелёный)
- [ ] Панель: ROAS по товарам (Bar, <2 красный / 2-5 жёлтый / >5 зелёный)
- [ ] Панель: Остатки в днях (Table, <7 🔴 / 7-14 🟡 / >14 🟢)
- [ ] Обновить URL в `.claude/skills/max-api/SKILL.md`

---

## Фаза 2 — Backend API endpoint (Python, ~2 часа)

Файл: `main.py` — добавить aiohttp route `/api/dashboard`

- [ ] Добавить `DASHBOARD_URL` в `config.py` (секция CONSTANTS): `os.getenv("DASHBOARD_URL", "")`
- [ ] Написать `_validate_telegram_init_data(init_data, bot_token)` — HMAC-SHA256
- [ ] Написать `handle_dashboard(request)` — извлечь `chat_id` из `initData`, вернуть JSON
- [ ] Реализовать `_get_dashboard_data(chat_id, days)` — SQL из `peter.py:_collect_data()` + `_collect_advanced_data()` адаптированные для JSON-ответа
- [ ] Добавить CORS header для origin Vercel
- [ ] Зарегистрировать route в существующем aiohttp app
- [ ] Добавить переменную `DASHBOARD_URL` в Railway (с явного разрешения Бориса)

---

## Фаза 3 — Telegram Mini App Frontend (React, ~4 часа)

Новая папка `dashboard/` в корне репо

- [ ] `npm create vite@latest dashboard -- --template react-ts`
- [ ] Добавить зависимости: `recharts`, `@telegram-apps/sdk`, `tailwindcss`
- [ ] `src/api.ts` — fetch `/api/dashboard` с `initData` в заголовке
- [ ] `src/App.tsx` — layout, 6 карточек, mobile-first
- [ ] `src/charts/RevenueChart.tsx` — LineChart (выручка по дням, 2 линии WB/Ozon)
- [ ] `src/charts/TopProducts.tsx` — BarChart horizontal (топ-10 товаров)
- [ ] `src/charts/DrrGauge.tsx` — RadialBarChart (ДРР WB и Ozon)
- [ ] `src/charts/CtrRoas.tsx` — BarChart с цветовой шкалой
- [ ] `src/charts/StockTable.tsx` — Table с условным форматированием
- [ ] `vite.config.ts` — base path для Vercel
- [ ] Деплой на Vercel, получить `DASHBOARD_URL`

---

## Фаза 4 — Интеграция WebApp кнопки в Питера (~30 минут)

Файл: `agents/peter.py`

- [ ] Импортировать `InlineKeyboardMarkup`, `InlineKeyboardButton`, `WebAppInfo`
- [ ] В `_handle_report` — кнопка "📊 Дашборд" с `WebAppInfo(url=config.DASHBOARD_URL)`
- [ ] В `_handle_audit` — та же кнопка
- [ ] В `_handle_drr` — та же кнопка

---

## Файлы для изменения

| Файл | Изменение |
|---|---|
| `main.py` | Route `/api/dashboard` + CORS |
| `agents/peter.py` | WebApp кнопка в 3 методах |
| `config.py` | `DASHBOARD_URL` в CONSTANTS |
| `.claude/skills/max-api/SKILL.md` | URL Grafana дашборда |
| `dashboard/` (новая папка) | Vite + React + Recharts проект |

---

## Верификация

- [ ] Grafana: все 6 панелей показывают реальные данные, ссылка доступна
- [ ] `GET /api/dashboard` с валидным Telegram `initData` → 200 JSON
- [ ] `GET /api/dashboard` с невалидным `initData` → 401
- [ ] `/report` → кнопка "📊 Дашборд" → открывается WebApp → 6 графиков рендерятся
- [ ] Тест на реальном телефоне в Telegram — мобильная адаптация корректна
- [ ] `railway up` (с явного разрешения Бориса) — деплой успешен, endpoint доступен
