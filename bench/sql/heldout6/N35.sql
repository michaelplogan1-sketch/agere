-- % of patients reporting unknown or >10y tetanus recall who have a completed Tdap administration on/after the history date.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('immunization_history','immunizations')),
h AS (SELECT patient, min(json_extract(payload,'$.event_date')) hd FROM r WHERE stream='immunization_history'
      AND json_extract(payload,'$.recall') IN ('does_not_recall_tetanus','recalls_tetanus_over_ten_years') GROUP BY 1),
t AS (SELECT patient, json_extract(payload,'$.event_date') td FROM r WHERE stream='immunizations' AND json_extract(payload,'$.vaccine')='Tdap'
      AND json_extract(payload,'$.administration_status')='completed')
SELECT round(100.0*(SELECT count(*) FROM h WHERE EXISTS (SELECT 1 FROM t WHERE t.patient=h.patient AND t.td>=h.hd))/(SELECT count(*) FROM h),1) AS value;
