# BizNext Agent

> **Demo Environment · synthetic data.** Все компании, клиенты, сотрудники и цифры в демо-базе вымышлены.

## What is BizNext Agent?

BizNext Agent — AI Data Agent для руководителя предприятия. Директор задаёт вопрос о бизнесе
обычным языком («Почему снизилась прибыль?»), а агент сам решает, какие данные нужны, строит и
проверяет SQL, выполняет его в read-only режиме, анализирует результат и возвращает короткий ответ
с KPI, таблицей и графиком. Каждый ответ можно раскрыть: какие таблицы и SQL использованы и какие
шаги агент реально выполнил.

Это не чат-бот с заготовленными ответами: цифры считает база данных, а LLM интерпретирует
результат.

Демо-предприятие — вымышленный молочный завод **Demo Foods** (Казахстан, тенге, `Asia/Almaty`):
закупки сырья, производственные линии, склады, продажи по регионам, возвраты, списания, расходы и
план продаж за 13+ месяцев.

## Architecture

```text
User (browser)
   ↓
Frontend — React + TypeScript + Tailwind + Recharts (nginx)
   ↓  /api  (JSON + SSE-события шагов агента)
FastAPI backend
   ├── AI Agent ── LLMProvider (Anthropic | OpenAI-compatible)
   │      ↓
   │    Tools: get_database_schema · get_table_metadata · calculate_metric · run_readonly_sql · submit_answer
   │      ↓
   ├── Semantic layer (semantic/*.yaml): словарь данных, бизнес-метрики, дашборд, правила инсайтов
   ├── Safe SQL layer: sqlglot-валидатор → LIMIT → read-only транзакция → timeout
   │      ↓
   └── DataConnector (PostgreSQL)  ──  роль agent_readonly (только SELECT)
   ↓
PostgreSQL 17
   ├── схема demo  — бизнес-данные (synthetic)
   └── схема app   — история диалогов и Agent Trace
```

Подробный контракт компонентов, схемы данных и API — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Installation

Нужен Linux-сервер (или любая машина) с Docker Engine и Docker Compose v2. Облачная инфраструктура
не требуется.

```bash
git clone <repo-url> biznext
cd biznext
python3 scripts/init_env.py      # создаёт .env со случайными паролями БД
nano .env                        # впишите LLM_API_KEY (и при желании LLM_PROVIDER / LLM_MODEL)
docker compose up -d --build
```

Первый запуск: PostgreSQL инициализирует роли и схемы, одноразовый сервис `seed` генерирует
демо-данные (1–3 минуты), затем стартуют backend и web.

Откройте `http://<server>:8095` (порт задаётся `WEB_PORT`).

## Running

```bash
docker compose ps                         # статус: db, backend, web (seed — одноразовый, Exited 0)
docker compose logs -f backend            # логи агента: вопрос, SQL, результат валидации, строки, время
docker compose restart backend            # перезапуск после смены .env
SEED_FORCE=1 docker compose run --rm seed # пересоздать демо-данные (конец периода = сегодня)
docker compose down                       # остановить (данные остаются в томе pgdata)
```

Демо-данные строятся относительно даты генерации. Если стенд долго не пересоздавался, «прошлый месяц»
в вопросах уедет от данных — выполните `SEED_FORCE=1 docker compose run --rm seed`.

### Домашний сервер

Локальные настройки хоста — в `docker-compose.override.yml` (не в git). Пример для сервера без IPv6
и с нестабильным DNS в контейнерах:

```yaml
services:
  db:
    ports: ["127.0.0.1:55432:5432"]      # только localhost, для диагностики
  backend:
    ports: ["127.0.0.1:18000:8000"]      # только localhost, для dev-туннеля
    dns: [1.1.1.1, 8.8.8.8]
    sysctls: [net.ipv6.conf.all.disable_ipv6=1]
```

Все сервисы — `restart: unless-stopped`, стек переживает перезагрузку сервера.

### Тесты

