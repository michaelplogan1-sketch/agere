"""Reader-facing presentation of executed answers: money in dollars, organization IDs with their names.

Runs after execution on every shared-planner path. It never changes which rows or patients an answer contains; the
stored values remain inspectable in the query evidence, and each conversion is disclosed in the limitations.
"""
import re
import sqlite3

MONEY_AGGREGATES = ('sum', 'avg', 'min', 'max')


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
    if notes:
        answer['limitations'] = list(dict.fromkeys([*(answer.get('limitations') or []), *notes]))
    return result
