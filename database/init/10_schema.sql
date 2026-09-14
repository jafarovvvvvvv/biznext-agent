-- BizNext Agent demo schema: вымышленный молочный завод «Demo Foods».
-- Все денежные суммы — тенге (KZT) без НДС. Данные синтетические.

SET search_path = demo;

-- ---------------------------------------------------------------- справочники

CREATE TABLE regions (
    region_id    smallint PRIMARY KEY,
    name         text NOT NULL UNIQUE,
    macro_region text NOT NULL CHECK (macro_region IN ('Юг', 'Север', 'Центр', 'Запад', 'Восток'))
);
COMMENT ON TABLE regions IS 'Регионы Казахстана, в которых работают клиенты и склады.';
COMMENT ON COLUMN regions.region_id IS 'Идентификатор региона.';
COMMENT ON COLUMN regions.name IS 'Название региона (город).';
COMMENT ON COLUMN regions.macro_region IS 'Макрорегион: Юг, Север, Центр, Запад, Восток.';

CREATE TABLE warehouses (
    warehouse_id     smallint PRIMARY KEY,
    name             text NOT NULL UNIQUE,
    region_id        smallint NOT NULL REFERENCES regions,
    warehouse_type   text NOT NULL CHECK (warehouse_type IN ('plant', 'distribution_center')),
    capacity_pallets integer NOT NULL
);
COMMENT ON TABLE warehouses IS 'Склады: склад готовой продукции завода и региональные распределительные центры (РЦ).';
COMMENT ON COLUMN warehouses.warehouse_id IS 'Идентификатор склада.';
COMMENT ON COLUMN warehouses.name IS 'Название склада.';
COMMENT ON COLUMN warehouses.region_id IS 'Регион расположения склада.';
COMMENT ON COLUMN warehouses.warehouse_type IS 'Тип: plant — склад завода, distribution_center — распределительный центр.';
COMMENT ON COLUMN warehouses.capacity_pallets IS 'Вместимость склада, паллетомест.';

CREATE TABLE product_categories (
    category_id smallint PRIMARY KEY,
    name        text NOT NULL UNIQUE,
    description text NOT NULL
);
COMMENT ON TABLE product_categories IS 'Категории продукции.';
COMMENT ON COLUMN product_categories.category_id IS 'Идентификатор категории.';
COMMENT ON COLUMN product_categories.name IS 'Название категории.';
COMMENT ON COLUMN product_categories.description IS 'Описание категории.';

CREATE TABLE products (
    product_id      integer PRIMARY KEY,
    sku             text NOT NULL UNIQUE,
    name            text NOT NULL,
    category_id     smallint NOT NULL REFERENCES product_categories,
    package_size    numeric(10,3) NOT NULL,
    package_unit    text NOT NULL CHECK (package_unit IN ('л', 'кг')),
    shelf_life_days integer NOT NULL,
    list_price      numeric(12,2) NOT NULL,
    launched_at     date NOT NULL,
    is_active       boolean NOT NULL DEFAULT true
);
COMMENT ON TABLE products IS 'Товары (SKU), выпускаемые заводом.';
COMMENT ON COLUMN products.product_id IS 'Идентификатор товара.';
COMMENT ON COLUMN products.sku IS 'Артикул товара.';
COMMENT ON COLUMN products.name IS 'Название товара с фасовкой.';
COMMENT ON COLUMN products.category_id IS 'Категория товара.';
COMMENT ON COLUMN products.package_size IS 'Объём или вес одной упаковки.';
COMMENT ON COLUMN products.package_unit IS 'Единица фасовки: л или кг.';
COMMENT ON COLUMN products.shelf_life_days IS 'Срок годности, дней.';
COMMENT ON COLUMN products.list_price IS 'Текущая базовая оптовая цена за упаковку, тенге без НДС.';
COMMENT ON COLUMN products.launched_at IS 'Дата вывода товара на рынок.';
COMMENT ON COLUMN products.is_active IS 'Товар в текущем ассортименте.';

