"""Self-growing compiler: learning-loop gap clusters -> draft declarative rules -> acceptance gate -> only passing rules land.

Input: a learning-loop output directory (tools/learning_loop writes grammar-proposals.json there: decline clusters that the
operator router or the verified SQL ladder resolved consistently, with example questions and the resolved operator
parameters or verified SQL). Each proposal is drafted into a rule by grammar induction over its examples:

  1. normalise each example with the compiler's own normaliser and abstract the spans a slot type recognises
     (period, condition, measurement, drug class, number) into {slots};
  2. examples sharing one skeleton (at least --min-support of them) become one pattern; a trailing period may be absent;
  3. a slot is kept only when the plan uses it: operator drafts map a period onto the operator's period parameter; SQL
     drafts replace the slot's literal rendering in the verified SQL and must re-render every example's SQL exactly.
     Anything else stays literal text, so a draft never silently ignores a word;
  4. the examples become positives; perturbations that add unexplained meaning (", by practice", " and why?") negatives.

Drafts then go through tools/compiler_growth/accept.py's gate. Only accepted drafts are written (with --write) to
config/agere/compiler_rules/learned_rules.json plus their receipts; everything else stays in tmp/compiler-growth/proposals/.

Hosted drafting hook: register_drafter(fn) lets a hosted model draft rules for clusters the templates cannot abstract. It
is OFF by default and also requires AGERE_COMPILER_RULE_DRAFTER=hosted; this module never calls a model itself. Drafts from
a hosted drafter go through the same gate and are never written unless they pass.

Usage: .venv/bin/python tools/compiler_growth/propose.py artifacts/learning-loop/<stack> [--write] [--min-support 2]
"""
import argparse, hashlib, json, os, re, sys, time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'tools'))

from clinical_core.agere.clinical import capability_registry, compiler_rules  # noqa: E402

VERSION = 'compiler-growth-propose/1'
LEARNED_FILE = compiler_rules.RULES_DIR / 'learned_rules.json'
OUT = ROOT / 'tmp' / 'compiler-growth' / 'proposals'
AS_OF = '2029-08-15'
PERIOD_OPERATORS = {'monitor', 'claims_review', 'market', 'network', 'contract_strategy', 'pathway', 'profile'}
ABSTRACT = [('period', 'period'), ('drug', 'drug'), ('condition', 'condition'), ('measurement', 'measurement'), ('n', 'number')]
_DRAFTER = None


# ---------------------------------------------------------------- hosted drafting hook (off by default)
def register_drafter(fn):
    """fn(proposal) -> [rule] drafted by a hosted model. Used only when AGERE_COMPILER_RULE_DRAFTER=hosted."""
    global _DRAFTER
    _DRAFTER = fn


def hosted_drafts(proposal):
    if os.environ.get('AGERE_COMPILER_RULE_DRAFTER') != 'hosted' or _DRAFTER is None:
        return []
    return list(_DRAFTER(proposal) or [])


# ---------------------------------------------------------------- grammar induction
def abstract(question, as_of=AS_OF):
    """-> (skeleton regex text, {slot: matched text}) over the normalised question."""
    low = compiler_rules.normalize(question, as_of)
    spans = []
    for name, kind in ABSTRACT:
        rx = re.compile(r'(?<![a-z0-9])(?:' + compiler_rules.slot_regex(kind, {}, None) + r')(?![a-z0-9])')
        for m in rx.finditer(low):
            if any(m.start() < e and s < m.end() for s, e, _, _ in spans): continue
            if compiler_rules.resolve_slot(kind, m.group(0), {}, None, as_of) is None: continue
            if any(n == name for _, _, n, _ in spans): break  # one occurrence per slot name; a second stays literal
            spans.append((m.start(), m.end(), name, m.group(0)))
            break
    spans.sort()
    out, pos, values = [], 0, {}
    for s, e, name, text in spans:
        out.append(re.escape(low[pos:s]).replace('\\ ', ' ')); out.append('{' + name + '}'); values[name] = text; pos = e
    out.append(re.escape(low[pos:]).replace('\\ ', ' '))
    return ''.join(out), values


