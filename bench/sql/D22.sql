SELECT COUNT(*) AS count FROM records WHERE stream='appointments' AND (json_extract(payload,'$.status')='no_show');
