"""Shared definitions for the study harness: arms, question sets, fingerprints, file layout.

Layout of one run (artifacts/study/<run_id>/):
    run.json                         git commit, pricing/thresholds hashes, question-set fingerprints, arms + env flags
    <arm>/p<pass>/answers.jsonl      one row per question (set, id, answer fields, cost_usd, seconds, route, decision, ...)
    <arm>/p<pass>/breadth_submission.jsonl   breadth rows in the generator scorer's submission shape
    <arm>/p<pass>/timings.json       prepare / total / per-question seconds
    <arm>/p<pass>/ledger.jsonl       the arm-pass ledger excerpt (one line per ledger call: model, status, usage, cost)
    <arm>/p<pass>/ledger.sqlite3     the arm-pass ledger itself (fresh per pass; arm A pass 2 reuses pass 1: warm)
    <arm>/p<pass>/env.json           the environment flags the worker ran with
    <arm>/p<pass>/score.json         per-question scored rows (score.py)
    analysis.json                    analyze.py
    report.html, report.md           report.py
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
STUDY_ROOT = ROOT / 'artifacts' / 'study'
GOLD = ROOT / 'docs' / 'agere' / 'gold10'
PRICING = ROOT / 'config' / 'agere' / 'pricing.json'
THRESHOLDS = ROOT / 'config' / 'agere' / 'cascade_thresholds.json'
WORKSPACE = 'pilot-native-v2-12'
GENERATOR = Path('/Users/mikel/synthetic-patient-ecosystem')
BREADTH_RELEASE = 'releases/healthcare-warehouse-v2.12'
BREADTH_SAMPLE = 'releases/healthcare-warehouse-v2.12-sample-0.30-native'
BREADTH_QUESTIONS = GENERATOR / BREADTH_SAMPLE / 'questions' / 'questions.jsonl'
# The second scored set can be swapped for another sealed generator set (Gold-50: questions/gold50.jsonl, scored with
# --gold50): AGERE_STUDY_SET2_QUESTIONS names the file, AGERE_STUDY_SET2_SCORER the scorer flag. Defaults: the native 150.
SET2_QUESTIONS = Path(os.environ.get('AGERE_STUDY_SET2_QUESTIONS') or BREADTH_QUESTIONS)
SET2_SCORER = os.environ.get('AGERE_STUDY_SET2_SCORER') or '--questions'

# Every flag an arm may set. Arm A (and every other arm) starts with all of these removed from the inherited environment,
# so a flag left in the owner's shell can never leak into an arm; each arm then sets only its own.
ABLATION_FLAGS = ('AGERE_COHORT_FAMILIES', 'AGERE_DQ_FAMILIES', 'AGERE_VBC_FAMILIES', 'AGERE_CASCADE', 'AGERE_BOTTLED_COHORT',
                  'AGERE_RESULT_CACHE', 'AGERE_SEMANTIC_CACHE', 'AGERE_LEARNED', 'AGERE_PLAN_CACHE', 'AGERE_SPECIALISTS_FIRST',
                  'AGERE_ENGINES_DEFER_ON_DEFINITIONS', 'AGERE_HYBRID_MODEL', 'AGERE_HYBRID_ESCALATION_MODEL',
                  'AGERE_HYBRID_REVIEW', 'AGERE_HYBRID_REVIEW_MODEL')

ARMS = {
    'A': {'kind': 'agere', 'pipeline': 'full', 'env': {}, 'label': 'Agere full stack'},
    'B': {'kind': 'baseline', 'model': 'claude-opus-5-5', 'env': {}, 'label': 'Frontier model for everything (text-to-SQL)'},
    'C': {'kind': 'baseline', 'model': 'claude-sonnet-5-5', 'env': {}, 'label': 'Mid model for everything (text-to-SQL)'},
    'D': {'kind': 'baseline', 'model': 'claude-haiku-5-5', 'env': {}, 'label': 'Small model for everything (text-to-SQL)'},
    'E': {'kind': 'agere', 'pipeline': 'hybrid', 'env': {}, 'label': 'Agentic analysts for everything (hybrid core)'},
    'A2': {'kind': 'agere', 'pipeline': 'full', 'label': 'Agere, Opus analysts and tie-break for unclaimed questions (exploratory)',
           'env': {'AGERE_HYBRID_MODEL': 'claude-opus-5-5', 'AGERE_HYBRID_ESCALATION_MODEL': 'claude-opus-5-5'}},
    'A3': {'kind': 'agere', 'pipeline': 'full', 'label': 'Agere, bounded frontier review of failed or unclear analysts (candidate)',
           'env': {'AGERE_HYBRID_REVIEW': 'on', 'AGERE_HYBRID_REVIEW_MODEL': 'claude-opus-5-5'}},
    'F1': {'kind': 'agere', 'pipeline': 'full', 'label': 'A without families',
           'env': {'AGERE_COHORT_FAMILIES': 'off', 'AGERE_DQ_FAMILIES': 'off', 'AGERE_VBC_FAMILIES': 'off'}},
    'F2': {'kind': 'agere', 'pipeline': 'full', 'label': 'A without certificates', 'env': {'AGERE_CASCADE': 'off'}},
    'F3': {'kind': 'agere', 'pipeline': 'full', 'label': 'A without the bottled model', 'env': {'AGERE_BOTTLED_COHORT': 'off'}},
    'F4': {'kind': 'agere', 'pipeline': 'full', 'label': 'A without caches',
           'env': {'AGERE_RESULT_CACHE': 'off', 'AGERE_SEMANTIC_CACHE': 'off', 'AGERE_LEARNED': 'off', 'AGERE_PLAN_CACHE': 'off'}},
    'F5': {'kind': 'agere', 'pipeline': 'full', 'label': 'A without specialists-first', 'env': {'AGERE_SPECIALISTS_FIRST': 'off'}},
    'F6': {'kind': 'agere', 'pipeline': 'full', 'label': 'A without the stated-definition rule',
           'env': {'AGERE_ENGINES_DEFER_ON_DEFINITIONS': 'off'}},
}
ARM_ORDER = ('A', 'A2', 'A3', 'B', 'C', 'D', 'E', 'F1', 'F2', 'F3', 'F4', 'F5', 'F6')
WARM_ARM = 'A'  # the one arm whose second pass reuses its first pass's caches (protocol: "warm on the second pass by design")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def sha16(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def git_state():
    def git(*args):
        try:
            return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            return ''
    status = git('status', '--porcelain', '--untracked-files=no')
    return {'commit': git('rev-parse', 'HEAD').strip() or None, 'dirty': bool(status.strip()),
            'dirty_files': [l[3:] for l in status.splitlines() if l.strip()],
            'diff_sha256': hashlib.sha256(git('diff', 'HEAD').encode()).hexdigest() if status.strip() else None}


def read_jsonl(path):
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def write_jsonl(path, rows):
    Path(path).write_text(''.join(json.dumps(r, default=str) + '\n' for r in rows))


def locked_questions():
    """The locked set: docs/agere/gold10/questions.jsonl restricted to the ids the locked reference holds. The reference is
    opened only through the official scorer module (bench/gold10_run.py), and only its question ids are used here."""
    import sys
    sys.path.insert(0, str(ROOT / 'bench'))
    import gold10_run
    _, ref = gold10_run.reference()
    wanted = set(ref['questions'])
    out = []
    for q in read_jsonl(GOLD / 'questions.jsonl'):
        if q['id'] in wanted:
            out.append({'set': 'locked', 'id': q['id'], 'question': q['question'] + ' Definition: ' + q['definition'],
                        'answer_type': q['answer_type'], 'domain': q.get('domain')})
    return out, ref.get('workspace') or WORKSPACE


def breadth_questions():
    out = []
    rows = read_jsonl(SET2_QUESTIONS)
    shared = next((r.get('definition') for r in rows if r.get('id') == 'SHARED'), None)  # a set's shared definitions
    for q in rows:
        if q.get('id') == 'SHARED':
            continue
        out.append({'set': 'breadth', 'id': q['id'],
                    'question': q['question'] + (' Definition: ' + q['definition'] if q.get('definition') else '')
                    + (' Shared definitions: ' + shared if shared else ''),
                    'answer_type': q['answer_type'], 'domain': q.get('domain')})
    return out


def load_questions(which, limit=None):
    sets = ('locked', 'breadth') if which == 'both' else (which,)
    qs = []
    for s in sets:
        part = locked_questions()[0] if s == 'locked' else breadth_questions()
        qs.extend(part[:limit] if limit else part)
    return qs


def question_fingerprint(questions):
    """Per set: sha256 over the sorted (id, question text, answer_type) triples, as asked."""
    out = {}
    for s in sorted({q['set'] for q in questions}):
        items = sorted((q['id'], q['question'], q['answer_type']) for q in questions if q['set'] == s)
        out[s] = {'n': len(items), 'sha256': hashlib.sha256(json.dumps(items).encode()).hexdigest()}
    return out


def arm_pass_dir(run_dir, arm, n):
    return Path(run_dir) / arm / f'p{n}'


def worker_env(arm, base=None):
    """The environment one arm's worker runs with: inherited minus every ablation flag, plus the arm's own flags."""
    env = dict(os.environ if base is None else base)
    for k in ABLATION_FLAGS:
        env.pop(k, None)
    env.update(ARMS[arm]['env'])
    return env


def agere_flags(env):
    return {k: v for k, v in sorted(env.items()) if k.startswith('AGERE_')}