CREATE TABLE raw_materials (
    material_id   integer PRIMARY KEY,
    name          text NOT NULL UNIQUE,
    unit          text NOT NULL CHECK (unit IN ('л', 'кг', 'шт')),
    material_type text NOT NULL CHECK (material_type IN ('Сырьё', 'Ингредиенты', 'Упаковка'))
);
COMMENT ON TABLE raw_materials IS 'Сырьё, ингредиенты и упаковочные материалы.';
COMMENT ON COLUMN raw_materials.material_id IS 'Идентификатор материала.';
COMMENT ON COLUMN raw_materials.name IS 'Название материала.';
COMMENT ON COLUMN raw_materials.unit IS 'Единица измерения: л, кг или шт.';
COMMENT ON COLUMN raw_materials.material_type IS 'Тип: Сырьё, Ингредиенты, Упаковка.';

CREATE TABLE suppliers (
    supplier_id        integer PRIMARY KEY,
    name               text NOT NULL UNIQUE,
    region_id          smallint NOT NULL REFERENCES regions,
    material_type      text NOT NULL CHECK (material_type IN ('Сырьё', 'Ингредиенты', 'Упаковка')),
    payment_terms_days integer NOT NULL,
    reliability_rating numeric(3,2) NOT NULL
);
COMMENT ON TABLE suppliers IS 'Поставщики сырья, ингредиентов и упаковки.';
COMMENT ON COLUMN suppliers.supplier_id IS 'Идентификатор поставщика.';
COMMENT ON COLUMN suppliers.name IS 'Название поставщика.';
COMMENT ON COLUMN suppliers.region_id IS 'Регион поставщика.';
COMMENT ON COLUMN suppliers.material_type IS 'Какой тип материалов поставляет.';
COMMENT ON COLUMN suppliers.payment_terms_days IS 'Отсрочка оплаты поставщику, дней.';
COMMENT ON COLUMN suppliers.reliability_rating IS 'Оценка надёжности поставщика от 0 до 5.';

CREATE TABLE bill_of_materials (
    product_id        integer NOT NULL REFERENCES products,
    material_id       integer NOT NULL REFERENCES raw_materials,
    quantity_per_unit numeric(12,4) NOT NULL,
    PRIMARY KEY (product_id, material_id)
);
COMMENT ON TABLE bill_of_materials IS 'Нормы расхода материалов на одну упаковку товара (спецификация).';
COMMENT ON COLUMN bill_of_materials.product_id IS 'Товар.';
COMMENT ON COLUMN bill_of_materials.material_id IS 'Материал.';
COMMENT ON COLUMN bill_of_materials.quantity_per_unit IS 'Расход материала на одну упаковку, в единицах материала.';

-- ---------------------------------------------------------------- закупки

CREATE TABLE purchase_orders (
    purchase_order_id      bigint PRIMARY KEY,
    po_number              text NOT NULL UNIQUE,
    supplier_id            integer NOT NULL REFERENCES suppliers,
    warehouse_id           smallint NOT NULL REFERENCES warehouses,
    order_date             date NOT NULL,
    expected_delivery_date date NOT NULL,
    delivered_date         date,
    status                 text NOT NULL CHECK (status IN ('delivered', 'in_transit', 'cancelled'))
);
COMMENT ON TABLE purchase_orders IS 'Заказы поставщикам (шапка закупки).';
COMMENT ON COLUMN purchase_orders.purchase_order_id IS 'Идентификатор заказа поставщику.';
COMMENT ON COLUMN purchase_orders.po_number IS 'Номер заказа поставщику.';
COMMENT ON COLUMN purchase_orders.supplier_id IS 'Поставщик.';
COMMENT ON COLUMN purchase_orders.warehouse_id IS 'Склад получения (завод).';
COMMENT ON COLUMN purchase_orders.order_date IS 'Дата заказа поставщику; используется как дата закупки.';
COMMENT ON COLUMN purchase_orders.expected_delivery_date IS 'Ожидаемая дата поставки.';
COMMENT ON COLUMN purchase_orders.delivered_date IS 'Фактическая дата поставки; NULL — ещё не поставлено.';
COMMENT ON COLUMN purchase_orders.status IS 'Статус: delivered — поставлен, in_transit — в пути, cancelled — отменён.';

