-- Distinct patients with any LDL ('LDL cholesterol' or 'LDL') >= 190 mg/dL, dated or undated.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('labs'))
SELECT COUNT(DISTINCT patient) AS count FROM r
WHERE json_extract(payload,'$.name') IN ('LDL cholesterol','LDL') AND json_type(payload,'$.value') IN ('integer','real')
  AND json_extract(payload,'$.value') >= 190;
