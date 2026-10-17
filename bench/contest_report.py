"""Paired contest report from saved runs (bench/contest.py) and the sealed scorer's --per-question output.

Reads <round>/<contestant>/<mode>/attempt-<k>/{rows.jsonl, per_question.json}. Correctness comes ONLY from the scorer's
per-question ids (correct / answered); rows give domain, recoverability, cost and latency. Writes one Markdown page and
the same numbers as JSON:
  - accuracy and precision on answered, Wilson 95% CIs, per contestant x mode x domain (pooled over attempts, plus
    majority-of-k and pass^k per question);
  - McNemar exact test of agere vs each baseline on paired per-question majority correctness;
  - abstention correctness on unsupported questions (and abstentions on answerable ones);
  - cost per question and correct answers per dollar, only when precision on answered >= 90% (else "below floor");
  - latency median / p90.

    python bench/contest_report.py results/contest/v3-r1 [--out-md report.md] [--out-json report.json]
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from math import ceil, comb, sqrt
from pathlib import Path
import re

Z95 = 1.959963984540054
PRECISION_FLOOR = 0.90
PRIMARY = 'agere'
ORDER = ('agere', 'llm_raw', 'llm_tuva', 'llm_tuva_frontier')


def wilson(k, n, z=Z95):
    """Wilson score interval for k successes in n trials: (point, low, high), or None when n == 0."""
    if not n:
        return None
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (p, max(0.0, center - half), min(1.0, center + half))


def mcnemar_exact(b, c):
    """Two-sided exact McNemar p-value from the discordant counts b (only A right) and c (only B right)."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def quantile(values, q):
    """Nearest-rank quantile (no interpolation)."""
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, max(0, ceil(q * len(v)) - 1))]


def median(values):
    if not values:
        return None
    v, n = sorted(values), len(values)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _read_jsonl(path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()] if path.exists() else []


def load_round(round_dir):
    """{(contestant, mode): {'attempts': {k: {id: (correct, answered)}}, 'rows': [...], 'stamps': [...]}}"""
    runs = {}
    for per in sorted(Path(round_dir).glob('*/*/attempt-*/per_question.json')):
        attempt_dir = per.parent
        mode, contestant = attempt_dir.parent.name, attempt_dir.parent.parent.name
        k = int(re.sub(r'\D', '', attempt_dir.name.split('-', 1)[1]))
        entry = runs.setdefault((contestant, mode), {'attempts': {}, 'rows': [], 'stamps': []})
        data = json.loads(per.read_text())
        entry['attempts'][k] = {x['id']: (bool(x['correct']), bool(x['answered'])) for x in data.get('per_question', [])}
        entry['rows'] += _read_jsonl(attempt_dir / 'rows.jsonl')
        stamp = attempt_dir / 'stamp.json'
        if stamp.exists():
            entry['stamps'].append(json.loads(stamp.read_text()))
    return runs


def _fmt_ci(ci, pct=True):
    if ci is None:
        return 'n/a'
    p, lo, hi = ci
    return f'{100 * p:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}]' if pct else f'{p:.3f} [{lo:.3f}, {hi:.3f}]'


def _ci_json(ci):
    return None if ci is None else {'estimate': round(ci[0], 4), 'low': round(ci[1], 4), 'high': round(ci[2], 4)}


def group_metrics(ids, attempts):
    """Accuracy / precision over the questions `ids` across all attempts."""
    ks = sorted(attempts)
    n_attempts = len(ks)
    pairs = [attempts[k].get(i, (False, False)) for k in ks for i in ids]
    correct = sum(c for c, _ in pairs)
    answered = sum(a for _, a in pairs)
    correct_answered = sum(c and a for c, a in pairs)
    per_q = {i: sum(attempts[k].get(i, (False, False))[0] for k in ks) for i in ids}
    majority = sum(1 for v in per_q.values() if v * 2 > n_attempts)
    pass_all = sum(1 for v in per_q.values() if v == n_attempts)
    return {'questions': len(ids), 'attempts': n_attempts, 'pairs': len(pairs),
            'correct': correct, 'answered': answered, 'correct_answered': correct_answered,
            'accuracy': wilson(correct, len(pairs)), 'precision_on_answered': wilson(correct_answered, answered),
            'answered_rate': wilson(answered, len(pairs)),
            'majority_accuracy': wilson(majority, len(ids)), 'pass_all_accuracy': wilson(pass_all, len(ids)),
            'per_attempt_accuracy': {k: (sum(attempts[k].get(i, (False, False))[0] for i in ids) / len(ids) if ids else None)
                                     for k in ks}}


