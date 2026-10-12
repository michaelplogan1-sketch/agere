WITH studies AS MATERIALIZED (SELECT id FROM records WHERE stream='imaging_studies'),
reports AS MATERIALIZED (
 SELECT DISTINCT json_extract(payload,'$.study_record_id') AS study FROM records
 WHERE stream='imaging_reports' AND json_extract(payload,'$.study_record_id') IS NOT NULL
) SELECT COUNT(*) AS count FROM studies WHERE id NOT IN (SELECT study FROM reports);
