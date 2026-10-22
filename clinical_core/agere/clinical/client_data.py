"""Client-feed artifacts of real EHR, lab, claims, pharmacy, ADT and document feeds (warehouse v2.3+), made explicit.

Called from population_feeds.build after the core semantic tables exist. Releases without these artifacts (v1.23, v2.0,
v2.2) get the same columns with neutral values, so earlier answers keep their meaning. Every rule below is stated in
SCHEMA_DOC and covered by a fixture test (test_warehouse_v23.py):

labs        string results '>60' / '<0.01' -> numeric value with value_censor ('>' | '<' | '>=' | '<='); TNP, hemolyzed or
            empty results -> excluded_reason; corrected results supersede what they correct (replaces_result_record_id,
            else the same order + test); duplicate interface messages (same order, test, value) -> one current row;
            local-only codes -> LOINC and concept via LOCAL_LAB_CODES; measurement_latest rebuilt on current, included rows.
claims      frequency-7 replacement supersedes its original (original_payer_claim_id); frequency-8 voids end the original;
            CARC 18 duplicate submissions never count when their original claim is present (else the claim is kept); ICD-10 sent without the decimal is normalised (received value kept);
            blank rendering NPI -> NULL; counts_in_metrics = the one current, non-duplicate version of each service.
pharmacy    B2 return-to-stock reversals are paired with the paid claim they reverse; neither counts as a fill
            (counts_as_fill, medication.fill_reversed), so PDC and fill linkage never see a returned prescription.
orders      e-prescriptions carry no fill link (NCPDP SCRIPT message id only): fills are linked by fuzzy matching of
            pharmacy prescription chains (patient + pharmacy NPI + Rx number) on drug, prescriber NPI, date, pharmacy and
            quantity, with a match_confidence; see link_prescriptions.
lists       problem, medication and allergy lists with last-confirmed dates, supporting evidence dates, staleness and a
            recency weight; structured smoking status per patient.
ADT/usage   adt_visit (one row per visit number: admit, discharge or not received, A08 updates, out-of-order arrival);
            utilization_event merges EHR encounters/stays, ADT visits and payer claims (POS 23, revenue 045x/0981, TOB 011x,
            revenue 0762) into one ED visit / admission per real event; events not already in the EHR are added to encounter
            (ED) and stay (admissions), so vbc, aggregate and the metric layer count claims-only members.
documents   scanned_document: outside PDFs with the text that can be read (PDF text layer, else the front-office OCR text)
            or the reason it cannot (image-only scan without OCR, file missing, file belongs to another record).
eligibility 9999-12-31 coverage ends -> open spans; reissued member IDs linked to the previous ID (same patient).
notes       note_therapy_change: starts, stops, dose changes and declined therapy recorded only in note text (sound-alike
            dictation errors corrected), with whether fills ended and whether the medication list was ever updated.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
import json, math, re, sqlite3

from . import concepts

# Local lab codes seen on interfaces that send no LOINC -> (LOINC, Agere concept).
LOCAL_LAB_CODES = {'CREAT': ('2160-0', 'creatinine'), 'NA': ('2951-2', 'sodium'), 'K': ('2823-3', 'potassium'),
                   'GLU': ('2345-7', 'glucose'), 'EGFR': ('98979-8', 'egfr'), 'HGB': ('718-7', 'hemoglobin'),
                   'WBC': ('6690-2', 'wbc'), 'LDLC': ('13457-7', 'ldl'), 'HDL': ('2085-9', 'hdl'), 'HGBA1C': ('4548-4', 'hba1c'),
                   'MALB/CR': ('14959-1', 'uacr'), 'TSH': ('3016-3', 'tsh'), 'NTBNP': ('33762-6', 'nt_probnp'),
                   'ALT': ('1742-6', None), 'PLT': ('777-3', 'platelets'), 'BUN': ('3094-0', 'bun'), 'TRIG': ('2571-8', 'triglycerides'),
                   'CHOL': ('2093-3', 'total_cholesterol')}
CENSORED = re.compile(r'^\s*(<=|>=|<|>)\s*(\d+(?:\.\d+)?)\s*$')
NOT_PERFORMED = re.compile(r'^\s*(tnp|test not performed|not performed|qns|quantity not sufficient)\s*$', re.I)
HEMOLYZED = re.compile(r'hemoly', re.I)
DUPLICATE_CARC = re.compile(r'^(?:[A-Z]{2}-)?18$')
ED_REVENUE = re.compile(r'^0?(45\d|981)$')
OBS_REVENUE = re.compile(r'^0?762$')
ICD10 = re.compile(r'^[A-TV-Z][0-9][0-9A-Z][0-9A-Z]{1,4}$')
ICD10_CATEGORY = re.compile(r'^[A-TV-Z][0-9][0-9A-Z]')
MATCH_DAYS = 1           # ED / admission records from different sources within a day are the same event
READMIT_DAYS = 30
STALE_DAYS = 365         # list entries not confirmed (or supported by claims/encounters) within a year are stale
FILL_LOOKAHEAD_DAYS = 60  # a prescription chain must start within 60 days of the order
LINK_THRESHOLD = 0.5
OTC = re.compile(r'\b(aspirin|vitamin|fish oil|omega|calcium carbonate|multivitamin|melatonin|acetaminophen|ibuprofen 200|'
                 r'loratadine|cetirizine|docusate|senna|polyethylene glycol|famotidine 10|magnesium|probiotic|glucosamine)\b', re.I)
ACUTE_PREFIXES = ('J00', 'J01', 'J02', 'J03', 'J06', 'J20', 'J11', 'N39', 'H66', 'L03', 'R05', 'R50', 'S', 'T1', 'B34', 'A09')
SHORTHAND = {'HTN': 'I10', 'DM2': 'E11', 'T2DM': 'E11', 'DM': 'E11', 'CKD': 'N18', 'CHF': 'I50', 'HF': 'I50', 'HFREF': 'I50',
             'HFPEF': 'I50', 'HLD': 'E78', 'HYPERLIPIDEMIA': 'E78', 'COPD': 'J44', 'CAD': 'I25', 'AFIB': 'I48', 'AF': 'I48',
             'GERD': 'K21', 'OA': 'M19', 'MDD': 'F32', 'DEPRESSION': 'F32', 'ANXIETY': 'F41', 'GAD': 'F41', 'OSA': 'G47',
             'OBESITY': 'E66', 'ASTHMA': 'J45', 'HYPOTHYROIDISM': 'E03', 'BPH': 'N40', 'OSTEOPOROSIS': 'M81', 'DEMENTIA': 'F03'}
SALTS = {'hydrochloride', 'hcl', 'potassium', 'sodium', 'calcium', 'maleate', 'besylate', 'succinate', 'tartrate', 'mesylate',
         'citrate', 'sulfate', 'bitartrate', 'fumarate', 'magnesium', 'acetate', 'phosphate', 'bromide', 'hyclate', 'monohydrate',
         'dihydrate', 'anhydrous', 'propionate', 'furoate', 'dipropionate'}
FORM_WORDS = {'oral', 'tablet', 'tablets', 'capsule', 'capsules', 'tab', 'cap', 'extended', 'release', 'delayed', 'chewable',
              'solution', 'suspension', 'injection', 'injectable', 'pen', 'inhaler', 'inhalation', 'metered', 'dose', 'actuat',
              'film', 'coated', 'hr', 'er', 'xl', 'sr', 'dr', 'odt', 'disintegrating', 'topical', 'cream', 'prefilled', 'syringe',
              'auto', 'injector', 'subcutaneous', 'mg', 'mcg', 'ml', 'unit', 'units', 'per', 'and', 'the', 'kit'}

DDL = """
CREATE TABLE payer_claim_diagnosis(payer_claim_id TEXT, patient_id TEXT, service_from TEXT, rank INTEGER, icd10 TEXT,
  icd10_received TEXT, counts_in_metrics INTEGER);
CREATE TABLE adt_visit(visit_number TEXT, patient_id TEXT, organization_id TEXT, patient_class TEXT, final_patient_class TEXT,
  admit_date TEXT, discharge_date TEXT, discharge_known INTEGER, discharge_disposition TEXT, admit_reason TEXT, messages INTEGER,
  a08_updates INTEGER, out_of_order INTEGER, discharge_before_admit INTEGER, admit_message_missing INTEGER, record_id TEXT,
  PRIMARY KEY(patient_id, visit_number));
CREATE TABLE utilization_event(patient_id TEXT, kind TEXT, event_date TEXT, end_date TEXT, discharge_known INTEGER, sources TEXT,
  in_ehr INTEGER, source_records INTEGER, record_id TEXT, organization_id TEXT, readmitted_30d INTEGER, days_to_readmission INTEGER);
CREATE TABLE problem_list_entry(patient_id TEXT, problem_key TEXT, icd10 TEXT, label TEXT, entries INTEGER, coded_entries INTEGER,
  shorthand_entries INTEGER, first_recorded TEXT, last_confirmed TEXT, clinical_status TEXT, resolved_date TEXT, onset_date TEXT,
  last_supporting_evidence TEXT, days_since_confirmed INTEGER, days_since_evidence INTEGER, acute INTEGER, stale INTEGER,
  recency_weight REAL, record_id TEXT, PRIMARY KEY(patient_id, problem_key));
CREATE TABLE medication_list_entry(patient_id TEXT, drug_key TEXT, name TEXT, rxcui TEXT, list_status TEXT, list_source TEXT,
  entries INTEGER, first_listed TEXT, last_listed TEXT, last_fill_date TEXT, last_order_date TEXT, otc INTEGER,
  days_since_listed INTEGER, days_since_fill INTEGER, discrepancy TEXT, stale INTEGER, recency_weight REAL, record_id TEXT,
  PRIMARY KEY(patient_id, drug_key));
CREATE TABLE allergy_entry(patient_id TEXT, substance TEXT, substance_received TEXT, reaction TEXT, reaction_missing INTEGER,
  free_text INTEGER, no_known_allergies INTEGER, status TEXT, entries INTEGER, first_recorded TEXT, last_confirmed TEXT,
  days_since_confirmed INTEGER, record_id TEXT, PRIMARY KEY(patient_id, substance));
CREATE TABLE smoking_status(patient_id TEXT PRIMARY KEY, status TEXT, status_received TEXT, snomed TEXT, status_date TEXT,
  days_since_status INTEGER, structured_records INTEGER, record_id TEXT);
CREATE TABLE scanned_document(record_id TEXT PRIMARY KEY, patient_id TEXT, document_id TEXT, document_type TEXT, document_title TEXT,
  received_via TEXT, event_date TEXT, available_date TEXT, page_count INTEGER, has_text_layer INTEGER, review_status TEXT,
  signature_status TEXT, sending_facility TEXT, file_status TEXT, text_source TEXT, readable INTEGER, unreadable_reason TEXT,
  text_chars INTEGER, records_sharing_file INTEGER, pdf_date_of_service TEXT, duplicate_of_record_id TEXT);
