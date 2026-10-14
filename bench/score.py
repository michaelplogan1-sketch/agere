"""Strict answer scoring and a compact, reproducible scorecard."""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bench.common import ROOT, finite_number, load_questions


def same_value(a, b, tolerance=0):
    if finite_number(a) and finite_number(b):
        return abs(a - b) <= tolerance
    return type(a) is type(b) and a == b


CONFIDENCE_LEVELS = ('verified', 'unverified', 'low')
_LEGACY_LEVELS = {'verified': 'verified', 'governed': 'verified', 'low': 'low'}


def confidence_of(answer):
    """'verified' | 'unverified' | 'low' | None. Reads the answer contract's confidence field, and the earlier dict form
    ({'level': 'governed'|'verified'|'low'}) of preserved answers."""
    if not isinstance(answer, dict):
        return None
    c = answer.get('confidence')
    if isinstance(c, dict):
        return _LEGACY_LEVELS.get(c.get('level'))
    return c if c in CONFIDENCE_LEVELS else None


def valid_answer(answer):
    if not isinstance(answer, dict):
        return False
    # confidence is optional; when present it is 'verified' | 'unverified' | 'low' (or the earlier {'level': ...} form). It never
    # changes how correctness is scored.
    c = answer.get('confidence')
    if c is not None and not (c in CONFIDENCE_LEVELS or (isinstance(c, dict) and c.get('level') in _LEGACY_LEVELS)):
        return False
    if answer.get('status') not in {'answered', 'partial', 'unsupported', 'grounding_failed'}:
        return False
    if answer.get('answer_type') not in {'count', 'patient_set', 'value', 'table', 'unsupported'}:
        return False
    unknown = answer.get('unknown_count')
    if type(unknown) is not int or unknown < 0:
        return False
    missing = answer.get('missing')
    if not isinstance(missing, list) or any(not isinstance(x, str) for x in missing):
        return False
    if not isinstance(answer.get('grounding'), list):
        return False
    kind = answer['answer_type']
    if kind == 'count':
        return type(answer.get('count')) is int and answer['count'] >= 0
    if kind == 'value':
        return finite_number(answer.get('value'))
    if kind == 'patient_set':
        ids = answer.get('patient_ids')
        return (isinstance(ids, list) and all(isinstance(x, str) and x for x in ids) and
                len(ids) == len(set(ids)) and type(answer.get('count')) is int and answer['count'] == len(ids))
    if kind == 'table':
        return isinstance(answer.get('table'), list) and all(isinstance(r, dict) for r in answer['table'])
    return (answer.get('count') is None and answer.get('value') is None and
            answer.get('table') in (None, []) and answer.get('patient_ids') in (None, []))


def exact_table(actual, expected, tolerance):
    if len(actual) != len(expected):
        return False
    # Multiset comparison: duplicate rows remain meaningful, order does not.
    remaining = list(expected)
    for row in actual:
        match = next((i for i, e in enumerate(remaining) if set(row) == set(e) and
                      all(same_value(row[k], e[k], tolerance) for k in e)), None)
        if match is None:
            return False
        remaining.pop(match)
    return True


