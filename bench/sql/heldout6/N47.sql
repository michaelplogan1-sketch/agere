-- Diagnostic: RVU / relative value units. No RVU field in any stream. Expect 0.
SELECT (SELECT COUNT(*) FROM records WHERE payload LIKE '%rvu%' OR payload LIKE '%relative_value%' OR payload LIKE '%relative value%') AS prerequisite_available;
