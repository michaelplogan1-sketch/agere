-- Diagnostic: preferred language / interpreter need. No preferred-language, primary-language or interpreter-needed field exists in the roster or any stream. Expect 0.
SELECT (SELECT COUNT(*) FROM patients WHERE payload LIKE '%language%' OR payload LIKE '%interpreter%')
     + (SELECT COUNT(*) FROM records WHERE payload LIKE '%"preferred_language%' OR payload LIKE '%"primary_language%' OR payload LIKE '%"language":%' OR payload LIKE '%interpreter_need%' OR payload LIKE '%"interpreter":%' OR payload LIKE '%limited english%') AS prerequisite_available;
