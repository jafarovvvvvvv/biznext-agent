# BizNext Agent — архитектура и контракты (этап 1)

> Этот документ — **контракт** между частями системы. БД, backend и frontend разрабатываются
> параллельно и обязаны соблюдать имена, форматы и API, описанные здесь. Изменение контракта —
> только правкой этого файла.
>
> Исходные требования: `Промпт для сборки BizNext Agent Demo.md` (разделы 1–47).

---

## 1. Решения верхнего уровня

| Решение | Выбор | Почему |
|---|---|---|
| Среда запуска | Docker Compose на домашнем Linux-сервере | требование §1: без облаков и managed-сервисов |
| Архитектура | один backend (модульный монолит) + статический frontend + PostgreSQL | §46.13–14: без микросервисов |
| БД | PostgreSQL 17 | §3 |
| Backend | Python 3.12, FastAPI, psycopg 3 (async pool), sqlglot | §4; sqlglot — надёжный парсер SQL для валидатора |
| LLM | собственный слой `LLMProvider`; реализации `anthropic` (по умолчанию, `claude-opus-5`) и `openai_compatible` (OpenAI, OpenRouter, Ollama, vLLM…) | §7, §46.3 |
| Агент | один агент + инструменты, ручной tool-use loop с жёсткими лимитами | §7, §27, §33 |
| Frontend | React 19 + TypeScript strict + Vite + Tailwind CSS 4 + Recharts, отдаётся nginx | §5, §11 |
| Семантический слой | YAML-файлы в `semantic/` (словарь данных, метрики, дашборд, правила инсайтов) | §28–29, §46.10–11: demo-специфика — данные, а не код агента |
| Числа | считаются SQL/Python детерминированно; LLM только интерпретирует | §10, §17, §46.9 |
| Read-only | 2 уровня: SQL-валидатор + PostgreSQL-роль `agent_readonly` (только SELECT) | §9, §46.6 |
| Наблюдаемость | Agent Trace в `app.agent_traces` + SSE-события шагов в UI | §13, §47 |

---

## 2. Структура репозитория и владельцы

```text
biznext.kz/
├── docker-compose.yml          # весь стек (владелец: интеграция)
├── .env.example                # все переменные окружения
├── scripts/init_env.py         # создаёт .env со случайными паролями БД
├── docs/ARCHITECTURE.md        # этот контракт
├── database/                   # владелец: DB-поток
│   ├── init/                   # выполняется Postgres при первой инициализации тома
│   │   ├── 00_roles.sh         # роли, схемы, права, read-only настройки
│   │   └── 10_schema.sql       # таблицы схемы demo, индексы, COMMENT ON
│   └── seed/                   # одноразовый контейнер-генератор synthetic data
│       ├── Dockerfile
│       ├── requirements.txt
│       └── generate.py (+ модули)
├── semantic/                   # владелец: DB-поток (данные), читает backend
│   ├── business_context.yaml
│   ├── data_dictionary.yaml
│   ├── metrics.yaml
│   ├── dashboard.yaml
│   ├── insights.yaml
│   └── suggested_questions.yaml
├── backend/                    # владелец: backend-поток
└── frontend/                   # владелец: frontend-поток
```

Правило: поток правит только свои директории. Нужна правка чужой части — фиксируется в отчёте,
вносит владелец или интеграция.

---

## 3. Предметная область (demo)

**Demo Foods** — вымышленный средний молочный завод в Казахстане (synthetic data, реальных
компаний и людей нет). Валюта — тенге (KZT, ₸), суммы **без НДС**. Часовой пояс `Asia/Almaty`.

Бизнес-процессы: закупка сырья (сырое молоко, сахар, закваски, упаковка) у поставщиков →
производство на линиях (смены, план/факт, брак, простои) → склады (завод + распределительные
центры, ежедневные остатки, списания) → продажи клиентам в регионах (сети, дистрибьюторы,
HoReCa, магазины; менеджеры, скидки, возвраты, оплаты) → операционные расходы → план продаж.

### 3.1 Период данных

13 полных месяцев до текущего + текущий месяц по сегодняшний день включительно. Конечная дата =
`SEED_END_DATE` или «сегодня» в `APP_TIMEZONE` на момент генерации. Все даты вычисляются
относительно конечной даты, поэтому «прошлый месяц», «этот квартал» и т.п. всегда есть в данных.
Генерация детерминирована (`SEED_RANDOM_SEED`).

### 3.2 Сюжеты, заложенные в данные (для demo-сценариев)

Генератор обязан заложить эти закономерности (месяцы — относительно конечной даты; M = последний
полный месяц, M-1 — предыдущий):

1. **Прибыль в M снизилась к M-1**, при этом выручка почти не упала. Причины (агент должен суметь
   их найти): закупочная цена сырого молока выросла ~12–15% начиная примерно с середины M-1
   (себестоимость ↑); незапланированный простой «Линия розлива №2» ~4–5 дней в M (выпуск молока
   1 л ↓, часть заказов недогружена); рост списаний йогуртов и кисломолочной продукции на РЦ Шымкент из-за
   нарушения холодовой цепи в M.
2. **Категория «Йогурты и десерты»** растёт устойчиво весь период (+2–3% м/м) — самая быстрорастущая.
3. **«Сыры» в Караганде** падают последние 2–3 месяца: крупный клиент-сеть в регионе перестал
   заказывать (заказы прекратились).
4. **Сезонность**: мороженое и соки — пик летом; кефир и сливочное масло — выше зимой.
5. **Сейчас требует внимания**: по 2–3 SKU (например сливочное масло) остаток ниже точки заказа
   на последнюю дату; есть просроченная дебиторка у нескольких клиентов.
6. Разовая аномалия: всплеск возвратов одного SKU в один из дней в M-2 (бракованная партия,
   связанная с `production_batches.defect_quantity`).
