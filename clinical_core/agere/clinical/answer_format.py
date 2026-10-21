"""Reader-facing presentation of executed answers: money in dollars, organization IDs with their names, and an
explicit "not answerable" when the requested value is absent.

Runs after execution on every shared-planner path. It never changes which rows or patients an answer contains; the
stored values remain inspectable in the query evidence, and each conversion is disclosed in the limitations.
"""
import re
import sqlite3

MONEY_AGGREGATES = ('sum', 'avg', 'min', 'max')
ABSENCE = re.compile(r'\b(not observable|not available|unavailable|cannot be (computed|determined|established|answered)'
                     r'|no such records?|not (received|present) in the (received )?data)\b', re.I)


def _dollars(value):
    return None if value is None else round(value / 100.0, 2)


def _scalar_is_cents(result):
    contract = result.get('request_contract') or {}
    select = (contract.get('request') or {}).get('select') or []
    if contract.get('kind') not in ('general_analysis', 'request_program') and len(select) == 1:
        s = select[0]
        if s.get('kind') in MONEY_AGGREGATES and str(s.get('field', '')).endswith('_cents'):
            return True
    columns = ((result.get('results') or [{}])[0].get('result') or {}).get('columns') or []
    return len(columns) == 1 and columns[0].lower().endswith('_cents')


def _organization_names(target, values):
    if not target or not values: return {}
    try:
        db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
        try:
            marks = ','.join('?' * len(values))
            return dict(db.execute(f'SELECT organization_id, display_name FROM organization WHERE organization_id IN ({marks}) '
                                   'AND display_name IS NOT NULL', list(values)).fetchall())
        finally:
            db.close()
    except sqlite3.Error:
        return {}


def present(result, target=None):
    """Mutates result['answer'] in place and returns result."""
    answer = result.get('answer')
    if not isinstance(answer, dict): return result
    notes = []
    # A question that names cents gets cents.
    money = not re.search(r'cents?\b', str(result.get('question', '')), re.I)
    if money and isinstance(answer.get('value'), (int, float)) and not isinstance(answer['value'], bool) and _scalar_is_cents(result):
        answer['value_cents'] = answer['value']
        answer['value'] = _dollars(answer['value'])
        answer['unit'] = 'USD'
        label = str(answer.get('headline', ''))
        label = label[len(str(answer['value_cents'])):].replace(' cents', '') if label.startswith(str(answer['value_cents'])) else ''
        answer['headline'] = f"${answer['value']:,.2f}" + label
        notes.append('Money is shown in dollars; the warehouse stores cents.')
    table = answer.get('table')
    if isinstance(table, list) and table and all(isinstance(r, dict) for r in table):
        columns = list(table[0].keys())
        rename = {c: c[:-6] + '_dollars' for c in columns
                  if money and c.lower().endswith('_cents') and c[:-6] + '_dollars' not in columns}
        texts = {c: {r[c] for r in table if isinstance(r.get(c), str)} for c in columns}
        names = {}
        for c in columns:
            if c.endswith('_name') or c + '_name' in columns or not texts[c]: continue
            found = _organization_names(target, texts[c])
            if found and len(found) == len(texts[c]): names[c] = found
        if rename or names:
            rows = []
            for r in table:
                out = {}
                for c in columns:
                    if c in rename:
                        v = r[c]
                        out[rename[c]] = _dollars(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v
                    else:
                        out[c] = r[c]
                    if c in names:
                        out[c + '_name'] = names[c].get(r[c])
                rows.append(out)
            answer['table'] = rows
            if rename: notes.append('Money columns are shown in dollars (' + ', '.join(rename.values()) + '); the warehouse stores cents.')
            if names: notes.append('Organization names come from organization.display_name.')
            if str(answer.get('headline', '')).endswith(', '.join(columns)):
                answer['headline'] = f"{len(rows)} rows · " + ', '.join(rows[0].keys())
    _honest_unknown(answer)
    _outside_coverage(result, answer, target)
    notes += _basis_notes(result)
    if notes:
        answer['limitations'] = list(dict.fromkeys([*(answer.get('limitations') or []), *notes]))
    return result


def _honest_unknown(answer):
    """The requested value is absent: say so instead of showing a null, a flag row or a proxy as the answer."""
    if answer.get('status') not in ('answered', 'partial'): return
    reasons = [str(m) for m in answer.get('missing') or []]
    table = answer.get('table')
    if any(k in answer for k in ('value', 'count')) and answer.get('value', answer.get('count')) is None:
        absent = True
    elif isinstance(table, list) and table and all(isinstance(r, dict) for r in table):
        notes = [[v for v in r.values() if isinstance(v, str) and ABSENCE.search(v)] for r in table]
        absent = all(notes)
        reasons += [n for row in notes for n in row]
    elif 'value' not in answer and 'count' not in answer and table is None and answer.get('answer_type') in ('count', 'value', 'table'):
        absent = True
    else:
        absent = False
    if not absent: return
    reasons = list(dict.fromkeys(reasons)) or ['The requested value is not present in the received data.']
    answer.update(answerable=False, actionable_cohort=False,
                  headline='Not answerable from the received data: ' + reasons[0])
    answer['missing'] = list(dict.fromkeys([*(answer.get('missing') or []), *reasons]))


def _outside_coverage(result, answer, target):
    """A governed measure asked for a period the source never covers (claims that begin later) is not a zero."""
    analyses = ((result.get('request_contract') or {}).get('request') or {}).get('analyses') or []
    if not target or answer.get('answerable') is False: return
    from .request_vocabulary import definitions
    gaps = []
    for a in analyses:
        if a.get('operation') != 'governed_metric': continue
        p = a['parameters']
        field = ((definitions().get(p['id']) or {}).get('time_anchor') or {}).get('field', '')
        table, _, column = field.partition('.')
        if not re.fullmatch(r'[a-z_]+', table or '') or not re.fullmatch(r'[a-z_]+', column or ''): continue
        try:
            db = sqlite3.connect(f'file:{target}?mode=ro', uri=True)
            try: low, high = db.execute(f'SELECT MIN({column}), MAX({column}) FROM {table}').fetchone()
            finally: db.close()
        except sqlite3.Error:
            continue
        if low and (p['period']['end'] < str(low)[:10] or p['period']['start'] > str(high)[:10]):
            gaps.append(f"{table} data covers {str(low)[:10]} to {str(high)[:10]}; the requested period "
                        f"{p['period']['start']} to {p['period']['end']} is outside it.")
    if gaps and len(gaps) == len([a for a in analyses if a.get('operation') == 'governed_metric']):
        answer.update(answerable=False, actionable_cohort=False, headline='Not answerable from the received data: ' + gaps[0])
        answer['missing'] = list(dict.fromkeys([*(answer.get('missing') or []), *gaps]))
    elif gaps:
        answer['limitations'] = list(dict.fromkeys([*(answer.get('limitations') or []), *gaps]))


BASIS = {'payer_allowed_medical_pmpm': 'PMPM basis: allowed dollars on paid payer medical claims (plain "PMPM" means allowed; '
                                      'ask for "paid PMPM" for paid dollars). Pharmacy excluded.',
         'payer_paid_medical_pmpm': 'PMPM basis: paid dollars on payer medical claims, as asked. Pharmacy excluded.'}


def _basis_notes(result):
    analyses = ((result.get('request_contract') or {}).get('request') or {}).get('analyses') or []
    return list(dict.fromkeys(BASIS[a['parameters']['id']] for a in analyses
                              if a.get('operation') == 'governed_metric' and a['parameters'].get('id') in BASIS))
