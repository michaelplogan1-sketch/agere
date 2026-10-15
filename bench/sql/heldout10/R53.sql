SELECT json_extract(payload,'$.contact_result') AS contact_result, COUNT(*) AS contacts FROM records
WHERE stream = 'behavioral_care_contacts' AND json_extract(payload,'$.purpose') = 'positive_mood_screen_followthrough'
GROUP BY 1 ORDER BY 1
