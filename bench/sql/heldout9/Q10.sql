-- Average payment lag in days for medical claims first paid in 2029: per claim_id, first adjudication row with status 'paid' (earliest event_date); lag = event_date of that row minus the claim service_date; claims whose first paid adjudication date is 2029-01-01..2029-12-31; mean, 2 dp. Denied-then-paid claims use the later paid date.
WITH p AS MATERIALIZED (SELECT json_extract(payload,'$.claim_id') cid, min(json_extract(payload,'$.event_date')) pd, min(json_extract(payload,'$.service_date')) sd FROM records
  WHERE stream='medical_claims' AND json_extract(payload,'$.claim_id') IS NOT NULL AND json_extract(payload,'$.status')='paid' GROUP BY 1)
SELECT ROUND(AVG(julianday(pd)-julianday(sd)),2) AS value FROM p WHERE pd BETWEEN '2029-01-01' AND '2029-12-31';
