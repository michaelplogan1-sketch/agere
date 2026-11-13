"""The sentence above the table. No model computes or supplies a number.

  one-row answer    code writes the sentence (no model)
  several rows      code computes the facts (row count, totals, highest and lowest row); the small model only words them;
                    code then checks that every number in its text is a number from the table, the facts or the question.
                    A text that fails the check is discarded and the code-written sentence is used instead.
"""
import json
import re

from . import models

MAX_ROWS = 40
SYSTEM = '''Write the summary that sits above a result table: one to three plain sentences that answer the question.
Use only the facts and rows given. Copy every number exactly as given: do not round, convert units, compute
percentages, differences or ratios, or state any number that is not in the input. Do not explain method, do not
add interpretation, causes or advice. Column names ending in _cents are cents; say "cents" if you quote them.
Return exactly one JSON object: {"summary": "<the sentences>"}'''


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _show(v):
    if isinstance(v, int): return f'{v:,}'
    if isinstance(v, float): return f'{v:,.4f}'.rstrip('0').rstrip('.')
    return str(v)


def facts(columns, rows):
    """What code can say about a table without judgment."""
    out = {'rows': len(rows)}
    label = next((i for i, c in enumerate(columns) if rows and all(not _num(r[i]) for r in rows)), None)
    for i, c in enumerate(columns):
        values = [r[i] for r in rows if _num(r[i])]
        if len(values) != len(rows) or not rows: continue
        hi, lo = max(rows, key=lambda r: r[i]), min(rows, key=lambda r: r[i])
        out[c] = {**({'total': sum(values)} if all(isinstance(v, int) for v in values) and not re.search(r'rate|share|pct|percent|ratio|mean|avg|median', c) else {}),
                  'highest': {'value': hi[i], **({'row': hi[label]} if label is not None else {})},
                  'lowest': {'value': lo[i], **({'row': lo[label]} if label is not None else {})}}
    return out


def by_code(columns, rows, read=None, statement=None):
    """The sentence code can always write."""
    if read and read.get('mode') == 'count' and len(rows) == 1:
        n, yes, no, open_ = rows[0]
        return (f'{_show(yes)} of the {_show(n)} documents read state it' + (f' ({_show(open_)} could not be decided)' if open_ else '') +
                (f': "{statement}"' if statement else '.'))
    if read and read.get('mode') == 'extract':
        counts = {}
        for r in rows: counts[r[-1]] = counts.get(r[-1], 0) + 1
        top = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[:6]
        return f'{_show(len(rows))} documents give the value: ' + '; '.join(f'{v} ({_show(n)})' for v, n in top) + ('; and others.' if len(counts) > 6 else '.')
    if len(rows) == 1:
        return '; '.join(f"{c.replace('_', ' ')}: {_show(v)}" for c, v in zip(columns, rows[0])) + '.'
    f, parts = facts(columns, rows), [f'{_show(len(rows))} rows by {columns[0].replace("_", " ")}.']
    for c, got in f.items():
        if c == 'rows': continue
        name = c.replace('_', ' ')
        hi, lo = got['highest'], got['lowest']
        parts.append((f'Total {name}: {_show(got["total"])}. ' if 'total' in got else '') +
                     f'Highest {name}: {_show(hi["value"])}' + (f' ({hi["row"]})' if 'row' in hi else '') +
                     f'; lowest: {_show(lo["value"])}' + (f' ({lo["row"]})' if 'row' in lo else '') + '.')
        if len(parts) >= 3: break
    return ' '.join(parts)


def _numbers(text):
    return {t.replace(',', '').rstrip('.') for t in re.findall(r'(?<![\w.])\d[\d,]*(?:\.\d+)?', str(text))}


def unsupported_numbers(text, question, columns, rows, computed):
    """Numbers in the text that are not in the question, the table or the computed facts."""
    allowed = _numbers(question) | _numbers(json.dumps(computed, default=str)) | _numbers(' '.join(columns))
    for r in rows:
        for v in r:
            allowed |= _numbers(v) if not _num(v) else {str(v), _show(v).replace(',', '')}
    return sorted(_numbers(text) - allowed)


def write(question, columns, rows, read=None, statement=None):
    """-> (text, {'by': 'code'|'small_model', 'usd', 'tokens', ...})."""
    plain = by_code(columns, rows, read, statement)
    if len(rows) <= 1 or (read and read.get('mode') == 'count'):
        return plain, {'by': 'code', 'usd': 0.0, 'tokens': 0}
    computed = facts(columns, rows)
    try:
        text, step = models._claude(models.SLM, SYSTEM, json.dumps(
            {'question': question, 'columns': columns, 'facts_computed_by_code': computed, 'rows': rows[:MAX_ROWS],
             **({'rows_not_shown': len(rows) - MAX_ROWS} if len(rows) > MAX_ROWS else {}),
             **({'each_row_is_one_document_and_value_is_copied_from_it': True} if read else {})}, default=str))
    except Exception as exc:
        if 'limit' in str(exc).lower(): raise
        return plain, {'by': 'code', 'usd': 0.0, 'tokens': 0, 'small_model': f'{type(exc).__name__}'}
    base = {'usd': step['usd'], 'tokens': step['tokens']}
    try:
        summary = ' '.join(str(models._json(text).get('summary') or '').split())
    except ValueError:
        return plain, {**base, 'by': 'code', 'small_model': 'unusable output'}
    bad = unsupported_numbers(summary, question, columns, rows, computed)
    if not 20 <= len(summary) <= 700 or bad:
        return plain, {**base, 'by': 'code', 'small_model': 'discarded: numbers not in the result: ' + ', '.join(bad[:6]) if bad else 'discarded: length'}
    return summary, {**base, 'by': 'small_model'}