7. План продаж (`sales_plans`) в целом выполняется на 95–105%, в Караганде по сырам — заметно ниже.

Масштаб: ~40 SKU в 7 категориях, 10 регионов, ~150 клиентов, ~1 500–2 000 заказов в месяц,
~150–250 тыс. строк заказов за весь период, выручка порядка 350–500 млн ₸ в месяц.

---

## 4. Модель данных — схема `demo`

Все таблицы в схеме `demo` (имя схемы — `DATA_SCHEMA`). Денежные поля `numeric(16,2)` в KZT без
НДС. У каждой таблицы и колонки — `COMMENT ON` (русский). Первичные ключи — суррогатные id.

| Таблица | Колонки (тип) | Смысл |
|---|---|---|
| `regions` | `region_id` smallint PK, `name` text UNIQUE, `macro_region` text (`Юг`,`Север`,`Центр`,`Запад`,`Восток`) | регионы продаж |
| `warehouses` | `warehouse_id` smallint PK, `name` text, `region_id` FK, `warehouse_type` text (`plant`,`distribution_center`), `capacity_pallets` int | завод и РЦ |
| `product_categories` | `category_id` smallint PK, `name` text UNIQUE, `description` text | категории продукции |
| `products` | `product_id` int PK, `sku` text UNIQUE, `name` text, `category_id` FK, `package_size` numeric(10,3), `package_unit` text (`л`,`кг`), `shelf_life_days` int, `list_price` numeric(12,2), `launched_at` date, `is_active` bool | SKU |
| `raw_materials` | `material_id` int PK, `name` text, `unit` text (`л`,`кг`,`шт`), `material_type` text (`Сырьё`,`Ингредиенты`,`Упаковка`) | сырьё и материалы |
| `suppliers` | `supplier_id` int PK, `name` text, `region_id` FK, `material_type` text, `payment_terms_days` int, `reliability_rating` numeric(3,2) | поставщики |
| `bill_of_materials` | `product_id` FK, `material_id` FK, `quantity_per_unit` numeric(12,4); PK(product_id, material_id) | норма расхода на единицу |
| `purchase_orders` | `purchase_order_id` bigint PK, `po_number` text UNIQUE, `supplier_id` FK, `warehouse_id` FK, `order_date` date, `expected_delivery_date` date, `delivered_date` date NULL, `status` text (`delivered`,`in_transit`,`cancelled`) | заказы поставщикам |
| `purchase_order_items` | `purchase_order_item_id` bigint PK, `purchase_order_id` FK, `material_id` FK, `quantity` numeric(14,3), `unit_price` numeric(12,2), `amount` numeric(16,2) | строки закупок |
| `production_lines` | `line_id` smallint PK, `name` text, `warehouse_id` FK, `category_id` FK, `capacity_units_per_hour` int, `commissioned_at` date | производственные линии |
| `production_batches` | `batch_id` bigint PK, `line_id` FK, `product_id` FK, `production_date` date, `shift` text (`day`,`night`), `planned_quantity` int, `produced_quantity` int, `defect_quantity` int, `labor_hours` numeric(8,2) | партии выпуска |
| `line_downtime` | `downtime_id` bigint PK, `line_id` FK, `started_at` timestamptz, `ended_at` timestamptz, `duration_minutes` int, `downtime_type` text (`planned`,`unplanned`), `reason` text, `comment` text | простои линий |
| `employees` | `employee_id` int PK, `full_name` text, `department` text, `position` text, `region_id` FK NULL, `hire_date` date, `termination_date` date NULL, `monthly_salary` numeric(12,2) | сотрудники (вымышленные) |
| `customers` | `customer_id` int PK, `name` text, `customer_type` text (`Розничная сеть`,`Дистрибьютор`,`HoReCa`,`Независимый магазин`), `region_id` FK, `sales_manager_id` FK employees, `payment_terms_days` int, `credit_limit` numeric(16,2), `created_at` date, `is_active` bool | клиенты |
| `sales_orders` | `order_id` bigint PK, `order_number` text UNIQUE, `customer_id` FK, `warehouse_id` FK, `sales_manager_id` FK, `order_date` date, `shipped_date` date NULL, `status` text (`delivered`,`shipped`,`cancelled`), `payment_due_date` date, `paid_date` date NULL | заказы клиентов |
| `sales_order_items` | `order_item_id` bigint PK, `order_id` FK, `product_id` FK, `quantity` int, `unit_price` numeric(12,2), `discount_pct` numeric(5,2), `revenue_amount` numeric(16,2), `unit_cost` numeric(12,2), `cost_amount` numeric(16,2) | строки продаж; `revenue_amount = quantity*unit_price*(1-discount_pct/100)`, `cost_amount = quantity*unit_cost` (себестоимость на дату продажи) |
| `sales_returns` | `return_id` bigint PK, `order_id` FK, `product_id` FK, `return_date` date, `quantity` int, `reason` text, `refund_amount` numeric(16,2) | возвраты |
| `inventory_snapshots` | `snapshot_date` date, `warehouse_id` FK, `product_id` FK, `quantity_on_hand` int, `reorder_point` int; PK(snapshot_date, warehouse_id, product_id) | ежедневные остатки |
| `write_offs` | `write_off_id` bigint PK, `write_off_date` date, `warehouse_id` FK, `product_id` FK, `quantity` int, `reason` text, `cost_amount` numeric(16,2) | списания |
| `operating_expenses` | `expense_id` bigint PK, `expense_date` date, `expense_category` text (`Логистика`,`Фонд оплаты труда`,`Электроэнергия`,`Маркетинг`,`Аренда`,`Ремонт и обслуживание`,`Прочее`), `department` text, `amount` numeric(16,2), `description` text | операционные расходы |
| `sales_plans` | `plan_month` date (1-е число), `region_id` FK, `category_id` FK, `planned_revenue` numeric(16,2); PK(plan_month, region_id, category_id) | план продаж |

