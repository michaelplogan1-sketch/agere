-- Diagnostic: nurse staffing ratios / workforce turnover. No staffing, FTE, vacancy, headcount or turnover data in any stream. Expect 0.
SELECT (SELECT COUNT(*) FROM records WHERE payload LIKE '%staffing%' OR payload LIKE '%turnover%' OR payload LIKE '%vacancy%' OR payload LIKE '%"fte%' OR payload LIKE '%headcount%') AS prerequisite_available;
