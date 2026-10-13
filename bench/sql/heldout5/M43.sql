-- Patients reporting financial strain on EITHER instrument (financial_screens.difficulty_paying_medical_bills = true OR social_screens.difficulty_paying_for_care_reported = true) who currently carry an open patient balance: any account_id whose latest patient_billing_accounts snapshot (max event_time, record_id tiebreak) has balance_cents > 0. Distinct patients.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('social_screens','financial_screens','patient_billing_accounts')),
fs AS (SELECT DISTINCT patient FROM r WHERE (stream='financial_screens' AND json_extract(payload,'$.difficulty_paying_medical_bills')=1) OR (stream='social_screens' AND json_extract(payload,'$.difficulty_paying_for_care_reported')=1)),
acct AS (SELECT patient, json_extract(payload,'$.balance_cents') b, ROW_NUMBER() OVER (PARTITION BY json_extract(payload,'$.account_id') ORDER BY json_extract(payload,'$.event_time') DESC, json_extract(payload,'$.record_id') DESC) rn FROM r WHERE stream='patient_billing_accounts'),
open AS (SELECT DISTINCT patient FROM acct WHERE rn=1 AND b>0)
SELECT COUNT(*) AS count FROM fs WHERE patient IN (SELECT patient FROM open);
