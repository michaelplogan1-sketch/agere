-- Diagnostic: household/personal income values. financial_screens.income_exact is always null and social_screens.income_amount is always
-- 'not_disclosed'; no other stream or the roster carries an income amount or bracket.
SELECT (SELECT COUNT(*) FROM records WHERE stream='financial_screens' AND json_extract(payload,'$.income_exact') IS NOT NULL)
     + (SELECT COUNT(*) FROM records WHERE stream='social_screens' AND json_extract(payload,'$.income_amount') IS NOT NULL
          AND json_extract(payload,'$.income_amount') <> 'not_disclosed')
     + (SELECT COUNT(*) FROM records WHERE payload LIKE '%household_income%' OR payload LIKE '%annual_income%' OR payload LIKE '%income_bracket%' OR payload LIKE '%federal_poverty%')
     + (SELECT COUNT(*) FROM patients WHERE payload LIKE '%income%') AS prerequisite_available;
