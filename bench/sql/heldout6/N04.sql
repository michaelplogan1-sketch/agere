-- Distinct patients with any insulin record across med list, orders, dispenses, pharmacy claims and pharmacy history.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream IN ('medication_lists','medication_orders','pharmacy_dispenses','pharmacy_claims','pharmacy_history')),
m AS (SELECT patient, lower(coalesce(json_extract(payload,'$.medication_name'), json_extract(payload,'$.name'),'')) n FROM r)
SELECT COUNT(DISTINCT patient) AS count FROM m WHERE n LIKE '%insulin%' OR n LIKE '%humulin%';