CREATE TABLE scanned_document_text(record_id TEXT PRIMARY KEY, patient_id TEXT, text_source TEXT, text TEXT);
CREATE TABLE note_therapy_change(record_id TEXT, patient_id TEXT, event_date TEXT, encounter_id TEXT, stream TEXT, action TEXT,
  target TEXT, target_kind TEXT, drug_key TEXT, drug_class TEXT, reason TEXT, dictated INTEGER, sound_alike_corrected INTEGER,
  evidence_text TEXT, order_record_id TEXT, fills_after_180d INTEGER, list_active_after INTEGER, extractor TEXT);
"""
INDEXES = """
CREATE INDEX payer_claim_diagnosis_code ON payer_claim_diagnosis(icd10, patient_id);
CREATE INDEX utilization_event_patient ON utilization_event(kind, patient_id, event_date);
CREATE INDEX scanned_document_patient ON scanned_document(patient_id);
CREATE INDEX note_therapy_change_patient ON note_therapy_change(patient_id, action);
"""

SCHEMA_DOC = {
    'enrollment_span_quality': 'enrollment_span.coverage_end is NULL for open spans, including spans a payer sends with the '
                               '9999-12-31 sentinel (open_ended = 1; the literal stays in coverage_end_received). A member ID '
                               'reissued mid-span (member_id_reissued = 1, previous_member_id = the old ID) is the same person: '
                               'spans, member months and claims stay on one patient_id.',
    'measurement_quality': 'measurement.value_text = result received as text; value_censor = > | < | >= | <= when a censored '
                           'string such as eGFR ">60" or TSH "<0.01" was read as its bound (value/value_canonical hold the bound; '
                           'treat as "at least"/"at most"); excluded_reason = test_not_performed | specimen_hemolyzed | '
                           'no_value_reported | cancelled (no value; never a result); result_current = 0 when a later corrected '
                           'result or an earlier identical interface message supersedes the row (superseded_by_record_id, '
                           'supersede_reason = corrected | duplicate_interface_message); local_code = interface code; '
                           'loinc_source = local_code_map when the LOINC came from LOCAL_LAB_CODES. measurement_latest uses '
                           'current, non-excluded rows only and carries value_censor.',
    'payer_claim_versions': 'payer_claim.claim_frequency_code (1 original, 7 replacement, 8 void), original_payer_claim_id '
                            '(on a replacement), superseded_by_payer_claim_id (on the original), version_status = current | '
                            'superseded_by_replacement | voided | duplicate_submission, duplicate_submission = 1 when any '
                            'adjudication carries CARC 18 (exact duplicate), counts_in_metrics = 1 only for the current, '
                            'non-duplicate version: use it for every count, cost, denial and utilization metric. '
                            'principal_diagnosis/diagnosis_codes are dotted ICD-10-CM (payers that drop the decimal are '
                            'normalised; *_received keep the literal); rendering_npi NULL when not sent (rendering_npi_missing).',
    'payer_claim_diagnosis': 'One row per (payer claim, diagnosis position): dotted icd10, icd10_received, counts_in_metrics.',
    'payer_pharmacy_reversal': 'payer_pharmacy_claim.transaction_code B2 = reversal (return to stock: filled, never picked up); '
                               'reversal_of_record_id / reversed_by_record_id pair it with the paid claim it reverses (the claim a reversal or '
                               'recoupment names in reverses_record_id or supersedes_record_id, else the same prescription chain within 30 days); '
                               'counts_as_fill = 1 only for paid, unreversed claims. medication.fill_reversed = 1 on reversed '
                               'fills (PDC and fill linkage ignore them).',
    'adt_visit': 'One row per ADT visit number: patient_class at admission (E/I/O) and after updates, admit_date (A01/A04), '
                 'discharge_date (A03; NULL with discharge_known = 0 when no discharge was received), a08_updates, '
                 'out_of_order = messages arrived in a different order than they happened, discharge_before_admit.',
    'utilization_event': 'All-source utilization: one row per ED visit (kind ed_visit) or hospital admission (inpatient | '
                         'observation) per patient, merging EHR encounters/stays, ADT visits and payer claims (ED = POS 23 '
                         'professional or revenue 045x/0981; inpatient = TOB 011x; observation = revenue 0762) that fall within '
                         'one day of each other; sources = ehr,adt,claim; in_ehr = 1 when an EHR encounter/stay exists. '
                         'Claims count only when counts_in_metrics = 1. Events not in the EHR are also rows of encounter '
                         '(source_table adt_messages | payer_medical_claims, type_concept emergency_visit) and stay '
                         '(stay_source adt | payer_claim), so ED and admission rates cover claims-only members.',
    'problem_list_entry': 'Problem list per (patient, ICD-10 category; shorthand such as HTN/DM2 mapped): entries (duplicates '
                          'collapsed), first_recorded, last_confirmed (latest entry date), clinical_status of the latest entry, '
                          'last_supporting_evidence (latest payer-claim or encounter diagnosis in the category), stale = 1 when '
                          'neither confirmed nor supported in 365 days (or an acute problem left active > 90 days), '
                          'recency_weight = 0.5 ^ (days since the later of the two / 365). Down-weight stale entries.',
    'medication_list_entry': 'Medication list per (patient, ingredient): list_status/list_source of the latest entry, '
                             'last_listed, last_fill_date (paid, unreversed fills), discrepancy = listed_active_no_fill_180d '
                             '(commission candidate) | filled_not_listed (omission) | listed_discontinued_still_filled, '
                             'otc = 1 for over-the-counter items (list-only by nature), stale, recency_weight.',
    'allergy_entry': 'Allergies per (patient, substance): substance normalised from free text ("penicillin (rash)", '
                     '"Sulfa - rash"), reaction parsed or reaction_missing = 1, free_text = 1 when uncoded, last_confirmed.',
    'smoking_status': 'Latest structured smoking status per patient (never | former | current | unknown) with its date; '
                      'patients without a row have smoking status only in note text (note_fact smoking_status).',
    'note_therapy_change': 'Therapy decisions recorded only in note text (starts, stops, dose increases/decreases, declined '
                           'therapy or services) from notes and readable outside documents, extractor therapy-rules/1 (deterministic, '
                           'precision not yet measured on a held-out sample: treat as candidate evidence). target_kind = drug | '
                           'drug_class | service | unspecified; drug_class from the ingredient or class word (antihypertensive, '
                           'SSRI, statin). For stops: fills_after_180d = counted fills of the drug in the 180 days after the note '
                           '(0 = fills ended), list_active_after = 1 when the latest medication-list entry still shows the drug active '
                           '(list never updated after the stop). order_record_id = an order for a start on the same encounter/date. '
                           'dictated = speech-recognition note (sound-alike drug names such as "lysine april" corrected: '
                           'sound_alike_corrected = 1); clinical_note.dictated / dictation_unreviewed carry the same flags.',
    'scanned_document': 'Outside documents (faxes, scans, portal uploads) with indexing as received (document_type is often a '
                        'generic label), review/signature status, and readability: text_source = pdf_text_layer | '
                        'front_office_ocr (OCR text with errors); readable = 0 with unreadable_reason = image_only_no_ocr | '
                        'file_missing | file_belongs_to_another_record | file_unverified_shared. duplicate_of_record_id = '
                        'same patient, title, date and text as an earlier fax. Text in scanned_document_text.',
}


def _days(a, b):
    try: return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    except (TypeError, ValueError): return None


def _add(out, table, *cols):
    have = {r[1] for r in out.execute(f'PRAGMA table_info({table})')}
    for col in cols:
        if col.split()[0] not in have: out.execute(f'ALTER TABLE {table} ADD COLUMN {col}')


def _has(out, table):
    return out.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone() is not None


# ---- labs ---------------------------------------------------------------------------------------------------------
def _factor(concept, unit):
    spec = concepts.MEASUREMENTS.get(concept or '')
    return spec['units'].get(unit) if spec else None


def labs(out):
    _add(out, 'measurement', 'value_text TEXT', 'value_censor TEXT', 'excluded_reason TEXT', 'result_current INTEGER',
         'superseded_by_record_id TEXT', 'supersede_reason TEXT', 'local_code TEXT', 'interface_message_id TEXT')
    out.execute('CREATE INDEX IF NOT EXISTS measurement_record ON measurement(record_id)')  # per-record updates below
    out.execute('UPDATE measurement SET result_current = 1')
    rows = out.execute("""SELECT record_id, patient_id, json_extract(payload,'$.value'), json_type(payload,'$.value'),
        json_extract(payload,'$.comment'), json_extract(payload,'$.result_status'), json_extract(payload,'$.replaces_result_record_id'),
        json_extract(payload,'$.interface_message_id'), json_extract(payload,'$.local_code'), json_extract(payload,'$.laboratory_order_record_id'),
        coalesce(json_extract(payload,'$.name'), json_extract(payload,'$.test')), json_extract(payload,'$.unit'),
        coalesce(json_extract(payload,'$.specimen_time'), json_extract(payload,'$.collected_time'), event_date), event_date, available_date,
        json_extract(payload,'$.loinc'), json_extract(payload,'$.source_system')
        FROM record WHERE stream = 'labs'""").fetchall()
    stats = Counter()
    censored, excluded, local = [], [], []
    groups = defaultdict(list)
    supersede = {}
    identity = defaultdict(list)  # (patient, analyte, collection minute) -> results from any feed
    for rid, pid, value, vtype, comment, status, replaces, msg, lcode, order, name, unit, spec, d, av, loinc, system in rows:
        analyte = loinc or next((LOCAL_LAB_CODES[str(c).strip().upper()][0] for c in (lcode, name)
                                 if c and str(c).strip().upper() in LOCAL_LAB_CODES), None)
        if pid and analyte and isinstance(spec, str) and len(spec) >= 16 and vtype not in (None, 'null'):
            identity[(pid, analyte, spec[:16])].append((av or '', 0 if msg is None else 1, rid, status, value, system))
        if vtype == 'text':
            m = CENSORED.match(value)
            if m: censored.append((rid, m.group(1), float(m.group(2)), value)); stats['censored'] += 1
            elif NOT_PERFORMED.match(value): excluded.append((rid, 'test_not_performed', value)); stats['test_not_performed'] += 1
        elif vtype in (None, 'null'):
            if status == 'cancelled': excluded.append((rid, 'cancelled', None)); stats['cancelled'] += 1
            elif comment and HEMOLYZED.search(comment): excluded.append((rid, 'specimen_hemolyzed', None)); stats['specimen_hemolyzed'] += 1
            elif comment or status == 'not_calculated': excluded.append((rid, 'no_value_reported', None)); stats['no_value_reported'] += 1
        if lcode: local.append((rid, lcode, msg))
        elif msg: local.append((rid, None, msg))
        if replaces and replaces != rid: supersede[replaces] = (rid, 'corrected'); stats['corrected_by_reference'] += 1
        if order and name:
            groups[(pid, order, ' '.join(str(name).lower().split()), spec)].append((av or '', 0 if msg is None else 1, rid, status, value))
    for key, g in groups.items():
        if len(g) < 2: continue
        g.sort()
        corrected = [x for x in g if x[3] == 'corrected']
        if corrected:  # the latest correction wins over every other result of the same order and test
            win = corrected[-1][2]
            for x in g:
                if x[2] != win and x[2] not in supersede: supersede[x[2]] = (win, 'corrected'); stats['corrected_in_order'] += 1
            continue
        seen = {}
        for av, _, rid, status, value in g:  # identical result re-sent by the interface: the first arrival is the result
            k = (status, json.dumps(value))
            if k in seen and rid not in supersede: supersede[rid] = (seen[k], 'duplicate_interface_message'); stats['duplicate_interface_message'] += 1
            else: seen.setdefault(k, rid)
    # One result across feeds (HL7 ORU, the EHR extract, the FHIR export): the same patient, analyte (LOINC, or the LOINC of a
    # local-only code) and collection minute is one result. A correction from any feed wins over every other copy; otherwise
    # copies with the same value are one result (the first arrival). Different values without a correction stay apart, and
    # results from one source system are left to that system's own versioning (above).
    for key, g in identity.items():
        live = [x for x in g if x[2] not in supersede]
        if len(live) < 2 or len({x[5] for x in live}) < 2: continue
        live.sort()
        corrected = [x for x in live if x[3] == 'corrected']
        if corrected:
            win = corrected[-1]
            for x in live:
                if x[5] != win[5]: supersede[x[2]] = (win[2], 'corrected'); stats['corrected_across_feeds'] += 1
            continue
        seen = {}
        for av, _, rid, status, value, system in live:
            try: k = round(float(value), 6)
            except (TypeError, ValueError): k = ' '.join(str(value).lower().split())
            first = seen.setdefault(k, (rid, system))
            if first[1] != system: supersede[rid] = (first[0], 'duplicate_across_feeds'); stats['duplicate_across_feeds'] += 1
    out.executemany('UPDATE measurement SET result_current = 0, superseded_by_record_id = ?, supersede_reason = ? WHERE record_id = ?',
                    [(w, why, rid) for rid, (w, why) in supersede.items()])
    out.executemany('UPDATE measurement SET excluded_reason = ?, value_text = ?, value = NULL, value_canonical = NULL WHERE record_id = ?',
                    [(why, txt, rid) for rid, why, txt in excluded])
    out.executemany('UPDATE measurement SET local_code = ?, interface_message_id = ? WHERE record_id = ?', [(c, m, r) for r, c, m in local])
    # Local-only codes: the code decides the analyte (a name such as 'HEMOGLOBIN A1C' must not also read as hemoglobin).
    fixed = 0
    for rid, lcode, _ in local:
        code = LOCAL_LAB_CODES.get(str(lcode or '').upper())
        if not code: continue
        loinc, concept = code
        cur = out.execute('SELECT rowid, concept, loinc FROM measurement WHERE record_id = ? ORDER BY rowid', (rid,)).fetchall()
        if not cur: continue
        if any(r[1] == 'gfr_unnormalized' for r in cur): continue  # GFR in a non-BSA unit stays unnormalised
        if concept and (len(cur) > 1 or cur[0][1] != concept):
            keep = next((r for r in cur if r[1] == concept), cur[0])
            out.execute('DELETE FROM measurement WHERE record_id = ? AND rowid <> ?', (rid, keep[0]))
            out.execute('UPDATE measurement SET concept = ? WHERE rowid = ?', (concept, keep[0])); fixed += 1
        out.execute("UPDATE measurement SET loinc = ?, loinc_source = 'local_code_map' WHERE record_id = ? AND loinc IS NULL", (loinc, rid))
    stats['local_code_concept_fixed'] = fixed
    for rid, op, num, txt in censored:
        for rowid, concept, unit in out.execute('SELECT rowid, concept, unit FROM measurement WHERE record_id = ?', (rid,)).fetchall():
            f = _factor(concept, unit)
            out.execute('UPDATE measurement SET value = ?, value_canonical = ?, unit_canonical = ?, value_censor = ?, value_text = ? WHERE rowid = ?',
                        (num, num * f if f is not None else None, concepts.MEASUREMENTS[concept]['unit'] if f is not None else None, op, txt, rowid))
    # Canonical values for rows whose concept came from the local-code map or the normalization dictionary (_normalize sets the
    # concept after the row was written, so its canonical value was never computed and measurement_latest skipped it).
    for rowid, concept, value, unit in out.execute("SELECT rowid, concept, value, unit FROM measurement WHERE concept IS NOT NULL "
                                                  "AND value IS NOT NULL AND value_canonical IS NULL").fetchall():
        f = _factor(concept, unit)
        if f is not None:
            out.execute('UPDATE measurement SET value_canonical = ?, unit_canonical = ? WHERE rowid = ?', (value * f, concepts.MEASUREMENTS[concept]['unit'], rowid))
            stats['canonical_value_filled'] += 1
    _add(out, 'measurement_latest', 'value_censor TEXT')
    out.execute('DELETE FROM measurement_latest')
    out.execute("""INSERT INTO measurement_latest(patient_id, concept, event_date, value_canonical, unit_canonical, same_day_values,
          same_day_min, record_id, value_censor)
        SELECT patient_id, concept, event_date, value_canonical, unit_canonical, n, mn, record_id, value_censor FROM (
          SELECT m.*, count(*) OVER d AS n, min(m.value_canonical) OVER d AS mn,
                 row_number() OVER (PARTITION BY m.patient_id, m.concept ORDER BY m.event_date DESC, m.available_date DESC, m.record_id DESC) AS rn
          FROM measurement m WHERE m.concept IS NOT NULL AND m.value_canonical IS NOT NULL AND m.event_date IS NOT NULL
            AND coalesce(m.result_current, 1) = 1 AND m.excluded_reason IS NULL
          WINDOW d AS (PARTITION BY m.patient_id, m.concept, m.event_date)) WHERE rn = 1""")
    return dict(stats)


# ---- eligibility -----------------------------------------------------------------------------------------------------
OPEN_END = '9999'


def enrollment(out):
    """Open spans sent as 9999-12-31 -> coverage_end NULL (open_ended = 1, literal kept in coverage_end_received); member IDs
    reissued mid-span stay one person (same patient_id): previous_member_id links the new ID to the old one."""
    _add(out, 'enrollment_span', 'coverage_end_received TEXT', 'open_ended INTEGER', 'previous_member_id TEXT', 'member_id_reissued INTEGER')
    out.execute('UPDATE enrollment_span SET coverage_end_received = coverage_end')
    rows = out.execute("""SELECT record_id, json_extract(payload,'$.previous_member_id'), json_extract(payload,'$.enrollment_reason'),
                                json_extract(payload,'$.termination_reason') FROM record WHERE stream = 'enrollment_spans'""").fetchall()
    out.executemany('UPDATE enrollment_span SET previous_member_id = ?, member_id_reissued = ? WHERE record_id = ?',
                    [(prev, int(bool(prev) or 'member_id_reissued' in (er, tr)), rid) for rid, prev, er, tr in rows])
    out.execute(f"UPDATE enrollment_span SET coverage_end = NULL WHERE coverage_end >= '{OPEN_END}'")
    out.execute('UPDATE enrollment_span SET open_ended = (coverage_end IS NULL)')
    one = lambda sql: out.execute(sql).fetchone()[0]
    return {'open_end_sentinel': one(f"SELECT count(*) FROM enrollment_span WHERE coverage_end_received >= '{OPEN_END}'"),
            'member_id_reissued_spans': one('SELECT count(*) FROM enrollment_span WHERE member_id_reissued = 1'),
            'patients_with_several_member_ids': one('SELECT count(*) FROM (SELECT patient_id FROM enrollment_span WHERE member_id IS NOT NULL '
                                                    'GROUP BY patient_id, payer_id HAVING count(DISTINCT member_id) > 1)')}


# ---- claims ---------------------------------------------------------------------------------------------------------
def icd10(code):
    """Dotted upper-case ICD-10-CM: 'E119' -> 'E11.9'; already dotted or non-ICD values unchanged."""
    if not isinstance(code, str): return code
    c = code.strip().upper()
    if '.' in c or len(c) <= 3 or not ICD10.match(c): return c
    return c[:3] + '.' + c[3:]


def claims(out):
    _add(out, 'payer_claim', 'member_id TEXT', 'claim_frequency_code TEXT', 'original_payer_claim_id TEXT',
         'superseded_by_payer_claim_id TEXT', 'version_status TEXT', 'duplicate_submission INTEGER', 'counts_in_metrics INTEGER',
         'principal_diagnosis_received TEXT', 'diagnosis_codes_received TEXT', 'icd10_normalized INTEGER', 'rendering_npi_missing INTEGER')
    _add(out, 'payer_claim_line', 'counts_in_metrics INTEGER')
    info = defaultdict(lambda: {'freq': [], 'carc': set(), 'orig': None, 'member': None, 'void': False})
    for cid, freq, carc, orig, member, status in out.execute(
            """SELECT json_extract(payload,'$.payer_claim_id'), json_extract(payload,'$.claim_frequency_code'), json_extract(payload,'$.carc'),
                      json_extract(payload,'$.original_payer_claim_id'), json_extract(payload,'$.member_id'), json_extract(payload,'$.claim_status')
               FROM record WHERE stream = 'payer_medical_claims'"""):
        i = info[cid]
        if freq: i['freq'].append(str(freq))
        if carc: i['carc'].add(str(carc))
        if orig: i['orig'] = orig
        if member: i['member'] = member
        if str(freq) == '8' or status == 'voided': i['void'] = True
    replaced = {i['orig']: cid for cid, i in info.items() if i['orig']}
    present = set(info) | {r[0] for r in out.execute('SELECT payer_claim_id FROM payer_claim')}
    rows, stats = [], Counter()
    for cid, pdx, dx, rnpi, ctype in out.execute('SELECT payer_claim_id, principal_diagnosis, diagnosis_codes, rendering_npi, claim_type FROM payer_claim').fetchall():
        i = info.get(cid) or info.default_factory()
        # a CARC 18 denial drops the claim only when the original it duplicates was received (-D -> its base claim id);
        # without the original the claim is the only copy of the service and is kept
        orig = i['orig'] if i['orig'] and i['orig'] != cid else (re.sub(r'-D(?=(-X)?$)', '', cid) if isinstance(cid, str) else None)
        carc18 = any(DUPLICATE_CARC.match(c) for c in i['carc'])
        dup = int(carc18 and bool(orig) and orig != cid and orig in present)
        stats['duplicate_carc_without_original_kept'] += int(carc18 and not dup)
        if dup: status = 'duplicate_submission'
        elif cid in replaced: status = 'superseded_by_replacement'
        elif i['void']: status = 'voided'
        else: status = 'current'
        stats[status] += 1
        codes = json.loads(dx) if isinstance(dx, str) and dx.startswith('[') else ([dx] if dx else [])
        norm = [icd10(c) for c in codes]
        changed = int(icd10(pdx) != pdx or norm != codes)
        stats['icd10_normalized'] += changed
        rnpi = rnpi if rnpi not in ('', None) else None
        stats['professional_rendering_npi_missing'] += rnpi is None and ctype == 'professional'
        stats['professional_claims'] += ctype == 'professional'
        freq = i['freq'][-1] if i['freq'] else None
        rows.append((i['member'], freq, i['orig'], replaced.get(cid), status, dup, int(status == 'current'), pdx, dx, changed,
                     int(rnpi is None), icd10(pdx), json.dumps(norm) if codes else dx, rnpi, cid))
    out.executemany("""UPDATE payer_claim SET member_id = ?, claim_frequency_code = ?, original_payer_claim_id = ?,
        superseded_by_payer_claim_id = ?, version_status = ?, duplicate_submission = ?, counts_in_metrics = ?,
        principal_diagnosis_received = ?, diagnosis_codes_received = ?, icd10_normalized = ?, rendering_npi_missing = ?,
        principal_diagnosis = ?, diagnosis_codes = ?, rendering_npi = ? WHERE payer_claim_id = ?""", rows)
    out.execute('UPDATE payer_claim_line SET counts_in_metrics = (SELECT c.counts_in_metrics FROM payer_claim c '
                'WHERE c.payer_claim_id = payer_claim_line.payer_claim_id)')
    dx_rows = []
    for cid, pid, svc, dx, received, counts in out.execute(
            'SELECT payer_claim_id, patient_id, service_from, diagnosis_codes, diagnosis_codes_received, counts_in_metrics FROM payer_claim'):
        codes = json.loads(dx) if isinstance(dx, str) and dx.startswith('[') else ([dx] if dx else [])
        raw = json.loads(received) if isinstance(received, str) and received.startswith('[') else codes
        dx_rows += [(cid, pid, svc, n + 1, c, raw[n] if n < len(raw) else c, counts) for n, c in enumerate(codes) if c]
    out.executemany('INSERT INTO payer_claim_diagnosis VALUES (?,?,?,?,?,?,?)', dx_rows)
    stats['diagnosis_rows'] = len(dx_rows)
    return dict(stats)


# ---- pharmacy reversals -------------------------------------------------------------------------------------------
def pharmacy(out):
    _add(out, 'payer_pharmacy_claim', 'prescription_number TEXT', 'member_id TEXT', 'transaction_code TEXT', 'reversal_reason TEXT',
         'reversal_of_record_id TEXT', 'reversed_by_record_id TEXT', 'counts_as_fill INTEGER')
    _add(out, 'medication', 'fill_reversed INTEGER')
    out.execute('CREATE INDEX IF NOT EXISTS medication_record ON medication(record_id)')
    extra = out.execute("""SELECT record_id, json_extract(payload,'$.prescription_number'), json_extract(payload,'$.member_id'),
        json_extract(payload,'$.transaction_code'), json_extract(payload,'$.reversal_reason') FROM record WHERE stream = 'payer_pharmacy_claims'""").fetchall()
    out.executemany('UPDATE payer_pharmacy_claim SET prescription_number = ?, member_id = ?, transaction_code = ?, reversal_reason = ? '
                    'WHERE record_id = ?', [(n, m, t, r, rid) for rid, n, m, t, r in extra])
    paid = defaultdict(list)
    rows = out.execute("""SELECT record_id, patient_id, prescription_number, fill_number, ndc, pharmacy_npi, fill_date, claim_status, transaction_code
                          FROM payer_pharmacy_claim ORDER BY fill_date, record_id""").fetchall()
    for rid, pid, rx, n, ndc, ph, d, status, code in rows:
        if status == 'paid' and code != 'B2': paid[(pid, rx, n, ndc, ph)].append((d, rid))
    pairs, used, unpaired = [], set(), 0
    # Exact links first: a reversal or recoupment that names the paid claim it takes back (supersedes_record_id; the snapshot
    # keeps that paid adjudication current as a step of the claim's chain) pairs with it; the rest pair by prescription chain.
    status_of = {rid: (status, code) for rid, pid, rx, n, ndc, ph, d, status, code in rows}
    for rid, prev in out.execute("""SELECT record_id, COALESCE(json_extract(payload,'$.reverses_record_id'),
                                                          json_extract(payload,'$.supersedes_record_id')) FROM record
                                    WHERE stream = 'payer_pharmacy_claims' AND COALESCE(json_extract(payload,'$.reverses_record_id'),
                                          json_extract(payload,'$.supersedes_record_id')) IS NOT NULL"""):
        st, code = status_of.get(rid, (None, None))
        if (st in ('reversed', 'recouped') or code == 'B2') and status_of.get(prev, (None,))[0] == 'paid' and prev not in used:
            used.add(prev); pairs.append((rid, prev))
    linked = {r for r, _ in pairs}
    for rid, pid, rx, n, ndc, ph, d, status, code in rows:
        if code != 'B2' and status != 'reversed' or rid in linked: continue
        cand = [(fd, frid) for fd, frid in paid.get((pid, rx, n, ndc, ph), []) if frid not in used and fd and d and fd <= d
                and _days(fd, d) is not None and _days(fd, d) <= 30]
        if not cand: unpaired += 1; continue
        fd, frid = max(cand)
        used.add(frid); pairs.append((rid, frid))
    out.executemany('UPDATE payer_pharmacy_claim SET reversal_of_record_id = ? WHERE record_id = ?', [(o, r) for r, o in pairs])
    out.executemany('UPDATE payer_pharmacy_claim SET reversed_by_record_id = ? WHERE record_id = ?', [(r, o) for r, o in pairs])
    out.execute("UPDATE payer_pharmacy_claim SET counts_as_fill = CASE WHEN claim_status = 'paid' AND coalesce(transaction_code, '') <> 'B2' "
                "AND reversed_by_record_id IS NULL THEN 1 ELSE 0 END")
    # Provider-side dispenses: a fill dispensed and later returned to stock (same fill_id) is not a fill either.
    returned = {fid for (fid,) in out.execute("SELECT json_extract(payload,'$.fill_id') FROM record WHERE stream = 'pharmacy_dispenses' "
                                              "AND json_extract(payload,'$.status') = 'returned_to_stock' AND json_extract(payload,'$.fill_id') IS NOT NULL")}
    disp = [rid for rid, fid in out.execute("SELECT record_id, json_extract(payload,'$.fill_id') FROM record WHERE stream = 'pharmacy_dispenses' "
                                            "AND json_extract(payload,'$.status') = 'dispensed'") if fid in returned]
    reversed_ids = [o for _, o in pairs] + disp
    out.execute('UPDATE medication SET fill_reversed = 0')
    out.executemany('UPDATE medication SET fill_reversed = 1 WHERE record_id = ?', [(r,) for r in reversed_ids])
    return {'b2_reversals_paired': len(pairs), 'b2_reversals_unpaired': unpaired, 'dispenses_returned_after_dispense': len(disp)}


def reversed_fill_ids(out):
    if not _has(out, 'payer_pharmacy_claim') or 'counts_as_fill' not in {r[1] for r in out.execute('PRAGMA table_info(payer_pharmacy_claim)')}:
        return set()
    ids = {r for (r,) in out.execute("SELECT record_id FROM payer_pharmacy_claim WHERE counts_as_fill = 0 AND claim_status = 'paid'")}
    return ids | {r for (r,) in out.execute('SELECT DISTINCT record_id FROM medication WHERE fill_reversed = 1')}


# ---- fuzzy order -> fill linkage ------------------------------------------------------------------------------------
_STRENGTH = re.compile(r'(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|unit|units|meq|%)\b')


def drug_key(name):
    """(ingredient words, strength) of a received drug name: 'losartan potassium 100 MG Oral Tablet' -> ('losartan', '100mg')."""
    s = re.sub(r'\[[^\]]*\]', ' ', str(name or '').lower())
    s = re.sub(r'^\s*\d+(?:\.\d+)?\s*(?:hr|actuat|ml)\b', ' ', s)  # leading package/duration qualifiers ('12 HR', '120 ACTUAT')
    m = _STRENGTH.search(s)
    strength = f'{float(m.group(1)):g}{m.group(2).rstrip("s")}' if m else None
    head = s[:m.start()] if m else s
    words = [w for w in re.findall(r'[a-z]+', head) if w not in SALTS and w not in FORM_WORDS and len(w) > 2]
    return (' '.join(words) or None, strength)


def _sigmoid(x): return 1 / (1 + math.exp(-x))


def _next_fill(order, chain):
    """First fill of the chain on or after the order date, for the order's ingredient, within FILL_LOOKAHEAD_DAYS (else None)."""
    for f in chain['fills']:
        if f[0] < order['date'] or f[3][0] != order['key'][0]: continue
        lag = _days(order['date'], f[0])
        return (f, lag) if lag is not None and lag <= FILL_LOOKAHEAD_DAYS else None
    return None


