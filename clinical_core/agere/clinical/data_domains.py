"""Honest 'not available': questions about data the warehouse was never sent.

A question names a data domain (patient-experience surveys, prior authorization, Star ratings, ED triage level, ...). When no received
table, column, record-stream field or coded value name could hold that domain, the answer says so and names what was checked, instead
of a generic 'not understood'. Anything that might exist falls through to normal planning. A period that starts after the snapshot is
reported the same way. Absence is checked against the catalog, never assumed from wording alone.

Two stages, one registry (_SPEC; DOMAINS is its label-pattern-hints view):

  pre    check(catalog, question)          before planning, for the domains whose wording is unambiguous (a domain with stage 'pre');
  post   after_decline(path, question, e)  after every answering layer declined (QuestionNotUnderstood): the whole registry, including
                                           the domains whose wording could also describe answerable data. Nothing that was answered is
                                           ever replaced; a question that is answerable but was not parsed still declines as before.

Catalog lookup is by NAME TOKENS, not substrings ('gene' is not found in 'generic'): the vocabulary is every table, column, record
stream and stream field of the received catalog plus the coded value names the warehouse holds (measurement / lab names and
categories, note-fact types, document, note and encounter types, condition concepts, social needs). A domain that lists `text` terms is
also looked up in the received note and document text (read-only, cached): a mention there means the data exists in some form, so no
absence is stated. `unless` names wording that points at an answerable neighbour (pharmacy rejects for prior authorization).
AGERE_STATED_ABSENCE=off disables the post stage. Synthetic data only; no model is called.
"""
import functools
import os
import re
import sqlite3
import threading
from datetime import date

