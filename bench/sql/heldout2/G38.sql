-- Diagnostic: any death/mortality source (dedicated stream, or death date / deceased markers in a payload).
SELECT (SELECT COUNT(*) FROM records WHERE stream IN ('deaths','death_records','mortality','vital_status'))
     + (SELECT COUNT(*) FROM records WHERE payload LIKE '%death_date%' OR payload LIKE '%date_of_death%' OR payload LIKE '%deceased%')
     + (SELECT COUNT(*) FROM patients WHERE payload LIKE '%death%' OR payload LIKE '%deceased%') AS prerequisite_available;