def score_answer(question, expected, actual):
    result = {'correct': False, 'value_match': False, 'correct_abstention': False,
              'false_assertion': False, 'covered': False, 'reason': 'missing_answer'}
    if actual is None:
        return result
    if not valid_answer(actual):
        return {**result, 'reason': 'invalid_answer_contract'}
    answered = actual['status'] == 'answered'
    result['covered'] = answered and actual['answer_type'] != 'unsupported' and not actual['missing']
    if question['answer_type'] == 'unsupported':
        text = ' '.join(actual['missing']).casefold()
        rubric = question['expected_behavior']['required_missing']
        missing = [r['id'] for r in rubric if not any(word.casefold() in text for word in r['any_of'])]
        correct = (actual['status'] == 'unsupported' and actual['answer_type'] == 'unsupported' and not missing)
        return {**result, 'correct': correct, 'correct_abstention': correct,
                'false_assertion': result['covered'],
                'reason': 'correct_abstention' if correct else
                          ('missing_prerequisites:' + ','.join(missing) if missing else 'unsupported_contract_mismatch')}
    kind = question['answer_type']
    match = False
    type_match = actual['answer_type'] == kind
    comparable = type_match or {kind, actual['answer_type']} == {'count', 'value'}
    if type_match:
        if kind == 'patient_set':
            match = set(actual['patient_ids']) == set(expected['patient_ids'])
        elif kind == 'table':
            match = exact_table(actual['table'], expected['table'], question.get('tolerance', 0))
        elif kind == 'count':
            match = actual['count'] == expected['count']
        else:
            match = same_value(actual['value'], expected['value'], question.get('tolerance', 0))
    elif comparable:
        # Preserve a correct scalar as diagnostic evidence, while still failing
        # the requested type contract. A count returned as value is not a false
        # numerical assertion merely because the answer serializer chose wrong.
        match = same_value(actual[actual['answer_type']], expected[kind], question.get('tolerance', 0))
    # unknown_count can describe records explicitly excluded by the question
    # (e.g. undated results). It is not necessarily unresolved answer membership.
    # Incomplete answers must use partial/grounding_failed or declare missing.
    correct = type_match and match and answered and not actual['missing']
    result.update(correct=correct, value_match=match,
                  false_assertion=answered and comparable and not match,
                  reason='answer_type_mismatch' if not type_match else
                         ('correct' if correct else ('incomplete_answer' if match else 'answer_mismatch')))
    return result


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * p
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def recorded_cost(row):
    """Full measured cost, or a known lower bound when some usage is missing."""
    if finite_number(row.get('estimated_cost_usd')):
        return row['estimated_cost_usd']
    if finite_number(row.get('known_cost_usd')):
        return row['known_cost_usd']
    if row.get('system') == 'agere':
        planning = (row.get('response') or {}).get('planning_economics') or {}
        # Only newly incurred planner spend, never historical cached-call cost.
        if finite_number(planning.get('new_api_estimate_usd')):
            return planning['new_api_estimate_usd']
    return None


def by_confidence(rows):
    """Precision of asserted answers split by the answer's confidence flag (verified | unverified | low | unlabelled).

    asserted = a scored, non-abstaining answer with a comparable value; precision = value matches / asserted (a correct value
    marked partial by an older contract still counts as a correct value here); correct uses the unchanged strict rule."""
    out = {}
    for label in CONFIDENCE_LEVELS + ('unlabelled',):
        group = [r for r in rows if r.get('score') is not None and (confidence_of(r.get('answer')) or 'unlabelled') == label
                 and isinstance(r.get('answer'), dict) and r['answer'].get('answer_type') != 'unsupported']
        matches = sum(r['score']['value_match'] for r in group)
        out[label] = {'asserted': len(group), 'value_matches': matches, 'correct': sum(r['score']['correct'] for r in group),
                      'precision': matches / len(group) if group else None}
    return out


def summarize(rows, total_questions):
    rows = list(rows)
    scored = [r for r in rows if r.get('score') is not None]
    correct = sum(r['score']['correct'] for r in scored)
    costs = [r.get('estimated_cost_usd') for r in rows]
    known = [recorded_cost(r) for r in rows if recorded_cost(r) is not None]
    complete = all(finite_number(c) for c in costs) and bool(rows)
    # A fast connection refusal must not make answer delivery appear faster.
    latency = [r['latency_s'] for r in rows if finite_number(r.get('latency_s')) and not r.get('error')]
    return {'attempted': len(rows), 'total_questions': total_questions, 'correct': correct,
            'value_matches': sum(r['score']['value_match'] for r in scored),
            'correct_abstentions': sum(r['score']['correct_abstention'] for r in scored),
            'false_assertions': sum(r['score']['false_assertion'] for r in scored),
            'coverage': sum(r['score']['covered'] for r in scored),
            'reference_errors': sum(r.get('reference_error') is not None for r in rows),
            'errors': sum(r.get('error') is not None for r in rows),
            'latency_p50_s': statistics.median(latency) if latency else None,
            'latency_p95_s': percentile(latency, .95),
            'known_cost_usd': sum(known), 'cost_complete': complete,
            'cost_per_correct_usd': sum(known) / correct if complete and correct else None,
            'token_rows_known': sum(type(r.get('input_tokens')) is int and type(r.get('output_tokens')) is int for r in rows),
            'by_confidence': by_confidence(scored)}


