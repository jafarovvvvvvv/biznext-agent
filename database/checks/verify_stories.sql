-- Shows that the storylines from docs/ARCHITECTURE.md §3.2 are present in the data.
-- M = last full month relative to the latest order date.
\pset footer off
WITH bounds AS (
    SELECT date_trunc('month', max(order_date))::date - interval '1 month' AS m FROM demo.sales_orders
)
SELECT to_char(m, 'YYYY-MM') AS last_full_month FROM bounds;

\echo '1. Прибыль M vs M-1: выручка, валовая прибыль, расходы, списания, операционная прибыль (млн)'
WITH months AS (
    SELECT (date_trunc('month', max(order_date)) - interval '1 month')::date AS m,
           (date_trunc('month', max(order_date)) - interval '2 month')::date AS m1
    FROM demo.sales_orders
), sales AS (
    SELECT date_trunc('month', so.order_date)::date AS month, sum(soi.revenue_amount) AS revenue, sum(soi.cost_amount) AS cogs
    FROM demo.sales_order_items soi JOIN demo.sales_orders so USING (order_id)
    WHERE so.status <> 'cancelled' GROUP BY 1
), opex AS (
    SELECT date_trunc('month', expense_date)::date AS month, sum(amount) AS opex FROM demo.operating_expenses GROUP BY 1
), wo AS (
    SELECT date_trunc('month', write_off_date)::date AS month, sum(cost_amount) AS wo FROM demo.write_offs GROUP BY 1
)
SELECT to_char(s.month, 'YYYY-MM') AS month, round(revenue / 1e6, 1) AS revenue, round((revenue - cogs) / 1e6, 1) AS gross,
       round((revenue - cogs) / revenue * 100, 1) AS gm_pct, round(opex / 1e6, 1) AS opex, round(wo / 1e6, 2) AS write_offs,
       round((revenue - cogs - opex - wo) / 1e6, 1) AS op_profit
FROM sales s JOIN opex o USING (month) JOIN wo w USING (month), months
WHERE s.month IN (months.m, months.m1) ORDER BY 1;

\echo '1a. Средняя цена сырого молока по месяцам (последние 4)'
SELECT to_char(date_trunc('month', po.order_date), 'YYYY-MM') AS month, round(sum(poi.amount) / sum(poi.quantity), 1) AS avg_price
FROM demo.purchase_order_items poi JOIN demo.purchase_orders po USING (purchase_order_id)
JOIN demo.raw_materials rm USING (material_id)
WHERE rm.name = 'Молоко сырое' AND po.status <> 'cancelled'
GROUP BY 1 ORDER BY 1 DESC LIMIT 4;

\echo '1b. Внеплановые простои дольше 8 часов'
SELECT pl.name, ld.started_at::date, round(ld.duration_minutes / 60.0, 1) AS hours, ld.reason
FROM demo.line_downtime ld JOIN demo.production_lines pl USING (line_id)
WHERE ld.downtime_type = 'unplanned' AND ld.duration_minutes > 480 ORDER BY ld.started_at;

\echo '1c. Списания по складам и причинам за последние 3 месяца (млн)'
SELECT to_char(date_trunc('month', write_off_date), 'YYYY-MM') AS month, w.name, reason, round(sum(cost_amount) / 1e6, 2) AS cost
FROM demo.write_offs wo JOIN demo.warehouses w USING (warehouse_id)
WHERE write_off_date >= (SELECT date_trunc('month', max(write_off_date)) - interval '2 month' FROM demo.write_offs)
GROUP BY 1, 2, 3 HAVING sum(cost_amount) > 300000 ORDER BY 1, 4 DESC;

\echo '2. Рост категорий: выручка первого и последнего полного месяца (млн)'
WITH cat AS (
    SELECT pc.name, date_trunc('month', so.order_date)::date AS month, sum(soi.revenue_amount) AS revenue
    FROM demo.sales_order_items soi JOIN demo.sales_orders so USING (order_id)
    JOIN demo.products p USING (product_id) JOIN demo.product_categories pc USING (category_id)
    WHERE so.status <> 'cancelled' GROUP BY 1, 2
), b AS (
    SELECT min(month) AS first_m, (date_trunc('month', max(month)) - interval '1 month')::date AS last_m FROM cat
)
SELECT c1.name, round(c1.revenue / 1e6, 1) AS first_month, round(c2.revenue / 1e6, 1) AS last_full_month,
       round((c2.revenue / c1.revenue - 1) * 100, 1) AS change_pct
