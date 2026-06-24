# Марта как единственная точка входа

Статус: в работе

Задача: пользователь устал переключаться между 8 ботами. Нет единой истории. Нужна единственная точка входа — чат Марты — где работают все команды всех агентов.

---

## Контекст и текущее состояние

Уже работает:
- `_ORIGIN_TOKENS["marta"] = MARTA_BOT_TOKEN` в `base_agent.py` — если задача поставлена Мартой (`from_agent="marta"`), результат идёт через бот Марты
- Марта уже роутит свободный текст к агентам через `enqueue_task`
- Уже есть прокси-команды в Марте: `/report`, `/sync`, `/reviews`, `/research`, `/write`, `/remind`

Чего не хватает:
- Прокси-команды Марты — не все (нет `/order`, `/supply`, `/drr`, `/abc`, `/funnel`, `/audit` и т.д.)
- Когда агент отправляет `InlineKeyboardMarkup`, `CallbackQueryHandler` зарегистрирован в Application агента, а не Марты → кнопки не работают через Марту
- Wizards Max (`/add_shop`, `/cost`, `/reprice`, `/map`) — ConversationHandler в Application Max

---

## Архитектура после рефакторинга

```
Пользователь → Мартин бот (единственный)
    │
    ├── /report, /sync, /reviews, /order, /supply... → enqueue_task(agent_key) → воркер агента
    │       воркер: _notify_user(chat_id, result, bot_token=MARTA_BOT_TOKEN)  ← уже работает
    │       callback кнопок: зарегистрированы на Мартином App                 ← нужно сделать
    │
    ├── свободный текст → Марта роутит → агент → результат через Марту       ← уже работает
    │
    └── Wizards (/add_shop, /cost, /reprice) → ConversationHandler на Марте   ← сложнее всего
```

---

## Фаза 1 — Прокси-команды на Марте [x]

Зарегистрировать на Application Марты все недостающие команды как proxy к агентам.
Принцип: `enqueue_task(agent_key=X, payload=..., chat_id=..., from_agent="marta")`.

### Команды Питера для Марты
- [ ] `/order [период=N]` → peter
- [ ] `/supply [период=N]` → peter
- [ ] `/drr [период=N]` → peter
- [ ] `/abc [период=N]` → peter
- [ ] `/funnel [период=N]` → peter
- [ ] `/returns [период=N]` → peter
- [ ] `/audit` → peter
- [ ] `/seo_audit` → peter
- [ ] `/analyze <вопрос>` → peter

### Команды Макса для Марты
- [ ] `/sync_fin` → max
- [ ] `/sync_adv` → max
- [ ] `/sync_funnel` → max
- [ ] `/sync_returns` → max
- [ ] `/sync_cards` → max
- [ ] `/sync_keywords` → max
- [ ] `/sync_sku` → max
- [ ] `/questions` → max
- [ ] `/pending` → max
- [ ] `/products` → max
- [ ] `/shop_kpi` → max
- [ ] `/data_status` → max
- [ ] `/shops` → max
- [ ] `/seo_check` → max
- [ ] `/reprice` → max (без wizard — просто запустить анализ)
- [ ] `/bid_adjust` → max
- [ ] `/margin <артикул>` → max

### Команды остальных
- [ ] `/code <задача>` → kevin
- [ ] `/plan <идея>` → kevin
- [ ] `/post <тема>` → elina
- [ ] `/seo <артикул>` → elina
- [ ] `/tenders` → tina
- [ ] `/tenders_report` → tina
- [ ] `/testpush` → alex

**Результат Фазы 1:** пользователь может запускать все команды из чата Марты. Результаты приходят туда же. Кнопки пока не работают (они всё ещё шлются через бот агента).

---

## Фаза 2 — Callback-кнопки через Марту [ ]

Каждый агент при отправке кнопок сейчас использует свой бот-токен → `context.bot` = бот агента → callback query идёт в Application агента.

