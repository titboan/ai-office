# 2026-06-12-market-analytics-viz.md

Статус: завершён

## Контекст

DataLens (Yandex) подключён к Railway Postgres через TCP-прокси (`maglev.proxy.rlwy.net:12614`). Проблема: много лишних шагов, непонятный UI, нет интеграции в Telegram. Питер генерирует text-only отчёты без графиков.

Цель: красивые графики прямо в Telegram Mini App. Grafana Cloud — опционально, по необходимости.

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
- [ ] Панели: Выручка, Топ-10, ДРР, CTR, ROAS, Остатки
- [ ] Обновить URL в `.claude/skills/max-api/SKILL.md`

> Пропустили — делаем Mini App напрямую.

---

## Фаза 2 — Backend API endpoint ✅

Файл: `main.py`

- [x] Добавить `DASHBOARD_URL` в `config.py` (секция CONSTANTS)
- [x] Написать `_validate_init_data(init_data, bot_token)` — HMAC-SHA256
- [x] Написать `handle_dashboard(request)` — извлечь `chat_id` из `initData`, вернуть JSON
- [x] Реализовать данные через `peter_agent._collect_data()` + `_collect_advanced_data()` + `revenue_by_day`
- [x] Добавить CORS header для origin Vercel
- [x] Зарегистрировать route в aiohttp + `/health`
- [x] Добавить переменную `DASHBOARD_URL` в Railway (с явного разрешения Бориса)

---

## Фаза 3 — Telegram Mini App Frontend ✅

Папка `dashboard/` в корне репо

- [x] Vite + React + TypeScript + recharts + tailwindcss
- [x] `src/api.ts` — fetch `/api/dashboard` с `initData` в заголовке
- [x] `src/App.tsx` — layout, KPI-карточки, переключатель периода (7/14/30д), mobile-first
- [x] `src/charts/RevenueChart.tsx` — LineChart (выручка по дням, 2 линии WB/Ozon)
- [x] `src/charts/TopProducts.tsx` — BarChart horizontal (топ-10 товаров)
- [x] `src/charts/DrrGauge.tsx` — цветовые карточки (ДРР WB и Ozon)
- [x] `src/charts/CtrRoas.tsx` — BarChart CTR + ROAS с цветовой шкалой
- [x] `src/charts/StockTable.tsx` — Table с условным форматированием 🔴🟡🟢
- [x] `vercel.json` — конфиг деплоя
- [x] Деплой на Vercel, получить `DASHBOARD_URL` — https://dashboard-aioffice.vercel.app

---

## Фаза 4 — Интеграция WebApp кнопки в Питера (~30 минут)

Файл: `agents/peter.py`

- [x] Импортировать `InlineKeyboardMarkup`, `InlineKeyboardButton`, `WebAppInfo`
- [x] В `cmd_report` — кнопка "📊 Дашборд" с `WebAppInfo(url=config.DASHBOARD_URL)`
- [x] В `cmd_audit` — та же кнопка
- [x] В `cmd_drr` — та же кнопка

---

## Файлы изменены

| Файл | Изменение |
|---|---|
| `main.py` | Route `/api/dashboard` + CORS + HMAC-валидация |
| `config.py` | `DASHBOARD_URL` в CONSTANTS |
| `dashboard/` | Vite + React + Recharts проект (новая папка) |
| `agents/peter.py` | WebApp кнопка — **ещё не сделано** |

---

## Следующие шаги

1. `cd dashboard && npm install && npm run build` — убедиться что билд проходит
2. Деплой `dashboard/` на Vercel → получить URL
3. Добавить `DASHBOARD_URL=<vercel-url>` в Railway (с разрешения Бориса)
4. Фаза 4: добавить кнопку "📊 Дашборд" в `peter.py`
5. `railway up` (с явного разрешения Бориса)

---

## Верификация

- [ ] `npm run build` в `dashboard/` — без ошибок
- [ ] `GET /api/dashboard` с валидным Telegram `initData` → 200 JSON
- [ ] `GET /api/dashboard` с невалидным `initData` → 401
- [ ] `/report` → кнопка "📊 Дашборд" → открывается WebApp → 6 графиков рендерятся
- [ ] `GET /health` → 200 ok
- [ ] `railway up` (с явного разрешения Бориса) — деплой успешен, endpoint доступен
