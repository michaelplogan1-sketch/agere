-- Patients with any LDL cholesterol result >= 190 mg/dL (names 'LDL cholesterol' or 'LDL', all mg/dL), dated or undated.
SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream='labs' AND json_extract(payload,'$.name') IN ('LDL cholesterol','LDL')
  AND json_extract(payload,'$.value') >= 190;
