SELECT stream, COUNT(*) AS corrections FROM records WHERE supersedes IS NOT NULL GROUP BY stream ORDER BY stream
