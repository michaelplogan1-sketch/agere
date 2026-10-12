SELECT COUNT(*) AS count FROM patients WHERE json_extract(payload,'$.sex_reported')='F';