Индексы: все FK; даты (`order_date`, `production_date`, `write_off_date`, `expense_date`,
`return_date`, `snapshot_date`, `order_date` у закупок); составные для частых фильтров
(`sales_orders(order_date, status)`, `sales_order_items(product_id)`).

Бизнес-определения (фиксируются в `semantic/metrics.yaml`):
- **Выручка** = `SUM(revenue_amount)` по заказам со `status <> 'cancelled'`, дата — `order_date`.
- **Себестоимость продаж (COGS)** = `SUM(cost_amount)` там же.
- **Валовая прибыль** = выручка − COGS. **Валовая маржа** = валовая прибыль / выручка.
- **Операционная прибыль** = валовая прибыль − операционные расходы − списания.
  «Прибыль» без уточнения = операционная прибыль (агент сообщает, какую прибыль он считал).
- **Просроченная дебиторка** на дату D: заказы не отменены, `paid_date IS NULL` (или > D),
  `payment_due_date < D`.

---

## 5. Роли и права PostgreSQL (`database/init/00_roles.sh`)

| Роль | Права | Кто использует |
|---|---|---|
| `POSTGRES_USER` (`biznext_owner`) | владелец БД и схемы `demo` | только init-скрипты и `seed` |
| `APP_DB_USER` (`biznext_app`) | владелец схемы `app` (история, traces); **никаких** прав на схему `demo` — бизнес-данные backend читает только через `agent_readonly` | backend: история, traces |
| `AGENT_DB_USER` (`agent_readonly`) | `CONNECT`; `USAGE` на `demo`; `SELECT` на все таблицы `demo` (+ `ALTER DEFAULT PRIVILEGES`); ничего на `app`; `NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT`; `CONNECTION LIMIT 20`; `ALTER ROLE ... SET default_transaction_read_only = on`, `statement_timeout = SQL_STATEMENT_TIMEOUT_MS`, `search_path = demo` | агент, дашборд, метрики, инсайты (всё чтение бизнес-данных) |

Дополнительно: `REVOKE ALL ON DATABASE ... FROM PUBLIC`, `REVOKE CREATE ON SCHEMA public FROM PUBLIC`,
`REVOKE ALL ON SCHEMA public FROM PUBLIC` (приложение схему public не использует).

Seed после загрузки данных повторно выдаёт `GRANT SELECT ON ALL TABLES IN SCHEMA demo TO agent_readonly`
и выполняет `ANALYZE`.

Проверка (обязательный интеграционный тест): под `agent_readonly` INSERT/UPDATE/DELETE/TRUNCATE/
DROP/CREATE/ALTER/GRANT завершаются ошибкой PostgreSQL даже при прямом подключении в обход валидатора;
чтение `app.*` запрещено.

---

## 6. Семантический слой (`semantic/*.yaml`)

Все человекочитаемые строки — объект `{ru: ..., en: ...}` (en можно опустить — тогда берётся ru).
Backend загружает файлы при старте, валидирует pydantic-моделями и падает с понятной ошибкой при
нарушении формата. Агент и сервисы **не содержат** demo-специфики — только этот слой.

### 6.1 `business_context.yaml`

```yaml
version: 1
company:
  name: {ru: "Demo Foods", en: "Demo Foods"}
  industry: {ru: "Молочное производство", en: "Dairy manufacturing"}
  description: {ru: "...", en: "..."}
currency: {code: KZT, symbol: "₸", amounts_note: {ru: "Все суммы в тенге без НДС"}}
timezone: Asia/Almaty
week_starts_on: monday
glossary:                      # термины директора → смысл в данных
  - term: {ru: "прибыль"}
    meaning: {ru: "По умолчанию операционная прибыль (metric operating_profit). Уточнять, какая прибыль посчитана."}
analysis_guidelines:           # доменные подсказки для агента (без цифр)
  - {ru: "Текущий месяц неполный: сравнивай с тем же числом дней прошлого месяца или явно предупреждай."}
```

### 6.2 `data_dictionary.yaml`

```yaml
version: 1
schema: demo
tables:
  sales_orders:
    description: {ru: "Заказы клиентов (шапка)"}
    grain: {ru: "одна строка = один заказ"}
    business_meaning: {ru: "..."}
    columns:
      order_date: {description: {ru: "Дата заказа; используется для периода выручки"}}
      status:
        description: {ru: "Статус заказа"}
        values: {delivered: {ru: "доставлен"}, shipped: {ru: "отгружен"}, cancelled: {ru: "отменён"}}
```
Типы колонок и связи backend берёт интроспекцией БД (`information_schema`/`pg_catalog`);
словарь добавляет смысл. Описание колонки: словарь → `COMMENT ON` в БД → пусто. Таблицы без
описания в словаре всё равно видны агенту.

### 6.3 `metrics.yaml`

