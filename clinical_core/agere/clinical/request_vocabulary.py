"""Versioned business measures in the shared request language.

Aliases select definitions; they never supply SQL. The existing metric/quality/
RAF libraries own definitions. Population, period, grouping and exclusion compose
with these names through the same request contract and graph.
"""
from collections import defaultdict
from datetime import date
import hashlib
import json
import re
import sqlite3

from . import metrics
from .age import age_at

VERSION = 'shared-request-vocabulary/1'
CONDITION_EVIDENCE_POLICY = (
    'EHR diagnosis labels (plus ehr_problem_list entries whose diagnosis flag is unknown) or a counted payer-claim '
    'ICD-10 diagnosis in the condition\'s code set (Mike, 2026-10-21: claims-only members are included). '
    'Diabetes and CKD also count laboratory evidence of the condition (Mike, 2026-10-21); rows flagged as not a diagnosis '
    '(rule-outs) stay excluded. For other conditions, lab-inferred evidence is excluded. '
    'Assessment topics, note-only and medication-indication evidence are excluded. '
    'A match does not establish current clinical status.')
ALIASES = {
    'denial rate': 'first_pass_denial_rate',
    'first pass denial rate': 'first_pass_denial_rate',
    'first-pass denial rate': 'first_pass_denial_rate',
    'payer claim denial rate': 'payer_claim_denial_rate',
    'no-show rate': 'no_show_rate', 'no show rate': 'no_show_rate',
    'missed appointment rate': 'no_show_rate',
    'pmpm': 'allowed_pmpm', 'allowed pmpm': 'allowed_pmpm',
    'payer allowed pmpm': 'payer_allowed_pmpm',
    'readmission rate': 'readmission_30d', '30-day readmission rate': 'readmission_30d',
    'readmissions': 'readmission_30d',
    'los': 'average_length_of_stay', 'average los': 'average_length_of_stay',
    'length of stay': 'average_length_of_stay', 'average length of stay': 'average_length_of_stay',
    'ed visits per 1,000': 'all_source_ed_visits_per_1000',
    'ed visits per 1000': 'all_source_ed_visits_per_1000',
    'er visits per 1,000': 'all_source_ed_visits_per_1000',
    'emergency visits per 1,000': 'all_source_ed_visits_per_1000',
    'ed utilization per 1,000': 'all_source_ed_visits_per_1000',
    'inpatient admissions per 1,000': 'inpatient_admissions_per_1000',
    'hospital admissions per 1,000': 'hospital_admissions_per_1000', 'admissions per 1,000': 'hospital_admissions_per_1000',
    'raf': 'cms_hcc_raf', 'average raf': 'cms_hcc_raf',
    'kidney health evaluation': 'kidney_health_evaluation_diabetes',
    'ked': 'kidney_health_evaluation_diabetes',
    'blood pressure control': 'measure.cbp', 'a1c poor control': 'measure.gsd_poor_control',
    'breast cancer screening': 'measure.bcs', 'colorectal cancer screening': 'measure.col',
    'statin adherence': 'measure.pdc_statins',
    # analyst wording of the same governed definitions (2026-10-24)
    'hba1c poor control': 'measure.gsd_poor_control', 'hemoglobin a1c poor control': 'measure.gsd_poor_control',
    'glycemic status assessment poor control': 'measure.gsd_poor_control', 'poor a1c control': 'measure.gsd_poor_control',
    'pdc for statins': 'measure.pdc_statins', 'proportion of days covered for statins': 'measure.pdc_statins',
    'statin proportion of days covered': 'measure.pdc_statins', 'adherence to statins': 'measure.pdc_statins',
    'cholesterol medication adherence': 'measure.pdc_statins',
    'proportion of days covered for diabetes medications': 'measure.pdc_diabetes', 'adherence to diabetes medications': 'measure.pdc_diabetes',
    'proportion of days covered for ras antagonists': 'measure.pdc_rasa', 'adherence to ras antagonists': 'measure.pdc_rasa',
    'hypertension medication adherence': 'measure.pdc_rasa', 'adherence to ace inhibitors or arbs': 'measure.pdc_rasa',
    'kidney health evaluation for patients with diabetes': 'kidney_health_evaluation_diabetes',
    'kidney health evaluation in diabetes': 'kidney_health_evaluation_diabetes',
    'follow-up after ed visit': 'ed_followup_7d', 'follow up after ed visit': 'ed_followup_7d', 'follow-up after an ed visit': 'ed_followup_7d',
    'ed follow-up': 'ed_followup_7d', 'ed follow up': 'ed_followup_7d', 'follow-up after emergency department visit': 'ed_followup_7d',
    'post-discharge follow-up': 'post_discharge_followup_7d', 'follow-up after hospital discharge': 'post_discharge_followup_7d',
    'follow-up after discharge': 'post_discharge_followup_7d', 'follow-up after hospitalization': 'post_discharge_followup_7d',
    'readmissions within 30 days': 'readmission_30d', '30-day readmissions': 'readmission_30d', 'hospital readmission rate': 'readmission_30d',
    'all-cause readmissions': 'readmission_30d', 'all-cause readmission rate': 'readmission_30d', '30 day readmission rate': 'readmission_30d',
    'preventable admissions': 'measure.pqi_90_overall_composite', 'prevention quality indicators': 'measure.pqi_90_overall_composite',
    'pqi': 'measure.pqi_90_overall_composite',
}
# Mike, 2026-10-21: medical-claim finance uses payer-adjudicated claims (counted rows). Generic names resolve to the
# payer metric when the warehouse has counted payer claims; names of a provider-side convention keep it.
ALIASES.update({
    'claim denial rate': 'first_pass_denial_rate', 'medical claim denial rate': 'first_pass_denial_rate',
    'medical pmpm': 'allowed_pmpm', 'medical cost per member per month': 'allowed_pmpm',
    'paid pmpm': 'payer_paid_medical_pmpm', 'paid per member per month': 'payer_paid_medical_pmpm',
    'medical paid': 'payer_medical_paid', 'total medical paid': 'payer_medical_paid',
    'medical paid dollars': 'payer_medical_paid', 'total paid amount on medical claims': 'payer_medical_paid',
    'paid amount on medical claims': 'payer_medical_paid',
})
# An outcome word in "what share of X were <word>" names the governed rate for that outcome.
EVENT_METRICS = {r'denied': 'first_pass_denial_rate', r'no[- ]shows?': 'no_show_rate'}
PAYER_PREFERRED = {'first_pass_denial_rate': 'payer_claim_denial_rate', 'allowed_pmpm': 'payer_allowed_medical_pmpm'}
PROVIDER_NAMED = re.compile(r'first[- ]pass|provider', re.I)
BASIS_VARIANTS = {k: {'paid': 'payer_paid_medical_pmpm', 'allowed': 'payer_allowed_medical_pmpm'}
                  for k in ('allowed_pmpm', 'payer_allowed_medical_pmpm', 'payer_paid_medical_pmpm')}
