-- Diagnostic: household income / federal poverty level. Social screens carry income_amount='not_disclosed' and financial screens income_exact=null on every row; no household size or FPL field exists. Expect 0 usable values.
WITH r AS MATERIALIZED (SELECT payload FROM records WHERE stream IN ('social_screens','financial_screens'))
SELECT (SELECT COUNT(*) FROM r WHERE coalesce(json_extract(payload,'$.income_amount'),'not_disclosed')<>'not_disclosed' OR json_extract(payload,'$.income_exact') IS NOT NULL)
     + (SELECT COUNT(*) FROM records WHERE payload LIKE '%poverty%' OR payload LIKE '%"fpl%' OR payload LIKE '%household_size%' OR payload LIKE '%household_income%')
     + (SELECT COUNT(*) FROM patients WHERE payload LIKE '%income%') AS prerequisite_available;
