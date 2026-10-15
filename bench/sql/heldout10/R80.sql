SELECT json_extract(payload,'$.event_date_precision') AS precision, COUNT(*) AS records FROM (SELECT * FROM records WHERE stream='conditions' AND id NOT IN (SELECT supersedes FROM records WHERE stream='conditions' AND supersedes IS NOT NULL))
WHERE json_extract(payload,'$.event_date_precision') IN ('month', 'year', 'unknown')
GROUP BY 1 ORDER BY 1
