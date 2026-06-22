# Git config и статус "Unverified" на GitHub

## Правило

В самом начале каждой сессии (до первого коммита) выполнять:

```bash
git config user.email noreply@anthropic.com
git config user.name Claude
```

## Про "Unverified" на GitHub

Статус **Unverified** означает отсутствие GPG-подписи — **не** неправильный email.
`reset-author` и смена email это **не исправят**.

В этом контейнере GPG-ключа нет → все коммиты от Claude будут Unverified на GitHub.
Это косметическая проблема, не функциональная. Принять как данность.

Если верификация критична — настроить GPG-ключ на уровне Railway/среды выполнения.

## Когда обнаружено

Сессия 2026-06-22, SEO-пайплайн.
