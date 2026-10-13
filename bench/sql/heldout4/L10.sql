-- Distinct patients with a dated 2029 potassium (mmol/L or mEq/L) > 5.0.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('labs'))
SELECT COUNT(DISTINCT patient) AS count FROM r
WHERE json_extract(payload,'$.name')='Potassium' AND json_extract(payload,'$.unit') IN ('mmol/L','mEq/L')
  AND json_type(payload,'$.value') IN ('integer','real') AND json_extract(payload,'$.value') > 5.0
  AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND '2029-08-15';
