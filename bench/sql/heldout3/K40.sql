-- Diagnostic: a causal effect estimate needs a randomized/assigned comparison (treatment arm, control group, enrollment assignment) or a
-- validated counterfactual design; no randomization, study-arm, or program-enrollment assignment field exists in any stream.
SELECT (SELECT COUNT(*) FROM records WHERE payload LIKE '%randomiz%' OR payload LIKE '%control_group%' OR payload LIKE '%treatment_arm%'
          OR payload LIKE '%study_arm%' OR payload LIKE '%propensity%' OR payload LIKE '%counterfactual%')
     + (SELECT COUNT(*) FROM records WHERE stream IN ('program_enrollments','care_management_enrollment','trial_assignments')) AS prerequisite_available;
