"""EHRSQL 2024 (MIMIC-IV demo) as an external benchmark: loader, official-metric scorer and test-split denylist.

Data: artifacts/external/ehrsql-2024/ (see bench/external/build_ehrsql_db.py and ehrsql_manifest.json).
Task: Lee et al., "Overview of the EHRSQL 2024 Shared Task on Reliable Text-to-SQL Modeling on Electronic Health
Records", ClinicalNLP 2024 (arXiv:2405.06673). Each question is answerable (reference SQL) or unanswerable
(label 'null'); a system answers with SQL or abstains with 'null'.

Scoring is a faithful port of the organisers' scoring_program (scoring_utils.py, postprocessing.py at commit f9e1aa0):
both SQLs are post-processed, executed on mimic_iv.sqlite, results rounded to 3 places, rows sorted and only the first
100 compared. Per question: answerable and correct = +1, answerable and abstained = 0, answerable and wrong = -1,
unanswerable and answered = -1, unanswerable and abstained = +1. RS(c) is the mean with every -1 multiplied by c;
RS(0) is plain accuracy (abstaining on an unanswerable question counts as correct), RS(N) uses c = number of questions,
so a single mistake costs as much as all N right answers. Abstention precision/recall treat "abstain" as a prediction
of "unanswerable".

The test split is denied for training: ehrsql_test_denylist.json holds sha256 of each normalised test question and of
each raw test question. Any training/export path must call `denied(question)` and drop (or fail on) hits.
"""
from __future__ import annotations

from ast import literal_eval
from pathlib import Path
import hashlib, json, re, sqlite3, time, unicodedata

ROOT = Path('/Users/mikel/clinical-insights-local/artifacts/external/ehrsql-2024')
DATA = ROOT / 'ehrsql-repo' / 'data' / 'mimic_iv'
DB = ROOT / 'mimic_iv.sqlite'
DENYLIST = Path(__file__).resolve().parent / 'ehrsql_test_denylist.json'
NULL = 'null'
SPLITS = ('train', 'valid', 'test')

# --- official post-processing (scoring_program/postprocessing.py) --------------------------------------------------
CURRENT_DATE = '2100-12-31'
CURRENT_TIME = '23:59:00'
NOW = f'{CURRENT_DATE} {CURRENT_TIME}'
PRECOMPUTED = {'temperature': (35.5, 38.1), 'sao2': (95.0, 100.0), 'heart rate': (60.0, 100.0),
               'respiration': (12.0, 18.0), 'systolic bp': (90.0, 120.0), 'diastolic bp': (60.0, 90.0),
               'mean bp': (60.0, 110.0)}
TIME_PATTERN = r"(DATE_SUB|DATE_ADD)\((\w+\(\)|'[^']+')[, ]+ INTERVAL (\d+) (MONTH|YEAR|DAY)\)"


def _date_function(match):
    function, date, number, unit = match.group(1), match.group(2), match.group(3), match.group(4).lower()
    unit = unit.rstrip('s') if number == '1' else unit + ('' if unit.endswith('s') else 's')
    return f"datetime({date}, '{'-' if function == 'DATE_SUB' else '+'}{number} {unit}')"


def post_process_sql(query):
    query = re.sub('[ ]+', ' ', query.replace('\n', ' ')).strip()
    query = query.replace('> =', '>=').replace('< =', '<=').replace('! =', '!=')
    query = re.sub(TIME_PATTERN, _date_function, query)
    query = query.replace('current_time', f"'{NOW}'").replace('current_date', f"'{CURRENT_DATE}'")
    query = query.replace("'now'", f"'{NOW}'").replace('NOW()', f"'{NOW}'")
    query = query.replace('CURDATE()', f"'{CURRENT_DATE}'").replace('CURTIME()', f"'{CURRENT_TIME}'")
    lower = re.findall('[ \n]+([a-zA-Z0-9_]+_lower)', query)
    upper = re.findall('[ \n]+([a-zA-Z0-9_]+_upper)', query)
    if lower and upper:
        names = set(re.findall('([a-zA-Z0-9_]+)_lower', lower[0]) + re.findall('([a-zA-Z0-9_]+)_upper', upper[0]))
        if len(names) == 1 and (name := names.pop().replace('_', ' ')) in PRECOMPUTED:
            query = query.replace(lower[0], f'{PRECOMPUTED[name][0]}').replace(upper[0], f'{PRECOMPUTED[name][1]}')
    return query.replace('%y', '%Y').replace('%j', '%J')