```yaml
version: 1
sources:
  sales_items:
    from: "demo.sales_order_items soi JOIN demo.sales_orders so ON so.order_id = soi.order_id"
    date_column: "so.order_date"          # колонка периода (date или timestamptz)
    filters: ["so.status <> 'cancelled'"] # всегда применяются
    dimensions:
      category:
        label: {ru: "Категория", en: "Category"}
        joins:
          - "JOIN demo.products p ON p.product_id = soi.product_id"
          - "JOIN demo.product_categories pc ON pc.category_id = p.category_id"
        column: "pc.name"
      region:
        label: {ru: "Регион"}
        joins:
          - "JOIN demo.customers c ON c.customer_id = so.customer_id"
          - "JOIN demo.regions r ON r.region_id = c.region_id"
        column: "r.name"
metrics:
  revenue:
    label: {ru: "Выручка", en: "Revenue"}
    description: {ru: "Сумма продаж без НДС за вычетом скидок, без отменённых заказов"}
    synonyms: ["выручка", "продажи", "оборот", "revenue", "sales"]
    unit: currency            # currency | number | percent | hours | liters | kg
    better: higher            # higher | lower — для тональности инсайтов
    kind: simple              # simple | ratio | formula | snapshot
    source: sales_items
    expression: "SUM(soi.revenue_amount)"
  gross_margin_pct:
    label: {ru: "Валовая маржа"}
    unit: percent
    better: higher
    kind: ratio               # numerator/denominator * 100, считается в Python
    numerator: gross_profit
    denominator: revenue
  operating_profit:
    label: {ru: "Операционная прибыль"}
    unit: currency
    better: higher
    kind: formula             # только + - * / ( ) и ключи метрик, без dimensions смешанных источников
    formula: "gross_profit - operating_expenses - write_off_cost"
  inventory_below_reorder:
    label: {ru: "SKU ниже точки заказа"}
    unit: number
    better: lower
    kind: snapshot            # значение на конечную дату периода, не сумма за период
    source: inventory_latest  # источник со своим date_column
    expression: "COUNT(*) FILTER (WHERE inv.quantity_on_hand < inv.reorder_point)"
```

Правила сборки SQL (backend `MetricService`), параметры — только bind-параметры:

```sql
SELECT [<dimension.column> AS dimension,] [date_trunc('<granularity>', <date_column>)::date AS period,]
       <expression> AS value
FROM <source.from> <dimension.joins (без дублей)>
WHERE <date_column> >= %(start)s AND <date_column> < %(end)s AND <filters...>
[GROUP BY ...] [ORDER BY ...]
```
- период полуинтервал `[start, end)`; даты — `date`.
- `snapshot`: `WHERE <date_column> = (SELECT MAX(<date_column>) FROM <from> WHERE <date_column> < %(end)s)`.
- `ratio`/`formula` вычисляются в Python из составляющих (по совпадающему ключу dimension/period).
- Для `formula`/`ratio` с `group_by` все составляющие должны поддерживать это измерение, иначе ошибка
  «измерение не поддерживается» (агент получает её как результат инструмента).
- Все запросы метрик выполняются через тот же безопасный SQL-слой под `agent_readonly`.

Минимальный набор метрик: `revenue`, `cogs`, `gross_profit`, `gross_margin_pct`,
`operating_expenses`, `write_off_cost`, `operating_profit`, `operating_margin_pct`, `orders_count`,
`average_order_value`, `units_sold`, `active_customers`, `returns_amount`, `return_rate_pct`,
`production_volume`, `defect_rate_pct`, `production_plan_attainment_pct`, `downtime_hours`,
`purchase_amount`, `avg_raw_milk_price`, `sales_plan`, `sales_plan_attainment_pct`,
`inventory_below_reorder`, `overdue_receivables`.

### 6.4 `dashboard.yaml`

```yaml
version: 1
default_period: last_month
periods: [month_to_date, last_month, quarter_to_date, last_quarter, year_to_date, last_30_days]
kpis:                                   # сравнение всегда с предыдущим сопоставимым периодом
  - {metric: revenue, sparkline: {granularity: month, points: 12}}
  - {metric: operating_profit}
  - {metric: gross_margin_pct}
  - {metric: orders_count}
  - {metric: production_volume}
  - {metric: inventory_below_reorder}
charts:
  - key: revenue_profit_trend
    type: area                          # line | bar | area | pie
    title: {ru: "Выручка и валовая прибыль по месяцам"}
    metrics: [revenue, gross_profit]
    granularity: month
    lookback: 12                        # точек, заканчивая периодом дашборда
  - key: revenue_by_category
    type: bar
    title: {ru: "Выручка по категориям"}
    metrics: [revenue]
    group_by: category
    top: 10                             # за выбранный период
  - key: revenue_by_region
    type: pie
    title: {ru: "Структура выручки по регионам"}
    metrics: [revenue]
    group_by: region
    top: 8
```

### 6.5 `insights.yaml`

```yaml
version: 1
max_insights: 5
rules:
  - {type: metric_change, metric: revenue, min_abs_change_pct: 3}
  - {type: metric_change, metric: operating_profit, min_abs_change_pct: 3}
  - {type: dimension_movers, metric: revenue, dimension: category, top: 2, min_abs_change_pct: 5}
  - type: sql_check                      # SELECT через безопасный слой; строки = факты
    key: low_stock
    severity: warning                    # positive | negative | warning | info
    title: {ru: "Запасы ниже точки заказа"}
    sql: "SELECT ... FROM demo.inventory_snapshots ... LIMIT 10"
    suggested_question: {ru: "Какие товары нужно срочно дозаказать?"}
```

### 6.6 `suggested_questions.yaml`

```yaml
version: 1
questions:
  - {text: {ru: "Что происходит с бизнесом за последний месяц?"}, category: summary}
  - {text: {ru: "Сравни выручку за {last_month} с {month_before_last}"}, category: comparison}
```
Плейсхолдеры подставляет backend из дат: `{this_month}`, `{last_month}`, `{month_before_last}`,
`{this_year}`, `{last_quarter}`. ru — месяц строчными с годом («август 2026»), en — «August 2026»;
quarter — «III кв. 2026» / «Q3 2026».

---

## 7. Backend

### 7.1 Модули

