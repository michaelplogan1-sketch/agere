"""The analysis methods of the wider application, as a rung of the tiered core.

  code         the application's own compiler reads the question (no model). It names a method only for wording it
               recognises: forecasts, simulations, program effect, contract and payer measures, denial risk, outreach
               allocation, interaction screens, care gaps, monitoring, and the rest of clinical/engine_suite.py.
  code         the method runs on the warehouse inside a time limit and states its own assumptions and limits.

A plan made only of counting or listing steps is not taken here: the recipe tiers answer those. A question that a
built-in recipe or a promoted saved recipe already matches is not taken either. A method that would need a paid call,
or that does not finish in time, is declined and the question continues down the ladder.

When the compiler does not recognise the wording but the question uses the vocabulary of these methods (assisted):

  small model  picks one method from the registered menu, or none, and restates the question in that method's wording
  code         the restatement may not add a number, period or id; the compiler must name the picked method from it
  System One   confirms the restatement asks exactly what the question asks; unconfirmed is not used

System One is also asked whether the method the compiler named answers the question (fits); a confident no declines it.
"""
import os
import re
import threading
import time

MAX_SECONDS = float(os.environ.get('AGERE_TIERED_METHOD_SECONDS', '60'))
MAX_ROWS = 500
# Steps that count, list or look something up: the recipe tiers and the takeover rung own these.
NOT_METHODS = {'cohort', 'aggregate', 'sql', 'union', 'intersect', 'difference', 'knowledge', 'briefing', 'chart_reader', 'domain_pack',
               'ed_visits', 'scanned_document_search', 'medication_state'}
_PLANS = {}


def enabled():
    return os.environ.get('AGERE_TIERED_METHODS', 'off') == 'on'


def _compiled(stack, question):
    """Code only: what the application's compiler makes of the question. -> (graph or None, info), cached."""
    key = (str(stack.path), ' '.join(question.split()))
    if key in _PLANS: return _PLANS[key]
    try:
        from ..clinical import local_compiler
        from ..clinical.source_profile import load as load_profile
        graph, info = local_compiler.compile(question, load_profile(stack.path), stack.retriever.meta['snapshot']['as_of'])
    except Exception:  # the compiler failing leaves the question to the other tiers
        graph, info = None, {}
    if len(_PLANS) > 512: _PLANS.clear()
    _PLANS[key] = (graph, info or {})
    return _PLANS[key]


def plan(stack, question):
    """Code only. -> (graph, info, method names) when the application's compiler names an analysis method, else None."""
    if not enabled() or getattr(stack, 'path', None) is None or not hasattr(stack, 'run_graph'): return None
    graph, info = _compiled(stack, question)
    named = [n['operation'] for n in (graph or {}).get('nodes') or [] if n['operation'] not in NOT_METHODS]
    if named and not (graph.get('unmet_requirements') and graph.get('coverage') == 'unsupported'):
        return graph, info, list(dict.fromkeys(named))
    return None


def needs(stack, question, bottles=None):
    """Code only. -> the inputs a method needs that are neither in the question nor in the warehouse (contract terms, a
    comparator, a benchmark), or None. The compiler recognised the question and knows exactly what it lacks, so the
    user is told what to add instead of a model guessing the missing figure. Not when a recipe matches the question."""
    from . import recipes
    if not enabled() or getattr(stack, 'path', None) is None or not hasattr(stack, 'run_graph'): return None
    graph, info = _compiled(stack, question)
    wanted = [str(x) for x in info.get('requires_input') or []] if info.get('route') == 'local_requires_input' else []
    if not wanted or recipes.match(question) is not None: return None
    if bottles is not None and bottles.match(question, statuses=('promoted',)) is not None: return None
    return wanted


READS_DOCUMENTS = re.compile(r'(?i)\b(notes?|documents?|letters?|discharge summar(?:y|ies)|scanned|reports? (?:say|state|document|mention))\b')


def claims(stack, question, bottles=None):
    """Code only: does this rung take the question before the recipe tiers? Not when a recipe or saved recipe matches."""
    from . import recipes
    # A question about what notes, documents or letters say is for the reading tiers: the methods compute from structured
    # fields and would answer something else (notes_v1, 2026-11-17: an ED-note question went to the ED timing method).
    if READS_DOCUMENTS.search(str(question)): return False
    if plan(stack, question) is None: return False
    if recipes.match(question) is not None: return False
    return not (bottles is not None and bottles.match(question, statuses=('promoted',)) is not None)