# _SPEC rows: (label, question pattern, catalog name hints that would hold it, optional keys). A hint is one name: its tokens must all occur in ONE
# catalog name ('prior_auth' = a name with both tokens). Optional keys: stage ('pre' default | 'post'), unless (pattern: wording that
# means an answerable neighbouring question), text (literals searched in received note / document text), exists (what IS received).
_SPEC = [
    ('patient-experience survey (CAHPS / satisfaction) results',
     r'\b(?:h?cahps|patient satisfaction|satisfaction scores?|patient experience surveys?|press ganey|nps score)\b',
     ('survey', 'cahps', 'satisfaction', 'experience_score')),
    ('prior authorization requests and decisions', r'\bprior[- ]?auth(?:orization)?s?\b|\bpre-?auth(?:orization)?s?\b',
     ('prior_auth', 'prior_authorization', 'preauth', 'preauthorization', 'authorization_request', 'auth_decision', 'reject_code')),  # reject_code: pharmacy claims rejected for PA (prior_auth_access adapter)
    ('CMS Star ratings', r'\bstar ratings?\b|\bstars? (?:measure )?ratings?\b|\bpart [cd] stars?\b', ('star_rating', 'stars_rating', 'star_measure')),
    ('clinician compensation and RVUs', r'\b(?:w?rvus?|relative value units?|salar(?:y|ies)|physician compensation|provider pay)\b',
     ('rvu', 'salary', 'compensation', 'payroll')),
    ('genetic or genomic test results', r'\b(?:brca[12]?|genetic|genomic|germline|gene mutation|variant of uncertain)\b',
     ('genetic', 'genomic', 'variant', 'gene', 'brca', 'germline')),
    ('ED throughput timestamps', r'\bdoor[- ]to[- ](?:provider|doctor|doc|needle|balloon)\b|\b(?:ed|er) wait times?\b|\blength of (?:ed|er) wait\b',
     ('triage_time', 'provider_seen', 'door_to', 'arrival_time', 'wait_minutes', 'time_seen')),
    ('premium revenue, medical loss ratio and risk-adjustment revenue',
     r'\b(?:medical loss ratios?|mlr|premium revenues?|premium income|capitation revenue|risk[- ]adjust\w*\s+(?:revenue|payments?|dollars|money)|'
     r'(?:hcc|raf)\w*[^.?]{0,60}\b(?:revenue|dollars|money|payments?)|(?:revenue|dollars|money)[^.?]{0,60}\b(?:hcc|raf|risk[- ]adjust\w*))\b',
     ('premium_revenue', 'capitation_payment', 'mlr', 'medical_loss')),
    ('manufacturer rebates and net drug cost', r'\b(?:manufacturer rebates?|drug rebates?|net drug (?:cost|spend)|net of rebates)\b',
     ('rebate', 'net_drug_cost')),
    ('cost-accounting (internal cost) data',
     r'\b(?:cost accounting|cost to serve|direct costs?|cost per case|activity[- ]based cost|labor costs?|operating (?:margin|expenses?)|overhead (?:costs?|rates?))\b',
     ('cost_accounting', 'direct_cost', 'cost_per_case', 'labor_cost', 'operating_margin', 'overhead_cost')),
    # -- domains stated only after every answering layer declined --------------------------------------------------------
    ('ambulatory cardiac rhythm monitor results (Holter, event monitor, patch)',
     r'\b(?:holter|event monitors?|ambulatory (?:ecg|ekg|rhythm)|zio patch|cardiac (?:event|rhythm) monitors?)\b',
     ('holter', 'event_monitor', 'ambulatory_ecg', 'rhythm_monitor', 'zio', 'ecg', 'ekg'),
     {'stage': 'post', 'text': ('holter', 'event monitor', 'ambulatory ecg', 'ambulatory ekg', 'zio patch')}),
    ('Medicare fee-schedule and payer contract rates',
     r'\b(?:percent(?:age)?|%) of medicare\b|\bmedicare (?:fee schedule|rates?|reimbursement rates?)\b|\b(?:relative|compared?|benchmarked?) (?:to|with|against) medicare (?:rates?|prices?|payments?)\b',
     ('fee_schedule', 'medicare_rate', 'medicare_fee', 'contract_rate', 'rate_table', 'price_schedule', 'negotiated_rate'),
     {'stage': 'post', 'exists': 'allowed and paid amounts per claim are received, but no Medicare fee schedule or contract rate table to compare them with'}),
    ('program or intervention cost and return-on-investment data',
     r'\b(?:roi|return on investment|cost[- ]effectiveness|net savings)\b[^.?]{0,80}\b(?:program|initiative|intervention|outreach|care management|health workers?|chw)\b'
     r'|\b(?:program|initiative|intervention|outreach|care management|health workers?|chw)\b[^.?]{0,80}\b(?:roi|return on investment|cost[- ]effectiveness|net savings)\b',
     ('program_cost', 'intervention_cost', 'program_spend', 'program_budget', 'roi', 'return_on_investment', 'program_expense'),
     {'stage': 'post', 'exists': 'program enrollments and claims are received, but no program, staffing or intervention cost'}),
    ('ED triage level and acuity (ESI) scores',
     r'\bemergency severity index\b|\besi (?:level|score|acuity|[1-5])\b|\btriage (?:level|acuity|category|score|class)\b|\bacuity (?:level|score)s?\b',
     ('triage_level', 'triage_acuity', 'triage_category', 'triage_score', 'acuity_level', 'acuity_score', 'esi', 'esi_level', 'emergency_severity'),
     {'stage': 'post', 'exists': 'ED visit groups carry arrival and departure times but no triage level or acuity score'}),
    ('prior authorization requests, approvals and denials',
     r'\bprior[- ]?auth(?:orization)?s?\b|\bpre-?auth(?:orization)?s?\b',
     ('prior_auth', 'prior_authorization', 'preauth', 'preauthorization', 'authorization_request', 'auth_decision', 'auth_status', 'authorization_status'),
     {'stage': 'post', 'unless': r'\b(?:reject\w*|pharmacy|formulary|rx|drug|prescription)\b',
      'exists': 'only pharmacy-claim reject codes are received, no authorization requests or determinations'}),
    ('staffing and bed-capacity data',
     r'\b(?:nurse[- ]to[- ]patient ratios?|nurse staffing|staffing (?:levels?|ratios?)|bed occupancy|occupancy rates?|operating room (?:utilization|turnover)|or utilization|bed turnover)\b',
     ('staffing', 'occupancy', 'bed_count', 'bed_census', 'or_utilization', 'nurse_ratio', 'fte', 'staffed_beds'), {'stage': 'post'}),
    ('workforce (FTE, turnover, burnout) data', r'\b(?:ftes?|full[- ]time equivalents?|clinician turnover|provider turnover|physician burnout|clinician burnout)\b',
     ('fte', 'turnover', 'burnout', 'full_time_equivalent', 'headcount'), {'stage': 'post'}),
    ('quality-program scores and payment adjustments',
     r'\bmips (?:final |composite )?(?:score|payment adjustments?|bonus)\b|\bquality bonus payments?\b|\bvalue-based purchasing (?:score|adjustments?|payments?)\b|\bleapfrog\b|\bhospital star ratings?\b',
     ('mips', 'payment_adjustment', 'quality_bonus', 'vbp', 'leapfrog', 'quality_payment'), {'stage': 'post'}),
    ('cancer staging and tumor registry data',
     r'\b(?:cancer|tumou?r|tnm) stag(?:e|es|ing)\b|\btumou?r (?:size|grade)\b|\bgleason\b|\bcancer registry\b',
     ('cancer_stage', 'tnm', 'tumor_stage', 'tumor_grade', 'gleason', 'tumor_size', 'cancer_registry'),
     {'stage': 'post', 'text': ('tnm', 'gleason', 'tumor grade', 'tumour grade', 'tumor size')}),
    ('pain scores and body-composition measures', r'\b(?:pain (?:scores?|scales?|levels?)|waist circumference|body fat|waist[- ]to[- ]hip)\b',
     ('pain_score', 'pain_scale', 'pain_level', 'waist', 'body_fat', 'pain_rating'),
     {'stage': 'post', 'text': ('pain score', 'pain scale', 'waist circumference', 'body fat')}),
    ('patient-portal activity', r'\b(?:patient portal|mychart|portal (?:messages?|activations?|logins?|usage|enrol\w+))\b',
     ('portal', 'mychart'), {'stage': 'post', 'text': ('mychart', 'patient portal')}),
    ('hospital supply, implant and chargemaster cost data',
     r'\b(?:charge ?master|supply (?:cost|chain|spend)s?|implant costs?|drug acquisition costs?)\b',
     ('chargemaster', 'charge_master', 'supply_cost', 'implant_cost', 'acquisition_cost', 'supply_spend'), {'stage': 'post'}),
    ('education, employment and veteran status',
     r'\b(?:education(?:al)? (?:level|attainment)|employment status|veteran status|religio(?:n|us affiliation))\b',
     ('education_level', 'educational_attainment', 'employment_status', 'veteran_status', 'religion'), {'stage': 'post'}),
    ('obstetric and neonatal outcomes (birth weight, Apgar, delivery mode)',
     r'\b(?:birth ?weights?|apgar|gestational age|c-?section rates?|cesarean rates?|delivery mode|nicu admissions?)\b',
     ('birth_weight', 'apgar', 'gestational_age', 'delivery_mode', 'cesarean', 'nicu'),
     {'stage': 'post', 'text': ('apgar', 'birth weight', 'gestational age', 'cesarean', 'c-section')}),
    ('wearable, remote-monitoring and continuous glucose device data',
     r'\b(?:wearables?|fitbit|apple watch|remote patient monitoring|rpm devices?|continuous glucose monitor\w*|cgm (?:data|readings?|time in range)|time in range|step counts?)\b',
     ('wearable', 'cgm', 'step_count', 'rpm', 'device_reading', 'time_in_range', 'remote_monitoring'),
     {'stage': 'post', 'text': ('cgm', 'continuous glucose', 'fitbit', 'wearable', 'apple watch', 'remote patient monitoring')}),
    ('patient-safety and adverse-event reports', r'\b(?:incident reports?|sentinel events?|never events?|medication errors?|patient safety events?|adverse drug events?)\b',
     ('incident_report', 'sentinel_event', 'never_event', 'medication_error', 'safety_event', 'adverse_event', 'adverse_drug_event'),
     {'stage': 'post', 'text': ('incident report', 'sentinel event', 'never event', 'medication error', 'adverse drug event')}),
]
DOMAINS = [row[:3] for row in _SPEC]  # (label, pattern, hints): the registry other modules and tests read
MONTHS = {m: i for i, m in enumerate('january february march april may june july august september october november december'.split(), 1)}
_CAMEL, _TOKEN = re.compile(r'([a-z0-9])([A-Z])'), re.compile(r'[a-z0-9]+')
_CACHE, _LOCK = {}, threading.Lock()
# coded value names the semantic layer holds besides its schema: (table, column) pairs read DISTINCT (read-only, cached per file version)
VALUE_NAMES = (('measurement', 'name'), ('measurement', 'component'), ('measurement', 'concept'), ('measurement', 'lab_category'),
               ('note_fact', 'fact_type'), ('hie_document', 'document_type'), ('scanned_document', 'document_type'),
               ('clinical_note', 'note_type'), ('encounter', 'type'), ('condition', 'concept'), ('social_need', 'need'),
               ('cancer_screening', 'test'), ('immunization', 'vaccine'), ('therapy_barrier', 'barrier_type'))