# --- official answer normalisation (scoring_program/scoring_utils.py) ----------------------------------------------
def _item(value):
    try:
        value = round(float(value), 3)
    except (TypeError, ValueError):
        pass
    return str(value)


def process_answer(answer):
    """A result (list of rows) or its string form -> the comparison key; strings that are not results stay as is."""
    if isinstance(answer, str):
        try:
            answer = literal_eval(answer)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            return answer
        if isinstance(answer, str):
            return answer
    return str(sorted([[_item(c) for c in row] for row in answer])[:100])


def execute(sql, db=DB, timeout_s=30.0):
    """Read-only execution with a wall-clock limit. Raises sqlite3.Error (incl. interrupt) on failure."""
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    con.text_factory = lambda b: b.decode(errors='ignore')
    deadline = time.monotonic() + timeout_s
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def result_key(sql, db=DB, tag='pred', timeout_s=30.0):
    """Official per-query outcome: 'null' for abstention, 'error_<tag>' on failure, else the normalised result."""
    if sql == NULL:
        return NULL
    try:
        rows = execute(post_process_sql(sql), db, timeout_s)
    except (sqlite3.Error, ValueError, OverflowError):
        return 'error_' + tag
    return process_answer(rows)


# --- loader ----------------------------------------------------------------------------------------------------------
def _answer_type(sql, result):
    if sql == NULL:
        return 'unsupported'
    if isinstance(result, str):
        try:
            result = literal_eval(result)
        except (ValueError, SyntaxError):
            pass
    if isinstance(result, list) and len(result) == 1 and len(result[0]) == 1:
        return 'count' if re.match(r'(?is)\s*select\s+count\s*\(', sql) else 'value'
    return 'table'


def load_split(split, data=DATA, allow_test=False):
    """EHRSQL items in the bench question format. The test split needs allow_test=True (scoring only)."""
    if split not in SPLITS:
        raise ValueError(f'unknown split {split!r}')
    if split == 'test' and not allow_test:
        raise PermissionError('EHRSQL test split is held out; pass allow_test=True only for a final scored run')
    items = json.loads((data / split / 'data.json').read_text())['data']
    labels = json.loads((data / split / 'label.json').read_text())
    answers = json.loads((data / split / 'answer.json').read_text())
    out = []
    for item in items:
        sql, result = labels[item['id']], answers[item['id']]
        out.append({'id': item['id'], 'question': item['question'], 'answer_type': _answer_type(sql, result),
                    'unanswerable': sql == NULL, 'reference_sql': None if sql == NULL else sql,
                    'reference_result': None if sql == NULL else result, 'split': f'ehrsql2024-{split}',
                    'source': 'EHRSQL 2024 (CC-BY-4.0) on MIMIC-IV demo 2.2 (ODbL)'})
    return out


# --- scorer ----------------------------------------------------------------------------------------------------------
def reliability(real, pred):
    """Official per-question score in {-1, 0, 1} from two outcome keys."""
    if real != NULL:
        if real == pred:
            return 1
        return 0 if pred == NULL else -1
    return 1 if pred == NULL else -1


def penalize(scores, penalty):
    return sum(s * penalty if s == -1 else s for s in scores) / len(scores) if scores else 0.0


