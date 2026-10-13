-- Pharmacy fills returned to stock (prepared but never picked up) with the return dated (event_date) in calendar 2028, by return reason.
-- Counts distinct fill_id on pharmacy_dispenses records with status 'returned_to_stock'.
SELECT json_extract(payload,'$.return_reason') AS return_reason, COUNT(DISTINCT json_extract(payload,'$.fill_id')) AS fills
FROM records WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='returned_to_stock'
  AND json_extract(payload,'$.event_date') BETWEEN '2028-01-01' AND '2028-12-31'
GROUP BY 1 ORDER BY 1;
