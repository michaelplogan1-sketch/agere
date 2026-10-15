-- Prescriptions prepared but never picked up in 2029: distinct fill_id with a pharmacy_dispenses row of status 'returned_to_stock' dated 2029-01-01..2029-12-31.
SELECT COUNT(DISTINCT json_extract(payload,'$.fill_id')) AS count FROM records
WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='returned_to_stock' AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-12-31';
