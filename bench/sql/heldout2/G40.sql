-- Diagnostic: any disclosed household income amount in social or financial screens.
SELECT COUNT(*) AS prerequisite_available FROM records
WHERE (stream='social_screens' AND json_extract(payload,'$.income_amount') IS NOT NULL AND json_extract(payload,'$.income_amount') <> 'not_disclosed')
   OR (stream='financial_screens' AND json_extract(payload,'$.income_exact') IS NOT NULL)
   OR stream IN ('household_income','income_verifications');
