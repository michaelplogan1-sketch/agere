-- Patients with any HbA1c result >= 6.5%, any lab name variant (HbA1c, Hemoglobin A1c, Hemoglobin A1c/Hemoglobin.total), dated or undated.
SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream='labs' AND json_extract(payload,'$.name') IN ('HbA1c','Hemoglobin A1c','Hemoglobin A1c/Hemoglobin.total')
  AND json_extract(payload,'$.value') >= 6.5;
