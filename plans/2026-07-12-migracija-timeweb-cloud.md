# Переход с Railway на TimeWeb Cloud

Статус: в работе

## Контекст

Разобрали варианты RU-хостинга (Amvera / TimeWeb Cloud / Yandex Cloud), выбрали **TimeWeb Cloud**:
ближе всего по UX к Railway (App Platform + DBaaS), надёжнее Amvera, проще и дешевле Yandex Cloud.
Технической привязки к Railway в коде нет — `Dockerfile` обычный, `config.py` и `dashboard/src/api.ts`
читают всё через env-переменные. Переход = смена деплой-таргета + перенос БД + переменные, без
переписывания кода.

Важный нюанс: процесс — не чистый polling-воркер. `main.py` поднимает ещё aiohttp HTTP-сервер на
`config.PORT` для дашборд-API (`/api/dashboard`, `/api/apply_price`, `/api/apply_bid`, `/api/timeline`),
который читает Vercel-дашборд через `VITE_API_URL`. Значит на TimeWeb нужен сервис с публичным портом,
не воркер без входящего трафика.

Полный план с деталями (SQL-сверка, PowerShell-команды pg_dump/restore, полный список переменных
окружения) — в истории сессии Claude Code от 2026-07-12, тема "переход на TimeWeb Cloud".
Здесь — краткий чек-лист прогресса, чтобы продолжить с того же места.

## Итоговая архитектура на TimeWeb

- **App Platform** — 1 сервис, тот же `Dockerfile`, команда `python main.py`, публичный порт = `config.PORT`
- **DBaaS PostgreSQL** — замена Railway Postgres
- **DBaaS Redis** — замена Railway Redis
- **Fly.io IMAP-прокси** (Ева) — остаётся без изменений
- **Vercel-дашборд** — остаётся, меняется только `VITE_API_URL`

## Фаза 0 — Подготовка [x]
- Выгружены все переменные из Railway → Variables в защищённое место
- Найден `DATABASE_PUBLIC_URL` (включён TCP Proxy на сервисе Postgres в Railway)

## Фаза 1 — Ресурсы на TimeWeb [ ]
- [ ] Аккаунт TimeWeb Cloud + рублёвая карта — **сделано**
- [ ] DBaaS → PostgreSQL (мин. конфигурация 1 CPU/2ГБ/20ГБ NVMe) — **остановились здесь**,
      в меню «Создать» выбрать «База данных» → PostgreSQL
- [ ] DBaaS → Redis (мин. конфигурация)
- [ ] App Platform → новое приложение из `titboan/ai-office`, ветка `main`, Dockerfile-деплой,
      назвать `ai-office-staging`, прод-трафик пока не давать

## Фаза 2 — Перенос данных Postgres [ ]
- [ ] `pg_dump` из Railway (`DATABASE_PUBLIC_URL`) → `pg_restore` в TimeWeb
- [ ] Сверка `count(*)` по 15 ключевым таблицам (tasks, marketplace_orders, marketplace_sales,
      marketplace_stocks, marketplace_adv_stats, marketplace_reviews, product_mapping, product_costs,
      marketplace_financial_report, product_funnel_stats, daily_revenue_snapshot, stock_history_daily,
      digest_channels, wb_campaigns, marketplace_shops)

## Фаза 3 — Переменные окружения на TimeWeb (staging) [ ]
- [ ] Задать все ~35 переменных 1:1 с Railway, кроме новых `DATABASE_URL`/`REDIS_URL`
- [ ] На время staging — тестовый бот-токен (не прод), иначе конфликт `getUpdates` с Railway

## Фаза 4 — Проверка на staging [ ]
- [ ] Чистый старт в логах (Postgres/Redis подключились, боты вышли в polling)
- [ ] Тестовый бот отвечает
- [ ] `curl https://<timeweb-app-url>/api/dashboard?days=7` → валидный JSON

## Фаза 5 — Cutover [ ]
- [ ] Финальный передамп БД (данные могли обновиться)
- [ ] Прод `*_BOT_TOKEN` на TimeWeb
- [ ] Railway → Suspend
- [ ] Vercel: обновить `VITE_API_URL`, редеплой
- [ ] Мониторинг 24ч

## Фаза 6 — Финализация [ ]
- [ ] Railway удалить только через неделю стабильной работы TimeWeb (не сразу)
- [ ] `CLAUDE.md` — заменить упоминания Railway на TimeWeb Cloud
- [ ] `.claude/skills/deploy-railway/SKILL.md` → переписать в `deploy-timeweb/SKILL.md`
- [ ] `.env.example` — обновить комментарии про плагины Railway
- [ ] `railway.toml` — удалить или пометить как архив

## Критерии успеха
- Все 9 агентов отвечают в проде через TimeWeb ≥24ч без ошибок
- `count(*)` по таблицам совпадает Railway ↔ TimeWeb на момент cutover
- Дашборд Vercel отдаёт данные без CORS/404
- Хотя бы один цикл фоновых задач Макса отработал на TimeWeb
- Railway отключён, списания прекратились
