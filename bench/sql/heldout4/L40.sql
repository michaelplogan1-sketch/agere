-- laboratory_orders dated <= 2029-07-16 with no result referencing them.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('labs','laboratory_orders')),
used AS (SELECT DISTINCT json_extract(payload,'$.laboratory_order_record_id') oid FROM r WHERE stream='labs' AND json_extract(payload,'$.laboratory_order_record_id') IS NOT NULL)
SELECT COUNT(*) AS count FROM r WHERE stream='laboratory_orders' AND json_extract(payload,'$.event_date') <= '2029-07-16'
  AND json_extract(payload,'$.record_id') NOT IN (SELECT oid FROM used);
