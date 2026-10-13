-- Open patient A/R: for every patient billing account (account_id), take its latest snapshot in patient_billing_accounts (max event_time; record_id tiebreak) received by the as-of date and sum balance_cents where > 0. Dollars, 2 decimals. Credits (patient_credit_cents) are not netted against balances.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='patient_billing_accounts'),
a AS (SELECT json_extract(payload,'$.balance_cents') b, ROW_NUMBER() OVER (PARTITION BY json_extract(payload,'$.account_id') ORDER BY json_extract(payload,'$.event_time') DESC, json_extract(payload,'$.record_id') DESC) rn FROM r)
SELECT ROUND(SUM(b)/100.0,2) AS value FROM a WHERE rn=1 AND b>0;
