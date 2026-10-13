-- 2027 referral records by specialty, coalescing 'specialty' and 'requested_specialty' fields.
WITH r AS MATERIALIZED (SELECT payload FROM records WHERE stream='referrals')
SELECT coalesce(json_extract(payload,'$.specialty'), json_extract(payload,'$.requested_specialty')) AS specialty, count(*) AS referrals
FROM r WHERE coalesce(json_extract(payload,'$.specialty'), json_extract(payload,'$.requested_specialty')) IS NOT NULL
  AND json_extract(payload,'$.event_date') BETWEEN '2027-01-01' AND '2027-12-31' GROUP BY 1 ORDER BY 1;
