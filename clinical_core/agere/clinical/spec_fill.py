"""Fill a question spec three ways in parallel and choose one (docs/agere/QUESTION_SPEC.md, "How a spec is filled").

    fill(stack, catalog, question, population=None, ledger=None, ...) -> {'winner', 'plan', 'contract', 'receipt', ...}
    repair(...)                                                        -> one Haiku repair of a spec System One refused part by part

A spec is the typed request (request_plan) plus the note predicate and subject. LLMs write specs, never SQL. Three fillers:

  grammar    request_language.parse: free, instant, deterministic. Run first (it takes milliseconds); when it fills a valid spec the paid
             fillers never start. Its failure message is the partial parse the System One filler works from.
  s1_slots   System One slot filling: the open spans of the grammar's partial parse become closed typed choices. A clinical term the
             grammar did not know -> the concept library shortlist, one typed choice (terminology.map_label). A trailing clause that is a
             note criterion (grammar prefix + "and had metformin stopped because of side effects") -> frame, status, combine and detail
             chosen from the closed lists, the concept from the library, the window from the decision context.
  haiku      ONE Haiku call that writes the typed request JSON (shared_planner's rules, catalog shaping, the decision-context block and the
             note frame catalog / subject rules), one bounded repair from validator feedback; Sonnet is the escalation rung only (model_policy):
             it runs after Haiku's spec failed validation twice AND every other Haiku tier of the question has failed. Never Opus.

A spec VALIDATES when request_plan.compile_request succeeds, the literal check finds no dropped requirement and decision_context
passes (living patients, latest labs, the one condition-evidence rule, all-source events, the question's windows). Choice:
the first valid; if several validate within the grace window and DISAGREE in meaning (different normalized specs and different results) the
grammar wins, else the spec System One verifies better (spec_verify), else both readings are reported. Receipts: which filler won,
seconds, calls, cost, and what each filler did.
"""
import contextvars
import copy
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

from . import request_plan, request_language, spec_readback
from .question_input import QuestionNotUnderstood

VERSION = 'spec-fill/1'
FILLERS = ('grammar', 's1_slots', 'haiku')
from .. import model_policy
HAIKU, SONNET = model_policy.ladder('writer')
SPEC_TOKENS = 6000


PROVIDERS = {'decide': None, 'models': None}  # test hooks: fake System One / Claude callables used when a caller passes none


class SpecInvalid(ValueError):
    """The spec does not validate (compile, literal check or decision context)."""
    held = None  # (plan, contract) of a spec that validated except for its own unresolved requirements


class SpecNotFilled(QuestionNotUnderstood):
    """No filler produced a spec that validates (or two valid readings disagree): .receipt says what each did; .readings the readings."""
    receipt = None
    readings = None
    held = None


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def enabled_fillers():
    raw = os.environ.get('AGERE_SPEC_FILLERS', '').strip()
    names = tuple(x.strip() for x in raw.split(',') if x.strip()) if raw else FILLERS
    return tuple(n for n in FILLERS if n in names)


# -- validation ------------------------------------------------------------------------------------------------------------------------
def canon(value):
    """The meaning of a plan without its wording: quotes and output names dropped, commutative arguments sorted."""
    if isinstance(value, list):
        items = [canon(v) for v in value]
        return items
    if isinstance(value, dict):
        out = {k: canon(v) for k, v in value.items() if k not in ('quote', 'alias', 'reason')}
        if out.get('op') in ('and', 'or'): out['args'] = sorted(out['args'], key=lambda x: json.dumps(x, sort_keys=True, default=str))
        return out
    return value


def same_spec(a, b):
    return json.dumps(canon(a), sort_keys=True, default=str) == json.dumps(canon(b), sort_keys=True, default=str)


def normalize_windows(plan, question, as_of):
    """A note predicate's rolling window is the decision context's 12 months ending at the as-of date, whatever dates a filler wrote."""
    from . import decision_context as DC
    if not as_of: return plan

    def visit(node):
        if isinstance(node, list):
            for x in node: visit(x)
        elif isinstance(node, dict):
            if node.get('op') == 'note':
                window = node.get('window')
                quote = ' '.join(window['quote']) if isinstance(window, dict) and isinstance(window.get('quote'), list) else (window or {}).get('quote') if isinstance(window, dict) else None
                phrase = quote if quote and DC.ROLLING_YEAR.search(quote) else None
                if phrase:
                    start, end = DC.rolling_year(as_of)
                    node['window'] = {'start': start, 'end': end, 'quote': phrase}
            for child in node.values():
                if isinstance(child, (list, dict)): visit(child)
    visit(plan)
    return plan


def refit_concepts(catalog, question, plan):
    """A concept leaf is compiled only when the grammar reads its own quote as exactly that concept (request_vocabulary.concept_sql), and the grammar
    reads a fill or a condition together with its lead word ("have no SGLT2 inhibitor fill in the last year"). A filler often quotes more or less than that.
    Replace a concept leaf (or a negated one) by the grammar's own reading of the longest span of the question around its quote that is the same concept
    (same kind, same names, same polarity): the grammar states the window and the form itself, nothing the filler meant is changed."""
    def spans_of(quote):
        at = question.find(quote)
        if at < 0: return []
        lead = [m for m in re.finditer(r"[^\s]+", question[:at])][-3:]
        starts = [m.start() for m in lead] + [at]
        found = [m for m in re.finditer(r"[^\s]+", question[at:at + len(quote)])]
        out = []
        for i, start in enumerate(starts):
            for j in range(len(found), 0, -1):
                end = at + found[j - 1].end()
                out.append(question[start:end])
        for i in range(len(found)):
            for j in range(len(found), i, -1):
                out.append(quote[found[i].start():found[j - 1].end()])
        return sorted(dict.fromkeys(out), key=lambda t: (-len(t), t))

    def concept_of(node):
        negated = False
        while isinstance(node, dict) and node.get('op') == 'not':
            negated, node = not negated, node.get('arg')
        return (node, negated) if isinstance(node, dict) and node.get('op') == 'concept' else (None, False)

    def polarity(got):
        negated = False
        while isinstance(got, dict) and got.get('op') == 'not':
            negated, got = not negated, got.get('arg')
        return got, negated

    def fit(node):
        leaf, negated = concept_of(node)
        if leaf is None: return node
        quote = request_plan.quote_text(node.get('quote') or '')
        if node is not leaf: quote = request_plan.quote_text(node.get('quote') or '') if False else request_plan.quote_text(leaf.get('quote') or '')
        if not quote or quote not in question: return node
        names = [n.partition('@')[0] for n in leaf.get('names') or []]
        for span in spans_of(quote):
            try:
                got, tail = request_language.Parser(catalog, span).expression(span)
            except (request_plan.PlanError, KeyError, TypeError, ValueError):
                continue
            if not isinstance(got, dict) or tail.strip(): continue
            core, got_negated = polarity(got)
            if got_negated != negated or not isinstance(core, dict): continue
            same = core.get('op') == 'concept' and core.get('kind') == leaf.get('kind') and set(core.get('names') or []) == set(leaf.get('names') or [])
            fill = leaf.get('kind') == 'drug_fill' and core.get('op') == 'exists' and all(n in json.dumps(core) for n in names)
            if same or fill: return got
        return node

    def visit(node):
        if isinstance(node, list): return [visit(x) for x in node]
        if isinstance(node, dict):
            leaf, _ = concept_of(node)
            if leaf is not None: return fit(node)
            return {k: visit(v) if isinstance(v, (list, dict)) else v for k, v in node.items()}
        return node
    out = dict(plan)
    if isinstance(plan.get('where'), dict): out['where'] = visit(plan['where'])
    return out


