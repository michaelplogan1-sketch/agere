"""Question variants with trusted answers for calibrating the dq_family and vbc_family routes (tools/calibrate_cascades.py
collect-families).

Each variant is a question with an inline definition in the delivery's own phrasing, parameters varied (years, products,
formats, conditions, statuses, codes, counts, admission types, windows, grouping). For each variant the TRUSTED definition
is written here explicitly as a small dict (never by parsing the question) and its answer is computed by an independent
Python reference over plain row reads of the semantic layer (no family SQL is reused): a route's answer is correct when it
equals the reference answer (counts exactly, money and rates to the cent / 0.01, tables key by key, patient lists as sets).

Reference conventions (the definitions the families claim to implement; see dq_families.py / vbc_families.py docstrings):
late = original file delivered after its name date, or (no name date) > 7 days past its series' usual lag; flat = any
parser except hl7/x12/ncpdp/fhir; final paid claim = final_status paid and counts_in_metrics 1; DRG claims with 0 allowed
take paid + patient responsibility; member-month = (person, month[, product]) covered on the 1st, 1st <= death date,
months up to as_of; stays = distinct (person, admission date) on non-voided counts-in-metrics 011x institutional claims.

Synthetic data only. Deterministic (seeded). Nothing here calls a model.
"""
from __future__ import annotations

import datetime as dt, json, random, sqlite3
from collections import Counter, defaultdict

VERSION = 'family-variants/1'
AS_OF = '2030-05-27'
SEED = 20261018
SCOPE = ' Scope: every patient in the (sampled) delivery, duplicate charts counted as their person.'
SPANS = (' Delivered enrollment spans (primary coverage; Medigap and COB secondary excluded; a span superseded by a later version '
         'of the same coverage is replaced by it, while a span superseded only by a member_id_reissued continuation is kept) and '
         'delivered death records.')
APPTS = (" Appointments: each appointment_id once, in its latest delivered version, from every delivered EHR scheduling record "
         "(each EHR's appointment table or FHIR Appointment resources, including the deep-chart panel; monthly extracts are "
         "incremental, latest month wins); slot date = slot_start, else scheduled_time/scheduled_date, else the appointment's "
         "date (some deep-chart panel appointments carry only a date); attended and arrived count as fulfilled, rescheduled as cancelled.")
STAY = (' Inpatient stay = distinct person and admission date on non-voided institutional payer claims with bill type 011x, '
        'final adjudication.')


def _item(tier, fam, question, definition, truth, answer_type):
    text = question + ' Definition: ' + definition
    gid = f"{tier}:{fam}:" + json.dumps(truth, sort_keys=True)
    return {'id': gid, 'group': gid, 'tier': tier, 'family': fam, 'question': text, 'truth': truth, 'answer_type': answer_type,
            'provenance': {'generator': VERSION, 'seed': SEED, 'trusted': 'explicit definition + independent Python reference'}}


# ============================================================================================ variants
FMT_WORDS = {'flat': 'Flat files', 'hl7': 'HL7 batches', 'x12': 'X12 interchanges', 'fhir': 'FHIR files', 'ncpdp': 'NCPDP files'}
COND_DEF = {
    'late': 'Files delivered after their normal cycle (late or caught up in the next cycle)',
    'resend_identical': 'files re-sent with identical content under another name',
    'truncated': 'files truncated or cut off',
    'empty_expected': 'Files with a header only, or 0 bytes, where rows were expected',
    'empty_any': 'Files with a header only, or 0 bytes',
    'zero_byte': 'Files of 0 bytes',
    'header_only': 'Files with a header only',
    'v2_replacement': 'v2 corrected copies of earlier files',
}
COND_Q = {'late': 'How many data files came in late', 'resend_identical': 'How many files were sent to us twice',
          'truncated': 'How many files arrived cut off', 'empty_expected': 'How many files came through empty',
          'empty_any': 'How many files were empty', 'zero_byte': 'How many zero-byte files did we get',
          'header_only': 'How many files had only a header', 'v2_replacement': 'How many v2 corrected files did we get'}


