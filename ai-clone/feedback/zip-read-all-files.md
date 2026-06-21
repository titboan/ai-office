# Правило: при работе с ZIP из API читай все файлы, не только первый

## Проблема

В `OzonPerformanceClient.get_ad_stats` была строка:
```python
csv_text_batch = zf.read(zf.namelist()[0])  # только первый файл!
```

Ozon Performance API возвращает ZIP с отдельным CSV на каждую кампанию в батче. При батче из 10 кампаний — 10 файлов в ZIP. Мы читали только первый → захватывали ~10% рекламных расходов. Баг жил несколько недель.

## Правило

Когда API возвращает ZIP-архив — всегда итерироваться по `zf.namelist()`, не брать `[0]`:

```python
# ПРАВИЛЬНО:
for fname in zf.namelist():
    content = zf.read(fname).decode("utf-8-sig", errors="replace")
    # обработать content

# НЕПРАВИЛЬНО:
content = zf.read(zf.namelist()[0])
```

## Когда применять

При работе с любым ZIP из API (Ozon Performance, WB отчёты, другие маркетплейсы). Всегда логировать `len(zf.namelist())` чтобы видеть сколько файлов пришло.
