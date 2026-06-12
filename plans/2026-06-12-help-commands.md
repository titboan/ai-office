# /help и регистрация команд для всех ботов

Статус: в работе

## Контекст

Сейчас `/start` у каждого агента выдаёт только одну строку: «Привет! Я {name} — {role}. Напиши задачу.» Нет `/help`, нет регистрации команд в Telegram (set_my_commands), нет красивого описания возможностей. Пользователь не знает что умеет каждый бот. Задача — добавить красивый `/help` каждому агенту и зарегистрировать команды в Telegram UI (меню «/»).

---

## Что меняем

### 1. base_agent.py — инфраструктура

**Новый метод `_help_text() -> str`:**
```python
def _help_text(self) -> str:
    return (
        f"{self.emoji} <b>{self.name}</b> — {self.role}\n\n"
        "/reset — очистить историю диалога\n\n"
        "Напишите задачу, и я займусь ею."
    )
```

**Обновлённый `cmd_start()`** — показывает help_text вместо голого «напиши задачу»

**Новый `cmd_help()`** — вызывает `_help_text()` и отвечает

**Регистрация `/help` в `build_app()`** рядом с `/start` и `/reset`

**Новый `post_init(app)` callback** — вызывает `set_my_commands()` после инициализации бота:
```python
async def _post_init(app):
    commands = self._bot_commands()
    await app.bot.set_my_commands(commands)
```

**Новый метод `_bot_commands() -> list[BotCommand]`** — возвращает список команд для Telegram меню. Базовая реализация: только `/start` и `/reset`. Каждый агент переопределяет.

---

### 2. Каждый агент переопределяет `_help_text()` и `_bot_commands()`

Формат `/help` для каждого:

```
{emoji} {name} — {role}

{краткое описание что умеет}

📌 Команды:
/cmd1 — описание
/cmd2 [параметр] — описание

💡 Пример:
/cmd "пример запроса"
```

**👩‍💼 Марта (marta.py)**
```
👩‍💼 Марта — координатор команды

Принимаю задачи на русском языке и направляю нужному агенту.
Могу планировать цепочки задач и следить за статусом офиса.

📌 Команды:
/start — главное меню
/status — состояние офиса и активные задачи
/history — последние 10 выполненных задач
/delegate — явно передать задачу агенту
/cancel — отменить задачу из очереди
/reset — очистить историю

💡 Примеры: "напиши пост про наш товар", "исследуй конкурентов"
```

**👨‍💻 Кевин (kevin.py)**
```
👨‍💻 Кевин — разработчик

Пишу код, создаю PR на GitHub, разбираю баги.

📌 Команды:
/code — написать код и создать PR
/reset — очистить историю

💡 Пример: /code "добавь логирование в max.py"
```

**🔍 Каспер (kasper.py)**
```
🔍 Каспер — исследователь

Ищу информацию в интернете, анализирую конкурентов и рынок.

📌 Команды:
/research — глубокое исследование по теме
/reset — очистить историю

💡 Пример: /research "тренды WB в категории одежда 2026"
```

**📊 Питер (peter.py)**
```
📊 Питер — бизнес-аналитик

Анализирую продажи WB и Ozon, считаю ДРР и рентабельность,
даю конкретные рекомендации по росту.

📌 Команды:
/report [цель=X] [период=14] — отчёт о продажах и план роста
/audit — полная оценка магазина (SWOT, KPI, топ-5 действий)
/drr [период=30] — ДРР и ROAS по товарам с вердиктами
/analyze <вопрос> — произвольный бизнес-анализ
/reset — очистить историю

💡 Пример: /report цель=100000 период=14
```

**✍️ Элина (elina.py)**
```
✍️ Элина — копирайтер

Пишу тексты для карточек товаров, посты и рекламные тексты.

📌 Команды:
/write <бриф> — написать текст по заданию
/post <тема> — написать пост для Telegram
/reset — очистить историю

💡 Пример: /write "карточка товара: термокружка 500мл"
```