def score_pair(order, chain, npi_of):
    """(score, components, next fill) of an order against a pharmacy prescription chain; None when the pair is impossible.
    The order is matched to the chain's first fill on/after the order date: a new prescription starts a chain (fill number 0);
    a renewal continues an existing Rx number (payers keep the number across refills and renewals)."""
    nxt = _next_fill(order, chain)
    if not nxt: return None
    f, lag = nxt
    comp = {}
    if order['rxcui'] and f[4] and order['rxcui'] == f[4]: comp['drug'] = 4.0
    else: comp['drug'] = 3.5 if order['key'][1] == f[3][1] else 1.5
    npi = npi_of.get(order['clinician'])
    if npi and chain['prescriber']: comp['prescriber'] = 2.5 if npi == chain['prescriber'] else -1.5
    comp['timing'] = 2.0 if lag <= 3 else 1.0 if lag <= 14 else 0.0 if lag <= 30 else -1.5
    started_before = chain['first_date'] < order['date']
    comp['new_prescription'] = 1.0 if f[1] in (0, None) else (0.0 if started_before else -2.0)
    if order['pharmacy']:
        same = (order['pharmacy'] == chain['pharmacy_name'] or (order['pharmacy'] == 'mail order' and chain['mail']))
        comp['pharmacy'] = 1.5 if same else -0.5
    if order['days_supply'] and f[5]: comp['days_supply'] = 0.5 if order['days_supply'] == f[5] else 0.0
    return sum(comp.values()), comp, f


