-- Patients reporting difficulty paying for care, by coverage type (roster coverage record). Reported = financial_screens
-- difficulty_paying_medical_bills = true OR social_screens difficulty_paying_for_care_reported = true, on any screen.
WITH d AS (SELECT DISTINCT patient FROM records WHERE
             (stream='financial_screens' AND json_extract(payload,'$.difficulty_paying_medical_bills')=1)
          OR (stream='social_screens' AND json_extract(payload,'$.difficulty_paying_for_care_reported')=1)),
cov AS (SELECT patient, json_extract(payload,'$.coverage_type') coverage_type FROM records WHERE stream='coverage')
SELECT cov.coverage_type, COUNT(*) AS patients FROM d JOIN cov ON cov.patient=d.patient GROUP BY 1 ORDER BY 1;