def window_findings(question, plan, contract, as_of):
    """A window the question names must reach the compiled spec: the 12 months ending at the as-of date, or the calendar year (unless an engine
    owns the period)."""
    from . import decision_context as DC
    if not as_of or plan.get('analyses'): return []
    text = contract['query'] + json.dumps(contract.get('note_predicates') or {}, default=str) + json.dumps(plan.get('where'), default=str)
    out = []
    for w in DC.year_window(question, as_of):
        if w['kind'] == 'rolling_12_months':
            start, end = DC.rolling_year(as_of)
            if not ('-12 months' in text or start in text or w['start'] in text or '"within_last_months"' in text):
                out.append(f'the question says "{w["phrase"]}" (the 12 months ending {as_of}) but the spec has no such window')
        elif w['start'][:4] not in text:
            out.append(f'the question says "{w["phrase"]}" (calendar year {w["start"][:4]}) but the spec has no such window')
    return out


def decision_findings(question, contract, as_of):
    """decision_context.verify over the compiled SQL (all-source events, a query it cannot restrict). The living-patient and latest-lab rules
    are enforced in the SQL that runs (decision_context.enforce_sql) and checked again on the executed answer."""
    from . import decision_context as DC
    sqls = [s for s in (contract.get('query'), contract.get('membership_query')) if s]
    return list(DC.verify(question, {}, DC.Scope(question, as_of), None, sqls)['reasons'])


