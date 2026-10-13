-- Lab result rows by sending source_system: total rows, rows with no event_date (collection/result date missing), and percent undated (1 decimal). All received lab rows count, including cancelled and non-numeric ones.
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='labs')
SELECT json_extract(payload,'$.source_system') AS source_system, COUNT(*) AS lab_rows, SUM(json_extract(payload,'$.event_date') IS NULL) AS undated_rows, ROUND(100.0*SUM(json_extract(payload,'$.event_date') IS NULL)/COUNT(*),1) AS pct_undated FROM r GROUP BY 1 ORDER BY 1;
