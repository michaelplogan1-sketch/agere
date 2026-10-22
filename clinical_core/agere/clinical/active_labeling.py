"""Active learning for the frontier labeler: spend each distillation dollar on units the local stack is unsure about, and on a
diverse spread of them, instead of labelling everything.

select(items, budget, uncertainty=..., distance=...) -> chosen items, each with a 'why' (uncertain | diverse | audit):
  * uncertainty u in [0,1] comes from a FREE signal (router margin, rule-vs-NER disagreement, local-SQL vs semantic-layer
    disagreement) -- never from a paid call;
  * greedy farthest-first weighted by uncertainty picks the item maximising  u * (floor + min distance to those already chosen),
    so near-duplicates of a chosen unit are worth little even when uncertain;
  * a stable-hash AUDIT slice (default 10% of the budget) is drawn from the CONFIDENT units so agreement is still measured and
    the uncertainty signal is itself checked (a signal that never fires on errors shows up there);
  * units already labelled are skipped; nothing is dropped from the pool -- unselected units stay available for a later round.
Used by tools/notes_extraction/label.py (--budget) and tools/bottling/harvest.py (--budget).
"""
import hashlib
import math
import re

VERSION = 'active-labeling/1'
AUDIT_SHARE = 0.10
DIVERSITY_FLOOR = 0.05


def entropy(ps):
    ps = [p for p in ps if p > 0]
    z = sum(ps)
    return -sum(p / z * math.log(p / z) for p in ps) / math.log(len(ps)) if len(ps) > 1 else 0.0


def margin_uncertainty(ps):
    """1 - (top1 - top2): 1 when two classes tie, 0 when one class owns the mass."""
    top = sorted(ps, reverse=True)[:2] + [0.0]
    return 1.0 - (top[0] - top[1])


def tokens(text):
    return set(re.findall(r'[a-z0-9]+', str(text).lower()))


def jaccard_distance(a, b):
    ta, tb = tokens(a), tokens(b)
    return 1.0 - len(ta & tb) / max(1, len(ta | tb))


def _h(text):
    return int(hashlib.sha256(str(text).encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def select(items, budget, uncertainty, *, text=lambda x: x['text'], ident=lambda x: x['unit_id'], distance=jaccard_distance,
           labelled=(), audit_share=AUDIT_SHARE):
    """items: list; uncertainty: callable(item) -> [0,1] (free signals only). -> (chosen, report)."""
    done = set(labelled)
    pool = [x for x in items if ident(x) not in done]
    budget = max(0, min(int(budget), len(pool)))
    n_audit = min(int(round(budget * audit_share)), budget) if budget else 0
    u = {ident(x): float(min(1.0, max(0.0, uncertainty(x)))) for x in pool}
    chosen, mind = [], {ident(x): 1.0 for x in pool}
    for _ in range(budget - n_audit):
        best, best_score = None, -1.0
        for x in pool:
            i = ident(x)
            if i in mind and (s := (u[i] + 1e-6) * (DIVERSITY_FLOOR + mind[i])) > best_score:
                best, best_score = x, s
        if best is None: break
        bi = ident(best)
        why = 'uncertain' if u[bi] >= 0.5 else 'diverse'
        chosen.append({**best, 'why': why, 'active_score': round(best_score, 4), 'uncertainty': round(u[bi], 3)})
        del mind[bi]
        tb = text(best)
        for x in pool:
            i = ident(x)
            if i in mind: mind[i] = min(mind[i], distance(text(x), tb))
    confident = sorted((x for x in pool if ident(x) in mind and u[ident(x)] < 0.5), key=lambda x: _h(ident(x)))
    audit = confident[:n_audit] if len(confident) >= n_audit else confident + [x for x in pool if ident(x) in mind and x not in confident][:n_audit - len(confident)]
    chosen += [{**x, 'why': 'audit', 'active_score': 0.0, 'uncertainty': round(u[ident(x)], 3)} for x in audit]
    report = {'version': VERSION, 'pool': len(pool), 'already_labelled': len(done), 'budget': budget, 'chosen': len(chosen),
              'by_why': {w: sum(c['why'] == w for c in chosen) for w in ('uncertain', 'diverse', 'audit')},
              'mean_uncertainty_chosen': round(sum(c['uncertainty'] for c in chosen) / max(1, len(chosen)), 3),
              'mean_uncertainty_pool': round(sum(u.values()) / max(1, len(u)), 3)}
    return chosen, report


# ---- free uncertainty signals -----------------------------------------------------------------------------------------
def unit_uncertainty(unit):
    """Note units: disagreement between the rule extractor and the local NER (an NER symptom the rules miss, a rule fact with
    no NER support), plus clinical-lexicon text for which the rules found nothing. Free: local rules + local NER only."""
    from . import clinical_ner, note_facts
    text = unit['text']
    rules = note_facts.extract(text, unit.get('stream'), unit.get('field'))
    rk = {(f['fact_type'], f['value'], f['negated']) for f in rules if f.get('source') != 'ner'}
    try:
        ents = clinical_ner.entities(text)
    except Exception:
        ents = []
    ner_only = [f for f in clinical_ner.candidates(text, unit.get('stream'), unit.get('field'), ents)
                if (f['fact_type'], f['value'], f['negated']) not in rk]
    score = 0.0
    if ner_only: score = max(score, 0.9)
    if not rk and any(e['kind'] == 'disease' for e in ents): score = max(score, 0.6)
    if rk and not ents: score = max(score, 0.3)
    return score


def question_uncertainty(question, encoder):
    """NL questions: 1 - router margin (learned_router top-2 gap); unknown -> maximal (an unscored question is worth labelling)."""
    from . import learned_router
    try:
        model = learned_router.load()
        ps = learned_router.probabilities(model, learned_router.features([question], encoder))[0]
        return margin_uncertainty([float(p) for p in ps])
    except Exception:
        return 1.0
