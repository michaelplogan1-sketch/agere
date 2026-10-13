-- Per specialty: referrals with receiving org, closed by completed visit at that org within 0-21 days.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('referrals','encounters')),
ref AS (SELECT patient, json_extract(payload,'$.specialty') sp, json_extract(payload,'$.receiving_organization_id') org, json_extract(payload,'$.event_date') d
        FROM r WHERE stream='referrals' AND json_extract(payload,'$.receiving_organization_id') IS NOT NULL),
e AS (SELECT DISTINCT patient, json_extract(payload,'$.organization_id') org, json_extract(payload,'$.event_date') d FROM r
      WHERE stream='encounters' AND json_extract(payload,'$.status')='completed')
SELECT sp AS specialty, COUNT(*) AS referrals,
       SUM(EXISTS(SELECT 1 FROM e WHERE e.patient=ref.patient AND e.org=ref.org AND julianday(e.d)-julianday(ref.d) BETWEEN 0 AND 21)) AS closed_21d,
       round(100.0*SUM(EXISTS(SELECT 1 FROM e WHERE e.patient=ref.patient AND e.org=ref.org AND julianday(e.d)-julianday(ref.d) BETWEEN 0 AND 21))/COUNT(*),1) AS closed_pct
FROM ref GROUP BY sp ORDER BY sp;
