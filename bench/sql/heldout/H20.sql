SELECT COUNT(*) AS count FROM records WHERE stream='imaging_reports'
 AND json_extract(payload,'$.impression')='distal_radius_fracture';
