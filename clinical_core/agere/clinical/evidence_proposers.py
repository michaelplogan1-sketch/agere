"""Evidence, retrieval and reader components as CHECKED proposers for ordinary questions.

Same contract as economical_planner.Ladder rungs: each proposer returns (graph, receipt) or None. The graph is an
analysis_graph plan executable by stack.run_graph(graph, question=...); the cascade (proposers.py) literal-checks it,
executes it read-only and checks the output shape, so a proposal here never answers by itself.
  * retrieval_tier: what notes/documents/charts say -> cohort (the named patients, a saved population or everyone) ->
    'retrieve' (warehouse_retriever: FTS5 BM25 + MiniLM + RRF + BGE rerank). A free preflight confirms the primary ranker
    finds passages; when it finds none, granite_hybrid_retrieval (BM25 + Granite RRF over the patients' received text)
    proposes the passages and their distinctive terms expand the query (pseudo-relevance feedback). Patient-scoped
    questions also get question_context_selection; the learned schema reranker decides wording that is only weakly
    narrative ("why", "reason") by whether it ranks the notes/documents streams first.
  * review_tier: patient-scoped evidence questions (does the chart support X, why, intolerant, eligible) -> cohort of the
    named patient(s) -> 'retrieve' -> 'review'. review_patient() reads free first: semantic student heads, then the
    reader_comparison roles (cohort lexical, windowed MiniLM, Kev, d1) or the semantic statement reader's native Kev/d1;
    only an undecided question escalates, and only with stack.allow_live, to Jev/TypeSafe (Stack.review), a matching
    context-pass bundle, Perplexity Decisions, and last the Jev -> Claude gateway cascade.
Both return None at once (no I/O) when the question is not of their kind, or asks for a count/value/table/patient list
(ranked passages are examples, never a denominator, so the output check would reject them anyway).
"""
import json
import re
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

VERSION = 'evidence-proposers/1'
ROOT = Path(__file__).resolve().parents[3]

LOCAL_READERS = ('student_lexical', 'student_minilm', 'cohort_windowed_minilm', 'kev', 'd1', 'medgemma')  # free, cheapest first
PAID_READERS = ('jev', 'perplexity')
REVIEW_ORDER = LOCAL_READERS + PAID_READERS
RETRIEVAL_COMPONENTS = ('warehouse_retriever', 'fts5_index', 'minilm_encoder', 'bge_reranker')
READER_COMPONENTS = {'student_lexical': 'semantic_student_heads', 'student_minilm': 'semantic_student_heads',
                     'cohort_windowed_minilm': 'windowed_student', 'kev': 'kev_reader', 'd1': 'd1_reader',
                     'jev': 'jev_typesafe_review', 'perplexity': 'perplexity_decisions_review', 'medgemma': 'medgemma_reader'}

NARRATIVE = re.compile(
    r"\b(?:notes?|documents?|documented|documentation|charts?|charted|charting|narratives?|free[- ]?text|passages?|"
    r"mentions?|mentioned|mentioning|says?|said|wrote|written|writes|quotes?|quoted|excerpts?|text|transcripts?|"
    r"discharge summar(?:y|ies)|progress notes?|clinician comments?|reasons? (?:given|documented|recorded|noted))\b", re.I)
WEAK_NARRATIVE = re.compile(r'\b(?:why|reasons?|explain|context|describe|history of|what happened)\b', re.I)
EVIDENCE = re.compile(
    r'\b(?:support(?:s|ed|ing)?|evidence|establish(?:es|ed)?|why|reasons?|document(?:s|ed)?|indicat(?:es?|ed|ion)|'
    r'intoleran\w*|tolerat\w*|contraindicat\w*|eligib\w*|qualif\w*|declin\w*|refus\w*|allerg\w*|adheren\w*|'
    r'side effects?|adverse)\b', re.I)
ASSERTION = re.compile(r'^\s*(?:does|do|did|is|are|was|were|has|have|had|can|could|should|why)\b', re.I)
PATIENT_TOKEN = re.compile(r'(?<![\w-])[A-Za-z]{0,12}[-_]?\d[\w-]{0,40}(?![\w-])')
STOP = set('a an and are as at be by did do does for from had has have in is it its of on or patient patients that the '
           'their there this to was were what which who whom why with chart charts note notes document documents say says '
           'said show shows mention mentions mentioned any about documented text'.split())