# Nouns that restate a measure's own population, keyed by its grain (and, for stays, its anchor).
POPULATION_WORDS = {'claim': r'(?:medical\s+)?claims?|services|dates? of service',
                    'member_month': r'members|member[- ]months|enrolled members',
                    'appointment': r'appointments', 'stay:discharge_date': r'(?:inpatient\s+)?discharges',
                    'stay:admit_date': r'(?:inpatient\s+)?(?:admissions|stays)'}


def preferred(ident, quote, tables):
    """The metric a matched name means in this warehouse, or None when its sources were not received."""
    words = ' '.join(quote) if isinstance(quote, list) else str(quote)
    payer = 'counts_in_metrics' in tables.get('payer_claim', [])
    if payer and ident in PAYER_PREFERRED and not PROVIDER_NAMED.search(words):
        ident = PAYER_PREFERRED[ident]
    if ident.startswith('payer_') and not payer: return None  # payer-feed measure without a received payer feed
    return ident if ident in definitions() else None


def population_words(definition):
    grain = definition.get('grain', {}).get('entity')
    anchor = definition.get('time_anchor', {}).get('field', '').rpartition('.')[2]
    return POPULATION_WORDS.get(f'{grain}:{anchor}') or POPULATION_WORDS.get(grain)


GROUPS = {'payer': 'payer_id', 'payer_id': 'payer_id', 'coverage': 'coverage_type',
          'coverage type': 'coverage_type', 'coverage_type': 'coverage_type',
          'pcp': 'pcp_clinician_id', 'practice': 'pcp_practice_id', 'sex': 'sex_reported',
          'age': 'age', 'age band': 'age_band', 'age_band': 'age_band'}


