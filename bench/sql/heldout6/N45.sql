-- Diagnostic: patient experience / satisfaction surveys. No CAHPS, survey or promoter fields anywhere. Expect 0.
SELECT (SELECT COUNT(*) FROM records WHERE payload LIKE '%cahps%' OR payload LIKE '%survey%' OR payload LIKE '%promoter%' OR payload LIKE '%satisfaction%') AS prerequisite_available;
