-- Medical claims (claim_id) with a 2028 service_date whose FIRST payer adjudication (earliest medical_claims record by event_time) was 'denied' and that have since received a 'paid' adjudication (denial overturned / paid on resubmission), as of 2029-08-15. Distinct claim_ids.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='medical_claims'),
a AS (SELECT json_extract(payload,'$.claim_id') cid, json_extract(payload,'$.service_date') sd, json_extract(payload,'$.status') st, json_extract(payload,'$.event_time') t, json_extract(payload,'$.record_id') rid FROM r WHERE json_extract(payload,'$.claim_id') IS NOT NULL),
first AS (SELECT cid, st FROM (SELECT cid, st, ROW_NUMBER() OVER (PARTITION BY cid ORDER BY t, rid) rn FROM a) WHERE rn=1)
SELECT COUNT(*) AS count FROM first WHERE st='denied' AND cid IN (SELECT cid FROM a WHERE sd BETWEEN '2028-01-01' AND '2028-12-31') AND cid IN (SELECT cid FROM a WHERE st='paid');