def definitions():
    from . import measures, risk_adjustment
    result = {k: m for k, m in metrics.library().items()
              if (m.get('sql_template') and m['output']['returns'] in ('value', 'count'))
              or m.get('engine', {}).get('measures')}
    for ident, definition in list(result.items()):
        measure_id = definition.get('engine', {}).get('measures')
        if measure_id:
            result[ident] = {**definition, 'measure_definition': measures.library()[measure_id]}
    result['cms_hcc_raf'] = {
        'id': 'cms_hcc_raf', 'version': 1, 'name': 'CMS-HCC calculated payment RAF',
        'summary': 'Existing risk_adjustment engine; received diagnoses, governed model tables and payment rules. '
                   'A calculated score is not a payer-reported RAF. Missing qualifying data remains unsupported.',
        'engine': {'risk_adjustment': True}, 'unit': 'RAF',
        'model_manifest': risk_adjustment.manifest(),
        'payment_parameters': json.loads((risk_adjustment.TABLES/'payment_parameters.json').read_text()),
        'time_anchor': {'default': 'as_of_year'}, 'output': {'returns': 'value'}}
    return result


def aliases():
    names = dict(ALIASES)
    for ident, definition in definitions().items():
        names.setdefault(ident.replace('_', ' '), ident)
        names.setdefault(ident, ident)
        if ident.startswith('measure.'):
            names.setdefault(ident.partition('.')[2], ident)
        names.setdefault(definition['name'].lower(), ident)
        # The library's own aliases ("kidney health evaluation", "pdc for statins", "pqi 90", "follow-up after ed visit") name the same definition
        # in analyst wording; a name already claimed by an earlier, hand-written alias keeps that meaning.
        for alias in definition.get('aliases') or []:
            if isinstance(alias, str) and len(alias.strip()) >= 3: names.setdefault(alias.strip().lower(), ident)
    return names


def library_alias_only():
    """The names that exist only because the library lists them as aliases of a definition (analyst wording), not the hand-written or id / title names."""
    core = {k.lower() for k in ALIASES}
    for ident, definition in definitions().items():
        core |= {ident.replace('_', ' ').lower(), ident.lower(), definition['name'].lower()}
        if ident.startswith('measure.'): core.add(ident.partition('.')[2].lower())
    return {k for k in aliases() if k.lower() not in core}


def digest(definition):
    return hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()

def concept_definitions():
    from . import concepts, drug_classes
    return {'condition': {k: {'version': 1, 'definition_digest': digest([v, CONDITION_EVIDENCE_POLICY])}
                           for k, v in concepts.CONDITIONS.items()},
            'drug_fill': {k: {'version': 1, 'definition_digest': digest([k, concepts.FILL_EVIDENCE, concepts.FILL_STATUSES,
                                                                      drug_classes.CLASSES.get(k)])}
                          for k in drug_classes.CLASSES},
            'encounter': {'ed_visit': {'version': 2, 'relation': 'utilization_event', 'kind': 'ed_visit',
                                       'definition': 'Deduplicated ADT, EHR and claims ED events'}}}