RELATIVE = re.compile(r'\b(?:last|past|previous|prior|within(?: the)?(?: last| past)?)\s+(\d+|one|two|three|six|twelve)?\s*'
                      r'(days?|weeks?|months?|years?)\b', re.I)
WORDS = {'one': 1, 'two': 2, 'three': 3, 'six': 6, 'twelve': 12}


# -- question kind -------------------------------------------------------------------------------------------------
def _not_examples(question):
    """Counts, values, tables and patient lists are not answered by ranked examples."""
    from .analysis_graph import requested_shape
    return requested_shape(question) is not None


def narrative_question(question, stack=None):
    if _not_examples(question): return False
    if NARRATIVE.search(question): return True
    if WEAK_NARRATIVE.search(question) and stack is not None:
        return _schema_points_to_text(stack, question)
    return False


def evidence_question(question):
    return not _not_examples(question) and bool(EVIDENCE.search(question))


def _schema_points_to_text(stack, question):
    """Learned schema reranker: a weakly narrative question is about documents when notes/documents rank first."""
    try:
        from .schema_rerank import schema_slice
        from .source_profile import load
        streams, _ = schema_slice(question, load(stack.path), path=stack.path)
    except Exception:  # noqa: BLE001 - no profile/model (fixtures): the reranker abstains
        return False
    return any(s in ('notes', 'documents') for s in list(streams)[:3])


# -- shared plan pieces ----------------------------------------------------------------------------------------------
def named_patients(stack, question, limit=20):
    """Exact roster IDs literally present in the question (case-sensitive), in question order."""
    tokens = list(dict.fromkeys(m.group().rstrip('.,;:?') for m in PATIENT_TOKEN.finditer(question)))
    if not tokens: return []
    db = getattr(getattr(stack, 'retriever', None), 'db', None)
    owned = db is None
    if owned: db = sqlite3.connect(Path(stack.path)/'stack.sqlite3')
    try:
        found = {r[0] for r in db.execute(f"SELECT id FROM patients WHERE id IN ({','.join('?' * len(tokens))})", tokens)}
    finally:
        if owned: db.close()
    return [t for t in tokens if t in found][:limit]


def population_ids(population):
    ids = population.get('patient_ids') if isinstance(population, dict) else population
    return sorted({str(p) for p in ids}) if isinstance(ids, (list, tuple, set)) and ids else None


def window(question, as_of):
    """The question's own date restriction on source-event dates, or the full observed snapshot."""
    full = {'basis': 'all_observed', 'start': '', 'end': ''}
    end = date.fromisoformat(as_of[:10])
    m = RELATIVE.search(question)
    if m:
        n = int(WORDS.get((m.group(1) or 'one').lower(), m.group(1) or 1))
        unit = m.group(2).lower().rstrip('s')
        days = {'day': 1, 'week': 7, 'month': 30.4375, 'year': 365.25}[unit] * n
        return {'basis': 'source_event', 'start': (end - timedelta(days=round(days))).isoformat(), 'end': end.isoformat()}
    m = re.search(r'\bsince (\d{4}-\d{2}-\d{2})\b', question)
    if m and m.group(1) <= end.isoformat(): return {'basis': 'source_event', 'start': m.group(1), 'end': end.isoformat()}
    m = re.search(r'\bin (\d{4})\b', question)
    if m and f'{m.group(1)}-01-01' <= end.isoformat():
        return {'basis': 'source_event', 'start': f'{m.group(1)}-01-01', 'end': min(f'{m.group(1)}-12-31', end.isoformat())}
    return full


def cohort_node(ids, purpose):
    from .cohorts import empty
    spec = empty()
    atoms = [{'kind': 'patient', 'field': 'patient_id', 'op': 'eq', 'value': p} for p in (ids or [])]
    if len(atoms) == 1: spec['all'] = atoms
    elif atoms: spec['any'] = atoms
    return {'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': spec}, 'purpose': purpose}


def retrieve_node(query, win, purpose):
    return {'id': 'passages', 'operation': 'retrieve', 'inputs': ['population'],
            'parameters': {'query': query[:1500], 'analysis_window': win}, 'purpose': purpose}


