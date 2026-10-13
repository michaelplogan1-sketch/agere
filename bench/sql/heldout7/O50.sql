-- Diagnostic: DRG grouping. Hospital facility claims exist (service_code 'facility_episode'), but no MS-DRG / diagnosis related group is recorded on any claim or encounter. Expect 0.
SELECT (SELECT COUNT(*) FROM records WHERE payload LIKE '%drg%' OR payload LIKE '%diagnosis related group%' OR payload LIKE '%diagnosis_related%' OR payload LIKE '%diagnosis-related%') AS prerequisite_available;
