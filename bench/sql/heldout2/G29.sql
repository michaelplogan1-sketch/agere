-- imaging_orders records whose accession_number has no imaging_reports record received.
WITH rep AS MATERIALIZED (
  SELECT DISTINCT json_extract(payload,'$.accession_number') AS acc FROM records WHERE stream='imaging_reports'
)
SELECT COUNT(*) AS count FROM records
WHERE stream='imaging_orders' AND json_extract(payload,'$.accession_number') NOT IN (SELECT acc FROM rep WHERE acc IS NOT NULL);
