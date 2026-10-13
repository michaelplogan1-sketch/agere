-- Billed vs allowed vs payer-paid by service_code for medical claims (claim_id) with service_date 2029-01-01..2029-08-15, using each claim's CURRENT adjudication = latest medical_claims record by event_time (record_id tiebreak). Billed = billed_cents of that record; allowed and paid = its allowed_cents/payer_paid_cents, except a claim whose latest record is 'reversed' (awaiting replacement) counts 0 allowed/paid. Denied claims: billed counts, allowed/paid 0. Dollars, 2 decimals. Claims not yet adjudicated are excluded.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='medical_claims'),
a AS (SELECT json_extract(payload,'$.claim_id') cid, json_extract(payload,'$.service_code') sc, json_extract(payload,'$.service_date') sd, json_extract(payload,'$.status') st,
             json_extract(payload,'$.billed_cents') b, json_extract(payload,'$.allowed_cents') al, json_extract(payload,'$.payer_paid_cents') pd,
             ROW_NUMBER() OVER (PARTITION BY json_extract(payload,'$.claim_id') ORDER BY json_extract(payload,'$.event_time') DESC, json_extract(payload,'$.record_id') DESC) rn
      FROM r WHERE json_extract(payload,'$.claim_id') IS NOT NULL)
SELECT sc AS service_code, COUNT(*) AS claims, ROUND(SUM(b)/100.0,2) AS billed, ROUND(SUM(CASE WHEN st='reversed' THEN 0 ELSE al END)/100.0,2) AS allowed, ROUND(SUM(CASE WHEN st='reversed' THEN 0 ELSE pd END)/100.0,2) AS paid
FROM a WHERE rn=1 AND sd BETWEEN '2029-01-01' AND '2029-08-15' GROUP BY sc ORDER BY sc;