def graph_of(question, nodes):
    return {'interpretation': question[:2000], 'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': nodes}


def as_of(stack):
    return stack.retriever.meta['snapshot']['as_of']


def membership(stack, ids):
    """The exact membership the graph's cohort node yields, so the preflight search shares the executed search's cache."""
    if ids: return sorted(ids)
    return sorted(r[0] for r in stack.retriever.db.execute('SELECT id FROM patients'))


def receipt(route, started, components, **extra):
    return {'route': route, 'model': None, 'new_paid_api_calls': 0, 'frontier_called': False, 'steps': [],
            'components': list(dict.fromkeys(components)), 'evidence_proposers': VERSION,
            'elapsed_seconds': round(time.perf_counter() - started, 3), **extra}


# -- granite fallback ranker ---------------------------------------------------------------------------------------
def patient_notes(stack, patient):
    from ..narrative_sources import text_fields
    notes = []
    for rid, raw in stack.retriever.db.execute('SELECT id,payload FROM records WHERE patient=?', (patient,)):
        r = json.loads(raw)
        if not r.get('event_date'): continue
        for part in text_fields(r):
            notes.append({'patient_id': patient, 'note_id': json.dumps([rid, part['field']], separators=(',', ':')),
                          'record_id': rid, 'source_table': r.get('source_table'), 'source_field': part['field'],
                          'date': r['event_date'][:10], 'available_date': (r.get('available_date') or '')[:10] or None,
                          'text': part['text']})
    return notes


def granite_fallback(stack, question, ids, cutoff, *, encoder=None, top_k=6):
    """BM25 + Granite RRF over the named patients' received text. -> (hits, receipt) ; ([], reason) when unavailable."""
    if not ids or len(ids) > 3: return [], {'status': 'skipped', 'reason': 'Granite fallback runs per patient (1-3 named patients)'}
    from ...hybrid_retrieval import retrieve
    owned = encoder is None
    if owned:
        path = ROOT/'models/granite-english-r2'
        if not path.exists(): return [], {'status': 'unavailable', 'reason': 'Granite R2 model not installed'}
        from ...local_encoders import LocalEncoder
        encoder = LocalEncoder(path, pooling='cls', cache=Path(stack.path)/'model-cache.sqlite3')
    try:
        hits = []
        for pid in ids:
            packet = retrieve(encoder, pid, patient_notes(stack, pid), question, cutoff[:10], top_k=top_k)
            hits.extend(e for e in packet['evidence'] if e.get('lexical_score', 0) > 0 or e.get('dense_similarity', 0) > .3)
        return hits, {'status': 'ran', 'method': 'BM25 + Granite cosine RRF', 'hits': len(hits),
                      'encoder_identity': getattr(encoder, 'identity', None)}
    finally:
        if owned: encoder.close()


def expand(question, hits, limit=12):
    """Pseudo-relevance feedback: the fallback passages' most frequent distinctive terms join the original question."""
    asked = set(re.findall(r'[a-z0-9]{3,}', question.lower()))
    counts = {}
    for h in hits:
        for w in re.findall(r'[a-z][a-z0-9]{3,}', h['text'].lower()):
            if w not in STOP and w not in asked: counts[w] = counts.get(w, 0) + 1
    terms = [w for w, _ in sorted(counts.items(), key=lambda x: (-x[1], x[0]))[:limit]]
    return (question + (' ' + ' '.join(terms) if terms else ''))[:1500], terms


