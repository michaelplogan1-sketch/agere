WITH discharges AS MATERIALIZED (
 SELECT id,patient,event,json_extract(payload,'$.encounter_id') AS encounter FROM records
 WHERE stream='adt' AND json_extract(payload,'$.event')='discharge' AND date(event)=event
 AND event<=:as_of
), admissions AS MATERIALIZED (
 SELECT id,patient,event,json_extract(payload,'$.encounter_id') AS encounter FROM records
 WHERE stream='adt' AND json_extract(payload,'$.event')='admission' AND date(event)=event AND event<=:as_of
) SELECT COUNT(DISTINCT d.patient) AS count FROM discharges d JOIN admissions a ON a.patient=d.patient AND a.encounter<>d.encounter WHERE julianday(a.event)-julianday(d.event) BETWEEN 1 AND 30;
