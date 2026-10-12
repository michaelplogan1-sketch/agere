WITH ed AS MATERIALIZED (
 SELECT DISTINCT patient,event FROM records WHERE stream='encounters' AND json_extract(payload,'$.type') IN ('emergency','ED')
  AND date(event)=event AND event<=:as_of
), pc AS MATERIALIZED (
 SELECT DISTINCT patient,event FROM records WHERE stream='encounters' AND json_extract(payload,'$.type')='primary_care'
  AND date(event)=event AND event<=:as_of
) SELECT COUNT(DISTINCT ed.patient) AS count FROM ed JOIN pc ON pc.patient=ed.patient
 WHERE julianday(pc.event)-julianday(ed.event) BETWEEN 1 AND 7;
