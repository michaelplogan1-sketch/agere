-- Patients with lab results whose laboratory_order_record_id has no matching laboratory_orders record.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('labs','laboratory_orders')),
o AS (SELECT json_extract(payload,'$.record_id') rid FROM r WHERE stream='laboratory_orders')
SELECT DISTINCT patient AS patient_id FROM r WHERE stream='labs' AND json_extract(payload,'$.laboratory_order_record_id') IS NOT NULL
  AND json_extract(payload,'$.laboratory_order_record_id') NOT IN (SELECT rid FROM o) ORDER BY patient;