def validate(catalog, question, plan, *, population=None, as_of=None):
    """-> the compiled contract, or SpecInvalid(reason). A spec that still has unresolved requirements is invalid here (the caller may hold it)."""
    from .proposers import evidence_of
    from . import requirement_ledger
    as_of = as_of or catalog.identity.get('as_of')
    try:
        plan = request_plan.normalize_wire(catalog, question, plan)
        plan = normalize_windows(plan, question, as_of)
        try:
            contract = request_plan.compile_request(catalog, question, plan, population=population)
        except request_plan.PlanError:
            refit = refit_concepts(catalog, question, plan)
            if refit == plan: raise
            plan = refit
            contract = request_plan.compile_request(catalog, question, plan, population=population)
    except request_plan.AmbiguousRequest as exc:
        raise SpecInvalid(f'ambiguous: {exc}') from exc
    except (request_plan.PlanError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise SpecInvalid(f'compile: {exc}') from exc
    if plan['unresolved']:  # incomplete by its own account: held as the decline (the unresolved words are not executable, so the literal check does not apply)
        error = SpecInvalid('unresolved: ' + '; '.join(f"{request_plan.quote_text(u['quote'])} ({u['reason']})" for u in plan['unresolved'])[:400])
        error.held = (plan, contract)
        raise error
    gaps = requirement_ledger.gaps(question, evidence_of(contract))
    if gaps: raise SpecInvalid('literal check: the spec drops ' + '; '.join(gaps)[:400])
    reasons = decision_findings(question, contract, as_of) + window_findings(question, plan, contract, as_of)
    if reasons: raise SpecInvalid('decision context: ' + '; '.join(reasons)[:400])
    return contract


# -- the context the fillers share -----------------------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, stack, catalog, question, *, population=None, ledger=None, decide=None, models=None, as_of=None, deadline=None):
        self.stack, self.catalog, self.question, self.population = stack, catalog, question, population
        self.ledger = ledger
        self.ledger_path = str(getattr(ledger, 'path', '') or '') or None
        self.decide = decide
        self.models = models  # {'haiku': callable, 'sonnet': callable} (system, user, max_tokens=...) -> (raw, stats); None -> hosted
        self.as_of = str(as_of or catalog.identity.get('as_of') or '')[:10]
        self.live = bool(getattr(stack, 'allow_live', False)) or decide is not None or models is not None
        self.cancel = threading.Event()
        self.deadline = deadline
        self.grammar_error = None
        self.lock = threading.Lock()
        self.usage = {}  # filler -> {'calls', 'usd', 'known'}

    def expired(self):
        if self.cancel.is_set(): return True
        return self.deadline is not None and time.monotonic() >= self.deadline

    def charge(self, filler, calls=0, usd=0.0, known=True):
        with self.lock:
            u = self.usage.setdefault(filler, {'calls': 0, 'usd': 0.0, 'known': True})
            u['calls'] += calls or 0
            u['usd'] += usd or 0.0
            u['known'] = u['known'] and known

    def choose(self, filler, step, prompt, options, threshold):
        """One typed System One choice (cached by request hash). -> {'label', 'p', 'accepted', 'entry'}."""
        from .system_one_gate import cost
        from .spec_verify import decide_default
        started = time.perf_counter()
        decide = self.decide or decide_default
        out = decide(prompt, options, [], as_of=self.as_of, threshold=threshold, task_key='s1_spec_slot', ledger=self.ledger_path, allow_live=True)
        calls, usd = cost(out)
        self.charge(filler, calls, usd)
        probs = out.get('calibrated_probabilities') or out.get('probabilities') or {}
        label = out.get('label') or 'unknown'
        p = float(probs.get(label, 0.0))
        accepted = out.get('decided_by') is not None and label in options and p >= threshold
        return {'label': label, 'p': p, 'accepted': accepted,
                'entry': {'step': step, 'label': label, 'p': round(p, 4), 'accepted': accepted, 'provider': out.get('decided_by') or out.get('provider'),
                          'calls': calls, 'cost_usd': round(usd, 6), 'seconds': round(time.perf_counter() - started, 3)}}


# -- filler 1: the grammar -------------------------------------------------------------------------------------------------------------
def grammar_fill(ctx):
    try:
        program = request_language.parse_program(ctx.catalog, ctx.question, bound=ctx.population is not None)
    except request_plan.PlanError as exc:
        ctx.grammar_error = str(exc)
        raise SpecInvalid(f'grammar: {exc}') from exc
    if program is not None:
        ctx.grammar_error = 'a request program (several outputs) is answered by the grammar tier'
        raise SpecInvalid('grammar: the question is a request program, not one spec')
    try:
        plan = request_language.parse(ctx.catalog, ctx.question, bound=ctx.population is not None)
    except request_plan.PlanError as exc:
        ctx.grammar_error = str(exc)
        raise SpecInvalid(f'grammar: {exc}') from exc
    return plan, {}


# -- filler 2: System One slot filling ----------------------------------------------------------------------------------------------------
CONNECTORS = re.compile(r'(?:,\s*(?:and\s+)?|\s+)(?:and|but|who|that|whose|where|with|whom)\s+', re.I)
CLAUSE_VERBS = re.compile(r'\s+(?=(?:had|has|have|having|was|were|is|are|got|received|took|taking|declined|refused|reports?|reported|denies|denied|shows?|showed)\b)', re.I)
NOTE_CUES = re.compile(r'\b(?:notes?|documented|documentation|charted|chart|mentions?|mentioned|stopped|discontinued|declined|refus\w+|because|reason|side effects?|'
                       r'history of|denies|denied|reports?|reported|complain\w*|states?|stated|advance directive|goals of care|code status|fell|falls?|memory|noncompliant|'
                       r'non-adherent|intolerance|intolerant|allerg\w+|wrote|says?)\b', re.I)


def clause_boundaries(question):
    """[(prefix, suffix)] splits of the question where a trailing clause could begin (after a connector word or before a clause verb), latest
    boundary last."""
    cuts = {}
    for m in CONNECTORS.finditer(question): cuts[m.end()] = m.start()
    for m in CLAUSE_VERBS.finditer(question): cuts[m.end()] = m.start()
    return [(question[:cuts[end]].rstrip(' ,'), question[end:].strip().rstrip('?.').strip()) for end in sorted(cuts)]
NOTE_KIND = {'note_criterion': 'The clause asks what the clinical notes or documents say: documentation, a reason, a symptom, a refusal, a history, what a clinician wrote.',
             'structured_criterion': 'The clause is about coded data: a diagnosis code, lab value, fill, visit, claim, age or coverage.',
             'neither': 'It is not a condition on patients (a formatting request, a different question, or nonsense).'}
COMBINE_CUES = {'latest': r'\b(?:latest|most recent|newest)\b', 'first': r'\b(?:first|earliest|initial)\b', 'trend': r'\b(?:trend|worsen\w*|improv\w*|declin\w*)\b',
                'conflict': r'\b(?:contradict\w*|conflict\w*|disagree\w*|inconsisten\w*)\b', 'all': r'\b(?:every|always|all of the)\b'}


def stems_of(text):
    from .s1_governed import stems
    return stems(text)


def note_available():
    return request_plan.executor('note_frames.evaluate') is not None


def term_of(suffix, store, catalog):
    """(term, codes, quote): the clinical thing a note clause is about -- the longest span the concept library maps (deterministically), else the clause's
    content words."""
    from . import terminology
    best = None
    for span in terminology._spans(suffix):
        found = terminology.deterministic(terminology._norm(span))
        if found and found.get('kind') in terminology.KINDS and (best is None or len(span) > len(best[0])):
            best = (span, found)
    if best:
        span, found = best
        info = terminology.codes(found['kind'], found['concept']) or {}
        return found['concept'].replace('_', ' '), list(info.get('codes') or [])[:12], span
    content = [w for w in re.findall(r"[A-Za-z][\w'-]*", suffix) if w.lower() not in terminology.STOP and w.lower() not in request_plan.STOP_WORDS]
    phrase = ' '.join(content[:4]) or suffix
    return phrase, [], phrase if phrase in suffix else suffix


def note_clause(ctx, filler, prefix_plan, suffix, entries):
    """A typed note predicate for one trailing clause, or None. Every field is a choice from a closed list."""
    frames, statuses, combines, sources, details = request_plan.note_catalog()
    kind = ctx.choose(filler, 'clause_kind', f'What kind of condition is this clause of a patient question? Clause: {suffix}',
                      NOTE_KIND, env_float('AGERE_SPEC_S1_ACCEPT', 0.6))
    entries.append(kind['entry'])
    if not kind['accepted'] or kind['label'] != 'note_criterion': return None
    frame_options = {f: f'{f.replace("_", " ")}: the notes describe {spec_readback.FRAME_WORDS.get(f, f)}' for f in frames}
    frame = ctx.choose(filler, 'frame', f'Which kind of statement in the notes does this clause ask about? Clause: {suffix}', frame_options,
                       env_float('AGERE_SPEC_S1_ACCEPT', 0.6))
    entries.append(frame['entry'])
    if not frame['accepted'] or frame['label'] not in frames: return None
    status_options = {s: spec_readback.STATUS_WORDS.get(s, s) for s in statuses}
    status = ctx.choose(filler, 'status', f'What must the notes say about it for a patient to count? Clause: {suffix}', status_options,
                        env_float('AGERE_SPEC_S1_ACCEPT', 0.6))
    entries.append(status['entry'])
    if not status['accepted'] or status['label'] not in statuses: return None
    combine = 'any'
    for name, cue in COMBINE_CUES.items():
        if name in combines and re.search(cue, suffix, re.I): combine = name
    term, codes, quote = term_of(suffix, None, ctx.catalog)
    concept = {'term': term, 'quote': quote}
    if codes: concept['codes'] = codes
    detail = {}
    for field, options in (details.get(frame['label']) or {}).items():
        if not (stems_of(suffix) & stems_of(' '.join([field, *map(str, options)]))): continue
        menu = {str(o): str(o).replace('_', ' ') for o in options}
        menu['not_stated'] = 'The clause does not say.'
        got = ctx.choose(filler, f'detail:{field}', f'For the "{field.replace("_", " ")}" of this note statement, which value does the clause ask for? Clause: {suffix}',
                         menu, env_float('AGERE_SPEC_S1_ACCEPT', 0.6))
        entries.append(got['entry'])
        if got['accepted'] and got['label'] not in ('not_stated', 'unknown'): detail[field] = got['label']
    predicate = {'op': 'note', 'frame': frame['label'], 'concept': concept, 'statuses': [status['label']], 'combine': combine, 'quote': suffix.strip()}
    if detail: predicate['detail'] = detail
    from . import decision_context as DC
    for w in DC.year_window(suffix, ctx.as_of):
        if w['kind'] == 'rolling_12_months':
            start, end = DC.rolling_year(ctx.as_of)
            predicate['window'] = {'start': start, 'end': end, 'quote': w['phrase']}
    return predicate


def note_split(ctx, entries, notes):
    """The grammar's plan for the longest prefix it reads, with the trailing clause System One reads as a note predicate. -> plan or None."""
    for prefix, suffix in reversed(clause_boundaries(ctx.question)[-8:]):
        if ctx.expired(): raise SpecInvalid('s1_slots: out of time')
        if not suffix or not prefix: continue
        try:
            base = request_language.parse(ctx.catalog, prefix, bound=ctx.population is not None)
        except request_plan.PlanError:
            continue
        clause = note_clause(ctx, 's1_slots', base, suffix, entries)
        if clause is None: continue
        plan = json.loads(json.dumps(base))
        plan['where'] = request_language.combine('and', [plan['where'], clause], ctx.question)
        notes['note_clause'] = suffix
        return plan
    return None


def s1_fill(ctx):
    """System One fills the open slots of the grammar's partial parse. -> (plan, notes) or SpecInvalid."""
    if not ctx.live: raise SpecInvalid('s1_slots: System One is not available (not a live stack)')
    from . import terminology
    entries = []
    notes = {'steps': entries}
    # 1. a trailing note criterion: the grammar reads the prefix, System One reads the clause (tried first when the wording is about what the notes say)
    if note_available() and NOTE_CUES.search(ctx.question):
        found = note_split(ctx, entries, notes)
        if found: return found, notes
    # 2. an unknown clinical term in the unconsumed tail -> the concept library shortlist, one typed choice per span, then the grammar again
    mapped, error = [], ctx.grammar_error
    store = terminology.store_path(Path(ctx.catalog.target).parent)
    adjudications = 0
    for _ in range(3):
        if ctx.expired(): raise SpecInvalid('s1_slots: out of time')
        tail = terminology.unconsumed(error)
        if not tail: break
        found = None
        for span in terminology.candidate_spans(tail):
            if adjudications >= terminology.MAX_ADJUDICATIONS: break
            r = terminology.map_label(span, store=store, question=ctx.question, as_of=ctx.as_of, ledger=ctx.ledger_path, allow_live=True, decider=ctx.decide)
            ctx.charge('s1_slots', r.get('calls', 0), r.get('cost_usd', 0.0))
            adjudications += 1 if r.get('system_one') else 0
            entries.append({'step': 'term', 'span': span, 'concept': r.get('concept'), 'method': r.get('method'), 'calls': r.get('calls', 0)})
            if r.get('concept'):
                found = {**r, 'span': span}
                break
        if not found: break
        mapped.append(found)
        rows = {terminology._norm(m['span']): {'kind': m['kind'], 'concept': m['concept'], 'classes': m.get('classes'), 'method': 'saved_system_one',
                                              'provider': None, 'probability': None, 'narrowing': m.get('narrowing')} for m in mapped}
        terminology.use_session(rows)  # this filler's thread only (a copied context): the compile's own re-reads of the leaf see the mapping too
        try:
            plan = request_language.parse(ctx.catalog, ctx.question, bound=ctx.population is not None)
            notes['terminology'] = [{k: m.get(k) for k in terminology.RECEIPT_KEYS} for m in mapped]
            notes['session_rows'] = rows
            return plan, notes
        except request_plan.PlanError as exc:
            error = str(exc)
    # 3. the same note split, for a note clause the wording cues did not announce
    if note_available() and not NOTE_CUES.search(ctx.question):
        found = note_split(ctx, entries, notes)
        if found: return found, notes
    raise SpecInvalid('s1_slots: no closed slot could be filled (' + (f'grammar said: {ctx.grammar_error[:160]}' if ctx.grammar_error else 'no partial parse') + ')')


# -- filler 3: one Haiku call that writes the spec ---------------------------------------------------------------------------------------
SPEC_ADDENDUM = '''
QUESTION SPEC (this call). Return exactly one typed request JSON object with the keys above (source, select, where, group_by, order_by, limit, unresolved; optional analyses, joins, having, workflow, subject).
You write a SPEC, never SQL. Do not return {"analysis": ...} or {"program": ...}. If a requirement cannot be expressed, keep its exact words in unresolved with the reason.
The decision context above is applied by the compiler: do not add a living-patient filter yourself unless the question asks for the deceased; write "last year" windows as the compiler expects (within_last_months 12 on the event date).
Every quote is a literal substring of the original question. Preserve every predicate, negation, OR/AND, number, window, measure and grouping.
Use op=concept only with names listed in named_concepts. Its quote must describe exactly that one predicate, including its own evidence verb and window when present: e.g. "on a statin in 2029". The compiler reparses the quote and requires the concept, polarity and period to agree. Do not attach another condition, a sibling negation, grouping, or an unrelated filter to a concept leaf. Literal spans may be a list when the relevant words are separated.
A negative condition uses op=not around the positive concept; put the negation words on the NOT quote and the positive evidence wording on its child. "neither X nor Y" means NOT X AND NOT Y. "both X and Y" and a comma-separated condition list mean AND for the same patient; they do not require equal diagnosis dates unless dates are explicitly requested.
Every substantive word must bind to an executed leaf or an explicit operator. Source/measure quotes should include their complete source/calculation wording, not just a single noun. Preserve comparative groups and denominators as separate executed predicates; do not mark ordinary counting, conjunction, negation, or a stated group_by as unresolved merely because the wording is unfamiliar.
A drug or condition that no listed name covers cannot be expressed: keep its exact words in unresolved with that reason; never invent a name.
'''
COMPOSITION_ADDENDUM = '''
COMPOSITION FORMS the compiler accepts (docs/agere/QUESTION_SPEC.md, Addition 4); use them instead of leaving a requirement unresolved:
- predicate {"op":"at_least_of","n":2,"args":[p1,p2,p3],"quote":"at least two of these three"}: at least N of 2..12 predicates.
- predicate {"op":"sum_at_least","table":"payer_claim","field":"paid_cents","where":claim predicate,"amount":50000,"scale":0.01,"cmp":">=","quote":"..."}: a patient's total of a money column reaches an amount.
- count_at_least may carry "distinct":"need" (at least N DIFFERENT values of that column).
- negation and "A or B but not both" are not / and / or trees; the quote of an and / or / not / at_least_of holds only logic words ("both", "no", "neither", "nor", "all at once"); every content word sits in a leaf.
- dimension {"kind":"cohort_group","alias":"payer_type","quote":[...],"groups":[{"label":"medicare","where":predicate,"quote":"Medicare"},...]} (2..12 named groups, may overlap) for age bands, payer types, "women and men", "heart failure patients vs COPD patients"; group by its alias.
- measure {"kind":"count_related","table":"utilization_event","where":event predicate,"alias":"events"} = total related rows of the counted patients; in a ratio of scale 1000 it is "per 1,000 patients" (denominator count_distinct patient_id). measure {"kind":"sum_related","table":"payer_claim","field":"paid_cents","where":...} is the total of a money column of the counted patients.
- a ranking is group_by + order_by (alias, direction) + limit; "at least N rows" is a having on an output alias (result.<alias>).
'''
NOTE_ADDENDUM = '''
NOTE PREDICATES (what the clinical notes, scanned documents and HIE documents say) -- a predicate that may stand anywhere a predicate can (inside where, and/or/not, exists):
{"op":"note","frame":FRAME,"concept":{"term":"metformin","codes":["RxNorm:6809"],"quote":"metformin"},"statuses":[STATUS...],"detail":{FIELD:VALUE},"window":{"start":"YYYY-MM-DD","end":"YYYY-MM-DD","quote":"in the last year"},"combine":COMBINE,"sources":[...],"quote":"literal clause of the question"}
frame is one of: {frames}.
statuses (the shared assertion options; the predicate holds when the patient's combined status is one of them): {statuses}.
combine (how several passages or notes reduce to one patient value): {combines}. sources (optional): {sources}.
detail fields per frame (closed option lists): {details}
Use a note predicate only for what the notes say (a reason, a refusal, a symptom, a stated history); use coded fields for diagnoses, labs, fills, visits and claims. Never invent a status or detail value outside these lists.
'''
NO_NOTES_ADDENDUM = '''
This build has no note reader: a requirement about what the clinical notes say stays in unresolved with that reason.
'''
SUBJECT_ADDENDUM = '''
SUBJECT: a question about ONE named patient adds {"subject":{"kind":"patient","patient_id":"<id exactly as written>","quote":"<literal id wording>"}} at the top level. Every patient-keyed relation then binds that patient.
Use it only when subject_hint (or the question) gives a patient id. Without a subject the request is about the population. A chart-level answer about the notes is select [{"kind":"note_answer","alias":"note_answer","quote":"..."}] with a note predicate in where.
'''


def spec_system():
    """shared_planner's rules (core, vbc, engines, joins, streams, concepts, workflow, decision context) without the free analysis / program / discovery
    parts, plus this module's spec rules and the note frame catalog."""
    from . import shared_planner
    base = ''.join(text for name, text in shared_planner.SYSTEM_PARTS if name not in ('analysis', 'program', 'discovery'))
    frames, statuses, combines, sources, details = request_plan.note_catalog()
    if note_available():
        notes = NOTE_ADDENDUM
        for key, value in (('{frames}', ', '.join(frames)), ('{statuses}', ', '.join(statuses)), ('{combines}', ', '.join(combines)), ('{sources}', ', '.join(sources)),
                           ('{details}', json.dumps({f: {k: list(v) for k, v in d.items()} for f, d in details.items()}) if details
                            else 'see the frame names; leave detail out when unsure')):
            notes = notes.replace(key, value)
    else:
        notes = NO_NOTES_ADDENDUM
    return base + SPEC_ADDENDUM + COMPOSITION_ADDENDUM + notes + SUBJECT_ADDENDUM


def default_models(ctx):
    """Hosted Haiku / Sonnet on a ledger connection of their own (the thread that calls them opens it)."""
    from .nl2sql import HostedModel
    from ..gateway import BudgetLedger, default_ledger_path
    ledger = BudgetLedger(ctx.ledger_path or default_ledger_path())
    receipts = getattr(ctx.stack, 'receipts', None)
    return {'haiku': HostedModel(ledger, receipts, model=os.environ.get('AGERE_SPEC_HAIKU', HAIKU)),
            'sonnet': HostedModel(ledger, receipts, model=os.environ.get('AGERE_SPEC_SONNET_MODEL', SONNET))}, ledger


def subject_hint(ctx):
    resolve = request_plan.executor('chart_reader.resolve_subject')
    if resolve is None: return None
    try:
        return resolve(ctx.stack, ctx.question)
    except Exception:  # noqa: BLE001 - a hint only
        return None


def spec_user(ctx, feedback, extra, operations, streams):
    from . import shared_planner, decision_context as DC
    context = shared_planner.schema_slice(ctx.catalog, ctx.question, extra, operations, streams)
    try:
        from .learning import correction_context
        corrections = correction_context(Path(ctx.catalog.target).parent, ctx.question)
    except Exception:  # noqa: BLE001 - no learning store (fixtures)
        corrections = []
    return shared_planner.planner_user(context, question=ctx.question, saved_cohort_size=len(ctx.population) if ctx.population is not None else None,
                                       validator_feedback=feedback, reported_corrections=corrections,
                                       spec_hints={'as_of': ctx.as_of, 'subject_hint': subject_hint(ctx),
                                                   'decision_context_windows': [{k: w[k] for k in ('phrase', 'kind', 'start', 'end')}
                                                                                for w in DC.year_window(ctx.question, ctx.as_of)]})


def haiku_fill(ctx, *, previous=None, review=None, steps=None):
    """Haiku writes the spec; one bounded repair from validator feedback; Sonnet only after two failed validations.
    previous/review: a spec System One refused part by part and what it said (the verification repair). -> (plan, notes)."""
    from . import schema_prefetch, shared_planner
    if not ctx.live: raise SpecInvalid('haiku: Claude is not available (not a live stack)')
    models, owned_ledger = (ctx.models, None) if ctx.models is not None else default_models(ctx)
    try:
        system = spec_system()
        likely = schema_prefetch.likely(ctx.catalog, ctx.question)
        extra, operations, streams = list(likely['tables']), list(likely['operations']), list(likely['streams'])
        feedback = ''
        if review:
            feedback = {'spec_review': review, 'previous_proposal': previous,
                        'instruction': 'System One checked each part of this spec against the question and these parts do not match. Repair exactly those parts, keep every other part, '
                                       'and preserve the whole original question. Return the complete corrected spec.'}
        notes = {'calls': []}
        # The Haiku-first ladder (model_policy): the first pass is Haiku, Haiku again with the validator's feedback; Sonnet is the escalation
        # rung only, and the cascade runs it after every Haiku attempt of every tier for the question has failed.
        plan_calls = ('sonnet',) if model_policy.escalating() else ('haiku', 'haiku')
        used = 0
        attempt = 0
        last_error = None
        while attempt < len(plan_calls):
            if ctx.expired(): raise SpecInvalid('haiku: out of time')
            which = plan_calls[attempt]
            if review and attempt == 0 and which != 'haiku': which = 'haiku'
            if which == 'sonnet' and ctx.deadline is not None and ctx.deadline - time.monotonic() < env_float('AGERE_SPEC_SONNET_MIN_SECONDS', 5.0):
                raise SpecInvalid(f'haiku: {last_error}; no time left for the Sonnet attempt')
            model = models[which]
            user = spec_user(ctx, feedback, extra, operations, streams)
            started = time.perf_counter()
            raw, stats = model(system, user, max_tokens=SPEC_TOKENS)
            stats = stats if isinstance(stats, dict) else {}
            model_policy.record(stats.get('model') or which)
            ctx.charge('haiku', 0 if stats.get('cache_hit') else 1, stats.get('api_usd') or 0.0, stats.get('api_cost_known', True))
            notes['calls'].append({'model': stats.get('model') or which, 'seconds': round(time.perf_counter() - started, 3), 'usd': stats.get('api_usd') or 0.0,
                                   'cache_hit': bool(stats.get('cache_hit'))})
            try:
                plan = shared_planner.decode(raw)
                names = {'need_tables', 'need_operations', 'need_streams'}
                if plan and set(plan) <= names:  # a schema request is part of this call, not a failed validation
                    from .graph_planner import CONTRACTS
                    for key, available in (('need_tables', ctx.catalog.tables), ('need_operations', CONTRACTS), ('need_streams', ctx.catalog.stream_fields)):
                        if any(n not in available for n in plan.get(key, [])): raise request_plan.PlanError('Unknown schema request: ' + key)
                    extra = list(dict.fromkeys([*extra, *plan.get('need_tables', [])]))
                    operations = list(dict.fromkeys([*operations, *plan.get('need_operations', [])]))
                    streams = list(dict.fromkeys([*streams, *plan.get('need_streams', [])]))
                    feedback = 'Requested schemas are now included. Produce the complete typed request.'
                    used += 1
                    if used > 2: raise request_plan.PlanError('Too many schema requests')
                    continue
                if set(plan) & {'analysis', 'program'} and 'source' not in plan:
                    raise request_plan.PlanError('Return the typed request (source, select, where, ...), not an analysis or a program')
                plan = schema_prefetch.apply_patch(None, plan)
                contract = validate(ctx.catalog, ctx.question, plan, population=ctx.population, as_of=ctx.as_of)
                notes['attempt'] = attempt + 1
                return plan, notes
            except SpecInvalid as exc:
                last_error = exc
                if exc.held is not None: raise
                feedback = {'error': str(exc), 'previous_proposal': raw[:6000],
                            'instruction': 'Repair this specific validation error while preserving the original question. Quotes are literal substrings of the question; '
                                           'every predicate, number, window and negation must survive. Return the complete corrected spec.'}
            except (ValueError, KeyError, TypeError, AttributeError, request_plan.PlanError) as exc:
                last_error = SpecInvalid(f'haiku: {exc}')
                feedback = {'error': str(exc), 'previous_proposal': raw[:6000],
                            'instruction': 'Repair this specific validation error while preserving the original question. Return the complete corrected spec.'}
            attempt += 1
        raise SpecInvalid(f'haiku: {last_error}' if last_error else 'haiku: no valid spec')
    finally:
        if owned_ledger is not None: owned_ledger.close()


# -- running the fillers -------------------------------------------------------------------------------------------------------------------
def run_filler(name, fn, ctx):
    """-> the candidate dict for one filler (never raises)."""
    started = time.perf_counter()
    cand = {'name': name, 'status': 'error', 'seconds': 0.0, 'plan': None, 'contract': None, 'error': None, 'notes': {}}
    try:
        plan, notes = fn(ctx)
        cand['notes'] = notes
        contract = validate(ctx.catalog, ctx.question, plan, population=ctx.population, as_of=ctx.as_of)
        cand.update(status='valid', plan=copy.deepcopy(contract['request']), contract=contract)
    except SpecInvalid as exc:
        cand.update(status='invalid', error=str(exc)[:500])
        if exc.held is not None: cand['held'] = exc.held
    except Exception as exc:  # noqa: BLE001 - one filler failing never stops the others
        cand.update(status='error', error=f'{type(exc).__name__}: {str(exc)[:300]}')
    cand['seconds'] = round(time.perf_counter() - started, 3)
    usage = ctx.usage.get(name) or {}
    cand['calls'], cand['usd'], cand['cost_known'] = usage.get('calls', 0), round(usage.get('usd', 0.0), 6), usage.get('known', True)
    return cand


def run_signature(stack, contract, population):
    """The result a spec's SQL gives, for comparing two readings (None when it cannot be run cheaply)."""
    if contract.get('note_predicates') or contract.get('request', {}).get('analyses'): return None
    try:
        from . import sql_query
        report = sql_query.execute(stack.path, {'query': contract['query'], 'returns': contract['returns']}, patient_ids=population,
                                   target=request_plan.Catalog.load(stack.path).target)
    except Exception:  # noqa: BLE001
        return None
    if report['returns'] == 'patient_set': return ('patient_set', tuple(sorted(report['patient_ids'])))
    if report['returns'] in ('count', 'value'): return (report['returns'], report['value'])
    return ('table', json.dumps(report['rows'], sort_keys=True, default=str))


def reading(cand, catalog, question):
    return {'filler': cand['name'], 'read_back': spec_readback.read_back(cand['plan'], catalog, question)}


def choose(ctx, valid, verify=None):
    """The chosen candidate among those that validated, and how it was chosen. Raises SpecNotFilled with both readings when they disagree and
    nothing settles it."""
    order = {n: i for i, n in enumerate(FILLERS)}
    valid = sorted(valid, key=lambda c: order.get(c['name'], 9))
    if len(valid) == 1: return valid[0], 'single', None
    first = valid[0]
    others = [c for c in valid[1:] if not same_spec(first['plan'], c['plan'])]
    if not others: return first, 'agree', None
    sig = run_signature(ctx.stack, first['contract'], ctx.population)
    differing = [c for c in others if sig is None or run_signature(ctx.stack, c['contract'], ctx.population) != sig]
    if not differing: return first, 'agree_results', None
    if first['name'] == 'grammar': return first, 'disagree_grammar_preferred', [reading(c, ctx.catalog, ctx.question) for c in valid]
    if verify is not None:
        scored = [(verify(c), c) for c in [first, *differing]]
        scored.sort(key=lambda sc: (not sc[0]['confirmed'], bool(sc[0]['failed']), -sc[0]['score']))
        best = scored[0]
        tied = len(scored) > 1 and scored[1][0]['confirmed'] == best[0]['confirmed'] and abs(scored[1][0]['score'] - best[0]['score']) < 0.05
        if not tied and not best[0]['failed']:
            best[1]['verification'] = best[0]
            return best[1], 'disagree_system_one', [reading(c, ctx.catalog, ctx.question) for c in valid]
    error = SpecNotFilled('The question has more than one reading and nothing settled which is meant: ' + ' | '.join(
        f'{r["filler"]}: {r["read_back"]}' for r in (reading(c, ctx.catalog, ctx.question) for c in valid)))
    error.readings = [reading(c, ctx.catalog, ctx.question) for c in valid]
    raise error


# -- the early start: the paid fillers begin as soon as the grammar declines (Mike 2026-10-24: the Haiku spec writer runs in parallel), while the
# cascade's earlier stages (reuse, chart reader, System One choosers, free planners) run; the spec_path tier then takes the finished candidates.
_EARLY, _EARLY_LOCK, EARLY_TTL = {}, threading.Lock(), 180.0


def _early_key(stack, question, population):
    """Per request: the question's decision-context scope object (one per compose) keeps two concurrent identical questions apart."""
    from .decision_context import _SCOPE
    return (str(getattr(stack, 'path', '') or id(stack)), ' '.join(str(question or '').split()), tuple(population) if population is not None else None,
            id(_SCOPE.get()))


def start_early(stack, catalog, question, *, population=None, ledger=None, names=('s1_slots', 'haiku')):
    """Start the paid fillers for this question in the background (once per question). Never raises."""
    try:
        key = _early_key(stack, question, population)
        now = time.monotonic()
        with _EARLY_LOCK:
            for k in [k for k, v in _EARLY.items() if now - v[2] > EARLY_TTL]: _EARLY.pop(k, None)
            if key in _EARLY: return
            names = tuple(n for n in names if n in enabled_fillers())
            if not names: return
            ctx = Ctx(stack, catalog, question, population=population, ledger=ledger, decide=PROVIDERS['decide'], models=PROVIDERS['models'],
                      deadline=now + env_float('AGERE_SPEC_EARLY_SECONDS', 30.0))
            # the spec grammar first, on this context (cheap, deterministic): System One slot filling reads its decline (the unconsumed tail),
            # and a spec the grammar fills needs no paid filler at all
            if run_filler('grammar', grammar_fill, ctx)['status'] == 'valid': return
            fns = {'s1_slots': s1_fill, 'haiku': haiku_fill}
            pool = ThreadPoolExecutor(max_workers=len(names), thread_name_prefix='spec-early')
            futures = {n: pool.submit(contextvars.copy_context().run, run_filler, n, fns[n], ctx) for n in names}
            pool.shutdown(wait=False)
            _EARLY[key] = (ctx, futures, now)
    except Exception:  # noqa: BLE001 - an early start is an optimisation; the tier runs its fillers itself without it
        return


def take_early(stack, question, population):
    """-> (ctx, {name: Future}) started for this question (removed from the registry), or (None, {})."""
    with _EARLY_LOCK:
        entry = _EARLY.pop(_early_key(stack, question, population), None)
    return (entry[0], entry[1]) if entry else (None, {})


def cancel_early(stack, question, population):
    """The question is answered (another route won, or the spec path finished): the early fillers stop at their next step."""
    ctx, _ = take_early(stack, question, population)
    if ctx is not None: ctx.cancel.set()


def fill(stack, catalog, question, *, population=None, ledger=None, decide=None, models=None, as_of=None, verify=None, fillers=None, grace=None,
         seconds=None, ctx_hook=None):
    """Run the fillers and choose. -> {'winner', 'plan', 'contract', 'agreement', 'readings', 'receipt'}; raises SpecNotFilled.
    decide / models: System One / Claude callables (tests pass fakes; None -> the live providers). verify(candidate) -> spec_verify receipt, used
    only to settle a disagreement."""
    started = time.perf_counter()
    decide, models = decide or PROVIDERS['decide'], models or PROVIDERS['models']
    names = tuple(fillers) if fillers else enabled_fillers()
    grace = env_float('AGERE_SPEC_GRACE_SECONDS', 2.0) if grace is None else grace
    seconds = env_float('AGERE_SPEC_SECONDS', 12.0) if seconds is None else seconds
    ctx = Ctx(stack, catalog, question, population=population, ledger=ledger, decide=decide, models=models, as_of=as_of,
              deadline=time.monotonic() + seconds)
    if ctx_hook: ctx_hook(ctx)
    from .answer_budget import stop_check
    stopped = stop_check()  # the cascade's tier deadline, bound now: worker threads do not inherit the context

    candidates = {}
    if 'grammar' in names:
        candidates['grammar'] = run_filler('grammar', grammar_fill, ctx)
    paid = [n for n in names if n != 'grammar']
    if candidates.get('grammar', {}).get('status') == 'valid': cancel_early(stack, question, population)  # the grammar's spec wins; stop the early start
    if candidates.get('grammar', {}).get('status') != 'valid' and paid:
        limits = {'s1_slots': env_float('AGERE_SPEC_S1_SECONDS', 8.0), 'haiku': env_float('AGERE_SPEC_HAIKU_SECONDS', 12.0)}
        fns = {'s1_slots': s1_fill, 'haiku': haiku_fill}
        early_ctx, early = take_early(stack, question, population) if decide is PROVIDERS['decide'] and models is PROVIDERS['models'] else (None, {})
        pool = ThreadPoolExecutor(max_workers=len(paid), thread_name_prefix='spec-fill')
        futures = {(early[n] if n in early else pool.submit(contextvars.copy_context().run, run_filler, n, fns[n], ctx)): n for n in paid}
        due = {n: time.monotonic() + limits.get(n, 20.0) for n in paid}
        pending, first_valid = set(futures), None
        try:
            while pending:
                now = time.monotonic()
                if stopped() or now >= ctx.deadline: break
                horizon = [due[futures[f]] for f in pending]
                timeout = max(0.05, min(min(horizon) - now, 0.25))
                done, pending = wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
                for f in done:
                    cand = f.result()
                    candidates[cand['name']] = cand
                    if cand['status'] == 'valid' and first_valid is None: first_valid = time.monotonic()
                for f in list(pending):
                    if time.monotonic() >= due[futures[f]]:
                        pending.discard(f)
                        candidates[futures[f]] = {'name': futures[f], 'status': 'timeout', 'seconds': round(limits[futures[f]], 3), 'error': 'filler deadline',
                                                  'plan': None, 'contract': None, 'notes': {}, 'calls': ctx.usage.get(futures[f], {}).get('calls', 0),
                                                  'usd': round(ctx.usage.get(futures[f], {}).get('usd', 0.0), 6), 'cost_known': False}
                if first_valid is not None and time.monotonic() - first_valid >= grace: break
        finally:
            ctx.cancel.set()
            if early_ctx is not None: early_ctx.cancel.set()  # the early fillers belong to this request: they stop with it
            pool.shutdown(wait=False, cancel_futures=True)
        for f, n in futures.items():
            if n not in candidates:
                candidates[n] = {'name': n, 'status': 'cancelled', 'seconds': round(time.perf_counter() - started, 3), 'error': 'a valid spec was chosen first' if first_valid else 'out of time',
                                 'plan': None, 'contract': None, 'notes': {}, 'calls': ctx.usage.get(n, {}).get('calls', 0), 'usd': round(ctx.usage.get(n, {}).get('usd', 0.0), 6),
                                 'cost_known': False}
    valid = [c for c in candidates.values() if c['status'] == 'valid']
    receipt = {'version': VERSION, 'fillers': {n: {k: c.get(k) for k in ('status', 'seconds', 'calls', 'usd', 'cost_known', 'error')} for n, c in candidates.items()},
               'seconds': round(time.perf_counter() - started, 3), 'calls': sum(c.get('calls', 0) for c in candidates.values()),
               'usd': round(sum(c.get('usd', 0.0) for c in candidates.values()), 6), 'cost_known': all(c.get('cost_known', True) for c in candidates.values())}
    for n, c in candidates.items():
        if c.get('notes'): receipt['fillers'][n]['notes'] = {k: v for k, v in c['notes'].items() if k != 'session_rows'}
    if not valid:
        held = next((c['held'] for c in candidates.values() if c.get('held')), None)
        error = SpecNotFilled('No filler produced a spec that validates: ' + '; '.join(f'{n}: {c.get("error") or c["status"]}' for n, c in candidates.items())[:700])
        error.receipt, error.held = receipt, held
        raise error
    try:
        chosen, agreement, readings = choose(ctx, valid, verify)
    except SpecNotFilled as exc:
        exc.receipt = {**receipt, 'agreement': 'ambiguous'}
        raise
    receipt.update(winner=chosen['name'], agreement=agreement)
    return {'winner': chosen['name'], 'plan': chosen['plan'], 'contract': chosen['contract'], 'agreement': agreement, 'readings': readings,
            'candidate': chosen, 'receipt': receipt, 'ctx': ctx}


def repair(stack, catalog, question, plan, review, *, population=None, ledger=None, models=None, as_of=None):
    """One Haiku repair of a spec System One refused part by part (feedback: spec_verify.feedback). -> the same dict as fill, or SpecNotFilled."""
    started = time.perf_counter()
    models = models or PROVIDERS['models']
    ctx = Ctx(stack, catalog, question, population=population, ledger=ledger, models=models, as_of=as_of, deadline=time.monotonic() + env_float('AGERE_SPEC_HAIKU_SECONDS', 12.0))
    cand = run_filler('haiku', lambda c: haiku_fill(c, previous=plan, review=review), ctx)
    receipt = {'version': VERSION, 'fillers': {'haiku': {k: cand.get(k) for k in ('status', 'seconds', 'calls', 'usd', 'cost_known', 'error')}},
               'seconds': round(time.perf_counter() - started, 3), 'calls': cand.get('calls', 0), 'usd': cand.get('usd', 0.0), 'cost_known': cand.get('cost_known', True),
               'winner': 'haiku_repair', 'agreement': 'repair'}
    if cand['status'] != 'valid':
        error = SpecNotFilled('The repaired spec does not validate: ' + str(cand.get('error')))
        error.receipt = receipt
        raise error
    return {'winner': 'haiku_repair', 'plan': cand['plan'], 'contract': cand['contract'], 'agreement': 'repair', 'readings': None, 'candidate': cand, 'receipt': receipt, 'ctx': ctx}
