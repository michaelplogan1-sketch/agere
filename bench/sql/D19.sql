SELECT COUNT(*) AS count FROM records WHERE stream='pharmacy_dispenses' AND (json_extract(payload,'$.status')='dispensed');