def link_prescriptions(orders, chains, npi_of):
    """Order -> fills by fuzzy matching. Each order takes its best-scoring chain (ties: shorter lag, chain id) when
    confidence = logistic(score - 5) >= LINK_THRESHOLD (drug + prescriber + fill within 3 days ~ 0.97; drug alone 30 days later
    ~ 0.18). Several orders may continue one chain (renewals): each order owns the chain's fills of its ingredient from its
    first fill until the next linked order on that chain, at most refills + 1 fills. A fill belongs to one order.
    Returns {order_id: (chain_id, confidence, components, [fills])}."""
    by_patient = defaultdict(list)
    for cid, ch in chains.items(): by_patient[ch['patient']].append(cid)
    best = {}
    for oid, o in orders.items():
        cands = []
        for cid in by_patient.get(o['patient'], ()):
            s = score_pair(o, chains[cid], npi_of)
            if s: cands.append((-s[0], _days(o['date'], s[2][0]), cid, s))
        if not cands: continue
        neg, lag, cid, s = min(cands)
        conf = _sigmoid(-neg - 5)
        if conf >= LINK_THRESHOLD: best[oid] = (cid, round(conf, 4), s[1], s[2])
    per_chain = defaultdict(list)
    for oid, (cid, conf, comp, first) in best.items(): per_chain[cid].append((first[0], orders[oid]['date'], oid))
    out, used = {}, set()
    for cid, linked in per_chain.items():
        linked.sort()
        for i, (first_date, odate, oid) in enumerate(linked):
            until = linked[i + 1][0] if i + 1 < len(linked) else None
            o = orders[oid]; limit = (o['refills'] + 1) if isinstance(o.get('refills'), int) else None
            fills = []
            for f in chains[cid]['fills']:
                if f[0] < first_date or f[3][0] != o['key'][0] or f[2] in used: continue
                if until and f[0] >= until: break
                fills.append(f); used.add(f[2])
                if limit and len(fills) >= limit: break
            if fills: out[oid] = (cid, best[oid][1], best[oid][2], fills)
    return out


