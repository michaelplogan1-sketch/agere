-- Any LDL result >= 190 mg/dL (names 'LDL cholesterol' or 'LDL'), dated or undated, excluding cancelled results.
SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream='labs'
  AND json_extract(payload,'$.name') IN ('LDL cholesterol','LDL')
  AND json_extract(payload,'$.unit')='mg/dL'
  AND json_type(payload,'$.value') IN ('integer','real')
  AND json_extract(payload,'$.value') >= 190
  AND COALESCE(json_extract(payload,'$.result_status'),'') <> 'cancelled';
