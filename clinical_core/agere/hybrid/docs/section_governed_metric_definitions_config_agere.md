## Governed metric definitions (config/agere/metrics). Definitions and SQL templates are documentation: '{period_start}'/'{period_end}' are placeholders you fill (default period = the as-of calendar year unless the question states one). Use them when a question asks for that metric; otherwise answer the question as asked.
### Domain clinical_quality
- kidney_health_evaluation_diabetes: Kidney health evaluation for patients with diabetes (eGFR and uACR). Denominator: diabetics aged 18-85. Numerator: at least one dated eGFR and at least one dated urine albumin-creatinine ratio, both with event_date in the measurement year. Percent.
  population: Patients with diabetes (library concept 'diabetes') aged 18-85.
  denominator: Diabetics aged 18-85.
  numerator: Both an eGFR and a uACR dated in the year.
  grain: {"entity": "patient", "key": "patient_id", "table": "patient"}; unit: percent; returns: value
  time anchor: {"default": "as_of_year", "field": "measurement.event_date", "mode": "period"}
  value sets: {"egfr": {"concept": "egfr", "dimension": "measurement.name", "label_patterns": ["egfr|glomerular filtration"], "standard": {"loinc": ["33914-3", "48642-3", "48643-1", "62238-1", "98979-8"]}}, "uacr": {"concept": "uacr", "dimension": "measurement.name", "exclude": ["Urine albumin"], "label_patterns": ["albumin/creatinine|microalbumin|uacr"], "standard": {"loinc": ["9318-7", "14959-1", "32294-1"]}}}
  SQL template: SELECT 100.0 * count(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM measurement m WHERE m.patient_id = p.patient_id AND m.concept = 'egfr' AND m.value_canonical IS NOT NULL AND m.event_date BETWEEN '{period_start}' AND '{period_end}') AND EXISTS (SELECT 1 FROM measurement m WHERE m.patient_id = p.patient_id AND m.concept = 'uacr' AND m.value_canonical IS NOT NULL AND m.event_date BETWEEN '{period_start}' AND '{period_end}') THEN p.patient_id END) / count(DISTINCT p.patient_id) AS pct_kidney_health_evaluation FROM patient p JOIN concept_patient cp ON cp.patient_id = p.patient_id AND cp.concept = 'diabetes' WHERE p.age_latest BETWEEN 18 AND 85
- respiratory_registry: Respiratory registry (asthma or COPD). Distinct patients with an asthma or COPD condition record (COPD includes emphysema and chronic obstructive bronchitis). All ages. Patient list.
  population: All roster patients.
  denominator: None (patient list).
  numerator: Patients with condition concept asthma or copd.
  grain: {"entity": "patient", "key": "patient_id", "table": "patient"}; unit: patients; returns: patient_set
  time anchor: {"default": "all", "field": "condition.event_date", "mode": "any_time"}
  value sets: {"respiratory": {"dimension": "condition.label", "label_patterns": ["asthma|copd|chronic obstructive|emphysema"], "standard": {"icd10_prefixes": ["J43", "J44", "J45"], "snomed": ["195967001", "233678006", "87433001", "185086009"]}}}
  SQL template: SELECT DISTINCT patient_id FROM concept_patient WHERE concept IN ('asthma','copd') ORDER BY 1
- ldl_190_ever: Patients with any LDL cholesterol >= 190 mg/dL (possible familial hypercholesterolemia). Distinct patients with any numeric, non-cancelled LDL cholesterol result >= 190 mg/dL, dated or undated (timing does not matter for an FH screen).
  population: All roster patients.
  denominator: None (patients).
  numerator: Any LDL value_canonical >= 190.
  grain: {"entity": "patient", "key": "patient_id", "table": "patient"}; unit: patients; returns: patient_set
  time anchor: {"default": "all", "field": "measurement.event_date", "mode": "any_time"}
  value sets: {"ldl": {"concept": "ldl", "dimension": "measurement.name", "exclude": ["Non-HDL cholesterol", "Total cholesterol", "Cholesterol"], "label_patterns": ["ldl"], "members": ["LDL cholesterol", "LDL"], "standard": {"loinc": ["13457-7", "18262-6", "2089-1"]}}}
  SQL template: SELECT DISTINCT patient_id FROM measurement WHERE concept = 'ldl' AND value_canonical >= 190 ORDER BY 1
- beers_high_risk_medications_65: Older adults dispensed a Beers-list high-risk medication. Patients aged 65+ with at least one picked-up fill (medication.picked_up = 1: dispensed, pharmacy claim or payer pharmacy claim; prepared, returned-to-stock and reversed fills excluded) of a first-generation antihistamine, benzodiazepine, meperidine or skeletal muscle relaxant, at any date. Second-generation antihistamines (loratadine, cetirizine, fexofenadine) are not Beers-list drugs.
  population: Patients aged 65 or older.
  denominator: None (patients).
  numerator: Patients with a dispensed Beers-list drug.
  grain: {"entity": "patient", "key": "patient_id", "table": "patient"}; unit: patients; returns: count
  time anchor: {"default": "all", "field": "medication.event_date", "mode": "any_time"}
  value sets: {"beers_drugs": {"dimension": "medication.ingredient", "exclude": ["loratadine", "cetirizine", "fexofenadine", "levocetirizine", "desloratadine"], "standard": {"drug_classes": ["first_generation_antihistamine", "benzodiazepine", "skeletal_muscle_relaxant"], "ingredients": ["diphenhydramine", "chlorpheniramine", "doxylamine", "hydroxyzine", "promethazine", "diazepam", "lorazepam", "clonazepam", "alprazolam", "temazepam", "meperidine", "cyclobenzaprine", "methocarbamol", "carisoprodol"]}}}
  SQL template: SELECT count(DISTINCT p.patient_id) AS patients FROM patient p JOIN medication m ON m.patient_id = p.patient_id WHERE p.age_latest >= 65 AND m.picked_up = 1 AND (m.drug_class IN ('first_generation_antihistamine','benzodiazepine','skeletal_muscle_relaxant') OR lower(m.drug_name) LIKE '%meperidine%')
- hf_no_beta_blocker: Heart failure patients with no beta-blocker on record. Patients with heart failure (library concept) with no beta-blocker (drug_class beta_blocker, any evidence: dispensed, claim, ordered, listed, administered, reported) anywhere in their medication records.
  population: Heart failure patients.
  denominator: None (patient list).
  numerator: HF patients with no beta_blocker medication row.
  grain: {"entity": "patient", "key": "patient_id", "table": "patient"}; unit: patients; returns: patient_set
  time anchor: {"default": "all", "field": "medication.event_date", "mode": "any_time"}
  value sets: {"beta_blocker": {"dimension": "medication.ingredient", "exclude": ["albuterol", "salmeterol", "formoterol", "levalbuterol"], "standard": {"drug_classes": ["beta_blocker", "hf_evidence_based_beta_blocker"]}}}
  SQL template: SELECT DISTINCT cp.patient_id FROM concept_patient cp WHERE cp.concept = 'heart_failure' AND NOT EXISTS (SELECT 1 FROM medication m WHERE m.patient_id = cp.patient_id AND m.drug_class = 'beta_blocker') ORDER BY 1
