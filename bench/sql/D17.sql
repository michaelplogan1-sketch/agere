SELECT COUNT(*) AS count FROM records WHERE stream='encounters' AND (json_extract(payload,'$.status')='completed');