def _literalize(skeleton, values, keep):
    """Put back the literal text of slots the plan does not use."""
    for name, text in values.items():
        if name not in keep:
            skeleton = skeleton.replace('{' + name + '}', re.escape(text).replace('\\ ', ' '))
    return skeleton


def _sql_template(examples, keep):
    """Verified SQL with slot renderings replaced by placeholders; must reproduce every example's SQL. -> (template, kept)."""
    first_sql, first_vals = examples[0]['sql'], examples[0]['values']
    template, used = first_sql, set()
    for name in sorted(keep):
        text = first_vals.get(name)
        if text is None: continue
        kind = dict(ABSTRACT)[name]
        v = compiler_rules.resolve_slot(kind, text, {}, None, AS_OF)
        renderings = {'period': [('{period.start}', v.get('start')), ('{period.end}', v.get('end')), ('{period.year}', (v.get('start') or '')[:4])],
                      'condition': [('{condition.in}', v.get('in'))] + [('{condition.concepts}', c) for c in v.get('concepts', [])[:1]],
                      'measurement': [('{measurement.concept}', v.get('concept'))],
                      'drug': [('{drug.in}', v.get('in'))], 'n': [('{n.value}', str(v.get('value')))]}.get(name, [])
        for placeholder, literal in renderings:
            if literal and str(literal) in template and placeholder != '{period.year}':
                template = template.replace(str(literal), placeholder); used.add(name)
        if name == 'period' and 'period' not in used and v and v.get('start', '')[:4] and f"'{v['start'][:4]}'" in template:
            template = template.replace(f"'{v['start'][:4]}'", "substr('{period.start}', 1, 4)"); used.add(name)
    for ex in examples:  # the template must re-render each example's verified SQL
        values = {}
        for name in used:
            kind = dict(ABSTRACT)[name]
            values[name] = compiler_rules.resolve_slot(kind, ex['values'].get(name, ''), {}, None, AS_OF)
            if values[name] is None: return None, set()
        try:
            rendered = compiler_rules._fill(template, values)
        except ValueError:
            return None, set()
        if ' '.join(rendered.split()) != ' '.join(ex['sql'].split()): return None, set()
    return template, used


def _rule_id(proposal, skeleton):
    h = hashlib.sha256((proposal.get('id', '') + skeleton).encode()).hexdigest()[:8]
    target = proposal['target']
    head = target.get('operator') if target.get('kind') == 'operator' else 'sql'
    return f'learned.{head}.{h}'


