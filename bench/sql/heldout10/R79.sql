SELECT COUNT(DISTINCT patient) AS count FROM records
WHERE stream = 'enrollment_spans'
  AND id IN (SELECT supersedes FROM records WHERE stream = 'enrollment_spans' AND supersedes IS NOT NULL)
