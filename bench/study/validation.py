"""Fail closed before model calls or scoring unless every selected reference is validated.

This reads attestations and hashes, never generator truth. A sealed generator key
alone is insufficient. Validation must cover every question, regardless of which
arm got it right. Unit-test fixtures inject their own reference explicitly.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from bench.study import common

REGISTRY = common.ROOT / 'docs/agere/study/reference_validation.json'


def question_digest(q):
    return hashlib.sha256(json.dumps([q['id'], q['question'], q['answer_type']],
                                     ensure_ascii=True).encode()).hexdigest()


def require_validated(questions, *, registry_path=None):
    registry = json.loads(Path(registry_path or REGISTRY).read_text())
    if registry.get('schema') != 'agere-reference-validation/1':
        raise ValueError('Unknown reference validation schema')
    if not questions:
        raise ValueError('No questions selected')
    seen, used = set(), {}
    for q in questions:
        identity = (q['set'], q['id'])
        if identity in seen:
            raise ValueError(f'Duplicate study question: {identity}')
        seen.add(identity)
        matches = [s for s in registry['sets'] if s['set'] == q['set']
                   and (s.get('questions', {}).get(q['id']) or {}).get('sha256') == question_digest(q)]
        if len(matches) != 1:
            raise ValueError(f'Reference validation required for {q["set"]}:{q["id"]}; no matching question attestation')
        item = matches[0]
        entry = item['questions'][q['id']]
        if item.get('status') != 'validated' or entry.get('status') != 'validated':
            raise ValueError(f'Reference validation required for {q["set"]}:{q["id"]}; {item.get("reason", "audit incomplete")}')
        if not item.get('generator_digest') or not item.get('independent_derivation') or not item.get('reconciliation'):
            raise ValueError(f'Incomplete independent-validation evidence: {item["name"]}')
        if not item.get('evidence'):
            raise ValueError(f'Missing validation evidence: {item["name"]}')
        for evidence in item['evidence']:
            path = (common.ROOT / evidence['path']).resolve()
            if not path.is_relative_to(common.ROOT.resolve()) or not path.is_file():
                raise ValueError('Missing or outside-project validation evidence')
            if common.sha256_file(path) != evidence['sha256']:
                raise ValueError(f'Validation evidence changed: {evidence["path"]}')
        used[item['name']] = {'generator_digest': item['generator_digest'], 'scorer': item['scorer']}
    return {'registry_sha256': common.sha256_file(registry_path or REGISTRY), 'sets': used,
            'questions': common.question_fingerprint(questions)}
