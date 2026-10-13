-- eGFR below 30 with a source event date 2028-01-01..2029-08-15; names 'eGFR' and 'Glomerular filtration rate', any unit spelling.
SELECT DISTINCT patient AS patient_id FROM records
WHERE stream='labs'
  AND json_extract(payload,'$.name') IN ('eGFR','Glomerular filtration rate')
  AND json_type(payload,'$.value') IN ('integer','real')
  AND json_extract(payload,'$.value') < 30
  AND COALESCE(json_extract(payload,'$.result_status'),'') <> 'cancelled'
  AND event BETWEEN '2028-01-01' AND '2029-08-15'
ORDER BY 1;
