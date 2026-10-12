WITH orders AS MATERIALIZED (
 SELECT patient,id FROM records WHERE stream='medication_orders'
), dispenses AS MATERIALIZED (
 SELECT DISTINCT patient,json_extract(payload,'$.medication_order_record_id') AS order_id
 FROM records WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.medication_order_record_id') IS NOT NULL
) SELECT COUNT(*) AS count FROM orders o LEFT JOIN dispenses d ON d.patient=o.patient AND d.order_id=o.id WHERE d.order_id IS NULL;
