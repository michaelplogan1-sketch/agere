"""What the acceptance checks may read of a semantic-analyst proposal, beyond the text of its SQL (2026-10-24).

The analyst writes free SQL over the definition views (analyst_definitions: living_patient, condition_member, latest_lab, last_12_months,
medicare_member). Those views CARRY the decision-context rules, so a query that says `FROM latest_lab WHERE concept = 'hba1c'` has applied "living
patients only", "the latest result" and the canonical A1c concept without spelling any of it, and the checks (literal_check, requirement_ledger,
acceptance_contract) read only the spelled text: the question's "A1c", "last 12 months" or "30-day" looked absent. This module turns what the views
and the concept library already say into evidence TEXT (never a restated question), and nothing else:

  * every query the answer rests on (a share's numerator and denominator, not whichever ran last): receipt['analyst_evidence']['queries'];
  * the rule each definition view applies, as the words the checks look for (living, last 12 months), and ONLY those a view really applies: a
    question about the deceased or a calendar year is never satisfied by a view;
  * a string literal naming a library concept (`concept = 'hba1c'`) in the concept's own words (concepts.MEASUREMENTS / CONDITIONS label; never its
    codes or digits), so 'hba1c' meets "hemoglobin A1c" and 'chronic_kidney_disease' meets "chronic kidney disease";
  * numbers that live inside a column name (`readmitted_30d`: 30);
  * a directly negated scalar comparison as its complement (`NOT IN (... v <= 9)` is "above 9").

It applies to analyst proposals only (receipt carries analyst_evidence); every other tier is checked on its own SQL as before.
"""
import re

VERSION = 'analyst-acceptance/1'

# the rule a definition view applies, in the words the checks look for. Never the word "deceased" or "death": a view EXCLUDES them.
VIEW_RULES = {
    'living_patient': 'living patients',
    'condition_member': 'living patients; condition evidence',
    'latest_lab': 'living patients; latest result per patient and concept',
    'medicare_member': 'living patients; medicare, dual and medigap members',
    'last_12_months': 'last 12 months; rolling window of 12 months ending at the as_of date',
}
_STRING = re.compile(r"'([^']{1,80})'")
_IDENT_NUMBER = re.compile(r'(?<=[a-z])_(\d{1,4})(?=[a-z_]|\b)', re.I)
_COMPLEMENT = {'>=': '<', '<=': '>', '>': '<=', '<': '>='}


def is_analyst(receipt):
    return isinstance(receipt, dict) and isinstance((receipt.get('analyst_evidence')), dict)


def queries(receipt, graph=None):
    """Every query of the proposal: the analyst's own (analyst_evidence) and the graph's executable queries."""
    found = [q for q in (receipt.get('analyst_evidence') or {}).get('queries') or [] if isinstance(q, str)] if is_analyst(receipt) else []
    for node in (graph or {}).get('nodes', []):
        q = (node.get('parameters') or {}).get('query') if isinstance(node.get('parameters'), dict) else None
        if isinstance(q, str) and q not in found: found.append(q)
    return found


def _concept_terms(literal):
    """The library WORDS of one SQL string literal that names a concept (its label), else ''. Codes and digits are never added: an ICD-10 prefix or a
    LOINC code would otherwise satisfy any number the question states ('E09' is not the 9 of 'above 9')."""
    from . import concepts
    key = literal.strip().lower()
    label = (concepts.MEASUREMENTS.get(key) or concepts.CONDITIONS.get(key) or {}).get('label', '')
    return ' '.join(re.findall(r'[a-z][a-z0-9]*', str(label).lower()))  # words only: a token that starts with a digit is dropped


def _negated_complements(sql):
    """Complement only a directly negated scalar comparison, preserving SQL NULL logic.

    NOT IN / NOT EXISTS can include people with no measurement. Negating a
    compound predicate does not imply that each comparison is complemented.
    Neither form establishes an above/below-threshold observation.
    """
    code = _STRING.sub(lambda m: m.group(0) if re.fullmatch(r"-?\d+(?:\.\d+)?", m.group(1)) else "''", sql)
    code = re.sub(r'/\*.*?\*/|--[^\n]*', ' ', code, flags=re.S)
    scalar = re.compile(r"\bnot\s*\(\s*(?:[a-z_]\w*\.)?[a-z_]\w*\s*(>=|<=|>|<)\s*'?(-?\d+(?:\.\d+)?)'?\s*\)", re.I)
    return [f'{_COMPLEMENT[op]} {number}' for op, number in scalar.findall(code)]


def text(receipt, graph=None):
    """Extra evidence text for an analyst proposal ('' for every other tier). Used as AC.evaluate(extra_text=...)."""
    if not is_analyst(receipt): return ''
    parts = []
    sqls = queries(receipt, graph)
    for sql in sqls:
        code = re.sub(r'--[^\n]*', ' ', sql)
        bare = _STRING.sub("''", code).lower()
        for view, rule in VIEW_RULES.items():
            if re.search(rf'\b{view}\b', bare): parts.append(rule)
        parts += [_concept_terms(lit) for lit in _STRING.findall(code)]
        parts += [lit.replace('_', ' ') for lit in _STRING.findall(code) if '_' in lit]  # 'bilateral_screening_mammography': \\bmammo needs a word boundary
        parts += [f' {n} ' for n in _IDENT_NUMBER.findall(bare)]
        parts += _negated_complements(code)
    return '\n'.join(p for p in dict.fromkeys(parts) if p.strip())


def wants_count_not_set(question, answer):
    """A 'how many ... on their list / who ...' question answered with a count: requested_shape reads 'list' / 'who' as a patient list, but the
    question names its output ('how many', 'number of'), which is a count."""
    return bool(re.search(r'\b(?:how many|number of|count of)\b', question or '', re.I)) and (answer or {}).get('answer_type') == 'count'


_ENTITY_WHICH = re.compile(r'^\s*which\s+(?!patients?\b|members?\b|people\b|individuals\b|persons?\b)[a-z][\w-]*', re.I)


def entity_ranking_question(question):
    """'Which practice has the most ...': the question asks WHICH entity (practice, payer, drug), not which patients, so a table of entities is its shape."""
    return bool(_ENTITY_WHICH.search(question or ''))
