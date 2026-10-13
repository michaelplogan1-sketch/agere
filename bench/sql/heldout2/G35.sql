-- Patients reporting cost difficulty (financial_screens difficulty_paying_medical_bills=true OR social_screens difficulty_paying_for_care_reported=true), by registration coverage_type.
WITH fin AS MATERIALIZED (
  SELECT DISTINCT patient FROM records WHERE
    (stream='financial_screens' AND json_extract(payload,'$.difficulty_paying_medical_bills')=1)
    OR (stream='social_screens' AND json_extract(payload,'$.difficulty_paying_for_care_reported')=1)
)
SELECT json_extract(c.payload,'$.coverage_type') AS coverage_type, COUNT(DISTINCT c.patient) AS patients
FROM records c WHERE c.stream='coverage' AND c.patient IN (SELECT patient FROM fin)
GROUP BY 1 ORDER BY 1;