- adult_obesity_computed_bmi: Adults with BMI >= 30 computed from latest weight and height. Adults (18+) whose BMI = weight kg / (height m)^2 is >= 30, with height from the vitals height measurement (an undated baseline) and weight = the latest dated weight, else the undated baseline weight. The obesity diagnosis is not used.
  population: Adults 18+.
  denominator: None (count).
  numerator: Adults with computed BMI >= 30.
  grain: {"entity": "patient", "key": "patient_id", "table": "patient"}; unit: patients; returns: count
  time anchor: {"default": "as_of", "field": "measurement.event_date", "mode": "snapshot"}
  SQL template: WITH h AS (SELECT patient_id, max(value) / 100.0 AS m FROM measurement WHERE source_table = 'vitals' AND name = 'height_cm' AND value > 0 GROUP BY patient_id), w AS (SELECT p.patient_id, coalesce((SELECT value_canonical FROM measurement_latest l WHERE l.patient_id = p.patient_id AND l.concept = 'weight'), (SELECT max(value_canonical) FROM measurement m WHERE m.patient_id = p.patient_id AND m.concept = 'weight' AND m.event_date IS NULL)) AS kg FROM patient p WHERE p.age_latest >= 18) SELECT count(*) AS adults_bmi_30_plus FROM w JOIN h ON h.patient_id = w.patient_id WHERE w.kg / (h.m * h.m) >= 30
### Domain families
- patient_cohort: Patients matching a governed population. Distinct roster patients whose records satisfy every population atom: conditions from the concept library (concept_patient: ICD-10-CM, SNOMED CT and adopted labels), lab results by concept (canonical units; "latest" = the lowest value on the most recent dated result, cancelled results excluded), latest paired blood pressure, age/sex/coverage from the roster, medications by governed drug group (picked up = pharmacy dispense with status dispensed; any_record = list, orders, dispenses, pharmacy claims or history), visits by governed visit class, positive social-needs screens, appointment outcomes. Returns the count, the list, a percent with numerator atoms, or a table by roster group.
  population: Roster patients (synthetic); the population slot narrows it.
  denominator: With returns=percent: the patients matching the population atoms.
  numerator: With returns=percent: those who also match the numerator atoms.
  grain: {"entity": "patient", "key": "patient_id"}; unit: patients or percent; returns: count
  time anchor: {"default": "all", "field": "patient", "mode": "period"}
- encounter_volume: Encounter volume by governed visit class. Distinct encounters (encounter versions de-duplicated by encounter_id) whose received type belongs to the chosen visit classes, dated (event_date) in the period; optionally distinct patients, or visits per 1,000 roster patients (the whole roster of each group is the denominator, visit or not). Grouped by month, quarter, half-year, year, facility or a roster attribute.
  population: Roster patients (synthetic); the population slot narrows it.
  denominator: None (count) unless the slots ask for a rate.
  numerator: Distinct encounters (encounter versions de-duplicated by encounter_id) whose received type belongs to the chosen visit classes, dated (event_date) in the period; optionally distinct patients, or visits per 1,000 roster patients (the whole roster of each group is the denominator, visit or not). Grouped by month, quarter, half-year, year, facility or a roster attribute.
  grain: {"entity": "visit", "key": "encounter_id"}; unit: visits; returns: count
  time anchor: {"default": "all", "field": "visit.event_date", "mode": "period"}
- visit_followup: Follow-up after an index visit. Index encounters of the chosen classes dated in the period; an index visit is followed up when the same patient has a different encounter of the follow-up classes dated days_min..days_max days after the index date (visit date, or discharge date for stays). Returns the number or the percent of index visits followed up.
  population: Roster patients (synthetic); the population slot narrows it.
  denominator: None (count) unless the slots ask for a rate.
  numerator: Index encounters of the chosen classes dated in the period; an index visit is followed up when the same patient has a different encounter of the follow-up classes dated days_min..days_max days after the index date (visit date, or discharge date for stays). Returns the number or the percent of index visits followed up.
  grain: {"entity": "visit", "key": "encounter_id"}; unit: percent of index visits; returns: value
  time anchor: {"default": "all", "field": "visit.event_date", "mode": "period"}
- length_of_stay: Length of stay. Mean (or median) of end_time - start_time over completed stays of the chosen classes, in days or hours; the period applies to the admission or the discharge date.
  population: Roster patients (synthetic); the population slot narrows it.
  denominator: None (count) unless the slots ask for a rate.
  numerator: Mean (or median) of end_time - start_time over completed stays of the chosen classes, in days or hours; the period applies to the admission or the discharge date.
  grain: {"entity": "stay", "key": "encounter_id"}; unit: days; returns: value
  time anchor: {"default": "all", "field": "visit.start_time", "mode": "period"}
- medical_claims: Medical claim outcomes. One row per medical claim (adjudication versions settled in the claim table): first-pass status, ever denied, denied then paid, net payer paid (every adjudication delta, reversals net out), billed/allowed, and days from service to first submission, first adjudication and first payer cash. The period applies to the claim clock the question names: service date (default), first adjudication date, first paid adjudication date, first payer cash date, reversal event date, or the benefit year; days to first paid adjudication are available too.
  population: Roster patients (synthetic); the population slot narrows it.
  denominator: None (count) unless the slots ask for a rate.
  numerator: One row per medical claim (adjudication versions settled in the claim table): first-pass status, ever denied, denied then paid, net payer paid (every adjudication delta, reversals net out), billed/allowed, and days from service to first submission, first adjudication and first payer cash. The period applies to the service date, or the benefit year.
  grain: {"entity": "claim", "key": "claim_id"}; unit: claims, percent, dollars or days; returns: value
  time anchor: {"default": "all", "field": "claim.service_date", "mode": "period"}
- feed_quality: Received feed quality. Per received stream: rows, rows with no event date, percent undated, mean arrival lag (available_date - event_date), rows arriving more than N days after the event, correction records (records that supersede an earlier record of the same stream, payload supersedes_record_id; stream all + group_by stream = corrections per feed) and current records with an imprecise event date (event_date_precision month, year or unknown; group_by date_precision breaks them down); encounters can be counted once per encounter_id (first arrival, earliest date).
  population: Roster patients (synthetic); the population slot narrows it.
  denominator: None (count) unless the slots ask for a rate.
  numerator: Per received stream: rows, rows with no event date, percent undated, mean arrival lag (available_date - event_date), rows arriving more than N days after the event, correction records (records that supersede an earlier record of the same stream, payload supersedes_record_id; stream all + group_by stream = corrections per feed) and current records with an imprecise event date (event_date_precision month, year or unknown; group_by date_precision breaks them down); encounters can be counted once per encounter_id (first arrival, earliest date).
  grain: {"entity": "lab_row", "key": "record_id"}; unit: rows, percent or days; returns: value
  time anchor: {"default": "all", "field": "record.event_date", "mode": "period"}
- patient_payments: Patient payments received. Patient payments received (patient_payments records), dated by the payment event date; dollars or count.
  population: Roster patients (synthetic); the population slot narrows it.
  denominator: None (count) unless the slots ask for a rate.
  numerator: Patient payments received (patient_payments records), dated by the payment event date; dollars or count.
  grain: {"entity": "account", "key": "record_id"}; unit: dollars; returns: value
  time anchor: {"default": "all", "field": "patient_payments.event_date", "mode": "period"}
