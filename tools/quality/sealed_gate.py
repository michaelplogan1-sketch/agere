"""Sealed truth release gate: score an Agere build against the generator's sealed truth and keep AGGREGATES ONLY.

The generator's scorer (`python3 -m ecosystem.eval_scorer`, synthetic-patient-ecosystem) reads its own private/ truth
and prints aggregates; Agere never opens private/ (or the sealed question bank) and never sees a row. This gate:
  1. writes Agere's prediction files with tools/eval_predictions.py (Agere's own engines, no model calls);
  2. runs the generator's sealed scorer on them in a subprocess (--predictions, scoped to the sampled release);
  3. reduces the scorer's JSON to aggregates (numbers and short labels; any patient id, row list or long id list is
     dropped, defensively, even though the scorer already prints aggregates), compares them with the last passing gate for
     the same release (a quality metric that fell by more than TOLERANCE is a regression) and saves the record in
     bench/results/sealed_gate/ where the public scorecard reads it.
Release activation (warehouse_activation.activate) runs it after activating a build (default on; recorded on the
activation receipt). It heals, never blocks: a regression is recorded and shown; only an explicit enforce=True (CLI
--require-sealed-gate) refuses. It never runs the frozen sealed question bank.

    .venv/bin/python tools/quality/sealed_gate.py --build artifacts/<build> [--release healthcare-warehouse-v2.8] [--enforce]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
ECOSYSTEM = Path('/Users/mikel/synthetic-patient-ecosystem')
GATE_DIR = ROOT / 'bench' / 'results' / 'sealed_gate'
VERSION = 'agere-sealed-gate/1'
TOLERANCE = 0.02
QUALITY = re.compile(r'(?:^|_)(?:f1|precision|recall|accuracy|correct|agreement|sensitivity|ppv|kappa|auc)(?:_|$)', re.I)
_PRIVATE_KEYS = re.compile(r'patient|person|member|answers?$|^rows?$|predictions?$|_ids?$|^ids$|items$|examples?$|name$', re.I)


def aggregates(value, key=None, depth=0):
    """Numbers, booleans and short labels only; drop anything that could carry a row (ids, lists of records, long text)."""
    if depth > 8: return None
    if key is not None and _PRIVATE_KEYS.search(str(key)): return None
    if isinstance(value, bool) or isinstance(value, (int, float)) or value is None: return value
    if isinstance(value, str): return value if len(value) <= 80 and not re.search(r'\b(?:P|WHP|SYN|ECO)-[\w-]+', value) else None
    if isinstance(value, dict):
        out = {k: aggregates(v, k, depth + 1) for k, v in value.items()}
        return {k: v for k, v in out.items() if v is not None and v != {} and v != []} or None
    if isinstance(value, list):
        if len(value) > 20 or any(isinstance(v, dict) and any(_PRIVATE_KEYS.search(str(k)) for k in v) for v in value): return None
        out = [aggregates(v, None, depth + 1) for v in value]
        return [v for v in out if v is not None] or None
    return None


def leaves(value, prefix=''):
    if isinstance(value, dict):
        for k, v in value.items(): yield from leaves(v, f'{prefix}.{k}' if prefix else str(k))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield prefix, float(value)


def regressions(current, previous, tolerance=TOLERANCE):
    """Quality metrics (f1/precision/recall/accuracy/...) that dropped by more than tolerance since the previous gate."""
    if not previous: return []
    before = dict(leaves(previous))
    out = []
    for name, v in leaves(current):
        if QUALITY.search(name.split('.')[-1]) and name in before and before[name] - v > tolerance:
            out.append({'metric': name, 'previous': before[name], 'current': v})
    return out


def release_of(source_release):
    """artifacts/healthcare-warehouse-v2.8-sample-0.30 -> (healthcare-warehouse-v2.8, that directory as the sample)."""
    name = Path(source_release).name
    return re.sub(r'-sample-[\d.]+$', '', name), Path(source_release)


def previous(release, gate_dir=GATE_DIR):
    for f in sorted(Path(gate_dir).glob(f'*__{release}.json'), reverse=True):
        try: doc = json.loads(f.read_text())
        except (OSError, ValueError): continue
        if doc.get('status') == 'passed': return doc  # the last passing gate: repeated regressions are not absorbed
    return None


def run(build, *, release=None, sample_release=None, gate_dir=GATE_DIR, ecosystem=ECOSYSTEM, runner=subprocess.run,
        predictions=None, enforce=False, python=None):
    """-> gate record (aggregates only). Raises only with enforce=True and a regression."""
    build = Path(build)
    if release is None or sample_release is None:
        active = json.loads((build/'connection.json').read_text()) if (build/'connection.json').exists() else {}
        src = active.get('release') or json.loads((ROOT/'config/agere/active_warehouse.json').read_text()).get('source_release')
        r, s = release_of(src)
        release, sample_release = release or r, sample_release or s
    rel_dir = Path(ecosystem) / 'releases' / release
    if not (Path(ecosystem) / 'ecosystem' / 'eval_scorer.py').exists() or not rel_dir.exists():
        return {'schema': VERSION, 'status': 'unavailable', 'release': release,
                'reason': f'generator scorer or release not found under {ecosystem}'}
    with tempfile.TemporaryDirectory(prefix='agere-sealed-gate-') as tmp:
        pred = Path(predictions) if predictions else Path(tmp) / 'predictions'
        if not predictions:
            made = runner([python or sys.executable, str(ROOT/'tools/eval_predictions.py'), '--build', str(build), '--out', str(pred)],
                          cwd=ROOT, capture_output=True, text=True)
            if made.returncode != 0:
                return {'schema': VERSION, 'status': 'error', 'release': release, 'stage': 'predictions',
                        'reason': (made.stderr or '')[-300:]}
        res = runner(['python3', '-m', 'ecosystem.eval_scorer', '--release', f'releases/{release}', '--predictions', str(pred),
                      '--sample-release', str(sample_release)], cwd=ecosystem, capture_output=True, text=True)
    try:
        scored = json.loads(res.stdout)
    except ValueError:
        return {'schema': VERSION, 'status': 'error', 'release': release, 'stage': 'scorer', 'reason': 'scorer output was not JSON'}
    agg = aggregates(scored) or {}
    prior = previous(release, gate_dir)
    regressed = regressions(agg, (prior or {}).get('aggregates'))
    record = {'schema': VERSION, 'status': 'regressed' if regressed else 'passed', 'release': release, 'build': str(build),
              'scored_at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'aggregates': agg,
              'regressions': regressed, 'previous': (prior or {}).get('scored_at'), 'tolerance': TOLERANCE,
              'scope': 'aggregates only; produced by the generator scorer; Agere read no truth rows'}
    Path(gate_dir).mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')  # unique and sortable
    (Path(gate_dir) / f"{stamp}__{release}.json").write_text(json.dumps(record, indent=1))
    if enforce and regressed:
        raise ValueError(f'Sealed truth gate regressed on {len(regressed)} metric(s): ' + ', '.join(r['metric'] for r in regressed[:5]))
    return record


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--build', type=Path, required=True)
    ap.add_argument('--release'); ap.add_argument('--sample-release', type=Path)
    ap.add_argument('--enforce', action='store_true')
    a = ap.parse_args(argv)
    record = run(a.build, release=a.release, sample_release=a.sample_release, enforce=a.enforce)
    print(json.dumps({k: record[k] for k in record if k != 'build'}, indent=1))  # aggregates only
    return 0 if record.get('status') in ('passed', 'unavailable') else 1


if __name__ == '__main__':
    sys.exit(main())
