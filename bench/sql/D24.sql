SELECT COUNT(DISTINCT patient) AS count FROM records WHERE stream='social_screens' AND (json_type(payload,'$.food_difficulty_reported')='true');
