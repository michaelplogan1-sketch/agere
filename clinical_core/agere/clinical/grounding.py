"""Ground cohort predicates in the received data before evaluating them.

Every record/patient predicate is checked against the source profile:
  - stream_unavailable / field_unavailable: the data never populates it;
  - value_not_observed: a literal code or label that occurs nowhere;
  - partial_code_coverage: a code field that only some records carry
    (e.g. ICD-10 on 3% of conditions, the rest SNOMED/labels).
Where the predicate names a known clinical concept (an ICD-10 prefix, a LOINC
code, a lab label) it is rewritten to a concept or measurement atom that covers
every coding system present. What cannot be repaired is returned as a
grounding failure, never silently evaluated into patient-level "unknown".
"""
import copy
from . import concepts, source_profile

CODE_FIELDS = {('conditions', 'icd10'), ('conditions', 'snomed'), ('condition_assessments', 'icd10'),
               ('problem_lists', 'problem_code'), ('medical_claims', 'dx')}
MEASUREMENT_LABEL_FIELDS = {('labs', 'code'), ('labs', 'test'), ('labs', 'name'), ('vitals', 'measurement')}
CONDITION_LABEL_FIELDS = {(s, f) for s, spec in concepts.CONDITION_STREAMS.items() for f in spec['labels']}
NUMERIC_OPS = ('lt', 'le', 'gt', 'ge', 'eq', 'ne')
REPORTED_PATIENT_FIELDS = {'age': 'age_reported', 'sex': 'sex_reported'}
PARTIAL_COVERAGE = 0.9


class GroundingError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__('; '.join(f"{i['issue']}: {i.get('stream', 'patients')}.{i.get('field', '')}" for i in issues))


def _field(profile, stream, name):
    return source_profile.field(profile, stream, name)


def _observed(f):
    return (f or {}).get('values') or (f or {}).get('element_values')


def check_record(atom, profile):
    issues = []
    stream = atom['stream']
    if stream not in profile['streams']:
        return [{'issue': 'stream_unavailable', 'stream': stream, 'severity': 'error',
                 'detail': f'No received records in stream {stream}.'}]
    total = profile['streams'][stream]['records']
    for c in atom['conditions']:
        f = _field(profile, stream, c['field'])
        if not f or not f['populated']:
            issues.append({'issue': 'field_unavailable', 'stream': stream, 'field': c['field'], 'severity': 'error',
                           'detail': f'{stream}.{c["field"]} is never populated in the received data.'})
            continue
        if f['populated'] < PARTIAL_COVERAGE * total and (stream, c['field']) in CODE_FIELDS:
            issues.append({'issue': 'partial_code_coverage', 'stream': stream, 'field': c['field'], 'severity': 'warning',
                           'detail': f'Only {f["populated"]} of {total} {stream} records carry {c["field"]}; '
                                     'the rest use other coding systems or labels.'})
        values = _observed(f)
        if values is not None and isinstance(c['value'], str) and c['op'] in ('eq', 'prefix', 'contains'):
            hit = (c['value'] in values if c['op'] in ('eq', 'contains')
                   else any(str(v).startswith(c['value']) for v in values))
            if not hit:
                issues.append({'issue': 'value_not_observed', 'stream': stream, 'field': c['field'], 'severity': 'error',
                               'value': c['value'], 'detail': f'No {stream}.{c["field"]} value matches {c["op"]} {c["value"]!r}.'})
    return issues


