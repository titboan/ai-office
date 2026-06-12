---
name: deploy-railway
description: >
  Используй этот skill при задачах связанных с деплоем на Railway,
  диагностикой ошибок в логах, настройкой переменных окружения,
  типичными поломками агентов. Также при вопросах про переменные
  ANTHROPIC_API_KEY, BOT_TOKEN, DATABASE_URL, REDIS_URL и другие.
---

# Деплой и отладка — Railway

## Логи (PowerShell)

```powershell
railway logs --tail 200
railway logs --tail 100 | Select-String -Pattern "max|error|sync"
railway logs --tail 50  | Select-String -Pattern "WB|Ozon|marketplace"
railway logs --tail 100 | Select-String -Pattern "adv|OzonPerf|реклама"
```

⚠️ Логи Railway доступны только для текущего деплоя — диагностику проводить сразу после деплоя.

## Типичные поломки

| Симптом | Причина | Решение |
|---------|---------|---------|
| `Conflict` при деплое | Rolling restart | Норма, само проходит 30-60 сек |
| `rate limit 429` у WB | Statistics API | sleep 60 сек + retry (реализовано) |
| `Chat not found` у Макса | Неверный PARTNERS_GROUP_ID | Проверить в Railway Variables |
| `Message is too long` | > 4096 символов | Разбивается автоматически |
| `PermissionDenied` у Ozon | Отзывы на Premium Lite | Недоступно, норма |
| `WB 404 /adv/v1/promotion/adverts` | Баг WB с окт 2025 | Названия кампаний вручную в wb_campaigns |

## Статус задач (через Telegram)

- `/status` у Марты — активные задачи
- `/history` — выполненные

## Переменные окружения (Railway Variables)
ANTHROPIC_API_KEY
MARTA_BOT_TOKEN / KEVIN_BOT_TOKEN / KASPER_BOT_TOKEN
PETER_BOT_TOKEN / ELINA_BOT_TOKEN / ALEX_BOT_TOKEN
DEN_BOT_TOKEN / EVA_BOT_TOKEN / MAX_BOT_TOKEN
OFFICE_GROUP_ID
PARTNERS_GROUP_ID
DATABASE_URL            -- внутренний Railway
DATABASE_PUBLIC_URL     -- публичный (использовать для подключения извне)
REDIS_URL
TAVILY_API_KEY / GROQ_API_KEY
GITHUB_TOKEN / GITHUB_USERNAME
NOTION_TOKEN
NOTION_PARENT_PAGE_ID / NOTION_PROJECTS_DB / NOTION_TASKS_DB
NOTION_IDEAS_DB / NOTION_RESEARCH_DB / NOTION_CONTENT_DB / NOTION_STATUS_PAGE_ID
NTFY_TOPIC
TELEGRAM_API_ID / TELEGRAM_API_HASH
TELETHON_SESSION        -- StringSession, генерировать через Replit
OZON_PERFORMANCE_CLIENT_ID      -- формат: цифры@advertising.performance.ozon.ru
OZON_PERFORMANCE_CLIENT_SECRET
CLAUDE_MODEL=claude-sonnet-4-6
PORT=8080