# -- retrieval proposer --------------------------------------------------------------------------------------------
def retrieval_tier(stack, question, population=None, *, granite_encoder=None):
    """What do the notes/documents/charts say -> cohort -> retrieve. None when not a narrative question or nothing found."""
    if not narrative_question(question, stack): return None
    started, cutoff = time.perf_counter(), as_of(stack)
    named = named_patients(stack, question)
    if named and len(named) <= 3 and ASSERTION.match(question) and evidence_question(question):
        return None  # "does the chart support X for P-1" is an assertion for review_tier, not a passage search
    ids = named or population_ids(population)
    if ids is not None and len(ids) > 20: ids = None  # cohort predicates are bounded; rank across the whole roster
    win = window(question, cutoff)
    components = list(RETRIEVAL_COMPONENTS)
    members = membership(stack, ids)
    found = stack.retriever.search(question, patient_ids=members, analysis_window=win)
    query, fallback = question, None
    if not found.get('evidence'):
        hits, fallback = granite_fallback(stack, question, ids, cutoff, encoder=granite_encoder)
        components.append('granite_hybrid_retrieval')
        if not hits: return None
        query, fallback['expansion_terms'] = expand(question, hits)
        fallback['passages'] = [{'record_id': h['record_id'], 'field': h['source_field'], 'start': h['start'], 'end': h['end'],
                                 'text': h['text'][:400]} for h in hits[:4]]
        found = stack.retriever.search(query, patient_ids=members, analysis_window=win)
        if not found.get('evidence'): return None
    context = None
    if ids and len(ids) == 1:
        components.append('question_context_selection')
        try:
            ctx = stack.prepare_question_context(ids[0], question)
            context = {k: ctx.get(k) for k in ('status', 'context_id', 'selected_categories', 'retrieved_record_ids',
                                                'connected_record_ids', 'scope_sufficiency')}
        except Exception as exc:  # noqa: BLE001 - context selection is supporting detail; retrieval still answers
            context = {'status': 'failed', 'reason': f'{type(exc).__name__}: {exc}'[:300]}
    scope = f"patient {ids[0]}" if ids and len(ids) == 1 else f"{len(ids)} selected patients" if ids else 'all observed patients'
    nodes = [cohort_node(ids, f'Source passages are ranked within {scope}'),
             retrieve_node(query, win, 'Ranked source passages answering what the received text says (examples, not a denominator)')]
    graph = graph_of(question, nodes)
    from .analysis_graph import validate
    validate(graph, cutoff)
    from .ocr_passages import cite_evidence  # scanned/faxed passages cited by page and bbox
    ocr = cite_evidence(stack.path, found.get('evidence'))
    if ocr: components.append('ocr_passages')
    return graph, receipt('evidence_retrieval', started, components, scope=scope, granite_fallback=fallback, ocr_citations=ocr,
                          question_context=context, preflight={'passages': len(found['evidence']),
                                                               'method': found.get('method'), 'cache_hit': found.get('cache_hit')})


# -- reader cascade --------------------------------------------------------------------------------------------------
def targets(question, evidence, limit=4):
    """Reader targets: the question's own term inside each top passage (else the passage span), with its concept."""
    terms = [w for w in re.findall(r'[A-Za-z][A-Za-z0-9-]{2,}', question) if w.lower() not in STOP]
    out = []
    for row in evidence[:limit]:
        s, text = row['source'], row['text']
        low = text.lower()
        hit = next(((low.index(t.lower()), t) for t in sorted(terms, key=len, reverse=True) if t.lower() in low), None)
        a, b, concept = (s['start'] + hit[0], s['start'] + hit[0] + len(hit[1]), hit[1].lower()) if hit else \
            (s['start'], s.get('end', s['start'] + len(text)), ' '.join(terms)[:150] or 'requested assertion')
        out.append({'patient_id': s['patient_id'], 'record_id': s['record_id'], 'field': s['field'],
                    'start': a, 'end': b, 'concept': concept[:150]})
    return out


def verdict(reading):
    """yes / no / unknown from one statement reading (students must pass their own development threshold)."""
    if not reading or reading.get('status') == 'failed': return 'unknown'
    student = reading.get('student') or {}
    if 'passes_development_threshold' in student and not student['passes_development_threshold']: return 'unknown'
    label = ((reading.get('reading') or {}).get('labels') or reading.get('labels') or {}).get('statement')
    return {'patient_affirmed': 'yes', 'patient_negated': 'no'}.get(label, 'unknown')


def combined(verdicts):
    decided = {v for v in verdicts if v != 'unknown'}
    return decided.pop() if len(decided) == 1 else 'conflict' if decided else 'unknown'


def _comparison_cohort(stack):
    from . import reader_comparison
    try:
        listed = reader_comparison.availability(stack.path, {})['cohorts']
    except Exception:  # noqa: BLE001 - no learning cohorts registered
        return None
    return next((c['cohort_id'] for c in listed if c['status'] == 'available'), None)