CREATE TABLE purchase_order_items (
    purchase_order_item_id bigint PRIMARY KEY,
    purchase_order_id      bigint NOT NULL REFERENCES purchase_orders,
    material_id            integer NOT NULL REFERENCES raw_materials,
    quantity               numeric(14,3) NOT NULL,
    unit_price             numeric(12,2) NOT NULL,
    amount                 numeric(16,2) NOT NULL
);
COMMENT ON TABLE purchase_order_items IS 'Строки заказов поставщикам.';
COMMENT ON COLUMN purchase_order_items.purchase_order_item_id IS 'Идентификатор строки закупки.';
COMMENT ON COLUMN purchase_order_items.purchase_order_id IS 'Заказ поставщику.';
COMMENT ON COLUMN purchase_order_items.material_id IS 'Закупаемый материал.';
COMMENT ON COLUMN purchase_order_items.quantity IS 'Количество в единицах материала.';
COMMENT ON COLUMN purchase_order_items.unit_price IS 'Закупочная цена за единицу, тенге без НДС.';
COMMENT ON COLUMN purchase_order_items.amount IS 'Сумма строки = quantity * unit_price, тенге без НДС.';

-- ---------------------------------------------------------------- производство

CREATE TABLE production_lines (
    line_id                 smallint PRIMARY KEY,
    name                    text NOT NULL UNIQUE,
    warehouse_id            smallint NOT NULL REFERENCES warehouses,
    category_id             smallint NOT NULL REFERENCES product_categories,
    capacity_units_per_hour integer NOT NULL,
    commissioned_at         date NOT NULL
);
COMMENT ON TABLE production_lines IS 'Производственные линии завода.';
COMMENT ON COLUMN production_lines.line_id IS 'Идентификатор линии.';
COMMENT ON COLUMN production_lines.name IS 'Название линии.';
COMMENT ON COLUMN production_lines.warehouse_id IS 'Завод (склад), на котором стоит линия.';
COMMENT ON COLUMN production_lines.category_id IS 'Основная категория продукции линии.';
COMMENT ON COLUMN production_lines.capacity_units_per_hour IS 'Паспортная производительность, упаковок в час.';
COMMENT ON COLUMN production_lines.commissioned_at IS 'Дата ввода линии в эксплуатацию.';

CREATE TABLE production_batches (
    batch_id          bigint PRIMARY KEY,
    line_id           smallint NOT NULL REFERENCES production_lines,
    product_id        integer NOT NULL REFERENCES products,
    production_date   date NOT NULL,
    shift             text NOT NULL CHECK (shift IN ('day', 'night')),
    planned_quantity  integer NOT NULL,
    produced_quantity integer NOT NULL,
    defect_quantity   integer NOT NULL,
    labor_hours       numeric(8,2) NOT NULL
);
COMMENT ON TABLE production_batches IS 'Производственные партии: план и факт выпуска по сменам.';
COMMENT ON COLUMN production_batches.batch_id IS 'Идентификатор партии.';
COMMENT ON COLUMN production_batches.line_id IS 'Линия, на которой выпущена партия.';
COMMENT ON COLUMN production_batches.product_id IS 'Выпущенный товар.';
COMMENT ON COLUMN production_batches.production_date IS 'Дата выпуска.';
COMMENT ON COLUMN production_batches.shift IS 'Смена: day — дневная, night — ночная.';
COMMENT ON COLUMN production_batches.planned_quantity IS 'План выпуска, упаковок.';
COMMENT ON COLUMN production_batches.produced_quantity IS 'Фактически выпущено, упаковок (включая брак).';
COMMENT ON COLUMN production_batches.defect_quantity IS 'Брак, упаковок (входит в produced_quantity).';
COMMENT ON COLUMN production_batches.labor_hours IS 'Отработанные человеко-часы на партию.';

