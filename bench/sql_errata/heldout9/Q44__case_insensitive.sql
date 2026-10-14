-- Potassium results in a unit other than mmol/L, lab name matched case-insensitively ('Potassium', population 'POTASSIUM').
SELECT COUNT(*) AS count FROM records WHERE stream='labs' AND lower(json_extract(payload,'$.name'))='potassium' AND coalesce(json_extract(payload,'$.unit'),'')<>'mmol/L';