FROM b JOIN cat c1 ON c1.month = b.first_m JOIN cat c2 ON c2.month = b.last_m AND c2.name = c1.name
ORDER BY change_pct DESC;

\echo '3. Сыры в Караганде по месяцам (млн)'
SELECT to_char(date_trunc('month', so.order_date), 'YYYY-MM') AS month, round(sum(soi.revenue_amount) / 1e6, 2) AS revenue
FROM demo.sales_order_items soi JOIN demo.sales_orders so USING (order_id)
JOIN demo.customers c USING (customer_id) JOIN demo.regions r USING (region_id)
JOIN demo.products p USING (product_id) JOIN demo.product_categories pc USING (category_id)
WHERE so.status <> 'cancelled' AND r.name = 'Караганда' AND pc.name = 'Сыры'
GROUP BY 1 ORDER BY 1;

\echo '3a. Клиенты Караганды: последний заказ'
SELECT c.name, c.customer_type, max(so.order_date) AS last_order
FROM demo.customers c JOIN demo.regions r USING (region_id) JOIN demo.sales_orders so USING (customer_id)
WHERE r.name = 'Караганда' AND c.customer_type = 'Розничная сеть' GROUP BY 1, 2;

\echo '5. Остатки ниже точки заказа на последнюю дату'
SELECT p.name, w.name AS warehouse, inv.quantity_on_hand, inv.reorder_point
FROM demo.inventory_snapshots inv JOIN demo.products p USING (product_id) JOIN demo.warehouses w USING (warehouse_id)
WHERE inv.snapshot_date = (SELECT max(snapshot_date) FROM demo.inventory_snapshots)
  AND inv.quantity_on_hand < inv.reorder_point ORDER BY p.name, w.name;

\echo '5a. Просроченная дебиторка на последнюю дату (топ-8, млн)'
SELECT c.name, count(DISTINCT so.order_id) AS orders, round(sum(soi.revenue_amount) / 1e6, 2) AS amount,
       max((SELECT max(order_date) FROM demo.sales_orders) - so.payment_due_date) AS max_days_overdue
FROM demo.sales_orders so JOIN demo.sales_order_items soi USING (order_id) JOIN demo.customers c USING (customer_id)
WHERE so.status <> 'cancelled' AND so.paid_date IS NULL
  AND so.payment_due_date < (SELECT max(order_date) FROM demo.sales_orders)
GROUP BY 1 ORDER BY amount DESC LIMIT 8;

\echo '6. Всплеск возвратов (дни с максимальной суммой)'
SELECT sr.return_date, p.name, sr.reason, sum(sr.quantity) AS qty, round(sum(sr.refund_amount) / 1e6, 2) AS refund
FROM demo.sales_returns sr JOIN demo.products p USING (product_id)
GROUP BY 1, 2, 3 ORDER BY refund DESC LIMIT 4;

\echo '6a. Партии с долей брака > 5%'
SELECT pb.production_date, p.name, pb.produced_quantity, pb.defect_quantity,
       round(pb.defect_quantity * 100.0 / pb.produced_quantity, 1) AS defect_pct
FROM demo.production_batches pb JOIN demo.products p USING (product_id)
WHERE pb.produced_quantity > 0 AND pb.defect_quantity * 1.0 / pb.produced_quantity > 0.05;

\echo '7. Выполнение плана продаж за M по регионам (%)'
WITH m AS (SELECT (date_trunc('month', max(order_date)) - interval '1 month')::date AS m FROM demo.sales_orders),
fact AS (
    SELECT c.region_id, sum(soi.revenue_amount) AS revenue
    FROM demo.sales_order_items soi JOIN demo.sales_orders so USING (order_id) JOIN demo.customers c USING (customer_id), m
    WHERE so.status <> 'cancelled' AND date_trunc('month', so.order_date) = m.m GROUP BY 1
), plan AS (
    SELECT region_id, sum(planned_revenue) AS plan FROM demo.sales_plans, m WHERE plan_month = m.m GROUP BY 1
)
SELECT r.name, round(fact.revenue / plan.plan * 100, 1) AS attainment_pct
FROM fact JOIN plan USING (region_id) JOIN demo.regions r USING (region_id) ORDER BY 2;
