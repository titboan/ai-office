# ai-clone/feedback/

Накопленные правила работы с Борисом. Формируются в ходе сессий — когда он говорит «не так, потому что вот так», а агент оформляет это в файл.

## Формат каждого файла

```
# Заголовок правила

**Rule:** одна строка — что делать / не делать
**Why:** почему это правило появилось
**How to apply:** когда срабатывает, как распознать ситуацию
```

## Текущие правила

- [git-add-named.md](git-add-named.md) — git add только поимённо
- [no-deploy-without-boris.md](no-deploy-without-boris.md) — не деплоить без явной команды
- [no-big-rewrites.md](no-big-rewrites.md) — эволюция, не переписывание
- [plan-before-big-task.md](plan-before-big-task.md) — план перед большой задачей
- [russian-language.md](russian-language.md) — всегда отвечать на русском
- [format-prompt-matches-parse-mode.md](format-prompt-matches-parse-mode.md) — формат промпта = parse_mode отправки
