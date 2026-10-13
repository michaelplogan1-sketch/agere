-- Final imaging report records by anatomic_site and coded impression (all received reports).
SELECT json_extract(payload,'$.anatomic_site') AS anatomic_site, json_extract(payload,'$.impression') AS impression, COUNT(*) AS reports
FROM records WHERE stream='imaging_reports' AND json_extract(payload,'$.report_status')='final'
GROUP BY 1,2 ORDER BY 1,2;
