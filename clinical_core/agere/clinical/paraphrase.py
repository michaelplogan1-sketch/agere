"""Reuse a verified interpretation for a reworded question, with zero model calls.

Runs only after the deterministic tier declines and before any hosted fallback. A saved verified question is reused
when the local sentence encoder finds it close AND deterministic guards show the same meaning: identical numbers and
years, the same negation, and the same content words after a small normalization (plurals, common healthcare
synonyms). Any doubt declines; a miss costs nothing but the fallback that would have run anyway. The saved plan is
recompiled under the current code against its own verified wording, and the answer names that wording.
"""
import json
import re
import sqlite3
from pathlib import Path

MIN_SIMILARITY = 0.65  # secondary; the deterministic guards decide meaning
VERSION = 'paraphrase-reuse/1'

PHRASES = [  # multi-word forms first; values are normalized tokens
    (r'emergency (department|room)', 'ed'), (r'\ber\b', 'ed'), (r'hemoglobin a1c|\ba1c\b', 'hba1c'),
    (r'length of stay|\blos\b', 'length_of_stay'), (r'ejection fraction|\bef\b', 'ejection_fraction'),
    (r'no[- ]shows?', 'no_show'), (r'plan[- ]paid', 'plan paid'), (r'calendar year', ''),
    (r'less than|lower than|fewer than|\bunder\b', 'below'), (r'greater than|higher than|more than|\bover\b', 'above'),
    (r'\bper cent\b', 'percent'), (r'(\d+(?:\.\d+)?)\s*(%|percent\b)', r'\1'), (r'(\d+)\s*\+', r'\1 or more'),
]
SYNONYMS = {'percent': 'rate', 'percentage': 'rate', 'share': 'rate', 'fraction': 'rate', 'proportion': 'rate',
            'mean': 'average', 'avg': 'average', 'spend': 'paid', 'spending': 'paid', 'pay': 'paid', 'diabetic': 'diabetes',
            'hypertensive': 'hypertension', 'filled': 'fill', 'filling': 'fill', 'visit': 'visit', 'admission': 'admission',
            'screened': 'screen', 'screening': 'screen', 'positive': 'positive', 'doctor': 'physician'}
NUMBER_WORDS = {w: str(i) for i, w in enumerate('zero one two three four five six seven eight nine ten eleven twelve'.split())}
NEGATION = {'no', 'not', 'without', 'never', 'none', 'missing', 'missed', 'miss', 'gap', 'lack', 'lacking'}
FILLER = {'how', 'many', 'much', 'what', 'whats', 'which', 'was', 'were', 'is', 'are', 'be', 'been', 'the', 'a', 'an', 'of',
          'in', 'for', 'on', 'to', 'at', 'by', 'during', 'did', 'do', 'does', 'we', 'our', 'us', 'have', 'has', 'had',
          'there', 'see', 'get', 'number', 'count', 'total', 'size', 'population', 'patient', 'distinct', 'unique',
          'overall', 'all', 'any', 'it', 'that', 'this', 'with', 'and', 'or', 'more', 'amount', 'please', 'tell', 'me',
          'show', 'give', 'list', 'value', 'year', 'who', 'whose', 'their', 'among', 'from'}
KEEP_S = ('diabetes', 'ss', 'us', 'is', 'sepsis')


def _tokens(text):
    t = text.lower().replace("'", '').replace('’', '')
    for pattern, repl in PHRASES:
        t = re.sub(pattern, repl, t)
    words = re.findall(r'[a-z_]+|\d+(?:\.\d+)?', t)
    out = []
    for w in words:
        w = NUMBER_WORDS.get(w, w)
        if not w.isdigit() and len(w) > 3 and w.endswith('s') and not w.endswith(KEEP_S): w = w[:-1]
        out.append(SYNONYMS.get(w, w))
    return out


def signature(text):
    words = _tokens(text)
    numbers = set()
    for w in words:
        if re.fullmatch(r'\d+(\.\d+)?', w): numbers.add(str(float(w)))
    for a, b in re.findall(r'\b(20\d\d)\s*[-/]\s*(\d{2,4})\b', text):  # 2029-30 and 2029-2030 are one season
        numbers.add(str(float(b if len(b) == 4 else a[:2] + b)))
    return {'numbers': numbers, 'negated': any(w in NEGATION for w in words),
            'content': {w for w in words if w not in FILLER and w not in NEGATION and not w[0].isdigit()}}


def same_meaning(a, b):
    sa, sb = signature(a), signature(b)
    return sa == sb


def _verified(path):
    db = sqlite3.connect(Path(path) / 'question-plans.sqlite3')
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='shared_requests'").fetchone(): return {}
        found = {}
        for ident, payload in db.execute('SELECT id,payload FROM shared_requests ORDER BY rowid'):
            item = json.loads(payload)
            if item.get('question') and item.get('interpretation', {}).get('execution_accepted') and (
                    item.get('program') or item.get('analysis') or item.get('request')):
                found[item['question']] = (ident, item)
        return found
    finally:
        db.close()


def find(path, question, encoder=None):
    """(saved_question, saved_item, similarity) or None."""
    from .learning import correction_context
    saved = _verified(path)
    if not saved or question in saved or correction_context(path, question): return None
    matching = [q for q in saved if same_meaning(question, q) and not correction_context(path, q)]
    if not matching: return None
    if encoder is None:
        from .schema_rerank import load_encoder
        encoder = load_encoder()
    if encoder is None: return None
    vectors = encoder.encode([question, *matching])
    scored = sorted(((float(vectors[0] @ vectors[i + 1]), q) for i, q in enumerate(matching)), reverse=True)
    best = [s for s in scored if s[0] >= MIN_SIMILARITY]
    if not best: return None
    plans = {json.dumps({k: saved[q][1].get(k) for k in ('program', 'analysis', 'request')}, sort_keys=True, default=str)
             for _, q in best}
    if len(plans) != 1: return None  # two verified readings of the same wording disagree
    score, chosen = best[0]
    return chosen, saved[chosen][1], round(score, 3)


def disclosure(interpretation):
    return (f"Reworded question answered with the verified interpretation of \"{interpretation['paraphrase_of']}\" "
            '(same numbers, negation and terms; local encoder similarity '
            f"{interpretation['paraphrase_similarity']}). Recompiled under the current code.")