def concept_sql(expr, compiler, scope):
    from . import concepts
    from .request_plan import literal, PlanError, quote_text
    if set(expr) != {'op', 'kind', 'names', 'version', 'period', 'quote'} or expr['version'] != 1:
        raise PlanError('Explicit versioned concept and period required')
    kind, names = expr['kind'], expr['names']
    registry = concept_definitions()
    if kind not in ('condition', 'drug_fill') or not isinstance(names, list) or not names or len(names) > 30:
        raise PlanError('Unknown named concept')
    if any(n.partition('@')[0] not in registry[kind] for n in names): raise PlanError('Unknown concept name')
    if kind == 'condition' and not all(concepts.valid_scope(n) for n in names): raise PlanError('Narrowed codes outside the concept')
    if kind != 'condition' and any('@' in n for n in names): raise PlanError('Unknown concept name')
    # A named definition cannot consume unrelated requirements merely because a
    # model included them in its quote. Reparse this leaf using the same concept
    # vocabulary and require the names, polarity and period to agree.
    from .request_language import Parser
    quoted = quote_text(expr['quote'])
    parser = Parser(compiler.catalog, quoted)
    parser.conditions.update({
        phrase: preset[1] for phrase, (_, preset) in parser.sources.items()
        if preset and preset[0] == 'condition'
    })
    try:
        parsed, tail = parser.expression(quoted)
    except PlanError as exc:
        raise PlanError('Named concept wording is not fully accounted for: ' + quoted) from exc
    if (tail.strip() or parsed.get('op') != 'concept' or parsed.get('kind') != kind
            or set(parsed.get('names', [])) != set(names) or parsed.get('period') != expr['period']):
        raise PlanError('Named concept wording contains a different or uncompiled requirement: ' + quoted)
    table = 'condition' if kind == 'condition' else 'medication'
    fields = {'patient_id', 'concept', 'is_diagnosis'} if kind == 'condition' else {
        'patient_id', 'drug_class', 'evidence', 'status', 'fill_reversed'}
    if not fields <= set(compiler.catalog.tables.get(table, [])):
        raise PlanError('Required concept evidence fields are unavailable: '+table)
    compiler.serial += 1
    alias = 'concept_'+str(compiler.serial)
    outer = compiler.field('patient.patient_id', scope)
    column = 'concept' if kind == 'condition' else 'drug_class'
    if kind == 'condition':
        evidence = concepts.condition_evidence_sql(names, compiler.catalog.tables)
        where = 'TRUE'
        if expr['period']:
            start, end = (date.fromisoformat(expr['period'][k]).isoformat() for k in ('start', 'end'))
            if end > compiler.catalog.identity['as_of']: raise PlanError('Concept period exceeds snapshot')
            where = f'{alias}.event_date BETWEEN {literal(start)} AND {literal(end)}'
        # Uncorrelated: the condition population is built once, then the patient filter is a set lookup.
        return f'{outer} IN (SELECT {alias}.patient_id FROM {evidence} {alias} WHERE {where})'
    where = f'{alias}.{column} IN ({",".join(map(literal, names))})'
    where += ' AND '+concepts.picked_up_sql(alias)
    period = expr['period']
    if period:
        if set(period) != {'start', 'end'}: raise PlanError('Explicit concept period required')
        try:
            start, end = (date.fromisoformat(period[k]).isoformat() for k in ('start', 'end'))
        except (ValueError, TypeError) as exc: raise PlanError('Invalid concept period') from exc
        if start > end or end > compiler.catalog.identity['as_of']: raise PlanError('Concept period exceeds snapshot')
        if 'event_date' not in compiler.catalog.tables[table]: raise PlanError('Concept date unavailable')
        where += f' AND {alias}.event_date BETWEEN {literal(start)} AND {literal(end)}'
        from . import decision_context
        if (start, end) == tuple(decision_context.rolling_year(compiler.catalog.identity['as_of'])):
            compiler.window_numbers.add('12')  # "in the last 12 months": the rolling-year period encodes the 12
        compiler.notes.append(f'Medication use means at least one picked-up fill dated {start} through {end}'
                              + (' (the 12 months ending at the as-of date: current use).' if end == compiler.catalog.identity['as_of'] else '.'))
    return f'EXISTS (SELECT 1 FROM {table} {alias} WHERE {alias}.patient_id = {outer} AND {where})'


def parameters(ident, period, groups):
    m = definitions()[ident]
    return {'id': ident, 'version': m['version'], 'definition_digest': digest(m),
            'period': period, 'group_by': groups}


