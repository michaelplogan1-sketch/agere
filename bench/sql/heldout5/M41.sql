-- Lab test names (exact 'name' string) reported under more than one distinct non-empty unit string (e.g. mmol/L vs mEq/L, K/uL vs 10^3/uL). Columns: test_name, distinct_units. Empty-string units ignored; names are not merged across spellings.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='labs')
SELECT json_extract(payload,'$.name') AS test_name, COUNT(DISTINCT json_extract(payload,'$.unit')) AS distinct_units FROM r WHERE coalesce(json_extract(payload,'$.unit'),'')<>'' GROUP BY 1 HAVING COUNT(DISTINCT json_extract(payload,'$.unit'))>1 ORDER BY 1;
