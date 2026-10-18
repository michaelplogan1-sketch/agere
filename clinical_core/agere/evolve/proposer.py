"""Mutation proposers for the evolution loop.

- ListProposer: a fixed list of candidate programs handed out in order (tests, replays; no model call).
- LLMProposer: Claude proposes code mutations of the best programs. The prompt carries the parent programs, their training
  scores, failure cases (the selected patients that cost the objective most and the skipped patients that would have helped
  most, described by features only) and the equity result. Calls go through the repository's ledgered sender
  (clinical/context_student.send with a gateway.BudgetLedger): every request is hashed, its validated response is cached in
  the ledger, so a re-run with the same seeds replays the same responses at no cost and gives the same archive.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

CODE_RE = re.compile(r'```(?:python|py)?\s*\n(.*?)```', re.S)

SYSTEM = ('You improve small, auditable outreach-prioritization functions for a population-health team. You write plain '
          'Python using only the math and statistics modules. The functions run in a sandbox without file, network or '
          'module access beyond math/statistics; names and attributes starting with an underscore, print, open, eval, exec, '
          'getattr, classes, with-blocks and generators are rejected. All data is synthetic.')


def extract_code(text):
    blocks = [b.strip() + '\n' for b in CODE_RE.findall(text or '') if 'def prioritize' in b]
    return blocks


class ListProposer:
    """Hands out `codes` in order, `per_call` at a time (then nothing)."""

    def __init__(self, codes, per_call=2):
        self.codes, self.per_call, self.pos = list(codes), per_call, 0
        self.calls = 0

    def propose(self, ctx):
        self.calls += 1
        out = self.codes[self.pos:self.pos + self.per_call]
        self.pos += len(out)
        return [{'code': c, 'origin': 'list'} for c in out]

    def explain(self, code, profile):
        return None

    def spend(self):
        return {'calls': 0, 'estimated_usd': 0.0}


def _fmt(x):
    if isinstance(x, float):
        return round(x, 4) if abs(x) < 100 else round(x, 1)
    if isinstance(x, dict):
        return {k: _fmt(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_fmt(v) for v in x]
    return x


def build_prompt(ctx):
    obj = ctx['objective']
    lines = [
        f"Goal: write an improved policy for the objective **{obj}** ({ctx['objective_text']}). "
        f"Generation {ctx.get('generation', 1)} of {ctx.get('generations', '?')}.",
        '',
        'Setting',
        f"- Program kind: {ctx['program_kind']}. Each routed patient can get one program variant or no outreach. Variants: "
        + json.dumps(ctx['programs']),
        f"- Capacity: {ctx['capacity_text']}. Patients are ranked by prioritize() (higher first; return None to never contact), then "
        'filled into quarterly waves in rank order. A patient in a later wave starts the program later, so gets less of the '
        'effect inside the 12-month horizon but costs the same.',
        '- Outcomes come from a Monte Carlo population simulator (do-nothing baseline rates, an outreach / enrollment / attendance '
        'funnel, cited GDMT effects on heart-failure admissions and ED visits, placeholder program costs).',
        f"- Equity rule: within every line of business and age band with >= {ctx['equity_min_group']} patients, the share "
        f"contacted must be >= {ctx['equity_floor']} x the overall share contacted, or the policy is rejected.",
        *([f"- Note for {obj}: {ctx['guidance']}"] if ctx.get('guidance') else []),
        '- choose_program(patient, programs) is optional: return a variant id or None (no outreach). Without it every patient '
        'gets the program the router chose (patient["routed_program"]).',
        '',
        'Patient features (a dict; never identifiers). Feature: meaning [observed range or values]:',
    ]
    for k, v in ctx['feature_schema'].items():
        lines.append(f"- {k}: {v} {ctx['feature_stats'].get(k.split(' ')[0], '')}")
    lines += ['', 'Current best programs for this objective, with training results:']
    for p in ctx['parents']:
        lines += [f"### {p['id']} (origin {p['origin']})", '```python', p['code'].strip(), '```',
                  'Training result: ' + json.dumps(_fmt(p['metrics'])),
                  'Equity: ' + json.dumps(_fmt(p['equity_summary'])),
                  'Contacted per quarterly wave: ' + json.dumps(p['by_wave']) + '; by program: ' + json.dumps(p['by_program'])]
        if p.get('failures'):
            lines.append('Failure cases (features of patients; "value" = expected objective contribution if contacted in that wave):')
            for f in p['failures']:
                lines.append('- ' + json.dumps(_fmt(f)))
    if ctx.get('inspiration'):
        lines += ['', 'A different niche of the archive (for ideas, other trade-offs):']
        for p in ctx['inspiration']:
            lines += [f"### {p['id']}", '```python', p['code'].strip(), '```', 'Training result: ' + json.dumps(_fmt(p['metrics']))]
    if ctx.get('recent'):
        lines += ['', 'Recent attempts for this objective that did NOT beat the best program (do not repeat them; try a different idea):']
        for r in ctx['recent']:
            lines += [f"### {r['id']}: {json.dumps(_fmt(r['result']))}", '```python', r['code'].strip()[:1500], '```']
    lines += ['', f"Write {ctx['n']} different improved programs. Each in its own ```python block, defining prioritize(p) "
              '(and optionally choose_program(p, programs) plus small helpers / constants). Use only features listed above, '
              'handle None values, keep each program under 60 lines, deterministic. Think about which patients actually '
              f"produce {obj} in the simulator, the capacity per quarter and the equity rule. After each block, one sentence "
              'saying what changed.']
    return '\n'.join(lines)


class LLMProposer:
    def __init__(self, ledger, out_dir, *, model='claude-sonnet-5-5', n=2, max_tokens=12000, temperature=None, transport=None):
        self.ledger, self.out = ledger, Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.model, self.n, self.max_tokens, self.temperature = model, n, max_tokens, temperature
        self.transport = transport
        self.receipts = []

    def _send(self, prompt, max_tokens=None, model=None):
        from ..clinical.context_student import send
        body = {'model': model or self.model, 'max_tokens': max_tokens or self.max_tokens,
                'system': SYSTEM, 'messages': [{'role': 'user', 'content': prompt}]}
        if self.temperature is not None:
            body['temperature'] = self.temperature

        def parse(raw):
            text = ''.join(c.get('text', '') for c in raw.get('content') or [] if c.get('type') == 'text')
            if not text.strip():
                raise ValueError('empty model response')
            return text
        text, receipt = send(body, 'claude', self.ledger, self.out, parse=parse, timeout=180, transport=self.transport)
        self.receipts.append(receipt)
        return text

    def propose(self, ctx):
        prompt = build_prompt({**ctx, 'n': self.n})
        text = self._send(prompt)
        return [{'code': c, 'origin': f'llm:{self.model}'} for c in extract_code(text)]

    def explain(self, code, profile, model='claude-haiku-5-5'):
        prompt = ('Explain in 2-3 plain-English sentences, for a care-management director, what this outreach policy learned: '
                  'whom it contacts first, whom it skips or defers, and which program it picks. Use the profile numbers, no '
                  'jargon, no code terms, no caveats.\n\n```python\n' + code.strip() + '\n```\n\nProfile of contacted vs not '
                  'contacted patients (training split):\n' + json.dumps(_fmt(profile)))
        return self._send(prompt, max_tokens=3000, model=model).strip()

    def spend(self):
        usd = sum((r.get('cost_estimate') or {}).get('estimated_usd') or 0.0 for r in self.receipts if not r.get('cache_hit'))
        return {'calls': sum(1 for r in self.receipts if not r.get('cache_hit')),
                'cache_hits': sum(1 for r in self.receipts if r.get('cache_hit')), 'estimated_usd': round(usd, 4)}