def read_latest(results):
    """Latest attempt per question, never best score. Partial runs stay visible."""
    latest = {}
    for path in sorted(Path(results).glob('*/*.jsonl')):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f'Invalid result row {path}:{number}') from exc
            key = row['system'], row['id']
            if key not in latest or row['started_at'] > latest[key]['started_at']:
                latest[key] = row
    return latest


def read_attempts(results):
    """Every attempt per (system, id), oldest first (for pass^k)."""
    attempts = {}
    for path in sorted(Path(results).glob('*/*.jsonl')):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f'Invalid result row {path}:{number}') from exc
            attempts.setdefault((row['system'], row['id']), []).append(row)
    for rows in attempts.values():
        rows.sort(key=lambda r: r['started_at'])
    return attempts


def pass_k(attempt_lists, k=3):
    """pass^k: a question passes only if its latest k scored attempts are ALL correct.

    Questions with fewer than k scored attempts are reported as not measured, never as passes or failures.
    """
    measured = passed = 0
    for rows in attempt_lists:
        scored = [r for r in rows if r.get('score') is not None and not r.get('reference_error')]
        if len(scored) < k:
            continue
        measured += 1
        passed += all(r['score']['correct'] for r in scored[-k:])
    return {'k': k, 'questions_measured': measured, 'pass_k': passed,
            'pass_k_rate': passed / measured if measured else None}


def rescore(row, question, errata=None):
    """Current explicit scorer on a preserved answer; errata scoring only when an errata file is given."""
    if errata is None:
        return score_answer(question, row['expected'], row.get('answer'))
    from bench.errata import score_with_errata
    return score_with_errata(question, row['expected'], row.get('answer'), errata['questions'].get(question['id']),
                             row.get('release'), row.get('errata_expected'))


