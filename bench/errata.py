"""Benchmark errata: one stated, consistent definition per question, applied only when asked (--errata).

bench/errata.json records, per question id, an audit category and the canonical reading the scorer should hold an
answer to. The original question files and reference SQL are never edited, so default scoring (errata off) is unchanged
and old numbers stay reproducible.

Categories
  a  unambiguous; the reference is correct (canonical = reference)
  b  the question text under-specifies a definition; the canonical reading follows the stated conventions and the other
     plausible readings are accepted alternatives, each with its own expected answer
  c  the reference contradicts the question text or another split's convention; canonical = the convention reading, and
     the reference's own reading is kept as an accepted alternative
  d  reference bug; canonical = corrected answer (SQL under bench/sql_errata/)

Errata scoring: an answer is correct when it matches the canonical answer under the strict scorer, or when it matches an
accepted alternative AND the answer states that alternative's definition (every phrase group in `stated_any_of` must
appear in the answer's interpretation / definition / computed_as / readings text). Expected values are stored per
warehouse release; `source: "reference"` means "the reference answer of the run being scored", valid for any release.
"""
from __future__ import annotations

import json
from pathlib import Path

if __package__ in (None, ''):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.common import ROOT, empty_answer, reference_answer

ERRATA_PATH = ROOT / 'errata.json'
SCHEMA = 'bench-errata/1'
CATEGORIES = {'a', 'b', 'c', 'd'}
KIND_FIELDS = {'count': ('count',), 'value': ('value',), 'patient_set': ('patient_ids',), 'table': ('table',),
               'unsupported': ()}
DEFINITION_FIELDS = ('interpretation', 'definition', 'computed_as', 'method', 'headline', 'standards', 'readings',
                     'clarifying_question', 'concepts')


def validate_errata(data, questions=None):
    """Raise ValueError on any schema problem. `questions` (id -> question) enables id/type checks."""
    problems = []
    if not isinstance(data, dict) or data.get('schema') != SCHEMA:
        raise ValueError(f'errata schema must be {SCHEMA}')
    entries = data.get('questions')
    if not isinstance(entries, dict):
        raise ValueError('errata.questions must be an object keyed by question id')
    for qid, entry in entries.items():
        where = f'errata[{qid}]'
        if questions is not None and qid not in questions:
            problems.append(f'{where}: unknown question id')
            continue
        kind = (questions or {}).get(qid, {}).get('answer_type')
        if entry.get('category') not in CATEGORIES:
            problems.append(f'{where}: category must be one of a/b/c/d')
        canon = entry.get('canonical')
        if not isinstance(canon, dict) or not canon.get('reading'):
            problems.append(f'{where}: canonical.reading required')
            continue
        prec = entry.get('precision')
        if prec is not None and (type(prec) is not int or not 0 <= prec <= 6 or (kind and kind not in ('value', 'table'))):
            problems.append(f'{where}: precision must be an integer 0..6 on a value/table question')
        if not entry.get('evidence'):
            problems.append(f'{where}: evidence required')
        if source_of(canon, True) not in ('reference', 'errata'):
            problems.append(f'{where}: canonical.source must be reference or errata')
        alts = entry.get('alternatives', [])
        if not isinstance(alts, list):
            problems.append(f'{where}: alternatives must be a list')
            alts = []
        cat = entry.get('category')
        if cat == 'a' and (source_of(canon, True) != 'reference' or alts):
            problems.append(f'{where}: category a keeps the reference and has no alternatives')
        if cat == 'b' and not alts:
            problems.append(f'{where}: category b needs at least one accepted alternative')
        if cat in ('c', 'd') and source_of(canon, True) != 'errata':
            problems.append(f'{where}: category {cat} needs an errata canonical answer')
        if cat == 'd' and not canon.get('sql'):
            problems.append(f'{where}: a reference bug needs corrected SQL under sql_errata/')
        parts = [('canonical', canon)] + [(f'alternatives[{i}]', a) for i, a in enumerate(alts)]
        seen = set()
        for name, part in parts:
            if name != 'canonical':
                if not part.get('id') or part['id'] in seen:
                    problems.append(f'{where}.{name}: unique id required')
                seen.add(part.get('id'))
                groups = part.get('stated_any_of')
                if not (isinstance(groups, list) and groups and
                        all(isinstance(g, list) and g and all(isinstance(w, str) and w for w in g) for g in groups)):
                    problems.append(f'{where}.{name}: stated_any_of must be a non-empty list of phrase lists')
                if not part.get('reading'):
                    problems.append(f'{where}.{name}: reading required')
            if source_of(part, name == 'canonical') == 'errata':
                exp = part.get('expected')
                if not isinstance(exp, dict) or not exp:
                    problems.append(f'{where}.{name}: expected answers by release required')
                elif kind:
                    for rel, value in exp.items():
                        if not isinstance(value, dict) or set(value) != set(KIND_FIELDS[kind]):
                            problems.append(f'{where}.{name}: expected[{rel[:8]}] must carry exactly {KIND_FIELDS[kind]}')
            sql = part.get('sql')
            if sql:
                path = (ROOT / sql).resolve()
                if not path.is_relative_to(ROOT / 'sql_errata') or not path.is_file():
                    problems.append(f'{where}.{name}: sql must be an existing file under bench/sql_errata/')
    if problems:
        raise ValueError('Invalid errata:\n' + '\n'.join(problems))
    return data