CREATE TABLE line_downtime (
    downtime_id      bigint PRIMARY KEY,
    line_id          smallint NOT NULL REFERENCES production_lines,
    started_at       timestamptz NOT NULL,
    ended_at         timestamptz NOT NULL,
    duration_minutes integer NOT NULL,
    downtime_type    text NOT NULL CHECK (downtime_type IN ('planned', 'unplanned')),
    reason           text NOT NULL,
    comment          text
);
COMMENT ON TABLE line_downtime IS 'Простои производственных линий.';
COMMENT ON COLUMN line_downtime.downtime_id IS 'Идентификатор простоя.';
COMMENT ON COLUMN line_downtime.line_id IS 'Линия.';
COMMENT ON COLUMN line_downtime.started_at IS 'Начало простоя.';
COMMENT ON COLUMN line_downtime.ended_at IS 'Окончание простоя.';
COMMENT ON COLUMN line_downtime.duration_minutes IS 'Длительность простоя, минут.';
COMMENT ON COLUMN line_downtime.downtime_type IS 'Тип: planned — плановый, unplanned — внеплановый.';
COMMENT ON COLUMN line_downtime.reason IS 'Причина простоя.';
COMMENT ON COLUMN line_downtime.comment IS 'Комментарий мастера смены.';

-- ---------------------------------------------------------------- персонал и клиенты

CREATE TABLE employees (
    employee_id      integer PRIMARY KEY,
    full_name        text NOT NULL,
    department       text NOT NULL,
    position         text NOT NULL,
    region_id        smallint REFERENCES regions,
    hire_date        date NOT NULL,
    termination_date date,
    monthly_salary   numeric(12,2) NOT NULL
);
COMMENT ON TABLE employees IS 'Сотрудники (вымышленные).';
COMMENT ON COLUMN employees.employee_id IS 'Идентификатор сотрудника.';
COMMENT ON COLUMN employees.full_name IS 'ФИО (вымышленное).';
COMMENT ON COLUMN employees.department IS 'Подразделение.';
COMMENT ON COLUMN employees.position IS 'Должность.';
COMMENT ON COLUMN employees.region_id IS 'Регион ответственности (для менеджеров продаж), иначе NULL.';
COMMENT ON COLUMN employees.hire_date IS 'Дата приёма на работу.';
COMMENT ON COLUMN employees.termination_date IS 'Дата увольнения; NULL — работает.';
COMMENT ON COLUMN employees.monthly_salary IS 'Оклад в месяц, тенге.';

CREATE TABLE customers (
    customer_id        integer PRIMARY KEY,
    name               text NOT NULL,
    customer_type      text NOT NULL CHECK (customer_type IN ('Розничная сеть', 'Дистрибьютор', 'HoReCa', 'Независимый магазин')),
    region_id          smallint NOT NULL REFERENCES regions,
    sales_manager_id   integer NOT NULL REFERENCES employees,
    payment_terms_days integer NOT NULL,
    credit_limit       numeric(16,2) NOT NULL,
    created_at         date NOT NULL,
    is_active          boolean NOT NULL DEFAULT true
);
COMMENT ON TABLE customers IS 'Клиенты (покупатели продукции), вымышленные.';
COMMENT ON COLUMN customers.customer_id IS 'Идентификатор клиента.';
COMMENT ON COLUMN customers.name IS 'Название клиента.';
COMMENT ON COLUMN customers.customer_type IS 'Тип клиента: Розничная сеть, Дистрибьютор, HoReCa, Независимый магазин.';
COMMENT ON COLUMN customers.region_id IS 'Регион клиента.';
COMMENT ON COLUMN customers.sales_manager_id IS 'Ответственный менеджер по продажам.';
COMMENT ON COLUMN customers.payment_terms_days IS 'Отсрочка оплаты по договору, дней.';
COMMENT ON COLUMN customers.credit_limit IS 'Кредитный лимит, тенге.';
COMMENT ON COLUMN customers.created_at IS 'Дата начала работы с клиентом.';
COMMENT ON COLUMN customers.is_active IS 'Договор с клиентом действует.';

