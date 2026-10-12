WITH discharges AS MATERIALIZED (
 SELECT id,patient,event,json_extract(payload,'$.encounter_id') AS encounter FROM records
 WHERE stream='adt' AND json_extract(payload,'$.event')='discharge' AND date(event)=event
 AND event<=:as_of
), admissions AS MATERIALIZED (
 SELECT id,patient,event,json_extract(payload,'$.encounter_id') AS encounter FROM records
 WHERE stream='adt' AND json_extract(payload,'$.event')='admission' AND date(event)=event AND event<=:as_of
), eligible AS (SELECT * FROM discharges WHERE encounter IS NOT NULL AND event<=date(:as_of,'-30 days')) SELECT 100.0*SUM(CASE WHEN EXISTS(SELECT 1 FROM admissions a WHERE a.patient=d.patient AND a.encounter<>d.encounter AND julianday(a.event)-julianday(d.event) BETWEEN 1 AND 30) THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0) AS value FROM eligible d;
