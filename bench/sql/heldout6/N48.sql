-- Diagnostic: death / mortality. No death date, deceased flag or death disposition anywhere. Expect 0.
SELECT (SELECT COUNT(*) FROM patients WHERE payload LIKE '%death%' OR payload LIKE '%deceased%')
     + (SELECT COUNT(*) FROM records WHERE payload LIKE '%death%' OR payload LIKE '%deceased%' OR payload LIKE '%died%' OR payload LIKE '%mortality%' OR payload LIKE '%passed away%') AS prerequisite_available;