**🗓️ Алекс (alex.py)**
```
🗓️ Алекс — планировщик

Составляю планы, дорожные карты и отправляю push-уведомления.

📌 Команды:
/plan <задача> — составить план и добавить в Notion
/roadmap <проект> — построить дорожную карту
/testpush — проверить push-уведомления
/reset — очистить историю

💡 Пример: /plan "запустить новую карточку товара к пятнице"
```

**🎨 Дэн (dan.py)**
```
🎨 Дэн — дизайнер

Генерирую изображения и визуалы для карточек товаров и постов.

📌 Как работать:
Опишите что нужно нарисовать — Дэн создаст изображение.

/reset — очистить историю

💡 Пример: "нарисуй баннер: термокружка на фоне леса"
```

**📰 Ева (eva.py)**
```
📰 Ева — редактор дайджеста

Собираю дайджест из Telegram-каналов, которые вы добавите.

📌 Команды:
/digest — сгенерировать дайджест
/add_channel @username — добавить канал
/remove_channel @username — удалить канал
/channels — список подключённых каналов
/reset — очистить историю

💡 Пример: /add_channel @wildberries_sellers
```

**🛒 Макс (max.py)**
```
🛒 Макс — менеджер маркетплейсов

Управляю магазинами WB и Ozon: отзывы, заказы, остатки, реклама.

📌 Команды:
/sync — синхронизировать заказы, остатки, отзывы
/sync_adv — синхронизировать рекламную статистику
/products — список товаров и себестоимость
/cost <артикул> <сумма> — задать себестоимость
/map name=X wb=Y ozon=Z — добавить товар в реестр
/cancel — отменить активный мастер
/reset — очистить историю

💡 Пример: /cost КБ50 850
```

---

## Критические файлы

| Файл | Что меняем |
|------|-----------|
| `agents/base_agent.py` | `_help_text()`, `cmd_help()`, `cmd_start()` update, `_bot_commands()`, post_init callback |
| `agents/marta.py` | override `_help_text()` и `_bot_commands()` |
| `agents/kevin.py` | override `_help_text()` и `_bot_commands()` |
| `agents/kasper.py` | override `_help_text()` и `_bot_commands()` |
| `agents/peter.py` | override `_help_text()` и `_bot_commands()` |
| `agents/elina.py` | override `_help_text()` и `_bot_commands()` |
| `agents/alex.py` | override `_help_text()` и `_bot_commands()` |
| `agents/dan.py` | override `_help_text()` и `_bot_commands()` |
| `agents/eva.py` | override `_help_text()` и `_bot_commands()` |
| `agents/max.py` | override `_help_text()` и `_bot_commands()` |

---

## Реализация в base_agent.py

Найти метод `build_app()` и добавить `post_init` callback для `set_my_commands`.
Найти `cmd_start()` (~строка 392) и обновить.

```python
# В build_app():
async def _post_init(app: Application) -> None:
    await app.bot.set_my_commands(self._bot_commands())
app = Application.builder().token(...).post_init(_post_init).build()

# Новый метод:
def _bot_commands(self) -> list:
    from telegram import BotCommand
    return [
        BotCommand("start", "Запуск и помощь"),
        BotCommand("reset", "Очистить историю диалога"),
    ]

def _help_text(self) -> str:
    return (
        f"{self.emoji} <b>{self.name}</b> — {self.role}\n\n"
        "/start — главное меню\n"
        "/reset — очистить историю\n\n"
        "Напишите задачу, и я займусь ею."
    )

async def cmd_start(self, update, context):
    await update.message.reply_text(self._help_text(), parse_mode="HTML")

async def cmd_help(self, update, context):
    await update.message.reply_text(self._help_text(), parse_mode="HTML")
```

---

## Фазы

- [x] base_agent.py — `_help_text()`, `cmd_help()`, обновить `cmd_start()`, `_bot_commands()`, post_init
- [x] Переопределить в каждом из 9 агентов: marta, kevin, kasper, peter, elina, alex, dan, eva, max
- [ ] Коммит + пуш

---

## Проверка

1. Открыть любого бота → `/start` → красивое меню с командами
2. `/help` → то же что `/start`
3. В интерфейсе Telegram нажать `/` → видеть список команд бота
4. Убедиться что /reset и все специфичные команды в меню