def dq_variants(rng):
    out = []
    years = [None, 2027, 2028, 2029, 2030]
    for cond in COND_DEF:
        fmts = [[], ['flat'], ['hl7'], ['x12'], ['fhir'], ['flat', 'hl7', 'x12']]
        if cond == 'resend_identical': fmts = [['flat'], []]
        for f in fmts:
            for y in rng.sample(years, 2):
                for by_feed in ([False, True] if rng.random() < 0.25 else [False]):
                    if cond == 'resend_identical' and not f and y is None: continue
                    if f and cond not in ('resend_identical',):
                        lead = ', '.join(FMT_WORDS[x] for x in f)
                        d = f"Release-wide raw/ deliveries. {lead}: {COND_DEF[cond][0].lower() + COND_DEF[cond][1:]}"
                    elif f:
                        d = f"Release-wide raw/ deliveries. Flat {COND_DEF[cond]}"
                    else:
                        d = f"Release-wide raw/ deliveries. {COND_DEF[cond][0].upper() + COND_DEF[cond][1:]}"
                    if y: d += f", delivered in {y}"
                    if by_feed: d += ', by feed'
                    d += '.'
                    qtxt = COND_Q[cond] + (f' in {y}' if y else '') + (' by feed' if by_feed else '') + '?'
                    truth = {'family': 'file_count', 'condition': cond, 'formats': f, 'year': y, 'by_feed': by_feed}
                    out.append(_item('dq_family', 'file_count', qtxt, d, truth, 'table' if by_feed else 'count'))
    for y in [None, 2027, 2028, 2029, 2030]:
        for by_feed in (False, True):
            d = 'Release-wide raw/ deliveries. Expected periodic files that never arrived by as_of' + (f', missing periods in {y}' if y else '') + \
                (', by feed' if by_feed else '') + '.'
            qtxt = 'Are we missing any expected files? How many' + (f' for {y}' if y else '') + (' by feed' if by_feed else '') + '?'
            out.append(_item('dq_family', 'missing_periods', qtxt, d, {'family': 'missing_periods', 'year': y, 'by_feed': by_feed},
                             'table' if by_feed else 'count'))
    out.append(_item('dq_family', 'duplicate_charts', 'How many duplicate patient charts are in the data?',
                     'Duplicate charts present in the (sampled) warehouse: patient_ids that belong to the same person as another chart.',
                     {'family': 'duplicate_charts', 'unit': 'charts'}, 'count'))
    out.append(_item('dq_family', 'duplicate_charts', 'How many patients have more than one chart in our data?',
                     'People with at least one duplicate chart present in the (sampled) warehouse: a separate patient_id that belongs to the same person.',
                     {'family': 'duplicate_charts', 'unit': 'people'}, 'count'))
    out.append(_item('dq_family', 'chart_renumbering', 'How many patients got a new chart number when their practice changed EHRs?',
                     'Release-wide. Patients whose MRN changed at an EHR go-live.', {'family': 'chart_renumbering'}, 'count'))
    for y in [None, 2026, 2027, 2028, 2029, 2030]:
        out.append(_item('dq_family', 'corrected_results', 'How many lab results were corrected after they were first reported' + (f' in {y}' if y else '') + '?',
                         'All patients. Lab results with result_status corrected' + (f', dated {y}' if y else '') + '.',
                         {'family': 'corrected_results', 'year': y}, 'count'))
    for streams, word in ((None, 'Source records'), (['labs'], 'Lab records'), (['payer_medical_claims'], 'Payer claim records'),
                          (['enrollment_spans'], 'Enrollment records'), (['encounters'], 'Encounter records'),
                          (['conditions'], 'Condition records'), (['medication_lists'], 'Medication records')):
        out.append(_item('dq_family', 'restated_records', 'How many of our records were later restated by a newer version?',
                         f'All patients. {word} that a later record supersedes (supersedes_record_id points to them).',
                         {'family': 'restated_records', 'streams': streams}, 'count'))
    for unit, phrase in (('affiliations', 'Provider-practice affiliations listed'), ('rows', 'Monthly roster rows listing a provider-practice affiliation'),
                         ('providers', 'Distinct providers listed')):
        out.append(_item('dq_family', 'roster_after_departure', 'How many providers were still on the roster after they left?',
                         f"Release-wide. {phrase} on the monthly roster after the provider's departure.",
                         {'family': 'roster_after_departure', 'unit': unit}, 'count'))
    names = {'conditions': 'problems', 'medication_lists': 'meds', 'allergies': 'allergies', 'immunizations': 'immunizations',
             'labs': 'labs', 'notes': 'notes'}
    allst = list(names)
    for k in range(8):
        sub = allst if k == 0 else sorted(rng.sample(allst, rng.randint(1, 4)), key=allst.index)
        d = f"Release-wide. Records created by an EHR data conversion that copy an existing record ({', '.join(names[s] for s in sub)})."
        out.append(_item('dq_family', 'conversion_duplicates', 'After the EHR migrations, how many converted records duplicate data we already had?',
                         d, {'family': 'conversion_duplicates', 'streams': sub}, 'count'))
    return out


