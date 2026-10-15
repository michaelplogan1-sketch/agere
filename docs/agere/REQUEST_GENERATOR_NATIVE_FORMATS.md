# Request to the synthetic-warehouse generator: native-format delivery for a competitor-grade test warehouse

**From:** Agere. **For:** the next release after v2.10, or a parallel "native" delivery of the same population.

## Why
To find out whether Agere is more accurate and cheaper than its competitors, every contestant has to plug into the same warehouse, built the way a health system or ACO data engineer actually builds one:

native feeds → warehouse → open-source canonical model (the Tuva Project, via its official connectors) → semantic layer → analytics tools.

Today's 237 custom JSON streams don't match how any real buyer's data arrives, and they force us to write the mapping ourselves, which biases the test. Native formats let us use Tuva's off-the-shelf connectors with minimal glue of our own.

## Ask: the same simulated population and truth, delivered as these files

| Feed | Format | Notes |
|---|---|---|
| Medicare ACO claims | **CMS CCLF** (CCLF1–9, CCLF A/B; fixed-width or the CSV CMS issues) + **ALR** assignment | Tuva's `medicare_cclf_connector` and `cms_alr_connector` read these directly |
| Commercial / Medicaid / MA medical claims | **X12 837P/837I** (submitted) + **835** (remittance: adjudications, CARC/RARC, reversals, recoupments) | One interchange per payer per week, with corrections and voids as frequency 7/8 |
| Eligibility | **X12 834** (adds, terms, retro-terms) + a flat monthly roster per payer | |
| Pharmacy | **NCPDP D.0** claims (paid / rejected with reject codes / reversed) + pharmacy dispense extract | |
| Hospitals | **HL7 v2 ADT** (A01/A03/A04/A06/A08, PV1 class, DG1) | Raw message files, multiple sending facilities |
| EHR clinical | **FHIR R4 bundles** (Patient, Encounter, Condition, Procedure, Observation, MedicationRequest, Immunization, Appointment) and/or **C-CDA** | Tuva's `fhir_preprocessing` reads FHIR |
| Labs | **HL7 v2 ORU** with LOINC/UCUM, corrections (result status C) | |
| SDOH vendor | flat CSV with SVI/ADI | |

Keep the current realism: duplicate charts, late arrivals, corrections, payer copies of provider claims, multiple feeds per event, time zones.

## Truth-backed questions and keys
Deliver the native release together with the sealed truth-backed question sets from REQUEST_GENERATOR_TRUTH_QUESTIONS.md:
- about 150 questions, about 15 in each of 10 healthcare domains;
- mostly in plain language, the way users actually ask, plus a share with definitions spelled out;
- answers sealed in `private/` and scored by `ecosystem.eval_scorer`;
- answers regenerated for every release.
Agere and the competitor baselines never see the answers.

## How it will be used
1. A DuckDB warehouse (a stand-in for Snowflake/Databricks), loaded through Tuva's official connectors, then Tuva core, marts and semantic layer.
2. The contestants plug into that warehouse:
   - an LLM with Tuva's semantic layer and verified queries (the Genie/Cortex shape);
   - an LLM on raw tables (the floor);
   - Agere, through its normal onboarding;
   - Agere and the LLM combined.
3. Paired scoring on the sealed questions, 3 repeats each: correct, precision on answered, abstentions, $/question and latency, per domain.
