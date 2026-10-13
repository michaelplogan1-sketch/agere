-- Diagnostic: preferred language / interpreter need. No such field on the roster or any stream. Expect 0.
SELECT (SELECT COUNT(*) FROM patients WHERE payload LIKE '%language%' OR payload LIKE '%interpreter%')
     + (SELECT COUNT(*) FROM records WHERE payload LIKE '%preferred_language%' OR payload LIKE '%"language"%' OR payload LIKE '%interpreter%' OR payload LIKE '%spanish%') AS prerequisite_available;