def run(stack, question):
    """-> {'result': the application's result dict, 'methods', 'seconds', 'info'} or None (declined, failed, too slow or paid)."""
    found = plan(stack, question)
    if found is None: return None
    graph, info, named = found
    started, box = time.monotonic(), {}

    def work():
        try:
            box['result'] = stack.run_graph(graph, question=question)
        except Exception as exc:  # reported to the caller, which continues down the ladder
            box['error'] = f'{type(exc).__name__}: {str(exc)[:200]}'

    worker = threading.Thread(target=work, daemon=True, name='tiered-method')
    worker.start(); worker.join(MAX_SECONDS)
    seconds = round(time.monotonic() - started, 2)
    if worker.is_alive(): return {'declined': f'{", ".join(named)} did not finish in {MAX_SECONDS:.0f} seconds', 'methods': named, 'seconds': seconds}
    if 'error' in box: return {'declined': box['error'], 'methods': named, 'seconds': seconds}
    result = box['result']
    answer = result.get('answer') if isinstance(result, dict) else None
    if not isinstance(answer, dict) or answer.get('status') not in ('answered', 'partial'):
        return {'declined': f"{', '.join(named)} returned {(answer or {}).get('status') or 'no answer'}", 'methods': named, 'seconds': seconds}
    if result.get('new_paid_api_calls'):  # this rung is code only; a method that paid for a model is not presented as one
        return {'declined': f"{', '.join(named)} used a paid model call", 'methods': named, 'seconds': seconds}
    return {'result': result, 'methods': named, 'seconds': seconds, 'info': info}


# ---- wording the compiler does not recognise: the small model restates it, code and System One check the restatement ----

WORDS = re.compile(r"\b(?:forecast\w*|predict\w*|project\w*|risk\w*|likely|simulat\w*|what if|if we|would happen|effect\w*|impact\w*|uplift|benefit\w*|anomal\w*|"
                   r"unusual\w*|out of control|signals?|chang\w+|trend\w*|driv\w+|why|optimi[sz]\w*|allocat\w*|assign\w*|staff\w*|roster\w*|capacity|queue\w*|"
                   r"wait\w*|boarding|benchmark\w*|contracts?|savings|settlement|stars?|loss ratio|mlr|gaps?|denial\w*|denied|upcod\w*|kickback\w*|interact\w*|"
                   r"340b|prior auth\w*|surviv\w*|time to|how long|adequa\w*|networks?|referr\w*|clusters?|outbreaks?|infection\w*|stewardship|antibiotic\w*|"
                   r"bundle\w*|sepsis|conform\w*|guideline\w*|pathways?|journeys?|delay\w*|missed|loops?|hcc\w*|raf|risk adjustment|captur\w*|underpa\w*|"
                   r"reimburse\w*|negotiated|overbook\w*|no-?shows?|fair\w*|social needs?|transitions?|merge\w*|duplicates?|cql|polic\w+|channels?|outreach|"
                   r"calibrat\w*|accura\w*|compar\w+ (?:practices|providers|pcps|clinics)|outliers?|concentrat\w*|declin\w*|worsen\w*|trajector\w*|"
                   r"lines? of therapy|trials?|feasib\w*|effectiveness|leak\w*|integrity|data quality|defects?|feeds?|measures?|spik\w*|surg\w*|worse|"
                   r"than expected|expected|quickly|how soon|busy|next (?:\d+ )?(?:days?|weeks?|months?|quarters?|years?)|heading|headed|getting|should we|"
                   r"bottlenecks?|what would|how many (?:nurses|staff|beds|providers|clinicians))\b", re.I)
