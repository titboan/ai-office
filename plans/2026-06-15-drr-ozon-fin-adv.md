# План: ДРР Ozon из финансового отчёта (ежедневное обновление)

**Статус: в работе**

---

## Контекст

На странице `seller.ozon.ru/app/finances/balance` раздел «Продвижение и реклама» показывает ПОЛНЫЕ расходы:
- Подписка Premium: −9 990 ₽
- Продвижение бренда: −9 282 ₽
- Продвижение с оплатой за заказ: −68 251 ₽
- Оплата за клик: −106 917 ₽
- **Итого: −194 440 ₽**

Дашборд считает ДРР из `marketplace_adv_stats` (Ozon Performance API) — там только «Оплата за клик» (~107к). Остальные три статьи (~87к) не попадают. ДРР занижен.

WB Performance API захватывает все типы кампаний WB — там менять не нужно.

---

## Архитектура решения

Ozon `/v3/finance/transaction/list` с `transaction_type="all"` возвращает ВСЕ операции, включая сервисные списания. Каждая операция имеет массив `services: [{name, price}]`. Маркетинговые списания попадают туда с именами типа `MarketingSellerLeadOrders`, `SellerPremiumSubscription` и т.д.

**Новый поток (только Ozon):**
```
daily 03:00 UTC → OzonClient.get_fin_adv_spend() → marketplace_fin_adv → peter.py adv query
```

WB остаётся без изменений: `marketplace_adv_stats`.

---

## Фазы

### [ ] Фаза 1: Новая таблица в БД

**Файл: `db.py`**

Добавить в `_create_schema()` после таблицы `marketplace_adv_stats`:
```sql
CREATE TABLE IF NOT EXISTS marketplace_fin_adv (
    id          SERIAL PRIMARY KEY,
    chat_id     BIGINT        NOT NULL,
    marketplace VARCHAR(10)   NOT NULL,
    stat_date   DATE          NOT NULL,
    adv_spend   NUMERIC(12,2) DEFAULT 0,
    updated_at  TIMESTAMPTZ   DEFAULT NOW(),
    UNIQUE(chat_id, marketplace, stat_date)
)
```

Добавить функцию-upsert (по аналогии с `upsert_ad_stat`, строки 687–710):
```python
async def upsert_fin_adv(chat_id: int, marketplace: str, stat_date, adv_spend: float) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO marketplace_fin_adv (chat_id, marketplace, stat_date, adv_spend, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (chat_id, marketplace, stat_date) DO UPDATE
                SET adv_spend  = EXCLUDED.adv_spend,
                    updated_at = NOW()
        """, chat_id, marketplace, stat_date, adv_spend)
```

---

### [ ] Фаза 2: Новый метод в OzonClient

**Файл: `tools/marketplace.py`**

Добавить после `get_financial_report` (строка 1423):

```python
# Имена маркетинговых услуг в транзакциях Ozon
_OZON_MARKETING_SERVICES = {
    "MarketingSellerLeadOrders",    # Продвижение с оплатой за заказ
    "MarketingSellerSearch",         # Оплата за клик (вариант 1)
    "SellerClicksMarketing",         # Оплата за клик (вариант 2)
    "SellerPremiumSubscription",     # Подписка Premium (вариант 1)
    "PremiumCashback",               # Подписка Premium (вариант 2)
    "MarketingSellerBrandBanner",    # Продвижение бренда
    "ClientServiceFinancial",        # Прочие маркетинговые списания
}

async def get_fin_adv_spend(self, date_from: str, date_to: str) -> list[dict]:
    """Рекламные расходы Ozon из финансовых транзакций (все типы, включая Premium).
    
    Читает /v3/finance/transaction/list (transaction_type=all), извлекает
    из services[] строки с маркетинговыми именами, группирует по дате.
    Возвращает: [{"date": "YYYY-MM-DD", "adv_spend": float}, ...]
    """
    import json as _json
    url = f"{self._BASE}/v3/finance/transaction/list"
    daily: dict[str, float] = {}
    page = 1

    while True:
        body = {
            "filter": {
                "date": {"from": f"{date_from}T00:00:00.000Z", "to": f"{date_to}T23:59:59.000Z"},
                "transaction_type": "all",
            },
            "page": page,
            "page_size": 1000,
        }
        data = None
        async with aiohttp.ClientSession() as s:
            for attempt in range(3):
                try:
                    async with s.post(url, json=body, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        raw = await resp.text()
                        if resp.status == 429:
                            await asyncio.sleep(60)
                            continue
                        if resp.status != 200:
                            logger.error(f"[Ozon.get_fin_adv_spend] HTTP {resp.status}: {raw[:200]}")
                            break
                        data = _json.loads(raw)
                        break
                except Exception as e:
                    logger.error(f"[Ozon.get_fin_adv_spend] {e}")
                    break
        if data is None:
            break

        operations = (data.get("result") or {}).get("operations") or []
        for op in operations:
            op_date = (op.get("operation_date") or date_from)[:10]
            for svc in (op.get("services") or []):
                name = svc.get("name", "")
                price = abs(float(svc.get("price", 0) or 0))
                if name in _OZON_MARKETING_SERVICES and price > 0:
                    daily[op_date] = daily.get(op_date, 0.0) + price

        page_count = (data.get("result") or {}).get("page_count") or 1
        if page >= page_count or not operations:
            break
        page += 1

    results = [{"date": d, "adv_spend": round(v, 2)} for d, v in sorted(daily.items())]
    logger.info(f"[Ozon.get_fin_adv_spend] {date_from}–{date_to}: {len(results)} дней маркетинговых расходов")
    return results
```

