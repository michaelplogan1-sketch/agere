"""The local model's planner prompt: only the schema, vocabulary and rule text this question needs, inside a token budget.

The hosted planner sends the whole static catalog (every governed measure, every concept digest, all operators, all streams)
plus the full rule text: ~48k tokens, which a 4B model's 4 GB attention cache declines. The local worker passes each prompt
through Shaper instead (AGERE_LOCAL_TYPED_PROMPT_TOKENS, default 8000, counted with the model's own tokenizer file; never
the weights). A prompt that already fits is returned untouched.

Kept, with the same JSON keys and rule text the model sees today: the question and feedback, the core grammar rules, the likely
tables (schema_prefetch) with their columns inline, the condition/drug concept names (ICD-10 codes for the conditions the
question touches), the matching governed measures and operator contracts. Left out until asked: unrelated tables,
discovery_metadata (columns are inline), concept digests, unrelated measures, operator meanings, stream lists, and the optional
rule parts (analysis, workflow, vbc, streams). Pieces are added in priority order while the budget holds; mandatory pieces are
never dropped (an over-budget prompt is flagged and the worker declines it with its reason, never shortens it further).

Repairs send only the missing fragment: {"need_tables"|"need_operations"|"need_streams"|"need_concepts"|"need_rules"} add those
pieces to the next prompt (Shaper.need), and a reply that uses a grammar part the prompt left out ("analysis", "workflow")
adds exactly that rule part (Shaper.observe). The full catalog is never resent.
"""
import json
import os
import re

DEFAULT_TOKENS = 8000
TEMPLATE_TOKENS = 48  # chat-template wrapper around system + user
ADVERT_TOKENS = 110  # the "request more" paragraph appended to a trimmed planner prompt
MAX_CONTRACT_TOKENS = 900  # larger operator contracts are sent on request, not up front
PREFETCH_TABLES = 3  # likely tables sent up front (the hosted path sends 5)
TRIGGERS = {  # optional rule parts a question needs up front; any can be requested later
    'workflow': r'dashboard|brief|report|monitor|watch|track|simulat|outreach|deliverable|worklist|work list|queue|alert|program',
    'vbc': r'shared.?savings|downside|star rating|stars|hedis|medicare advantage|part d|\bcms\b|\baco\b|risk corridor|loss rate|contract|\bmlr\b',
    'streams': r'\braw\b|stream|payload|feed|received field',
}
ALWAYS = ('core', 'concepts', 'engines', 'joins', 'discovery', 'program')
OPTIONAL = ('analysis', 'workflow', 'vbc', 'streams')
KNOWLEDGE = r'dose|dosing|target|guideline|gdmt|maximum|titrat'
CODE = re.compile(r'\b([A-TV-Z]\d{2})(?:\.\w+)?\b')
STOP = {'how', 'many', 'patients', 'patient', 'with', 'the', 'and', 'for', 'have', 'has', 'definition', 'persons', 'documented',
        'code', 'list', 'type', 'more', 'worse', 'than', 'that', 'what', 'who', 'are', 'was', 'were', 'count', 'number'}


def budget():
    try:
        return max(1000, int(os.environ.get('AGERE_LOCAL_TYPED_PROMPT_TOKENS', DEFAULT_TOKENS)))
    except ValueError:
        return DEFAULT_TOKENS


