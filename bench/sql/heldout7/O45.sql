-- Diagnostic: race/ethnicity. The roster carries only age_reported and sex_reported; no stream records race, ethnicity, Hispanic/Latino origin. Expect 0.
SELECT (SELECT COUNT(*) FROM records WHERE payload LIKE '%"race%' OR payload LIKE '%ethnic%' OR payload LIKE '%hispanic%' OR payload LIKE '%latino%')
     + (SELECT COUNT(*) FROM patients WHERE payload LIKE '%"race%' OR payload LIKE '%ethnic%') AS prerequisite_available;