Нужно: кнопки отправлять через Мартин бот, callback обрабатывать в Мартином Application.

### Подход

**Вариант A — shared bot instance (рекомендуется):**
- Хранить `marta_bot: Bot` как синглтон (создаётся при старте Марты)
- Передавать его в `BaseAgent.__init__` как необязательный параметр
- Агенты при отправке кнопок используют `self._shared_bot or self.bot_token`
- Callback handlers агентов регистрируются на App Марты (а не на App агента)

**Вариант B — регистрация на Марте через функцию:**
- Каждый агент предоставляет метод `register_callbacks(marta_app: Application)`
- Марта вызывает его при старте

### Что перенести
- [ ] Max: кнопки `/reviews` → approve/reject отзыв
- [ ] Max: кнопки `/bid_adjust` → apply/skip ставку
- [ ] Max: кнопки `/apply_prices` → apply WB / apply Ozon
- [ ] Max: кнопки `/pending` → approve review
- [ ] Peter: кнопки `/supply` → подтверждение поставки
- [ ] Peter: кнопки меню `/menu` → pmenu:* callbacks

**Результат Фазы 2:** все кнопки работают через чат Марты.

---

## Фаза 3 — ConversationHandlers Макса [ ]

Самая сложная часть. Multi-step wizards со стейтами:

- `/add_shop` — подключить WB/Ozon (7+ шагов, callback_query + text)
- `/cost` — ввод себестоимости товаров (3+ шага)
- `/map` — сопоставление артикулов (2-3 шага)

### Подход
Переместить ConversationHandler из Max.app в Marta.app.
Код обработчиков остаётся в `agents/max.py`, только регистрация переносится.

```python
# В marta.py, в _register_extra_handlers():
from agents.max import MaxAgent
max_agent = self._registry.get("max")
if max_agent:
    max_agent.register_conversation_handlers(self.app)
```

- [ ] Реестр агентов в `main.py` → передавать в Марту
- [ ] `MaxAgent.register_conversation_handlers(app: Application)` — вынести
- [ ] Зарегистрировать на `self.app` Марты

**Результат Фазы 3:** все wizards работают через чат Марты.

---

## Фаза 4 — Убрать отдельные боты [ ]

После Фаз 1-3 отдельные боты не нужны для пользовательского взаимодействия.
Они продолжают работать как worker-процессы, но не принимают команды от пользователя.

- [ ] Убрать `start_polling_async()` для не-Мартиных агентов (оставить только `_start_worker_loop()`)
- [ ] Оставить `Application` только у Марты
- [ ] Убрать из Railway env: `PETER_BOT_TOKEN`, `KEVIN_BOT_TOKEN`, `KASPER_BOT_TOKEN`, `ELINA_BOT_TOKEN`, `ALEX_BOT_TOKEN`, `MAX_BOT_TOKEN`, `TINA_BOT_TOKEN`, `DEN_BOT_TOKEN`, `EVA_BOT_TOKEN`
- [ ] Обновить ROADMAP, `config.py`

**Результат Фазы 4:** один токен (`MARTA_BOT_TOKEN`), один бот, один чат.

---

## Порядок реализации

1. **Сегодня**: Фаза 1 — proxy-команды (безопасно, быстро, сразу даёт пользу)
2. **Следующая сессия**: Фаза 2 — callback-кнопки
3. **Потом**: Фаза 3 — wizards (если пользователь готов тестировать)
4. **После тестирования**: Фаза 4 — убрать лишние токены

## Риски

- Фаза 1: минимальный. Proxy-команды — просто новые CommandHandler на Марте.
- Фаза 2: средний. Callback_data может совпасть у разных агентов → нужен namespace (`max:approve_123`, `peter:supply_ok`).
- Фаза 3: высокий. ConversationHandler — stateful, ошибка → wizard залипает.
- Фаза 4: после тестирования. Не убирать токены пока не проверено.
