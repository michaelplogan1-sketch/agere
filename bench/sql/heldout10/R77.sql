SELECT COALESCE(SUM(c - 1), 0) AS count FROM (
  SELECT COUNT(*) AS c FROM (SELECT * FROM records WHERE stream='labs' AND id NOT IN (SELECT supersedes FROM records WHERE stream='labs' AND supersedes IS NOT NULL))
  WHERE json_extract(payload,'$.event_time') IS NOT NULL
  GROUP BY patient, lower(trim(json_extract(payload,'$.name'))), json_extract(payload,'$.event_time'), CAST(json_extract(payload,'$.value') AS TEXT)
  HAVING COUNT(*) > 1
)