-- ---------------------------------------------------------------- продажи

CREATE TABLE sales_orders (
    order_id         bigint PRIMARY KEY,
    order_number     text NOT NULL UNIQUE,
    customer_id      integer NOT NULL REFERENCES customers,
    warehouse_id     smallint NOT NULL REFERENCES warehouses,
    sales_manager_id integer NOT NULL REFERENCES employees,
    order_date       date NOT NULL,
    shipped_date     date,
    status           text NOT NULL CHECK (status IN ('delivered', 'shipped', 'cancelled')),
    payment_due_date date NOT NULL,
    paid_date        date
);
COMMENT ON TABLE sales_orders IS 'Заказы клиентов (шапка). Одна строка — один заказ.';
COMMENT ON COLUMN sales_orders.order_id IS 'Идентификатор заказа.';
COMMENT ON COLUMN sales_orders.order_number IS 'Номер заказа.';
COMMENT ON COLUMN sales_orders.customer_id IS 'Клиент.';
COMMENT ON COLUMN sales_orders.warehouse_id IS 'Склад отгрузки.';
COMMENT ON COLUMN sales_orders.sales_manager_id IS 'Менеджер, оформивший заказ.';
COMMENT ON COLUMN sales_orders.order_date IS 'Дата заказа; по ней считается выручка за период.';
COMMENT ON COLUMN sales_orders.shipped_date IS 'Дата отгрузки; NULL для отменённых.';
COMMENT ON COLUMN sales_orders.status IS 'Статус: delivered — доставлен, shipped — отгружен, в пути, cancelled — отменён (не входит в выручку).';
COMMENT ON COLUMN sales_orders.payment_due_date IS 'Срок оплаты по договору.';
COMMENT ON COLUMN sales_orders.paid_date IS 'Дата оплаты; NULL — не оплачен.';

CREATE TABLE sales_order_items (
    order_item_id  bigint PRIMARY KEY,
    order_id       bigint NOT NULL REFERENCES sales_orders,
    product_id     integer NOT NULL REFERENCES products,
    quantity       integer NOT NULL CHECK (quantity > 0),
    unit_price     numeric(12,2) NOT NULL,
    discount_pct   numeric(5,2) NOT NULL DEFAULT 0,
    revenue_amount numeric(16,2) NOT NULL,
    unit_cost      numeric(12,2) NOT NULL,
    cost_amount    numeric(16,2) NOT NULL
);
COMMENT ON TABLE sales_order_items IS 'Строки заказов клиентов: товар, количество, цена, выручка и себестоимость.';
COMMENT ON COLUMN sales_order_items.order_item_id IS 'Идентификатор строки заказа.';
COMMENT ON COLUMN sales_order_items.order_id IS 'Заказ.';
COMMENT ON COLUMN sales_order_items.product_id IS 'Товар.';
COMMENT ON COLUMN sales_order_items.quantity IS 'Количество отгруженных упаковок.';
COMMENT ON COLUMN sales_order_items.unit_price IS 'Цена за упаковку до скидки, тенге без НДС.';
COMMENT ON COLUMN sales_order_items.discount_pct IS 'Скидка клиенту, %.';
COMMENT ON COLUMN sales_order_items.revenue_amount IS 'Выручка по строке = quantity * unit_price * (1 - discount_pct/100), тенге без НДС.';
COMMENT ON COLUMN sales_order_items.unit_cost IS 'Производственная себестоимость упаковки на дату заказа (сырьё + переработка), тенге.';
COMMENT ON COLUMN sales_order_items.cost_amount IS 'Себестоимость строки = quantity * unit_cost, тенге.';

