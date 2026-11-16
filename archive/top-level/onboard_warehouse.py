"""Automatic warehouse onboarding: profile -> propose mapping -> (human approval) -> apply -> verify.

  python onboard_warehouse.py model                                   # regenerate config/agere/semantic_model.json
  python onboard_warehouse.py profile  SOURCE --out profile.json
  python onboard_warehouse.py propose  SOURCE --out mapping.yaml [--encoder]
  python onboard_warehouse.py approve  mapping.yaml --reviewer NAME   # after a human has reviewed accept flags
  python onboard_warehouse.py apply    SOURCE mapping.yaml --out semantic.sqlite3
  python onboard_warehouse.py verify   SOURCE mapping.yaml semantic.sqlite3
  python onboard_warehouse.py prove-omop [--patients 200]             # OMOP-shaped round trip vs the native build

SOURCE is sqlite:///path, duckdb:///path or postgresql://... (read-only). No paid or hosted model is called.
Synthetic/public data only; existing artifacts are read, new outputs go under artifacts/onboarding/.
"""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import sys
import time

from clinical_core.agere.onboarding import apply as apply_mod, connectors, contract, matcher, omop_fixture, profile, verify

ROOT = Path(__file__).resolve().parent


def approve(mapping, reviewer):
    mapping['approval'] = {'approved': True, 'reviewer': reviewer,
                           'approved_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                           'accepted_fields': sum(1 for e in mapping['entities'].values() for f in e['fields'].values() if f.get('accept')),
                           'rejected_or_unreviewed_fields': sum(1 for e in mapping['entities'].values() for f in e['fields'].values()
                                                                if f.get('source') and not f.get('accept'))}
    return mapping


