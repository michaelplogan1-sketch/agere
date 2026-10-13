-- Ferritin < 30 ng/mL on any received result.
SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream='labs' AND lower(json_extract(payload,'$.name'))='ferritin'
  AND json_type(payload,'$.value') IN ('integer','real') AND json_extract(payload,'$.value') < 30
  AND COALESCE(json_extract(payload,'$.result_status'),'') <> 'cancelled';
