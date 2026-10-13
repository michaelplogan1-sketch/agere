-- No-show rate for appointments scheduled in 2028: distinct appointment_ids with any no_show record /
-- distinct appointment_ids with a fulfilled or no_show record; percentage.
WITH a AS MATERIALIZED (
  SELECT json_extract(payload,'$.appointment_id') AS appt, json_extract(payload,'$.status') AS st FROM records
  WHERE stream='appointments' AND json_extract(payload,'$.scheduled_date') BETWEEN '2028-01-01' AND '2028-12-31'
)
SELECT ROUND(100.0 * COUNT(DISTINCT CASE WHEN st='no_show' THEN appt END)
       / COUNT(DISTINCT CASE WHEN st IN ('fulfilled','no_show') THEN appt END), 4) AS value FROM a;