```text
backend/app/
  main.py                 # create_app(), lifespan: пулы БД, загрузка semantic, миграции app-схемы
  config.py               # Settings (pydantic-settings), все env из .env.example
  logging_config.py       # структурные логи, маскирование секретов
  api/                    # роутеры: health, meta, agent, conversations, reports, dashboard,
                          #          insights, data_sources, traces, suggested_questions
  connectors/             # DataConnector (абстракция источника) + postgres.py
  sql/                    # validator.py (sqlglot), executor.py (SafeSqlExecutor)
  semantic/               # loader.py + pydantic-модели YAML
  llm/                    # base.py (LLMProvider, нормализованные типы), anthropic_provider.py,
                          # openai_compatible_provider.py, factory.py
  agent/                  # agent.py (loop+лимиты), prompts.py, date_context.py, events.py,
                          # results.py (реестр результатов), response_builder.py, grounding.py
  tools/                  # base.py, registry.py, get_database_schema.py, get_table_metadata.py,
                          # run_readonly_sql.py, calculate_metric.py, submit_answer.py
  services/               # metrics.py, dashboard.py, insights.py, formatting.py, suggested.py
  storage/                # app_db.py (пул app-пользователя), migrations.sql, conversations.py, traces.py
  schemas/                # pydantic-модели API (контракт §8)
backend/tests/            # unit (без БД и LLM) + integration (маркер) + live_llm (маркер)
```

### 7.2 Интерфейсы (Python, упрощённо)

```python
class DataConnector(Protocol):          # PostgreSQL сейчас; MySQL/MSSQL/1C/REST/Excel позже
    id: str; type: str; dialect: str    # dialect для sqlglot: "postgres"
    async def test_connection(self) -> ConnectionStatus: ...
    async def introspect(self) -> SchemaInfo: ...          # таблицы, колонки, типы, FK, row_count
    async def execute_readonly(self, sql: str, params: dict | None, max_rows: int,
                               timeout_ms: int) -> QueryResult: ...

class LLMProvider(Protocol):
    name: str; model: str
    async def complete(self, *, system: str, messages: list[LLMMessage], tools: list[ToolSpec],
                       max_output_tokens: int) -> LLMResponse: ...
# LLMMessage: user(text) | assistant(text, tool_calls, provider_raw) | tool_results([...])
# provider_raw — нативный контент провайдера (например thinking-блоки Anthropic), который
# провайдер обязан вернуть без изменений в следующем запросе; другой провайдер его игнорирует.
# LLMResponse: text, tool_calls[{id,name,arguments}], stop_reason (end_turn|tool_use|max_tokens|refusal),
#              usage{input_tokens, output_tokens}, provider_raw

class AgentTool(Protocol):
    name: str; description: str; input_schema: dict
    async def run(self, ctx: ToolContext, arguments: dict) -> ToolOutcome: ...
# ToolOutcome: content_for_llm (компактно, в лимитах), is_error, trace_detail, result_ids
```

### 7.3 Anthropic provider

- Официальный SDK `anthropic` (1.x), `AsyncAnthropic(api_key=LLM_API_KEY, timeout, max_retries)`.
- `client.beta.messages.create(model, max_tokens, system=[{type:text, text, cache_control:{type:ephemeral}}],
  tools, messages, thinking={"type":"adaptive"}, output_config={"effort": LLM_EFFORT})`.
- Refusal fallbacks (при `LLM_FALLBACKS=auto` для `claude-opus-5` и `claude-fable-5-1`, либо `on`):
  `betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`. Всегда проверять
  `stop_reason == "refusal"` до чтения контента.
- Ответ ассистента добавляется в историю **полным** `response.content` (thinking-блоки — без изменений).
- Параметры `temperature`/`top_p`/`budget_tokens` не отправлять (400 на текущих моделях).
- Ошибки SDK: `AuthenticationError`/`PermissionDeniedError` → `llm_unavailable` (не ретраить);
  `RateLimitError`, `APIStatusError>=500`, `APIConnectionError`, `APITimeoutError` → уже
  отретраены SDK (`max_retries`), далее `llm_unavailable`.
- Стабильный префикс для кэша: tools → system (без текущей даты); дата и контекст периода —
  в первом user-сообщении хода.

### 7.4 OpenAI providers

`LLM_PROVIDER=openai` — официальный endpoint через **Responses API** (`llm/openai_responses_provider.py`):
reasoning-модели не принимают function tools вместе с reasoning в Chat Completions. История
передаётся без хранения на стороне OpenAI (`store=false`, `include=["reasoning.encrypted_content"]`),
выходные items модели возвращаются в следующий запрос без изменений.

`LLM_PROVIDER=openai_compatible` — Chat Completions (ниже).

#### OpenAI-compatible (Chat Completions)

SDK `openai`, `AsyncOpenAI(api_key, base_url=LLM_BASE_URL or None)`, Chat Completions с `tools`
(function calling). Маппинг нормализованных сообщений ↔ `assistant.tool_calls` / `role: tool`.
Годится для OpenAI, OpenRouter, Ollama, vLLM, LM Studio. `LLM_PROVIDER=openai` — тот же провайдер
с официальным endpoint OpenAI. Для reasoning-моделей (GPT-5): без `temperature`, лимит —
`max_completion_tokens`, `reasoning_effort` из `LLM_EFFORT` (с одним повтором без параметра, если
модель его не принимает).

### 7.5 Безопасный SQL-слой

`SqlValidator.validate(sql) -> ValidationResult(ok, reason_code, message, normalized_sql, tables)`:
1. Пустой/слишком длинный запрос (> 20 000 символов) → reject.
2. Парсинг `sqlglot` (dialect postgres); ошибка парсинга → reject (fail closed).
3. Ровно **одно** выражение (`;` в конце допустим, второе выражение → `multiple_statements`).
4. Корень — `SELECT` / `UNION|INTERSECT|EXCEPT` / `WITH ... SELECT`. Всё иное → `not_select`.
5. В дереве нет узлов модификации/DDL/DCL/управления: Insert, Update, Delete, Merge, Create, Drop,
   Alter, TruncateTable, Grant, Revoke, Command, Copy, Set, Transaction, Commit, Rollback, Lock,
   `SELECT ... INTO`, блокировок `FOR UPDATE/SHARE` → `forbidden_operation`.
