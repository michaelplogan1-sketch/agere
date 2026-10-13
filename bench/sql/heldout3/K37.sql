-- Diagnostic: preferred language / interpreter-need data. The only 'language' hits are pediatric developmental screens (speech development), not preferred language.
SELECT (SELECT COUNT(*) FROM records WHERE payload LIKE '%preferred_language%' OR payload LIKE '%primary_language%' OR payload LIKE '%interpreter%'
          OR payload LIKE '%language_preference%' OR payload LIKE '%limited_english%')
     + (SELECT COUNT(*) FROM patients WHERE payload LIKE '%language%' OR payload LIKE '%interpreter%') AS prerequisite_available;
