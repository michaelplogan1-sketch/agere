-- Patients with a potassium result > 5.0 (mmol/L and mEq/L are numerically equivalent) collected (event_date) from 2029-01-01 through the as-of date (2029-06-30).
-- Undated potassium results are excluded.
SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream='labs' AND json_extract(payload,'$.name')='Potassium' AND json_extract(payload,'$.unit') IN ('mmol/L','mEq/L')
  AND json_extract(payload,'$.value') > 5.0
  AND json_extract(payload,'$.event_date') BETWEEN '2029-01-01' AND :as_of;