def write_scorecard(results=ROOT / 'results', destination=ROOT / 'SCORECARD.md', errata=None, k=3):
    questions = {q['id']: q for q in load_questions()}
    latest = read_latest(results)
    # Keep raw result rows intact while applying the current explicit scorer.
    for (_, ident), row in latest.items():
        if ident in questions and row.get('expected') is not None and not row.get('reference_error'):
            row['score'] = rescore(row, questions[ident], errata)
    attempts = read_attempts(results)
    for (_, ident), rows in attempts.items():
        for row in rows:
            if ident in questions and row.get('expected') is not None and not row.get('reference_error'):
                row['score'] = rescore(row, questions[ident], errata)
    systems = sorted({s for s, _ in latest})
    lines = ['# Independent answer benchmark' + (' (errata scoring)' if errata else ''), '',
             'Latest attempt per question, rescored from preserved answers with the current scorer; unrun questions are not passes. Reference results are SQL self-checks, not product accuracy.', '']
    if errata:
        lines += ['Errata scoring (bench/errata.json): an answer is correct if it matches the canonical reading, or an accepted '
                  'alternative reading whose definition the answer states. Default scoring (no --errata) is unchanged.', '']
    lines += [
             '| System | Attempted / suite | Correct | Value matches | Coverage | Correct abstentions | False assertions | Execution errors | Response p50 / p95 seconds | Known API USD | Cost complete | USD / correct |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|']
    fmt = lambda v: 'unknown' if v is None else f'{v:.4f}'
    summaries = {}
    for system in systems:
        rows = [r for (s, i), r in latest.items() if s == system and i in questions]
        summary = summarize(rows, len(questions))
        summary['pass_k'] = pass_k([r for (s, i), r in attempts.items() if s == system and i in questions], k)
        summaries[system] = summary
        lines.append(f'| {system} | {summary["attempted"]} / {len(questions)} | {summary["correct"]} | {summary["value_matches"]} | {summary["coverage"]} | '
                     f'{summary["correct_abstentions"]} | {summary["false_assertions"]} | {summary["errors"]} | '
                     f'{fmt(summary["latency_p50_s"])} / {fmt(summary["latency_p95_s"])} | '
                     f'{fmt(summary["known_cost_usd"])} | {summary["cost_complete"]} | {fmt(summary["cost_per_correct_usd"])} |')
    compared = [s for s in systems if s != 'reference']
    shared = set.intersection(*(set(i for s, i in latest if s == system) for system in compared)) if compared else set()
    lines += ['', f'Common attempted questions across non-reference systems: {len(shared)}.',
              'Coverage counts answered structured attempts without declared missing requirements, including wrong answers. An unknown_count can describe explicitly excluded records and does not by itself invalidate an exact scoped answer. '
              'Unsupported scoring checks explicit prerequisite phrases; it is a mechanical rubric, not independent clinical adjudication.']
    lines += ['Value matches include matching scalar values returned in the wrong count/value field or marked partial; they are not complete contract passes. Correct abstentions are counted separately. False assertions require a comparable structured value that disagrees with the requested scope; the scorer does not adjudicate every prose claim. A disclosed alternative interpretation can still fail exact-answer scoring.']
    if shared:
        lines += ['', '## Common attempted questions', '',
                  '| System | Correct / common | Coverage | Known API USD | USD / correct |',
                  '|---|---:|---:|---:|---:|']
        for system in compared:
            s = summarize([latest[(system, i)] for i in shared], len(shared))
            lines.append(f'| {system} | {s["correct"]} / {len(shared)} | {s["coverage"]} | '
                         f'{fmt(s["known_cost_usd"])} | {fmt(s["cost_per_correct_usd"])} |')
    lines += ['', '## Precision by confidence', '',
              'Answers carry confidence verified (every gate on the answer path has a Learn-then-Test certificate and passed; '
              'clinical_core/agere/clinical/gate_policy.py), unverified (no failed check, but an uncertified gate) or low (a check '
              'failed, e.g. free-form SQL without independent agreement). Precision = value matches / asserted answers in each group; '
              'correctness scoring is unchanged.', '',
              '| System | Verified: matches / asserted | Verified precision | Unverified: matches / asserted | Unverified precision | '
              'Low: matches / asserted | Low precision | Unlabelled: matches / asserted |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    for system in systems:
        bc = summaries[system]['by_confidence']
        lines.append(f'| {system} | {bc["verified"]["value_matches"]} / {bc["verified"]["asserted"]} | {fmt(bc["verified"]["precision"])} | '
                     f'{bc["unverified"]["value_matches"]} / {bc["unverified"]["asserted"]} | {fmt(bc["unverified"]["precision"])} | '
                     f'{bc["low"]["value_matches"]} / {bc["low"]["asserted"]} | {fmt(bc["low"]["precision"])} | '
                     f'{bc["unlabelled"]["value_matches"]} / {bc["unlabelled"]["asserted"]} |')
    lines += ['', f'## Reliability (pass^{k})', '',
              f'A question passes^{k} only if its latest {k} scored attempts are all correct (`run.py --repeat {k}`). '
              f'Questions with fewer than {k} attempts are not measured.', '',
              f'| System | Questions with {k}+ attempts | pass^{k} | Rate |', '|---|---:|---:|---:|']
    for system in systems:
        pk = summaries[system]['pass_k']
        lines.append(f'| {system} | {pk["questions_measured"]} | {pk["pass_k"]} | {fmt(pk["pass_k_rate"])} |')
    lines += ['', '## Recorded spend across all attempts', '',
              'These totals include prior failed/replaced attempts; the tables above use only the latest attempt per question.', '',
              '| System | Attempts | Known API USD | Attempts with unknown cost |', '|---|---:|---:|---:|']
    for system in systems:
        attempts = [json.loads(line) for path in sorted((Path(results) / system).glob('*.jsonl'))
                    for line in path.read_text().splitlines() if line.strip()]
        known = sum(recorded_cost(r) for r in attempts if recorded_cost(r) is not None)
        unknown = sum(not finite_number(r.get('estimated_cost_usd')) for r in attempts)
        lines.append(f'| {system} | {len(attempts)} | {known:.6f} | {unknown} |')
    lines += ['', '## Per answer type', '', '| Type | ' + ' | '.join(systems) + ' |',
              '|---|' + '---|' * len(systems)]
    for kind in ('count', 'patient_set', 'value', 'table', 'unsupported'):
        cells = []
        total = sum(q['answer_type'] == kind for q in questions.values())
        for system in systems:
            rows = [r for (s, i), r in latest.items() if s == system and i in questions and questions[i]['answer_type'] == kind]
            s = summarize(rows, total)
            cells.append(f'{s["correct"]} correct / {s["attempted"]} attempted / {total} cases')
        lines.append('| ' + kind + ' | ' + ' | '.join(cells) + ' |')
    lines += ['', '## Per question', '', '| ID | Split | Type | ' + ' | '.join(systems) + ' |',
              '|---|---|---|' + '---|' * len(systems)]
    for ident, question in questions.items():
        values = []
        for system in systems:
            row = latest.get((system, ident))
            if row is None: values.append('unrun')
            elif row.get('reference_error'): values.append('reference error')
            elif row.get('error'): values.append('error')
            else: values.append('pass' if row['score']['correct'] else row['score']['reason'])
        lines.append(f'| {ident} | {question["split"]} | {question["answer_type"]} | ' + ' | '.join(values) + ' |')
    identities = sorted({(r.get('release'), r.get('suite_sha256'), r.get('model')) for r in latest.values()}, key=str)
    lines += ['', '## Measurement limits', '',
              '- Development and challenge cases were authored from received data; challenges are visible and are not an independently sealed clinical test set.',
              '- Counts, units, date rules, tables and patient sets are checked against separate raw-source SQL. SQL semantics still require independent review.',
              '- Latency percentiles use completed responses without execution errors, including incorrect answers; fast connection refusals are excluded. Raw attempts retain every elapsed time. Existing caches and concurrency vary; this is not controlled deployment performance.',
              '- API estimates use recorded token usage and dated project prices. Local compute, engineering-chat usage and human review are excluded. Unknown costs stay unknown.',
              '- Different suite/source identities must not be interpreted as a controlled comparison. Both systems must complete the same cases before comparing aggregate accuracy.',
              '- Execution errors include unavailable/interrupted endpoints; these are operational failures, not evidence that the model gave an incorrect clinical answer.',
              '', 'Run identities: `' + json.dumps(identities) + '`', '']
    Path(destination).write_text('\n'.join(lines))
    return summaries


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--results', type=Path, default=ROOT / 'results')
    parser.add_argument('--output', type=Path, help='Default SCORECARD.md, or SCORECARD-errata.md with --errata')
    parser.add_argument('--errata', nargs='?', const=ROOT / 'errata.json', type=Path,
                        help='Score against bench/errata.json (canonical readings + stated alternatives); off by default')
    parser.add_argument('--pass-k', type=int, default=3)
    args = parser.parse_args()
    errata = None
    if args.errata:
        from bench.errata import load_errata
        errata = load_errata(args.errata, {q['id']: q for q in load_questions()})
    output = args.output or ROOT / ('SCORECARD-errata.md' if errata else 'SCORECARD.md')
    print(json.dumps(write_scorecard(args.results, output, errata, args.pass_k), indent=2))
