-- Data quality: potassium results reported in a non-canonical unit: labs rows named 'Potassium' whose unit is not 'mmol/L' (e.g. 'mEq/L', numerically equivalent for potassium), any date or undated.
SELECT COUNT(*) AS count FROM records WHERE stream='labs' AND json_extract(payload,'$.name')='Potassium' AND coalesce(json_extract(payload,'$.unit'),'')<>'mmol/L';
