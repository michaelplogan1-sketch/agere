SELECT COUNT(DISTINCT patient) AS count FROM records WHERE stream='social_screens' AND (json_type(payload,'$.transport_difficulty_reported')='true');