def majority_correct(attempts):
    ks = sorted(attempts)
    ids = set().union(*(attempts[k] for k in ks)) if ks else set()
    return {i: sum(attempts[k].get(i, (False, False))[0] for k in ks) * 2 > len(ks) for i in ids}


def economics(rows, precision):
    """Fail closed: any row with an unknown cost leaves cost per question unknown; replayed / no-new-call rows are
    counted apart (bench/manifest.py cost_summary), never as $0 of work."""
    from bench.manifest import cost_summary
    summary = cost_summary(rows)
    lat = [r['latency_s'] for r in rows if r.get('latency_s') is not None]
    out = {'rows': len(rows), 'latency_median_s': median(lat), 'latency_p90_s': quantile(lat, .9),
           'cost_complete': summary['complete'] and bool(rows), 'known_cost_usd': summary['known_paid_usd'],
           'replayed_rows': summary['replayed_rows'], 'unknown_cost_rows': summary['unknown_cost_rows']}
    if not out['cost_complete']:
        out.update(cost_per_question_usd=None, correct_per_dollar=None,
                   note=f"cost unknown for {summary['unknown_cost_rows']} of {len(rows)} rows")
        return out
    total = summary['total_usd']
    out['cost_per_question_usd'] = total / summary['paid_rows'] if summary['paid_rows'] else 0.0  # fresh (paid) rows only
    if precision is None or precision[0] < PRECISION_FLOOR:
        out['correct_per_dollar'] = 'below floor'
    elif total == 0:
        out['correct_per_dollar'] = 'no paid spend'
    else:
        out['correct_per_dollar'] = None  # filled by caller with the pooled correct count
    return out


