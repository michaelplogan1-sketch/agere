-- Mean days from claim service_date (2028) to first positive payer cash allocation.
WITH r AS MATERIALIZED (SELECT stream, payload FROM records WHERE stream IN ('payer_cash_allocations','medical_claims')),
s AS (SELECT json_extract(payload,'$.claim_id') cid, min(json_extract(payload,'$.service_date')) sd FROM r WHERE stream='medical_claims' AND json_extract(payload,'$.claim_id') IS NOT NULL GROUP BY 1),
a AS (SELECT json_extract(payload,'$.claim_id') cid, min(json_extract(payload,'$.event_date')) ad FROM r WHERE stream='payer_cash_allocations' AND json_extract(payload,'$.allocated_cents')>0 GROUP BY 1)
SELECT round(avg(julianday(a.ad)-julianday(s.sd)),2) AS value FROM s JOIN a USING(cid) WHERE s.sd BETWEEN '2028-01-01' AND '2028-12-31';
