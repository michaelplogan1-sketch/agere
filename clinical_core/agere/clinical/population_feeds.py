"""Semantic tables for the broader-population feeds of expanded warehouse releases (v2.2+).

Called by semantic_layer.build after the core tables exist. Every table is created for every
release; releases without the streams (v1.23, v2.0) leave them empty and the added columns NULL,
so existing tables keep their rows and meaning. Values are literal source fields; the only
derivations are stated in SCHEMA_DOC (member months from spans, fill matching, county SVI lookup).
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
import json, re

# Payer product -> line-of-business group (literal product kept beside it).
LOB = {'commercial': 'commercial', 'marketplace': 'commercial', 'medicare_ffs': 'medicare', 'medicare_advantage': 'medicare',
       'medicaid': 'medicaid', 'dual_ffs': 'dual', 'dual_ma': 'dual'}
# AHC HRSN item -> need (food/transport keep the existing social_need meaning; housing/utilities/safety are new needs).
AHC_NEEDS = {'food_worry': 'food', 'food_ran_out': 'food', 'transportation_barrier': 'transport',
             'housing_situation': 'housing', 'housing_problems': 'housing', 'utilities_shutoff': 'utilities',
             'physical_safety': 'safety'}
# Directory organization_type -> facility class.
FACILITY = {'hospital': 'hospital', 'asc': 'ambulatory_surgery_center', 'imaging': 'imaging_idtf',
            'independent_outpatient_diagnostic_center': 'imaging_idtf', 'urgent_care': 'urgent_care',
            'standalone_urgent_care': 'urgent_care', 'dialysis': 'dialysis', 'snf': 'skilled_nursing',
            'home_health': 'home_health', 'home_health_agency': 'home_health', 'hospice': 'hospice', 'ambulance': 'ambulance',
            'dme': 'dme', 'pharmacy': 'pharmacy', 'retail_pharmacy': 'pharmacy', 'mail_pharmacy': 'pharmacy',
            'specialty_pharmacy': 'pharmacy', 'lab': 'laboratory', 'independent_outpatient_laboratory': 'laboratory',
            'independent_anatomic_pathology_laboratory': 'laboratory', 'primary_care': 'primary_care',
            'primary_care_practice': 'primary_care', 'specialty_group': 'outpatient_practice',
            'independent_specialty_practice': 'outpatient_practice', 'independent_outpatient_practice': 'outpatient_practice',
            'independent_endoscopy_practice': 'outpatient_practice', 'independent_medication_management_clinic': 'outpatient_practice',
            'independent_outpatient_rehabilitation': 'outpatient_practice', 'payer': 'payer'}
FILL_STREAMS = ('pharmacy_dispenses', 'payer_pharmacy_claims')
FILL_ID_DATE = re.compile(r'(\d{4}-\d{2}-\d{2})(T[0-9:]+)?$')
FILL_WINDOW_DAYS = 30  # orders without any link field: first fill of the same drug within 30 days of the order

DDL = """
CREATE TABLE enrollment_span(record_id TEXT PRIMARY KEY, patient_id TEXT, member_id TEXT, payer_id TEXT, product TEXT,
  line_of_business TEXT, plan_id TEXT, coverage_start TEXT, coverage_end TEXT, enrollment_reason TEXT, termination_reason TEXT,
  dual_status TEXT, attribution_practice_id TEXT, attribution_npi TEXT, available_date TEXT);
CREATE TABLE member_month(patient_id TEXT, month TEXT, payer_id TEXT, product TEXT, line_of_business TEXT, plan_id TEXT,
  attribution_practice_id TEXT, attribution_npi TEXT, spans INTEGER, record_id TEXT, PRIMARY KEY(patient_id, month));
CREATE TABLE payer_claim(payer_claim_id TEXT PRIMARY KEY, patient_id TEXT, payer_id TEXT, product TEXT, line_of_business TEXT,
  claim_type TEXT, type_of_bill TEXT, place_of_service TEXT, service_from TEXT, service_to TEXT, admission_date TEXT,
  discharge_date TEXT, discharge_status TEXT, drg TEXT, principal_diagnosis TEXT, diagnosis_codes TEXT, billing_npi TEXT,
  rendering_npi TEXT, network_status TEXT, adjudications INTEGER, first_status TEXT, final_status TEXT, final_sequence INTEGER,
  charge_cents INTEGER, allowed_cents INTEGER, paid_cents INTEGER, patient_responsibility_cents INTEGER, carc TEXT, paid_date TEXT,
  claim_received_date TEXT, first_available_date TEXT, last_available_date TEXT, record_id TEXT);