def check_patient(atom, profile):
    # age_latest is derived by evaluate_atom from the roster and demographic
    # updates. It is not a literal field in the received patients stream.
    if atom['field'] == 'age_latest':
        dependencies = [('patients', 'age_reported'), ('patients', 'birth_date'),
                        ('demographic_updates', 'age_reported')]
        if any(source_profile.populated(profile, stream, field) for stream, field in dependencies):
            return []
        return [{'issue': 'field_unavailable', 'stream': 'patients', 'field': 'age_latest',
                 'severity': 'error', 'sources_checked': dependencies,
                 'detail': 'No reported age, birth date or demographic age update is available to derive age_latest.'}]
    name = 'birth_date' if atom['field'] == 'age' else atom['field']
    if not source_profile.populated(profile, 'patients', name):
        return [{'issue': 'field_unavailable', 'stream': 'patients', 'field': name, 'severity': 'error',
                 'detail': f'Patient {name} is never populated.'}]
    return []


def _repair_record(atom, profile):
    """Rewrite a raw code/label predicate into a concept/measurement atom, or None."""
    stream, window = atom['stream'], atom['window']
    key_conditions = [c for c in atom['conditions'] if (stream, c['field']) in CODE_FIELDS | MEASUREMENT_LABEL_FIELDS | CONDITION_LABEL_FIELDS
                      and c['op'] in ('eq', 'prefix', 'contains') and isinstance(c['value'], str)]
    if len(key_conditions) != 1: return None
    key = key_conditions[0]
    others = [c for c in atom['conditions'] if c is not key]
    if (stream, key['field']) in MEASUREMENT_LABEL_FIELDS:
        term = None
        if key['field'] == 'code':
            concept = concepts.concept_for_code(key['value'])
        else:
            concept = concepts.concept_for_label(key['value'])
            if concept is None and key['op'] == 'eq':  # exact code and alias found nothing: second-encoder fallback
                term = concepts.match_label(key['value'], 'lab')
                concept = (term or {}).get('concept')
        if concept not in concepts.MEASUREMENTS: return None
        unit = next((c['value'] for c in others if c['field'] == 'unit' and c['op'] == 'eq'), None)
        values = [c for c in others if c['field'] == 'value' and c['op'] in NUMERIC_OPS and type(c['value']) in (int, float)]
        if len(others) != len(values) + (unit is not None) or len(values) > 1: return None
        d = concepts.MEASUREMENTS[concept]
        if unit is not None and unit not in d['units']: return None
        extra = {'_terminology': _term(term)} if term else {}
        if not values: return {'kind': 'concept', 'concept': concept, 'window': window, **extra}
        factor = d['units'][unit] if unit is not None else 1.0
        return {'kind': 'measurement', 'concept': concept, 'op': values[0]['op'],
                'value': values[0]['value'] * factor, 'aggregate': 'any', 'window': window, **extra}
    if (stream, key['field']) in CONDITION_LABEL_FIELDS:
        # a condition label: the library's label pattern (alias), then the second-encoder fallback
        if others or key['op'] != 'eq': return None
        term = concepts.match_label(key['value'], 'condition')
        concept = (term or {}).get('concept')
        if concept not in concepts.CONDITIONS: return None
        return {'kind': 'concept', 'concept': concept, 'window': window,
                **({'_terminology': _term(term)} if term['method'] == 'encoder' else {})}
    concept = concepts.concept_for_code(key['value'])
    if concept is None or others: return None
    return {'kind': 'concept', 'concept': concept, 'window': window}


def _term(m):
    return {k: m.get(k) for k in ('method', 'candidate', 'score', 'threshold', 'runner_up', 'version', 'encoder')}