def fill_chains(out, reversed_ids):
    """Pharmacy prescription chains: payer pharmacy claims grouped by (patient, pharmacy NPI, Rx number); counted fills only.
    Dispenses without an order link are grouped by their pharmacy prescription id."""
    ndc_rxcui = {}
    for ndc, rxcui in out.execute("SELECT json_extract(payload,'$.ndc'), json_extract(payload,'$.rxcui') FROM record "
                                  "WHERE stream IN ('pharmacy_dispenses','medication_orders','pharmacy_claims') "
                                  "AND json_extract(payload,'$.ndc') IS NOT NULL AND json_extract(payload,'$.rxcui') IS NOT NULL"):
        ndc_rxcui.setdefault(str(ndc), str(rxcui))
    chains = {}
    for rid, pid, d, ndc, name, ds, n, ph, phname, ptype, prescriber, rx in out.execute(
            """SELECT record_id, patient_id, fill_date, ndc, drug_name, days_supply, fill_number, pharmacy_npi, pharmacy_name, pharmacy_type,
                      prescriber_npi, prescription_number FROM payer_pharmacy_claim WHERE claim_status = 'paid' AND coalesce(counts_as_fill, 1) = 1"""):
        if rid in reversed_ids or not d or not rx: continue
        cid = f'payer:{pid}:{ph}:{rx}'
        ch = chains.setdefault(cid, {'patient': pid, 'fills': [], 'pharmacy_name': ' '.join(str(phname or '').lower().split()),
                                     'mail': ptype == 'mail_order_pharmacy', 'prescriber': prescriber, 'stream': 'payer_pharmacy_claims'})
        ch['fills'].append((d, n, rid, drug_key(name), ndc_rxcui.get(str(ndc)), ds))
    for rid, pid, d, rx, name, rxcui, ds in out.execute(
            """SELECT record_id, patient_id, event_date, json_extract(payload,'$.pharmacy_prescription_id'), json_extract(payload,'$.medication_name'),
                      json_extract(payload,'$.rxcui'), json_extract(payload,'$.days_supply_reported') FROM record
               WHERE stream = 'pharmacy_dispenses' AND json_extract(payload,'$.status') = 'dispensed'
                 AND json_extract(payload,'$.medication_order_record_id') IS NULL"""):
        if rid in reversed_ids or not d or not rx: continue
        ch = chains.setdefault(f'dispense:{pid}:{rx}', {'patient': pid, 'fills': [], 'pharmacy_name': None, 'mail': False,
                                                        'prescriber': None, 'stream': 'pharmacy_dispenses'})
        ch['fills'].append((d, None, rid, drug_key(name), str(rxcui) if rxcui else None, ds))
    for ch in chains.values():
        ch['fills'].sort(key=lambda f: (f[0], f[1] if f[1] is not None else -1, f[2]))
        first = ch['fills'][0]
        ch.update(first_date=first[0], first_fill_number=first[1], key=first[3], rxcui=first[4], days_supply=first[5])
    return chains


# ---- lists ----------------------------------------------------------------------------------------------------------
def _category(code):
    c = icd10(code) if code else None
    return c[:3] if c and ICD10_CATEGORY.match(c) else None


def _list_date(d, rec, av):
    return (d or rec or av or '')[:10] or None


def lists(out, as_of):
    stats = Counter()
    evidence = defaultdict(str)  # (patient, ICD-10 category) -> latest supporting diagnosis date
    sources = []
    if _has(out, 'payer_claim_diagnosis'):
        sources.append('SELECT patient_id, icd10, service_from FROM payer_claim_diagnosis WHERE counts_in_metrics = 1')
    sources.append('SELECT d.patient_id, d.icd10, v.event_date FROM encounter_diagnosis d JOIN visit v ON v.encounter_id = d.encounter_id')
    for sql in sources:
        for pid, code, d in out.execute(sql):
            cat = _category(code)
            if cat and d and d[:10] > evidence[(pid, cat)]: evidence[(pid, cat)] = d[:10]
    entries = defaultdict(list)
    for rid, pid, d, rec, av, desc, code, status, basis, onset, abate, stream in out.execute(
            """SELECT record_id, patient_id, event_date, recorded_date, available_date, coalesce(json_extract(payload,'$.description'),
                 json_extract(payload,'$.problem')), coalesce(json_extract(payload,'$.icd10'), json_extract(payload,'$.problem_code')),
                 json_extract(payload,'$.clinical_status'), json_extract(payload,'$.diagnosis_basis'),
                 coalesce(json_extract(payload,'$.onset_date'), json_extract(payload,'$.onset')), json_extract(payload,'$.abatement_date'), stream
               FROM record WHERE (stream = 'conditions' AND json_extract(payload,'$.diagnosis_basis') = 'ehr_problem_list') OR stream = 'problem_lists'"""):
        cat = _category(code)
        shorthand = None
        if not cat:
            shorthand = SHORTHAND.get(re.sub(r'[^A-Z0-9]', '', str(desc or '').upper()))
            cat = shorthand
        key = cat or ('label:' + ' '.join(str(desc or '').lower().split()))
        entries[(pid, key)].append((_list_date(d, rec, av) or '', rid, desc, icd10(code) if code else None, status, onset, abate, bool(code), bool(shorthand)))
    rows = []
    for (pid, key), es in entries.items():
        es.sort()
        last = es[-1]
        confirmed = last[0] or None
        ev = evidence.get((pid, key)) or None
        latest = max(x for x in (confirmed, ev) if x) if (confirmed or ev) else None
        since_c, since_e = _days(confirmed, as_of) if confirmed else None, _days(ev, as_of) if ev else None
        since = _days(latest, as_of) if latest else None
        acute = int(any(key.startswith(p) for p in ACUTE_PREFIXES))
        status = last[4] or 'active'
        resolved = next((x[6] for x in reversed(es) if x[6]), None)
        stale = int(status == 'active' and (since is None or since > STALE_DAYS or (acute and since_c is not None and since_c > 90)))
        stats['stale'] += stale; stats['entries'] += len(es); stats['duplicates'] += len(es) - 1
        rows.append((pid, key, next((x[3] for x in reversed(es) if x[3]), None), last[2], len(es), sum(x[7] for x in es), sum(x[8] for x in es),
                     es[0][0] or None, confirmed, status, resolved, next((x[5] for x in es if x[5]), None), ev, since_c, since_e, acute, stale,
                     round(0.5 ** (since / 365), 4) if since is not None and since >= 0 else None, last[1]))
    out.executemany('INSERT INTO problem_list_entry VALUES (' + ','.join('?' * 19) + ')', rows)
    stats['problems'] = len(rows)
    # Medication list vs fills and orders.
    fills, orders = defaultdict(str), defaultdict(str)
    reversed_ids = reversed_fill_ids(out)
    for rid, pid, ev, name, d in out.execute("SELECT DISTINCT record_id, patient_id, evidence, drug_name, event_date FROM medication "
                                             "WHERE evidence IN ('dispensed','claim','payer_claim','ordered') AND coalesce(status,'') NOT IN "
                                             "('rejected','reversed','returned_to_stock','prepared','cancelled','entered_in_error')"):
        if rid in reversed_ids or not d: continue
        k = (pid, drug_key(name)[0])
        target = orders if ev == 'ordered' else fills
        if d[:10] > target[k]: target[k] = d[:10]
    listed = defaultdict(list)
    for rid, pid, d, rec, av, name, rxcui, status, src in out.execute(
            """SELECT record_id, patient_id, event_date, recorded_date, available_date, json_extract(payload,'$.name'),
                      json_extract(payload,'$.rxcui'), json_extract(payload,'$.list_status'), json_extract(payload,'$.source')
               FROM record WHERE stream = 'medication_lists'"""):
        k = drug_key(name)[0]
        if not k: continue
        listed[(pid, k)].append((_list_date(d, rec, av) or '', rid, name, rxcui, status, src))
    rows = []
    for (pid, k), es in listed.items():
        es.sort(); last = es[-1]
        status = last[4] or 'active'
        lf, lo = fills.get((pid, k)) or None, orders.get((pid, k)) or None
        since_l = _days(last[0], as_of) if last[0] else None
        since_f = _days(lf, as_of) if lf else None
        otc = int(bool(OTC.search(str(last[2] or ''))))
        disc = None
        if status == 'active' and not otc and (since_f is None or since_f > 180): disc = 'listed_active_no_fill_180d'
        elif status in ('discontinued', 'historical_inactive') and lf and last[0] and lf > last[0]: disc = 'listed_discontinued_still_filled'
        latest = max(x for x in (last[0] or None, lf) if x) if (last[0] or lf) else None
        since = _days(latest, as_of) if latest else None
        stale = int(status == 'active' and (since is None or since > STALE_DAYS))
        stats['med_list_' + (disc or 'consistent')] += 1
        rows.append((pid, k, last[2], last[3], status, last[5], len(es), es[0][0] or None, last[0] or None, lf, lo, otc, since_l, since_f,
                     disc, stale, round(0.5 ** (since / 365), 4) if since is not None and since >= 0 else None, last[1]))
    with_list = {pid for pid, _ in listed}
    for (pid, k), lf in fills.items():  # omission: filled in the last 180 days, never on the patient's list
        if pid in with_list and (pid, k) not in listed and k and _days(lf, as_of) is not None and _days(lf, as_of) <= 180:
            rows.append((pid, k, None, None, None, None, 0, None, None, lf, orders.get((pid, k)) or None, 0, None, _days(lf, as_of),
                         'filled_not_listed', 0, round(0.5 ** (max(_days(lf, as_of), 0) / 365), 4), None))
            stats['med_list_filled_not_listed'] += 1
    out.executemany('INSERT OR IGNORE INTO medication_list_entry VALUES (' + ','.join('?' * 18) + ')', rows)
    # Allergies.
    allergies = defaultdict(list)
    for rid, pid, d, rec, av, sub, disp, reaction, coded, nka, status in out.execute(
            """SELECT record_id, patient_id, event_date, recorded_date, available_date, json_extract(payload,'$.substance'),
                      json_extract(payload,'$.display'), json_extract(payload,'$.reaction'), json_extract(payload,'$.coded'),
                      json_extract(payload,'$.no_known_allergies'), json_extract(payload,'$.status') FROM record WHERE stream = 'allergies'"""):
        raw = sub or disp or ''
        m = re.match(r'^\s*(.*?)\s*(?:\(([^)]*)\)|[-—–]\s*(.+))?\s*$', raw)
        name, parsed = (m.group(1), m.group(2) or m.group(3)) if m else (raw, None)
        name = re.sub(r'\s*\((?:substance|finding|organism|product)\)\s*$', '', name, flags=re.I).strip().lower()
        nkda = int(bool(nka) or name in ('nkda', 'nka', 'no known allergies', 'no known drug allergies'))
        if nkda: name = 'no known allergies'
        allergies[(pid, name)].append((_list_date(d, rec, av) or '', rid, raw, reaction or parsed, coded, nkda, status))
    rows = []
    for (pid, name), es in allergies.items():
        es.sort(); last = es[-1]
        reaction = next((x[3] for x in reversed(es) if x[3]), None)
        free = int(not any(x[4] for x in es))
        rows.append((pid, name, last[2], reaction, int(reaction is None and not last[5]), free, last[5], last[6] or 'active', len(es),
                     es[0][0] or None, last[0] or None, _days(last[0], as_of) if last[0] else None, last[1]))
        stats['allergy_free_text'] += free; stats['allergy_reaction_missing'] += int(reaction is None and not last[5])
    out.executemany('INSERT INTO allergy_entry VALUES (' + ','.join('?' * 13) + ')', rows)
    # Structured smoking status.
    smoke = defaultdict(list)
    for rid, pid, d, rec, av, status, snomed, legacy in out.execute(
            """SELECT record_id, patient_id, event_date, recorded_date, available_date, json_extract(payload,'$.smoking_status'),
                      json_extract(payload,'$.smoking_status_snomed'), json_extract(payload,'$.smoking') FROM record WHERE stream = 'social_history'"""):
        received = status or legacy
        if received: smoke[pid].append((_list_date(d, rec, av) or '', rid, received, snomed))
    rows = []
    for pid, es in smoke.items():
        when, rid, received, snomed = max(es); n = len(es)
        low = str(received).lower()
        status = ('never' if 'never' in low else 'former' if re.search(r'former|ex-|quit|sober', low) else
                  'current' if re.search(r'current|every day|some day|ppd|smoker|cigarettes/day', low) else 'unknown')
        rows.append((pid, status, received, snomed, when or None, _days(when, as_of) if when else None, n, rid))
    out.executemany('INSERT INTO smoking_status VALUES (?,?,?,?,?,?,?,?)', rows)
    stats['smoking_status_patients'] = len(rows)
    return dict(stats)


