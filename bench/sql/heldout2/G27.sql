-- Mean days from source event date to warehouse arrival for final imaging_reports with event dates in 2029.
SELECT ROUND(AVG(julianday(arrival) - julianday(event)), 4) AS value FROM records
WHERE stream='imaging_reports' AND json_extract(payload,'$.report_status')='final'
  AND event BETWEEN '2029-01-01' AND '2029-12-31';