6. Запрещённые функции (case-insensitive): `pg_sleep*`, `pg_read_*`, `pg_ls_dir`, `pg_stat_file`,
   `lo_*`, `dblink*`, `pg_terminate_backend`, `pg_cancel_backend`, `set_config`, `nextval`, `setval`,
   `pg_advisory*`, `txid_*`, `pg_notify` → `forbidden_function` (безобидные вроде `current_setting`,
   `now`, `date_trunc` разрешены).
7. Таблицы только из разрешённой схемы (`DATA_SCHEMA`) или без схемы (search_path) и CTE-имена;
   `pg_catalog`, `information_schema`, `app` и прочие → `forbidden_schema`.
8. `LIMIT`: нет лимита → добавить `LIMIT SQL_MAX_ROWS + 1`; лимит больше → заменить. Truncation
   определяется по лишней строке.

`SafeSqlExecutor.execute(sql, purpose)`: validate → лог (sql, purpose, результат валидации) →
`connector.execute_readonly` в транзакции `BEGIN READ ONLY` + `SET LOCAL statement_timeout` →
`QueryResult(columns[{name,type}], rows, row_count, truncated, execution_ms)` → лог (row_count, ms).
Ошибки БД возвращаются как `SqlExecutionError(code, message)` без стектрейса и секретов.
Валидатор — первый уровень; второй — права `agent_readonly` (§5), не зависящие от кода.

### 7.6 Агент

Инструменты (имена фиксированы — используются в trace и UI):

| Tool | Вход | Выход для LLM |
|---|---|---|
| `get_database_schema` | `{}` | все таблицы: описание, grain, колонки (имя, тип, описание, допустимые значения), связи FK, примерный row_count; список метрик (key, label, unit, description, dimensions) |
| `get_table_metadata` | `{table}` | колонки подробно, FK, row_count, 3 строки-примера, distinct-значения текстовых колонок с низкой кардинальностью (≤ 20) |
| `run_readonly_sql` | `{sql, purpose}` | `result_id`, колонки, строки (≤ `AGENT_MAX_ROWS_TO_LLM`, ≤ `AGENT_MAX_RESULT_CHARS_TO_LLM` символов), `row_count`, `truncated_for_llm`, `truncated_by_limit`, `execution_ms`; при ошибке — `is_error` с причиной валидатора/БД |
| `calculate_metric` | `{metric, start, end, compare_start?, compare_end?, group_by?, granularity?(day/week/month), top?}` | `result_id`, строки `{dimension?, period?, value, compare_value?, change_abs?, change_pct?}` — изменения считает Python; SQL сохраняется в trace |
| `submit_answer` | финальный структурированный ответ (ниже) | — завершает цикл |

`submit_answer` (строгая схема):
```json
{
  "status": "answered | insufficient_data | clarification_needed",
  "summary": "одна фраза-вывод",
  "answer": "короткий ответ для руководителя (markdown: абзацы, списки, **жирный**)",
  "highlights": [{"sentiment": "positive|negative|warning|neutral", "text": "..."}],
  "metrics": [{"label": "Выручка", "result_id": "r1", "row": 0, "value_column": "value",
               "change_pct_column": "change_pct", "unit": "currency|number|percent|hours|liters|kg"}],
  "table": {"result_id": "r2", "title": "...", "columns": ["month", "revenue"],
            "column_labels": [{"column": "revenue", "label": "Выручка", "unit": "currency"}]},
  "chart": {"result_id": "r1", "type": "line|bar|area|pie", "title": "...", "x": "period",
            "series": [{"column": "value", "label": "Выручка", "unit": "currency"}]},
  "limitations": "что не удалось/допущения, или null",
  "follow_up_questions": ["..."]
}
```
`table`/`chart`/`metrics` ссылаются на реальные результаты (`result_id`) — backend сам берёт
значения из результатов. Несуществующий `result_id`/колонка → элемент отбрасывается, пишется в trace.

Цикл и лимиты:
- Одна итерация = один вызов LLM. Стоп при: `submit_answer`; `AGENT_MAX_ITERATIONS`;
  `AGENT_MAX_TOOL_CALLS` (после исчерпания — одна попытка оформить ответ из собранных данных); `AGENT_MAX_SQL_RETRIES` подряд неудачных SQL; `AGENT_TIMEOUT_SECONDS`.
  При лимите — `status: limit_reached` и честный текст «не удалось завершить анализ».
- Если модель ответила текстом без `submit_answer` — одно напоминание (в пределах лимитов), затем
  текст становится ответом `answered` без структурных блоков.
- Ретраи LLM — только внутри SDK (`LLM_MAX_RETRIES`).
- Multi-turn: из истории беседы берутся последние `AGENT_HISTORY_TURNS` пар «вопрос → summary+answer
  + использованные метрики/периоды/SQL» (компактно, без сырых данных).
- Контекст дат (`date_context.py`, детерминированно в `APP_TIMEZONE`): today, yesterday, this_week,
  last_week, last_7_days, last_30_days, this_month, last_month, this_quarter, last_quarter,
  this_year, last_year, since_january, диапазон данных — как полуинтервалы `[start, end)` с
  русскими подписями. Передаётся в первом user-сообщении.
- Grounding: числа из `answer`/`summary` сверяются со значениями результатов (с учётом округления,
  «млн/млрд/тыс», процентов); несверенные — в trace (`grounding.unverified`), ответ не блокируется.

System prompt (`prompts.py`) обязан содержать правила §30 и: не выдумывать; сначала метрики из
каталога, SQL — когда метрики недостаточно; агрегировать в SQL; изменения и доли считать SQL/
`calculate_metric`, не в уме; при нехватке данных — `insufficient_data` и объяснение; отвечать на
языке вопроса; называть период и какую метрику (например какую прибыль) считал; неполный текущий
период; различать абсолютные и процентные изменения; в начале — одна фраза плана.

### 7.7 Шаги trace (общий словарь для SSE, trace и «Показать анализ»)

`planning`, `schema_inspection`, `metric_calculation`, `sql_generated`, `sql_validation`,
`sql_execution`, `analysis`, `final_answer`, `retry`, `limit_reached`, `error`.