- patient_refunds: Patient refunds issued. Refunds issued to patients (patient_refunds, status issued), dated by the refund event date.
  population: Roster patients (synthetic); the population slot narrows it.
  denominator: None (count) unless the slots ask for a rate.
  numerator: Refunds issued to patients (patient_refunds, status issued), dated by the refund event date.
  grain: {"entity": "account", "key": "record_id"}; unit: dollars; returns: value
  time anchor: {"default": "all", "field": "patient_refunds.event_date", "mode": "period"}
- pharmacy_returns_to_stock: Prepared fills returned to stock. Prescriptions prepared at the pharmacy but never picked up (returned to stock), from every source (returned_fill): pharmacy_dispenses status returned_to_stock (one per fill_id) and payer pharmacy claim return-to-stock reversals (one per rx_claim_id); distinct fills, dated by the return.
  population: Roster patients (synthetic); the population slot narrows it.
  denominator: None (count) unless the slots ask for a rate.
  numerator: Prescriptions prepared at the pharmacy but never picked up (returned to stock), from every source (returned_fill): pharmacy_dispenses status returned_to_stock (one per fill_id) and payer pharmacy claim return-to-stock reversals (one per rx_claim_id); distinct fills, dated by the return.
  grain: {"entity": "account", "key": "record_id"}; unit: count; returns: value
  time anchor: {"default": "all", "field": "returned_fill.event_date", "mode": "period"}
- clearinghouse_member_id_rejections: Clearinghouse member-ID rejections. Distinct claims with a clearinghouse acknowledgement rejecting the member identifier, dated by the acknowledgement.
  population: Roster patients (synthetic); the population slot narrows it.
  denominator: None (count) unless the slots ask for a rate.
  numerator: Distinct claims with a clearinghouse acknowledgement rejecting the member identifier, dated by the acknowledgement.
  grain: {"entity": "account", "key": "record_id"}; unit: count; returns: value
  time anchor: {"default": "all", "field": "claim_acknowledgements.event_date", "mode": "period"}
