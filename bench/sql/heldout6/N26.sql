-- Mean days from service_date to earliest original submission (event_date), claims with 2028 service dates.
WITH r AS MATERIALIZED (SELECT payload FROM records WHERE stream='medical_claim_submissions'),
s AS (SELECT json_extract(payload,'$.claim_id') cid, min(json_extract(payload,'$.service_date')) sd, min(json_extract(payload,'$.event_date')) ed FROM r
      WHERE json_extract(payload,'$.submission_kind')='original' GROUP BY 1)
SELECT round(avg(julianday(ed)-julianday(sd)),2) AS value FROM s WHERE sd BETWEEN '2028-01-01' AND '2028-12-31';