---

## 8. HTTP API (контракт frontend ↔ backend)

Базовый путь `/api`. JSON, даты ISO-8601, деньги — числа (float) в KZT, проценты — числа в
процентных пунктах (`-8.4` = −8,4%). Ошибки: HTTP 4xx/5xx с телом
`{"error": {"code": "...", "message": "..."}}`; коды: `invalid_request`, `not_found`,
`llm_not_configured`, `llm_unavailable`, `database_unavailable`, `unauthorized`, `internal_error`.

### 8.1 Служебные
- `GET /api/health` → `{"status": "ok|degraded", "database": "ok|error", "llm": "configured|not_configured"}`
- `GET /api/meta` →
```json
{"app": "BizNext Agent", "version": "0.1.0", "environment": "demo",
 "company": {"name": "Demo Foods", "industry": "...", "currency": "KZT", "currency_symbol": "₸"},
 "timezone": "Asia/Almaty", "today": "2026-09-14",
 "data_range": {"start": "2025-08-01", "end": "2026-09-14"},
 "llm": {"provider": "anthropic", "model": "claude-opus-5", "configured": true},
 "features": {"admin_token_required": false}}
```

### 8.2 Агент
- `POST /api/agent/ask` body `{"question": "...", "conversation_id": "uuid|null", "lang": "ru|en"}` → `AgentResponse`
- `POST /api/agent/ask/stream` тот же body → `text/event-stream`:
  - `event: start` → `{"trace_id", "conversation_id", "message_id"}`
  - `event: step` → `TraceStep` (ниже), по мере реального выполнения
  - `event: final` → `AgentResponse`
  - `event: error` → `{"code", "message", "trace_id"}`

`AgentResponse`:
```json
{
  "message_id": "uuid", "conversation_id": "uuid", "trace_id": "uuid",
  "question": "...",
  "status": "answered|insufficient_data|clarification_needed|limit_reached|error",
  "summary": "...", "answer": "markdown",
  "highlights": [{"sentiment": "positive|negative|warning|neutral", "text": "..."}],
  "metrics": [{"label": "Выручка", "value": 412345678.9, "unit": "currency",
               "formatted": "412,3 млн ₸", "change_pct": -8.4, "direction": "up|down|flat|null"}],
  "table": {"title": "...", "columns": [{"key": "month", "label": "month", "type": "text|number|date|currency|percent"}],
            "rows": [{"month": "2026-08-01", "revenue": 1.0}], "total_rows": 12, "truncated": false} ,
  "chart": {"type": "line|bar|area|pie", "title": "...", "x_key": "period", "x_type": "date|category",
            "series": [{"key": "value", "label": "Выручка", "unit": "currency"}], "data": [{"period": "2026-08-01", "value": 1.0}]},
  "sources": {"tables": ["sales_orders", "sales_order_items"], "metrics": ["revenue"]},
  "sql": [{"result_id": "r1", "purpose": "...", "sql": "SELECT ...", "row_count": 12, "execution_ms": 41}],
  "limitations": "... | null",
  "follow_up_questions": ["..."],
  "execution_ms": 5230,
  "created_at": "2026-09-14T10:00:00+05:00"
}
```
`table` и `chart` могут быть `null`.

`TraceStep`:
```json
{"seq": 3, "step": "sql_execution", "status": "started|completed|failed",
 "title": "Выполнение SQL", "started_at": "...", "duration_ms": 41,
 "detail": {"tool": "run_readonly_sql", "text": "...", "sql": "...", "purpose": "...",
            "validation": {"ok": true, "reason_code": null, "message": null},
            "result_id": "r1", "row_count": 12, "execution_ms": 41, "metric": "revenue", "error": null}}
```

### 8.3 История и отчёты
- `GET /api/conversations` → `{"conversations": [{"id", "title", "created_at", "updated_at", "message_count", "last_summary"}]}` (новые сверху)
- `GET /api/conversations/{id}` → `{"id", "title", "created_at", "messages": [{"id", "role": "user|assistant", "content", "response": AgentResponse|null, "created_at"}]}`
- `DELETE /api/conversations/{id}` → `204`
- `GET /api/reports?limit=50` → `{"reports": [{"message_id", "conversation_id", "question", "summary", "status", "has_chart", "has_table", "created_at"}]}`
- `GET /api/suggested-questions?lang=ru` → `{"questions": [{"text", "category"}]}`

### 8.4 Дашборд и инсайты (работают без LLM, кроме формулировок инсайтов)
- `GET /api/dashboard?period=last_month&lang=ru` →
```json
{"period": {"key": "last_month", "label": "Август 2026", "start": "2026-08-01", "end": "2026-09-01",
            "compare_label": "Июль 2026", "compare_start": "2026-07-01", "compare_end": "2026-08-01", "is_partial": false},
 "available_periods": [{"key": "month_to_date", "label": "Этот месяц"}],
 "kpis": [{"metric": "revenue", "label": "Выручка", "unit": "currency", "better": "higher",
           "value": 1.0, "formatted": "...", "compare_value": 1.0, "change_pct": -8.4,
           "direction": "down", "sentiment": "negative",
           "sparkline": [{"period": "2025-09-01", "value": 1.0}]}],
 "charts": [{"key": "revenue_by_category", "type": "bar", "title": "...", "x_key": "dimension", "x_type": "category",
             "series": [{"key": "revenue", "label": "Выручка", "unit": "currency"}], "data": [{"dimension": "Молоко", "revenue": 1.0}]}],
 "data_range": {"start": "...", "end": "..."}, "generated_at": "..."}
```
  `sentiment` учитывает `better` (рост расходов — negative). `change_pct` = `null`, если базы нет.
