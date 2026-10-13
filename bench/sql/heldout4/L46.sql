-- Diagnostic: race / ethnicity. The roster carries only age and sex; no race/ethnicity field in any stream.
SELECT (SELECT COUNT(*) FROM patients WHERE payload LIKE '%"race%' OR payload LIKE '%ethnic%')
     + (SELECT COUNT(*) FROM records WHERE payload LIKE '%"race"%' OR payload LIKE '%ethnicity%' OR payload LIKE '%hispanic%' OR payload LIKE '%latino%' OR payload LIKE '%african american%') AS prerequisite_available;
