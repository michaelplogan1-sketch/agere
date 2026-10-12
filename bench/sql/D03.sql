SELECT COUNT(*) AS count FROM patients WHERE json_type(payload,'$.age_reported') IN ('integer','real') AND json_extract(payload,'$.age_reported')>=65;
