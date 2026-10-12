SELECT COUNT(DISTINCT patient) AS count FROM records WHERE stream='coverage' AND (json_extract(payload,'$.coverage_type')='commercial' AND json_extract(payload,'$.status')='active');
