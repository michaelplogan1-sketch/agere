"""Stage diagnostics: attribute every cascade failure to the stage that caused it (retrieval / rerank / reading / planning /
execution / check) in the receipt and the usage tracer.

The checked-proposer cascade logs one entry per proposer ({proposer, outcome, detail, seconds}). Each non-accepting entry is
assigned the pipeline stage it failed in (RAGChecker / EvalMem-style separation of retrieval, reading and generation
errors, extended to Agere's plan -> execute -> check path):

  retrieval   an evidence / reuse tier found nothing to reuse or retrieve (semantic cache, paraphrase reuse, evidence
              retrieval, learned SQL) or a retrieval error
  rerank      the reranker / schema reranker failed or ranked nothing usable
  reading     a reader tier (evidence review, atomic System One, HCC suspecting, terminology mapping) declined or errored
  planning    no plan, a plan that dropped a requirement (literal check / requirement ledger), an unresolved or failed semantic
              review, a stale reused plan (PlanFence), or a typed interpreter decline
  execution   the read-only execution of the plan failed (SQL / engine error)
  check       the executed answer was rejected by the output-shape check or answer_checks (guard, zero-result probe,
              cross-check, certificate, System One check)

A question's failed_stage is the furthest stage any proposer reached before failing (a plan that executed and was then
rejected by a check failed at 'check', not at 'planning'); an answered question reports failed_stage None and lists the
failures it recovered from. Deterministic, free, no model calls.
"""
from collections import Counter
import re

VERSION = 'stage-diagnostics/1'
STAGES = ('retrieval', 'rerank', 'reading', 'planning', 'execution', 'check')
RETRIEVAL_TIERS = frozenset({'semantic_cache', 'paraphrase_reuse', 'evidence_retrieval', 'learned_verified_sql', 'exact_local_plan'})
READING_TIERS = frozenset({'evidence_review', 'atomic_system_one', 'hcc_suspecting', 'terminology_mapping'})
FAILED = frozenset({'rejected', 'error', 'declined', 'no proposal', 'decline held', 'skipped', 'not available'})
CHECK = re.compile(r'answer guard:|cross-check:|answer shape:|certificate:|zero[- ]result|probe|system one check|answer checks|'
                   r'the question asks for an? |the plan did not produce an answer|output check', re.I)
EXECUTION = re.compile(r'execution failed|operationalerror|sqlite3?\.|programmingerror|duckdb|no such (?:table|column)|'
                       r'database (?:is locked|disk image)|timed? ?out|timeout|absent from this warehouse|malformed', re.I)
RERANK = re.compile(r'\brerank|reranker|schema_rerank|bge', re.I)
RETRIEVAL = re.compile(r'\bretriev|no (?:passages?|evidence) (?:found|retrieved)|semantic cache|no cached|cache miss|vector', re.I)
READING = re.compile(r'\breader\b|\breading\b|windowed|student|extract', re.I)


def stage_of(entry):
    """The stage one proposer-log entry failed in, or None when it did not fail."""
    outcome, name, detail = entry.get('outcome'), entry.get('proposer') or '', str(entry.get('detail') or '')
    if outcome not in FAILED: return None
    if outcome == 'skipped': return None  # not attempted (budget / outage), not a stage failure
    if RERANK.search(detail): return 'rerank'
    if outcome == 'rejected':
        if EXECUTION.search(detail) and 'execution failed' in detail.lower(): return 'execution'
        if CHECK.search(detail): return 'check'
        return 'planning'  # literal check / requirement ledger / semantic review / PlanFence
    if outcome == 'error':
        if EXECUTION.search(detail): return 'execution'
        if name in READING_TIERS or READING.search(detail): return 'reading'
        if name in RETRIEVAL_TIERS or RETRIEVAL.search(detail): return 'retrieval'
        return 'planning'
    # declined / no proposal / decline held / not available
    if name in RETRIEVAL_TIERS: return 'retrieval'
    if name in READING_TIERS: return 'reading'
    if RETRIEVAL.search(detail): return 'retrieval'
    return 'planning'


def attribute(log, *, answered_by=None, error=None):
    """{schema, failed_stage, by_stage, events, recovered} for one question's proposer log."""
    events = []
    for e in log or []:
        stage = stage_of(e)
        if stage: events.append({'proposer': e.get('proposer'), 'outcome': e.get('outcome'), 'stage': stage,
                                 'detail': str(e.get('detail') or '')[:240]})
    by_stage = Counter(ev['stage'] for ev in events)
    furthest = max((ev['stage'] for ev in events), key=STAGES.index, default=None)
    if answered_by:
        failed = None
    elif error is not None and not events:
        failed = 'execution' if EXECUTION.search(str(error)) else 'planning'
    else:
        failed = furthest or 'planning'
    return {'schema': VERSION, 'failed_stage': failed, 'answered_by': answered_by,
            'by_stage': {s: by_stage[s] for s in STAGES if by_stage[s]}, 'furthest_stage_reached': furthest,
            'recovered': bool(answered_by and events), 'events': events[:40]}


def from_result(result=None, exc=None):
    """Stage diagnostics for a compose result or a failed compose (exception with planner_receipt): reads the attached
    attribution when present, else attributes the receipt's proposer log."""
    if exc is not None:
        receipt = getattr(exc, 'planner_receipt', {}) or {}
        if receipt.get('stage_diagnostics'): return receipt['stage_diagnostics']
        return attribute(receipt.get('proposers') or [], error=exc)
    receipt = (result or {}).get('planner_receipt') or {}
    interp = receipt.get('interpretation') or {}
    if interp.get('stage_diagnostics'): return interp['stage_diagnostics']
    answer = (result or {}).get('answer') or {}
    by = interp.get('interpreter') if answer.get('status') in ('answered', 'partial') else None
    return attribute(interp.get('proposers') or receipt.get('proposers') or [], answered_by=by)


def summarize(rows):
    """Aggregate per-question diagnostics (usage tracer rows) -> counts by failed stage and by failing proposer stage."""
    failed, events = Counter(), Counter()
    for r in rows:
        d = r.get('stage_diagnostics') or {}
        failed[d.get('failed_stage') or 'answered'] += 1
        for s, n in (d.get('by_stage') or {}).items(): events[s] += n
    return {'schema': VERSION, 'questions': sum(failed.values()), 'failed_stage': dict(failed), 'proposer_failures_by_stage': dict(events)}
