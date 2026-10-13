-- Laboratory orders placed (order event_date) in 2029 through the as-of date with no laboratory_result_notifications record
-- linked to them (notification.laboratory_order_record_id = order record_id) received by the as-of date.
SELECT COUNT(*) AS count FROM records o
WHERE o.stream='laboratory_orders' AND json_extract(o.payload,'$.event_date') BETWEEN '2029-01-01' AND :as_of
  AND json_extract(o.payload,'$.record_id') NOT IN (
    SELECT json_extract(payload,'$.laboratory_order_record_id') FROM records WHERE stream='laboratory_result_notifications'
    AND json_extract(payload,'$.laboratory_order_record_id') IS NOT NULL);