CREATE TABLE sales_returns (
    return_id     bigint PRIMARY KEY,
    order_id      bigint NOT NULL REFERENCES sales_orders,
    product_id    integer NOT NULL REFERENCES products,
    return_date   date NOT NULL,
    quantity      integer NOT NULL CHECK (quantity > 0),
    reason        text NOT NULL,
    refund_amount numeric(16,2) NOT NULL
);
COMMENT ON TABLE sales_returns IS 'Возвраты товара от клиентов.';
COMMENT ON COLUMN sales_returns.return_id IS 'Идентификатор возврата.';
COMMENT ON COLUMN sales_returns.order_id IS 'Исходный заказ.';
COMMENT ON COLUMN sales_returns.product_id IS 'Возвращённый товар.';
COMMENT ON COLUMN sales_returns.return_date IS 'Дата возврата.';
COMMENT ON COLUMN sales_returns.quantity IS 'Количество возвращённых упаковок.';
COMMENT ON COLUMN sales_returns.reason IS 'Причина возврата.';
COMMENT ON COLUMN sales_returns.refund_amount IS 'Сумма возврата клиенту, тенге без НДС.';

-- ---------------------------------------------------------------- склад

CREATE TABLE inventory_snapshots (
    snapshot_date    date NOT NULL,
    warehouse_id     smallint NOT NULL REFERENCES warehouses,
    product_id       integer NOT NULL REFERENCES products,
    quantity_on_hand integer NOT NULL,
    reorder_point    integer NOT NULL,
    PRIMARY KEY (snapshot_date, warehouse_id, product_id)
);
COMMENT ON TABLE inventory_snapshots IS 'Остатки готовой продукции на конец дня по складам. Текущие остатки — строки с максимальной snapshot_date.';
COMMENT ON COLUMN inventory_snapshots.snapshot_date IS 'Дата среза остатков (конец дня).';
COMMENT ON COLUMN inventory_snapshots.warehouse_id IS 'Склад.';
COMMENT ON COLUMN inventory_snapshots.product_id IS 'Товар.';
COMMENT ON COLUMN inventory_snapshots.quantity_on_hand IS 'Остаток, упаковок.';
COMMENT ON COLUMN inventory_snapshots.reorder_point IS 'Точка заказа: при остатке ниже нужно пополнение, упаковок.';

CREATE TABLE write_offs (
    write_off_id   bigint PRIMARY KEY,
    write_off_date date NOT NULL,
    warehouse_id   smallint NOT NULL REFERENCES warehouses,
    product_id     integer NOT NULL REFERENCES products,
    quantity       integer NOT NULL CHECK (quantity > 0),
    reason         text NOT NULL,
    cost_amount    numeric(16,2) NOT NULL
);
COMMENT ON TABLE write_offs IS 'Списания готовой продукции со складов (потери).';
COMMENT ON COLUMN write_offs.write_off_id IS 'Идентификатор списания.';
COMMENT ON COLUMN write_offs.write_off_date IS 'Дата списания.';
COMMENT ON COLUMN write_offs.warehouse_id IS 'Склад.';
COMMENT ON COLUMN write_offs.product_id IS 'Товар.';
COMMENT ON COLUMN write_offs.quantity IS 'Списано упаковок.';
COMMENT ON COLUMN write_offs.reason IS 'Причина списания.';
COMMENT ON COLUMN write_offs.cost_amount IS 'Себестоимость списанного товара, тенге.';

-- ---------------------------------------------------------------- финансы и план