Unit-тесты не требуют БД и LLM:

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -m "not integration and not live_llm"
```

Интеграционные тесты — внутри контейнера против живой БД (права `agent_readonly` в обход валидатора,
все метрики из `semantic/metrics.yaml`, дашборд, хранилище):

```bash
docker compose run --rm --no-deps -u root   -v "$PWD/backend/tests:/app/tests:ro" -v "$PWD/backend/requirements-dev.txt:/app/requirements-dev.txt:ro"   -v "$PWD/backend/pyproject.toml:/app/pyproject.toml:ro"   backend sh -c "pip install -q -r requirements-dev.txt && python -m pytest -m 'not live_llm'"
```

Frontend: `cd frontend && npm ci && npm run lint && npm test && npm run build`.

Проверка сюжетов данных и прав БД напрямую:

```bash
docker compose exec -T db psql -U agent_readonly -d biznext -f - < database/checks/verify_permissions.sql
docker compose exec -T db psql -U agent_readonly -d biznext -f - < database/checks/verify_stories.sql
```

Прогон demo-сценариев через API (реальные вызовы LLM, платно):

```bash
docker compose cp scripts/run_demo_scenarios.py backend:/tmp/ && docker compose exec backend python /tmp/run_demo_scenarios.py http://web /tmp/demo_report.json
```

### Выбор LLM

Агент не привязан к провайдеру — модель меняется переменными окружения:

| Провайдер | `.env` |
|---|---|
| Anthropic (по умолчанию) | `LLM_PROVIDER=anthropic`, `LLM_MODEL=claude-opus-5`, `LLM_API_KEY=...` |
| OpenAI (Responses API) | `LLM_PROVIDER=openai`, `LLM_MODEL=<model>`, `LLM_API_KEY=...` |
| OpenRouter / vLLM / Ollama (локальная модель) | `LLM_PROVIDER=openai_compatible`, `LLM_BASE_URL=http://host:port/v1`, `LLM_MODEL=<model>` |

`LLM_EFFORT` управляет глубиной рассуждений модели (скорость ↔ качество). Дашборд, KPI и источники данных работают и без LLM; без ключа недоступны только ответы агента
(и AI-формулировки инсайтов — вместо них показываются детерминированные).

## Demo

Вопросы для демонстрации:

<!-- DEMO_QUESTIONS: заполняется после ручной проверки сценариев -->

## Architecture decisions

- **Один backend-монолит, а не микросервисы** — проще развернуть у клиента и на домашнем сервере,
  модули разделены интерфейсами (`DataConnector`, `LLMProvider`, `AgentTool`).
- **FastAPI + psycopg 3** — async, типизация через pydantic, SSE для реальных шагов агента.
- **Семантический слой в YAML** — метрики («прибыль», «маржа», «выполнение плана») описаны данными,
  а не кодом агента. Замена демо-базы на базу клиента = новые YAML + коннектор, агент не меняется.
- **Цифры считает SQL/Python** — изменения в процентах, доли и сравнения периодов вычисляются
  детерминированно; LLM получает компактный результат и формулирует вывод.
- **Ручной tool-use loop** — полный контроль над лимитами итераций, ретраями, объёмом данных для
  LLM и записью trace.
- **React + Tailwind + Recharts** — быстрый современный интерфейс без тяжёлого UI-кита.

## Security

- **Read-only на двух уровнях.** SQL проходит валидатор (sqlglot): только один `SELECT`/`WITH`,
  запрет DML/DDL/DCL, опасных функций и чужих схем, принудительный `LIMIT`, таймаут. Даже если
  валидатор обойти, агент подключается ролью `agent_readonly` с правами только `SELECT` на схему
  `demo` и `default_transaction_read_only = on` — INSERT/UPDATE/DELETE/DDL физически запрещены
  PostgreSQL.
- **Разделение данных.** История и Agent Trace хранятся в схеме `app` под отдельным пользователем
  `biznext_app`; у агента к ней доступа нет. Владелец БД используется только при инициализации и
  генерации демо-данных.
- **API-ключи** задаются только через `.env` (не в git), не логируются и не попадают в trace.
- **Контроль стоимости LLM.** Лимиты итераций, вызовов инструментов, SQL-ретраев, строк и символов,
  передаваемых модели, общий таймаут запроса.
- **Честность ответов.** Если данных недостаточно, агент явно сообщает об ограничении и не
  додумывает значения; числа в ответе сверяются с результатами запросов (расхождения видны в trace).
- **Data isolation.** Каждая установка — своя БД на своём сервере; внешние сервисы получают только
  компактные агрегаты, необходимые для ответа.
