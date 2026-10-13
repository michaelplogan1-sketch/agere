-- Diagnostic: preferred language / interpreter need. No such field exists.
SELECT (SELECT COUNT(*) FROM patients WHERE payload LIKE '%language%' OR payload LIKE '%interpreter%')
     + (SELECT COUNT(*) FROM records WHERE payload LIKE '%interpreter%' OR payload LIKE '%preferred_language%' OR payload LIKE '%limited_english%' OR payload LIKE '%english%' OR payload LIKE '%spanish%') AS prerequisite_available;