CREATE TABLE operating_expenses (
    expense_id       bigint PRIMARY KEY,
    expense_date     date NOT NULL,
    expense_category text NOT NULL CHECK (expense_category IN ('Логистика', 'Фонд оплаты труда', 'Электроэнергия', 'Маркетинг', 'Аренда', 'Ремонт и обслуживание', 'Прочее')),
    department       text NOT NULL,
    amount           numeric(16,2) NOT NULL,
    description      text NOT NULL
);
COMMENT ON TABLE operating_expenses IS 'Операционные расходы (не входят в производственную себестоимость товаров).';
COMMENT ON COLUMN operating_expenses.expense_id IS 'Идентификатор расхода.';
COMMENT ON COLUMN operating_expenses.expense_date IS 'Дата расхода.';
COMMENT ON COLUMN operating_expenses.expense_category IS 'Статья расходов.';
COMMENT ON COLUMN operating_expenses.department IS 'Подразделение.';
COMMENT ON COLUMN operating_expenses.amount IS 'Сумма, тенге без НДС.';
COMMENT ON COLUMN operating_expenses.description IS 'Описание расхода.';

CREATE TABLE sales_plans (
    plan_month      date NOT NULL CHECK (extract(day FROM plan_month) = 1),
    region_id       smallint NOT NULL REFERENCES regions,
    category_id     smallint NOT NULL REFERENCES product_categories,
    planned_revenue numeric(16,2) NOT NULL,
    PRIMARY KEY (plan_month, region_id, category_id)
);
COMMENT ON TABLE sales_plans IS 'План продаж (выручки) по месяцам, регионам клиентов и категориям.';
COMMENT ON COLUMN sales_plans.plan_month IS 'Месяц плана (первое число месяца).';
COMMENT ON COLUMN sales_plans.region_id IS 'Регион клиентов.';
COMMENT ON COLUMN sales_plans.category_id IS 'Категория продукции.';
COMMENT ON COLUMN sales_plans.planned_revenue IS 'Плановая выручка, тенге без НДС.';

-- ---------------------------------------------------------------- индексы

CREATE INDEX ON warehouses (region_id);
CREATE INDEX ON products (category_id);
CREATE INDEX ON suppliers (region_id);
CREATE INDEX ON bill_of_materials (material_id);
CREATE INDEX ON purchase_orders (supplier_id);
CREATE INDEX ON purchase_orders (order_date);
CREATE INDEX ON purchase_order_items (purchase_order_id);
CREATE INDEX ON purchase_order_items (material_id);
CREATE INDEX ON production_lines (category_id);
CREATE INDEX ON production_batches (production_date);
CREATE INDEX ON production_batches (line_id, production_date);
CREATE INDEX ON production_batches (product_id);
CREATE INDEX ON line_downtime (line_id, started_at);
CREATE INDEX ON employees (department);
CREATE INDEX ON customers (region_id);
CREATE INDEX ON customers (sales_manager_id);
CREATE INDEX ON sales_orders (order_date, status);
CREATE INDEX ON sales_orders (customer_id);
CREATE INDEX ON sales_orders (warehouse_id);
CREATE INDEX ON sales_orders (sales_manager_id);
CREATE INDEX ON sales_orders (payment_due_date) WHERE paid_date IS NULL;
CREATE INDEX ON sales_order_items (order_id);
CREATE INDEX ON sales_order_items (product_id);
CREATE INDEX ON sales_returns (return_date);
CREATE INDEX ON sales_returns (order_id);
CREATE INDEX ON sales_returns (product_id);
CREATE INDEX ON inventory_snapshots (warehouse_id, product_id);
CREATE INDEX ON inventory_snapshots (product_id);
CREATE INDEX ON write_offs (write_off_date);
CREATE INDEX ON write_offs (warehouse_id, product_id);
CREATE INDEX ON operating_expenses (expense_date);
CREATE INDEX ON operating_expenses (expense_category);
CREATE INDEX ON sales_plans (region_id);
CREATE INDEX ON sales_plans (category_id);