def build(round_dir):
    runs = load_round(round_dir)
    contestants = sorted({c for c, _ in runs}, key=lambda c: (ORDER.index(c) if c in ORDER else len(ORDER), c))
    modes = sorted({m for _, m in runs})
    report = {'round': str(round_dir), 'contestants': {}, 'results': {}, 'mcnemar': {}, 'abstention': {},
              'economics': {}, 'checks': []}
    round_file = Path(round_dir) / 'round.json'
    if round_file.exists():
        r = json.loads(round_file.read_text())
        report['frozen'] = {'frozen_sha256': r['frozen_sha256'], **r['frozen'],
                            'warehouses': {p: w['sha256'] for p, w in r['warehouses'].items()}}
    budgets = set()
    for (c, m), run in runs.items():
        info = report['contestants'].setdefault(c, {'models': set(), 'budgets': set(), 'prompt_hashes': set(),
                                                    'config_hashes': set()})
        for row in run['rows']:
            info['models'].add(row.get('model'))
            info['budgets'].add(json.dumps(row.get('budget'), sort_keys=True))
            info['prompt_hashes'].update(row.get('system_prompt_sha256') or [])
            info['config_hashes'].add(row.get('contestant_config_sha256'))
        budgets |= info['budgets']
    for info in report['contestants'].values():
        for k in list(info):
            info[k] = sorted(x for x in info[k] if x is not None)
        info['budgets'] = [json.loads(b) for b in info['budgets']]
    report['checks'].append({'check': 'identical budget across contestants', 'ok': len(budgets) <= 1,
                             'budgets': [json.loads(b) for b in sorted(budgets)]})

    for (c, m), run in sorted(runs.items()):
        meta = {}
        for row in run['rows']:
            meta.setdefault(row['id'], row)
        ids = sorted(set().union(*run['attempts'].values()))
        domain = lambda i: (meta.get(i) or {}).get('domain') or 'unknown'
        groups = {'all': ids}
        for i in ids:
            groups.setdefault(f'domain:{domain(i)}', []).append(i)
        res = {g: group_metrics(v, run['attempts']) for g, v in sorted(groups.items())}
        report['results'].setdefault(c, {})[m] = res

        rec = defaultdict(list)
        for i in ids:
            rec[(meta.get(i) or {}).get('recoverability') or 'unknown'].append(i)
        ab = {}
        for kind, kids in sorted(rec.items()):
            g = group_metrics(kids, run['attempts'])
            if kind == 'unsupported':  # the scorer counts unsupported/abstain as correct here
                ab[kind] = {'questions': len(kids), 'correctly_declined': wilson(g['correct'], g['pairs'])}
            else:
                ab[kind] = {'questions': len(kids), 'abstained': wilson(g['pairs'] - g['answered'], g['pairs'])}
        report['abstention'].setdefault(c, {})[m] = ab

        econ = economics(run['rows'], res['all']['precision_on_answered'])
        if econ.get('correct_per_dollar', 'x') is None and econ['cost_complete']:
            econ['correct_per_dollar'] = res['all']['correct'] / econ['known_cost_usd']
        report['economics'].setdefault(c, {})[m] = econ

    for m in modes:
        if (PRIMARY, m) not in runs:
            continue
        a = majority_correct(runs[(PRIMARY, m)]['attempts'])
        for c in contestants:
            if c == PRIMARY or (c, m) not in runs:
                continue
            b_ = majority_correct(runs[(c, m)]['attempts'])
            shared = sorted(set(a) & set(b_))
            only_a = sum(1 for i in shared if a[i] and not b_[i])
            only_b = sum(1 for i in shared if b_[i] and not a[i])
            report['mcnemar'].setdefault(m, {})[c] = {
                'paired_questions': len(shared), 'agere_only_correct': only_a, 'baseline_only_correct': only_b,
                'both_correct': sum(1 for i in shared if a[i] and b_[i]),
                'p_value_exact': mcnemar_exact(only_a, only_b), 'basis': 'majority-of-attempts correctness'}
    return report


def _jsonable(report):
    def conv(x):
        if isinstance(x, tuple) and len(x) == 3 and all(isinstance(v, float) for v in x):
            return _ci_json(x)
        if isinstance(x, dict):
            return {str(k): conv(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)):
            return [conv(v) for v in x]
        return x
    return conv(report)


def _money(x):
    if x is None:
        return 'unknown'
    if isinstance(x, str):
        return x
    return f'${x:.4f}'


