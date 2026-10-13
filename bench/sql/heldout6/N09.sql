-- Distinct patients with any numeric non-cancelled LDL ('LDL cholesterol' or 'LDL') >= 190, dated or undated.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='labs')
SELECT COUNT(DISTINCT patient) AS count FROM r WHERE json_extract(payload,'$.name') IN ('LDL cholesterol','LDL')
  AND coalesce(json_extract(payload,'$.result_status'),'')<>'cancelled' AND typeof(json_extract(payload,'$.value')) IN ('integer','real')
  AND json_extract(payload,'$.value') >= 190;