def read_locally(stack, question, patient, evidence, providers=LOCAL_READERS):
    """Free readers in order; stop at the first provider whose readings decide. -> {'verdict','readings','components'}"""
    readings, used = [], []
    goal = targets(question, evidence)
    if not goal: return {'verdict': 'unknown', 'readings': [], 'components': [], 'reason': 'no retrieved passages'}
    cohort = None
    native = [p for p in ('cohort_windowed_minilm', 'kev', 'd1') if p in providers]
    for provider in [p for p in ('student_lexical', 'student_minilm') if p in providers] + (['comparison'] if native else []):
        verdicts = []
        for target in goal:
            started = time.perf_counter()
            try:
                if provider == 'comparison':
                    cohort = cohort if cohort is not None else (_comparison_cohort(stack) or '')
                    if cohort and 'cohort_windowed_minilm' in native:
                        from . import reader_comparison
                        roles = ['cohort_lexical', 'cohort_windowed_minilm'] + [r for r in ('kev', 'd1') if r in native]
                        done = reader_comparison.prepare(stack.path, {'target': target, 'cohort_id': cohort, 'roles': roles})
                        used += ['reader_comparison', 'windowed_student'] + [READER_COMPONENTS[r] for r in roles if r in READER_COMPONENTS]
                        for role, v in done['comparison']['variants'].items():
                            v = {**v, 'labels': v.get('labels')}
                            readings.append({'provider': role, 'target': target, 'verdict': verdict(v), 'status': v['status'],
                                             'reading_id': v.get('reading_id'), 'labels': v.get('labels'), 'reason': v.get('reason')})
                            verdicts.append(verdict(v))
                        continue
                    from .semantic_reader import read
                    for role in [r for r in ('kev', 'd1') if r in native]:  # semantic statement reader, native models
                        used += ['semantic_statement_reader', READER_COMPONENTS[role]]
                        try:
                            r = read(stack.path, target, role, allow_live=False)
                            readings.append({'provider': role, 'target': target, 'verdict': verdict(r), 'status': 'completed',
                                             'reading_id': r.get('id'), 'labels': r['reading']['labels']})
                            verdicts.append(verdict(r))
                        except Exception as exc:  # noqa: BLE001 - a missing native model never stops the others
                            readings.append({'provider': role, 'target': target, 'verdict': 'unknown', 'status': 'failed',
                                             'reason': f'{type(exc).__name__}: {exc}'[:300]})
                    continue
                used.append(READER_COMPONENTS[provider])
                r = stack.refine_statement(target, provider)
                readings.append({'provider': provider, 'target': target, 'verdict': verdict(r), 'status': 'completed',
                                 'reading_id': r.get('id'), 'labels': (r.get('reading') or {}).get('labels'),
                                 'seconds': round(time.perf_counter() - started, 3)})
                verdicts.append(verdict(r))
            except Exception as exc:  # noqa: BLE001 - heal, don't block: the next reader tries
                readings.append({'provider': provider, 'target': target, 'verdict': 'unknown', 'status': 'failed',
                                 'reason': f'{type(exc).__name__}: {exc}'[:300]})
        if combined(verdicts) in ('yes', 'no'):
            return {'verdict': combined(verdicts), 'decided_by': provider, 'readings': readings, 'components': list(dict.fromkeys(used))}
    return {'verdict': combined([r['verdict'] for r in readings]), 'decided_by': None, 'readings': readings,
            'components': list(dict.fromkeys(used))}


def paid_verdict(result):
    answers = ((result or {}).get('model_proposal') or result or {}).get('answers') or []
    labels = [a['label'] for a in answers if a.get('label') in ('yes', 'no') and not a.get('unsupported_decision')]
    if labels: return combined(labels)
    finding = (result or {}).get('finding') or (result or {}).get('label')
    return {'affirmed': 'yes', 'negated': 'no', 'yes': 'yes', 'no': 'no'}.get(finding, 'unknown')


def context_bundle(stack, question, patient):
    """An existing context-pass bundle for this patient whose own question matches this one (content-word Jaccard >= .5)."""
    path = Path(stack.path)/'context-passes.sqlite3'
    if not path.exists(): return None
    import zlib
    asked = set(re.findall(r'[a-z0-9]{3,}', question.lower())) - STOP
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        for bid, raw in db.execute('SELECT id,payload FROM bundles WHERE patient=?', (patient,)):
            q = set(re.findall(r'[a-z0-9]{3,}', str(json.loads(zlib.decompress(raw))['parameters'].get('question', '')).lower())) - STOP
            if asked and q and len(asked & q) / len(asked | q) >= .5: return bid
    finally:
        db.close()
    return None