def render(report):
    L = [f'# Contest report: {Path(report["round"]).name}', '']
    fz = report.get('frozen')
    if fz:
        L += ['## Frozen inputs', '', f'- Round hash: `{fz["frozen_sha256"][:16]}`',
              f'- Questions: `{fz["questions_file"]["path"]}` sha256 `{fz["questions_file"]["sha256"][:16]}`',
              '- Scorer: ' + ', '.join(f'{Path(k).name} `{v[:16]}`' for k, v in fz['scorer'].items()),
              f'- Question-set verification: {fz["verification"]["status"]}']
        L += [f'- Warehouse `{p}`: `{h[:16]}`' for p, h in fz.get('warehouses', {}).items()] + ['']
    L += ['## Contestants', '', '| Contestant | Model | Budget | Prompt hash(es) |', '|---|---|---|---|']
    for c, info in report['contestants'].items():
        b = info['budgets'][0] if len(info['budgets']) == 1 else None
        budget = (f'{b["max_sql_calls"]} SQL, {b["sql_timeout_s"]:g}s/SQL, {b["wall_clock_s"]:g}s wall, '
                  f'{b["max_tokens"]} tok') if b else f'{len(info["budgets"])} budgets'
        hashes = ', '.join(f'`{h[:12]}`' for h in info['prompt_hashes']) or 'n/a (service)'
        L.append(f'| {c} | {", ".join(info["models"]) or "n/a"} | {budget} | {hashes} |')
    for chk in report['checks']:
        L.append('')
        L.append(f'Check, {chk["check"]}: {"OK" if chk["ok"] else "FAILED"}')
    for m in sorted({m for v in report['results'].values() for m in v}):
        L += ['', f'## Mode: {m}', '',
              '| Contestant | n | Accuracy (pooled) | Precision on answered | Majority-of-k | pass^k | Answered |',
              '|---|---|---|---|---|---|---|']
        for c, by_mode in report['results'].items():
            r = by_mode.get(m, {}).get('all')
            if r:
                L.append(f'| {c} | {r["questions"]}x{r["attempts"]} | {_fmt_ci(r["accuracy"])} | '
                         f'{_fmt_ci(r["precision_on_answered"])} | {_fmt_ci(r["majority_accuracy"])} | '
                         f'{_fmt_ci(r["pass_all_accuracy"])} | {_fmt_ci(r["answered_rate"])} |')
        L += ['', '### By domain (pooled accuracy / precision on answered)', '',
              '| Domain | ' + ' | '.join(report['results']) + ' |', '|---|' + '---|' * len(report['results'])]
        domains = sorted({g for by_mode in report['results'].values() for g in by_mode.get(m, {}) if g != 'all'})
        for d in domains:
            cells = []
            for c, by_mode in report['results'].items():
                r = by_mode.get(m, {}).get(d)
                cells.append(f'{_fmt_ci(r["accuracy"])} / {_fmt_ci(r["precision_on_answered"])}' if r else '')
            L.append(f'| {d.split(":", 1)[1]} | ' + ' | '.join(cells) + ' |')
        mc = report['mcnemar'].get(m)
        if mc:
            L += ['', f'### Paired significance: {PRIMARY} vs each baseline (McNemar exact, majority-of-k)', '',
                  '| Baseline | Paired n | Only agere right | Only baseline right | Both right | p |', '|---|---|---|---|---|---|']
            for c, t in mc.items():
                L.append(f'| {c} | {t["paired_questions"]} | {t["agere_only_correct"]} | {t["baseline_only_correct"]} | '
                         f'{t["both_correct"]} | {t["p_value_exact"]:.4g} |')
        L += ['', '### Abstention', '', '| Contestant | Unsupported: correctly declined | Answerable: abstained |',
              '|---|---|---|']
        for c, by_mode in report['abstention'].items():
            ab = by_mode.get(m)
            if ab:
                uns = ab.get('unsupported')
                other = [f'{k} {_fmt_ci(v["abstained"])}' for k, v in ab.items() if k != 'unsupported']
                L.append(f'| {c} | {_fmt_ci(uns["correctly_declined"]) + " (n=" + str(uns["questions"]) + ")" if uns else "n/a"}'
                         f' | {"; ".join(other) or "n/a"} |')
        L += ['', f'### Cost and latency (correct per dollar only at precision on answered >= {PRECISION_FLOOR:.0%})', '',
              '| Contestant | Cost / question | Correct per $ | Latency median | Latency p90 |', '|---|---|---|---|---|']
        for c, by_mode in report['economics'].items():
            e = by_mode.get(m)
            if e:
                cpd = e['correct_per_dollar']
                cpd = f'{cpd:.1f}' if isinstance(cpd, (int, float)) else (cpd or e.get('note', 'unknown'))
                lat = lambda x: 'n/a' if x is None else f'{x:.1f}s'
                L.append(f'| {c} | {_money(e["cost_per_question_usd"])} | {cpd} | {lat(e["latency_median_s"])} | '
                         f'{lat(e["latency_p90_s"])} |')
    L += ['', 'Accuracy pools every question x attempt; majority-of-k and pass^k count questions. Intervals are Wilson 95%.',
          'Correctness is the sealed scorer\'s per-question verdict on the frozen key.']
    return '\n'.join(L) + '\n'


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('round', type=Path)
    p.add_argument('--out-md', type=Path)
    p.add_argument('--out-json', type=Path)
    a = p.parse_args(argv)
    report = build(a.round)
    md, js = a.out_md or a.round / 'report.md', a.out_json or a.round / 'report.json'
    md.write_text(render(report))
    js.write_text(json.dumps(_jsonable(report), indent=1, default=str))
    print(md)
    print(js)


if __name__ == '__main__':
    main()
