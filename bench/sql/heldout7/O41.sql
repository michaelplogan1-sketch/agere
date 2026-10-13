-- Roster patients with no DATED blood pressure reading: no vitals record carrying blood_pressure_systolic with an event_date. The undated intake 'bp_systolic'/'bp_diastolic' measurement rows (one per patient) do not count as dated readings.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='vitals'),
d AS (SELECT DISTINCT patient FROM r WHERE json_extract(payload,'$.blood_pressure_systolic') IS NOT NULL AND json_extract(payload,'$.event_date') IS NOT NULL)
SELECT COUNT(*) AS count FROM patients WHERE id NOT IN (SELECT patient FROM d);