CREATE TABLE payer_claim_line(record_id TEXT, payer_claim_id TEXT, patient_id TEXT, line_number INTEGER, service_date TEXT,
  code_system TEXT, cpt TEXT, hcpcs TEXT, revenue_code TEXT, units REAL, charge_cents INTEGER, allowed_cents INTEGER);
CREATE TABLE payer_pharmacy_claim(record_id TEXT PRIMARY KEY, rx_claim_id TEXT, patient_id TEXT, payer_id TEXT, fill_date TEXT,
  ndc TEXT, drug_name TEXT, days_supply INTEGER, quantity REAL, fill_number INTEGER, pharmacy_npi TEXT, pharmacy_name TEXT,
  pharmacy_type TEXT, bin TEXT, pcn TEXT, group_id TEXT, prescriber_npi TEXT, claim_status TEXT, reject_code TEXT,
  plan_paid_cents INTEGER, patient_pay_cents INTEGER, ingredient_cost_cents INTEGER, dispensing_fee_cents INTEGER,
  formulary_tier TEXT, available_date TEXT);
CREATE TABLE prescription(record_id TEXT PRIMARY KEY, patient_id TEXT, order_class TEXT, transmission TEXT, drug_name TEXT,
  rxcui TEXT, ordered_date TEXT, quantity REAL, days_supply INTEGER, refills_authorized INTEGER, indication TEXT,
  ordering_clinician_id TEXT, pharmacy TEXT, organization_id TEXT, encounter_id TEXT, available_date TEXT,
  declared_fill_ids INTEGER, fills_received INTEGER, first_fill_date TEXT, last_fill_date TEXT, fill_expected INTEGER,
  fill_status TEXT, filled INTEGER, prescribed_not_filled INTEGER, fill_basis TEXT, script_message_id TEXT, match_confidence REAL,
  match_components TEXT, matched_chain TEXT);
CREATE TABLE prescription_fill(order_record_id TEXT, fill_record_id TEXT, fill_stream TEXT, fill_date TEXT, basis TEXT,
  fill_event_id TEXT, match_confidence REAL);
CREATE TABLE hie_document(record_id TEXT PRIMARY KEY, patient_id TEXT, document_id TEXT, document_type TEXT, loinc TEXT,
  encounter_id TEXT, author_organization_id TEXT, delivery_channel TEXT, format TEXT, event_date TEXT, available_date TEXT,
  text_chars INTEGER);
CREATE TABLE sdoh_vendor(record_id TEXT PRIMARY KEY, patient_id TEXT, vendor_name TEXT, delivery_date TEXT, match_status TEXT,
  match_confidence REAL, county_fips TEXT, svi_overall REAL, svi_socioeconomic REAL, svi_household REAL,
  svi_minority_language REAL, svi_housing_transportation REAL, est_household_income_band TEXT, est_household_size INTEGER,
  homeowner_status TEXT, food_access_low INTEGER, financial_stress_score REAL, social_isolation_score REAL,
  transportation_risk_score REAL, housing_instability_score REAL, broadband_access_score REAL, health_literacy_estimate TEXT,
  language_preference_estimate TEXT, available_date TEXT);
CREATE TABLE county_svi(county_fips TEXT PRIMARY KEY, svi_overall REAL, svi_socioeconomic REAL, svi_household REAL,
  svi_minority_language REAL, svi_housing_transportation REAL, vendor_rows INTEGER);
CREATE TABLE patient_svi(patient_id TEXT PRIMARY KEY, county_fips TEXT, county_source TEXT, svi_overall REAL,
  svi_socioeconomic REAL, svi_household REAL, svi_minority_language REAL, svi_housing_transportation REAL, svi_source TEXT);
CREATE TABLE clinical_note(record_id TEXT PRIMARY KEY, patient_id TEXT, note_type TEXT, author_id TEXT, author_credential TEXT,
  template_style TEXT, note_status TEXT, signed_date TEXT, encounter_id TEXT, event_date TEXT, available_date TEXT,
  organization_id TEXT, text_chars INTEGER);
CREATE TABLE incident_diagnosis(record_id TEXT PRIMARY KEY, patient_id TEXT, concept TEXT, label TEXT, icd10 TEXT,
  diagnosis_date TEXT, diagnosing_encounter_id TEXT, encounter_icd10_match INTEGER, visit_status TEXT,
  problem_list_note_record_id TEXT, first_prescription_record_id TEXT, available_date TEXT);
