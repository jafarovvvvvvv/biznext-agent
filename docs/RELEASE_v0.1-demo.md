# BizNext Agent Demo v0.1 — зафиксированная версия

Git-тег: **`v0.1-demo`**. Стабильная демо-версия перед работой с реальными клиентами: если эксперименты
что-то сломают, Demo восстанавливается из этого тега и бэкапа.

## Что входит

- Demo Foods (PostgreSQL): AI-агент, дашборд, AI Insights, отчёты, Agent Trace, 19 demo-сценариев.
- Demo Foods · 1С (демо-режим, §56): эмулятор 1С → BizNext Connector → канонические данные → стандартные метрики.
- Отчёт сценариев на момент фиксации: [DEMO_SCENARIOS.md](DEMO_SCENARIOS.md).

## Проверено при фиксации (2026-09-15)

| Проверка | Результат |
|---|---|
| Файлы на сервере совпадают с коммитом | `diff` архива коммита и `~/biznext` — отличий нет |
| Запуск с нуля | отдельный проект Compose, пустые тома, новые пароли: сборка → роли → seed → выгрузка 1С → миграции → полная синхронизация 1С (69 с) за ~3 мин, все сервисы healthy |
| Тесты на свежем стеке | 142 passed (unit + integration: права ролей, сверка метрик 1С с прямым источником, изоляция сети 1С, атомарные загрузки) |
| Живые вопросы на свежем стеке | PostgreSQL: выручка за август, отказ удалять данные, «нет данных» о конкурентах; 1С: «Сколько продали сегодня?», «Какие клиенты перестали покупать?», «нет данных» о простоях — все корректны |
| Рабочий стек | health `ok`; коннектор: 1 полная + инкрементальные синхронизации каждые 30 мин без ошибок |
| Бэкап БД | дамп восстановлен во временный PostgreSQL: число строк demo / onec_demo / app / ingest совпало с рабочей базой, роли восстановлены |

## Где бэкап

Каталог `v0.1-demo-20260915` (права только владельца), две копии:

- сервер: `~/backups/biznext/v0.1-demo-20260915/`
- ПК: `C:\Users\Emil\backups\biznext\v0.1-demo-20260915\`

| Файл | Что это |
|---|---|
| `biznext.pgdump` | вся БД (`demo`, `onec_demo`, `ingest`, `app`), `pg_dump -Fc` |
| `globals.sql` | роли PostgreSQL (с хэшами паролей — секрет) |
| `env.backup` | `.env` рабочего стека (секреты: пароли, токены, LLM-ключ) — не коммитить, не пересылать |
| `docker-compose.override.yml` | настройки домашнего сервера |
| `demo_1c.sqlite` | снимок инфобазы эмулятора 1С |
| `DEMO_SCENARIOS-v0.1-demo.md` | отчёт сценариев |
| `crontab-biznext.txt` | ночная перегенерация demo-данных |
| `biznext-agent.bundle` | весь git-репозиторий (только на ПК), `git clone biznext-agent.bundle` |
| `SHA256SUMS` | контрольные суммы: `sha256sum -c SHA256SUMS` |

## Восстановление

Код и конфигурация:

```bash
git clone https://github.com/jafarovvvvvvv/biznext-agent.git biznext && cd biznext
git checkout v0.1-demo
cp ~/backups/biznext/v0.1-demo-20260915/env.backup .env && chmod 600 .env
cp ~/backups/biznext/v0.1-demo-20260915/docker-compose.override.yml .
```

Вариант А — чистая демо-версия (данные генерируются заново, история диалогов пустая):

```bash
docker compose up -d --build
```

Вариант Б — точное состояние на момент фиксации (с историей диалогов и traces):

```bash
docker compose up -d db
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' \
  < ~/backups/biznext/v0.1-demo-20260915/biznext.pgdump
docker compose up -d --build
```

При восстановлении на новый сервер с пустым томом роли создаются init-скриптами из `.env`; `globals.sql`
нужен только если БД восстанавливается без init-скриптов. После запуска: `docker compose ps` (все healthy),
страница «Источники данных» — 1С «Подключено», последняя синхронизация успешна.
