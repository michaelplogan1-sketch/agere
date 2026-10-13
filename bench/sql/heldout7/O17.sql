-- Distinct encounters at specialty organizations (organization_id WH-SPECIALTY-*; any encounter type) with visit date 2028-08-16..2029-08-15, by specialty parsed from attending_clinician_id 'WH-CLIN-<site>-<specialty>-<k>'; columns specialty, visits, patients.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
e AS (SELECT json_extract(payload,'$.encounter_id') eid, patient, min(json_extract(payload,'$.event_date')) d, max(json_extract(payload,'$.attending_clinician_id')) c
      FROM r WHERE json_extract(payload,'$.organization_id') LIKE 'WH-SPECIALTY-%' GROUP BY 1,2),
s AS (SELECT eid, patient, d, substr(substr(c,9), instr(substr(c,9),'-')+1) rest FROM e)
SELECT substr(rest,1,instr(rest,'-')-1) AS specialty, count(*) AS visits, count(DISTINCT patient) AS patients FROM s WHERE d BETWEEN '2028-08-16' AND '2029-08-15' GROUP BY 1 ORDER BY 1;
