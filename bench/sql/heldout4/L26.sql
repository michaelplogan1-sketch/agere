-- Distinct returned_to_stock fill_ids in 2029 YTD by return_reason.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('pharmacy_dispenses'))
SELECT json_extract(payload,'$.return_reason') AS return_reason, COUNT(DISTINCT json_extract(payload,'$.fill_id')) AS fills FROM r
WHERE json_extract(payload,'$.status')='returned_to_stock' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-08-15'
GROUP BY return_reason ORDER BY return_reason;
