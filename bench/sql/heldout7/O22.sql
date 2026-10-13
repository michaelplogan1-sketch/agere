-- Diagnostic test sessions (diagnostic_test_sessions, status performed, distinct record) by test_kind for all dates up to as-of; columns test_kind, sessions, patients, limited_quality (test_quality='limited').
WITH r AS MATERIALIZED (SELECT patient, payload FROM records WHERE stream='diagnostic_test_sessions')
SELECT json_extract(payload,'$.test_kind') AS test_kind, count(DISTINCT json_extract(payload,'$.record_id')) AS sessions, count(DISTINCT patient) AS patients,
       count(DISTINCT CASE WHEN json_extract(payload,'$.test_quality')='limited' THEN json_extract(payload,'$.record_id') END) AS limited_quality
FROM r WHERE json_extract(payload,'$.status')='performed' GROUP BY 1 ORDER BY 1;