def gateway_cascade(stack, question, patient, evidence):
    """Jev, then Claude when Jev is uncertain (PersistentGateway.cascade). Paid; allow_live only."""
    from ..gateway import BudgetLedger, PersistentGateway, default_ledger_path
    from ...providers import EvidenceTask
    rows = [{'id': r['id'], 'text': r['text'], 'date': r['source']['event_date'][:10], 'note_id': r['source']['record_id']}
            for r in evidence if r['source'].get('event_date')]
    task = EvidenceTask('For this patient only, does the submitted evidence establish: ' + question, as_of(stack)[:10],
                        {'yes': 'Explicitly established', 'no': 'Explicitly contradicted',
                         'unknown': 'Insufficient, contradictory or unresolved'}, rows)
    ledger = BudgetLedger(default_ledger_path())
    try:
        return PersistentGateway(ledger, allow_live=True).cascade(task)
    finally:
        ledger.close()


def review_patient(stack, question, patient, providers=REVIEW_ORDER):
    """Free readers first; escalate an undecided question to paid readers only with stack.allow_live. Never raises for a
    single reader failing; paid providers are skipped (and say so) without allow_live."""
    started = time.perf_counter()
    evidence = stack.retriever.search(question, patient=patient, limit=8)
    local = read_locally(stack, question, patient, evidence['evidence'], [p for p in providers if p in LOCAL_READERS])
    steps, paid_calls, components = [], 0, ['warehouse_retriever'] + local['components']
    outcome, decided_by = local['verdict'], local.get('decided_by')
    if outcome not in ('yes', 'no') and 'medgemma' in providers and evidence['evidence']:
        try:  # local MedGemma reads the same passages before any paid reader (skipped when its weights are absent)
            from . import document_readers
            if any(r['provider'] == 'medgemma' and r['available'] for r in document_readers.availability()):
                result = document_readers.review(question, evidence, 'medgemma')
                components.append('medgemma_reader')
                v = paid_verdict(result)
                steps.append({'provider': 'medgemma', 'status': 'completed', 'verdict': v, 'result': result})
                if v in ('yes', 'no'): outcome, decided_by = v, 'medgemma'
        except Exception as exc:  # noqa: BLE001 - a local model failing never stops the paid readers
            steps.append({'provider': 'medgemma', 'status': 'failed', 'reason': f'{type(exc).__name__}: {exc}'[:300]})
    paid = [p for p in providers if p in PAID_READERS]
    if outcome not in ('yes', 'no') and paid:
        if not getattr(stack, 'allow_live', False):
            steps.append({'provider': ', '.join(paid), 'status': 'skipped', 'reason': 'paid readers need allow_live'})
        else:
            plan = [('jev', lambda: stack.review(question, patient, 'jev'))] if 'jev' in paid else []
            bundle = context_bundle(stack, question, patient) if 'jev' in paid else None
            if bundle: plan.append(('context_pass_review', lambda: stack.review_passes(bundle, 'jev', 8)))
            if 'perplexity' in paid: plan.append(('perplexity', lambda: stack.review(question, patient, 'perplexity')))
            if 'jev' in paid and evidence['evidence']:
                plan.append(('gateway_jev_claude_cascade', lambda: gateway_cascade(stack, question, patient, evidence['evidence'])))
            for name, call in plan:
                components += {'jev': ['jev_typesafe_review', 'persistent_gateway'], 'perplexity': ['perplexity_decisions_review'],
                               'context_pass_review': ['context_pass_review'],
                               'gateway_jev_claude_cascade': ['gateway_jev_claude_cascade', 'persistent_gateway']}[name]
                try:
                    result = call()
                except Exception as exc:  # noqa: BLE001 - one provider failing never stops the next
                    steps.append({'provider': name, 'status': 'failed', 'reason': f'{type(exc).__name__}: {exc}'[:300]}); continue
                if name == 'gateway_jev_claude_cascade':
                    paid_calls += 0 if result.get('cache_hit') else 1 + (result.get('route') == 'frontier')
                else:
                    paid_calls += int(result.get('new_paid_api_calls', 0) or 0)
                v = paid_verdict(result)
                steps.append({'provider': name, 'status': 'completed', 'verdict': v, 'result': result})
                if v in ('yes', 'no'): outcome, decided_by = v, name; break
    return {'synthetic': True, 'question': question, 'patient_id': patient, 'verdict': outcome, 'decided_by': decided_by,
            'retrieval': evidence, 'local_readings': local['readings'], 'paid_steps': steps,
            'components': list(dict.fromkeys(components)), 'new_paid_api_calls': paid_calls,
            'elapsed_seconds': round(time.perf_counter() - started, 3), 'clinical_decision_authority': False,
            'complete_evidence_guarantee': False,
            'scope': 'Bounded ranked passages read free first; paid readers only for an undecided question with allow_live.'}