---

### [ ] Фаза 3: Вызов в sync_ad_stats

**Файл: `agents/max.py`**

В методе `sync_ad_stats()` (строки 1066–1136), в блоке `if mp == "ozon":` после уже существующего вызова Performance API, добавить:

```python
# Дополнительно: рекламные расходы из финансовых транзакций (Premium, бренд, оплата за заказ)
try:
    fin_adv = await client.get_fin_adv_spend(
        date_from=(datetime.now(_UTC) - timedelta(days=30)).strftime("%Y-%m-%d"),
        date_to=datetime.now(_UTC).strftime("%Y-%m-%d"),
    )
    for row in fin_adv:
        await upsert_fin_adv(
            chat_id=chat_id, marketplace="ozon",
            stat_date=row["date"], adv_spend=row["adv_spend"],
        )
    logger.info(f"[Макс/adv] Ozon fin adv: {len(fin_adv)} дней")
except Exception as e:
    logger.error(f"[Макс/adv] Ozon fin adv: {e}", exc_info=True)
```

Добавить `upsert_fin_adv` в импорт из `db` в начале файла.

**Период 30 дней** (а не 7 как у adv_stats) — чтобы улавливать отложенные списания Ozon.

---

### [ ] Фаза 4: Изменение запроса ДРР в peter.py

**Файл: `agents/peter.py`**

В методе `_collect_data()`, заменить запрос `adv` (строки 311–319):

**Было:**
```sql
SELECT marketplace, SUM(spend) AS spend, SUM(views) AS views, SUM(clicks) AS clicks
FROM marketplace_adv_stats
WHERE chat_id = $1 AND stat_date >= $2
GROUP BY marketplace
```

**Стало:**
```sql
SELECT
    a.marketplace,
    CASE
        WHEN a.marketplace = 'ozon' AND fa.fin_spend IS NOT NULL
        THEN fa.fin_spend
        ELSE a.perf_spend
    END AS spend,
    a.views,
    a.clicks
FROM (
    SELECT marketplace,
           SUM(spend)::numeric(12,2)  AS perf_spend,
           SUM(views)::bigint         AS views,
           SUM(clicks)::bigint        AS clicks
    FROM marketplace_adv_stats
    WHERE chat_id = $1 AND stat_date >= $2
    GROUP BY marketplace
) a
LEFT JOIN (
    SELECT marketplace, SUM(adv_spend)::numeric(12,2) AS fin_spend
    FROM marketplace_fin_adv
    WHERE chat_id = $1 AND stat_date >= $2
    GROUP BY marketplace
) fa USING (marketplace)
```

Это даёт: для Ozon — реальный расход из финотчёта (если есть); для WB — расход из Performance API. Логика fallback: если `marketplace_fin_adv` пуст для Ozon (до первого синка), вернётся `perf_spend`.

---

## Критические файлы

| Файл | Что меняем |
|---|---|
| `db.py` | Новая таблица + `upsert_fin_adv()` |
| `tools/marketplace.py` | Новый метод `OzonClient.get_fin_adv_spend()` + константа `_OZON_MARKETING_SERVICES` |
| `agents/max.py` | Вызов `get_fin_adv_spend` + `upsert_fin_adv` в `sync_ad_stats()` |
| `agents/peter.py` | Замена SQL-запроса `adv` в `_collect_data()` |

Фронтенд (`App.tsx`, `DrrGauge.tsx`) **не меняется** — данные придут автоматически через `data.adv`.

---

## Расписание обновлений

`sync_ad_stats` уже запускается **ежедневно в 03:00 UTC** (строки 229–265 в `main.py`). Добавление вызова `get_fin_adv_spend` туда же даёт ежедневное обновление ДРР из финотчёта. Отдельный scheduled loop не нужен.

---

## Проверка

1. Запустить `railway run psql` → `SELECT * FROM marketplace_fin_adv LIMIT 10;` — убедиться что таблица создана и данные появляются после первого `sync_adv`
2. Вызвать `/sync_adv` у Макса → проверить лог `[Макс/adv] Ozon fin adv: N дней`
3. Открыть дашборд → ДРР должен совпасть с суммой из `seller.ozon.ru` в разделе «Продвижение и реклама»
4. Убедиться что WB ДРР не изменился (остался из `marketplace_adv_stats`)