# ---- ADT and utilization --------------------------------------------------------------------------------------------
def adt_visits(out):
    msgs = defaultdict(list)
    for rid, pid, d, org, et, avt, av, trig, cls, visit, disp, reason in out.execute(
            """SELECT record_id, patient_id, event_date, organization_id, json_extract(payload,'$.event_time'),
                      json_extract(payload,'$.available_time'), available_date, json_extract(payload,'$.trigger_event'),
                      json_extract(payload,'$.patient_class'), coalesce(json_extract(payload,'$.visit_number'), json_extract(payload,'$.encounter_id')),
                      json_extract(payload,'$.discharge_disposition'), json_extract(payload,'$.admit_reason')
               FROM record WHERE stream = 'adt_messages'"""):
        if visit and d: msgs[(pid, visit)].append((et or d, avt or av or '', rid, trig, cls, d[:10], disp, reason, org))
    rows, stats = [], Counter()
    for (pid, visit), ms in msgs.items():
        by_event = sorted(ms, key=lambda m: (m[0], m[2]))
        by_arrival = sorted(ms, key=lambda m: (m[1], m[2]))
        admits = [m for m in by_event if m[3] in ('A01', 'A04', 'A05', 'A06')]
        disch = [m for m in by_event if m[3] == 'A03']
        first = admits[0] if admits else by_event[0]
        admit_date = first[5]
        discharge = disch[-1][5] if disch else None
        before = int(bool(discharge and discharge < admit_date))
        if before: admit_date, discharge = discharge, admit_date
        out_of_order = int([m[2] for m in by_event] != [m[2] for m in by_arrival])
        a08 = sum(1 for m in ms if m[3] == 'A08')
        stats['visits'] += 1; stats['missing_discharge'] += not disch; stats['out_of_order'] += out_of_order; stats['a08_updates'] += a08
        rows.append((visit, pid, first[8], first[4], by_event[-1][4], admit_date, discharge, int(bool(disch)), disch[-1][6] if disch else None,
                     first[7], len(ms), a08, out_of_order, before, int(not admits), first[2]))
    out.executemany('INSERT OR REPLACE INTO adt_visit VALUES (' + ','.join('?' * 16) + ')', rows)
    return dict(stats)


def _attach(events, cand, kind_ok, tol=MATCH_DAYS):
    """Index of an existing event of a compatible kind whose date window holds cand's date (within tol days), else None."""
    for i, e in enumerate(events):
        if not kind_ok(e['kind'], cand['kind']) or cand['source'] in e['sources']: continue
        lo, hi = e['event_date'], e['end_date'] or e['event_date']
        d = cand['event_date']
        a, b = _days(lo, d), _days(hi, d)
        if a is not None and b is not None and -tol <= a and b <= tol: return i
    return None


def utilization(out):
    """utilization_event plus encounter/stay rows for events that only ADT or payer claims carry."""
    _add(out, 'stay', 'stay_source TEXT')
    out.execute("UPDATE stay SET stay_source = 'encounters' WHERE stay_source IS NULL")
    ed = defaultdict(list); adm = defaultdict(list)
    for pid, d, rid, org, encs in out.execute(
            "SELECT patient_id, substr(event_date,1,10), min(record_id), min(organization_id), "
            "group_concat(DISTINCT CASE WHEN source_table <> 'encounters' THEN encounter_id END) FROM encounter "
            "WHERE type_concept = 'emergency_visit' AND event_date IS NOT NULL GROUP BY 1, 2"):
        ed[pid].append({'kind': 'ed_visit', 'event_date': d, 'end_date': None, 'sources': {'ehr'}, 'records': 1, 'record_id': rid,
                        'organization_id': org, 'in_ehr': 1, 'discharge_known': None, 'source': 'ehr',
                        'encounters': set((encs or '').split(',')) - {''}, 'claims': []})
    for rowid, pid, a, dis, typ, org, known, rd, gap in out.execute(
            'SELECT rowid, patient_id, admit_date, discharge_date, type, organization_id, discharge_known, readmitted_30d, days_to_readmission FROM stay'):
        kind = 'observation' if str(typ or '').lower() == 'observation' else 'inpatient'
        adm[pid].append({'kind': kind, 'event_date': (a or '')[:10], 'end_date': (dis or '')[:10] or None, 'sources': {'ehr'}, 'records': 1,
                         'record_id': None, 'organization_id': org, 'in_ehr': 1, 'discharge_known': known, 'source': 'ehr', 'stay_rowid': rowid,
                         'readmitted': rd, 'gap': gap})
    cands = []
    for visit, pid, org, cls, final, a, dis, known, rid in out.execute(
            'SELECT visit_number, patient_id, organization_id, patient_class, final_patient_class, admit_date, discharge_date, discharge_known, record_id FROM adt_visit'):
        classes = {str(cls or '').upper()[:1], str(final or '').upper()[:1]}
        if 'E' in classes: cands.append(('ed', pid, {'kind': 'ed_visit', 'event_date': a, 'end_date': None, 'source': 'adt', 'record_id': rid,
                                                     'organization_id': org, 'discharge_known': None, 'visit': visit}))
        if classes & {'I', 'O'}:
            cands.append(('adm', pid, {'kind': 'inpatient' if 'I' in classes else 'observation', 'event_date': a, 'end_date': dis if known else None,
                                       'source': 'adt', 'record_id': rid, 'organization_id': org, 'discharge_known': known, 'visit': visit}))
    first_ed_claim, same_visit_claims = {}, defaultdict(list)
    if _has(out, 'payer_claim'):
        ed_rev = defaultdict(set)
        for cid, rev in out.execute('SELECT payer_claim_id, revenue_code FROM payer_claim_line WHERE revenue_code IS NOT NULL'):
            ed_rev[cid].add(str(rev))
        from .ed_definition import ED_CPT  # the governed ED definition also accepts an ED E/M CPT 99281-99285 on a line
        ed_cpt = {r[0] for r in out.execute(f"SELECT DISTINCT payer_claim_id FROM payer_claim_line WHERE cpt IN ({','.join('?' * len(ED_CPT))})", ED_CPT)} \
            if 'cpt' in {r[1] for r in out.execute('PRAGMA table_info(payer_claim_line)')} else set()
        seen = set()
        for cid, pid, ctype, pos, tob, svc, svc_to, adm_d, dis_d, rid, org in out.execute(
                """SELECT payer_claim_id, patient_id, claim_type, place_of_service, type_of_bill, service_from, service_to, admission_date,
                          discharge_date, record_id, billing_npi FROM payer_claim
                   WHERE counts_in_metrics = 1 AND final_status <> 'voided' AND service_from IS NOT NULL
                   ORDER BY service_from, payer_claim_id"""):
            revs = ed_rev.get(cid, set())
            base = {'source': 'claim', 'record_id': rid, 'organization_id': org, 'discharge_known': None, 'visit': cid}
            if (ctype == 'professional' and str(pos) == '23') or any(ED_REVENUE.match(r) for r in revs) or cid in ed_cpt:
                if (pid, 'ed', svc[:10]) not in seen:
                    seen.add((pid, 'ed', svc[:10])); cands.append(('ed', pid, {**base, 'kind': 'ed_visit', 'event_date': svc[:10], 'end_date': None}))
                    first_ed_claim[(pid, svc[:10])] = cid
                else:  # a second claim of the same ED visit (professional + facility): its diagnoses / status belong to it too
                    same_visit_claims[first_ed_claim[(pid, svc[:10])]].append(cid)
            if str(tob or '').startswith('011'):
                a = (adm_d or svc)[:10]; dis = (dis_d or svc_to or '')[:10] or None
                if (pid, 'adm', a) not in seen:
                    seen.add((pid, 'adm', a)); cands.append(('adm', pid, {**base, 'kind': 'inpatient', 'event_date': a, 'end_date': dis,
                                                                         'discharge_known': int(bool(dis_d or svc_to))}))
            elif any(OBS_REVENUE.match(r) for r in revs) and (pid, 'adm', svc[:10]) not in seen:
                seen.add((pid, 'adm', svc[:10])); cands.append(('adm', pid, {**base, 'kind': 'observation', 'event_date': svc[:10],
                                                                            'end_date': (svc_to or '')[:10] or None, 'discharge_known': int(bool(svc_to))}))
    order = {'adt': 0, 'claim': 1}
    cands.sort(key=lambda c: (order[c[2]['source']], c[2]['event_date'] or ''))
    stats = Counter()
    for kind, pid, c in cands:
        if not c['event_date']: continue
        events = ed[pid] if kind == 'ed' else adm[pid]
        i = _attach(events, c, (lambda a, b: True))
        if i is None:
            events.append({**c, 'sources': {c['source']}, 'records': 1, 'in_ehr': 0, 'encounters': {c.get('visit')} - {None},
                           'claims': [c['visit']] if c['source'] == 'claim' else []})
            stats[f'{kind}_from_{c["source"]}'] += 1
        else:
            e = events[i]; e['sources'].add(c['source']); e['records'] += 1
            if c['source'] == 'claim': e.setdefault('claims', []).append(c['visit'])
            elif c.get('visit') and e.get('source') != 'ehr': e.setdefault('encounters', set()).add(c['visit'])
            if not e['end_date'] and c.get('end_date'): e['end_date'] = c['end_date']; e['discharge_known'] = c['discharge_known']
            stats[f'{kind}_{c["source"]}_matched'] += 1
    rows, enc_rows, stay_rows, stay_updates = [], [], [], []
    for pid, events in ed.items():
        for e in events:
            rows.append((pid, 'ed_visit', e['event_date'], None, None, ','.join(sorted(e['sources'])), e['in_ehr'], e['records'], e['record_id'],
                         e['organization_id'], None, None))
            if not e['in_ehr']:
                st = 'adt_messages' if e['source'] == 'adt' else 'payer_medical_claims'
                enc_rows.append((e['record_id'], pid, st, e.get('visit'), 'emergency (' + ('ADT' if st == 'adt_messages' else 'claim') + ')',
                                 'emergency_visit', 'completed', e['event_date'], None, None, e['organization_id'], None, None))
    for pid, events in adm.items():
        events.sort(key=lambda e: e['event_date'])
        merged = not all(e['in_ehr'] for e in events)  # EHR-only patients keep the stay table's own readmission flags
        for i, e in enumerate(events):
            if merged:
                nxt = events[i + 1] if i + 1 < len(events) else None
                gap = _days(e['end_date'], nxt['event_date']) if nxt and e['end_date'] else None
                e['readmitted'] = int(gap is not None and 0 <= gap <= READMIT_DAYS)
                e['gap'] = gap if e['readmitted'] else None
            rows.append((pid, e['kind'], e['event_date'], e['end_date'], e['discharge_known'], ','.join(sorted(e['sources'])), e['in_ehr'],
                         e['records'], e['record_id'], e['organization_id'], e['readmitted'], e['gap']))
            if e['in_ehr']:
                if merged: stay_updates.append((e['readmitted'], e['gap'], e['stay_rowid']))
            else:
                known = int(bool(e['end_date']))
                stay_rows.append((pid, e['event_date'], e['end_date'], e['kind'], e['organization_id'], e['readmitted'], e['gap'], known,
                                  ('adt' if e['source'] == 'adt' else 'payer_claim') if known else None, None,
                                  _days(e['event_date'], e['end_date']) if known else None, 'adt' if e['source'] == 'adt' else 'payer_claim'))
    out.executemany('INSERT INTO utilization_event VALUES (' + ','.join('?' * 12) + ')', rows)
    out.executemany('INSERT INTO encounter VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', enc_rows)
    stats.update(_ed_encounter_details(out, ed, same_visit_claims))
    out.executemany('UPDATE stay SET readmitted_30d = ?, days_to_readmission = ? WHERE rowid = ?', stay_updates)
    out.executemany('INSERT INTO stay(patient_id, admit_date, discharge_date, type, organization_id, readmitted_30d, days_to_readmission, '
                    'discharge_known, discharge_date_source, discharge_time_utc, los_days, stay_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', stay_rows)
    stats.update(events=len(rows), encounters_added=len(enc_rows), stays_added=len(stay_rows))
    return dict(stats)


