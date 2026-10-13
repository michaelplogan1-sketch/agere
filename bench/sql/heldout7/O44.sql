-- Patients who answered yes to difficulty_paying_medical_bills on a financial screen AND have at least one medical_payment_plans record (any date). Distinct patients.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('financial_screens','medical_payment_plans')),
fs AS (SELECT DISTINCT patient FROM r WHERE stream='financial_screens' AND json_extract(payload,'$.difficulty_paying_medical_bills')=1),
pp AS (SELECT DISTINCT patient FROM r WHERE stream='medical_payment_plans')
SELECT COUNT(*) AS count FROM fs JOIN pp USING(patient);
