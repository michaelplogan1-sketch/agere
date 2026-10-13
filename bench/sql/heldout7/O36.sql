-- Pediatric vision screening yield: among patients under 18 (roster age_reported) with a COMPLETED distance_visual_acuity screen (preventive_screening), % whose latest completed screen shows either eye 20/40 or worse (Snellen denominator >= 40). 'unable_to_complete' screens excluded.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='preventive_screening'),
s AS (SELECT patient, json_extract(payload,'$.event_time') t, CAST(substr(json_extract(payload,'$.right_eye'),4) AS INTEGER) re, CAST(substr(json_extract(payload,'$.left_eye'),4) AS INTEGER) le
      FROM r WHERE json_extract(payload,'$.screening_type')='distance_visual_acuity' AND json_extract(payload,'$.status')='completed'),
l AS (SELECT patient, re, le, row_number() OVER (PARTITION BY patient ORDER BY t DESC) rn FROM s)
SELECT round(100.0*sum(max(coalesce(re,0),coalesce(le,0))>=40)/count(*),1) AS value FROM l JOIN patients p ON p.id=l.patient WHERE rn=1 AND json_extract(p.payload,'$.age_reported')<18;
