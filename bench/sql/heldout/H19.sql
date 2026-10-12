SELECT json_extract(payload,'$.specialty') AS specialty,COUNT(*) AS referral_count FROM records
 WHERE stream='referrals' AND json_extract(payload,'$.specialty') IS NOT NULL GROUP BY 1 ORDER BY 1;
