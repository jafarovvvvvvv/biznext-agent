-- Run as the agent role, bypassing the application validator:
--   docker compose exec -T db psql -U agent_readonly -d biznext -f /checks/verify_permissions.sql
-- Every write/DDL/DCL attempt must fail; SELECT on demo must work; app schema must be unreadable.
\set ON_ERROR_STOP off
\echo '--- expected OK: select from demo'
SELECT count(*) AS orders FROM demo.sales_orders;
SHOW default_transaction_read_only;
\echo '--- expected ERROR for every statement below'
INSERT INTO demo.regions (region_id, name, macro_region) VALUES (99, 'x', 'Юг');
UPDATE demo.products SET list_price = 1 WHERE product_id = 1;
DELETE FROM demo.sales_returns WHERE return_id = 1;
TRUNCATE demo.write_offs;
DROP TABLE demo.sales_plans;
CREATE TABLE demo.hack (id int);
CREATE TEMP TABLE hack_tmp (id int);
ALTER TABLE demo.products ADD COLUMN hack int;
GRANT SELECT ON demo.products TO PUBLIC;
SELECT * FROM app.agent_traces LIMIT 1;
BEGIN READ WRITE; INSERT INTO demo.regions (region_id, name, macro_region) VALUES (98, 'y', 'Юг'); ROLLBACK;
SET default_transaction_read_only = off; UPDATE demo.products SET list_price = 1 WHERE product_id = 1;