def draft(proposal, min_support=2):
    """One learning-loop proposal -> [draft rule]."""
    target = proposal.get('target') or {}
    outcomes = {o['question']: o for o in proposal.get('verified_outcomes') or []}
    examples = []
    for q in proposal.get('examples') or []:
        skeleton, values = abstract(q)
        examples.append({'question': q, 'skeleton': skeleton, 'values': values, 'outcome': outcomes.get(q, {})})
    groups = defaultdict(list)
    for ex in examples:  # a trailing period may be absent: group on the skeleton without it
        groups[re.sub(r' \{period\}$', '', ex['skeleton'])].append(ex)
    drafts = []
    for key, exs in groups.items():
        if len(exs) < min_support: continue
        optional_period = any('period' not in e['values'] for e in exs) and any('period' in e['values'] for e in exs)
        names = set.intersection(*[set(e['values']) - ({'period'} if optional_period else set()) for e in exs])
        if optional_period: names |= {'period'}
        if target.get('kind') == 'operator':
            op = target['operator']
            if op not in capability_registry.PRODUCT: continue
            keep = {'period'} & names if op in PERIOD_OPERATORS else set()
            params = dict(target.get('shared_parameters') or {})
            if 'period' in keep: params['period'] = {'start': '{period.start}', 'end': '{period.end}'}
            plan = {'kind': 'operator', 'operation': op, 'parameters': params, 'purpose': f'{op} (learned from the router\'s consistent decisions)'}
            engine = op
        elif target.get('kind') == 'verified_sql_template':
            sql_examples = [{'sql': target.get('example_sql') or '', 'values': exs[0]['values']}]
            template, keep = _sql_template(sql_examples, names - ({'period'} if optional_period else set()))
            if template is None: template, keep = target.get('example_sql') or '', set()
            if not template: continue
            returns = (target.get('returns') or ['table'])[0]
            if returns not in ('count', 'value', 'patient_set', 'table'): continue
            plan = {'kind': 'sql', 'returns': returns, 'query': template, 'purpose': 'verified SQL (learned from the SQL ladder\'s consensus answers)'}
            engine = 'literal_lookup'
            if optional_period and 'period' not in keep: continue  # a period the SQL does not use would be ignored
        else:
            continue
        values0 = exs[0]['values']
        # slots the plan does not use stay literal: only examples with the same literal text there support this pattern
        exs = [e for e in exs if all(e['values'].get(n) == values0.get(n) for n in set(values0) | set(e['values']) if n not in keep)]
        if len(exs) < min_support: continue
        skeleton = _literalize(exs[0]['skeleton'], values0, keep)
        if optional_period and 'period' in keep:
            skeleton = re.sub(r' \{period\}$', '', skeleton) + ' {period?}'
        if re.search(r'\{(?!' + '|'.join(sorted(keep) or ['x^']) + r')[a-z]', skeleton): continue
        slots = {n: {'type': dict(ABSTRACT)[n], **({'default': 'open' if engine != 'literal_lookup' else 'all'} if n == 'period' and optional_period else {})}
                 for n in keep}
        pos = [{'question': e['question'], 'expect': {'operation': 'sql' if engine == 'literal_lookup' else engine}} for e in exs]
        negs = [exs[0]['question'].rstrip('?. ') + ', by practice?', exs[0]['question'].rstrip('?. ') + ' and why?']
        rule = {'id': _rule_id(proposal, skeleton), 'version': 1, 'engine': engine,
                'description': f"Learned from learning-loop proposal {proposal.get('id')} ({proposal.get('reason_family')})",
                'origin': {'proposal': proposal.get('id'), 'cluster': proposal.get('cluster'), 'drafter': 'template', 'drafted': time.strftime('%Y-%m-%d')},
                'pattern': '^' + skeleton + '$', 'slots': slots, 'plan': plan, 'positives': pos, 'negatives': negs}
        drafts.append(rule)
    return drafts


def load_proposals(directory):
    path = Path(directory) / 'grammar-proposals.json'
    if not path.exists(): return []
    return json.loads(path.read_text()).get('proposals', [])


def write_learned(rules, path=LEARNED_FILE):
    data = json.loads(path.read_text()) if path.exists() else {'schema': 'agere-compiler-rules/1', 'family': 'learned_rules',
                                                               'note': 'Rules drafted by tools/compiler_growth/propose.py from learning-loop gap clusters; '
                                                                       'each entered only through the acceptance gate.', 'rules': []}
    by_id = {r['id']: r for r in data['rules']}
    for r in rules: by_id[r['id']] = {k: v for k, v in r.items() if not k.startswith('_')}
    data['rules'] = list(by_id.values())
    path.write_text(json.dumps(data, indent=1) + '\n')


def run(directory, *, write=False, min_support=2, gate=None, log=print, learned_file=None):
    from compiler_growth import accept
    proposals = load_proposals(directory)
    drafts = []
    for p in proposals:
        drafts += draft(p, min_support=min_support) or hosted_drafts(p)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'drafts.json').write_text(json.dumps({'schema': VERSION, 'source': str(directory), 'rules': drafts}, indent=1))
    log(f'{len(proposals)} proposals -> {len(drafts)} draft rules')
    if not drafts: return []
    receipts = (gate or accept.gate)(drafts, write=write, log=log)
    passed = [d for d, r in zip(drafts, receipts) if r['status'] == 'accepted']
    if write and passed: write_learned(passed, learned_file or LEARNED_FILE)
    log(f'{len(passed)} accepted' + (f', written to {learned_file or LEARNED_FILE}' if write and passed else ' (dry run: nothing written)' if passed else ''))
    return receipts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('directory', help='learning-loop output directory (grammar-proposals.json)')
    ap.add_argument('--write', action='store_true', help='write accepted rules and receipts (makes them live)')
    ap.add_argument('--min-support', type=int, default=2)
    a = ap.parse_args(argv)
    run(a.directory, write=a.write, min_support=a.min_support)


if __name__ == '__main__':
    main()
