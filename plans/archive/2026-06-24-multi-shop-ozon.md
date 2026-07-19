# Мульти-магазин Ozon: подключение второго аккаунта

Статус: завершён

## Контекст

Пользователь расширяет бизнес и хочет подключить второй магазин Ozon.
Сейчас `marketplace_shops` хранит один магазин на маркетплейс (UNIQUE chat_id+marketplace),
а таблицы данных не разделяют откуда пришла запись — данные двух магазинов смешаются.

## Фазы

### Фаза 1: DB — разрешить два Ozon магазина
- [x] Убрать UNIQUE (chat_id, marketplace) из marketplace_shops
- [x] Добавить функциональный уникальный индекс: UNIQUE (chat_id, marketplace, COALESCE(client_id,'')) — позволяет несколько Ozon (разные client_id) и один WB (client_id пустой)
- [x] Добавить shop_id в 3 критичные таблицы (конфликт при одинаковых SKU):
  - marketplace_stocks (UNIQUE: shop_id + product_id + warehouse_name)
  - marketplace_financial_report (UNIQUE: shop_id + product_id + report_date)
  - marketplace_fin_adv (UNIQUE: shop_id + stat_date)
- [x] Миграция существующих данных: SET shop_id = (SELECT id FROM marketplace_shops ... LIMIT 1)

### Фаза 2: db.py — обновить функции
- [x] add_marketplace_shop(): убрать ON CONFLICT, сделать ручной SELECT → INSERT/UPDATE
- [x] upsert_stock(): добавить параметр shop_id
- [x] upsert_financial_report(): добавить параметр shop_id
- [x] upsert_fin_adv(): добавить параметр shop_id

### Фаза 3: max.py — синхронизация и команды
- [x] cmd_add_shop(): поддержка shop_name (4-й аргумент для Ozon: /add_shop ozon <token> <client_id> [name])
- [x] sync_marketplace_data(): передавать shop["id"] в upsert_stock()
- [x] sync_financial_report(): передавать shop["id"] в upsert_financial_report()
- [x] sync_ad_stats(): передавать shop["id"] в upsert_fin_adv()
- [x] cmd_shops(): показывать все магазины с именами и ID

## Проверка

1. Запустить `/add_shop ozon <token2> <client_id2> Новый магазин`
2. Убедиться `/shops` показывает оба магазина
3. Запустить `/sync` — данные обоих магазинов синхронизируются
4. Проверить что остатки первого магазина не перезаписаны вторым
5. Проверить `/report` — показывает суммарные данные обоих магазинов
