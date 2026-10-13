-- Diagnostic: clinician productivity in work RVUs. Claims use synthetic_local service codes with dollar amounts only; no RVU / relative value fields exist. Expect 0.
SELECT (SELECT COUNT(*) FROM records WHERE payload LIKE '%rvu%' OR payload LIKE '%relative value%') AS prerequisite_available;