# Wording the compiler accepts for methods that carry no examples of their own. Each is compiled once to learn its method.
CORE_EXAMPLES = ('Detect anomalies in monthly ED visits', 'Are admission rates out of control at any practice?',
                 'Time to readmission for heart failure patients', 'What predicts appointment no-shows?',
                 'What is our HCC capture rate?', 'Which patients have suspected uncoded HCCs?', 'What is the diagnostic delay for heart failure?',
                 'Which referral loops are not closed?', 'What was the effect of the care management program on readmissions?',
                 'Forecast ED visits for the next 4 weeks', 'What is the risk score of our population?', 'Which clinicians share the most patients?',
                 'Why are heart failure patients readmitted?', 'Which practices have unexpectedly high ED use?',
                 'Which patients have declining eGFR?', 'Who benefits most from care management outreach?',
                 'How many nurses do we need per hour in the ED using Erlang C?', 'How well calibrated are our admission forecasts?',
                 'Where do patients go after an ED visit?', 'Which patients are heading toward an admission?',
                 'What is the risk-standardized readmission rate by hospital?', 'How much should each clinic overbook?',
                 'Which providers bill unusually compared with their peers?', 'What are the lines of therapy for diabetes?',
                 'Which PCPs are truly high on ED use after adjustment?', 'Who should we call first this week?',
                 'What are our shared savings under MSSP ENHANCED with a benchmark of $950 PMPM?',
                 'Shared savings for 2029 at a $950 PMPM benchmark and 75% sharing rate')
SYSTEM = '''A user typed a question into a healthcare analytics tool. The tool has a menu of analysis methods. Each line of the
menu is: method id | what it answers | example questions written in the wording the tool understands.
Methods on the menu can look alike. Choose by what the question asks for, not by shared words: a method that measures
whether a step happened does not answer whether a program changed an outcome, and a method about how clinicians share
patients does not answer whether members have enough providers nearby.
Decide whether exactly one method on the menu answers the question. A plain count, list, rate, total or lookup that a
database query answers is NOT a method question: return {"method": null}. A question about one named patient's record
is NOT a method question. If no method clearly fits, return {"method": null}.
If one method fits, restate the question in the wording style of that method's examples so the tool understands it.
The restatement must ask exactly the same thing: keep every number, period, date, place, condition, drug, payer and
name from the question, add none, and do not add a scope or assumption the question does not state.
Return exactly one JSON object: {"method": "<id>", "restated": "<question>"} or {"method": null}.'''
OPTIONS = {'yes': 'The restated question asks exactly what the original asks: nothing is dropped, added or changed, and the described method answers it.',
           'no': 'Something is dropped, added or changed, or the described method does not answer what the original asks.'}
FIT = {'yes': 'Running the described method answers what the question asks.',
       'no': 'The described method answers something else, or the question asks for a plain count, list or lookup instead.'}
_TOKEN = re.compile(r'[A-Z]{2,}-[A-Z0-9]{4,}|\d[\d,.]*(?:-\d{2})?')
_MENU = {}


def _menu(stack):
    """-> {'text': the menu, 'ops': {method id: description}}. Built once from the application's own registry."""
    if _MENU: return _MENU
    from ..clinical import engine_suite, local_compiler
    from ..clinical.capability_registry import PRODUCT
    from ..clinical.source_profile import load as load_profile
    examples = {}
    for op, module in engine_suite.available().items():
        examples[op] = [t for t in getattr(module, 'TEMPLATES', ()) if '{' not in t][:2]
    profile, as_of = load_profile(stack.path), stack.retriever.meta['snapshot']['as_of']
    for text in CORE_EXAMPLES:
        try:
            graph, _ = local_compiler.compile(text, profile, as_of)
        except Exception:
            continue
        named = [n['operation'] for n in (graph or {}).get('nodes') or [] if n['operation'] not in NOT_METHODS]
        if named and len(examples.setdefault(named[-1], [])) < 3: examples[named[-1]].append(text)
    ops = {op: ' '.join(str(entry.get('use') or '').split()) for op, entry in PRODUCT.items() if op not in NOT_METHODS and examples.get(op)}
    uses = {op: ' '.join(str(entry.get('use') or '').split())[:400] for op, entry in PRODUCT.items()}
    _MENU.update(ops=ops, uses=uses, examples={op: examples[op] for op in ops},
                 text='\n'.join(f"{op} | {use[:170]} | {' / '.join(examples[op])}" for op, use in sorted(ops.items())))
    return _MENU


_WORD = re.compile(r'[a-z0-9]+')
_STOP = frozenset('a an the of to in on for by with and or is are was were be do does did our we us which what who how many much that this it at from as have has had '
                  'any each per vs their there than not no if will would should can could may patients patient members member'.split())


def _words(text):
    return [w[:-1] if len(w) > 4 and w.endswith('s') else w for w in _WORD.findall(text.lower()) if w not in _STOP]