def prove_omop(n_patients, out_dir, use_encoder=True):
    cfg = json.loads((ROOT / 'config' / 'agere' / 'warehouse_v20.json').read_text())
    warehouse = Path(cfg['snapshot']['source_dataset']['source'])
    native = Path(cfg['build']) / 'stack' / 'semantic.sqlite3'
    if not warehouse.is_file() or not native.is_file():
        raise SystemExit('Needs the v2.0 synthetic warehouse and the native v20 semantic build (read-only)')
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    timings = {}
    t = time.perf_counter()
    model = contract.write_model()
    ids = omop_fixture.sample_patients(warehouse, n_patients)
    fixture = out / 'omop_cdm.sqlite'
    counts = omop_fixture.build(warehouse, fixture, ids)
    timings['reshape_s'] = round(time.perf_counter() - t, 1)
    (out / 'sample_patients.json').write_text(json.dumps(ids))
    conn = connectors.SQLiteConnector(fixture)
    t = time.perf_counter()
    prof = profile.profile_source(conn)
    timings['profile_s'] = round(time.perf_counter() - t, 2)
    (out / 'profile.json').write_text(json.dumps(prof, indent=1, default=str))
    (out / 'profile.summary.json').write_text(json.dumps(profile.summary(prof), indent=1))
    enc = matcher.Encoder(enabled=use_encoder)
    runs = {}
    for name, kwargs in [('lexical+aliases', {'encoder': None}), ('encoder+aliases', {'encoder': enc}),
                         ('lexical, no aliases', {'encoder': None, 'use_aliases': False}),
                         ('encoder, no aliases', {'encoder': enc, 'use_aliases': False})]:
        if kwargs.get('encoder') is not None and kwargs['encoder'].model is None: continue
        t = time.perf_counter()
        m = matcher.propose(model, prof, **kwargs)
        s = matcher.score_against_truth(m, omop_fixture.TRUTH)
        runs[name] = {'mapping': m, 'score': s, 'seconds': round(time.perf_counter() - t, 2)}
    primary = 'lexical+aliases'  # default policy: the local encoder is opt-in (measured no gain over lexical+abbreviations here)
    mapping = runs[primary]['mapping']
    matcher.save(mapping, out / 'mapping.proposed.yaml')
    unmapped_correct = sorted(mapping['unmapped_source_tables']) == sorted(omop_fixture.UNMAPPED_TABLES)
    matching = {'primary_configuration': primary, 'encoder': enc.reason, 'truth_pairs': runs[primary]['score']['truth_pairs'],
                'entity_tables_correct': sum(1 for e, (t_, _) in omop_fixture.TRUTH.items() if mapping['entities'][e]['source_table'] == t_),
                'entities': len(omop_fixture.TRUTH), 'distractor_table_left_unmapped': unmapped_correct,
                'runs': {k: {**v['score'], 'seconds': v['seconds']} for k, v in runs.items()}}
    # Two further schemas with known truth (Clarity-like EHR extract, flattened X12 claims lake).
    from clinical_core.agere.onboarding import fixtures, fixtures_claims_lake
    matching['other_fixtures'] = {}
    for name, build, truth in [('clarity_like', fixtures.clarity_like, fixtures.TRUTH),
                               ('claims_lake', fixtures_claims_lake.claims_lake, fixtures_claims_lake.TRUTH)]:
        path = out / f'{name}.sqlite'
        build(path)
        c = connectors.SQLiteConnector(path)
        p = profile.profile_source(c)
        res = {}
        for label, e in [('lexical+aliases', None), ('encoder+aliases', enc if enc.model is not None else None)]:
            if label.startswith('encoder') and e is None: continue
            s = matcher.score_against_truth(matcher.propose(model, p, encoder=e), truth)
            res[label] = {k: s[k] for k in ('precision', 'recall', 'f1', 'true_positive', 'false_positive', 'truth_pairs', 'wrong', 'missed')}
        c.close()
        matching['other_fixtures'][name] = res
    matching['development_history'] = {
        'note': 'First-contact scores (lexical+aliases) recorded before the generic fixes each fixture prompted; '
                'after those fixes no fixture is held out any more.',
        'clarity_like_first_contact': {'precision': 0.898, 'recall': 0.667},
        'claims_lake_first_contact': {'precision': 0.600, 'recall': 0.196}}
    (out / 'matching-report.json').write_text(json.dumps(matching, indent=1))
    # Approval: in a pilot a human edits accept flags; the proof run records an explicit automated approval of the proposal as-is.
    approved = approve(json.loads(json.dumps(mapping)), reviewer='automated proof run on a synthetic fixture (no human edits)')
    matcher.save(approved, out / 'mapping.approved.yaml')
    t = time.perf_counter()
    receipt = apply_mod.apply(approved, conn, out / 'semantic.sqlite3', model)
    timings['apply_s'] = round(time.perf_counter() - t, 2)
    t = time.perf_counter()
    inv = verify.invariants(out / 'semantic.sqlite3', approved, conn)
    timings['verify_s'] = round(time.perf_counter() - t, 2)
    native_sample = verify.native_extract(native, ids, out / 'native-sample.sqlite3')
    rec = verify.reconcile(out / 'semantic.sqlite3', native_sample)
    conn.close()
    summary = {'source': {'warehouse': str(warehouse), 'native_semantic': str(native), 'patients': len(ids), 'omop_rows': counts},
               'matching': {k: {kk: v['score'][kk] for kk in ('precision', 'recall', 'f1', 'true_positive', 'false_positive')}
                            for k, v in runs.items()},
               'apply': receipt, 'invariants': {'passed': inv['passed'], 'failed': inv['failed'], 'checks': len(inv['checks'])},
               'reconciliation': {'all_within_tolerance': rec['all_within_tolerance'], 'tolerance': rec['tolerance'],
                                  'acceptance_matched': f"{rec['acceptance_matched']}/{len(rec['acceptance'])}"},
               'timings': timings, 'paid_api_calls': 0, 'synthetic': True}
    (out / 'verification.json').write_text(json.dumps(inv, indent=1, default=str))
    (out / 'reconciliation.json').write_text(json.dumps(rec, indent=1, default=str))
    (out / 'summary.json').write_text(json.dumps(summary, indent=1, default=str))
    return summary, matching, inv, rec


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('model')
    p = sub.add_parser('profile'); p.add_argument('source'); p.add_argument('--out', required=True); p.add_argument('--sample', type=int, default=5000)
    p = sub.add_parser('propose'); p.add_argument('source'); p.add_argument('--out', required=True); p.add_argument('--encoder', action='store_true', help='blend local MiniLM name similarity (opt-in)')
    p = sub.add_parser('approve'); p.add_argument('mapping'); p.add_argument('--reviewer', required=True)
    p = sub.add_parser('apply'); p.add_argument('source'); p.add_argument('mapping'); p.add_argument('--out', required=True)
    p = sub.add_parser('verify'); p.add_argument('source'); p.add_argument('mapping'); p.add_argument('semantic')
    p = sub.add_parser('prove-omop'); p.add_argument('--patients', type=int, default=200)
    p.add_argument('--out', default=str(contract.onboarding_dir() / 'omop-proof')); p.add_argument('--no-encoder', action='store_true')
    a = ap.parse_args(argv)
    if a.cmd == 'model':
        m = contract.write_model(); print(contract.MODEL_PATH, len(m['entities']), 'entities'); return
    if a.cmd == 'approve':
        m = approve(matcher.load(a.mapping), a.reviewer); matcher.save(m, a.mapping); print(json.dumps(m['approval'])); return
    if a.cmd == 'prove-omop':
        summary, matching, inv, rec = prove_omop(a.patients, a.out, not a.no_encoder)
        print(json.dumps(summary, indent=1, default=str)); return
    conn = connectors.connect(a.source)
    try:
        if a.cmd == 'profile':
            prof = profile.profile_source(conn, a.sample)
            Path(a.out).write_text(json.dumps(prof, indent=1, default=str)); print(json.dumps(profile.summary(prof), indent=1))
        elif a.cmd == 'propose':
            prof = profile.profile_source(conn)
            m = matcher.propose(contract.load_model(), prof, encoder=matcher.Encoder() if a.encoder else None)
            matcher.save(m, a.out)
            for e, v in m['entities'].items(): print(f"{e:13s} <- {v['source_table']} ({v['confidence']})")
        elif a.cmd == 'apply':
            print(json.dumps(apply_mod.apply(matcher.load(a.mapping), conn, a.out), indent=1, default=str))
        elif a.cmd == 'verify':
            r = verify.invariants(a.semantic, matcher.load(a.mapping), conn)
            print(json.dumps(r, indent=1, default=str)); sys.exit(0 if r['passed'] else 1)
    finally:
        conn.close()


if __name__ == '__main__':
    main()