PROD_FILTERS = ['commercial', 'medicaid', 'medicare_advantage', 'medicare_ffs', 'marketplace', 'dual_ma', 'medicare', 'dual']


def vbc_variants(rng):
    # Mike 2026-10-25: medical paid on counted service-date claims; pharmacy net over all event-date transactions.
    out = []
    years = [2027, 2028, 2029]
    for y in years + [2030]:
        for g in (None, 'product', 'line of business'):
            d = (f"All patients. Sum of paid_cents on counted payer medical claims with service_from in {y}" + (f", by the claim's {g}" if g else '') + '.')
            out.append(_item('vbc_family', 'claims_finance', f'How much did payers pay on medical claims in {y}' + (f' by {g}' if g else '') + '?', d,
                             {'family': 'paid', 'year': y, 'group': g}, 'table' if g else 'value'))
        out.append(_item('vbc_family', 'claims_finance', f'How much did members owe out of pocket on {y} medical claims?',
                         f"All patients. Sum of patient_responsibility (the payer's adjudicated member liability) on payer medical claims "
                         f"whose latest adjudication is paid, service_from in {y}." + SCOPE, {'family': 'liability', 'year': y}, 'value'))
        out.append(_item('vbc_family', 'claims_finance', f'What did the plans pay for prescriptions in {y}?',
                         f"All patients. Sum of payer_paid_delta_cents on all pharmacy claim transactions with event date in {y} (reversals and recoupments netted).", {'family': 'rx', 'year': y}, 'value'))
    for y in years:
        for prod in [None] + rng.sample(PROD_FILTERS, 3):
            out.append(_item('vbc_family', 'claims_finance', f'What were total allowed dollars on {prod or "all"} medical claims in {y}?',
                             f"Allowed amount on final-adjudication paid {prod + ' ' if prod else ''}medical claims with service in {y}." + SCOPE,
                             {'family': 'allowed', 'year': y, 'prod': prod}, 'value'))
        for prod in rng.sample(['commercial', 'medicaid', 'medicare_advantage', 'medicare_ffs', 'marketplace', 'medicare'], 3):
            out.append(_item('vbc_family', 'pmpm', f'What was our {prod} PMPM in {y}?',
                             f"Allowed amount on final-adjudication paid {prod} medical claims with service in {y} / {y} {prod} member-months "
                             f"(delivered spans, through death)." + SCOPE, {'family': 'pmpm', 'year': y, 'prod': prod}, 'value'))
        out.append(_item('vbc_family', 'pmpm', f'What was our total medical PMPM in {y}?',
                         f"Allowed amount on final-adjudication paid medical claims with service in {y} (all products) / all {y} primary "
                         f"member-months." + SCOPE, {'family': 'pmpm', 'year': y, 'prod': None}, 'value'))
        for scope in ('all', 'primary'):
            for g in (None, 'product', 'line of business'):
                sc = 'all delivered spans including secondary products' if scope == 'all' else 'primary spans only'
                d = (f"Member-months in {y} (covered on the 1st, through death)" + (f" by {g}" if g else '') + f", {sc}; a span version "
                     f"superseded by a later delivered version of the same coverage (retro-termination, correction) is replaced by it (a "
                     f"span superseded only by a member_id_reissued continuation is kept), and each person counts once per month"
                     + (f" per {g}" if g else '') + '.' + SCOPE)
                out.append(_item('vbc_family', 'member_months', f'How many member-months did we have in {y}' + (f' by {g}' if g else '') + '?', d,
                                 {'family': 'mm', 'year': y, 'scope': scope, 'group': g}, 'table' if g else 'count'))
        for n in rng.sample([10, 25, 50, 100], 2):
            out.append(_item('vbc_family', 'top_n_cost_members', f'Who were our {n} most expensive members in {y}?',
                             f"The {n} persons with the highest allowed amount on final-adjudication paid medical claims with service in {y}." + SCOPE,
                             {'family': 'top_n', 'year': y, 'n': n}, 'patient_set'))
        for days in (30, 14):
            out.append(_item('vbc_family', 'readmissions', f'How many unplanned readmissions within {days} days did we have in {y}?',
                             f"Inpatient stays admitted in {y} as emergency or urgent (UB-04 admission type 1 or 2) within {days} days after a "
                             f"prior inpatient discharge of the same person." + STAY + SCOPE, {'family': 'readmit', 'year': y, 'days': days, 'rate': False}, 'count'))
            out.append(_item('vbc_family', 'readmissions', f'What was the {days}-day readmission rate in {y}?',
                             f"Inpatient stays admitted in {y} as emergency or urgent (UB-04 admission type 1 or 2) within 0-{days} days after a "
                             f"prior inpatient discharge of the same person, divided by inpatient stays discharged in {y} (discharge date, else "
                             f"service-to date), percent." + STAY + SCOPE, {'family': 'readmit', 'year': y, 'days': days, 'rate': True}, 'value'))
        for types, label in (([3], ' (elective)'), ([1], ''), ([1, 2], ''), ([4], '')):
            ts = ' or '.join(str(t) for t in types)
            out.append(_item('vbc_family', 'admissions', f'How many admissions of type {ts} did we have in {y}?',
                             f"Inpatient stays admitted in {y} with UB-04 admission type {ts}{label}." + STAY + SCOPE,
                             {'family': 'admits', 'year': y, 'types': types}, 'count'))
    for y in years + [2030]:
        out.append(_item('vbc_family', 'appointments', f'How many appointments were scheduled for {y}?',
                         f"Appointments with a slot in {y}, any final status." + APPTS + SCOPE, {'family': 'appt', 'year': y, 'statuses': None}, 'count'))
        for st, extra in (('cancelled', ' (including rescheduled)'), ('no_show', ''), ('fulfilled', '')):
            out.append(_item('vbc_family', 'appointments', f'How many appointments ended {st} in {y}?',
                             f"Appointments with a {y} slot whose final status is {st}{extra}." + APPTS + SCOPE,
                             {'family': 'appt', 'year': y, 'statuses': [st] + (['rescheduled'] if extra else [])}, 'count'))
        out.append(_item('vbc_family', 'appointments', f'How many appointments do we have booked each month in {y}?',
                         f"Appointments (any final status) by slot month (YYYY-MM), {y}." + APPTS + SCOPE, {'family': 'appt_month', 'year': y}, 'table'))
    for n in (2, 3, 4, 5):
        for st in ('no_show', 'cancelled'):
            out.append(_item('vbc_family', 'appointment_repeat', f'Which patients had {n} or more {st} appointments?',
                             f"Persons with >= {n} appointments whose final status is {st}, through as_of." + APPTS + SCOPE,
                             {'family': 'repeat', 'n': n, 'status': st}, 'patient_set'))
    for codes, label in ((['99381-99397'], '99381-99397 (preventive E/M)'), (['90832', '90834', '90837'], '90832, 90834 or 90837'),
                         (['G0438', 'G0439'], 'G0438 or G0439'), (['99202-99215'], '99202-99215 (office E/M)')):
        for y in rng.sample(years, 2):
            out.append(_item('vbc_family', 'paid_line_visits', f'How many visits with {label} did we bill in {y}?',
                             f"All patients. Distinct member-dates with a final paid claim line {label}, service in {y}.",
                             {'family': 'lines', 'codes': codes, 'year': y}, 'count'))
    for statuses, carc, y, distinct in ((['recouped'], 'CO-27', None, False), (['reversed', 'recouped'], None, 2029, True),
                                        (['reversed'], None, 2028, False), (['denied'], 'CO-16', 2028, True), (['denied'], 'CO-18', 2029, False),
                                        (['recouped'], None, None, True), (['voided'], None, 2028, False), (['denied'], 'CO-50', 2027, True)):
        lead = 'Distinct payer claims with a ' if distinct else ''
        st = ' or '.join(statuses)
        body = f"{lead}{st} adjudication{'' if distinct else 's'}" if distinct else f"{st[0].upper() + st[1:]} adjudications"
        d = f"All patients. {body}{f' with CARC {carc}' if carc else ''} {f'dated {y}' if y else 'through as_of'}."
        out.append(_item('vbc_family', 'adjudication_count', f'How many {st} adjudications{f" with {carc}" if carc else ""}?', d,
                         {'family': 'adj', 'statuses': statuses, 'carc': carc, 'year': y, 'distinct': distinct}, 'count'))
    for date in ('as_of', '2028-01-01', '2028-07-01', '2029-01-01', '2029-12-31', '2027-06-30'):
        out.append(_item('vbc_family', 'attribution_counts', 'How many members are attributed to each practice' + ('' if date == 'as_of' else f' on {date}') + '?',
                         f"Persons alive and enrolled on {date} by the attributed practice on their active span (spans with no attribution are "
                         f"not counted)." + SPANS + SCOPE, {'family': 'attr', 'date': date}, 'table'))
    return out