def _index(stack):
    """Word weights over each method's description and examples (code only, built once)."""
    menu = _menu(stack)
    if 'index' in menu: return menu['index']
    import math
    from collections import Counter
    docs = {op: Counter(_words(menu['ops'][op] + ' ' + ' '.join(menu['examples'][op]))) for op in menu['ops']}
    df = Counter(w for c in docs.values() for w in c)
    idf = {w: math.log((1 + len(docs)) / (1 + n)) + 1.0 for w, n in df.items()}
    vectors = {}
    for op, c in docs.items():
        v = {w: (1 + math.log(tf)) * idf[w] for w, tf in c.items()}
        vectors[op] = (v, math.sqrt(sum(x * x for x in v.values())) or 1.0)
    menu['index'] = {'idf': idf, 'vectors': vectors}
    return menu['index']


def shortlist(stack, question, k=None, keep=()):
    """Code only: the k methods whose description and examples share the most distinctive words with the question,
    most similar first, always including `keep`. A model picks better from a handful of methods than from sixty."""
    import math
    from collections import Counter
    # Off by default (0 = the whole menu): on the second routing set a shortlist of 8 lost three restatements it had made from the full menu.
    k = int(os.environ.get('AGERE_TIERED_METHODS_SHORTLIST', '0')) if k is None else k
    ops = _menu(stack)['ops']
    if k <= 0 or k >= len(ops): return sorted(ops)
    ix = _index(stack)
    q = {w: (1 + math.log(tf)) * ix['idf'].get(w, 0.0) for w, tf in Counter(_words(question)).items()}
    norm = math.sqrt(sum(x * x for x in q.values())) or 1.0
    score = {op: sum(x * v.get(w, 0.0) for w, x in q.items()) / (norm * vn) for op, (v, vn) in ix['vectors'].items()}
    ranked = sorted(ops, key=lambda op: (-score[op], op))
    kept = [op for op in keep if op in ops]
    return kept + [op for op in ranked if op not in kept][:max(0, k - len(kept))]


def _system_one(prompt, options, s1_ledger):
    """-> (label or None, confidence, usd). An outage leaves the answer undecided."""
    from ..clinical import system_one
    from . import models
    usd, label, p = 0.0, None, 0.0
    for name in ('perplexity', 'jev'):
        try:
            out = system_one.choice(prompt, options, [], as_of=time.strftime('%Y-%m-%d'), provider=name, ledger=s1_ledger)
        except Exception:
            continue
        usd += out.get('cost_usd') or 0.0
        label = out.get('label'); p = float((out.get('probabilities') or {}).get(label, 0.0)) if label else 0.0
        if label and p >= models.S1_THRESHOLD: break
    return (label, p, usd) if label and p >= models.S1_THRESHOLD else (None, p, usd)


def fits(stack, question, named, s1_ledger=None):
    """System One, as a choice among the method the compiler named, its nearest neighbours on the menu and "none of
    these": is the named method the one that answers the question? -> (False only when it confidently picks another
    option, step). step['other'] names the neighbour it picked, so the caller can try that method instead."""
    started = time.monotonic()
    label, p, usd, other = None, 0.0, 0.0, None
    try:
        menu = _menu(stack)
        chosen = named[-1]
        near = [op for op in shortlist(stack, question, k=int(os.environ.get('AGERE_TIERED_METHODS_NEIGHBOURS', '3')) + 1) if op not in named][:3] \
            if os.environ.get('AGERE_TIERED_METHODS_NEIGHBOURS', '3') != '0' else []
        options = {chosen: (menu['uses'].get(chosen) or chosen.replace('_', ' '))[:400]}
        for op in near: options[op] = menu['uses'].get(op, op.replace('_', ' '))[:400]
        options['none'] = 'None of these methods answers the question, or it asks for a plain count, list or lookup instead.'
        label, p, usd = _system_one(f'Question: {question}\nWhich one of these analysis methods answers what the question asks?', options, s1_ledger)
        if label and label not in (chosen, 'none'): other = label
    except Exception:
        label, p, usd = None, 0.0, 0.0
    ok = label is None or label == named[-1]
    return ok, {'tier': 'methods', 'model': 'system_one' if usd or label else None, 'usd': usd, 'tokens': 0, 'seconds': round(time.monotonic() - started, 2),
                'method': 'system_one_method_fit', 'label': label, 'confidence': round(p, 4), **({'other': other} if other else {})}


