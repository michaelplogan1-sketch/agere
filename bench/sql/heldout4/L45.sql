-- Diagnostic: death / vital status (date of death, deceased flag, mortality). No such field exists.
SELECT (SELECT COUNT(*) FROM patients WHERE payload LIKE '%death%' OR payload LIKE '%deceased%' OR payload LIKE '%vital_status%')
     + (SELECT COUNT(*) FROM records WHERE payload LIKE '%death%' OR payload LIKE '%deceased%' OR payload LIKE '%mortality%' OR payload LIKE '%vital_status%' OR payload LIKE '%date_of_death%') AS prerequisite_available;