def generate(seed=SEED):
    rng = random.Random(seed)
    return dq_variants(rng) + vbc_variants(rng)


# ============================================================================================ independent reference
class Reference:
    def __init__(self, db, as_of=AS_OF):
        self.db, self.as_of = db, as_of
        self.db.row_factory = sqlite3.Row
        self._c = {}

    def rows(self, sql):
        if sql not in self._c: self._c[sql] = [dict(r) for r in self.db.execute(sql)]
        return self._c[sql]

    # ---- dq
    def files(self):
        return self.rows('SELECT * FROM delivery_file')

    @staticmethod
    def _fmt(r):
        p = r['parser']
        return p if p in ('hl7', 'x12', 'ncpdp', 'fhir') else 'flat'

    def _cond(self, r, c):
        if c == 'late':
            if r['is_original'] != 1: return False
            if r['name_date']: return (r['days_after_name_date'] or 0) >= 1
            if r['days_after_period_end'] is None or r['series_usual_lag_days'] is None: return False
            return r['days_after_period_end'] - r['series_usual_lag_days'] > 7
        if c == 'resend_identical': return r['resend_by_name'] == 1 and bool(r['content_identical_to'])
        return {'truncated': r['truncated'], 'empty_expected': r['empty_where_expected'], 'empty_any': r['empty_or_header_only'],
                'zero_byte': r['empty_file'], 'header_only': r['header_only'], 'v2_replacement': r['is_v2_replacement']}[c] == 1

    def _in_formats(self, r, t):
        fmts = t['formats']
        if t['condition'] == 'truncated' and len(fmts) > 1: return True  # a list of formats names evidence kinds
        if t['condition'] == 'resend_identical' and set(fmts) <= {'flat'}:
            return r['parser'] not in ('hl7', 'x12')  # whole-file resends: every format but HL7 batches / X12 interchanges
        return not fmts or self._fmt(r) in fmts

    def file_count(self, t):
        sel = [r for r in self.files() if self._cond(r, t['condition']) and self._in_formats(r, t)
               and (t['year'] is None or (r['delivered_date'] or '')[:4] == str(t['year'])) and (not r['delivered_date'] or r['delivered_date'] <= self.as_of)]
        return dict(Counter(r['feed'] for r in sel)) if t['by_feed'] else len(sel)

    def missing_periods(self, t):
        sel = [r for r in self.rows('SELECT * FROM delivery_gap') if t['year'] is None or r['missing_period'][:4] == str(t['year'])]
        return dict(Counter(r['feed'] for r in sel)) if t['by_feed'] else len(sel)

    def duplicate_charts(self, t):
        groups = defaultdict(set)
        for r in self.rows('SELECT source_key, person_id FROM raw_stg_person_map WHERE person_id IS NOT NULL'):
            kind = r['source_key'].split(':')[0]
            if kind in ('epic', 'athena', 'ecw', 'fhir'):
                groups[(r['person_id'], kind, r['source_key'].rsplit(':', 1)[0])].add(r['source_key'])
        per = defaultdict(lambda: [0, 0])
        for (pid, kind, _), keys in groups.items():
            per[pid][1 if kind == 'fhir' else 0] += len(keys) - 1
        extras = [max(v) for v in per.values() if max(v) > 0]
        return len(extras) if t['unit'] == 'people' else sum(extras)

    def _vendor_records(self):
        if 'vr' not in self._c:
            out = []
            for r in self.db.execute("SELECT patient_id, stream, event_date, available_date, payload FROM record WHERE payload LIKE '%\"source_system\": \"epic:%' "
                                     "OR payload LIKE '%\"source_system\": \"athena:%' OR payload LIKE '%\"source_system\": \"ecw:%'"):
                p = json.loads(r['payload'])
                ss = p.get('source_system') or ''
                vendor = ss.split(':')[0]
                if vendor not in ('epic', 'athena', 'ecw') or ':' not in ss: continue
                out.append((r['patient_id'], r['stream'], r['event_date'], r['available_date'], p.get('organization_id'), vendor))
            self._c['vr'] = out
        return self._c['vr']

    def _migrations(self):
        first = {}
        for pid, _, _, av, org, vendor in self._vendor_records():
            if org is None or pid is None: continue
            k = (org, vendor)
            if av is not None and (k not in first or av < first[k]): first[k] = av
        by_org = defaultdict(dict)
        for (org, vendor), av in first.items(): by_org[org][vendor] = av
        migs = []
        for org, vs in by_org.items():
            for new, nav in vs.items():
                for old, oav in vs.items():
                    if old != new and oav < nav:
                        migs.append({'org': org, 'new': new, 'old': old, 'go_live': nav[:8] + '01'})
        return migs

    def chart_renumbering(self, t):
        pov = defaultdict(set)
        for pid, _, _, _, org, vendor in self._vendor_records():
            if pid and org: pov[(org, vendor)].add(pid)
        mrns = defaultdict(set)
        for r in self.rows('SELECT source_key, person_id FROM raw_stg_person_map WHERE person_id IS NOT NULL'):
            if r['source_key'].startswith('mrn:'):
                mrns[(r['person_id'], r['source_key'].split(':')[1])].add(r['source_key'])
        hit = set()
        for m in self._migrations():
            for pid in pov[(m['org'], m['new'])] & pov[(m['org'], m['old'])]:
                if len(mrns.get((pid, m['org']), ())) > 1: hit.add(pid)
        return len(hit)

    def corrected_results(self, t):
        ids = {r['record_id'] for r in self.rows("SELECT record_id, event_date FROM measurement WHERE source_table = 'labs' AND result_status = 'corrected'")
               if t['year'] is None or (r['event_date'] or '')[:4] == str(t['year'])}
        return len(ids)

    def restated_records(self, t):
        return sum(1 for r in self.rows('SELECT stream FROM record_correction') if not t['streams'] or r['stream'] in t['streams'])

    def roster_after_departure(self, t):
        dates = {r['relpath']: ((r['period'] or '')[:7] or (r['name_date'] or r['delivered_date'] or '')[:7]) + '-01'
                 for r in self.rows("SELECT relpath, period, name_date, delivered_date FROM delivery_file WHERE feed = 'rosters'")}
        rows = self.rows('SELECT provider_npi, practice_id, term_date, _file FROM raw_rosters_aco_provider_roster')
        dep = {}
        for r in rows:
            td = (r['term_date'] or '').strip()
            if td:
                k = (r['provider_npi'], r['practice_id'])
                dep[k] = min(dep.get(k, td), td)
        stale = [r for r in rows if not (r['term_date'] or '').strip() and (r['provider_npi'], r['practice_id']) in dep
                 and dep[(r['provider_npi'], r['practice_id'])] <= (dates.get(r['_file']) or '')]  # gone for the whole roster month
        if t['unit'] == 'rows': return len(stale)
        if t['unit'] == 'providers': return len({r['provider_npi'] for r in stale})
        return len({(r['provider_npi'], r['practice_id']) for r in stale})

    def conversion_duplicates(self, t):
        migs = self._migrations()
        firsts = {}
        for pid, _, _, av, org, vendor in self._vendor_records():
            if pid and org and av:
                k = (pid, org, vendor)
                if k not in firsts or av < firsts[k]: firsts[k] = av
        n = 0
        for m in migs:
            gl = dt.date.fromisoformat(m['go_live'])
            nxt = (gl.replace(day=28) + dt.timedelta(days=4)).replace(day=1).isoformat()
            for pid, stream, ev, av, org, vendor in self._vendor_records():
                if org != m['org'] or vendor != m['new'] or stream not in t['streams'] or ev is None: continue
                if not (firsts.get((pid, org, m['old']), '9999') < nxt): continue
                if stream in ('labs', 'notes'):
                    n += ev < m['go_live']
                else:
                    n += ev == m['go_live']
        return n

    # ---- vbc
    def claims(self):
        return self.rows('SELECT * FROM payer_claim')

    @staticmethod
    def _allowed(c):
        if c['claim_type'] == 'institutional' and c['drg'] is not None and (c['allowed_cents'] or 0) == 0:
            return (c['paid_cents'] or 0) + (c['patient_responsibility_cents'] or 0)
        return c['allowed_cents'] or 0

    @staticmethod
    def _final_paid(c):
        return c['final_status'] == 'paid' and c['counts_in_metrics'] == 1

    @staticmethod
    def _in(d, y):
        return d is not None and str(y) + '-01-01' <= d <= str(y) + '-12-31'

    @staticmethod
    def _prod_ok(c, prod):
        if prod is None: return True
        return c['product'] == prod if prod not in ('medicare', 'dual') else c['line_of_business'] == prod

    def paid(self, t):
        # Mike 2026-10-25: reference arithmetic uses counted claims and service dates, not adjudication deltas.
        tot = defaultdict(int)
        for c in self.claims():
            if not c['counts_in_metrics'] or not self._in(c['service_from'], t['year']): continue
            key = {'product': c['product'], 'line of business': c['line_of_business']}.get(t['group'], '_')
            tot[key] += c['paid_cents'] or 0
        out = {k: round(v / 100.0, 2) for k, v in tot.items()}
        return out if t['group'] else out.get('_', 0.0)

    def allowed(self, t):
        return round(sum(self._allowed(c) for c in self.claims() if self._final_paid(c) and self._in(c['service_from'], t['year'])
                         and self._prod_ok(c, t['prod'])) / 100.0, 2)

    def liability(self, t):
        return round(sum(c['patient_responsibility_cents'] or 0 for c in self.claims() if self._final_paid(c) and self._in(c['service_from'], t['year'])) / 100.0, 2)

    def rx(self, t):
        return round(sum(r['payer_paid_delta_cents'] or 0 for r in self.rows('SELECT event_date, payer_paid_delta_cents FROM pharmacy_claim_event')
                         if self._in(r['event_date'], t['year'])) / 100.0, 2)

    def _member_months(self, y, scope, prod=None, key=None):
        deaths = {r['patient_id']: r['death_date'] for r in self.rows('SELECT patient_id, death_date FROM death')}
        months = [f'{y}-{m:02d}-01' for m in range(1, 13) if f'{y}-{m:02d}-01' <= self.as_of]
        out = set()
        for e in self.rows('SELECT * FROM enrollment_span'):
            if scope == 'primary' and e['product'] in ('medigap', 'cob_secondary'): continue
            if prod is not None and not self._prod_ok(e, prod): continue
            for m in months:
                if e['coverage_start'] <= m and (e['coverage_end'] is None or e['coverage_end'] >= m):
                    dd = deaths.get(e['patient_id'])
                    if dd is None or dd >= m:
                        out.add((e['patient_id'], m, e[key] if key else None))
        return out

    def mm(self, t):
        key = {'product': 'product', 'line of business': 'line_of_business'}.get(t['group'])
        s = self._member_months(t['year'], t['scope'], key=key)
        return dict(Counter(k for _, _, k in s)) if key else len(s)

    def pmpm(self, t):
        num = sum(self._allowed(c) for c in self.claims() if self._final_paid(c) and self._in(c['service_from'], t['year']) and self._prod_ok(c, t['prod']))
        mm = len(self._member_months(t['year'], 'primary' if t['prod'] is None else 'all', prod=t['prod']))
        return round(num / 100.0 / mm, 2) if mm else None

    def top_n(self, t):
        tot = defaultdict(int)
        for c in self.claims():
            if self._final_paid(c) and self._in(c['service_from'], t['year']): tot[c['patient_id']] += self._allowed(c)
        return sorted(sorted(tot, key=lambda p: (-tot[p], p))[:t['n']])

    def _stays(self):
        if 'stays' not in self._c:
            rows = self.rows("SELECT c.patient_id, c.admission_date, c.service_from, c.discharge_date, c.service_to, r.payload FROM payer_claim c "
                             "LEFT JOIN record r ON r.record_id = c.record_id WHERE c.claim_type = 'institutional' AND c.type_of_bill LIKE '011%' "
                             "AND c.counts_in_metrics = 1 AND c.final_status <> 'voided'")
            stays = {}
            for r in rows:
                k = (r['patient_id'], r['admission_date'] or r['service_from'])
                d = r['discharge_date'] or r['service_to']
                at = json.loads(r['payload']).get('admission_type') if r['payload'] else None
                s = stays.setdefault(k, {'disch': d, 'types': set()})
                if d and (s['disch'] is None or d > s['disch']): s['disch'] = d
                if at not in (None, ''): s['types'].add(int(at))
            self._c['stays'] = stays
        return self._c['stays']

    def readmit(self, t):
        st = self._stays()
        by_p = defaultdict(list)
        for (p, a), s in st.items(): by_p[p].append((a, s['disch']))
        n = 0
        for (p, a), s in st.items():
            if not self._in(a, t['year']) or not (s['types'] & {1, 2}): continue
            ad = dt.date.fromisoformat(a)
            if any(pa < a and pd and 0 <= (ad - dt.date.fromisoformat(pd)).days <= t['days'] for pa, pd in by_p[p]): n += 1
        if not t['rate']: return n
        den = sum(1 for s in st.values() if self._in(s['disch'], t['year']))
        return round(100.0 * n / den, 2) if den else None

    def admits(self, t):
        return sum(1 for (p, a), s in self._stays().items() if self._in(a, t['year']) and s['types'] & set(t['types']))

    def appts(self):
        return self.rows('SELECT patient_id, scheduled_date, final_status FROM appointment')

    def appt(self, t):
        return sum(1 for r in self.appts() if self._in(r['scheduled_date'], t['year']) and (not t['statuses'] or r['final_status'] in t['statuses']))

    def appt_month(self, t):
        return dict(Counter(r['scheduled_date'][:7] for r in self.appts() if self._in(r['scheduled_date'], t['year'])))

    def repeat(self, t):
        c = Counter(r['patient_id'] for r in self.appts() if r['final_status'] == t['status'] and r['patient_id'] and r['scheduled_date'] and r['scheduled_date'] <= self.as_of)
        return sorted(p for p, n in c.items() if n >= t['n'])

    def lines(self, t):
        claims = {c['payer_claim_id']: c for c in self.claims()}

        def hit(code):
            if not code: return False
            for c in t['codes']:
                if '-' in c:
                    lo, hi = c.split('-')
                    if lo <= code <= hi: return True
                elif code == c: return True
            return False
        seen = set()
        for l in self.rows('SELECT payer_claim_id, patient_id, service_date, cpt, hcpcs, counts_in_metrics FROM payer_claim_line'):
            c = claims.get(l['payer_claim_id'])
            if c is None or not self._final_paid(c) or l['counts_in_metrics'] != 1: continue
            if not hit(l['cpt'] if l['cpt'] is not None else l['hcpcs']): continue
            d = l['service_date'] or c['service_from']
            if self._in(d, t['year']): seen.add((l['patient_id'], d))
        return len(seen)

    def adj(self, t):
        sel = [a for a in self.rows('SELECT payer_claim_id, claim_status, carc, adjudication_date FROM payer_adjudication')
               if a['claim_status'] in t['statuses'] and (t['carc'] is None or a['carc'] == t['carc'])
               and (self._in(a['adjudication_date'], t['year']) if t['year'] else (a['adjudication_date'] or '') <= self.as_of)]
        return len({a['payer_claim_id'] for a in sel}) if t['distinct'] else len(sel)

    def attr(self, t):
        a = self.as_of if t['date'] == 'as_of' else t['date']
        deaths = {r['patient_id']: r['death_date'] for r in self.rows('SELECT patient_id, death_date FROM death')}
        per = defaultdict(set)
        for e in self.rows('SELECT * FROM enrollment_span'):
            if e['product'] in ('medigap', 'cob_secondary') or not e['attribution_practice_id']: continue
            if e['coverage_start'] <= a and (e['coverage_end'] is None or e['coverage_end'] >= a):
                dd = deaths.get(e['patient_id'])
                if dd is None or dd > a: per[e['attribution_practice_id']].add(e['patient_id'])
        return {k: len(v) for k, v in per.items()}

    def answer(self, truth):
        return getattr(self, truth['family'])(truth)


def same(got, truth_value, answer_type):
    """Route answer (count | value | rows of {key, value} | patient ids) vs the reference answer."""
    if truth_value is None or got is None: return False
    if answer_type == 'count': return int(got) == int(truth_value)
    if answer_type == 'value': return abs(float(got) - float(truth_value)) <= 0.011
    if answer_type == 'patient_set': return sorted(set(got)) == sorted(set(truth_value))
    if answer_type == 'table':
        g = {str(k): v for k, v in got.items()}
        t = {str(k): v for k, v in truth_value.items()}
        return set(g) == set(t) and all(abs(float(g[k]) - float(t[k])) <= 0.011 for k in t)
    return False
