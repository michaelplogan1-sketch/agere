-- Diagnostic: race / ethnicity. Neither the roster nor any received stream carries a race, ethnicity or Hispanic/Latino field. Expect 0.
SELECT (SELECT COUNT(*) FROM patients WHERE payload LIKE '%"race%' OR payload LIKE '%ethnic%' OR payload LIKE '%hispanic%')
     + (SELECT COUNT(*) FROM records WHERE payload LIKE '%"race%' OR payload LIKE '%"ethnicity%' OR payload LIKE '%hispanic%' OR payload LIKE '%latino%') AS prerequisite_available;
