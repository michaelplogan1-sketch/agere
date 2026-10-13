-- Diagnostic: ZIP / postal code. No zip or postal field on roster or streams. Expect 0.
SELECT (SELECT COUNT(*) FROM patients WHERE payload LIKE '%zip%' OR payload LIKE '%postal%' OR payload LIKE '%address%')
     + (SELECT COUNT(*) FROM records WHERE payload LIKE '%zip%' OR payload LIKE '%postal%') AS prerequisite_available;
