-- Diagnostic: race/ethnicity fields on the roster profile or demographic updates, or a dedicated stream.
SELECT (SELECT COUNT(*) FROM patients p, json_each(p.payload) j WHERE lower(j.key) LIKE '%race%' OR lower(j.key) LIKE '%ethnic%')
     + (SELECT COUNT(*) FROM records r, json_each(r.payload) j WHERE r.stream='demographic_updates' AND (lower(j.key) LIKE '%race%' OR lower(j.key) LIKE '%ethnic%'))
     + (SELECT COUNT(*) FROM records WHERE stream IN ('race_ethnicity','demographics','patient_demographics')) AS prerequisite_available;
