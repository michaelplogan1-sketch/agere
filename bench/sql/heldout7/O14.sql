-- Share (%) of distinct ED encounters (type 'emergency') with visit date in 2029-01-01..2029-08-15 whose completed disposition is admission or observation (admitted, inpatient_admission, hospital_observation, observation, observation_for_mobility). Denominator = all such ED encounters incl. those with no completed row yet.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
e AS (SELECT json_extract(payload,'$.encounter_id') eid, min(json_extract(payload,'$.event_date')) d,
             max(CASE WHEN json_extract(payload,'$.disposition') IN ('admitted','inpatient_admission','hospital_observation','observation','observation_for_mobility') THEN 1 ELSE 0 END) adm
      FROM r WHERE json_extract(payload,'$.type')='emergency' GROUP BY 1)
SELECT round(100.0*sum(adm)/count(*),1) AS value FROM e WHERE d BETWEEN '2029-01-01' AND '2029-08-15';
