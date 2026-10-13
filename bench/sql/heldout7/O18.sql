-- Of distinct ED encounters (type 'emergency') in 2028-01-01..2029-08-08 whose completed disposition was a discharge home (home, home_with_followup, home_with_guardian), % with a primary_care/office/pediatric_office/telephone encounter at a WH-PRACTICE-* org dated 1-7 days after the ED visit date.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='encounters'),
ed AS (SELECT json_extract(payload,'$.encounter_id') eid, patient, min(json_extract(payload,'$.event_date')) d,
              max(CASE WHEN json_extract(payload,'$.disposition') IN ('home','home_with_followup','home_with_guardian') THEN 1 ELSE 0 END) home
       FROM r WHERE json_extract(payload,'$.type')='emergency' GROUP BY 1,2),
pc AS (SELECT DISTINCT patient, json_extract(payload,'$.event_date') d FROM r WHERE json_extract(payload,'$.organization_id') LIKE 'WH-PRACTICE-%'
         AND json_extract(payload,'$.type') IN ('primary_care','office','pediatric_office','telephone')),
x AS (SELECT ed.eid, max(pc.patient IS NOT NULL) f FROM ed LEFT JOIN pc ON pc.patient=ed.patient AND julianday(pc.d)-julianday(ed.d) BETWEEN 1 AND 7
      WHERE ed.home=1 AND ed.d BETWEEN '2028-01-01' AND '2029-08-08' GROUP BY 1)
SELECT round(100.0*sum(f)/count(*),1) AS value FROM x;