def _ed_encounter_details(out, ed, same_visit_claims=None):
    """Diagnoses and disposition for ED encounters that have no EHR chart (ADT and claim-derived encounter rows).

    encounter_diagnosis (source payer_claim): the diagnoses of every payer ED claim the visit matched (utilization above) go on
    each of the visit's ADT / claim encounter ids; codes the encounter already carries (HL7 DG1) are not repeated. EHR
    encounters keep their charted diagnoses only. encounter.disposition (ED rows still NULL): admitted / observation when
    the ADT visit became an inpatient admission (admission / observation_start event of the same visit, or final patient
    class I / O), else the ADT discharge disposition, else a matched claim's patient discharge status."""
    if not _has(out, 'encounter_diagnosis'): return {}
    have = defaultdict(set)
    for enc, code in out.execute("SELECT encounter_id, icd10 FROM encounter_diagnosis WHERE encounter_id IN "
                                 "(SELECT encounter_id FROM encounter WHERE type_concept = 'emergency_visit' AND source_table <> 'encounters')"):
        have[enc].add(code)
    dx, status, claim_rid = defaultdict(list), {}, {}
    if _has(out, 'payer_claim_diagnosis'):
        for cid, rank, code in out.execute('SELECT payer_claim_id, rank, icd10 FROM payer_claim_diagnosis WHERE coalesce(counts_in_metrics, 1) = 1 '
                                           'AND icd10 IS NOT NULL ORDER BY payer_claim_id, rank'):
            dx[cid].append((rank, code))
    if _has(out, 'payer_claim'):
        claim_rid = {}
        for cid, rid, st in out.execute('SELECT payer_claim_id, record_id, discharge_status FROM payer_claim'):
            claim_rid[cid] = rid
            if st not in (None, ''): status[cid] = st
    adt_disp = {}
    for visit, ev in out.execute("SELECT encounter_id, json_extract(r.payload, '$.event') FROM encounter e JOIN record r ON r.record_id = e.record_id "
                                 "WHERE e.source_table = 'adt' AND json_extract(r.payload, '$.event') IN ('admission', 'observation_start')"):
        if visit and adt_disp.get(visit) != 'admitted': adt_disp[visit] = 'admitted' if ev == 'admission' else 'observation'
    if _has(out, 'adt_visit'):
        cols = {r[1] for r in out.execute('PRAGMA table_info(adt_visit)')}
        disp = 'discharge_disposition' if 'discharge_disposition' in cols else 'NULL'
        for visit, final, dd in out.execute(f'SELECT visit_number, final_patient_class, {disp} FROM adt_visit'):
            cls = str(final or '').upper()[:1]
            if visit in adt_disp: continue
            if cls == 'I': adt_disp[visit] = 'admitted'
            elif cls == 'O': adt_disp[visit] = 'observation'
            elif dd: adt_disp[visit] = dd
    for visit, d in out.execute("SELECT encounter_id, max(disposition) FROM encounter WHERE source_table = 'adt' AND disposition IS NOT NULL GROUP BY 1"):
        adt_disp.setdefault(visit, d)
    rows, disp_rows = [], []
    for pid, events in ed.items():
        for e in events:
            encs = e.get('encounters') or set()
            claims = sorted({x for c in e.get('claims') or [] for x in [c, *(same_visit_claims or {}).get(c, [])]})
            merged, seen = [], set()
            for cid in claims:
                for rank, code in dx.get(cid, []):
                    if code not in seen: seen.add(code); merged.append((cid, code))
            for enc in encs:
                for n, (cid, code) in enumerate(merged, 1):
                    if code in have[enc]: continue
                    have[enc].add(code)
                    rows.append((enc, pid, n, code, 'principal' if n == 1 else 'secondary', claim_rid.get(cid) or cid, 'payer_claim', e['event_date']))
                d = adt_disp.get(enc) or next((status[c] for c in claims if c in status), None)
                if d: disp_rows.append((d, enc))
    out.executemany('INSERT INTO encounter_diagnosis(encounter_id, patient_id, rank, icd10, type, record_id, source, event_date) '
                    'VALUES (?,?,?,?,?,?,?,?)', rows)
    out.executemany("UPDATE encounter SET disposition = ? WHERE encounter_id = ? AND type_concept = 'emergency_visit' "
                    "AND source_table <> 'encounters' AND disposition IS NULL", disp_rows)
    return {'ed_claim_diagnoses_added': len(rows), 'ed_dispositions_set': len(disp_rows)}


# ---- note-only therapy decisions ------------------------------------------------------------------------------------
THERAPY_EXTRACTOR = 'therapy-rules/1'
SOUND_ALIKES = {'lysine april': 'lisinopril', 'lie sign of pril': 'lisinopril', 'lasik': 'lasix', 'metal formin': 'metformin',
                'met forming': 'metformin', 'atorva staten': 'atorvastatin', 'simva staten': 'simvastatin', 'disney': 'dyspnea',
                'amlo deeping': 'amlodipine', 'car that a lol': 'carvedilol'}
_SOUND = re.compile(r'\b(' + '|'.join(re.escape(k) for k in sorted(SOUND_ALIKES, key=len, reverse=True)) + r')\b', re.I)
CLASS_WORDS = {'antihypertensive': 'antihypertensive', 'ssri': 'ssri', 'statin': 'statin', 'statin therapy': 'statin', 'insulin': 'insulin',
               'diuretic': 'loop_diuretic', 'beta blocker': 'beta_blocker', 'ace inhibitor': 'ace_inhibitor', 'anticoagulation': 'anticoagulant'}
SERVICES = re.compile(r'vaccin|colonoscop|mammogra|sleep study|referral|physical therapy|counsel|screening|imaging|ct\b|mri\b', re.I)
_DRUG = r'(?P<drug>[a-z][a-z/\-]*(?: [a-z][a-z/\-]*){0,3}?)'
THERAPY_PATTERNS = [
    ('declined', re.compile(r'\bdiscussed (?:starting |initiating )?' + _DRUG + r'\s*(?:therapy)?\s*[;,]\s*(?:the )?(?:patient|pt) declines?\b', re.I)),
    ('declined', re.compile(r'\b(?:patient|pt) (?:declines?|refuses?)\s+(?P<drug>medication|medications|treatment|' + _DRUG[8:-1] + r')\b', re.I)),
    ('stop', re.compile(r'\b(?:stop|discontinue|d/c|hold)\s+' + _DRUG + r'(?=\s+\d|\s+due\b|\s*[;,.]|\s*$)(?:[^;]*?\bdue to (?P<reason>[^;.]+))?', re.I)),
    ('dose_increase', re.compile(r'\b(?:increase|titrate up|uptitrate)\s+' + _DRUG + r'(?=\s+\d|\s+dose\b|\s+x\s|\s*[;,.]|\s*$)', re.I)),
    ('dose_decrease', re.compile(r'\b(?:decrease|reduce|titrate down)\s+' + _DRUG + r'(?=\s+\d|\s+dose\b|\s+x\s|\s*[;,.]|\s*$)', re.I)),
    ('start', re.compile(r'(?<!discussed )\b(?:start|initiate|begin)\s+' + _DRUG + r'(?=\s+\d)', re.I)),
]
NOT_DRUG = re.compile(r'^(?:the|a|an|current|dose|antibiotics?|to|it|this|all|home|at)$', re.I)