def prepare(spec, profile):
    """Return (grounded_spec, report). Raises GroundingError for unrepairable predicates."""
    grounded = {k: [] for k in spec}
    report, errors = [], []
    for group, atoms in spec.items():
        for atom in atoms:
            atom = copy.deepcopy(atom)
            if atom['kind'] == 'patient':
                issues = check_patient(atom, profile)
                reported = REPORTED_PATIENT_FIELDS.get(atom['field'])
                if issues and reported and source_profile.populated(profile, 'patients', reported):
                    report.append({**issues[0], 'severity': 'repaired', 'repair': f"{atom['field']} is not received; used {reported}"})
                    atom = {**atom, 'field': reported}
                    issues = []
                errors += issues
                grounded[group].append(atom)
            elif atom['kind'] == 'record':
                issues = check_record(atom, profile)
                if issues:
                    repaired = _repair_record(atom, profile)
                    if repaired:
                        term = repaired.pop('_terminology', None)
                        bound = concepts.resolve(repaired['concept'], profile)
                        if bound['bindings']:
                            how = (f" (label matched by the terminology encoder, cosine {term['score']} >= {term['threshold']})"
                                   if term else '')
                            report.extend({**i, 'severity': 'repaired',
                                           'repair': f"rewrote to {repaired['kind']} '{repaired['concept']}' covering all coding systems present{how}",
                                           **({'terminology': term} if term else {})}
                                          for i in issues)
                            grounded[group].append({**repaired, '_resolved': bound})
                            continue
                    errors += [i for i in issues if i['severity'] == 'error']
                    report += [i for i in issues if i['severity'] != 'error']
                grounded[group].append(atom)
            elif atom['kind'] in ('concept', 'measurement'):
                bound = concepts.resolve(atom['concept'], profile, atom.get('evidence') if atom['kind'] == 'concept' else None)
                if atom.get('evidence') and bound['bindings'] and not bound['evidence']['available']:
                    bound = {**bound, 'bindings': []}
                if atom.get('evidence') and not bound['bindings']:
                    errors.append({'issue': 'evidence_not_observed', 'concept': atom['concept'], 'severity': 'error',
                                   'detail': f"No received {atom['concept']} evidence of the requested kind "
                                             f"({', '.join(atom['evidence'])}); the sources that carry it are not received."})
                elif not bound['bindings']:
                    errors.append({'issue': 'concept_not_observed', 'concept': atom['concept'], 'severity': 'error',
                                   'detail': f"No received records match concept {atom['concept']}."})
                if atom.get('names'):
                    observed = {n.lower() for b in bound['bindings'] for n in b.get('names', [])}
                    missing = [n for n in atom['names'] if n.lower() not in observed]
                    if missing:
                        errors.append({'issue': 'value_not_observed', 'concept': atom['concept'], 'severity': 'error',
                                       'detail': f"No {atom['concept']} results are labelled {missing}."})
                if atom['kind'] == 'measurement' and atom['concept'] not in concepts.MEASUREMENTS:
                    errors.append({'issue': 'not_a_measurement', 'concept': atom['concept'], 'severity': 'error',
                                   'detail': 'Numeric comparisons need a measurement concept.'})
                grounded[group].append({**atom, '_resolved': bound})
            else:
                grounded[group].append(atom)
    if errors: raise GroundingError(errors + report)
    return grounded, report


def interpretation(grounded):
    """Human-readable record of what each concept atom included."""
    out, seen = [], set()
    for atom in sum(grounded.values(), []):
        r = atom.get('_resolved')
        if not r: continue
        signature = (r['concept'], atom['kind'], atom.get('op'), atom.get('value'), atom.get('aggregate'), tuple(atom.get('evidence') or ()))
        if signature in seen: continue
        seen.add(signature)
        item = {'concept': r['concept'], 'label': r['label']}
        if r['kind'] in ('condition', 'encounter'):
            item['included_labels'] = sorted(r['included_labels'])
            item['excluded_labels'] = sorted(r['excluded_labels'])
            item['codes'] = sorted({v for b in r['bindings'] if b['system'] == 'ICD-10-CM' for v in b.get('values', [])})
            if r.get('evidence'):
                item['evidence'] = r['evidence']
        else:
            item['unit'] = r['unit']
            item['included_series'] = sorted(r['included_series'])
            item['unconvertible_series'] = sorted(r['unconvertible_series'])
            if atom['kind'] == 'measurement':
                item['comparison'] = f"{atom.get('aggregate', 'any')} result {atom['op']} {atom['value']} {r['unit']}"
        out.append(item)
    return out
