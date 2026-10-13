-- pharmacy_dispenses records with status 'dispensed' for any ferrous sulfate product, event dated in 2028.
SELECT COUNT(*) AS count FROM records
WHERE stream='pharmacy_dispenses' AND json_extract(payload,'$.status')='dispensed'
  AND lower(json_extract(payload,'$.medication_name')) LIKE '%ferrous sulfate%'
  AND event BETWEEN '2028-01-01' AND '2028-12-31';