def validate(p):
    if not isinstance(p, dict) or set(p) != {'id', 'version', 'definition_digest', 'period', 'group_by'}:
        raise ValueError('A versioned governed measure, period and grouping are required')
    m = definitions().get(p['id'])
    if not m or p['version'] != m['version'] or p['definition_digest'] != digest(m):
        raise ValueError('Governed measure definition changed; recompile the request')
    if not isinstance(p['period'], dict) or set(p['period']) != {'start', 'end'}:
        raise ValueError('Explicit measure period endpoints required')
    start, end = [date.fromisoformat(p['period'][k]) for k in ('start', 'end')]
    if start > end: raise ValueError('Measure period is reversed')
    if not isinstance(p['group_by'], list) or len(p['group_by']) > 3 or any(g not in GROUPS for g in p['group_by']):
        raise ValueError('Unknown governed grouping')
    if m.get('engine', {}).get('risk_adjustment') and (start.month, start.day) != (1, 1):
        raise ValueError('RAF data collection uses calendar years')
    return p


PERIOD_LAB_READ = re.compile(r"(FROM\s+measurement\s+(?:AS\s+)?)(\w+)(\s+WHERE\s+[^()]*?BETWEEN\s+'\d{4}-\d{2}-\d{2}'\s+AND\s+'\d{4}-\d{2}-\d{2}'[^()]*)", re.I)


def period_lab_reads(sql):
    """A measure that bounds its own lab reads to the measurement period ("an eGFR and a uACR dated in the year") reads EVERY result in that period; the
    decision context's latest-result rule is for thresholds on a patient's current value, so these reads carry the lab-rule alias it leaves alone."""
    from .decision_context import LAB_RULE_READ

    def rename(m):
        alias, where = m.group(2), m.group(3)
        return m.group(1) + LAB_RULE_READ + re.sub(r'\b' + re.escape(alias) + r'\.', LAB_RULE_READ + '.', where)
    return PERIOD_LAB_READ.sub(rename, sql)


def scoped_sql(template, catalog):
    """Restrict each participating patient relation, including both ratio sides.

    Main-qualified reads prevent recursive CTEs. No user/model SQL is accepted.
    Tables without patient membership (e.g. value sets) retain their definition.
    """
    tables = set(re.findall(r'\b(?:FROM|JOIN)\s+([A-Za-z_]\w*)', template, re.I))
    scoped = sorted(t for t in tables if 'patient_id' in catalog.tables.get(t, []))
    if not scoped: raise ValueError('Metric has no registered population binding')
    ctes = [f'"{t}" AS (SELECT * FROM main."{t}" WHERE patient_id IN (SELECT patient_id FROM population))'
            for t in scoped]
    return 'WITH '+','.join(ctes)+(', '+template[4:].lstrip() if template.lstrip().upper().startswith('WITH ') else ' '+template)