- pharmacy_claim_net_paid: Net pharmacy payer payments. Net payer payments on pharmacy claims from every source (pharmacy_claim_event: our pharmacy's claim transactions plus payer pharmacy claims; payer copies of our dispensed fills carry no plan payment): payer_paid_delta_cents over every transaction dated in the period (paid positive, reversals and recoupments negative, rejections zero), dollars. Rejections count distinct claims by NCPDP reject reason (79 = REFILL_TOO_SOON).
  population: Roster patients (synthetic); the population slot narrows it.
  denominator: None (count) unless the slots ask for a rate.
  numerator: Net payer payments on pharmacy claims from every source (pharmacy_claim_event: our pharmacy's claim transactions plus payer pharmacy claims; payer copies of our dispensed fills carry no plan payment): payer_paid_delta_cents over every transaction dated in the period (paid positive, reversals and recoupments negative, rejections zero), dollars. Rejections count distinct claims by NCPDP reject reason (79 = REFILL_TOO_SOON).
  grain: {"entity": "account", "key": "record_id"}; unit: dollars; returns: value
  time anchor: {"default": "all", "field": "pharmacy_claim_event.event_date", "mode": "period"}
- payer_cash_posted: Payer cash posted. Payer cash allocations posted to claims (negatives are recoupments), dated by the allocation; net dollars.
  population: Roster patients (synthetic); the population slot narrows it.
  denominator: None (count) unless the slots ask for a rate.
  numerator: Payer cash allocations posted to claims (negatives are recoupments), dated by the allocation; net dollars.
  grain: {"entity": "account", "key": "record_id"}; unit: dollars; returns: value
  time anchor: {"default": "all", "field": "payer_cash_allocations.event_date", "mode": "period"}
- specialty_referrals_sent: Referrals sent. Referral orders sent (referrals stream), dated by the referral; by referral type.
  population: Roster patients (synthetic); the population slot narrows it.
  denominator: None (count) unless the slots ask for a rate.
  numerator: Referral orders sent (referrals stream), dated by the referral; by referral type.
  grain: {"entity": "account", "key": "record_id"}; unit: count; returns: value
  time anchor: {"default": "all", "field": "referrals.event_date", "mode": "period"}
- original_claims_missing_member_id: Original claims submitted without a member ID. Distinct claims whose original submission carried no member identifier.
  population: Roster patients (synthetic); the population slot narrows it.
  denominator: None (count) unless the slots ask for a rate.
  numerator: Distinct claims whose original submission carried no member identifier.
  grain: {"entity": "account", "key": "record_id"}; unit: count; returns: value
  time anchor: {"default": "all", "field": "medical_claim_submissions.event_date", "mode": "period"}
### Domain operations
- no_show_rate: Appointment no-show rate. No-shows (a no_show version and never kept) / (kept + no-shows) among distinct appointments scheduled in the period; cancelled and rescheduled appointments are outside the denominator. Statuses resolved per appointment_id across all versions.
  population: Appointments scheduled in the period.
  denominator: Distinct appointment_ids that were kept (arrived/fulfilled) or no-show.
  numerator: Denominator appointments with a no_show version and never kept (a no-show later arrived/attended is kept, v2).
  grain: {"entity": "appointment", "key": "appointment_id", "table": "appointment"}; unit: percent; returns: value
  time anchor: {"default": "as_of_year", "field": "appointment.scheduled_date", "forbid_fields": ["booked_date"], "mode": "period"}
  variants: aamc_vizient: (no-shows + day-of/day-before cancellations) / (that + completed).
  SQL template: SELECT 100.0 * sum(ever_no_show = 1 AND kept = 0) / count(*) AS no_show_rate FROM appointment WHERE scheduled_date BETWEEN '{period_start}' AND '{period_end}' AND (kept = 1 OR ever_no_show = 1)
- stale_booked_appointments: Stale booked appointments. Distinct appointment_ids whose only received status in any version is 'booked' and whose scheduled_date is before the as-of date (statuses can arrive out of order, so the latest-arrival status is not used). Appointments without a scheduled date are excluded.
  population: Received appointments.
  denominator: None (count).
  numerator: appointment_ids with no non-booked version and scheduled_date < as-of.
  grain: {"entity": "appointment", "key": "appointment_id", "table": "record"}; unit: appointments; returns: count
  time anchor: {"default": "all", "field": "appointment.scheduled_date", "mode": "snapshot"}
  SQL template: SELECT count(*) AS stale_booked FROM (SELECT json_extract(payload,'$.appointment_id') AS appointment_id, max(json_extract(payload,'$.scheduled_date')) AS scheduled_date, sum(json_extract(payload,'$.status') <> 'booked') AS other_statuses FROM record WHERE stream = 'appointments' GROUP BY 1) WHERE other_statuses = 0 AND scheduled_date < '{as_of}'
- community_referral_delivery: Community service referrals sent and delivered, by service. By service requested: community_service_referrals records (by record_id) and how many have ANY community_service_outcomes record (linked by referral_record_id) with service_delivered = true. Scheduled, waitlisted, declined and unable-to-reach outcomes are not delivered. Clinic-internal referrals are not community referrals.
  population: Community service referrals received.
  denominator: Referrals by service.
  numerator: Referrals with a delivered outcome.
  grain: {"entity": "referral", "key": "record_id", "table": "record"}; unit: referrals; returns: table
  time anchor: {"default": "all", "field": "record.event_date", "mode": "any_time"}
  SQL template: SELECT json_extract(r.payload,'$.service_requested') AS service, count(*) AS referrals, sum(EXISTS (SELECT 1 FROM record o WHERE o.stream = 'community_service_outcomes' AND json_extract(o.payload,'$.referral_record_id') = r.record_id AND json_extract(o.payload,'$.service_delivered') = 1)) AS delivered FROM record r WHERE r.stream = 'community_service_referrals' GROUP BY 1 ORDER BY 1
- duplicate_lab_rows: Duplicate lab rows. Among lab rows with a draw time, extra copies sharing patient, test, draw time (event_time) and value: sum over groups of (rows - 1). Rows differing only by record_id are duplicates; same-day draws at different times are not.
  population: Lab rows (measurement.source_table = 'labs') with event_time.
  denominator: None (count).
  numerator: Rows beyond the first in each (patient, test, event_time, value) group.
  grain: {"entity": "lab_row", "key": "patient_id+test+event_time+value", "table": "measurement"}; unit: rows; returns: count
  time anchor: {"default": "all", "field": "measurement.event_time", "forbid_fields": ["event_date"], "mode": "any_time"}
  SQL template: SELECT coalesce(sum(n - 1), 0) AS duplicate_lab_rows FROM (SELECT count(*) AS n FROM measurement WHERE source_table = 'labs' AND event_time IS NOT NULL GROUP BY patient_id, name, event_time, value)
- food_insecurity_rate: Food insecurity rate among screened patients. Percent of patients screened for food need (any social_need row with need = 'food') with a positive food screen.
  population: Patients with a food-need screening answer.
  denominator: Distinct patients screened for food need.
  numerator: Of those, distinct patients with any positive food screen.
  grain: {"entity": "patient", "key": "patient_id", "table": "social_need"}; unit: percent; returns: value
  time anchor: {"default": "all", "field": "social_need.event_date", "mode": "any_time"}
  SQL template: SELECT 100.0 * count(DISTINCT CASE WHEN positive = 1 THEN patient_id END) / count(DISTINCT patient_id) AS food_insecurity_pct FROM social_need WHERE need = 'food'
### Domain payer_feed
- payer_claim_denial_rate: Payer-feed claim denial rate (current claim versions). Percent of payer-feed medical claims with service_from in the period whose final adjudication is a denial (denied or denied_upheld), counting only the current, non-duplicate version of each claim (payer_claim.counts_in_metrics = 1): replaced originals, voids and CARC 18 duplicate submissions never count.
  population: Payer-feed medical claims with service_from in the period.
  denominator: Current, non-duplicate payer claims (counts_in_metrics = 1).
  numerator: Of those, claims whose final_status is denied or denied_upheld.
  grain: {"entity": "claim", "key": "payer_claim_id", "table": "payer_claim"}; unit: percent; returns: value
  time anchor: {"default": "as_of_year", "field": "payer_claim.service_from", "forbid_fields": ["first_available_date", "last_available_date"], "mode": "period"}
  SQL template: SELECT 100.0 * sum(final_status IN ('denied','denied_upheld')) / count(*) AS payer_claim_denial_rate FROM payer_claim WHERE counts_in_metrics = 1 AND service_from BETWEEN '{period_start}' AND '{period_end}'
- payer_allowed_pmpm: Payer-feed allowed cost per member per month (enrollment months). Allowed dollars on payer-feed medical claims (current version, final status paid; replaced, voided and CARC 18 duplicate claims excluded) plus allowed dollars on paid pharmacy claims not reversed by a B2 return to stock, with service/fill dates in the period, divided by member months from enrollment spans in the period.
  population: Members with enrollment-span member months in the period.
  denominator: member_month rows with month in the period.
  numerator: Allowed dollars: payer_claim (counts_in_metrics = 1, final_status paid) + payer_pharmacy_claim (counts_as_fill = 1).
  grain: {"entity": "member_month", "key": "patient_id+month", "table": "member_month"}; unit: dollars per member per month; returns: value
  time anchor: {"default": "as_of_year", "field": "payer_claim.service_from", "forbid_fields": ["first_available_date", "last_available_date", "available_date"], "mode": "period"}
  SQL template: SELECT ((SELECT coalesce(sum(allowed_cents), 0) FROM payer_claim WHERE counts_in_metrics = 1 AND final_status = 'paid' AND service_from BETWEEN '{period_start}' AND '{period_end}') + (SELECT coalesce(sum(coalesce(plan_paid_cents, 0) + coalesce(patient_pay_cents, 0)), 0) FROM payer_pharmacy_claim WHERE counts_as_fill = 1 AND fill_date BETWEEN '{period_start}' AND '{period_end}')) / 100.0 / nullif((SELECT count(*) FROM member_month WHERE month BETWEEN substr('{period_start}', 1, 7) AND substr('{period_end}', 1, 7)), 0) AS payer_allowed_pmpm
- all_source_ed_visits_per_1000: ED visits per 1,000 member-years, all sources (EHR, ADT, payer claims). ED visits from every source (EHR encounters, ADT registrations, payer claims with POS 23 or revenue 045x/0981; one per real visit, utilization_event) in the period per 1,000 member-years from enrollment-span member months; claims-only members have no EHR encounter, so visit-table counts miss them.
  population: Members with enrollment-span member months in the period.
  denominator: member_month rows in the period / 12, divided by 1,000.
  numerator: utilization_event rows with kind ed_visit dated in the period (one per real visit across sources).
  grain: {"entity": "member_month", "key": "patient_id+month", "table": "member_month"}; unit: per 1,000 member-years; returns: value
  time anchor: {"default": "as_of_year", "field": "utilization_event.event_date", "mode": "period"}
  SQL template: SELECT 1000.0 * (SELECT count(*) FROM utilization_event WHERE kind = 'ed_visit' AND event_date BETWEEN '{period_start}' AND '{period_end}') / nullif((SELECT count(*) FROM member_month WHERE month BETWEEN substr('{period_start}', 1, 7) AND substr('{period_end}', 1, 7)) / 12.0, 0) AS ed_per_1000_member_years
### Domain revenue_cycle
- patient_ar_outstanding: Outstanding patient A/R (current). Sum of positive balance_cents on the latest snapshot (max event_time, record_id tiebreak) of each patient billing account (account_id). Patient credits are not netted; payer-pending amounts (provider_pending_cents) are not patient A/R. Dollars.
  population: Patient billing accounts.
  denominator: None (dollar total).
  numerator: Sum of positive latest balances.
  grain: {"entity": "account", "key": "account_id", "table": "current_record"}; unit: dollars; returns: value
  time anchor: {"default": "as_of", "field": "patient_billing_accounts.event_time", "mode": "snapshot"}
  SQL template: SELECT sum(balance_cents) / 100.0 AS patient_ar_usd FROM (SELECT json_extract(payload,'$.balance_cents') AS balance_cents, row_number() OVER (PARTITION BY json_extract(payload,'$.account_id') ORDER BY json_extract(payload,'$.event_time') DESC, record_id DESC) AS rn FROM record WHERE stream = 'patient_billing_accounts') WHERE rn = 1 AND balance_cents > 0
- claim_financials_current_state: Billed, allowed and paid by service type (current claim state). Medical claims (claim_id) with service_date in the period and at least one adjudication: the latest medical_claims record per claim by event_time (record_id tiebreak) is the current state. Billed = its billed_cents; allowed/paid = its allowed_cents/payer_paid_cents, except denied claims and claims whose latest record is a reversal awaiting replacement count 0 allowed and 0 paid. Grouped by service_code; dollars.
  population: Adjudicated medical claims with service_date in the period.
  denominator: None (dollar totals by service code).
  numerator: Billed, allowed and paid from each claim's current adjudication.
  grain: {"entity": "claim", "key": "claim_id", "table": "current_record"}; unit: dollars; returns: table
  time anchor: {"default": "as_of_year", "field": "claim.service_date", "forbid_fields": ["available_date"], "mode": "period"}
  SQL template: SELECT sc AS service_code, count(*) AS claims, sum(b) / 100.0 AS billed, sum(CASE WHEN st IN ('reversed','denied') THEN 0 ELSE al END) / 100.0 AS allowed, sum(CASE WHEN st IN ('reversed','denied') THEN 0 ELSE pd END) / 100.0 AS paid FROM (SELECT json_extract(payload,'$.service_code') AS sc, json_extract(payload,'$.service_date') AS sd, json_extract(payload,'$.status') AS st, json_extract(payload,'$.billed_cents') AS b, json_extract(payload,'$.allowed_cents') AS al, json_extract(payload,'$.payer_paid_cents') AS pd, row_number() OVER (PARTITION BY json_extract(payload,'$.claim_id') ORDER BY json_extract(payload,'$.event_time') DESC, record_id DESC) AS rn FROM record WHERE stream = 'medical_claims' AND json_extract(payload,'$.claim_id') IS NOT NULL) WHERE rn = 1 AND sd BETWEEN '{period_start}' AND '{period_end}' GROUP BY sc ORDER BY sc
- net_payer_paid: Net payer paid on medical claims (after reversals). Sum over medical claims (our provider claims plus payer-feed claims that are not the payer's copy of one of them; claim.claim_source) with service_date in the period of payer-paid deltas across EVERY adjudication record (original payments, reversals, voids, recoupments, replacement payments; denials 0) = claim.net_payer_paid_cents. Dollars.
  population: Medical claims (claim_id) with service_date in the period.
  denominator: None (dollar total).
  numerator: Net payer paid across all adjudication versions.
  grain: {"entity": "claim", "key": "claim_id", "table": "claim"}; unit: dollars; returns: value
  time anchor: {"default": "as_of_year", "field": "claim.service_date", "forbid_fields": ["available_date"], "mode": "period"}
  variants: adjudicated: Net payer paid (every adjudication delta; reversals net out) on medical claims first adjudicated in the period. Dollars.; first_paid: Net payer paid on medical claims whose first PAID adjudication falls in the period. Dollars.
  SQL template: SELECT sum(net_payer_paid_cents) / 100.0 AS net_payer_paid_usd FROM claim WHERE service_date BETWEEN '{period_start}' AND '{period_end}'
- contractual_writeoffs: Contractual write-offs (net). Sum of financial_deltas_cents.contractual across ALL adjudication records of medical claims with service_date in the period (reversal records carry negative contractual amounts and net out). Patient responsibility and denials are not contractual adjustments. Dollars.
  population: Medical claims (claim_id) with service_date in the period.
  denominator: None (dollar total).
  numerator: Net contractual adjustment across every adjudication version.
  grain: {"entity": "adjudication", "key": "record_id", "table": "record"}; unit: dollars; returns: value
  time anchor: {"default": "as_of_year", "field": "claim.service_date", "forbid_fields": ["available_date"], "mode": "period"}
  SQL template: SELECT sum(json_extract(payload,'$.financial_deltas_cents.contractual')) / 100.0 AS contractual_usd FROM record WHERE stream = 'medical_claims' AND json_extract(payload,'$.claim_id') IS NOT NULL AND json_extract(payload,'$.service_date') BETWEEN '{period_start}' AND '{period_end}'
- payer_cash_net: Payer cash posted, net of take-backs, by payer. payer_cash_allocations with event_date (posting date) in the period, negative allocations (recoupments) netted in. Allocations do not name the payer: payer = the medical claim's payer organization (claim.payer). Columns: payer, allocations, net_cash_dollars.
  population: Payer cash allocation records.
  denominator: None (dollar totals by payer).
  numerator: Sum of allocated_cents by payer.
  grain: {"entity": "allocation", "key": "record_id", "table": "record"}; unit: dollars; returns: table
  time anchor: {"default": "as_of_year", "field": "payer_cash_allocations.event_date", "mode": "period"}
  SQL template: SELECT c.payer AS payer, count(*) AS allocations, sum(json_extract(r.payload,'$.allocated_cents')) / 100.0 AS net_cash_dollars FROM record r JOIN claim c ON c.claim_id = json_extract(r.payload,'$.claim_id') WHERE r.stream = 'payer_cash_allocations' AND r.event_date BETWEEN '{period_start}' AND '{period_end}' GROUP BY c.payer ORDER BY c.payer
- first_pass_denial_rate: First-pass denial rate. Percent of medical claims with service_date in the period whose first adjudication (claim.first_status) was a denial.
  population: Medical claims with service_date in the period.
  denominator: Distinct claims.
  numerator: Claims with first_status = 'denied'.
  grain: {"entity": "claim", "key": "claim_id", "table": "claim"}; unit: percent; returns: value
  time anchor: {"default": "as_of_year", "field": "claim.service_date", "mode": "period"}
  variants: billed_dollars_adjudicated: Percent of billed dollars (billed_cents) on medical claims first adjudicated in the period that belong to claims whose first adjudication (claim.first_status) was a denial.; adjudicated: Percent of medical claims first adjudicated in the period whose first adjudication (claim.first_status) was a denial.; billed_dollars: Percent of billed dollars (billed_cents) on medical claims with service_date in the period that belong to claims whose first adjudication was a denial.
  SQL template: SELECT 100.0 * sum(first_status = 'denied') / count(*) AS first_pass_denial_rate FROM claim WHERE service_date BETWEEN '{period_start}' AND '{period_end}'
- claim_lag_days: Claim lag (date of service to first submission). Mean days from service_date to first_submitted_date for medical claims with service_date in the period.
  population: Medical claims with a submission.
  denominator: Claims with first_submitted_date.
  numerator: Sum of day differences.
  grain: {"entity": "claim", "key": "claim_id", "table": "claim"}; unit: days; returns: value
  time anchor: {"default": "as_of_year", "field": "claim.service_date", "forbid_fields": ["available_date", "first_available_date", "last_available_date"], "mode": "period"}
  SQL template: SELECT avg(julianday(first_submitted_date) - julianday(service_date)) AS avg_claim_lag_days FROM claim WHERE first_submitted_date IS NOT NULL AND service_date BETWEEN '{period_start}' AND '{period_end}'
- days_to_payer_cash: Days from date of service to first payer cash. Mean (or median when asked) days from service_date to first_payer_cash_date for medical claims with service_date in the period that have received payer cash.
  population: Medical claims with payer cash.
  denominator: Claims with first_payer_cash_date.
  numerator: Day differences.
  grain: {"entity": "claim", "key": "claim_id", "table": "claim"}; unit: days; returns: value
  time anchor: {"default": "as_of_year", "field": "claim.service_date", "forbid_fields": ["available_date", "first_available_date", "last_available_date"], "mode": "period"}
  variants: first_paid: Mean days from service_date to the claim's first PAID adjudication (the payer's payment decision), for medical claims whose first paid adjudication date falls in the period. 'First paid in' anchors on the paid adjudication date, not the cash date (say 'cash received' for the cash clock).; cash_received: Mean days from service_date to first_payer_cash_date for medical claims whose first payer cash posted in the period.
  SQL template: SELECT avg(julianday(first_payer_cash_date) - julianday(service_date)) AS avg_days_to_cash FROM claim WHERE first_payer_cash_date IS NOT NULL AND service_date BETWEEN '{period_start}' AND '{period_end}'
- days_to_first_adjudication: Days from date of service to first payer adjudication. Mean days from service_date to first_adjudication_date for medical claims with service_date in the period.
  population: Adjudicated medical claims.
  denominator: Claims with first_adjudication_date.
  numerator: Day differences.
  grain: {"entity": "claim", "key": "claim_id", "table": "claim"}; unit: days; returns: value
  time anchor: {"default": "as_of_year", "field": "claim.service_date", "forbid_fields": ["available_date", "first_available_date", "last_available_date"], "mode": "period"}
  variants: adjudicated: Mean days from service_date to first_adjudication_date for medical claims first adjudicated in the period.
  SQL template: SELECT avg(julianday(first_adjudication_date) - julianday(service_date)) AS avg_days_to_adjudication FROM claim WHERE first_adjudication_date IS NOT NULL AND service_date BETWEEN '{period_start}' AND '{period_end}'
- allowed_pmpm: Allowed cost per member per month (PMPM). Allowed dollars on medical claims with service_date in the period (current adjudication; reversed/denied = 0) divided by member-months (roster headcount x months in period; no enrollment spans received).
  population: Roster patients; member-months approximated from headcount.
  denominator: Roster headcount x months in the period.
  numerator: Allowed dollars on current claim adjudications.
  grain: {"entity": "member_month", "key": "patient_id+month", "table": "patient"}; unit: dollars per member per month; returns: value
  time anchor: {"default": "as_of_year", "field": "claim.service_date", "forbid_fields": ["available_date"], "mode": "period"}
  SQL template: SELECT (SELECT sum(CASE WHEN final_status IN ('reversed','denied') THEN 0 ELSE allowed_cents END) FROM claim WHERE service_date BETWEEN '{period_start}' AND '{period_end}') / 100.0 / ((SELECT count(*) FROM patient) * ((julianday('{period_end}') - julianday('{period_start}') + 1) / 30.4375)) AS allowed_pmpm
- claims_reversed: Medical claims reversed by the payer. Distinct medical claims (claim_id; our provider claims and payer-feed claims, claim_adjudication) with at least one payer reversal adjudication (status 'reversed') whose event date falls in the period - anchored on the reversal event date, not the service date. Provider voids/replacements and recoupments are not payer reversals.
  population: Medical claims with a reversal adjudication in the period.
  denominator: None (count).
  numerator: Distinct claim_ids with a reversal adjudication record dated in the period.
  grain: {"entity": "claim", "key": "claim_id", "table": "claim"}; unit: claims; returns: count
  time anchor: {"default": "as_of_year", "field": "claim_adjudication.event_date", "mode": "period"}
  variants: service_date: Distinct medical claims with service_date in the period that have any reversal adjudication record.
  SQL template: SELECT count(DISTINCT claim_id) AS claims_reversed FROM claim_adjudication WHERE status = 'reversed' AND event_date BETWEEN '{period_start}' AND '{period_end}'
### Domain utilization
- ed_visits: Emergency department visits. Distinct ED encounters (encounter versions de-duplicated) dated in the period.
  population: All roster patients.
  denominator: None (count).
  numerator: Distinct ED encounter_ids (visit.type_concept = 'emergency_visit') with event_date in the period.
  grain: {"entity": "visit", "key": "encounter_id", "table": "visit"}; unit: visits; returns: count
  time anchor: {"default": "as_of_year", "field": "visit.event_date", "mode": "period"}
  value sets: {"ed_visit": {"concept": "emergency_visit", "dimension": "visit.type", "exclude": ["urgent_care", "urgentcare", "observation", "inpatient"], "members": ["emergency", "ED", "ED stroke alert"]}}
  variants: hedis_edu: One per patient per date; ED visits ending in inpatient admission excluded (HEDIS EDU).
  SQL template: SELECT count(DISTINCT encounter_id) AS ed_visits FROM visit WHERE type_concept = 'emergency_visit' AND event_date BETWEEN '{period_start}' AND '{period_end}'
- ed_visits_per_1000: ED visits per 1,000 member-years. Distinct ED encounters in the period per 1,000 roster member-years (roster headcount x period length in years).
  population: Roster patients; member-years approximated from roster headcount and period length (no enrollment spans received).
  denominator: Roster patients x days in period / 365.25, divided by 1,000.
  numerator: Distinct ED encounter_ids dated in the period.
  grain: {"entity": "member_month", "key": "patient_id", "table": "patient"}; unit: per 1,000 member-years; returns: value
  time anchor: {"default": "as_of_year", "field": "visit.event_date", "mode": "period"}
  value sets: {"ed_visit": {"concept": "emergency_visit", "dimension": "visit.type", "exclude": ["urgent_care", "urgentcare"], "members": ["emergency", "ED", "ED stroke alert"]}}
  variants: per_headcount: Per 1,000 roster patients, not annualised: 1,000 x distinct ED encounters dated in the period / roster headcount (count of patients).
  SQL template: SELECT 1000.0 * (SELECT count(DISTINCT encounter_id) FROM visit WHERE type_concept = 'emergency_visit' AND event_date BETWEEN '{period_start}' AND '{period_end}') / ((SELECT count(*) FROM patient) * (julianday('{period_end}') - julianday('{period_start}') + 1) / 365.25) AS ed_per_1000_member_years
- repeat_ed_users_12m: Repeat ED users (2+ ED visits, trailing 12 months). Patients with two or more distinct ED encounters dated in the trailing 12 months ending at the as-of date (inclusive, 365 days).
  population: All roster patients.
  denominator: None (patient count or list).
  numerator: Patients with >= 2 distinct ED encounter_ids with event_date in the window.
  grain: {"entity": "patient", "key": "patient_id", "table": "patient"}; unit: patients; returns: count
  time anchor: {"default": "trailing", "field": "visit.event_date", "mode": "trailing", "months": 12}
  value sets: {"ed_visit": {"concept": "emergency_visit", "dimension": "visit.type", "exclude": ["urgent_care", "urgentcare"], "members": ["emergency", "ED", "ED stroke alert"]}}
  SQL template: SELECT count(*) AS patients FROM (SELECT patient_id FROM visit WHERE type_concept = 'emergency_visit' AND event_date BETWEEN '{window_start}' AND '{as_of}' GROUP BY patient_id HAVING count(DISTINCT encounter_id) >= 2)
- ed_admit_obs_share: Share of ED visits ending in admission or observation. Percent of completed ED encounters dated in the period whose ED disposition is an inpatient admission or observation placement; denominator = distinct completed ED encounters (disposition known).
  population: All roster patients.
  denominator: Distinct ED encounter_ids whose latest version is completed (carries the disposition), dated in the period.
  numerator: Denominator encounters with disposition admitted, inpatient_admission, observation, hospital_observation or observation_for_mobility.
  grain: {"entity": "visit", "key": "encounter_id", "table": "visit"}; unit: percent; returns: value
  time anchor: {"default": "as_of_year", "field": "visit.event_date", "mode": "period"}
  value sets: {"admit_obs_disposition": {"dimension": "visit.disposition", "exclude": ["home", "home_with_followup", "home_with_guardian"], "members": ["admitted", "inpatient_admission", "observation", "hospital_observation", "observation_for_mobility"], "required": true}, "ed_visit": {"concept": "emergency_visit", "dimension": "visit.type", "exclude": ["urgent_care", "urgentcare"], "members": ["emergency", "ED", "ED stroke alert"]}}
  variants: linked_conversion: ED-to-hospital conversion: ED encounter linked to a downstream inpatient/observation stay (referring/transferred-from ids or ADT transfer_to_observation).
  SQL template: SELECT 100.0 * sum(disposition IN ('admitted','inpatient_admission','observation','hospital_observation','observation_for_mobility')) / count(*) AS pct_admit_or_obs FROM visit WHERE type_concept = 'emergency_visit' AND status = 'completed' AND event_date BETWEEN '{period_start}' AND '{period_end}'
- pcp_visit_gap_12m: Patients without a primary care visit in the trailing 12 months. Roster patients with no in-person primary care encounter (primary care, office, wellness, pediatric office, ambulatory, annual physical) dated in the trailing 12 months ending at the as-of date (inclusive, 365 days). Telephone/virtual, specialty, urgent care and ED contacts do not count.
  population: All roster patients.
  denominator: Roster patients (count of those without a visit).
  numerator: Patients with no qualifying visit in the window.
  grain: {"entity": "patient", "key": "patient_id", "table": "patient"}; unit: patients; returns: count
  time anchor: {"default": "trailing", "field": "visit.event_date", "mode": "trailing", "months": 12}
  value sets: {"pcp_visit": {"concept": "primary_care_visit", "dimension": "visit.type", "exclude": ["telephone", "virtual", "urgent_care", "urgentcare", "emergency", "specialty_followup", "specialist_office"], "members": ["primary_care", "office", "Office visit", "pediatric_office", "wellness", "Annual physical", "ambulatory", "PCP", "Geri primary care"]}}
  SQL template: SELECT count(*) AS patients FROM patient p WHERE NOT EXISTS (SELECT 1 FROM visit v WHERE v.patient_id = p.patient_id AND v.type_concept = 'primary_care_visit' AND v.in_person = 1 AND v.event_date BETWEEN '{window_start}' AND '{as_of}')
- attributed_pcp_visit_gap_12m: Attributed patients without an in-person primary care visit at an attributed practice, trailing 12 months. Attributed patients (latest assigned primary-care relationship) with no in-person primary care encounter held at one of our practices (visit.is_practice = 1) in the trailing 12 months ending at the as-of date (inclusive, 365 days).
  population: Attributed patients: care_team_relationships role primary_care/pediatric_primary_care, status assigned (patient.pcp_practice_id).
  denominator: Attributed patients.
  numerator: Attributed patients with no qualifying practice visit in the window.
  grain: {"entity": "patient", "key": "patient_id", "table": "patient"}; unit: patients; returns: count
  time anchor: {"default": "trailing", "field": "visit.event_date", "mode": "trailing", "months": 12}
  value sets: {"pcp_visit": {"concept": "primary_care_visit", "dimension": "visit.type", "exclude": ["telephone", "virtual", "urgent_care", "urgentcare", "emergency", "specialty_followup", "specialist_office"], "members": ["primary_care", "office", "Office visit", "pediatric_office", "wellness", "Annual physical", "ambulatory", "PCP", "Geri primary care"], "qualifiers": [{"requires": "is_practice\\s*=\\s*1|organization_id\\s+like\\s+'[^']*practice", "why": "only visits held at our practices count for attributed primary care (outside dental/optometry/specialty sites do not)"}]}}
  SQL template: SELECT count(*) AS patients FROM patient p WHERE p.pcp_practice_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM visit v WHERE v.patient_id = p.patient_id AND v.type_concept = 'primary_care_visit' AND v.in_person = 1 AND v.is_practice = 1 AND v.event_date BETWEEN '{window_start}' AND '{as_of}')
- pcp_visits_per_1000_attributed: In-person primary care visits per 1,000 attributed patients, by attributed practice. By attributed practice: distinct in-person primary care encounters held at our practices in the period, for patients attributed to that practice, per 1,000 attributed patients (headcount).
  population: Attributed patients grouped by patient.pcp_practice_id.
  denominator: Patients attributed to the practice (headcount).
  numerator: Distinct qualifying encounters of those patients in the period (counted for the attributed practice even when seen at another of our practices).
  grain: {"entity": "visit", "key": "encounter_id", "table": "visit"}; unit: per 1,000 attributed patients; returns: table
  time anchor: {"default": "as_of_year", "field": "visit.event_date", "mode": "period"}
  value sets: {"pcp_visit": {"concept": "primary_care_visit", "dimension": "visit.type", "exclude": ["telephone", "virtual", "urgent_care", "urgentcare", "emergency", "specialty_followup", "specialist_office"], "members": ["primary_care", "office", "Office visit", "pediatric_office", "wellness", "Annual physical", "ambulatory", "PCP", "Geri primary care"], "qualifiers": [{"requires": "is_practice\\s*=\\s*1|organization_id\\s+like\\s+'[^']*practice", "why": "only visits held at our practices count (outside specialty/dental/optometry sites do not)"}]}}
  SQL template: SELECT p.pcp_practice_id AS practice, count(DISTINCT p.patient_id) AS attributed_patients, count(DISTINCT v.encounter_id) AS visits, 1000.0 * count(DISTINCT v.encounter_id) / count(DISTINCT p.patient_id) AS per_1000 FROM patient p LEFT JOIN visit v ON v.patient_id = p.patient_id AND v.type_concept = 'primary_care_visit' AND v.in_person = 1 AND v.is_practice = 1 AND v.event_date BETWEEN '{period_start}' AND '{period_end}' WHERE p.pcp_practice_id IS NOT NULL GROUP BY p.pcp_practice_id ORDER BY p.pcp_practice_id
- inpatient_admissions_per_1000: Acute inpatient stays per 1,000 member-years. Distinct inpatient stays (stay.type = 'inpatient', collapsed by admit day and overlap) admitted in the period per 1,000 roster member-years.
  population: Roster patients; member-years from roster headcount x period length.
  denominator: Roster member-years / 1,000.
  numerator: Inpatient stays with admit_date in the period.
  grain: {"entity": "stay", "key": "patient_id+admit_date", "table": "stay"}; unit: per 1,000 member-years; returns: value
  time anchor: {"default": "as_of_year", "field": "stay.admit_date", "mode": "period"}
  SQL template: SELECT 1000.0 * (SELECT count(*) FROM stay WHERE type = 'inpatient' AND admit_date BETWEEN '{period_start}' AND '{period_end}') / ((SELECT count(*) FROM patient) * (julianday('{period_end}') - julianday('{period_start}') + 1) / 365.25) AS admits_per_1000_member_years
- observation_stays: Observation stays. Distinct hospital observation stays (stay.type = 'observation'; encounter and ADT versions collapsed) admitted in the period.
  population: All roster patients.
  denominator: None (count).
  numerator: Observation stays with admit_date in the period.
  grain: {"entity": "stay", "key": "patient_id+admit_date", "table": "stay"}; unit: stays; returns: count
  time anchor: {"default": "as_of_year", "field": "stay.admit_date", "mode": "period"}
  SQL template: SELECT count(*) AS observation_stays FROM stay WHERE type = 'observation' AND admit_date BETWEEN '{period_start}' AND '{period_end}'
- average_length_of_stay: Average inpatient length of stay (days). Mean stay.los_days of inpatient stays with a received discharge (discharge_known = 1), anchored on admit_date in the period; stays with unknown discharge are excluded, never counted as 0 days.
  population: Inpatient stays with a known discharge.
  denominator: Inpatient stays with los_days not null.
  numerator: Sum of los_days.
  grain: {"entity": "stay", "key": "patient_id+admit_date", "table": "stay"}; unit: days; returns: value
  time anchor: {"default": "as_of_year", "field": "stay.admit_date", "mode": "period"}
  variants: discharged: Mean stay.los_days of inpatient stays with a received discharge (discharge_known = 1), anchored on discharge_date in the period; stays with unknown discharge are excluded, never counted as 0 days.
  SQL template: SELECT avg(los_days) AS avg_los_days FROM stay WHERE type = 'inpatient' AND los_days IS NOT NULL AND admit_date BETWEEN '{period_start}' AND '{period_end}'
- observation_stay_hours: Average observation stay length (hours). Mean hours from start_time to end_time of observation encounters (one per encounter_id) dated in the period; encounters without both times are excluded.
  population: Observation encounters with start and end times.
  denominator: Observation encounters with both times.
  numerator: Sum of (end_time - start_time) in hours.
  grain: {"entity": "visit", "key": "encounter_id", "table": "visit"}; unit: hours; returns: value
  time anchor: {"default": "as_of_year", "field": "visit.event_date", "mode": "period"}
  SQL template: SELECT avg((julianday(end_time) - julianday(start_time)) * 24) AS avg_obs_hours FROM visit WHERE type_concept = 'observation_stay' AND start_time IS NOT NULL AND end_time IS NOT NULL AND event_date BETWEEN '{period_start}' AND '{period_end}'
- readmission_30d: 30-day all-cause readmission rate. Percent of hospital stays discharged in the period followed by a new stay starting within 30 days of discharge (all-cause; planned readmissions not excluded).
  population: Stays with a known discharge in the period.
  denominator: Stays with discharge_date in the period.
  numerator: Denominator stays with readmitted_30d = 1.
  grain: {"entity": "stay", "key": "patient_id+admit_date", "table": "stay"}; unit: percent; returns: value
  time anchor: {"default": "as_of_year", "field": "stay.discharge_date", "mode": "period"}
  variants: hedis_pcr: Exclude planned readmissions and pregnancy/perinatal index stays (needs procedure/diagnosis detail).
  SQL template: SELECT 100.0 * sum(readmitted_30d) / count(*) AS readmission_rate FROM stay WHERE discharge_date BETWEEN '{period_start}' AND '{period_end}'
- post_discharge_followup_7d: Outpatient follow-up within 7 days of hospital discharge. Percent of hospital discharges (ADT discharge events of inpatient/observation encounters, each event once) whose patient has an in-person outpatient visit (primary care types or specialist office/follow-up) 1-7 days after the discharge date; same-day, telephone/virtual, urgent care, ED and home-health contacts do not count. Discharge date = the ADT discharge event's local business date (stay.discharge_date is the UTC date of the discharge time, one day later for evening discharges). Discharges in the trailing 12 months, ending 7 days before the as-of date so every follow-up window is fully observed.
  population: Inpatient and observation encounters with an ADT discharge event.
  denominator: ADT discharge events (inpatient/observation encounters) dated from the window start through as-of minus 7 days.
  numerator: Denominator discharges with a qualifying visit 1-7 days after discharge.
  grain: {"entity": "discharge", "key": "adt record_id", "table": "record"}; unit: percent; returns: value
  time anchor: {"default": "trailing", "field": "adt.event_date", "followup_days": {"max": 7, "min": [1]}, "forbid_fields": ["admit_date"], "index_end_offset_days": 7, "mode": "trailing", "months": 12}
  value sets: {"followup_visit": {"concept": "primary_care_visit", "dimension": "visit.type", "exclude": ["telephone", "virtual", "urgent_care", "urgentcare", "emergency", "home_health"], "members": ["primary_care", "office", "Office visit", "pediatric_office", "wellness", "Annual physical", "ambulatory", "PCP", "Geri primary care", "specialty_followup", "specialist_office"], "question_overrides": "phone|telephone|virtual|telehealth|urgent care"}}
  SQL template: WITH dc AS (SELECT a.record_id, a.patient_id, a.event_date AS discharge_day FROM record a JOIN visit e ON e.encounter_id = json_extract(a.payload,'$.encounter_id') WHERE a.stream = 'adt' AND json_extract(a.payload,'$.event') = 'discharge' AND e.type_concept IN ('inpatient_admission','observation_stay') AND a.event_date BETWEEN '{window_start}' AND '{index_end}') SELECT 100.0 * sum(EXISTS (SELECT 1 FROM visit v WHERE v.patient_id = dc.patient_id AND v.in_person = 1 AND v.type_concept IN ('primary_care_visit','specialist_visit') AND julianday(v.event_date) - julianday(dc.discharge_day) BETWEEN 1 AND 7)) / count(*) AS pct_followup_7d FROM dc
- ed_followup_7d: Follow-up within 7 days of an ED visit. Percent of distinct ED encounters in the period followed by a primary care, specialist or telehealth contact on the ED date or within 7 days after (HEDIS FMC 8-day window; 1-7 days when the question says 'after').
  population: All roster patients' ED encounters.
  denominator: Distinct ED encounter_ids dated in the period.
  numerator: ED encounters with a qualifying follow-up contact 0-7 days later.
  grain: {"entity": "visit", "key": "encounter_id", "table": "visit"}; unit: percent; returns: value
  time anchor: {"default": "as_of_year", "field": "visit.event_date", "followup_days": {"max": 7, "min": [0, 1]}, "mode": "period"}
  value sets: {"ed_visit": {"concept": "emergency_visit", "dimension": "visit.type", "members": ["emergency", "ED", "ED stroke alert"]}, "followup_contact": {"dimension": "visit.type", "exclude": ["urgent_care", "urgentcare"], "members": ["primary_care", "office", "Office visit", "pediatric_office", "wellness", "Annual physical", "ambulatory", "PCP", "Geri primary care", "specialty_followup", "specialist_office", "telephone", "virtual"], "question_overrides": "phone|telephone|office|pcp|specialist|in-person|in person"}}
  SQL template: SELECT 100.0 * count(DISTINCT CASE WHEN EXISTS (SELECT 1 FROM visit b WHERE b.patient_id = e.patient_id AND b.encounter_id <> e.encounter_id AND b.type_concept IN ('primary_care_visit','specialist_visit','telehealth_contact') AND julianday(b.event_date) - julianday(e.event_date) BETWEEN 0 AND 7) THEN e.encounter_id END) / count(DISTINCT e.encounter_id) AS pct_followup_7d FROM visit e WHERE e.type_concept = 'emergency_visit' AND e.event_date BETWEEN '{period_start}' AND '{period_end}'