"""
INDEXES = """
CREATE INDEX enrollment_span_patient ON enrollment_span(patient_id);
CREATE INDEX member_month_month ON member_month(month, line_of_business);
CREATE INDEX payer_claim_patient ON payer_claim(patient_id, service_from);
CREATE INDEX payer_claim_line_claim ON payer_claim_line(payer_claim_id);
CREATE INDEX payer_claim_line_cpt ON payer_claim_line(cpt, patient_id);
CREATE INDEX payer_pharmacy_claim_patient ON payer_pharmacy_claim(patient_id, fill_date);
CREATE INDEX prescription_patient ON prescription(patient_id);
CREATE INDEX prescription_fill_order ON prescription_fill(order_record_id);
CREATE INDEX prescription_fill_fill ON prescription_fill(fill_record_id);
CREATE INDEX hie_document_patient ON hie_document(patient_id);
CREATE INDEX sdoh_vendor_patient ON sdoh_vendor(patient_id);
CREATE INDEX clinical_note_patient ON clinical_note(patient_id, note_type);
CREATE INDEX incident_diagnosis_patient ON incident_diagnosis(patient_id);
"""

SCHEMA_DOC = {
    'enrollment_span': 'One row per received payer enrollment span: payer_id, product (commercial, marketplace, medicare_ffs, '
                       'medicare_advantage, medicaid, dual_ffs, dual_ma), line_of_business (commercial | medicare | medicaid | dual), '
                       'plan_id, coverage_start/coverage_end (NULL end = open), termination_reason, payer attribution '
                       '(attribution_practice_id/attribution_npi).',
    'member_month': 'One row per (patient, calendar month) covered by any enrollment span on any day of the month, up to the '
                    'snapshot date. When spans overlap, the span with the latest coverage_start supplies payer/product/'
                    'line_of_business/plan_id; spans = number of covering spans. Member months come from spans, not a static roster; '
                    'patients without spans have no rows here (vbc falls back to the coverage record for them).',
    'payer_claim': 'One row per payer medical claim (payer_claim_id; payer feed, separate from provider-side claim). '
                   'Adjudications arrive as successive records (adjudication_sequence); final_status/final_sequence and the '
                   'amounts (cents) come from the highest sequence. first_status = status of the first adjudication, '
                   'adjudications = number of adjudication versions; on native workspaces both come from the full history '
                   '(payer_adjudication, superseded versions included). Statuses: paid, denied, denied_upheld, reversed.',
    'payer_adjudication': 'One row per RECEIVED payer medical claim adjudication (every adjudication sequence), superseded versions '
                          'INCLUDED (superseded = 1, superseded_by): the full received history. claim_status per row; adjudication_date = '
                          'the received record\'s event date; paid_cents per row. Count or sum received adjudication rows here; '
                          'payer_claim is one row per claim (final status).',
    'payer_claim_line': 'Payer medical claim lines: cpt/hcpcs (procedure code by system), revenue_code, units, charge/allowed cents.',
    'payer_pharmacy_claim': 'Payer pharmacy claims (NCPDP): fill_date, ndc, drug_name, days_supply, quantity, pharmacy NPI/name/type, '
                            'BIN/PCN/group routing, prescriber_npi, claim_status (paid | rejected), plan/patient paid cents.',
    'prescription': 'One row per medication order (e-prescriptions with order_class e-prescribe, incident-diagnosis '
                    'outpatient_prescription orders, legacy orders) with its fills. fills_received = matched paid, unreversed '
                    'fills received by the snapshot: e-prescriptions by linked_fill_event_ids when the release declares them '
                    '(v2.2); dispenses naming the order (medication_order_record_id); e-prescriptions without a link (v2.3: '
                    'only the NCPDP SCRIPT message id) by fuzzy matching to a pharmacy prescription chain (patient + pharmacy '
                    'NPI + Rx number) on drug (RxCUI via NDC, else ingredient + strength), prescriber NPI, days from order to '
                    'first fill, new-prescription fill number, pharmacy and days supply: match_confidence (0-1, linked at '
                    '>= 0.5), match_components (score per signal), matched_chain; other outpatient orders by the first same-'
                    'patient, same-drug fill within 30 days. fill_expected = 1 for e-prescribe and outpatient_prescription '
                    'orders. fill_status: filled | declared_unreceived (the order lists fill ids but no paid fill was received) '
                    '| not_filled. prescribed_not_filled = 1 only for not_filled with fill_expected (NULL when unknown); for '
                    'fuzzy-linked orders it means no chain matched with confidence >= 0.5 (cash fills and other pharmacies are '
                    'not in the payer feed).',
    'prescription_fill': 'Order-to-fill links behind prescription: order_record_id, fill_record_id, fill_stream, fill_date, '
                         'basis (linked_fill_event_id | medication_order_record_id | fuzzy_prescription_chain | '
                         'patient_drug_window_30d), fill_event_id, match_confidence (fuzzy links). Reversed (B2) fills never link.',
    'hie_document': 'HIE-pushed documents: ED summaries (LOINC 34111-5) and discharge summaries (18842-5) with encounter_id and '
                    'authoring organization. Text stays in record.payload ($.text).',
    'sdoh_vendor': 'Purchased consumer/SDOH file rows per delivery: match status/confidence, county_fips and CDC/ATSDR SVI 2022 '
                   'county percentiles (svi_*), modeled household estimates and risk scores (vendor estimates, not screens).',
    'county_svi': 'SVI percentiles per county FIPS as delivered in the vendor file (a county with conflicting values is omitted).',
    'patient_svi': 'SVI by patient via county: county_fips from patient demographics (county_source = patient_demographics), '
                   'else the latest matched vendor row (vendor_match); svi_* from county_svi.',
    'clinical_note': 'Signed notes: note_type (office_followup, acute_visit, annual_preventive, telehealth, well_child, '
                     'problem_list_update, ...), author_id/author_credential, template_style, note_status, signed_date, text_chars. '
                     'Text stays in record.payload ($.text).',
    'incident_diagnosis': 'Diagnoses made at a completed visit (conditions with diagnosis_basis = clinical_encounter): diagnosis_date '
                          '(onset, else event date), diagnosing_encounter_id, encounter_icd10_match = 1 when that encounter carries the '
                          'same ICD-10, problem_list_note_record_id (problem_list_update note on that encounter naming the code), '
                          'first_prescription_record_id (order written at that encounter).',
}


def _cents(v):
    return None if v is None else int(round(float(v) * 100))


def _rows(out, stream):
    for rid, pid, payload, d, av in out.execute('SELECT record_id, patient_id, payload, event_date, available_date FROM record WHERE stream = ?', (stream,)):
        yield rid, pid, json.loads(payload), d, av


def _month_list(start, end):
    y, m = int(start[:4]), int(start[5:7]); out = []
    while (y, m) <= (int(end[:4]), int(end[5:7])):
        out.append(f'{y:04d}-{m:02d}'); m += 1
        if m == 13: y, m = y + 1, 1
    return out


def _enrollment(out, as_of):
    spans = []
    for rid, pid, r, d, av in _rows(out, 'enrollment_spans'):
        product = r.get('product')
        spans.append((rid, pid, r.get('member_id'), r.get('payer_id'), product, LOB.get(product, product), r.get('plan_id'),
                      (r.get('coverage_start') or d or '')[:10] or None, (r.get('coverage_end') or '')[:10] or None,
                      r.get('enrollment_reason'), r.get('termination_reason'), r.get('dual_status'),
                      r.get('attribution_practice_id'), r.get('attribution_npi'), av))
    out.executemany('INSERT INTO enrollment_span VALUES (' + ','.join('?' * 15) + ')', spans)
    months = defaultdict(list)
    for s in spans:
        start, end = s[7], min(s[8] or as_of, as_of)
        if not start or start > end: continue
        for m in _month_list(start, end): months[(s[1], m)].append(s)
    rows = []
    for (pid, m), cover in months.items():
        s = max(cover, key=lambda x: (x[7], x[0]))
        rows.append((pid, m, s[3], s[4], s[5], s[6], s[12], s[13], len(cover), s[0]))
    out.executemany('INSERT INTO member_month VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
    return {'spans': len(spans), 'member_months': len(rows), 'members': len({r[0] for r in rows})}


def _payer_claims(out):
    hist = defaultdict(list)
    for rid, pid, r, d, av in _rows(out, 'payer_medical_claims'):
        if r.get('payer_claim_id'): hist[r['payer_claim_id']].append((r.get('adjudication_sequence') or 0, av or '', rid, pid, r))
    rows = []
    for cid, h in hist.items():
        h.sort(key=lambda x: (x[0], x[1], x[2]))
        seq, _, rid, pid, r = h[-1]
        dx = r.get('diagnosis_codes')
        rows.append((cid, pid, r.get('payer_id'), r.get('product'), LOB.get(r.get('product'), r.get('product')), r.get('claim_type'),
                     r.get('type_of_bill'), r.get('place_of_service'), r.get('service_from'), r.get('service_to'), r.get('admission_date'),
                     r.get('discharge_date'), r.get('discharge_status'), r.get('ms_drg'), r.get('principal_diagnosis'),
                     json.dumps(dx) if isinstance(dx, list) else dx, r.get('billing_provider_npi'), r.get('rendering_provider_npi'),
                     r.get('network_status'), len(h), h[0][4].get('claim_status'), r.get('claim_status'), seq,
                     _cents(r.get('total_charge')), _cents(r.get('allowed_amount')), _cents(r.get('paid_amount')),
                     _cents(r.get('patient_responsibility')), r.get('carc'), r.get('paid_date'), r.get('claim_received_date'),
                     min(x[1] for x in h) or None, max(x[1] for x in h) or None, rid))
    out.executemany('INSERT INTO payer_claim VALUES (' + ','.join('?' * 33) + ')', rows)
    lines = []
    for rid, pid, r, d, av in _rows(out, 'payer_medical_claim_lines'):
        system, code = r.get('procedure_code_system'), r.get('procedure_code')
        lines.append((rid, r.get('payer_claim_id'), pid, r.get('line_number'), r.get('service_date'), system,
                      code if system == 'CPT' else None, code if system == 'HCPCS' else None, r.get('revenue_code'), r.get('units'),
                      _cents(r.get('charge_amount')), _cents(r.get('allowed_amount'))))
    out.executemany('INSERT INTO payer_claim_line VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', lines)
    rx = []
    for rid, pid, r, d, av in _rows(out, 'payer_pharmacy_claims'):
        rx.append((rid, r.get('rx_claim_id'), pid, r.get('payer_id'), d, r.get('ndc'), r.get('drug_name'), r.get('days_supply'),
                   r.get('quantity_dispensed'), r.get('fill_number'), r.get('pharmacy_npi'), r.get('pharmacy_name'), r.get('pharmacy_type'),
                   r.get('bin'), r.get('pcn'), r.get('group_id'), r.get('prescriber_npi'), r.get('claim_status'), r.get('reject_code'),
                   _cents(r.get('plan_paid')), _cents(r.get('patient_pay')), _cents(r.get('ingredient_cost')),
                   _cents(r.get('dispensing_fee')), r.get('formulary_tier'), av))
    out.executemany('INSERT INTO payer_pharmacy_claim VALUES (' + ','.join('?' * 25) + ')', rx)
    return {'payer_claims': len(rows), 'payer_claim_lines': len(lines), 'payer_pharmacy_claims': len(rx)}


def _name(v):
    return ' '.join(str(v or '').casefold().split())


def _prescriptions(out):
    """Orders and their fills. Only paid/dispensed, unreversed fill records received by the snapshot count as fills."""
    from . import client_data
    reversed_ids = client_data.reversed_fill_ids(out)
    fills_by_day = defaultdict(list); fills_by_patient = defaultdict(list)
    for stream in FILL_STREAMS:
        for rid, pid, r, d, av in _rows(out, stream):
            status = r.get('claim_status') if stream == 'payer_pharmacy_claims' else r.get('status')
            if status not in ('paid', 'dispensed', None) or not d or rid in reversed_ids: continue
            fill = (rid, stream, d, _name(r.get('drug_name') or r.get('medication_name')), str(r.get('rxcui') or ''))
            fills_by_day[(pid, d)].append(fill); fills_by_patient[pid].append(fill)
    linked = defaultdict(list)  # legacy dispenses name their order
    for fill_id, order_id in out.execute("SELECT record_id, target_record_id FROM link WHERE field = 'medication_order_record_id' "
                                         "AND stream IN ('pharmacy_dispenses', 'pharmacy_claim_transactions', 'pharmacy_claims')"):
        linked[order_id].append(fill_id)
    fill_dates = {}
    for fid, d, st in out.execute("SELECT record_id, event_date, stream FROM record WHERE stream IN "
                                  "('pharmacy_dispenses', 'pharmacy_claim_transactions', 'pharmacy_claims')"):
        fill_dates[fid] = (d, st)
    orders, links, stats = [], [], Counter()
    received = list(_rows(out, 'medication_orders'))
    # e-prescriptions without any link field: fuzzy order -> pharmacy prescription chain matching (client_data.link_prescriptions)
    fuzzy = {rid: {'patient': pid, 'date': (d or '')[:10], 'key': client_data.drug_key(r.get('medication_name')),
                   'rxcui': str(r.get('rxcui')) if r.get('rxcui') else None, 'clinician': r.get('ordering_clinician_id'),
                   'pharmacy': _name(r.get('pharmacy')) or None, 'days_supply': r.get('days_supply'), 'refills': r.get('refills_authorized')}
             for rid, pid, r, d, av in received
             if r.get('order_class') == 'e-prescribe' and not isinstance(r.get('linked_fill_event_ids'), list) and not linked.get(rid) and d}
    chains = client_data.fill_chains(out, reversed_ids) if fuzzy else {}
    npi_of = dict(out.execute('SELECT clinician_id, npi FROM clinician WHERE npi IS NOT NULL')) if fuzzy else {}
    fuzzy_links = client_data.link_prescriptions(fuzzy, chains, npi_of) if fuzzy else {}
    for rid, pid, r, d, av in received:
        cls = r.get('order_class'); name = _name(r.get('medication_name')); rxcui = str(r.get('rxcui') or '')
        declared = r.get('linked_fill_event_ids'); matched = []; conf = comp = chain_id = None
        if isinstance(declared, list):
            basis = 'linked_fill_event_id'
            for event in declared:
                m = FILL_ID_DATE.search(str(event))
                if not m: stats['fill_ids_without_date'] += 1; continue
                same = [f for f in fills_by_day.get((pid, m.group(1)), []) if f[3] == name]
                if same: matched.append((same[0], event))
                else: stats['fill_ids_unreceived'] += 1
        elif linked.get(rid):
            basis = 'medication_order_record_id'
            for fid in linked[rid]:
                fd, st = fill_dates.get(fid, (None, None))
                matched.append(((fid, st, fd, None, None), None))
        elif rid in fuzzy:
            basis = 'fuzzy_prescription_chain'
            if rid in fuzzy_links:
                chain_id, conf, comp, chain_fills = fuzzy_links[rid]
                matched = [((frid, chains[chain_id]['stream'], fd, None, None), None) for fd, n, frid, key, frx, ds in chain_fills]
                stats['fuzzy_linked'] += 1
            else: stats['fuzzy_unlinked'] += 1
        elif cls == 'outpatient_prescription' and d:
            basis = 'patient_drug_window_30d'
            last = (date.fromisoformat(d[:10]) + timedelta(days=FILL_WINDOW_DAYS)).isoformat()
            cand = sorted(f for f in fills_by_patient.get(pid, []) if d[:10] <= f[2] <= last and
                          ((rxcui and f[4] == rxcui) or (f[3].split()[:1] == name.split()[:1] and name)))
            cand = sorted(cand, key=lambda f: (f[2], f[0]))
            if cand: matched.append((cand[0], None))
        else:
            basis = None
        seen = set(); kept = []
        for f, event in matched:
            if f[0] in seen: continue
            seen.add(f[0]); kept.append((f, event))
            links.append((rid, f[0], f[1], f[2], basis, event, conf))
        expected = 1 if cls in ('e-prescribe', 'outpatient_prescription') else None
        dates = sorted(f[2] for f, _ in kept if f[2])
        status = 'filled' if kept else ('declared_unreceived' if isinstance(declared, list) and declared else 'not_filled')
        not_filled = (1 if status == 'not_filled' else 0 if status == 'filled' else None) if expected else None
        stats[status] += 1
        orders.append((rid, pid, cls, r.get('transmission'), r.get('medication_name'), rxcui or None, d, r.get('quantity'),
                       r.get('days_supply'), r.get('refills_authorized'), r.get('indication'), r.get('ordering_clinician_id'),
                       r.get('pharmacy'), r.get('organization_id'), r.get('encounter_id'), av,
                       len(declared) if isinstance(declared, list) else None, len(kept), dates[0] if dates else None,
                       dates[-1] if dates else None, expected, status, int(bool(kept)), not_filled, basis, r.get('script_message_id'),
                       conf, json.dumps(comp, sort_keys=True) if comp else None, chain_id))
    out.executemany('INSERT INTO prescription VALUES (' + ','.join('?' * 29) + ')', orders)
    out.executemany('INSERT INTO prescription_fill VALUES (?,?,?,?,?,?,?)', links)
    return dict(stats)


def _documents(out):
    hie = [(rid, pid, r.get('document_id'), r.get('document_type'), r.get('loinc_document_code'),
            r.get('encounter_id') or r.get('source_encounter'), r.get('author_organization_id'), r.get('delivery_channel'),
            r.get('format'), d, av, len(r.get('text') or '')) for rid, pid, r, d, av in _rows(out, 'hie_documents')]
    out.executemany('INSERT INTO hie_document VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', hie)
    notes = [(rid, pid, r.get('note_type'), r.get('author_id'), r.get('author_credential'), r.get('template_style'),
              r.get('note_status'), r.get('signed_date'), r.get('encounter_id'), d, av, r.get('organization_id'),
              len(r.get('text') or '')) for rid, pid, r, d, av in _rows(out, 'notes')]
    out.executemany('INSERT INTO clinical_note VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', notes)
    return {'hie_documents': len(hie), 'notes': len(notes)}


def _sdoh(out):
    rows, county = [], defaultdict(set)
    svi_keys = ('svi_overall_percentile', 'svi_socioeconomic_percentile', 'svi_household_characteristics_percentile',
                'svi_minority_language_percentile', 'svi_housing_transportation_percentile')
    latest = {}
    for rid, pid, r, d, av in _rows(out, 'sdoh_vendor_attributes'):
        svi = tuple(r.get(k) for k in svi_keys)
        food = r.get('food_access_low_flag')
        rows.append((rid, pid, r.get('vendor_name'), r.get('delivery_date') or d, r.get('member_match_status'), r.get('match_confidence'),
                     r.get('county_fips'), *svi, r.get('est_household_income_band'), r.get('est_household_size'), r.get('homeowner_status'),
                     None if food is None else int(bool(food)), r.get('financial_stress_score'), r.get('social_isolation_score'),
                     r.get('transportation_risk_score'), r.get('housing_instability_score'), r.get('broadband_access_score'),
                     r.get('health_literacy_estimate'), r.get('language_preference_estimate'), av))
        if r.get('county_fips') and svi[0] is not None: county[r['county_fips']].add(svi)
        if r.get('county_fips') and r.get('member_match_status') not in (None, 'no_match', 'unmatched'):
            key = r.get('delivery_date') or d or ''
            if pid not in latest or key >= latest[pid][0]: latest[pid] = (key, r['county_fips'])
    out.executemany('INSERT INTO sdoh_vendor VALUES (' + ','.join('?' * 24) + ')', rows)
    vendor_rows = len(rows)
    counts = Counter(r[0] for r in out.execute("SELECT county_fips FROM sdoh_vendor WHERE county_fips IS NOT NULL"))
    lookup = {c: next(iter(v)) for c, v in county.items() if len(v) == 1}
    out.executemany('INSERT INTO county_svi VALUES (?,?,?,?,?,?,?)', [(c, *v, counts[c]) for c, v in lookup.items()])
    rows = []
    for pid, fips in out.execute('SELECT patient_id, county FROM patient'):
        src = 'patient_demographics' if fips else None
        if not fips and pid in latest: fips, src = latest[pid][1], 'vendor_match'
        if not fips: continue
        v = lookup.get(fips)
        rows.append((pid, fips, src, *(v or (None,) * 5), 'vendor_file_county_lookup' if v else None))
    out.executemany('INSERT INTO patient_svi VALUES (?,?,?,?,?,?,?,?,?)', rows)
    return {'vendor_rows': vendor_rows, 'counties': len(lookup),
            'counties_with_conflicting_svi': sum(1 for v in county.values() if len(v) > 1),
            'patients_with_svi': sum(1 for r in rows if r[3] is not None)}


def _ahc_needs(out):
    """AHC HRSN items -> social_need rows (one per screen and need; positive when any item for the need is 'yes')."""
    rows = []
    for rid, pid, r, d, av in _rows(out, 'social_screens'):
        items = r.get('items')
        if not isinstance(items, list): continue
        needs = defaultdict(list)
        for it in items:
            if isinstance(it, dict) and it.get('item') in AHC_NEEDS: needs[AHC_NEEDS[it['item']]].append(it)
        for need, its in sorted(needs.items()):
            answers = [str(it.get('answer')).casefold() for it in its]
            positive = 1 if 'yes' in answers else 0 if all(a == 'no' for a in answers) else None
            rows.append((pid, need, positive, r.get('instrument'), 'social_screens', 'items:' + ','.join(it['item'] for it in its), d, rid))
    out.executemany('INSERT INTO social_need VALUES (?,?,?,?,?,?,?,?)', rows)
    return len(rows)


def _directory(out):
    """Facility class from the directory type; place_of_service = most frequent POS on claims billed under the org NPI."""
    for col in ('facility_class TEXT', 'place_of_service TEXT', 'place_of_service_claims INTEGER'):
        if col.split()[0] not in {r[1] for r in out.execute('PRAGMA table_info(organization)')}:
            out.execute(f'ALTER TABLE organization ADD COLUMN {col}')
    out.executemany('UPDATE organization SET facility_class = ? WHERE organization_type = ?',
                    [(v, k) for k, v in FACILITY.items()])
    out.execute("UPDATE organization SET facility_class = 'other' WHERE facility_class IS NULL AND organization_type IS NOT NULL")
    pos = defaultdict(Counter)
    for npi, p in out.execute("SELECT billing_npi, place_of_service FROM payer_claim WHERE billing_npi IS NOT NULL AND place_of_service IS NOT NULL "
                              "UNION ALL SELECT billing_npi, place_of_service FROM claim WHERE billing_npi IS NOT NULL AND place_of_service IS NOT NULL"):
        pos[npi][str(p)] += 1
    out.executemany('UPDATE organization SET place_of_service = ?, place_of_service_claims = ? WHERE npi = ?',
                    [(c.most_common(1)[0][0], sum(c.values()), npi) for npi, c in pos.items()])


def _incident(out):
    for col in ('diagnosis_basis TEXT', 'diagnosing_encounter_id TEXT', 'diagnosis_date TEXT'):
        out.execute(f'ALTER TABLE condition ADD COLUMN {col}')
    out.execute("""CREATE TEMP TABLE dx_basis AS SELECT record_id, json_extract(payload,'$.diagnosis_basis') AS basis,
                     json_extract(payload,'$.diagnosing_encounter_id') AS enc,
                     substr(coalesce(json_extract(payload,'$.onset'), event_date), 1, 10) AS dx_date
                   FROM record WHERE stream = 'conditions'""")
    out.execute('CREATE INDEX temp.dx_basis_id ON dx_basis(record_id)')
    out.execute("""UPDATE condition SET (diagnosis_basis, diagnosing_encounter_id, diagnosis_date) =
                     (SELECT basis, enc, dx_date FROM dx_basis b WHERE b.record_id = condition.record_id)
                   WHERE source_table = 'conditions'""")
    rows = out.execute("""SELECT c.record_id, c.patient_id, min(c.concept), c.label, c.icd10, c.diagnosis_date, c.diagnosing_encounter_id,
          EXISTS(SELECT 1 FROM encounter_diagnosis d WHERE d.encounter_id = c.diagnosing_encounter_id AND d.icd10 = c.icd10),
          (SELECT v.status FROM visit v WHERE v.encounter_id = c.diagnosing_encounter_id),
          (SELECT n.record_id FROM clinical_note n JOIN record r ON r.record_id = n.record_id WHERE n.encounter_id = c.diagnosing_encounter_id
             AND n.patient_id = c.patient_id AND n.note_type = 'problem_list_update' AND instr(json_extract(r.payload,'$.text'), c.icd10) > 0
             ORDER BY n.record_id LIMIT 1),
          (SELECT p.record_id FROM prescription p WHERE p.encounter_id = c.diagnosing_encounter_id AND p.patient_id = c.patient_id
             ORDER BY p.record_id LIMIT 1),
          c.available_date
        FROM condition c WHERE c.source_table = 'conditions' AND c.diagnosis_basis = 'clinical_encounter'
        GROUP BY c.record_id""").fetchall()
    out.executemany('INSERT INTO incident_diagnosis VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', rows)
    return {'incident_diagnoses': len(rows), 'encounter_icd10_match': sum(r[7] for r in rows),
            'with_problem_list_note': sum(1 for r in rows if r[9]), 'with_prescription': sum(1 for r in rows if r[10])}


def _patient_columns(out, people):
    for col in ('data_segment TEXT', 'line_of_business TEXT', 'payer_id TEXT'):
        out.execute(f'ALTER TABLE patient ADD COLUMN {col}')
    out.executemany('UPDATE patient SET data_segment = ? WHERE patient_id = ?', [(p.get('data_segment'), pid) for pid, p in people.items()])
    out.execute("""UPDATE patient SET (line_of_business, payer_id) = (SELECT m.line_of_business, m.payer_id FROM member_month m
                     WHERE m.patient_id = patient.patient_id ORDER BY m.month DESC LIMIT 1)""")


def build(out, people, as_of, path=None):
    """Create and fill the population-feed tables; returns a summary stored in meta.population_feeds.
    Client-feed artifacts (claim versions, pharmacy reversals, lab quirks, list rot, ADT gaps, utilization from every source,
    outside documents) are resolved by client_data: claims and reversals before order-to-fill linkage, the rest after."""
    from . import client_data
    out.executescript(DDL)
    summary = {'enrollment': _enrollment(out, as_of), 'payer_claims': _payer_claims(out)}
    summary['client_claims'] = client_data.build_claims(out)
    summary.update(prescriptions=_prescriptions(out), documents=_documents(out), sdoh=_sdoh(out), ahc_social_need_rows=_ahc_needs(out))
    _directory(out)
    summary['incident'] = _incident(out)
    _patient_columns(out, people)
    out.executescript(INDEXES)
    summary['client_data'] = client_data.build(out, as_of, path)
    return summary
