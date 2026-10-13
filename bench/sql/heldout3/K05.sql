-- Patients with any BSA-normalized eGFR result below 30 at any time (dated or undated):
-- lab name 'eGFR' (units mL/min/1.73m2 or mL/min/1.73m²) or 'Glomerular filtration rate' in mL/min/{1.73_m2}.
-- 'Glomerular filtration rate' reported in plain mL/min is not BSA-normalized and is excluded.
SELECT DISTINCT patient AS patient_id FROM records
WHERE stream='labs' AND json_extract(payload,'$.value') < 30 AND (
  json_extract(payload,'$.name')='eGFR'
  OR (json_extract(payload,'$.name')='Glomerular filtration rate' AND json_extract(payload,'$.unit')='mL/min/{1.73_m2}'))
ORDER BY 1;
