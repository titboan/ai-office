# ai-clone/feedback/

Накопленные правила работы с Борисом. Читать в начале каждой сессии.

## Работа с кодом

- [no-big-rewrites.md](no-big-rewrites.md) — эволюция, не переписывание (>40% файла — спросить)
- [check-infra-before-code.md](check-infra-before-code.md) — проверять инфраструктурные ограничения ДО написания кода
- [verify-external-api-fields-before-merge.md](verify-external-api-fields-before-merge.md) — проверять имена полей внешнего API на живых данных ДО мерджа
- [russia-payment-constraints.md](russia-payment-constraints.md) — в РФ не работают международные платёжные инструменты
- [queue-task-dispatch-prefix.md](queue-task-dispatch-prefix.md) — прокси-команды должны передавать `__keyword__` prefix, а не текст: агенты получают задачи через handle_task → think() без инструментов

## Git и деплой

- [git-add-named.md](git-add-named.md) — git add только поимённо, никогда `git add .`
- [no-deploy-without-boris.md](no-deploy-without-boris.md) — `railway up` только по явной команде Бориса
- [railway-db-access.md](railway-db-access.md) — `railway run psql` для отладки разрешён без подтверждения

## Планирование

- [plan-rules.md](plan-rules.md) — когда создавать план, формат, где хранить
- [plan-completion-summary.md](plan-completion-summary.md) — при закрытии плана сразу давать резюме (проблема / что построили / зачем бизнесу)

## Коммуникация

- [communication-style.md](communication-style.md) — кратко объяснять шаги, резюмировать зачем (не что), давать практические примеры
- [format-prompt-matches-parse-mode.md](format-prompt-matches-parse-mode.md) — формат в промпте обязан совпадать с parse_mode во всех путях отправки

## Общее

- [russian-language.md](russian-language.md) — всегда отвечать на русском
