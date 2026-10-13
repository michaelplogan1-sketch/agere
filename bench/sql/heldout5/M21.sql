-- Of distinct patients with an Endocrinology referral (referrals.specialty or requested_specialty = 'Endocrinology') dated in 2026, the percent who had an encounter with an endocrinology clinician (attending_clinician_id containing '-endocrinology-') dated on or after their first 2026 endocrinology referral and within 90 days of it. Rounded to 1 decimal.
WITH r AS MATERIALIZED (SELECT patient, stream, payload FROM records WHERE stream IN ('referrals','encounters')),
ref AS (SELECT patient, min(json_extract(payload,'$.event_date')) d FROM r WHERE stream='referrals' AND coalesce(json_extract(payload,'$.specialty'),json_extract(payload,'$.requested_specialty'))='Endocrinology' AND json_extract(payload,'$.event_date') BETWEEN '2026-01-01' AND '2026-12-31' GROUP BY patient),
seen AS (SELECT DISTINCT ref.patient FROM ref JOIN r ON r.patient=ref.patient AND r.stream='encounters' AND json_extract(r.payload,'$.attending_clinician_id') LIKE '%-endocrinology-%'
         AND julianday(json_extract(r.payload,'$.event_date'))-julianday(ref.d) BETWEEN 0 AND 90)
SELECT ROUND(100.0*(SELECT COUNT(*) FROM seen)/(SELECT COUNT(*) FROM ref),1) AS value;
