WITH appts AS (
 SELECT DISTINCT json_extract(payload,'$.appointment_id') AS appt,json_extract(payload,'$.status') AS status FROM records
 WHERE stream='appointments' AND json_extract(payload,'$.status') IN ('fulfilled','no_show')
 AND json_extract(payload,'$.scheduled_date') BETWEEN '2028-01-01' AND '2028-12-31'
 AND json_extract(payload,'$.appointment_id') IS NOT NULL
) SELECT 100.0*SUM(status='no_show')/COUNT(*) AS value FROM appts;
