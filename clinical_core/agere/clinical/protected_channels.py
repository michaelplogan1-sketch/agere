"""Protected exception / contradiction retrieval channels: reranking can never silently drop negative evidence.

Relevance rerankers reward passages that *agree* with the question's words. The passages a reviewer most needs are often
the ones that say the opposite: "denies chest pain", "metformin stopped for GI intolerance", "declined statin",
"allergic to lisinopril", "hospice enrolled". Three channels keep them in the selection (CONTEXT_ENGINEERING_REVIEW
2026-10-09: retrieval built candidates with protected=False, reranked for relevance, then applied diversity and a count
cap; no guarantee that exceptions, negative evidence and contradictory accounts survived):

  exception      a dedicated FTS query: the question's subject terms AND an exception cue (negation, discontinuation,
                 intolerance/allergy, refusal, exclusion), run in the same scope as the main query, so an exception that
                 never reached the fused top 24 still becomes a candidate
  cue            fused candidates whose own text carries an exception cue about a subject term
  contradiction  two candidates of one patient that mention the same subject term with opposite polarity (one affirmed,
                 one negated/stopped): both are kept so the disagreement is visible

Selection reserves up to RESERVE_SHARE of the result slots for protected rows (best reranker score first); the remaining
slots are filled by relevance as before. Protected rows are labelled with their channel and cue. Kinds are heuristic
cues for inspection, not clinical interpretation; an absent cue proves nothing. No model calls; synthetic data only.
"""
from collections import defaultdict
import re

VERSION = 'protected-exception-channels/1'
RESERVE_SHARE = 0.25          # at most a quarter of the slots (min 1) are reserved for protected rows
CHANNEL_CANDIDATES = 6        # extra candidates the exception channel may add per query

CUES = {
    'negation': r"\b(?:denies|denied|deny|negative for|no (?:evidence|history|signs?|symptoms?) of|not (?:on|taking|present|found)|"
                r"without|ruled out|rules out|absent|no longer)\b",
    'discontinuation': r'\b(?:stopped|stop(?:ping)?|discontinu\w*|held|hold(?:ing)?|paused|ceased|tapered off|d/c|dc\'?d|'
                       r'switched (?:off|from)|no longer taking)\b',
    'intolerance': r'\b(?:intoleran\w*|allerg\w*|adverse (?:reaction|event|effect)s?|side effects?|reaction to|angioedema|'
                   r'hyperkalemia|contraindicat\w*|cannot tolerate|did not tolerate|not tolerated)\b',
    'refusal': r'\b(?:declined|declines|refused|refuses|unwilling|opted out|patient preference not to)\b',
    'exclusion': r'\b(?:hospice|palliative|comfort care|excluded|exclusion|exempt|not eligible|ineligible)\b',
    'access_barrier': r'\b(?:cannot afford|can\'t afford|unaffordable|cost (?:barrier|concern)s?|prior auth\w* denied|'
                      r'not covered|coverage denied|unable to obtain|out of stock)\b',
}
CUE_RX = {k: re.compile(v, re.I) for k, v in CUES.items()}
ANY_CUE = re.compile('|'.join(f'(?:{v})' for v in CUES.values()), re.I)
# FTS5 terms for the exception channel (single tokens; FTS has no regex)
FTS_CUES = ('denies', 'denied', 'negative', 'stopped', 'discontinued', 'held', 'intolerance', 'intolerant', 'allergy',
            'allergic', 'adverse', 'reaction', 'contraindicated', 'declined', 'refused', 'hospice', 'palliative', 'excluded',
            'afford', 'angioedema', 'hyperkalemia')
STOP = set('''how many much what which who whom whose the and for with did does was were are had has have this that these those
patient patients member members their there from into over under than then when where why all any each every our your
show list give tell find number count total average rate percent share last year years month months day days since
before after between during within about ever currently current taking take on in of to a an is be by or not no do'''.split())


def kinds(text):
    """Exception cue kinds present in one passage text (heuristic)."""
    return sorted(k for k, rx in CUE_RX.items() if rx.search(text or ''))


def subject_terms(question):
    """Question words that name the subject (drug, condition, test, service): exception cues are kept only about them."""
    words = re.findall(r'[a-z][a-z0-9-]{2,}', (question or '').lower())
    return list(dict.fromkeys(w for w in words if w not in STOP and not ANY_CUE.fullmatch(w)))[:12]


def _mentions(text, terms):
    low = (text or '').lower()
    return [t for t in terms if re.search(r'\b' + re.escape(t), low)]


def _window(text, term, span=90):
    low = text.lower(); i = low.find(term)
    return low[max(0, i - span): i + len(term) + span] if i >= 0 else ''


def polarity(text, term):
    """'negated' when an exception cue sits near the subject term, 'affirmed' when the term appears without one, None when
    the term is absent."""
    w = _window(text or '', term)
    if not w: return None
    return 'negated' if ANY_CUE.search(w) else 'affirmed'


def mark(question, candidates):
    """Label fused candidates in place: 'cue' channel (exception cue about a subject term) and 'contradiction' channel
    (same patient, same term, opposite polarity). -> number of rows marked."""
    terms = subject_terms(question)
    if not terms: return 0
    marked = 0
    by_patient_term = defaultdict(lambda: defaultdict(list))
    for row in candidates:
        text = row.get('text') or ''
        hit = _mentions(text, terms)
        if not hit: continue
        for t in hit:
            pol = polarity(text, t)
            if pol: by_patient_term[(row.get('source') or {}).get('patient_id')][t].append((pol, row))
        ks = [k for k in kinds(text) if any(polarity(text, t) == 'negated' for t in hit)]
        if ks and not row.get('protected_channel'):
            row['protected_channel'] = {'channel': 'cue', 'kinds': ks, 'terms': hit}; marked += 1
    for _pid, per_term in by_patient_term.items():
        for t, rows in per_term.items():
            pols = {p for p, _ in rows}
            if pols == {'negated', 'affirmed'}:
                for p, row in rows:
                    ch = row.get('protected_channel') or {}
                    if ch.get('channel') != 'contradiction':
                        row['protected_channel'] = {'channel': 'contradiction', 'kinds': kinds(row.get('text')) or ['affirmed'],
                                                    'terms': [t], 'polarity': p}
                        marked += 1
    return marked


def channel_query(question):
    """FTS5 expression for the exception channel: (subject terms) AND (cue terms); None without subject terms."""
    terms = subject_terms(question)
    if not terms: return None
    subject = ' OR '.join(f'"{t}"' for t in terms)
    cues = ' OR '.join(f'"{c}"' for c in FTS_CUES)
    return f'({subject}) AND ({cues})'


def reserve(limit):
    return max(1, int(limit * RESERVE_SHARE))


def select(ranked, limit, patient, accept):
    """Source-diverse selection with reserved protected slots.
    ranked: rows in relevance order; accept(row, seen, by_patient) -> bool applies the caller's diversity rule.
    -> selected rows in relevance order; protected rows carry protected=True."""
    from collections import Counter
    seen, by_patient, chosen = set(), Counter(), []
    slots = reserve(limit)
    for row in ranked:
        if len(chosen) >= slots: break
        if row.get('protected_channel') and accept(row, seen, by_patient):
            chosen.append(row)
    for row in ranked:
        if len(chosen) >= limit: break
        if row in chosen: continue
        if accept(row, seen, by_patient): chosen.append(row)
    order = {id(r): i for i, r in enumerate(ranked)}
    chosen.sort(key=lambda r: order[id(r)])
    for r in chosen:
        if r.get('protected_channel'): r['protected'] = True
    return chosen
