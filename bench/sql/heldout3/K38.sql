-- Diagnostic: sexual orientation / gender identity (SOGI) data. Roster carries only sex_reported; no SOGI field in any stream.
SELECT (SELECT COUNT(*) FROM records WHERE payload LIKE '%sexual_orientation%' OR payload LIKE '%gender_identity%' OR payload LIKE '%pronoun%'
          OR payload LIKE '%transgender%' OR payload LIKE '%nonbinary%' OR payload LIKE '%non-binary%')
     + (SELECT COUNT(*) FROM patients WHERE payload LIKE '%orientation%' OR payload LIKE '%gender_identity%' OR payload LIKE '%pronoun%') AS prerequisite_available;