def worth_asking(stack, question, bottles=None, redirected=False):
    """Code only: the compiler declined, no recipe matches and no one patient is named: worth one small-model check."""
    from . import recipes
    if not enabled() or os.environ.get('AGERE_TIERED_METHODS_ASSIST', 'on') != 'on': return False
    if getattr(stack, 'path', None) is None or not hasattr(stack, 'run_graph'): return False
    if plan(stack, question) is not None and not redirected: return False
    # AGERE_TIERED_METHODS_ASSIST_GATE=words limits this to questions that use the methods' vocabulary (WORDS). The first
    # routing run (2026-11-14) lost 5 of 28 method questions to that gate, so by default every question the cheap tiers
    # leave unanswered gets the one small-model check before the frontier model is asked to write a query.
    if os.environ.get('AGERE_TIERED_METHODS_ASSIST_GATE', 'all') == 'words' and not WORDS.search(question): return False
    if re.search(r'\b(?:SYN|WHP|P)-[A-Z0-9-]{3,}\b', question): return False  # one named patient: the patient recipes and the takeover rung
    if recipes.match(question) is not None: return False
    return not (bottles is not None and bottles.match(question, statuses=('promoted',)) is not None)


def assisted(stack, question, s1_ledger=None, prefer=None):
    """-> (restated question or None, {'method','usd','tokens','seconds', ...}). The restated question names a method by code."""
    from . import models
    started = time.monotonic()
    def done(found, method, usd=0.0, tokens=0, **extra):
        return found, {'tier': 'methods', 'model': models.SLM if tokens else None, 'method': method, 'usd': usd, 'tokens': tokens,
                       'seconds': round(time.monotonic() - started, 2), **extra}
    menu = _menu(stack)
    # System One preferred a neighbouring method to the one the compiler named: the small model chooses among that
    # method and its nearest neighbours only. Otherwise it sees the whole menu.
    listed = shortlist(stack, question, k=4, keep=(prefer,)) if prefer else shortlist(stack, question)
    shown = menu['text'] if len(listed) >= len(menu['ops']) else '\n'.join(
        f"{op} | {menu['ops'][op][:500]} | {' / '.join(menu['examples'][op])}" for op in listed)
    ask, usd, tokens, op, restated, found = 'Menu:\n' + shown + '\n\nQuestion: ' + question, 0.0, 0, None, '', None
    for attempt in (1, 2):
        text, step = models._claude(models.SLM, SYSTEM, ask)
        usd += step['usd']; tokens += step['tokens']
        try:
            got = models._json(text)
        except ValueError:
            return done(None, 'small_model_unusable', usd, tokens)
        op, restated = (got.get('method'), ' '.join(str(got.get('restated') or '').split())) if isinstance(got, dict) else (None, '')
        if not op: return done(None, 'not_a_method_question', usd, tokens)
        if op not in listed or len(restated) < 8: return done(None, 'pick_unusable', usd, tokens, picked=str(op)[:60])
        invented = sorted({t.rstrip('.,') for t in _TOKEN.findall(restated)} - {t.rstrip('.,') for t in _TOKEN.findall(question)})
        if invented: return done(None, 'restatement_adds_values', usd, tokens, picked=op, restated=restated, invented=invented[:6])
        found = plan(stack, restated)
        if found is not None and op in found[2]: break
        if attempt == 1:  # the tool did not read that wording as the picked method: one more try, closer to the method's own examples
            ask += (f'\n\nYour restatement "{restated}" was not understood by the tool as method {op}. Restate the question again, staying as close as you can to the '
                    f"wording of that method's examples: {' / '.join(menu['examples'][op])}. Keep every number, period and name from the question and add none. "
                    'If no method really fits, return {"method": null}.')
    if found is None or op not in found[2]:
        return done(None, 'compiler_did_not_name_the_method', usd, tokens, picked=op, restated=restated, named=found[2] if found else None)
    step = {'usd': usd, 'tokens': tokens}
    label, p, usd = _system_one(f"Original question: {question}\nRestated question: {restated}\nMethod: {op}: {menu['ops'][op][:400]}\n"
                                'Does the restated question ask exactly what the original asks, and does the method answer it?', OPTIONS, s1_ledger)
    if label != 'yes':
        return done(None, 'restatement_not_confirmed', step['usd'] + usd, step['tokens'], picked=op, restated=restated, confidence=round(p, 4))
    return done(restated, 'small_model_restated_confirmed_by_system_one', step['usd'] + usd, step['tokens'], picked=op, confidence=round(p, 4))