def load_errata(path=ERRATA_PATH, questions=None):
    data = json.loads(Path(path).read_text())
    return validate_errata(data, questions)


def definition_text(answer):
    """All definition-bearing text of an answer, casefolded."""
    if not isinstance(answer, dict):
        return ''
    chunks = []
    for key in DEFINITION_FIELDS:
        value = answer.get(key)
        if value in (None, '', [], {}):
            continue
        chunks.append(value if isinstance(value, str) else json.dumps(value, default=str))
    return ' '.join(chunks).casefold()


def states_definition(answer, groups):
    text = definition_text(answer)
    return bool(text) and all(any(phrase.casefold() in text for phrase in group) for group in groups)


def answer_from(kind, compact):
    """Rebuild a full reference-shaped answer from a compact stored expected value."""
    answer = empty_answer(kind)
    if kind == 'patient_set':
        ids = sorted(compact['patient_ids'])
        answer.update(patient_ids=ids, count=len(ids))
    elif kind in ('count', 'value', 'table'):
        answer[kind] = compact[kind]
    return answer


def compact(kind, answer):
    return {field: answer[field] for field in KIND_FIELDS[kind]}


def source_of(part, canonical):
    """'reference' = the run's own reference answer; 'errata' = stored/computed errata answer."""
    return part.get('source') or ('reference' if canonical else 'errata')


def part_expected(question, part, release, reference_expected, computed=None, canonical=False):
    """Expected answer for one reading, or None when it is unknown for this release."""
    if source_of(part, canonical) == 'reference':
        return reference_expected
    if computed is not None:
        return computed
    stored = (part.get('expected') or {}).get(release)
    return None if stored is None else answer_from(question['answer_type'], stored)


def compute_expected(question, part, warehouse):
    """Execute a reading's errata SQL like a reference (run.py --errata)."""
    if not part.get('sql'):
        return None
    answer, _ = reference_answer({**question, 'reference_sql': part['sql']}, warehouse)
    return answer


def with_precision(question, entry):
    """Convention C9: when the question states no precision, a value within half a unit of the reference's last
    reported decimal matches (24.44 or 24.4 for 24.4). Never loosens a stated tolerance."""
    digits = entry.get('precision')
    if digits is None or question.get('answer_type') not in ('value', 'table'):
        return question
    half = 0.5 * 10 ** -digits + 1e-9
    return {**question, 'tolerance': max(question.get('tolerance', 0) or 0, half)}


def score_with_errata(question, expected, actual, entry, release=None, computed=None):
    """Errata scoring. `computed` = {'canonical': answer, '<alt id>': answer} computed at run time (optional)."""
    from bench.score import score_answer
    computed = computed or {}
    if entry is None:
        return {**score_answer(question, expected, actual), 'errata': 'none'}
    question = with_precision(question, entry)
    canon = entry['canonical']
    canon_expected = part_expected(question, canon, release, expected, computed.get('canonical'), canonical=True)
    if canon_expected is None:
        return {**score_answer(question, expected, actual), 'errata': 'unavailable_for_release'}
    result = {**score_answer(question, canon_expected, actual), 'errata': 'canonical'}
    if result['correct']:
        return result
    for alt in entry.get('alternatives', []):
        alt_expected = part_expected(question, alt, release, expected, computed.get(alt['id']))
        if alt_expected is None:
            continue
        alt_score = score_answer(question, alt_expected, actual)
        if not alt_score['correct']:
            continue
        if states_definition(actual, alt['stated_any_of']):
            return {**alt_score, 'reason': 'correct_alternative', 'errata': 'alternative:' + alt['id']}
        # A defensible reading delivered without saying so: a disclosure failure (FM-19), not a wrong number.
        result = {**result, 'reason': 'alternative_unstated', 'false_assertion': False,
                  'errata': 'alternative_unstated:' + alt['id']}
    return result