def execute(path, patient_ids, p, as_of=None):
    from .request_plan import Catalog
    from . import sql_query
    p = validate(p)
    catalog = Catalog.load(path)
    as_of = as_of or catalog.identity['as_of']
    if p['period']['end'] > as_of: raise ValueError('Measure period exceeds the snapshot cutoff')
    m = definitions()[p['id']]
    groups = defaultdict(list)
    with sqlite3.connect(f'file:{catalog.target}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        keep = set(patient_ids) if patient_ids is not None else None
        died = (dict(db.execute('SELECT patient_id, MIN(death_date) FROM death WHERE death_date <= ? GROUP BY patient_id', (as_of,)))
                if {'patient_id', 'death_date'} <= set(catalog.tables.get('death', [])) else {})
        for row in db.execute('SELECT * FROM patient'):
            if keep is not None and row['patient_id'] not in keep: continue
            values = []
            for name in p['group_by']:
                field = GROUPS[name]
                if field in ('age', 'age_band'):
                    age = age_at(row['birth_date'], died.get(row['patient_id']) or as_of)  # age at death if deceased
                    value = age if field == 'age' or age is None else (
                        '0-17' if age < 18 else '18-44' if age < 45 else '45-64' if age < 65 else '65+')
                else:
                    if field not in row.keys(): raise ValueError('Grouping field unavailable: '+field)
                    value = row[field]
                values.append(value)
            groups[tuple(values)].append(row['patient_id'])
    if not p['group_by']: groups.setdefault((), [])
    rows, evidence, missing, limitations = [], [], [], []
    for keys, ids in sorted(groups.items(), key=lambda kv: str(kv[0])):
        row = dict(zip(p['group_by'], keys))
        if m.get('sql_template'):
            args = metrics.parameters(m, '', as_of, (p['period']['start'], p['period']['end']))
            query = scoped_sql(period_lab_reads(metrics.render_sql(m, args)), catalog)
            report = sql_query.execute(path, {'query': query, 'returns': m['output']['returns']},
                                       patient_ids=ids, target=catalog.target)
            value = report['value']
            evidence.append({'group': row.copy(), 'query': query})
        elif m.get('engine', {}).get('measures'):
            from . import measures
            with sqlite3.connect(f'file:{catalog.target}?mode=ro', uri=True) as db:
                report = measures.evaluate_db(db, measures.library()[m['engine']['measures']],
                                              p['period']['start'], p['period']['end'], as_of, keep=set(ids))
            value = (report.get('overall') or {}).get('rate')
            if m['unit'] == 'percent':
                if value is not None: value *= 100
            else:
                per = measures.library()[m['engine']['measures']].get('numerator', {}).get('per', 100000)
                value = (report.get('overall') or {}).get(f'rate_per_{per}')
            if report.get('declined'): missing.append(report.get('reason', report.get('reading', 'Measure inputs unavailable')))
            limitations.extend(report.get('warnings') or [])
            evidence.append(report)
        else:
            from . import risk_adjustment
            report = risk_adjustment.execute(path, ids, {'year': int(p['period']['start'][:4]), 'focus': 'population'}, as_of)
            value = ((report.get('population') or {}).get('payment_raf') or {}).get('mean')
            if report.get('declined') or value is None:
                missing.append(report.get('reason') or 'RAF cannot be calculated from the qualifying received inputs.')
            if report.get('unknown_age_patient_ids'):
                missing.append(f"{len(report['unknown_age_patient_ids'])} RAF scores lack a valid birth-date age.")
            evidence.append(report)
        row['value'] = value
        rows.append(row)
    answer = {'status': 'partial' if missing else 'answered', 'answer_type': 'table' if p['group_by'] else m['output']['returns'],
              'patient_ids': [], 'actionable_cohort': False,
              'analysis_population_count': len({pid for ids in groups.values() for pid in ids}),
              'missing': missing, 'limitations': limitations,
              'method': m.get('summary', m['name']),
              'computed_as': {'metric_id': m['id'], 'version': m['version'], 'definition_digest': digest(m),
                              'period': p['period'], 'unit': m['unit']},
              'headline': m['name'], 'table': rows if p['group_by'] else None}
    if not p['group_by']: answer['count' if m['output']['returns'] == 'count' else 'value'] = rows[0]['value']
    if p['id'] in ('ed_visits', 'all_source_ed_visits_per_1000'):
        from .request_plan import literal
        query = ("SELECT sources,in_ehr,count(*) AS events FROM utilization_event WHERE kind='ed_visit' "
                 f"AND event_date BETWEEN {literal(p['period']['start'])} AND {literal(p['period']['end'])} "
                 "AND patient_id IN (SELECT patient_id FROM population) GROUP BY sources,in_ehr")
        coverage = sql_query.execute(path, {'query': query, 'returns': 'table'},
                                     patient_ids=sorted({pid for ids in groups.values() for pid in ids}), target=catalog.target)['rows']
        answer['source_coverage'] = {'relation': 'utilization_event', 'deduplicated': True, 'source_combinations': coverage,
                                     'events': sum(r['events'] for r in coverage),
                                     'outside_ehr': sum(r['events'] for r in coverage if not r['in_ehr'])}
        answer['limitations'].append('ED source coverage: '+', '.join(f"{r['sources']}: {r['events']}" for r in coverage)+
                                     '. Events are deduplicated across ADT, EHR and claims.')
    return {'schema': VERSION, 'answer': answer, 'evidence': evidence, 'parameters': p}


# ---------------------------------------------------------------------------------------------------------------------------
# Analyst wording around a governed measure name (2026-10-24, independent dev set). A measure NAME is followed by words that restate
# what the measure already is: its kind ("rate", "score"), its own population ("for patients with diabetes", "among diabetics"), or a
# gloss of its definition ("Kidney health evaluation: share of diabetics 18 to 85 who got both an eGFR and a uACR in 2029"). None of
# these adds a requirement, so they are consumed -- but only when the definition itself accounts for every content word and number.
# Anything the definition does not state (another threshold, another age band, another drug) stays unconsumed and declines the form.
# ---------------------------------------------------------------------------------------------------------------------------
MEASURE_KIND_WORDS = r'(?:rates?|measures?|scores?|performance|percentages?|results?|compliance|numbers?|composite|indicators?|pdc|adherence|proportion\s+of\s+days\s+covered|for\s+hedis|hedis|stars?)'
OWN_POPULATIONS = [  # (word in the measure's name / population, wording that restates that population)
    (r'diabet', r'(?:(?:adult\s+|adults?\s+with\s+)?diabet(?:es|ics?)(?:\s+(?:patients|members|population))?|(?:patients|members|adults|persons|people)\s+(?:with|who\s+have|having)\s+diabetes)'),
    (r'cardiovascular|ascvd', r'(?:(?:patients|members|adults|persons|people)\s+(?:with|who\s+have|having)\s+(?:cardiovascular\s+disease|cvd|ascvd|clinical\s+ascvd)|cardiovascular\s+(?:disease\s+)?patients)'),
    (r'hypertension|blood pressure', r'(?:hypertensi(?:on|ves)|(?:patients|members|adults|persons|people)\s+(?:with|who\s+have|having)\s+hypertension)'),
]
FILLER_WORDS = set('''anyone anybody everyone those who whose got get gets gotten had have has having were was are is be been as counting counts counted count treated
    considered regarded rate share percent percentage proportion fraction what which how many much of in for on at to by with among within during their there it
    its if then also all any each per and or the a an both'''.split())
DIRECTION_GROUPS = [{'above', 'over', 'greater', 'higher', 'exceed', 'exceeds', 'exceeding', '>'}, {'below', 'under', 'less', 'lower', 'fewer', '<'}]


def _stem(word):
    """A crude stem for matching a gloss against a definition: a plural / tense / adverb suffix is dropped, then the first six letters compare
    (poorly ~ poor, controlled ~ control, specialty ~ specialist, diabetics ~ diabetes)."""
    w = word.lower()
    for suffix in ('ically', 'ly', 'ing', 'ed', 'es', 's', 'y'):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            w = w[:-len(suffix)]
            break
    return w[:6]


def own_population_regex(definition):
    """The wording that restates this measure's own population, or None."""
    text = ' '.join(str(definition.get(k, '')) for k in ('name', 'summary')).lower()
    text += ' ' + str((definition.get('denominator') or {}).get('summary', '')).lower()
    for marker, wording in OWN_POPULATIONS:
        if re.search(marker, text): return wording
    return None


def definition_vocabulary(definition):
    """-> (stems of every word the definition states, numbers it states, direction symbols it states)."""
    parts = [definition.get('name', ''), ' '.join(a for a in definition.get('aliases') or [] if isinstance(a, str)), definition.get('summary', ''),
             json.dumps(definition.get('denominator') or {}, default=str), json.dumps(definition.get('numerator') or {}, default=str),
             json.dumps(definition.get('population') or {}, default=str), json.dumps(definition.get('value_sets') or {}, default=str)]
    text = ' '.join(str(p) for p in parts)
    words = {_stem(w) for w in re.findall(r'[A-Za-z]+', text)}
    numbers = set(re.findall(r'\d+(?:\.\d+)?', text))
    symbols = set(re.findall(r'[<>]', text))
    return words, numbers, symbols


def gloss_unaccounted(gloss, definition):
    """The numbers and content words of a restating gloss that the measure's own definition does not state (empty = fully accounted)."""
    words, numbers, symbols = definition_vocabulary(definition)
    left = [n for n in re.findall(r'\d+(?:\.\d+)?', gloss) if float(n) not in {float(x) for x in numbers}]
    for word in re.findall(r'[A-Za-z]+', gloss):
        w = word.lower()
        if w in FILLER_WORDS or _stem(w) in words: continue
        group = next((g for g in DIRECTION_GROUPS if w in g), None)
        if group and (any(_stem(g) in words for g in group if g.isalpha()) or any(g in symbols for g in group if not g.isalpha())): continue
        left.append(word)
    return left


def gloss_accounted(gloss, definition):
    """True when every content word and number of a restating gloss is stated by the measure's own definition."""
    return not gloss_unaccounted(gloss, definition)
