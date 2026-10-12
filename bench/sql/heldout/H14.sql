WITH pcp AS MATERIALIZED (
 SELECT DISTINCT patient,json_extract(payload,'$.clinician_id') AS pcp FROM records WHERE stream='care_team_relationships'
  AND json_extract(payload,'$.role')='primary_care' AND json_extract(payload,'$.status')='assigned'
  AND json_extract(payload,'$.clinician_id') IS NOT NULL
), visits AS MATERIALIZED (
 SELECT DISTINCT patient,json_extract(payload,'$.encounter_id') AS enc FROM records WHERE stream='encounters'
  AND json_extract(payload,'$.type')='primary_care' AND event BETWEEN '2029-01-01' AND :as_of
  AND json_extract(payload,'$.encounter_id') IS NOT NULL
) SELECT pcp.pcp AS pcp_clinician_id,COUNT(DISTINCT visits.enc) AS visit_count FROM visits JOIN pcp USING(patient)
 GROUP BY 1 ORDER BY 1;