TEXT_STREAMS = ('notes', 'imported_documents', 'scanned_documents', 'encounter_text_fragments', 'hie_documents', 'referral_documents')


def enabled():
    return os.environ.get('AGERE_STATED_ABSENCE', 'on').strip().lower() != 'off'


def _tokens(name):
    return frozenset(_TOKEN.findall(_CAMEL.sub(r'\1 \2', str(name)).lower()))


def _match(hint, token):
    """One hint token against one name token: equal, or the name token is the hint with a short inflection ('survey' / 'surveys')."""
    return token == hint or (len(hint) >= 4 and token.startswith(hint) and token[len(hint):] in ('s', 'es', 'd', 'ed', 'ing'))


def _vocabulary(catalog):
    """({name token sets}, counts) over the catalog schema, record-stream fields and coded value names; cached per catalog file version."""
    target = getattr(catalog, 'target', None)
    key = None
    try:
        st = target.stat(); key = (str(target), st.st_mtime_ns, st.st_size, len(catalog.tables), len(catalog.stream_fields or {}))
    except (AttributeError, OSError):
        pass
    with _LOCK:
        if key is not None and key in _CACHE: return _CACHE[key]
    names, stats = set(), {'tables': len(catalog.tables), 'columns': 0, 'record streams': len(catalog.stream_fields or {}), 'stream fields': 0, 'coded value names': 0}
    names.update(catalog.tables)
    for cols in catalog.tables.values():
        names.update(cols); stats['columns'] += len(cols)
    for stream, fields in (catalog.stream_fields or {}).items():
        fields = list(fields) if isinstance(fields, (list, tuple, set, dict)) else []
        names.add(stream); names.update(fields); stats['stream fields'] += len(fields)
    values = set()
    if target is not None:
        try:
            db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
            try:
                for table, column in VALUE_NAMES:
                    if column not in catalog.tables.get(table, ()): continue
                    try:
                        values.update(str(r[0]) for r in db.execute(f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL') if r[0] is not None)
                    except sqlite3.Error:
                        continue
            finally:
                db.close()
        except sqlite3.Error:
            pass
    stats['coded value names'] = len(values)
    out = (frozenset(t for t in map(_tokens, names | values) if t), stats)
    with _LOCK:
        if key is not None: _CACHE[key] = out
    return out


def _held(vocab, hint):
    parts = [p for p in hint.lower().split('_') if p]
    return any(all(any(_match(p, t) for t in entry) for p in parts) for entry in vocab)


def _in_text(catalog, terms):
    """Terms found in the received note / document text, {term: records}; cached per catalog file version and term."""
    target = getattr(catalog, 'target', None)
    found = {}
    if target is None: return found
    try:
        st = target.stat(); version = (str(target), st.st_mtime_ns, st.st_size)
    except OSError:
        return found
    todo = []
    with _LOCK:
        for t in terms:
            if (version, t) in _CACHE: found[t] = _CACHE[(version, t)]
            else: todo.append(t)
    if todo:
        try:
            db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
            try:
                marks = ','.join('?' * len(TEXT_STREAMS))
                for t in todo:
                    like = '%' + t.replace('%', '').replace('_', '') + '%'
                    n = 0
                    try:
                        n += db.execute(f'SELECT COUNT(*) FROM record WHERE stream IN ({marks}) AND payload LIKE ?', (*TEXT_STREAMS, like)).fetchone()[0]
                    except sqlite3.Error:
                        pass
                    try:
                        n += db.execute('SELECT COUNT(*) FROM scanned_document_text WHERE text LIKE ?', (like,)).fetchone()[0]
                    except sqlite3.Error:
                        pass
                    with _LOCK: _CACHE[(version, t)] = n
                    found[t] = n
            finally:
                db.close()
        except sqlite3.Error:
            return found
    return found


def _entry(row):
    label, pattern, hints = row[:3]
    return label, pattern, hints, (row[3] if len(row) > 3 else {})


def check(catalog, question, stage='pre'):
    """(label, reason) when the question needs data the warehouse does not hold, else None. stage 'pre' (before planning): the domains
    with unambiguous wording; stage 'post' (after every layer declined): every domain."""
    text = question.lower()
    loaded = None
    for row in _SPEC:
        label, pattern, hints, opts = _entry(row)
        if stage == 'pre' and opts.get('stage', 'pre') != 'pre': continue
        if not re.search(pattern, text, re.I): continue
        if opts.get('unless') and re.search(opts['unless'], text, re.I): continue
        loaded = loaded or _vocabulary(catalog)
        vocab, stats = loaded
        if any(_held(vocab, h) for h in hints): continue  # something received could hold it: not absent (a later, narrower domain may still apply)
        # Every domain can arrive as narrative. Literal hits require investigation
        # even when no registry author anticipated this specific source form.
        terms=tuple(dict.fromkeys([*(opts.get('text') or ()),*(h.replace('_',' ') for h in hints),
                                  *(m.group(0) for m in re.finditer(pattern,text,re.I))]))
        if any(_in_text(catalog,terms).values()):continue
        checked = ', '.join(f'{n} {k}' for k, n in stats.items() if n)
        reason = (f'No mapped structured representation for {label}: no received table, column or feed field name matches ({", ".join(hints)})'
                  + '; literal narrative searches found no matching terms, but comprehensive narrative absence is not established'
                  + f'. Checked {checked}.' + (f' Received instead: {opts["exists"]}.' if opts.get('exists') else ''))
        return label, reason
    as_of = catalog.identity.get('as_of')
    if as_of:
        end = date.fromisoformat(as_of[:10])
        periods = [(int(y), MONTHS.get((m or '').lower(), 1)) for m, y in re.findall(
            r'\b(?:(' + '|'.join(MONTHS) + r')\s+)?(20\d\d)\b', text)]
        if periods and all((y, mo) > (end.year, end.month) for y, mo in periods):
            return 'data for that period', f'The warehouse data runs through {as_of[:10]}; the requested period is after it.'
    return None


def answer(question, label, reason, stage='pre', receipt=None):
    after = stage == 'post'
    established=label=='data for that period'
    return {'schema': 'agere-data-absent/1', 'synthetic': True, 'question': question, 'route': 'shared_planner',
            'answer': {'status': 'not_available', 'answer_type': 'data_absent', 'answerable': False,
                       'data_absence_established': established, 'discovery_status':'period_outside_snapshot' if established else 'structured_representation_unmapped',
                       'narrative_review_complete':False, 'headline': 'Not available: ' + reason,
                       'missing': [label], 'patient_ids': [], 'actionable_cohort': False,
                       'limitations': ['No accepted complete answer was produced. Catalog names and literal narrative searches do not establish comprehensive data absence.'
                                       if after else 'Catalog discovery only; narrative coverage and complete source absence are unestablished.']},
            'planner_receipt': {'route': 'shared_planner', 'interpretation': {'interpreter': 'data_domain_check', 'domain': label,
                                                                               'stage': 'after_decline' if after else 'before_planning'},
                                'new_paid_api_calls': (receipt or {}).get('new_paid_api_calls', 0)},
            'new_paid_api_calls': (receipt or {}).get('new_paid_api_calls', 0),
            'planning_economics': {'known_api_estimate_usd': (receipt or {}).get('known_api_estimate_usd', 0),
                                   'api_cost_known':(receipt or {}).get('api_cost_known',True)}}


def after_decline(path, question, exc=None):
    """The final answer for a question every layer declined, when the catalog shows the data it names was never received; else None
    (the generic decline stands). Only a 'not understood' decline is replaced, never an error and never an answered question."""
    if not enabled(): return None
    from .question_input import QuestionNotUnderstood
    if exc is not None and not isinstance(exc, QuestionNotUnderstood): return None
    try:
        from .request_plan import Catalog
        found = check(Catalog.load(path), question, stage='post')
    except (OSError, ValueError, KeyError, sqlite3.Error):
        return None
    return answer(question, *found, stage='post', receipt=getattr(exc, 'planner_receipt', None)) if found else None
