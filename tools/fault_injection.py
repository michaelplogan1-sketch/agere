"""Aerospace-style fault containment: inject failure sequences into the answer path and verify recovery.

A scenario is a script of faults applied to named cascade tiers in one question, plus the tier that is healthy and answers. The
cascade is real (proposers.Cascade.run with its ordering, outage handling, receipts and stage diagnostics); the tiers are scripted
stand-ins so a fault is exact and repeatable. Fault kinds:

  outage:<kind>    provider outage (billing / rate_limit / overloaded / server)  -> later Claude tiers skipped, free tiers continue
  timeout          the tier raises TimeoutError                                  -> noted as an error, the next tier runs
  error            the tier raises an arbitrary exception                        -> noted, the next tier runs
  decline          the tier declines the question                                -> the next tier runs
  corrupt_store    the tier hits a malformed state store (sqlite DatabaseError)  -> store quarantined, the next tier runs

Containment invariants checked on every scenario: (1) the question ends in an answer (or a typed provider outage when every
answering tier is down), never an unhandled exception; (2) every injected fault is in the receipt with its tier; (3) a Claude outage is
recorded once and later Claude tiers are skipped; (4) the healthy tier still answers after any number of upstream faults; (5) a
corrupt state store is quarantined (renamed, evidence kept) and a fresh store can be opened; (6) stage diagnostics attribute the
failures; (7) engines on a warehouse missing their tables raise DataAbsent, not a raw sqlite error. Real-component probes (corrupt file
on disk, missing tables in a real engine) run beside the scripted ones. Free: no network, no model, synthetic data.

    python tools/fault_injection.py            # runs every scenario, prints a recovery table, exit 1 on any failed invariant
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('AGERE_LOCAL_TYPED', 'off')
os.environ.setdefault('AGERE_LOCAL_NORMALIZER', 'off')

from clinical_core.agere import fault_containment as FC  # noqa: E402
from clinical_core.agere.gateway import ProviderUnavailable  # noqa: E402
from clinical_core.agere.clinical import proposers as P, claim_verification, stage_diagnostics  # noqa: E402
from clinical_core.agere.clinical.question_input import QuestionNotUnderstood  # noqa: E402

GRAPH = {'interpretation': 'count patients', 'coverage': 'supported_observation',
         'nodes': [{'id': 'n1', 'operation': 'sql', 'parameters': {'query': 'SELECT count(DISTINCT patient_id) FROM patient', 'returns': 'count'}}]}
ANSWER = {'answer': {'status': 'answered', 'answer_type': 'count', 'count': 4115, 'headline': '4115 patients'}, 'results': []}
SWITCHES = ('AGERE_S1_CHECK', 'AGERE_S1_ROUTER', 'AGERE_S1_FALLBACK', 'AGERE_PROPOSERS', 'AGERE_PROPOSER_SKIP', 'AGERE_S1_CHECK_REJECT', 'AGERE_S1_ROUTE_MIN')

SCENARIOS = [
    {'name': 'single billing outage on the hosted tier', 'faults': {'hosted_typed_interpreter': 'outage:billing'}, 'healthy': 'typed_decider'},
    {'name': 'rate limit then timeout then error', 'faults': {'hosted_typed_interpreter': 'outage:rate_limit', 'semantic_cache': 'timeout', 'evidence_retrieval': 'error'}, 'healthy': 'legacy_cost_ladder'},
    {'name': 'timeout storm on every free tier ahead of the healthy one', 'faults': 'all_before', 'fault_kind': 'timeout', 'healthy': 'legacy_cost_ladder'},
    {'name': 'corrupt learning store mid-question', 'faults': {'learned_verified_sql': 'corrupt_store'}, 'healthy': 'typed_decider', 'corrupt_file': 'learning.sqlite3'},
    {'name': 'outage + corrupt store + decline + timeout in one question', 'faults': {'hosted_typed_interpreter': 'outage:overloaded', 'learned_verified_sql': 'corrupt_store',
                                                                                    'semantic_cache': 'decline', 'bottled_sql': 'timeout'}, 'healthy': 'legacy_cost_ladder', 'corrupt_file': 'learning.sqlite3'},
    {'name': 'every answering tier down: typed outage with receipts', 'faults': 'all', 'fault_kind': 'outage:billing', 'healthy': None},
]


def raiser(kind, stack_path, corrupt_file):
    def fn(self):
        if kind.startswith('outage:'): raise ProviderUnavailable(f'provider_unavailable ({kind[7:]}): injected', kind=kind[7:])
        if kind == 'timeout': raise TimeoutError('injected: tier timed out')
        if kind == 'error': raise RuntimeError('injected: tier failed')
        if kind == 'decline': raise QuestionNotUnderstood('injected: tier declined')
        if kind == 'corrupt_store':
            if corrupt_file: (Path(stack_path) / corrupt_file).write_bytes(b'this is not a sqlite database' * 64)
            raise sqlite3.DatabaseError('database disk image is malformed')
        raise ValueError(kind)
    return fn


def candidate(name):
    contract = {'kind': 'checked_graph', 'graph': GRAPH, 'executed': ANSWER, 'id': 'fi-1', 'legacy_receipt': {}}
    return contract, {'interpreter': name, 'review': 'literal_check_then_output_check', 'execution_accepted': True, 'attempts': []}


def run_scenario(sc):
    """-> {'name', 'outcome', 'answered_by', 'log', 'violations': [...], 'healed': [...]}"""
    violations, order = [], tuple(P.ORDER)
    import os
    from unittest.mock import patch as _patch  # the scenario's healthy tier may be off the default path: switched on for the scenario
    with _patch.dict(os.environ, {'AGERE_PROPOSER_ON': ','.join(filter(None, [os.environ.get('AGERE_PROPOSER_ON', ''), sc.get('healthy') or '']))}), \
            tempfile.TemporaryDirectory() as d:
        stack = SimpleNamespace(allow_live=True, path=d)
        if sc.get('corrupt_file'):  # a healthy store that the fault will corrupt
            db = sqlite3.connect(Path(d) / sc['corrupt_file']); db.execute('CREATE TABLE verified(question TEXT)'); db.commit(); db.close()
        faults = sc['faults']
        if faults == 'all': faults = {n: sc['fault_kind'] for n in order if n in P.PAID or n in P.CLAUDE_TIERS or True}
        if faults == 'all_before':
            faults = {n: sc['fault_kind'] for n in order[:order.index(sc['healthy'])] if n not in P.CLAUDE_TIERS}
        behaviours = {n: raiser(k, d, sc.get('corrupt_file')) for n, k in faults.items()}
        if sc['healthy']: behaviours[sc['healthy']] = lambda self, n=sc['healthy']: candidate(n)
        reached = []

        def stub(name):
            def tier(self):
                reached.append(name)
                fn = behaviours.get(name)
                return fn(self) if fn else None
            return tier
        env = {k: v for k, v in os.environ.items() if k not in SWITCHES}
        env.update({'AGERE_S1_ROUTER': 'off', 'AGERE_S1_CHECK': 'off', 'AGERE_S1_FALLBACK': 'off', 'AGERE_LOCAL_TYPED': 'off'})
        outcome, found, exc_out = None, None, None
        with patch.dict(os.environ, env, clear=True), patch.multiple(P.Cascade, **{'tier_' + n: stub(n) for n in order}), \
                patch.object(claim_verification, 'verify', lambda c, o, n, f: (f[0], f[1])), \
                patch.object(P.Cascade, 'finish', lambda self, interp, name: {**interp, 'interpreter': name, 'proposers': self.log}):
            cascade = P.Cascade(stack, SimpleNamespace(identity={}), 'how many patients', ledger=SimpleNamespace(close=lambda: None))
            try:
                found = cascade.run()
                outcome = 'answered'
            except ProviderUnavailable as exc:
                outcome, exc_out = 'typed_outage', exc
            except QuestionNotUnderstood as exc:
                outcome, exc_out = 'not_understood', exc
            except Exception as exc:  # noqa: BLE001 - an unhandled exception IS the violation
                outcome, exc_out = 'unhandled', exc
        log = cascade.log
        errored = {e['proposer'] for e in log if e['outcome'] in ('error', 'declined')}
        # (1) end state
        if outcome == 'unhandled': violations.append(f'unhandled {type(exc_out).__name__}: {exc_out}')
        if sc['healthy'] and outcome != 'answered': violations.append(f'healthy tier {sc["healthy"]} did not answer ({outcome})')
        if not sc['healthy'] and outcome != 'typed_outage': violations.append(f'expected a typed outage, got {outcome}')
        # (2) every fault in the receipt
        for tier, kind in faults.items():
            if tier in reached and kind != 'outage:x':
                entry = [e for e in log if e['proposer'] == tier]
                if not entry or entry[0]['outcome'] not in ('error', 'declined', 'skipped'): violations.append(f'fault on {tier} ({kind}) missing from the receipt')
        # (3) one outage per question, later Claude tiers skipped
        if cascade.claude_down:
            # the first faulted Claude tier the cascade actually asked (an off-default tier is never asked)
            first = next(t for t in order if t in reached and t in P.CLAUDE_TIERS and str(faults.get(t, '')).startswith('outage:'))
            later = [n for n in order[order.index(first) + 1:] if n in P.CLAUDE_TIERS]
            hit = [n for n in later if n in reached]
            if hit: violations.append(f'Claude tiers ran after the outage: {hit}')
        # (4) healthy tier reached
        if sc['healthy'] and sc['healthy'] not in reached: violations.append('healthy tier never reached')
        # (5) corrupt store quarantined and a fresh store can be opened
        healed = [h for e in log for h in e.get('healed_stores', [])]
        if sc.get('corrupt_file'):
            if not healed: violations.append('corrupt store was not quarantined')
            else:
                if not any(Path(d).glob(sc['corrupt_file'] + '.corrupt-*')): violations.append('quarantined file not kept as evidence')
                try:
                    db = sqlite3.connect(Path(d) / sc['corrupt_file']); db.execute('CREATE TABLE IF NOT EXISTS verified(question TEXT)'); db.execute('SELECT count(*) FROM verified').fetchone(); db.close()
                except sqlite3.DatabaseError as exc: violations.append(f'store not recreatable after healing: {exc}')
        # (6) stage diagnostics attribute the failures
        diag = stage_diagnostics.attribute(log, answered_by=getattr(found[1], 'get', lambda *_: None)('interpreter') if found else None, error=exc_out)
        if errored and not diag['events']: violations.append('stage diagnostics attributed none of the failures')
    return {'name': sc['name'], 'outcome': outcome, 'answered_by': (found[1].get('interpreter') if found else None), 'faults_injected': len(faults),
            'faults_in_receipt': len([e for e in log if e['outcome'] in ('error', 'declined', 'skipped')]), 'healed': healed,
            'failed_stage': diag['failed_stage'], 'violations': violations}


def probe_corrupt_file():
    """Real files: corrupt the learning store, run the real semantic_cache path, heal, and look up again (a miss, not a crash)."""
    violations = []
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / 'learning.sqlite3').write_bytes(b'garbage' * 500)
        from clinical_core.agere.clinical import semantic_cache
        try:
            semantic_cache._verified(d, 'ctx'); violations.append('corruption was not detected by the real reader')
        except sqlite3.DatabaseError as exc:
            heals = FC.on_tier_error(d, exc)
            if [h['store'] for h in heals] != ['learning.sqlite3']: violations.append(f'wrong heal: {heals}')
        if (Path(d) / 'learning.sqlite3').exists(): violations.append('corrupt store still in place')
        r = semantic_cache.lookup(d, 'how many patients have diabetes', SimpleNamespace(encode=lambda x: []), threshold=0.9)
        if r[0] is not None: violations.append('lookup should be a miss after healing')
    return {'name': 'real corrupt learning store: detect, quarantine, miss', 'violations': violations}


def probe_missing_tables():
    """Real engines on a warehouse with no tables: DataAbsent (or a structured partial), never a raw sqlite error."""
    from clinical_core.agere.clinical import engine_suite
    violations, results = [], {}
    with tempfile.TemporaryDirectory() as d:
        db = sqlite3.connect(Path(d) / 'semantic.sqlite3'); db.execute('CREATE TABLE meta(key TEXT, value TEXT)'); db.commit(); db.close()
        for op, mod in engine_suite.available().items():
            if op in ('domain_pack', 'saved_monitors'): continue
            try:
                FC.contain_engine(mod, d, None, {} if op not in ('policy_search',) else {}, '2030-05-27'); results[op] = 'returned'
            except FC.DataAbsent as exc: results[op] = 'DataAbsent: ' + ', '.join(exc.sources)
            except (ValueError, FileNotFoundError, KeyError, TypeError) as exc: results[op] = f'{type(exc).__name__} (handled input error)'
            except sqlite3.Error as exc:
                results[op] = f'RAW sqlite {type(exc).__name__}'; violations.append(f'{op} raised a raw sqlite error: {exc}')
    return {'name': 'real engines on an empty warehouse', 'violations': violations, 'per_engine': results}


def main():
    rows = [run_scenario(sc) for sc in SCENARIOS] + [probe_corrupt_file(), probe_missing_tables()]
    bad = 0
    for r in rows:
        ok = not r['violations']; bad += not ok
        print(('PASS ' if ok else 'FAIL ') + r['name'] + (f" -> {r.get('outcome')} by {r.get('answered_by')}, stage {r.get('failed_stage')}" if 'outcome' in r else ''))
        for v in r['violations']: print('     violation:', v)
        if r.get('per_engine'):
            for op, how in sorted(r['per_engine'].items()): print(f'     {op}: {how}')
    print(f'{len(rows) - bad}/{len(rows)} scenarios contained')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