_VOCAB = []


def drug_vocabulary(extra=()):
    """Ingredient keys a therapy target must name (normalization dictionary ingredients + received drug names)."""
    if not _VOCAB:
        words = set()
        f = Path(__file__).resolve().parents[3] / 'config' / 'agere' / 'normalization' / 'medications.json'
        if f.is_file():
            for name, v in json.loads(f.read_text())['entries'].items():
                words.add(drug_key(name)[0]); words.update(i.lower() for i in v.get('ingredients') or [])
        _VOCAB.append({w for w in words if w})
    return _VOCAB[0] | set(extra)


def therapy_mentions(text, vocab=None):
    """[(action, target, target_kind, drug_class, reason, evidence, sound_alike_corrected)] in one note text. A target must be a
    drug (ingredient in the vocabulary or a classified ingredient), a drug-class word, a service (declined only) or the
    generic 'medication'; anything else ('lower than', 'order established') is not a therapy decision."""
    from .drug_classes import classify
    vocab = drug_vocabulary() if vocab is None else vocab
    fixed = _SOUND.sub(lambda m: SOUND_ALIKES[m.group(1).lower()], text)
    corrected = int(fixed != text)
    out, seen = [], set()
    for seg in re.split(r'[\n|]|(?<=\.)\s+', fixed):
        for action, rx in THERAPY_PATTERNS:
            for m in rx.finditer(seg):
                target = ' '.join(m.group('drug').lower().split()).strip(' -/')
                target = re.sub(r'\s+(?:dose|therapy|tablets?|to|by|from|and)$', '', target)
                if not target or NOT_DRUG.match(target): continue
                if (action, target) in seen: continue
                seen.add((action, target))
                classes = classify(target)
                cls = classes[0] if classes else next((v for k, v in CLASS_WORDS.items() if re.search(r'\b' + k + r'\b', target)), None)
                generic = target in ('medication', 'medications', 'treatment')
                service = bool(SERVICES.search(target))
                known = bool(classes) or drug_key(target)[0] in vocab
                if not (known or cls or generic or (service and action == 'declined')): continue
                kind = 'service' if service else 'drug' if known else 'drug_class' if cls else 'unspecified'
                reason = m.groupdict().get('reason')
                out.append((action, target, kind, cls, reason.strip() if reason else None, seg.strip()[:300], corrected))
    return out


def therapy_changes(out):
    fills = defaultdict(list)
    reversed_ids = reversed_fill_ids(out)
    for rid, pid, name, d in out.execute("SELECT DISTINCT record_id, patient_id, drug_name, event_date FROM medication WHERE evidence IN "
                                         "('dispensed','claim','payer_claim') AND coalesce(status,'') IN ('dispensed','paid','completed')"):
        if rid not in reversed_ids and d: fills[(pid, drug_key(name)[0])].append(d[:10])
    listed = {(pid, k): (st, last) for pid, k, st, last in out.execute('SELECT patient_id, drug_key, list_status, last_listed FROM medication_list_entry')}
    orders = defaultdict(list)
    for rid, pid, enc, d, name in out.execute("SELECT record_id, patient_id, json_extract(payload,'$.encounter_id'), event_date, "
                                              "json_extract(payload,'$.medication_name') FROM record WHERE stream = 'medication_orders'"):
        orders[(pid, drug_key(name)[0])].append((enc, (d or '')[:10], rid))
    vocab = drug_vocabulary(k for (name,) in out.execute('SELECT DISTINCT drug_name FROM medication') for k in [drug_key(name)[0]] if k)
    sources = [('notes', "SELECT record_id, patient_id, event_date, json_extract(payload,'$.encounter_id'), json_extract(payload,'$.text') "
                         "FROM record WHERE stream = 'notes'")]
    if _has(out, 'scanned_document_text'):
        sources.append(('scanned_documents', 'SELECT t.record_id, t.patient_id, d.event_date, NULL, t.text FROM scanned_document_text t '
                                             'JOIN scanned_document d USING(record_id)'))
    rows, stats = [], Counter()
    for stream, sql in sources:
        for rid, pid, d, enc, text in out.execute(sql).fetchall():
            if not isinstance(text, str) or not text: continue
            dictated = int(bool(re.search(r'dictated', text, re.I)))
            for action, target, kind, cls, reason, ev, corrected in therapy_mentions(text, vocab):
                key = drug_key(target)[0] if kind == 'drug' else None
                day = (d or '')[:10]
                after = [f for f in fills.get((pid, key), []) if day and day < f <= (date.fromisoformat(day) + timedelta(days=180)).isoformat()] if key and day else []
                st = listed.get((pid, key))
                list_active = int(bool(st and st[0] == 'active')) if action == 'stop' and key and day else None  # the list still shows it active
                order = next((o for e, od, o in orders.get((pid, key), []) if (enc and e == enc) or (od == day)), None) if action == 'start' and key else None
                rows.append((rid, pid, day or None, enc, stream, action, target, kind, key, cls, reason, dictated, corrected, ev, order,
                             len(after) if action == 'stop' and key else None, list_active, THERAPY_EXTRACTOR))
                stats[action] += 1
    out.executemany('INSERT INTO note_therapy_change VALUES (' + ','.join('?' * 18) + ')', rows)
    _add(out, 'clinical_note', 'dictated INTEGER', 'dictation_unreviewed INTEGER')
    out.execute("""UPDATE clinical_note SET (dictated, dictation_unreviewed) = (SELECT instr(lower(json_extract(r.payload,'$.text')), 'dictated') > 0,
                     instr(lower(json_extract(r.payload,'$.text')), 'dictated but not read') > 0 FROM record r WHERE r.record_id = clinical_note.record_id)""")
    stats['stops_with_fills_after'] = sum(1 for r in rows if r[5] == 'stop' and r[15])
    stats['stops_list_still_active'] = sum(1 for r in rows if r[5] == 'stop' and r[16])
    stats['dictated_notes'] = out.execute('SELECT count(*) FROM clinical_note WHERE dictated = 1').fetchone()[0]
    return dict(stats)


# ---- outside documents ----------------------------------------------------------------------------------------------
DOS_WINDOW_DAYS = 60  # a shared PDF's text belongs to the sharing record nearest its 'Date of service', within 60 days


def _document_texts(path):
    """{record_id: (status, reason, text, pdf_date_of_service, records_sharing_file, relative_path)} from the build's warehouse."""
    texts = {}
    if path is None: return texts
    source = Path(path).parent / 'warehouse.sqlite3'
    try: release = json.loads((Path(path) / 'run.json').read_text())['snapshot']['release']
    except (OSError, KeyError, ValueError): return texts
    if not source.is_file(): return texts
    db = sqlite3.connect(f'file:{source}?mode=ro', uri=True)
    try:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'source_document_text'").fetchone():
            for row in db.execute('SELECT record_id, status, reason, text, pdf_date_of_service, records_sharing_file, relative_path '
                                  'FROM source_document_text WHERE release = ?', (release,)):
                texts[row[0]] = row[1:]
    finally: db.close()
    return texts


def documents(out, path=None):
    texts = _document_texts(path)
    recs = []
    for rid, pid, d, av, p in out.execute("SELECT record_id, patient_id, event_date, available_date, payload FROM record "
                                          "WHERE stream = 'scanned_documents' ORDER BY patient_id, event_date, record_id"):
        recs.append((rid, pid, d, av, json.loads(p)))
    # A PDF file named by several records holds one document: its text goes to the sharing record (that expects a text layer)
    # whose date is nearest the PDF's own date of service; the others cannot use it.
    owner = {}
    by_file = defaultdict(list)
    for rid, pid, d, av, r in recs:
        t = texts.get(rid)
        if t and t[0] == 'text_layer' and r.get('has_text_layer'): by_file[t[5]].append((rid, d))
    for rel, sharing in by_file.items():
        dos = texts[sharing[0][0]][3]
        if len(sharing) == 1 and (texts[sharing[0][0]][4] or 1) == 1: owner[rel] = sharing[0][0]; continue
        if not dos: owner[rel] = None; continue
        near = [(abs(_days(dos, d)), rid) for rid, d in sharing if d and _days(dos, d) is not None]
        near = [x for x in near if x[0] <= DOS_WINDOW_DAYS]
        owner[rel] = min(near)[1] if near else False
    rows, text_rows, stats = [], [], Counter()
    first_seen = {}
    for rid, pid, d, av, r in recs:
        status, reason, pdf_text, dos, sharing, rel = texts.get(rid, (None,) * 6)
        own_layer = bool(r.get('has_text_layer'))
        source = text = unreadable = None
        if r.get('ocr_text'): source, text = 'front_office_ocr', r['ocr_text']
        elif not own_layer: unreadable = 'image_only_no_ocr'
        elif status == 'text_layer':
            who = owner.get(rel)
            if who == rid: source, text = 'pdf_text_layer', pdf_text
            elif who is None: unreadable = 'file_unverified_shared'
            else: unreadable = 'file_belongs_to_another_record'
        elif status in ('missing', 'checksum_mismatch'): unreadable = 'file_missing'
        elif status == 'image_only': unreadable = 'file_belongs_to_another_record'  # the record expects a text layer
        elif status is None: unreadable = 'file_not_read'
        else: unreadable = reason or 'no_text'
        key = (pid, text) if text and len(text) >= 200 else (pid, r.get('document_title'), d, text)  # same fax sent twice
        dup = first_seen.get(key) if text else None
        if text and dup is None: first_seen[key] = rid
        stats[source or ('unreadable:' + unreadable)] += 1
        rows.append((rid, pid, r.get('document_id'), r.get('document_type'), r.get('document_title'), r.get('received_via'), d, av,
                     r.get('page_count'), int(own_layer), r.get('review_status'), r.get('signature_status'), r.get('sending_facility_text'),
                     status, source, int(text is not None), unreadable, len(text or ''), sharing, dos, dup))
        if text: text_rows.append((rid, pid, source, text))
    out.executemany('INSERT INTO scanned_document VALUES (' + ','.join('?' * 21) + ')', rows)
    out.executemany('INSERT INTO scanned_document_text VALUES (?,?,?,?)', text_rows)
    return dict(stats)


def build_claims(out):
    """Claim versions and pharmacy reversals: run right after the payer-claim tables exist, before order-to-fill linkage."""
    out.executescript(DDL)
    return {'enrollment': enrollment(out), 'claims': claims(out), 'pharmacy': pharmacy(out)}


def build(out, as_of, path=None):
    """The other client-artifact tables (after population feeds: utilization reads claims and ADT, lists read fills)."""
    summary = {'labs': labs(out)}
    summary['adt'] = adt_visits(out)
    summary['utilization'] = utilization(out)
    summary['lists'] = lists(out, as_of)
    summary['documents'] = documents(out, path)
    summary['note_therapy_changes'] = therapy_changes(out)  # after lists (list status) and documents (readable outside text)
    out.executescript(INDEXES)
    return summary