- `GET /api/insights?period=last_month&lang=ru` →
```json
{"generated_at": "...", "period": {"key": "...", "label": "..."}, "source": "llm|deterministic",
 "insights": [{"id": "revenue_change", "severity": "positive|negative|warning|info",
               "title": "Выручка снизилась на 8,4%", "text": "...",
               "facts": [{"label": "Выручка", "value": 1.0, "unit": "currency", "change_pct": -8.4}],
               "suggested_question": "Почему снизилась выручка в августе?"}]}
```
  Конвейер: правила → детерминированные факты (SQL/Python) → LLM формулирует title/text только из
  фактов → проверка чисел → при расхождении или недоступности LLM — шаблонный текст
  (`source: deterministic`). Кэш на `INSIGHTS_CACHE_SECONDS` по (period, lang).

### 8.5 Источники данных
- `GET /api/data-sources` →
```json
{"sources": [
  {"id": "postgres-demo", "type": "postgresql", "name": "PostgreSQL", "status": "connected|error",
   "description": "Демо-база предприятия (synthetic data)",
   "details": {"database": "biznext", "schema": "demo", "tables": 20, "total_rows": 1, "read_only_user": "agent_readonly",
               "data_start": "...", "data_end": "...", "checked_at": "..."}},
  {"id": "1c", "type": "1c", "name": "1С:Предприятие", "status": "coming_soon"},
  {"id": "excel", "type": "excel", "name": "Excel", "status": "coming_soon"},
  {"id": "google_sheets", "type": "google_sheets", "name": "Google Sheets", "status": "coming_soon"},
  {"id": "rest_api", "type": "rest_api", "name": "REST API", "status": "coming_soon"},
  {"id": "mysql", "type": "mysql", "name": "MySQL", "status": "coming_soon"},
  {"id": "mssql", "type": "mssql", "name": "MS SQL Server", "status": "coming_soon"}]}
```
  `connected` — только по результату реальной проверки соединения.
- `GET /api/data-sources/postgres-demo/tables` → `{"tables": [{"name", "description", "row_count", "columns": [{"name", "type", "description"}]}]}`

### 8.6 Traces (§47)
- `GET /api/traces/{trace_id}` → `TraceDetail` (для «Показать анализ»; UUID, без секретов)
- `GET /api/traces?limit=50` → `{"traces": [TraceSummary]}`; если задан `ADMIN_TOKEN` — нужен заголовок `X-Admin-Token`
```json
TraceSummary: {"trace_id", "question", "status", "created_at", "execution_ms", "llm_calls", "tool_calls", "sql_count", "retry_count"}
TraceDetail:  TraceSummary + {"conversation_id", "message_id", "provider", "model",
               "usage": {"input_tokens", "output_tokens"}, "steps": [TraceStep], "final_response": AgentResponse|null,
               "grounding": {"unverified": ["..."]}, "error": null}
```

### 8.7 Хранилище приложения — схема `app` (создаёт backend при старте, идемпотентно)

```sql
app.conversations(id uuid pk, title text, created_at timestamptz, updated_at timestamptz)
app.messages(id uuid pk, conversation_id uuid fk, role text, content text, response jsonb, trace_id uuid, created_at timestamptz)
app.agent_traces(trace_id uuid pk, conversation_id uuid, message_id uuid, question text, status text,
                 provider text, model text, created_at timestamptz, finished_at timestamptz, execution_ms int,
                 llm_calls int, tool_calls int, sql_count int, retry_count int,
                 input_tokens int, output_tokens int, steps jsonb, final_response jsonb, grounding jsonb, error text)
```
API-ключи, пароли, строки подключения и сырые промпты в trace не пишутся.

---

## 9. Frontend

- Маршруты: `/` Dashboard, `/agent` и `/agent/:conversationId` AI Agent, `/reports` Отчёты,
  `/data-sources` Источники данных, `/admin/traces` и `/admin/traces/:traceId` (виден в developer mode).
- Шапка: «BizNext Agent», компания, бейдж **DEMO · synthetic data**, переключатель RU/EN,
  developer mode (localStorage; admin token при необходимости).
- AI Agent: история слева (Сегодня / Вчера / Ранее), пустое состояние «Ask your data» + поле
  «Спросите что угодно о бизнесе…» + кнопка «Спросить» + suggested questions; ответ — карточка:
  summary, текст, highlights, KPI, график, таблица, «На основе: …», **Показать SQL** (раскрытие),
  **Показать анализ** (реальные шаги trace), follow-up вопросы. Прогресс — из SSE-событий `step`.
- Dashboard: выбор периода, KPI-карточки со спарклайнами и дельтой, графики из `charts`,
  блок AI Insights (с пометкой источника), строка «Спросить данные» → переход в агент.
- Форматирование чисел — `Intl` по `unit` (ru: `412,3 млн ₸`, `−8,4 %`).
- Типы API — один файл `src/api/types.ts`, повторяющий §8.
- nginx: SPA fallback, `/api/` → `backend:8000`, для `/api/agent/ask/stream` — `proxy_buffering off`,
  таймаут чтения ≥ 300 с.

---

## 10. Этапы и проверки

| # | Этап | Готово, когда |
|---|---|---|
| 1 | Architecture | этот документ, compose, `.env.example` |
| 2 | Database + synthetic data | `docker compose up -d db seed` → данные есть; сюжеты §3.2 видны запросами; `agent_readonly` не может писать |
| 3 | Backend + Agent + tools | unit-тесты зелёные; реальные вопросы через API на сервере |
| 4 | Tests | validator, права БД, лимиты агента, API, метрики против живой БД |
| 5 | Frontend | `npm run build` без ошибок типов; все экраны по контракту |
| 6 | Integration | `docker compose up -d --build` на сервере; UI ↔ API ↔ БД end-to-end |
| 7 | Demo scenarios | ≥10 сценариев §34 + главная демонстрация §36 вручную |
| 8 | Polish | UX, ошибки, README, финальная проверка §40 |