# -- review proposer -------------------------------------------------------------------------------------------------
def review_node_accepts(providers, cutoff):
    """Whether analysis_graph's 'review' node accepts these providers (True once local readers are registered there)."""
    from .analysis_graph import validate
    probe = graph_of('probe', [cohort_node([], 'probe'), {'id': 'review', 'operation': 'review', 'inputs': ['population'],
             'parameters': {'providers': list(providers), 'question': 'probe', 'maximum_patients': 1}, 'purpose': 'probe'}])
    try:
        validate(probe, cutoff); return True
    except ValueError:
        return False


def review_tier(stack, question, population=None):
    """Does the chart support X / why / intolerant / eligible, for named patients -> cohort -> retrieve -> review."""
    if not evidence_question(question): return None
    ids = named_patients(stack, question, limit=4) or population_ids(population)
    if not ids or len(ids) > 3: return None  # patient-scoped review reads one to three named charts
    started, cutoff = time.perf_counter(), as_of(stack)
    live = bool(getattr(stack, 'allow_live', False))
    providers = [p for p in REVIEW_ORDER if live or p in LOCAL_READERS]
    scope = f'patient {ids[0]}' if len(ids) == 1 else f'patients {", ".join(ids)}'
    nodes = [cohort_node(ids, f'The chart of {scope}'),
             retrieve_node(question, window(question, cutoff), 'Ranked source passages bearing on the asked assertion')]
    extra, components = {}, ['engine_review', 'warehouse_retriever', 'fts5_index', 'bge_reranker']
    review = {'id': 'review', 'operation': 'review', 'inputs': ['population'], 'purpose':
              'Read the passages free first (students, windowed MiniLM, Kev, d1); escalate undecided charts to Jev, then Perplexity',
              'parameters': {'providers': providers, 'question': question[:1000], 'maximum_patients': len(ids)}}
    if review_node_accepts(providers, cutoff):
        nodes.append(review)  # the engine op runs the whole reader cascade (review_patient)
        components += [READER_COMPONENTS[p] for p in providers] + ['reader_comparison', 'semantic_statement_reader']
    else:
        # Today's 'review' op takes only jev/perplexity and needs allow_live: read free here, put the readings in the
        # receipt, and add the paid review node only for charts the free readers left undecided.
        local = {pid: read_locally(stack, question, pid, stack.retriever.search(question, patient=pid, limit=8)['evidence'])
                 for pid in ids}
        extra['local_review'] = local
        components += [c for r in local.values() for c in r['components']]
        undecided = [pid for pid, r in local.items() if r['verdict'] not in ('yes', 'no')]
        extra['verdicts'] = {pid: r['verdict'] for pid, r in local.items()}
        if live and undecided:
            nodes.append({**cohort_node(undecided, f'Charts the free readers left undecided ({", ".join(undecided)})'), 'id': 'undecided'})
            nodes.append({**review, 'inputs': ['undecided'], 'parameters': {'providers': list(PAID_READERS),
                          'question': question[:1000], 'maximum_patients': len(undecided)}})
            components += ['jev_typesafe_review', 'persistent_gateway', 'perplexity_decisions_review']
    graph = graph_of(question, nodes)
    from .analysis_graph import validate
    validate(graph, cutoff)
    return graph, receipt('evidence_review', started, components, scope=scope, providers=providers, **extra)
