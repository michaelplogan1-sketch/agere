-- Hemoglobin result records (names 'Hemoglobin' or 'Hgb') with no source event date.
SELECT COUNT(*) AS count FROM records
WHERE stream='labs' AND json_extract(payload,'$.name') IN ('Hemoglobin','Hgb') AND event IS NULL;