def score_keys(real, pred):
    """real/pred: {id: outcome key}. Returns the official RS numbers (x100) plus accuracy and abstention stats."""
    if set(real) != set(pred):
        raise ValueError('prediction ids do not match the reference ids')
    ids = sorted(real)
    per = {i: reliability(real[i], pred[i]) for i in ids}
    scores = list(per.values())
    n = len(ids)
    answerable = [i for i in ids if real[i] != NULL]
    unanswerable = [i for i in ids if real[i] == NULL]
    abstained = [i for i in ids if pred[i] == NULL]
    correct_answered = sum(per[i] == 1 for i in answerable)
    true_abstain = sum(real[i] == NULL for i in abstained)
    pct = lambda a, b: round(100 * a / b, 2) if b else None
    return {
        'n': n, 'answerable': len(answerable), 'unanswerable': len(unanswerable),
        'RS(0)': round(100 * penalize(scores, 0), 2), 'RS(5)': round(100 * penalize(scores, 5), 2),
        'RS(10)': round(100 * penalize(scores, 10), 2), 'RS(N)': round(100 * penalize(scores, n), 2),
        'accuracy': round(100 * penalize(scores, 0), 2),
        'answerable_execution_accuracy': pct(correct_answered, len(answerable)),
        'answered_precision': pct(correct_answered, n - len(abstained)),
        'coverage': pct(n - len(abstained), n),
        'abstention_precision': pct(true_abstain, len(abstained)),
        'abstention_recall': pct(true_abstain, len(unanswerable)),
        'errors': {'wrong_answer': sum(per[i] == -1 for i in answerable),
                   'answered_unanswerable': sum(per[i] == -1 for i in unanswerable),
                   'abstained_answerable': sum(per[i] == 0 for i in answerable)},
        'per_question': per,
    }


def score_sql(items, predictions, db=DB, timeout_s=30.0):
    """items: load_split output; predictions: {id: SQL or 'null'}. Executes both sides like the official scorer."""
    real = {q['id']: result_key(q['reference_sql'] or NULL, db, 'real', timeout_s) for q in items}
    pred = {q['id']: result_key(predictions[q['id']], db, 'pred', timeout_s) for q in items}
    return score_keys(real, pred)


def score_answers(items, answers):
    """Result-based scoring (the organisers' experimental scoring_v2): answers {id: rows, result string, or 'null'}."""
    real = {q['id']: NULL if q['unanswerable'] else process_answer(q['reference_result']) for q in items}
    pred = {q['id']: NULL if answers[q['id']] == NULL else process_answer(answers[q['id']]) for q in items}
    return score_keys(real, pred)


# --- test-split denylist ---------------------------------------------------------------------------------------------
def normalize_question(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    text = re.sub(r'[^\w\s]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def question_hash(text, normalized=True):
    return hashlib.sha256((normalize_question(text) if normalized else text.strip()).encode()).hexdigest()


def build_denylist(data=DATA):
    items = load_split('test', data, allow_test=True)
    return {'name': 'EHRSQL 2024 test split', 'reason': 'held-out external benchmark; never train or export on it',
            'normalization': 'NFKC, casefold, punctuation -> space, collapse whitespace; sha256 of UTF-8',
            'count': len(items),
            'normalized_sha256': sorted({question_hash(q['question']) for q in items}),
            'exact_sha256': sorted({question_hash(q['question'], normalized=False) for q in items}),
            'ids': sorted(q['id'] for q in items)}


_DENY = {}


def denied(question, path=DENYLIST):
    """True when the question (exact or normalised) is an EHRSQL 2024 test question."""
    if 'sets' not in _DENY:
        deny = json.loads(Path(path).read_text())
        _DENY['sets'] = (set(deny['normalized_sha256']), set(deny['exact_sha256']))
    norm, exact = _DENY['sets']
    return question_hash(question) in norm or question_hash(question, normalized=False) in exact


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Write the test-split denylist, or score a prediction file.')
    parser.add_argument('--write-denylist', action='store_true')
    parser.add_argument('--score', type=Path, help='prediction.json ({id: SQL or "null"}) for the valid split')
    args = parser.parse_args()
    if args.write_denylist:
        DENYLIST.write_text(json.dumps(build_denylist(), indent=1) + '\n')
        print('wrote', DENYLIST)
    if args.score:
        result = score_sql(load_split('valid'), json.loads(args.score.read_text()))
        result.pop('per_question')
        print(json.dumps(result, indent=1))
