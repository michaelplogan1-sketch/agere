-- Distinct laboratory_order_ids whose requested_tests_local includes 'UACR', order event date in 2028.
SELECT COUNT(DISTINCT json_extract(payload,'$.laboratory_order_id')) AS count FROM records
WHERE stream='laboratory_orders' AND event BETWEEN '2028-01-01' AND '2028-12-31'
  AND EXISTS (SELECT 1 FROM json_each(json_extract(payload,'$.requested_tests_local')) WHERE value='UACR');