def counter(model_dir=None):
    """Tokens in a text, from the base model's tokenizer.json (tokenizer only, no weights); else a conservative chars/3 estimate."""
    try:
        from pathlib import Path
        from tokenizers import Tokenizer
        if model_dir is None:
            from .local_request_worker import _base
            model_dir = _base()
        tok = Tokenizer.from_file(str(Path(model_dir) / 'tokenizer.json'))
        return lambda text: len(tok.encode(text, add_special_tokens=False).ids)
    except Exception:  # noqa: BLE001 - no tokenizer file/library: overcount rather than undercount
        return lambda text: -(-len(text) // 3)


def _words(text):
    from .request_discovery import tokens
    return tokens(text) - STOP


def _dump(value):
    return json.dumps(value, ensure_ascii=False, default=str, separators=(',', ':'))


class Shaper:
    """One per interpretation: shaper(system, user) -> (system, user, receipt). `wanted` = tables the loop asked for."""

    def __init__(self, question, tokens=None, count=None):
        self.question, self.limit = question, tokens or budget()
        self.count = count or counter()
        self.wanted, self.asked, self.rules, self.concepts = [], set(), set(), set()  # asked: names the model requested; never dropped

    # -- repair: send only the fragment the model asked for -------------------------------------------------
    def need(self, rules=(), concepts=()):
        if any(r not in (*ALWAYS, *OPTIONAL) for r in rules): raise ValueError('Unknown rule part in need_rules')
        from .request_vocabulary import concept_definitions
        defs = concept_definitions()
        if any(c not in defs['condition'] and c not in defs['drug_fill'] for c in concepts): raise ValueError('Unknown concept in need_concepts')
        self.rules |= set(rules); self.concepts |= set(concepts)

    def observe(self, reply):
        """A reply that uses a grammar part the prompt left out: add exactly that part. -> True when something was added."""
        text = reply if isinstance(reply, str) else _dump(reply)
        added = {part for part, key in (('analysis', 'analysis'), ('workflow', 'workflow'), ('streams', 'stream'))
                 if re.search(r'"%s"\s*:' % key, text) and part not in self.rules}
        self.rules |= added
        return bool(added)

    # -- the shaped prompt ------------------------------------------------------------------------------------
    def __call__(self, system, user):
        from . import shared_planner as SP
        full = self.count(system) + self.count(user) + TEMPLATE_TOKENS
        fits = {'tokens': full, 'budget': self.limit, 'trimmed': False}
        try:
            data = json.loads(user)
        except ValueError:
            return system, user, {**fits, 'over_budget': full > self.limit}
        if not isinstance(data, dict) or not isinstance(data.get('catalog'), dict):
            return system, user, {**fits, 'over_budget': full > self.limit}
        if full <= self.limit and system != SP.SYSTEM: return system, user, fits
        if full <= self.limit and not (self.rules or self.concepts): return system, user, fits
        cat = {**data.pop('catalog_static', {}), **data.pop('catalog')}
        data.pop('cache_boundary', None)
        planner = system == SP.SYSTEM
        rest = _dump(data)
        pieces = self._pieces(cat, ' '.join(_dump(v) for v in data.values()) + ' ' + self.question, planner)
        spent = self.count(rest) + TEMPLATE_TOKENS + (0 if planner else self.count(system)) + (ADVERT_TOKENS if planner else 0)
        chosen = []
        for p in pieces:
            if not p['mandatory'] and spent + p['cost'] > self.limit and p.get('alt') and spent + p['alt']['cost'] <= self.limit:
                p = p['alt']  # a table without its prose meaning (columns and observed values stay)
            if p['mandatory'] or spent + p['cost'] <= self.limit:
                chosen.append(p); spent += p['cost']
        while True:
            names = {p['name'] for p in chosen if p['kind'] == 'rule'}
            system_text = self._system(system, names, planner)
            body = self._render(chosen)
            user_text = _dump({'catalog': body, **data})
            total = self.count(system_text) + self.count(user_text) + TEMPLATE_TOKENS
            if total <= self.limit: break
            full = [p for p in chosen if p.get('alt')]  # shorten the tables (no prose) before dropping anything
            if full: chosen[chosen.index(full[-1])] = full[-1]['alt']; continue
            droppable = [p for p in chosen if not p['mandatory']]
            if not droppable: break
            chosen.remove(droppable[-1])
        receipt = {'tokens': total, 'budget': self.limit, 'trimmed': True, 'original_tokens': full,
                   'tables': list(body.get('selected_schemas', {})), 'rules': sorted(names),
                   'omitted_rules': sorted(set(OPTIONAL) - names) if planner else []}
        if total > self.limit: receipt['over_budget'] = True
        return system_text, user_text, receipt

    def _system(self, system, names, planner):
        if not planner: return system
        from .shared_planner import SYSTEM_PARTS
        text = ''.join(t for n, t in SYSTEM_PARTS if n in names)
        return text + ('\nThis prompt carries only the tables, rules and vocabulary this question is likely to need. Request more in ONE '
                       'JSON object, naming only what is missing: {"need_tables":[..],"need_operations":[..],"need_streams":[..],'
                       '"need_concepts":["registered concept name"],"need_rules":' + _dump([n for n in OPTIONAL if n not in names]) + '}.')

    @staticmethod
    def _render(chosen):
        out = {}
        for p in chosen:
            if p['kind'] != 'catalog': continue
            if p['sub'] is None: out[p['key']] = p['value']
            else: out.setdefault(p['key'], {})[p['sub']] = p['value']
        return out

    def _piece(self, key, value, sub=None, *, mandatory=False, alt=None):
        piece = {'kind': 'catalog', 'key': key, 'sub': sub, 'value': value, 'mandatory': mandatory, 'name': None,
                 'cost': self.count(_dump({sub: value} if sub is not None else value)) + 3}
        if alt is not None: piece['alt'] = self._piece(key, alt, sub, mandatory=mandatory)
        return piece

    def _rule(self, name, *, mandatory=False):
        from .shared_planner import SYSTEM_PARTS
        text = ''.join(t for n, t in SYSTEM_PARTS if n == name)
        return {'kind': 'rule', 'key': None, 'sub': None, 'value': None, 'mandatory': mandatory, 'name': name, 'cost': self.count(text)}

    def _pieces(self, cat, rest_text, planner):
        """Priority-ordered pieces; each is added while the budget holds, mandatory ones always."""
        q = self.question
        qwords = _words(q + ' ' + ' '.join(self.concepts))
        out = []
        add = out.append
        if planner:
            for name in ALWAYS: add(self._rule(name, mandatory=True))
            for name in OPTIONAL:
                if name in self.rules: add(self._rule(name, mandatory=True))
        for key in ('as_of', 'more_schema_available', 'condition_evidence_policy', 'all_received_tables'):
            if key in cat: add(self._piece(key, cat[key], mandatory=True))
        for key in ('healthcare_source_context', 'vbc_program_context', 'resolved_definitions'):
            if cat.get(key): add(self._piece(key, cat[key], mandatory=True))
        # tables: patient, what the loop asked for, then those the question/reply names; unrelated ones are never filled in
        schemas, metadata = cat.get('selected_schemas', {}), (cat.get('discovery_metadata') or {}).get('relations', {})
        rest_words = _words(rest_text)
        named = [t for t in schemas if _words(t) and _words(t) <= rest_words]
        have = [t for t in dict.fromkeys(['patient', *named, *self.wanted]) if t in schemas]  # a table the question names outranks a prefetch guess

        def table(t):
            s = dict(schemas[t])
            if 'columns_in' in s: s.pop('columns_in'); s['columns'] = list(metadata.get(t, {}).get('columns', {}))
            if 'meaning_in' in s: s.pop('meaning_in'); s['meaning'] = metadata.get(t, {}).get('meaning', '')
            if not s.get('observed_values_sample'): s.pop('observed_values_sample', None)
            return s
        def brief(t):
            s = table(t); s.pop('meaning', None)
            return s
        for i, t in enumerate(have[:4]): add(self._piece('selected_schemas', table(t), t, mandatory=i == 0 or t in self.asked, alt=brief(t)))
        add(self._piece('named_concepts', self._concepts(cat.get('named_concepts', {}), qwords, q), mandatory=True))
        ops, contracts = cat.get('analytical_operators', {}), cat.get('selected_operator_contracts', {})
        add(self._piece('analytical_operators', sorted(ops)))  # the model can only request an engine it can name
        # an engine's full parameter schema is costly: two matching ones up front, any other on request (need_operations)
        for op in dict.fromkeys([*[o for o in contracts if o in self.asked], *list(contracts)[:2]]):
            c = contracts[op]
            piece = self._piece('selected_operator_contracts', c, op, mandatory=op in self.asked)
            if piece['cost'] <= MAX_CONTRACT_TOKENS or op in self.asked: add(piece)
        measures = cat.get('governed_measures', {})
        for m in [m for m, v in measures.items() if _words(m + ' ' + str(v.get('meaning', ''))) & qwords][:6]:
            add(self._piece('governed_measures', measures[m], m))
        if planner:
            for name in OPTIONAL:
                if name in self.rules: continue
                if name != 'analysis' and re.search(TRIGGERS.get(name, '$^'), q, re.I): add(self._rule(name))
            if 'analysis' not in self.rules: add(self._rule('analysis'))
        for t in have[4:]: add(self._piece('selected_schemas', table(t), t, mandatory=t in self.asked, alt=brief(t)))
        streams, all_streams = cat.get('selected_stream_fields', {}), cat.get('all_received_streams', [])
        raw = bool(re.search(TRIGGERS['streams'], q, re.I)) or 'streams' in self.rules
        near = all_streams if raw else [s for s in all_streams if _words(s) & qwords][:12]
        if near: add(self._piece('all_received_streams', near))
        for s, fields in streams.items():
            if raw or s in self.asked or _words(s) & qwords: add(self._piece('selected_stream_fields', fields, s, mandatory=s in self.asked))
        coverage = {k: v for k, v in cat.get('source_coverage', {}).items() if set(have) & set(v.get('relations', []))}
        if coverage: add(self._piece('source_coverage', coverage))
        if re.search(KNOWLEDGE, q, re.I) and cat.get('governed_knowledge'): add(self._piece('governed_knowledge', cat['governed_knowledge']))
        return out

    def _concepts(self, named, qwords, question):
        """Concept names the compiler accepts (no digests); label + ICD-10 codes for the conditions the question touches."""
        from . import concepts as C
        codes = {m.group(0).upper() for m in CODE.finditer(question)}
        detail = {}
        for name in named.get('condition', {}):
            spec = C.CONDITIONS.get(name, {})
            icd = spec.get('icd10', [])
            if (name in self.concepts or (_words(name) <= qwords) or len(_words(spec.get('label', '')) & qwords) >= 2
                    or any(code.startswith(c) or c.startswith(code[:3]) for c in icd for code in codes)):
                detail[name] = {'label': spec.get('label', ''), 'icd10': icd}
        return {'version': 1, 'condition': sorted(named.get('condition', {})), 'condition_codes': detail,
                'drug_fill': sorted(named.get('drug_fill', {})), 'encounter': sorted(named.get('encounter', {}))}
