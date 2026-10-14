"""Compose observed cohorts, narrative evidence and existing analytic operators.

Memberships are complete patient sets, never retrieval samples. Statistical
comparisons describe indexed observations; neither clinical absence nor causality
is inferred. Source-bound text annotations retain their existing authority.
"""
from dataclasses import dataclass
from pathlib import Path
import copy
import hashlib
import json
import math
import re
import sqlite3
import time

from scipy.stats import fisher_exact

from ...contracts import digest
from ..mathematics import benjamini_hochberg, facility_shrinkage
from . import cohorts, evidence_queries, grounding
from .clinical_events import validate_window

VERSION = 'population-analysis-graph/1'
from .graph_contracts import SET_OPS, validate_inputs, validate_parameters as validate_shared_parameters
OPERATIONS = SET_OPS | {'aggregate', 'explain', 'sql', 'vbc', 'monitor', 'demand', 'program_effect', 'discover', 'trajectory', 'survival', 'observation_process', 'reference_review', 'compare', 'analyze', 'retrieve', 'forecast', 'source_changes', 'review','longitudinal','worklist','state_review','medication_identity','context','context_passes','assertion_review','baseline','decision_landmark','financial_accounts','accountability_review','protocol_change','program_document_change','program_acquisition','measure_review','evidence_portfolio','outreach'}
OPERATIONS |= {'market'}  # markets view: practice / PCP / payer tickers (market.py)
OPERATIONS |= {'pathway'}  # care pathways (Markov chains, process mining)
OPERATIONS = OPERATIONS | {'profile'}
OPERATIONS |= {'calibration'}  # calibration of predictions (conformal.py)
OPERATIONS |= {'scheduling'}  # no-show model, overbooking, access, reminders (scheduling.py)
OPERATIONS |= {'briefing'}  # ranked proactive briefing (briefing.py + briefing_rank.py)
OPERATIONS |= {'early_signal'}  # matched-filter precursor detection (early_signal.py)
OPERATIONS |= {'claims_review'}  # payment integrity and revenue leakage (claims_review.py)
OPERATIONS |= {'knowledge'}  # cited VBC knowledge base: explanation / strategy questions (vbc_knowledge.py)
OPERATIONS |= {'risk_adjustment'}  # CMS-HCC RAF, coding gaps, recapture, suspects (risk_adjustment.py)
NODE_FIELDS = {'id', 'operation', 'inputs', 'parameters', 'purpose'}
LIMITS = [
    'Membership means observed predicate matches, not confirmed disease or absent care.',
    'Text predicates select existing unvalidated machine annotations, not clinician-adjudicated facts.',
    'These comparisons are associations of observed documentation; temporal order and causality are not established.',
    'Fisher and beta-binomial calculations assume independent patient observations; independence and source capture are unverified.',
    'BH adjustment covers only the listed comparisons within this node; repeated/adaptive searches and arbitrary dependence are not controlled.',
    'Population comparisons are exploratory. Forecast and model-review nodes carry separate research limitations; no node authorizes clinical action.',
]


@dataclass
class Membership:
    universe: set
    yes: set
    unknown: set

    @property
    def no(self):
        return self.universe - self.yes - self.unknown


def combine(operation, values):
    universe = values[0].universe
    if any(v.universe != universe for v in values):
        raise ValueError('Membership universes must agree')
    if operation == 'intersect':
        yes = set.intersection(*(v.yes for v in values))
        no = set.union(*(v.no for v in values))
    elif operation == 'union':
        yes = set.union(*(v.yes for v in values))
        no = set.intersection(*(v.no for v in values))
    elif operation == 'difference':
        yes, no = values[0].yes & values[1].no, values[0].no | values[1].yes
    else:
        raise ValueError('Unknown set operation')
    return Membership(universe, yes, universe - yes - no)


def answer_coverage(plan, results):
    """Observed evidence gaps qualify planner scope without changing its saved plan."""
    nodes = [{'node_id': r['node']['id'], 'concept': r['node']['parameters']['concept'],
              'states': r['result']['status_counts'], **r['result']['evidence_coverage']}
             for r in results if r['node']['operation'] in ('source_state', 'pre_event_state')]
    gaps = any(n.get('patients_without_scoped_records', 0) or n.get('patients_with_unmapped_received_units', 0) or
               any(k not in ('documented_affirmed', 'documented_negated') and v for k,v in n['states'].items())
               for n in nodes)
    return {'status': 'partial_evidence' if gaps or plan['unmet_requirements'] else 'bounded_observations',
            'planner_scope': plan['coverage'], 'source_state_nodes': nodes,
            'whole_question_answer_established': False,
            'meaning': 'Execution coverage is separate from plan support. These are bounded source observations, not clinical or causal conclusions.'}


STATISTIC_WORDS = [('share', r'\b(share|percent(age)?|proportion|fraction|what %)\b'),
                   ('mean', r'\b(average|mean)\b'), ('median', r'\bmedian\b'),
                   ('rate_per_1000', r'\b(rate|per 1,?000|utili[sz]ation)\b'), ('count', r'\b(how many|number of|count)\b')]
COUNT_QUESTION = re.compile(r'\b(how many|number of|count|what (?:share|proportion|percent(?:age)?))\b', re.I)


VBC_VALUE_COLUMNS = [(r'\b(?:ed|er|emergency)\b.*\bper 1,?000\b|\bper 1,?000\b.*\b(?:ed|er|emergency)\b', 'ed_per_1000_roster'),
                     (r'\badmi\w*\b.*\bper 1,?000\b|\bper 1,?000\b.*\badmi\w*', 'admits_per_1000_roster'),
                     (r'\b(?:ed|er|emergency)\b.*\bper 1,?000\b|\bper 1,?000\b.*\b(?:ed|er|emergency)\b', 'ed_per_1000'),
                     (r'\badmi\w*\b.*\bper 1,?000\b|\bper 1,?000\b.*\badmi\w*', 'admits_per_1000'),
                     (r'\breadmission rate\b', 'readmission_rate'), (r'\bfollow-?up rate\b|\b7-day follow', 'followup_7d_rate'),
                     (r'\bpmpm\b|\bper member per month\b', 'allowed_pmpm'),
                     (r'control(?:led)?|compliance|adheren|measure|rate|%|percent', 'rate')]


_REGISTRY = {}


def standards(answer):
    """Published standard behind each governed definition the answer used (definitions registry)."""
    from pathlib import Path
    import json as _json
    if not _REGISTRY:
        target = Path(__file__).resolve().parents[3] / 'config' / 'agere' / 'definitions.json'
        _REGISTRY.update(_json.loads(target.read_text())['definitions'] if target.exists() else {})
    used = [c.get('concept') for c in (answer.get('concepts') or []) if isinstance(c, dict)]
    text = _json.dumps(answer.get('table') or [], default=str)[:4000] + ' ' + str(answer.get('method') or '')
    if re.search(r'per_1000|per 1,?000|member[- ]years?', text): used.append('utilization_per_1000')
    if 'readmission' in text: used.append('readmission_30d')
    if 'a1c_poor_control' in text: used.append('diabetes_a1c_poor_control')
    out = []
    for c in dict.fromkeys(u for u in used if u in _REGISTRY):
        d = _REGISTRY[c]
        out.append({'definition': c, 'agere_default': d['agere_default'], 'standard': d['standard']['name'],
                    'alignment': d['alignment'], 'sources': [x['url'] for x in d['standard']['sources']][:3],
                    'variants': list(d.get('variants', {}))})
    return out


NO_BENCHMARK_OPERATIONS = ('outreach', 'monitor', 'demand', 'survival', 'program_effect', 'discover', 'trajectory', 'explain')
NO_BENCHMARK_OPERATIONS += ('market',)
NO_BENCHMARK_OPERATIONS += ('pathway',)
NO_BENCHMARK_OPERATIONS += ('calibration',)
NO_BENCHMARK_OPERATIONS += ('scheduling',)
NO_BENCHMARK_OPERATIONS += ('briefing',)
NO_BENCHMARK_OPERATIONS += ('early_signal',)
NO_BENCHMARK_OPERATIONS += ('claims_review',)
NO_BENCHMARK_OPERATIONS += ('knowledge',)
NO_BENCHMARK_OPERATIONS += ('risk_adjustment',)


def benchmark_references(answer, question=None, operation=None):
    """National/peer reference values for the answer's metric (config/agere/benchmarks.json); never alters the answer.
    Worklists, monitoring reports, curves and effect estimates are not population rates, so they carry none."""
    if operation in NO_BENCHMARK_OPERATIONS: return []
    from .benchmarks import benchmarks
    try:
        return benchmarks(answer, question)
    except (OSError, ValueError, KeyError, TypeError):
        return []  # a damaged reference library must not break answering


def requested_shape(question):
    """What kind of answer the question asks for, from its wording."""
    q = (question or '').lower()
    if re.search(r'\b(?:by|per|for each|each|break(?:s)? ?(?:it )?(?:down|out)|split|tabulate|monthly|quarterly|weekly|trend)\b(?! 1,?000\b)', q) and \
            not re.search(r'\bper 1,?000\b(?!.*\bby\b)', q):
        return 'table'
    if '%' in q or re.search(r'\b(?:percent(?:age)?|share|proportion|fraction|rate|per 1,?000|average|mean|median|sum|total|how much)\b', q):
        return 'value'
    if re.search(r'\b(?:which|list|who)\b', q): return 'patient_set'
    if re.search(r'\b(?:how many|number of|count)\b', q): return 'count'
    return None


def _tidy(v):
    """Presentation precision for computed numbers: 2 decimals, 4 below 1 (exact values stay in the query result)."""
    if isinstance(v, float) and math.isfinite(v): return round(v, 4 if abs(v) < 1 else 2)
    return v


def _fmt(v):
    if isinstance(v, float): return f'{v:,.2f}'.rstrip('0').rstrip('.') if abs(v) < 1e6 else f'{v:,.0f}'
    return f'{v:,}' if isinstance(v, int) else str(v)


def headline(answer):
    """One plain-English sentence stating the answer and the definitions behind it (deterministic, no model)."""
    status, kind = answer.get('status'), answer.get('answer_type')
    if status in ('unsupported', 'grounding_failed') or kind == 'unsupported':
        missing = '; '.join(m.replace('Missing input: ', '') for m in answer.get('missing', [])[:4])
        return f'Not answerable from the received data. Missing: {missing}.' if missing else 'Not answerable from the received data.'
    if kind == 'count': core = f"{_fmt(answer.get('count'))}"
    elif kind == 'patient_set': core = f"{_fmt(answer.get('count') if answer.get('count') is not None else len(answer.get('patient_ids') or []))} patients"
    elif kind == 'value': core = (_fmt(answer.get('value')) + answer.get('unit', '')) if not isinstance(answer.get('value'), str) else answer['value']
    else:
        rows = answer.get('table') or []
        core = answer['value'] if isinstance(answer.get('value'), str) else f'{len(rows)} rows'
        if rows and not isinstance(answer.get('value'), str):
            metrics = ['allowed_pmpm_completed', 'oe_ratio', 'ed_per_1000_shrunk', 'shrunk_rate', 'rate', 'mean_risk_score', 'value',
                       'readmission_rate', 'members', 'patient_count', 'total_cents', 'count']
            first = rows[0]
            measured = [k for k, v in first.items() if isinstance(v, (int, float)) and not isinstance(v, bool)
                        and re.search(r'avg|average|mean|median|rate|pct|percent|share|per_1000|days|usd|dollars|_cents|ratio', k)]
            metric = next((m for m in metrics if isinstance(first.get(m), (int, float))), None) or (measured[-1] if measured else None) or \
                     next((k for k, v in first.items() if isinstance(v, (int, float)) and k not in ('patients', 'members')), None)
            key = next((k for k in ('group', 'segment', 'factor', 'label') if k in first), None) or next(iter(first))
            if metric:
                items = [f"{r.get(key) if not isinstance(r.get(key), dict) else ', '.join(f'{a}={b}' for a, b in r[key].items())} "
                         f"{_fmt(round(r[metric], 2) if isinstance(r[metric], float) else r[metric])}" for r in rows[:3] if metric in r]
                core = f"{metric.replace('_', ' ')}: " + '; '.join(items) + (f' (+{len(rows) - 3} more)' if len(rows) > 3 else '')
    defs = []
    for c in (answer.get('concepts') or [])[:3]:
        n = len(c.get('included_labels') or c.get('included_series') or [])
        defs.append(f"{c.get('label', c.get('concept'))} ({n} source labels)" if n else c.get('label', c.get('concept')))
    text = f'{core}.' if not core.endswith('.') else core
    if defs: text += ' Definitions: ' + '; '.join(defs) + '.'
    risk = next((r.get('settlement_risk') for r in (answer.get('table') or []) if isinstance(r, dict) and r.get('settlement_risk')), None)
    if risk:
        text += (f" Settlement risk: {risk['p_earn_savings']:.0%} chance of earning savings, {risk['p_owe_loss']:.0%} of owing a loss, "
                 f"{risk['p_within_corridor']:.0%} in the corridor; 95% value-at-risk ${_fmt(risk['value_at_risk_95'])}, "
                 f"expected ${_fmt(risk['expected_net_payment'])}.")
    calibration = (answer.get('overall') or {}).get('calibration') if isinstance(answer.get('overall'), dict) else None
    if isinstance(calibration, dict) and calibration.get('statement') and 'onformal' not in text:
        text += ' Conformal calibration: ' + calibration['statement']
    if answer.get('computed') and not defs: text += f" Computed as: {answer['computed'].rstrip('.')}."
    if status == 'partial' and answer.get('missing') and answer['missing'][0] not in text: text += ' Partial: ' + answer['missing'][0]
    others = [w for w in (answer.get('warnings') or []) if w not in text]
    if others: text += f" Warnings ({len(answer['warnings'])}): " + ' '.join(others[:2])
    return text


def chart_points(report, limit=60):
    """Monitor control charts as display series (already computed by the operator; the answer table omits the points)."""
    from .monitor import LABELS
    out = []
    for c in (report.get('chart_status') or [])[:limit]:
        out.append({'series': c['series'], 'label': LABELS.get(c['series'], c['series']), 'group_by': c['group_by'], 'group': c['group'],
                    'status': c['status'], 'center': c['center'], 'rate_signal_months': c.get('rate_signal_months') or [],
                    'data_signal_months': c.get('data_signal_months') or [],
                    'points': [{k: p.get(k) for k in ('month', 'value', 'lcl', 'ucl')} for p in c.get('chart') or []]})
    return out


def build_answer(plan, results, memberships, question):
    """Shared answer contract: the direct answer, what it covers, and why not if it can't.

    The terminal membership node answers population questions. Report-only
    graphs are partial until reduced to a scalar/table answer.
    """
    grounding_issues = [i for r in results for i in r['result'].get('grounding', [])]
    failed = [r for r in results if r['result'].get('grounding_failed')]
    interpretation = [i for r in results for i in r['result'].get('interpretation', [])]
    answer = {'status': None, 'answer_type': None, 'count': None, 'patient_ids': None, 'value': None,
              'table': None, 'unknown_count': 0, 'missing': list(plan.get('unmet_requirements', [])),
              'grounding': grounding_issues, 'interpretation': plan.get('interpretation'), 'concepts': interpretation}
    if failed:
        answer.update(status='grounding_failed', answer_type='unsupported')
        answer['missing'] += [i['detail'] for r in failed for i in r['result']['grounding'] if i.get('severity') == 'error']
        return answer
    if plan.get('coverage') == 'unsupported' or not plan['nodes']:
        answer.update(status='unsupported', answer_type='unsupported')
        return answer
    last = plan['nodes'][-1]
    aggregates = [i for i, n in enumerate(plan['nodes']) if n['operation'] == 'aggregate']
    if last['operation'] == 'aggregate' and len(aggregates) > 1 and question:
        # Several supporting numbers: answer with the statistic the question asks for.
        wanted = next((s for s, rx in STATISTIC_WORDS if re.search(rx, question, re.I)), None)
        chosen = [i for i in aggregates if plan['nodes'][i]['parameters']['statistic'] == wanted]
        if chosen:
            last, report_index = plan['nodes'][chosen[-1]], chosen[-1]
        else:
            report_index = len(plan['nodes']) - 1
    else:
        report_index = len(plan['nodes']) - 1
    if last['operation'] == 'trajectory':
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        answer.update(status='partial' if partial else 'answered', answer_type='table', table=report['patients'], method=report['method'],
                      count=report['flagged'], patient_ids=[x['patient_id'] for x in report['patients']],
                      value=(f"{report['flagged']} of {report['patients_with_series']} patients with a {report['concept']} series are projected "
                             f"to go {report['direction']} {_fmt(report['threshold'])} {report['unit']} within {report['horizon_days']} days "
                             f"(probability >= 50%)." if report.get('mode') == 'forecast' else
                             f"{report['flagged']} of {report['patients_with_series']} patients with a {report['concept']} series show a material, "
                             f"statistically separable change ({report['direction']})."))
        if report.get('conformal'):  # forecast mode: distribution-free intervals and their backtested coverage
            answer['value'] += ' Conformal calibration: ' + report['conformal']['statement']
            answer['overall'] = {**(answer.get('overall') or {}), 'calibration': report['conformal']}
        return answer
    if last['operation'] == 'calibration':
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        tested = any(r['backtest_coverage'] is not None for r in report['rows'])
        answer.update(status='partial' if partial or not tested else 'answered', answer_type='table',
                      table=[{k: v for k, v in r.items() if k != 'statement'} for r in report['rows']], value=report['reading'],
                      method=report['method'], limitations=report['limitations'],
                      overall={'calibration': {'statement': report['reading'], 'receipts': report['receipts']}, 'reliability': report['reliability']})
        return answer
    if last['operation'] == 'scheduling':
        from .scheduling import summary
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        ok = report.get('model') is not None
        focus = report['parameters']['focus']
        table = (report['appointments'] if focus in ('no_show_risk', 'reminders') else report['access'] if focus == 'access' else
                 [{k: v for k, v in s.items() if k != 'curve'} for s in report['sessions']] or report['appointments'])
        answer.update(status=('partial' if partial else 'answered') if ok else 'unsupported', answer_type='table' if ok else 'unsupported',
                      table=table, value=summary(report), method=report['method'], limitations=report['limitations'],
                      count=len(table), patient_ids=[a['patient_id'] for a in report['appointments']] if focus in ('no_show_risk', 'reminders') else [],
                      overall={'scheduling': {k: report.get(k) for k in ('practice', 'counts', 'model', 'sessions', 'upcoming_sessions',
                                                                         'appointments', 'reminders', 'access', 'access_unavailable',
                                                                         'data_defects', 'by_year', 'assumptions', 'notes')} | {'focus': focus},
                               'assumptions': report['assumptions']})
        if not ok: answer['missing'].append(summary(report))
        return answer
    if last['operation'] == 'survival':
        from .survival import summary
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        answer.update(status='partial' if partial else ('answered' if report['units'] else 'unsupported'),
                      answer_type='table' if report['units'] else 'unsupported', table=report['table'], method=report['method'],
                      value=summary(report), count=report['units'],
                      overall={k: report[k] for k in ('event_label', 'time_zero', 'units', 'patients', 'events', 'excluded',
                                                      'included_without_baseline_result', 'groups', 'logrank', 'cox', 'curves')})
        if not report['units']:
            answer['missing'].append(summary(report))
        return answer
    if last['operation'] == 'briefing':
        from .briefing import summarize as briefing_summary
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        value, table = briefing_summary(report, last['parameters'].get('top', 10))
        answer.update(status='partial' if partial else 'answered', answer_type='table', value=value, table=table, method=report.get('method'),
                      overall={'briefing': report.get('ranking') or {}, 'cards': len(report.get('cards') or []),
                               'cache_hit': report.get('cache_hit')})
        return answer
    if last['operation'] == 'pathway':
        from .pathway import summary
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        ok = bool(report['transitions'])
        answer.update(status='partial' if partial else ('answered' if ok else 'unsupported'),
                      answer_type='table' if ok else 'unsupported', table=report['table'], method=report['method'],
                      value=summary(report), count=report['transitions'],
                      overall={k: report[k] for k in ('states', 'matrix', 'patients', 'transitions', 'anchors', 'chain', 'reach', 'variants',
                                                      'conformance', 'bottlenecks', 'leakage', 'second_order', 'groups', 'acute_data_gaps',
                                                      'excluded', 'parameters', 'notes')})
        if not ok:
            answer['missing'].append(summary(report))
        return answer
    if last['operation'] == 'discover':
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        answer.update(status='partial' if partial else 'answered', answer_type='table', table=report['cards'], method=report['method'],
                      value=(f"{len(report['cards'])} significant concentrations among {report['cells_tested']} segment cells tested; "
                             f"base rate {report['base_rate']:.1%}"),
                      overall={k: report[k] for k in ('population', 'with_outcome', 'base_rate', 'cells_tested')})
        return answer
    if last['operation'] == 'outreach':
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        from .outreach import REASONS
        table = [{k: v for k, v in w.items() if k != 'reasons'} for w in report['worklist']]
        used = {c['code'] for w in report['worklist'] for c in w.get('reasons') or []}
        answer.update(status='partial' if partial else 'answered', answer_type='table', table=table, value=report['headline'],
                      count=report['selected'], patient_ids=[w['patient_id'] for w in report['worklist']], method=report['method'],
                      overall={'portfolio': report['portfolio'], 'optimization': report['optimization'], 'by_group': report['by_group'],
                               'capacity': report['capacity'], 'candidates': report['candidates'],
                               'assumptions': report['assumptions'], 'learning_loop': report['learning_loop'],
                               'limitations': report['limitations'],
                               **({'calibration': report['conformal']} if report.get('conformal') else {}),
                               # display: plain-language label and category for each reason code on the worklist
                               'reason_labels': {c: {'category': REASONS[c][0], 'label': REASONS[c][1]} for c in sorted(used) if c in REASONS}})
        return answer
    if last['operation'] == 'knowledge':
        from .vbc_knowledge import summary
        report = results[-1]['result']
        ok = report['answered']
        rows = [{'n': i + 1, 'id': h['id'], 'topic': h['topic'], 'title': h['title'], 'score': h['score'], 'confidence': h['confidence'],
                 'last_verified': h['last_verified'], 'summary': h['summary'], 'facts': h['facts'], 'citations': h['citations'],
                 'contradictions': h['contradictions']} for i, h in enumerate(h for h in report['hits'] if h['relevant'])]
        answer.update(status='answered' if ok else 'unsupported', answer_type='table' if ok else 'unsupported', table=rows,
                      value=summary(report), method=report['method'], count=len(rows),
                      overall={'knowledge': {'query': report['query'], 'retrieval': report['retrieval'], 'corpus': report['corpus'],
                                             'sources': report['sources'], 'synthesis': report['synthesis']}})
        if not ok: answer['missing'].append(summary(report))
        return answer
    if last['operation'] == 'early_signal':
        from .early_signal import summary
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        ok = bool(report['patients'])
        answer.update(status=('partial' if partial else 'answered') if ok else 'unsupported', answer_type='table' if ok else 'unsupported',
                      table=report['patients'], value=summary(report), method=report['method'], count=report['flagged'],
                      patient_ids=[x['patient_id'] for x in report['patients'] if x['alert']],
                      overall={'early_signal': {k: report[k] for k in ('outcome_label', 'panel', 'acute_data_gaps', 'acute_spikes', 'partial_month',
                                                                       'patients_scored', 'flagged', 'threshold_snr', 'training_events', 'template_source',
                                                                       'background_samples', 'template_norm', 'templates', 'evaluation', 'notes')},
                               'parameters': report['parameters']})
        if not ok: answer['missing'].append(summary(report))
        return answer
    if last['operation'] == 'profile':
        from .profile import summary
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        ok = bool(report['units'])
        answer.update(status=('partial' if partial else 'answered') if ok else 'unsupported', answer_type='table' if ok else 'unsupported',
                      table=report['units'], value=summary(report), method=report['method'], count=report.get('units_total', 0),
                      overall={'profile': {k: report.get(k) for k in ('metric_label', 'display_unit', 'network_rate', 'members', 'unattributed_members',
                                                                      'units_total', 'counts', 'hyper', 'focus', 'shrinkage_median', 'period', 'notes')},
                               'unit': report['parameters']['unit'], 'metric': report['parameters']['metric'],
                               'threshold': report['parameters']['threshold'], 'method_used': report['parameters']['method']})
        if not ok: answer['missing'].append(summary(report))
        return answer
    if last['operation'] == 'monitor':
        from .monitor import summarize
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        value, table = summarize(report)
        answer.update(status='partial' if partial else 'answered', answer_type='table', value=value, table=table,
                      method=report['method'], limitations=report['limitations'],
                      overall={'period': report['period'], 'members_in_scope': report['members_in_scope'],
                               'tests_examined': report['tests_examined'], 'look_elsewhere_bits': report['look_elsewhere_bits'],
                               'charts': chart_points(report)})
        return answer
    if last['operation'] == 'claims_review':
        from .claims_review import summarize as claims_summary
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        value, table = claims_summary(report)
        ok = bool(report['claims'])
        answer.update(status=('partial' if partial else 'answered') if ok else 'unsupported', answer_type='table' if ok else 'unsupported',
                      value=value, table=table, count=len(report['flags']), method=report['method'], limitations=report['limitations'],
                      overall={'claims_review': {k: report.get(k) for k in ('parameters', 'period', 'claims', 'patients', 'providers', 'payers', 'billed',
                                                                         'allowed', 'runs', 'notes', 'leakage_pareto')},
                               'denials': {k: v for k, v in (report.get('denials') or {}).items()} or None,
                               'underpayment': {k: v for k, v in (report.get('underpayment') or {}).items() if k != 'claims'} or None,
                               'providers': report.get('provider_review'), 'benford': report.get('benford'),
                               'duplicates': report.get('duplicates'), 'churn': report.get('churn')})
        if not ok: answer['missing'].append(value)
        return answer
    if last['operation'] == 'risk_adjustment':
        from .risk_adjustment import summarize as ra_summary
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        value, table = ra_summary(report)
        ok = report.get('status') == 'answered'
        answer.update(status=('partial' if partial else 'answered') if ok else 'unsupported', answer_type='table' if ok else 'unsupported',
                      value=value, table=table, count=(report.get('population') or {}).get('patients_in_scope'),
                      method=report['method'], limitations=report['limitations'],
                      overall={'risk_adjustment': {k: report.get(k) for k in ('parameters', 'period', 'payment', 'assumptions', 'data_gaps',
                                                                             'diagnosis_filter', 'capability')},
                               'population': {k: v for k, v in (report.get('population') or {}).items()}})
        if not ok: answer['missing'].append(value)
        return answer
    if last['operation'] == 'market':
        from .market import summarize as market_summary
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        value, table = market_summary(report)
        answer.update(status='partial' if partial else ('answered' if report['tickers'] else 'unsupported'),
                      answer_type='table' if report['tickers'] else 'unsupported', value=value, table=table,
                      method=report['method'], limitations=report['limitations'],
                      overall={k: report[k] for k in ('unit', 'metrics', 'period', 'network', 'sectors', 'data_gaps', 'skipped_metrics',
                                                      'tests_examined', 'look_elsewhere_bits', 'members_in_scope')})
        if not report['tickers']: answer['missing'].append(value)
        return answer
    if last['operation'] == 'demand':
        from .demand import summary
        report = results[-1]['result']
        main = report['series'][0] if report['series'] else {}
        ok = main.get('status') == 'forecast'
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements') or report['missing']
        answer.update(status=('partial' if partial else 'answered') if ok else 'unsupported', answer_type='table' if ok else 'unsupported',
                      table=report['table'], value=summary(report), method=report['method'],
                      overall={'measure': report['measure_label'], 'frequency': report['frequency'], 'as_of': report['as_of'],
                               'capacity': report['capacity'], 'notes': report['notes'], 'flags': report['flags'],
                               # display: history (last 3 years of periods) + forecast band per series
                               'demand': [{k: (v[-156:] if k == 'history' else v) for k, v in s.items()} for s in report['series']]})
        answer['missing'] += report['missing'] + ([] if ok else [summary(report)])
        if report['notes']: answer['warnings'] = report['notes']
        return answer
    if last['operation'] == 'vbc':
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        if report.get('declined'):  # measure library: an input the measure needs is not in the received data
            answer.update(status='unsupported', answer_type='unsupported', value=report.get('reading'), method=report['method'])
            answer['missing'] += report.get('missing_inputs', [])
            return answer
        if report['metric'] == 'quality' and report.get('reading'):  # measure library: rate (or gap list), citation, gaps
            answer.update(status='partial' if partial else 'answered', answer_type='table', table=report['rows'], value=report['reading'],
                          method=report['method'],
                          overall={'period': report['period'], 'members_in_scope': report['members_in_scope'],
                                   **{k: report[k] for k in ('overall', 'gap_count', 'care_gaps', 'citations', 'certification', 'spec_year') if k in report}})
            if report.get('warnings'): answer['warnings'] = report['warnings']
            o = report.get('overall') or {}
            rate = o.get('rate') if 'rate' in o else next((v for k, v in o.items() if k.startswith('rate_per_')), None)
            if rate is not None and len(report['rows']) == 1 and requested_shape(question) == 'value':
                answer.update(answer_type='value', value=round(100 * rate, 1) if 'rate' in o else rate)  # percent, or rate per N
                answer['overall']['reading'] = report['reading']
            return answer
        if report['metric'] == 'change':
            top = report['rows'][0]
            answer.update(status='partial' if partial else 'answered', answer_type='table', table=report['rows'], method=report['method'],
                          value=(f"{top['measure']} {top['value_a']} -> {top['value_b']} ({top['change']:+}): rate effect {top['rate_effect']:+}, "
                                 f"mix effect {top['mix_effect']:+}. {top['reading']}"))
            return answer
        single = len(report['rows']) == 1 and report['metric'] == 'shared_savings'
        value = report['rows'][0].get('payment_to_aco', report['rows'][0].get('payment_from_aco')) if single else None
        if not single and len(report['rows']) == 1 and requested_shape(question) == 'value':
            # One population, one number asked for: pick the column the question names, or decline.
            q = (question or '').lower()
            column = next((c for pattern, c in VBC_VALUE_COLUMNS if re.search(pattern, q) and isinstance(report['rows'][0].get(c), (int, float))), None)
            if column is None:
                answer.update(status='unsupported', answer_type='unsupported',
                              missing=['The question asks for one number; Agere could not tell which utilization measure is meant.'])
                return answer
            single, value = True, report['rows'][0][column]
        answer.update(status='partial' if partial else 'answered', answer_type='value' if single else 'table',
                      value=value,
                      table=report['rows'], method=report['method'], overall={'period': report['period'], 'members_in_scope': report['members_in_scope'],
                                                                             **({'calibration': report['conformal']} if report.get('conformal') else {})})
        return answer
    if last['operation'] == 'program_effect':
        report = results[-1]['result']
        partial = (plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
                   or report['inference'] != 'supported_under_assumptions')
        answer.update(status='partial' if partial else 'answered', answer_type='table', table=report['rows'], value=report['reading'],
                      method=report['method'], warnings=report['warnings'], assumptions=report['assumptions'],
                      overall={k: report[k] for k in ('exposure', 'outcome', 'sample', 'did', 'balance', 'pre_trend', 'placebo',
                                                       'synthetic_control', 'feed_checks', 'inference')})
        answer['missing'] += report['blocking_warnings']
        return answer
    if last['operation'] == 'sql':
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        kind = report['returns']
        answer.update(status='partial' if partial else 'answered', answer_type=kind, query=report['query'])
        stated = re.search(r'--\s*DEFINITION:\s*(.+)', report['query'] or '')
        purpose = plan['nodes'][-1].get('purpose') if plan.get('nodes') else None
        answer['computed'] = stated.group(1).strip() if stated else purpose
        if kind == 'count': answer['count'] = report['value']
        elif kind == 'value': answer['value'] = _tidy(report['value'])
        elif kind == 'patient_set': answer.update(patient_ids=report['patient_ids'], count=len(report['patient_ids']))
        else: answer['table'] = [{k: _tidy(v) for k, v in r.items()} if isinstance(r, dict) else r for r in report['rows']]
        if report['truncated']: answer['missing'].append(f'Result truncated at {report["row_count"]} rows.')
        return answer
    if last['operation'] == 'explain':
        report = results[-1]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        answer.update(status='partial' if partial else 'answered', answer_type='table', value=report['finding'],
                      table=report['factors'] or report.get('cases'), method=report['method'],
                      overall={k: report[k] for k in ('unit', 'units', 'positive_units', 'outcome_rate', 'status')})
        return answer
    if last['operation'] == 'aggregate':
        report = results[report_index]['result']
        partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements')
        answer['concepts'] = interpretation + report.get('interpretation', [])
        if last['parameters']['group_by'] == 'none' and last['parameters']['statistic'] == 'count':
            answer.update(answer_type='count', count=report['overall'].get('value'), table=[report['overall']])
        elif last['parameters']['group_by'] == 'none':
            answer.update(answer_type='value', value=report['overall'].get('value'), table=[report['overall']])
            if last['parameters']['statistic'] == 'share': answer['unit'] = '%'
        else:
            rows = report['rows']
            if last['parameters'].get('columns'):  # the user named the output columns
                key, val = last['parameters']['columns']
                rows = [{key: r['group'], val: r['value']} for r in rows]
            answer.update(answer_type='table', table=rows)
        answer.update(status='partial' if partial else 'answered', method=report['method'], overall=report['overall'])
        return answer
    terminal = next((n['id'] for n in reversed(plan['nodes']) if n['id'] in memberships), None)
    if terminal is None or plan['nodes'][-1]['id'] != terminal:
        # A report-only graph has no direct answer: say what is missing instead of an empty table.
        answer.update(status='unsupported' if plan.get('unmet_requirements') else 'partial', answer_type='unsupported')
        answer['missing'].append('The plan ends in a descriptive report; no direct count, value or table answers the question.')
        return answer
    m = memberships[terminal]
    wants_count = bool(question and COUNT_QUESTION.search(question))
    answer.update(answer_type='count' if wants_count else 'patient_set', count=len(m.yes),
                  patient_ids=sorted(m.yes), unknown_count=len(m.unknown))
    partial = plan.get('coverage') != 'supported_observation' or plan.get('unmet_requirements') or m.unknown \
        or any(i.get('severity') == 'warning' for i in grounding_issues)
    if m.unknown: answer['missing'].append(f'{len(m.unknown)} patients could not be evaluated from the received records.')
    answer['status'] = 'partial' if partial else 'answered'
    return answer


def validate(plan, as_of):
    from .stack_planner import validate as validate_analytic_plan
    if not isinstance(plan, dict) or set(plan) != {'interpretation', 'nodes', 'coverage', 'unmet_requirements'}:
        raise ValueError('Explicit population graph contract required')
    if not isinstance(plan['interpretation'], str) or not 1 <= len(plan['interpretation']) <= 2000:
        raise ValueError('Bounded interpretation required')
    if plan['coverage'] not in ('supported_observation', 'partial', 'unsupported'):
        raise ValueError('Invalid graph coverage')
    unmet = plan['unmet_requirements']
    if not isinstance(unmet, list) or len(unmet) > 20 or any(not isinstance(x, str) or not 1 <= len(x) <= 1000 for x in unmet):
        raise ValueError('Bounded missing requirements required')
    if not isinstance(plan['nodes'], list) or len(plan['nodes']) > 16:
        raise ValueError('At most sixteen graph nodes')
    if plan['coverage'] == 'unsupported' and plan['nodes']:
        raise ValueError('Unsupported graph cannot execute')
    if plan['coverage'] == 'supported_observation' and (unmet or not plan['nodes']):
        raise ValueError('Inconsistent complete graph')
    seen = {}
    normalized = []
    for node in plan['nodes']:
        if not isinstance(node, dict) or set(node) != NODE_FIELDS:
            raise ValueError('Invalid graph node fields')
        ident, op, inputs, p = (node[k] for k in ('id', 'operation', 'inputs', 'parameters'))
        if not isinstance(ident, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', ident) or ident in seen:
            raise ValueError('Unique bounded node IDs required')
        if op not in OPERATIONS or not isinstance(p, dict):
            raise ValueError('Unregistered graph operation or invalid parameters')
        if not isinstance(node['purpose'], str) or not 1 <= len(node['purpose']) <= 1000:
            raise ValueError('Node purpose required')
        if not isinstance(inputs, list) or len(inputs) > 10 or any(not isinstance(x, str) for x in inputs) or len(set(inputs)) != len(inputs):
            raise ValueError('Bounded unique node references required')
        validate_inputs(op, inputs, seen)
        p = validate_shared_parameters(op, p)
        if op == 'cohort':
            if inputs or set(p) != {'specification'}:
                raise ValueError('Cohort node needs its explicit specification')
            p = {'specification': cohorts.validate(p['specification'], as_of)}
        elif op == 'aggregate':
            from . import aggregate
            p = aggregate.validate(p)
        elif op == 'explain':
            from . import explain
            p = explain.validate(p)
        elif op == 'sql':
            from . import sql_query
            p = sql_query.validate(p)
        elif op == 'vbc':
            from . import vbc
            p = vbc.validate(p)
        elif op == 'program_effect':
            from . import program_effect
            p = program_effect.validate(p)
        elif op == 'monitor':
            from . import monitor
            p = monitor.validate(p)
            if any(v > as_of for v in p['period'].values()):
                raise ValueError('Monitor period exceeds snapshot cutoff')
        elif op == 'claims_review':
            from . import claims_review
            p = claims_review.validate(p)
            if any(v > as_of for v in p['period'].values()):
                raise ValueError('Claims review period exceeds snapshot cutoff')
        elif op == 'risk_adjustment':
            from . import risk_adjustment
            p = risk_adjustment.validate(p)
            if p['year'] is not None and p['year'] > int(str(as_of)[:4]):
                raise ValueError('Risk adjustment collection year exceeds snapshot cutoff')
        elif op == 'market':
            from . import market
            p = market.validate(p)
            if any(v > as_of for v in p['period'].values()):
                raise ValueError('Market period exceeds snapshot cutoff')
        elif op == 'demand':
            from . import demand
            p = demand.validate(p)
        elif op == 'trajectory':
            from . import trajectory
            p = trajectory.validate(p)
        elif op == 'outreach':
            from . import outreach
            p = outreach.validate(p)
        elif op == 'calibration':
            from . import calibration
            p = calibration.validate(p)
        elif op == 'survival':
            from . import survival
            p = survival.validate(p)
        elif op == 'scheduling':
            from . import scheduling
            p = scheduling.validate(p)
        elif op == 'briefing':
            from . import briefing_rank
            p = briefing_rank.validate(p)
        elif op == 'pathway':
            from . import pathway
            p = pathway.validate(p)
            if any(v > as_of for v in p['period'].values()):
                raise ValueError('Pathway period exceeds snapshot cutoff')
        elif op == 'knowledge':
            from . import vbc_knowledge
            p = vbc_knowledge.validate(p)
        elif op == 'early_signal':
            from . import early_signal
            p = early_signal.validate(p)
            if p['train_cutoff'] and p['train_cutoff'] > as_of[:7]:
                raise ValueError('early_signal train_cutoff exceeds snapshot cutoff')
        elif op == 'profile':
            from . import profile
            p = profile.validate(p)
            if any(v and v > as_of for v in p['period'].values()):
                raise ValueError('Profile period exceeds snapshot cutoff')
        elif op == 'discover':
            from . import discover
            p = discover.validate(p)
            p['outcome'] = cohorts.validate(p['outcome'], as_of)
        elif op == 'evidence':
            if inputs or set(p) != {'plan'}:
                raise ValueError('Evidence node needs its explicit plan')
            ep = evidence_queries.validate_plan(p['plan'])
            for clause in ep['clauses']:
                if any(clause.get(k, as_of) > as_of for k in ('event_date_from', 'event_date_through')):
                    raise ValueError('Evidence window exceeds snapshot cutoff')
            p = {'plan': ep}
        elif op in ('intersect', 'union', 'difference'):
            if p or len(inputs) < 2 or (op == 'difference' and len(inputs) != 2):
                raise ValueError('Set operations need membership inputs and no parameters')
        elif op == 'compare':
            if p or not 3 <= len(inputs) <= 10:
                raise ValueError('Compare inputs are base, observed endpoint, and one to eight groups')
        elif op == 'analyze':
            if len(inputs) != 1 or set(p) != {'operator', 'drug_class', 'view', 'analysis_window'}:
                raise ValueError('Analyze needs one population and explicit operator settings')
            if p['operator'] == 'evidence_search':
                raise ValueError('Use retrieve for ranked source passages')
            validate_analytic_plan({'interpretation': 'Execute selected population', 'steps': [{
                'operation': p['operator'], 'query': '', 'patient_id': '', 'drug_class': p['drug_class'],
                'view': p['view'], 'purpose': node['purpose']}], 'coverage': 'supported_observation',
                'unmet_requirements': [], 'cohort': cohorts.empty(), 'analysis_window': p['analysis_window']}, as_of)
        elif op == 'retrieve':
            if len(inputs) != 1 or set(p) != {'query', 'analysis_window'} or not isinstance(p['query'], str) or not 1 <= len(p['query']) <= 1500:
                raise ValueError('Retrieve needs one population and bounded query/window')
            p = {**p, 'analysis_window': validate_window(p['analysis_window'], as_of)}
        elif op == 'forecast':
            if len(inputs)!=1 or set(p)!={'model'} or p['model'] not in ('all','logistic','boosted','boosted_blocks'):
                raise ValueError('Forecast needs one population and a registered model')
        elif op == 'observation_process':
            from .graph_observation import validate as validate_observation
            p=validate_observation(p,as_of)
        elif op == 'source_changes':
            if len(inputs)!=1 or set(p)!={'streams'} or not isinstance(p['streams'],list) or not 1<=len(p['streams'])<=5:
                raise ValueError('Source changes need one population and one to five streams')
            if any(not isinstance(s,str) or s not in cohorts.RECORD_FIELDS for s in p['streams']) or len(set(p['streams']))!=len(p['streams']):
                raise ValueError('Distinct mapped source streams required')
        elif op == 'review':
            if len(inputs)!=1 or set(p)!={'providers','question','maximum_patients'}:
                raise ValueError('Explicit bounded patient review required')
            if not isinstance(p['providers'],list) or not 1<=len(p['providers'])<=2 or any(x not in ('jev','perplexity') for x in p['providers']) or len(set(p['providers']))!=len(p['providers']):
                raise ValueError('Use named Jev/Perplexity review routes')
            if type(p['maximum_patients']) is not int or not 1<=p['maximum_patients']<=3 or not isinstance(p['question'],str) or not 1<=len(p['question'])<=1000:
                raise ValueError('One to three sampled patient reviews and a bounded assertion question required')
        elif op in ('sequence','longitudinal'):
            from .longitudinal import validate_parameters
            if len(inputs)!=1:raise ValueError('Longitudinal operation requires one full population')
            p=validate_parameters(p,as_of,'sequence' if op=='sequence' else 'context')
        elif op == 'appointment_followthrough':
            from .appointment_graph import validate as validate_appointments
            p = validate_appointments(p, as_of)
        elif op == 'functional_review':
            if len(inputs)!=1:raise ValueError('Functional review requires one complete population')
        elif op == 'domain_review':
            if len(inputs)!=1:raise ValueError('Domain review requires one complete population')
        elif op == 'worklist':
            from .program_work import definition
            support=[seen[x] for x in inputs[1:]]
            if not 1<=len(inputs)<=4 or len(set(support))!=len(support) or any(x not in ('accountability_review','program_acquisition','measure_review') for x in support) or set(p) != {'program'}:
                raise ValueError('Worklist needs one population, distinct optional accountability/acquisition results, and a registered program')
            definition(p['program'])
        elif op == 'state_review':
            from .state_contracts import definition
            if len(inputs)!=1 or set(p)!={'contract'}:
                raise ValueError('State review needs one complete population and an evidence contract')
            if p['contract']!='all':definition(p['contract'])
        elif op == 'program_document':
            from .program_documents import validate as validate_document
            if len(inputs)!=1:raise ValueError('Program document needs one complete selected population')
            p=validate_document(p)
        elif op == 'reference_review':
            from .graph_references import validate as validate_reference
            p=validate_reference(p,as_of,inputs,normalized)
        elif op == 'measure_review':
            from .measure_composition import validate as validate_measure
            if len(inputs)!=1:raise ValueError('Measure needs one complete population')
            p=validate_measure(p)
        elif op == 'evidence_portfolio':
            from .evidence_portfolio import validate as validate_portfolio
            if len(inputs)!=1:raise ValueError('Evidence portfolio requires one complete selected population')
            p=validate_portfolio(p)
        elif op == 'program_acquisition':
            from .program_acquisition import validate as validate_program_acquisition
            if len(inputs)!=1:raise ValueError('Program acquisition needs one complete selected population')
            p=validate_program_acquisition(p)
        elif op == 'program_document_change':
            from .program_document_change import validate as validate_document_change
            if len(inputs)!=1:raise ValueError('Quoted-rule comparison needs one complete selected population')
            p=validate_document_change(p)
        elif op == 'protocol_change':
            from .protocol_change import validate as validate_change
            if len(inputs)!=1:raise ValueError('Protocol comparison needs one complete selected population')
            p=validate_change(p)
        elif op == 'accountability_review':
            from .program_accountability import validate as validate_accountability
            if len(inputs)!=1:raise ValueError('Accountability review needs one complete selected population')
            p=validate_accountability(p,as_of)
        elif op == 'financial_accounts':
            from .financial_accounts import validate as validate_financial
            if len(inputs)!=1:raise ValueError('Financial accounts need one complete selected population')
            p=validate_financial(p,as_of)
        elif op == 'decision_landmark':
            from .decision_landmarks import validate as validate_landmark
            if len(inputs)!=1:raise ValueError('Decision landmark needs one complete selected population')
            p=validate_landmark(p,as_of)
        elif op == 'baseline':
            from .baseline_evidence import validate as validate_baseline
            if len(inputs)!=1 or seen[inputs[0]]!='outcome':raise ValueError('Baseline requires one direct upstream outcome population')
            p=validate_baseline(p)
        elif op == 'protocol_review':
            from .care_protocols import validate as validate_protocol
            if len(inputs)!=1:raise ValueError('Protocol review needs one complete selected population')
            p=validate_protocol(p)
        elif op == 'outcome':
            from .outcomes import validate as validate_outcome
            if len(inputs)!=1:raise ValueError('Outcome needs one complete selected population')
            p=validate_outcome(p,as_of)
        elif op == 'requirements':
            from .evidence_requirements import validate as validate_requirements
            if len(inputs)!=1:raise ValueError('Requirements need one complete selected population')
            p=validate_requirements(p)
        elif op in ('source_state', 'pre_event_state'):
            if len(inputs)!=1:raise ValueError('Source state needs one complete selected population')
        elif op == 'interpretation':
            from .interpretation_state import validate as validate_interpretation
            if len(inputs)!=1:raise ValueError('Interpretation needs one complete selected population')
            p=validate_interpretation(p)
        elif op == 'assertion_review':
            if len(inputs)!=1:raise ValueError('Assertion review needs one complete selected population')
        elif op in ('context','context_passes'):
            if len(inputs)!=1:raise ValueError('Context needs one complete selected population')
        elif op == 'medication_identity':
            if len(inputs)!=1 or p:raise ValueError('Medication identity needs one complete population and no invented parameters')
        if op == 'pre_event_state':
            parent = next(n for n in normalized if n['id'] == inputs[0])
            if parent['parameters']['filter'] != 'all':
                raise ValueError('Pre-event state requires all-state outcome retention; missing indices must remain visible')
        normalized.append({**node, 'parameters': p})
        seen[ident] = op
    # Work adapters assess at the current snapshot. Reject a temporal mismatch
    # through any set-operation chain before executing or creating proposals.
    appointment_ancestors = {}
    for node in normalized:
        parents = set().union(*(appointment_ancestors[x] for x in node['inputs']))
        if node['operation'] == 'appointment_followthrough':
            parents.add(node['parameters']['available_by'])
        appointment_ancestors[node['id']] = parents
        if node['operation'] == 'worklist' and any(day != as_of for day in parents):
            raise ValueError('Historical appointment evidence cannot feed current-snapshot work')
    by_id = {n['id']: n for n in normalized}
    for node in normalized:
        operands = node['inputs'][1:] if node['operation'] == 'compare' else node['inputs'] if node['operation'] in ('intersect', 'union', 'difference') else []
        for ref in operands:
            parent = by_id[ref]
            if parent['operation'] in ('source_state', 'pre_event_state', 'appointment_followthrough') and parent['parameters']['filter'] == 'all':
                raise ValueError('All-state retention is not a predicate for overlap or comparison; select an explicit state filter')
    return {**plan, 'nodes': normalized}


def membership_receipt(value):
    return {'counts': {'evaluated': len(value.universe), 'matched': len(value.yes),
                       'not_matched': len(value.no), 'unknown': len(value.unknown)},
            'membership_digest': digest({'matched': sorted(value.yes), 'unknown': sorted(value.unknown)}),
            'examples': {'matched': sorted(value.yes)[:10], 'unknown': sorted(value.unknown)[:10]},
            'scope': 'Full observed patient membership; nonmatch is not absence of care.'}


def evidence_connection(path, meta):
    link = json.loads((path / 'annotation-link.json').read_text())
    root = Path(link['path'])
    evidence_meta = json.loads((root / 'run.json').read_text())
    if (evidence_meta.get('synthetic') is not True or evidence_meta.get('schema') != evidence_queries.VERSION
            or evidence_meta['source_run'] != meta['source_run']
            or evidence_meta['snapshot'] != meta['snapshot']
            or evidence_meta['model_identity'] != link['model_identity']):
        raise ValueError('Evidence index and warehouse snapshot/model do not agree')
    db = sqlite3.connect((root / 'evidence.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
    return db, evidence_meta


def evidence_membership(db, plan, universe):
    if {r[0] for r in db.execute('SELECT id FROM patients')} != universe:
        raise ValueError('Evidence and warehouse patient universes differ')
    parts, params = [], []
    for clause in plan['clauses']:
        where, values = evidence_queries.predicate(clause)
        negate = 'NOT ' if clause['observation'] == 'not_observed' else ''
        parts.append(f'{negate}EXISTS (SELECT 1 FROM evidence e WHERE e.patient=p.id AND {where})')
        params.extend(values)
    expression = (' AND ' if plan['match'] == 'all' else ' OR ').join(parts)
    yes = {r[0] for r in db.execute('SELECT id FROM patients p WHERE ' + expression, params)}
    # This is the complete *indexed-observation* query. It says nothing about
    # semantic extraction recall, outside care or the actual clinical state.
    traces = []
    for pid in sorted(yes)[:5]:
        checks = []
        for clause in plan['clauses']:
            where, values = evidence_queries.predicate(clause)
            count = db.execute('SELECT count(*) FROM evidence e WHERE e.patient=? AND ' + where, [pid, *values]).fetchone()[0]
            support = [{'evidence_id': r[0], 'recorded_evidence': json.loads(r[1])} for r in db.execute(
                'SELECT id,payload FROM evidence e WHERE e.patient=? AND ' + where + ' ORDER BY id LIMIT 2', [pid, *values])]
            checks.append({'clause': clause, 'matching_occurrences': count, 'support': support})
        traces.append({'patient_id': pid, 'checks': checks})
    return Membership(universe, yes, set()), {'evidence_examples': traces, 'predicate': plan,
        'authority': 'Structured observations and unvalidated text annotations. Nonmatch is only absence from this index.'}


def compare_populations(base, endpoint, groups):
    rows = []
    for name, group in groups:
        if endpoint.universe != base.universe or group.universe != base.universe:
            raise ValueError('Comparison universes differ')
        eligible = base.yes - endpoint.unknown - group.unknown
        positive, negative = eligible & group.yes, eligible & group.no
        a, b = positive & endpoint.yes, positive & endpoint.no
        c, d = negative & endpoint.yes, negative & endpoint.no
        row = {'group_node': name, 'base_patients': len(base.yes), 'base_unknown': len(base.unknown),
               'patients_evaluated': len(eligible), 'excluded_unknown_endpoint_or_group': len(base.yes - eligible),
               'cells': {'group_and_endpoint': len(a), 'group_without_observed_endpoint': len(b),
                         'complement_and_endpoint': len(c), 'complement_without_observed_endpoint': len(d)},
               'cell_examples': {k: sorted(v)[:3] for k, v in [
                   ('group_and_endpoint', a), ('group_without_observed_endpoint', b),
                   ('complement_and_endpoint', c), ('complement_without_observed_endpoint', d)]}}
        if not positive or not negative:
            row.update(status='insufficient_comparison_groups', nominal_fisher_p=None, nominal_bh_q=None,
                       risk_difference=None, rates=[])
        else:
            rates = facility_shrinkage([{'group': 'predicate_matched', 'eligible': len(positive), 'events': len(a)},
                                       {'group': 'predicate_not_matched', 'eligible': len(negative), 'events': len(c)}])
            for rate in rates:
                rate['observed_rate'] = rate['events'] / rate['eligible']
            odds, p = fisher_exact([[len(a), len(b)], [len(c), len(d)]])
            row.update(status='exploratory_comparison', rates=rates,
                       risk_difference=len(a)/len(positive)-len(c)/len(negative),
                       nominal_fisher_p=float(p), odds_ratio=float(odds) if math.isfinite(odds) else None,
                       odds_ratio_nonfinite=not math.isfinite(odds))
        rows.append(row)
    computed = [row for row in rows if row['nominal_fisher_p'] is not None]
    for row, q in zip(computed, benjamini_hochberg([r['nominal_fisher_p'] for r in computed])):
        row['nominal_bh_q'] = q
    return {'comparisons': rows, 'comparison_family': [name for name, _ in groups],
            'computed_comparisons': len(computed), 'multiple_testing_method': 'Benjamini-Hochberg within this node only',
            'inference_validity_verified': False, 'causal_effect_estimated': False, 'forecast': None,
            'clinical_recommendation': None, 'limitations': LIMITS}


def code_identity():
    from .analytic_registry import code_identity as analytic_identity
    return digest([analytic_identity(), cohorts.code_identity(),
                   *[hashlib.sha256(p.read_bytes()).hexdigest() for p in
                     [Path(__file__), Path(evidence_queries.__file__), Path(__file__).with_name('graph_contracts.py'),
                      Path(__file__).with_name('warehouse_retrieval.py'),
                      Path(__file__).with_name('warehouse_prediction.py'),
                      Path(__file__).with_name('longitudinal.py'), Path(__file__).with_name('survival.py'),
                      Path(__file__).with_name('monitor.py'), Path(__file__).with_name('claims_review.py'),
                      Path(__file__).with_name('risk_adjustment.py'), Path(__file__).parents[3] / 'config' / 'agere' / 'risk_adjustment' / 'manifest.json',
                      Path(__file__).parents[3] / 'config' / 'agere' / 'risk_adjustment' / 'payment_parameters.json',
                      Path(__file__).with_name('market.py'),
                      Path(__file__).with_name('briefing.py'), Path(__file__).with_name('briefing_rank.py'),
                      Path(__file__).with_name('demand.py'),
                      Path(__file__).with_name('profile.py'),
                      Path(__file__).with_name('early_signal.py'),
                      Path(__file__).with_name('vbc_knowledge.py'), *sorted((Path(__file__).resolve().parents[3] / 'config/agere/knowledge').glob('*.jsonl')),
                      *[Path(__file__).with_name(n) for n in ('program_work.py','program_work_current.py','program_work_v1.py','functional_programs.py','functional_review.py','registered_program_work.py','domain_programs.py','domain_review.py','work_adapters.py')],Path(__file__).with_name('program_documents.py'),Path(__file__).with_name('program_document_change.py'),
                      Path(__file__).with_name('program_acquisition.py'),Path(__file__).with_name('evidence_portfolio.py'),Path(__file__).with_name('program_resolution.py'),Path(__file__).with_name('measure_composition.py'),
                      Path(__file__).with_name('requirement_work.py'), Path(__file__).with_name('graph_decision.py'), Path(__file__).with_name('graph_criteria.py'), Path(__file__).with_name('graph_reports.py'), Path(__file__).with_name('graph_observation.py'), Path(__file__).with_name('graph_references.py'),
                      Path(__file__).with_name('clinical_state.py'),
                      Path(__file__).with_name('state_contracts.py'),
                      Path(__file__).with_name('context_builder.py'),
                      Path(__file__).with_name('context_passes.py'),
                      Path(__file__).with_name('assertion_reconciliation.py'),
                      Path(__file__).with_name('interpretation_state.py'),
                      Path(__file__).with_name('pre_event_state.py'), Path(__file__).with_name('source_state.py'), Path(__file__).with_name('native_assertions.py'),
                      Path(__file__).with_name('native_care_assertions.py'),
                      Path(__file__).with_name('source_structure.py'),
                      Path(__file__).with_name('evidence_requirements.py'),
                      Path(__file__).with_name('native_requirements.py'),
                      Path(__file__).with_name('documentary_contracts.json'),
                      *[Path(__file__).with_name(n) for n in ('care_protocols.py','care_protocols.json','care_protocol_history.json','protocol_change.py','clinical_inputs.py','clinical_input_maps.json','selection_context.py')],
                      *[Path(__file__).with_name(n) for n in ('baseline_evidence.py','baseline_profiles.json','baseline_input_maps.json','outcome_readiness.py')],
                      Path(__file__).with_name('outcomes.py'),
                      Path(__file__).with_name('financial_accounts.py'),
                      Path(__file__).with_name('financial_account_maps.json'),
                      *[Path(__file__).with_name(n) for n in ('program_accountability.py','accountability_contracts.json','accountability_source_maps.json')],
                      Path(__file__).with_name('decision_landmarks.py'),
                      Path(__file__).with_name('landmark_contracts.json'),
                      Path(__file__).with_name('outcome_contracts.json'),
                      Path(__file__).with_name('medication_identity.py'),
                      Path(__file__).with_name('source_blocks.py'),
                      Path(__file__).with_name('program_effect.py'),
                      Path(__file__).with_name('outreach.py'), Path(__file__).with_name('outreach_bandit.py'),
                      Path(__file__).with_name('pathway.py'),
                      Path(__file__).with_name('conformal.py'), Path(__file__).with_name('calibration.py'),
                      Path(__file__).with_name('scheduling.py')]]])


def result_cache_enabled():
    """AGERE_RESULT_CACHE=off computes every graph/cohort afresh and writes nothing (cold-path profiling, read-only stacks)."""
    import os
    return os.environ.get('AGERE_RESULT_CACHE', 'on') != 'off'


def _json_default(value):
    """Engines compute with numpy; persist its scalars/arrays as plain JSON (anything else is a bug and still raises)."""
    import numpy as np
    if isinstance(value, np.generic): return value.item()
    if isinstance(value, np.ndarray): return value.tolist()
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')


_VOCABULARY = {}
_RECORDS_DERIVED = {}


def _records_memo(name, path, compute):
    """Memoise a value computed only from stack records, keyed by the records data version (record_store.token)."""
    from . import record_store
    token = record_store.token(path)
    if token is None: return compute(path)
    key = (name, str(Path(path).resolve()), token)
    if key not in _RECORDS_DERIVED:
        _RECORDS_DERIVED.clear() if len(_RECORDS_DERIVED) > 64 else None
        _RECORDS_DERIVED[key] = compute(path)
    return copy.deepcopy(_RECORDS_DERIVED[key])


def _evidence_vocabulary(path, db, evidence_meta):
    """Observed evidence vocabulary (~0.25-2 s of GROUP BY/DISTINCT scans), memoised per evidence file version.

    Key: the evidence index file's identity (path, size, mtime) and model identity, so a rebuilt or
    modified index is re-read; the queries and their results are unchanged."""
    root = Path(json.loads((Path(path) / 'annotation-link.json').read_text())['path'])
    files = [root / 'evidence.sqlite3', *(root / f'evidence.sqlite3{x}' for x in ('-wal',))]
    key = (str(root.resolve()), evidence_meta['model_identity'],
           tuple((f.stat().st_size, f.stat().st_mtime_ns) if f.exists() else None for f in files))
    if key in _VOCABULARY: return _VOCABULARY[key]
    concepts = [{'source': kind, 'concept': concept, 'observations': n} for kind, concept, n in
                db.execute('SELECT kind,concept,count(*) FROM evidence GROUP BY kind,concept ORDER BY kind,concept')]
    axes = {field: [r[0] for r in db.execute(f'SELECT DISTINCT {field} FROM evidence WHERE kind=? ORDER BY {field}', ('text',))]
            for field in sorted(evidence_queries.FIELDS - {'concept'})}
    scalar_values = {}
    boolean_concepts = []
    for row in concepts:
        if row['source'] != 'structured': continue
        values = [json.loads(r[0]) for r in db.execute(
            "SELECT DISTINCT value_json FROM evidence WHERE kind='structured' AND concept=? ORDER BY value_json LIMIT 17", (row['concept'],))]
        if values and all(type(v) is bool for v in values): boolean_concepts.append(row['concept'])
        if len(values)<=16 and all(type(v) in (str,bool) for v in values): scalar_values[row['concept']]=values
    _VOCABULARY.clear()
    _VOCABULARY[key] = (concepts, axes, scalar_values, boolean_concepts)
    return _VOCABULARY[key]


def catalog(path):
    from .graph_references import catalog as reference_catalog
    from .native_care_assertions import concepts as care_concepts
    from .program_documents import packages as document_packages
    from .evidence_requirements import definitions
    from .outcomes import definitions as outcome_definitions
    from .care_protocols import definitions as protocol_definitions
    from .care_protocols import read_catalog, CATALOG as PROTOCOL_CATALOG, HISTORY as PROTOCOL_HISTORY
    from .baseline_evidence import profiles as baseline_profiles
    from .decision_landmarks import definitions as landmark_definitions
    path = Path(path)
    meta = json.loads((path / 'run.json').read_text())
    db, evidence_meta = evidence_connection(path, meta)
    try:
        concepts, axes, scalar_values, boolean_concepts = copy.deepcopy(_evidence_vocabulary(path, db, evidence_meta))
    finally:
        db.close()
    forecast_info={'available':False}
    if (path/'forecast-link.json').exists():
        link=json.loads((path/'forecast-link.json').read_text());card=json.loads((Path(link['path'])/'model-card.json').read_text())
        forecast_info={'available':True,'models':list(card['models']),'as_of':card['prediction_as_of'],
                       'through':card['prediction_through'],'target':card['target'],'status':'research_only'}
    longitudinal_info={'available':False}
    if (path/'longitudinal-link.json').exists():
        from .longitudinal import connect
        ldb,lmeta=connect(path);ldb.close()
        longitudinal_info={'available':True,'vocabulary':lmeta['vocabulary'],'clocks':['source_event','arrival','clinical','reference']}
    return {'schema': VERSION, 'operations': sorted(OPERATIONS), 'evidence_concepts': concepts,
            'native_care_concepts': sorted(_records_memo('care_concepts', path, care_concepts)),
            'program_documents':document_packages(path), 'scientific_references':reference_catalog(path),
            'appointment_followthrough':{'contract':'first_received_booking_terminal_7d_receipt_14d','authority':'received_source_review_only'},
            'functional_review':{'filters':['source_review_candidates','source_available','source_absent'],'program':'functional_support_review','authority':'dated_source_review_only','clinical_quality_validated':False},
            'domain_review':{'domains':['respiratory','behavioral'],'filters':['source_review_candidates','source_available','source_absent'],'programs':{'respiratory':'respiratory_source_review','behavioral':'behavioral_source_review'},'authority':'received_source_review_only','clinical_quality_validated':False},
            'documentary_contracts':list(definitions().values()),
            'outcome_contracts':list(outcome_definitions().values()),
            'care_protocols':list(protocol_definitions().values()),
            'protocol_versions':sorted(set(read_catalog(PROTOCOL_CATALOG))|set(read_catalog(PROTOCOL_HISTORY))),
            'baseline_profiles':list(baseline_profiles().values()),
            'landmark_contracts':list(landmark_definitions().values()),
            'text_axis_values': axes, 'evidence_identity': evidence_meta['model_identity'],
            'structured_scalar_values': scalar_values, 'boolean_valued_concepts': boolean_concepts,
            'forecast':forecast_info,'longitudinal':longitudinal_info,
            'clinical_state_available':(path/'clinical-state-link.json').exists(),
            'medication_terminology_available':(path/'medication-terminology-link.json').exists(), 'typed_review_providers':['jev','perplexity'],
            'limits': LIMITS, 'maximum_nodes': 16, 'membership_preview_is_complete': False,
            'population_calculations_use_full_membership': True}


def execute(stack, plan, *, question=None):
    from .analytic_registry import execute as analytic_execute
    started = time.perf_counter()
    path, meta = stack.path, stack.retriever.meta
    plan = validate(plan, meta['snapshot']['as_of'])
    if question is not None and (not isinstance(question,str) or not 1 <= len(question) <= 1500):
        raise ValueError('Bounded original graph question required')
    db = sqlite3.connect(path / 'stack.sqlite3')
    evidence_db = None
    try:
        universe = {r[0] for r in db.execute('SELECT id FROM patients')}
        evidence_db, evidence_meta = evidence_connection(path, meta)
        # Validate every narrative vocabulary before running any graph node.
        observed = catalog(path)
        concepts = {(r['source'], r['concept']) for r in observed['evidence_concepts']}
        for node in plan['nodes']:
            if node['operation'] in ('sequence','longitudinal'):
                from .longitudinal import validate_parameters
                if not observed['longitudinal']['available']:raise ValueError('No longitudinal artifact attached')
                validate_parameters(node['parameters'],meta['snapshot']['as_of'],
                                    'sequence' if node['operation']=='sequence' else 'context',observed['longitudinal']['vocabulary'])
            if node['operation']=='review' and not stack.allow_live:
                raise ValueError('Live model route disabled on this server')
            if node['operation']=='forecast' and not observed['forecast']['available']:
                raise ValueError('No fitted forecast artifact is attached')
            if node['operation'] == 'evidence':
                for clause in node['parameters']['plan']['clauses']:
                    if not any(kind == clause['source'] and (concept.startswith(clause['concept']) if clause['concept_match'] == 'prefix' else concept == clause['concept']) for kind, concept in concepts):
                        raise ValueError('Unmapped evidence concept; cannot interpret missing vocabulary as negative evidence')
                    if clause['source'] == 'text' and any(clause[k] not in observed['text_axis_values'][k] for k in observed['text_axis_values'] if k in clause):
                        raise ValueError('Unmapped narrative axis value')
                    boolean_matches = [concept for concept in observed['boolean_valued_concepts'] if
                                       (concept.startswith(clause['concept']) if clause['concept_match']=='prefix' else concept==clause['concept'])]
                    if clause['source']=='structured' and boolean_matches and type(clause.get('value_equals')) is not bool:
                        raise ValueError('Boolean-valued evidence requires explicit true/false comparison; screen presence is not a positive screen')
        retrieval_identity = [getattr(getattr(stack.retriever, 'encoder', None), 'identity', None),
                              getattr(getattr(stack.retriever, 'reranker', None), 'identity', None)]
        # Program versions affect downstream analyses even when source data does not change.
        from .program_registry import packages
        program_identity = packages(path) if any(n['operation']=='analyze' and n['parameters']['operator']=='program_rule_review' for n in plan['nodes']) else None
        forecast_identity=json.loads((path/'forecast-link.json').read_text()) if any(n['operation']=='forecast' for n in plan['nodes']) else None
        longitudinal_identity=json.loads((path/'longitudinal-link.json').read_text()) if any(n['operation'] in ('sequence','longitudinal') for n in plan['nodes']) else None
        review_identity=None
        if any(n['operation']=='review' for n in plan['nodes']):
            from ..gateway import PersistentGateway
            review_identity={'models':PersistentGateway.MODELS,
                'code':[hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                        for name in ('stack_planner.py','model_reader.py','stack_runtime.py')]}
        from . import functional_review as functional_sources
        functional_population=functional_sources.prepare(path) if any(n['operation']=='functional_review' for n in plan['nodes']) else None
        from . import domain_review as domain_sources
        domain_populations={d:domain_sources.prepare(path,d) for d in sorted({n['parameters']['domain'] for n in plan['nodes'] if n['operation']=='domain_review'})}
        from . import appointment_graph
        appointment_dependency = (appointment_graph.dependency(path)
            if any(n['operation']=='appointment_followthrough' for n in plan['nodes']) else None)
        work_programs = [n['parameters']['program'] for n in plan['nodes'] if n['operation'] == 'worklist']
        from .program_work import preparation_identity
        work_identity = preparation_identity(path, work_programs) if work_programs else None
        state_identity=None
        if any(n['operation']=='state_review' for n in plan['nodes']):
            from .clinical_state import attached
            _,state_metadata=attached(path)
            state_identity=state_metadata['identity']
        medication_identity=None
        if any(n['operation']=='medication_identity' for n in plan['nodes']):
            from .medication_identity import identity
            medication_identity=identity(path)
        context_identities=[];interpretation_snapshots={};outcome_preparations={};protocol_preparations={};baseline_preparations={};landmark_preparations={};financial_preparations={};accountability_preparations={};change_preparations={}
        document_preparations={};document_change_preparations={};acquisition_preparations={};measure_preparations={};reference_preparations={};observation_preparations={}
        for n in plan['nodes']:
            if n['operation']=='observation_process':
                from .graph_observation import prepare as prepare_observation
                observation_preparations[n['id']]=prepare_observation(path,n['parameters'])
                context_identities.append(observation_preparations[n['id']]['identity'])
            if n['operation']=='reference_review':
                from .graph_references import prepare as prepare_reference
                reference_preparations[n['id']]=prepare_reference(path,n['parameters'])
                context_identities.append(reference_preparations[n['id']]['id'])
            if n['operation']=='measure_review':
                from .measure_composition import prepare as prepare_measure
                measure_preparations[n['id']]=prepare_measure(path,n['parameters'])
                context_identities.append(measure_preparations[n['id']]['identity'])
            if n['operation']=='evidence_portfolio':
                from .evidence_portfolio import dependency as portfolio_dependency
                context_identities.append(portfolio_dependency(path,n['parameters']))
            if n['operation']=='program_acquisition':
                from .program_acquisition import dependency
                acquisition_preparations[n['id']]=dependency(path,n['parameters'])
                context_identities.append(acquisition_preparations[n['id']]['identity'])
            if n['operation']=='program_document_change':
                from .program_document_change import prepare as prepare_document_change
                document_change_preparations[n['id']]=prepare_document_change(path,n['parameters'])
                context_identities.append(document_change_preparations[n['id']]['identity'])
            if n['operation']=='program_document':
                from .program_documents import prepare as prepare_document
                document_preparations[n['id']]=prepare_document(path,n['parameters'])
                context_identities.append(document_preparations[n['id']]['identity'])
            if n['operation']=='protocol_change':
                from .protocol_change import prepare as prepare_change
                change_preparations[n['id']]=prepare_change(path,n['parameters'])
                context_identities.append(change_preparations[n['id']]['identity'])
            if n['operation']=='accountability_review':
                from .program_accountability import prepare as prepare_accountability
                accountability_preparations[n['id']]=prepare_accountability(path,n['parameters'])
                context_identities.append(accountability_preparations[n['id']]['identity'])
            if n['operation']=='financial_accounts':
                from .financial_accounts import prepare as prepare_financial
                financial_preparations[n['id']]=prepare_financial(path,n['parameters'])
                context_identities.append(financial_preparations[n['id']]['identity'])
            if n['operation']=='decision_landmark':
                from .decision_landmarks import prepare as prepare_landmark
                landmark_preparations[n['id']]=prepare_landmark(path,n['parameters'])
                context_identities.append(landmark_preparations[n['id']]['identity'])
            if n['operation']=='baseline':
                from .baseline_evidence import prepare as prepare_baseline
                baseline_preparations[n['id']]=prepare_baseline(path,n['parameters'],outcome_preparations[n['inputs'][0]])
                context_identities.append(baseline_preparations[n['id']]['identity'])
            if n['operation']=='protocol_review':
                from .care_protocols import prepare as prepare_protocol
                protocol_preparations[n['id']]=prepare_protocol(path,n['parameters'])
                context_identities.append(protocol_preparations[n['id']]['identity'])
            if n['operation']=='outcome':
                from .outcomes import prepare as prepare_outcome
                outcome_preparations[n['id']]=prepare_outcome(path,n['parameters'])
                context_identities.append(outcome_preparations[n['id']]['identity'])
            if n['operation']=='requirements':
                from .evidence_requirements import snapshot as requirement_snapshot
                interpretation_snapshots[n['id']]=requirement_snapshot(path,n['parameters'])
                context_identities.append(interpretation_snapshots[n['id']]['identity'])
            if n['operation'] in ('source_state', 'pre_event_state'):
                from .source_state import scope
                from .interpretation_state import snapshot
                interpretation_snapshots[n['id']]=snapshot(path,scope(n['parameters']))
                context_identities.append(interpretation_snapshots[n['id']]['identity'])
            if n['operation']=='interpretation':
                from .interpretation_state import snapshot
                interpretation_snapshots[n['id']]=snapshot(path,n['parameters'])
                context_identities.append(interpretation_snapshots[n['id']]['identity'])
            if n['operation']=='assertion_review':
                from .assertion_reconciliation import dependency
                context_identities.append(dependency(path,n['parameters'])[0])
            if n['operation'] in ('context','context_passes'):
                from .context_builder import dependency_identity
                context_identities.append(dependency_identity(path,n['parameters'])[0])
                if n['operation']=='context_passes':
                    from .context_passes import code_identity as pass_identity
                    context_identities.append(pass_identity())
        if functional_population:context_identities.append(functional_population['id'])
        context_identities.extend(r['id'] for r in domain_populations.values())
        if appointment_dependency:
            context_identities.append({'appointment_sources':appointment_dependency,'original_question':question})
        context_identities.append({'original_question': question})
        key = digest([VERSION, code_identity(), meta['source_digest'], evidence_meta['model_identity'],
                      retrieval_identity, program_identity, forecast_identity, review_identity, longitudinal_identity, work_identity, state_identity, medication_identity, context_identities, plan])
        setup_seconds = time.perf_counter() - started
        old = db.execute('SELECT payload FROM result_cache WHERE id=?', (key,)).fetchone() if result_cache_enabled() else None
        if old:
            replay = json.loads(old[0])
            if isinstance(replay.get('answer'), dict):  # reference library may be newer than the cached result
                replay['answer']['benchmark'] = benchmark_references(replay['answer'], question, plan['nodes'][-1]['operation'] if plan.get('nodes') else None)
            return {**replay, 'cache_hit': True, 'new_paid_api_calls':0,'elapsed_seconds': time.perf_counter()-started,
                    'compute': {'new_graph_nodes': 0, 'aggregate_result_replayed': True}}
        memberships, results = {}, [];paid_calls=0;requirement_ancestors={};protocol_ancestors={};document_ancestors={}
        node_index={n['id']:n for n in plan['nodes']}
        appointment_ancestors = {}
        node_seconds = []
        for node in plan['nodes']:
            op, params = node['operation'], node['parameters']
            value, detail = None, {}
            node_started = time.perf_counter()
            if op == 'cohort':
                try:
                    selected = cohorts.select(path, params['specification'])
                except grounding.GroundingError as exc:
                    # The data cannot support this predicate: report it, don't
                    # convert a binding failure into patient-level unknowns.
                    value = Membership(universe, set(), set(universe))
                    detail = {'specification': params['specification'], 'grounding_failed': True,
                              'grounding': exc.issues}
                else:
                    value = Membership(universe, set(selected['patient_ids']), set(selected['unknown_patient_ids']))
                    detail = {'specification': selected['specification'], 'source_examples': selected['examples'],
                              'selection_receipt': selected['id'], 'selection_cache_hit': selected['cache_hit'],
                              'grounding': selected.get('grounding', []), 'interpretation': selected.get('interpretation', [])}
            elif op == 'aggregate':
                from . import aggregate
                chosen = memberships[node['inputs'][0]]
                detail = aggregate.execute(path, sorted(chosen.yes), params)
                detail['input_membership'] = membership_receipt(chosen)
            elif op == 'trajectory':
                from . import trajectory
                chosen = memberships[node['inputs'][0]]
                detail = trajectory.execute(path, sorted(chosen.yes), params)
                detail['input_membership'] = membership_receipt(chosen)
            elif op == 'survival':
                from . import survival
                chosen = memberships[node['inputs'][0]]
                detail = survival.execute(path, sorted(chosen.yes), params)
                detail['input_membership'] = membership_receipt(chosen)
            elif op == 'briefing':
                from . import briefing, briefing_rank
                detail = briefing_rank.serve(path, briefing.cached(path), record=False)
            elif op == 'pathway':
                from . import pathway
                chosen = memberships[node['inputs'][0]] if node['inputs'] else None
                detail = pathway.execute(path, sorted(chosen.yes) if chosen else None, params)
                if chosen: detail['input_membership'] = membership_receipt(chosen)
            elif op == 'discover':
                from . import discover
                chosen = memberships[node['inputs'][0]]
                detail = discover.execute(path, sorted(chosen.yes), params)
                detail['input_membership'] = membership_receipt(chosen)
            elif op == 'vbc':
                from . import vbc
                chosen = memberships[node['inputs'][0]] if node['inputs'] else None
                detail = vbc.execute(path, sorted(chosen.yes) if chosen else None, params, meta['snapshot']['as_of'])
                if chosen: detail['input_membership'] = membership_receipt(chosen)
            elif op == 'program_effect':
                from . import program_effect
                chosen = memberships[node['inputs'][0]] if node['inputs'] else None
                detail = program_effect.execute(path, sorted(chosen.yes) if chosen else None, params, meta['snapshot']['as_of'])
            elif op == 'outreach':
                from . import outreach
                chosen = memberships[node['inputs'][0]] if node['inputs'] else None
                detail = outreach.execute(path, sorted(chosen.yes) if chosen else None, params, meta['snapshot']['as_of'])
            elif op == 'knowledge':
                from . import vbc_knowledge
                ledger = None
                if vbc_knowledge.synthesis_enabled() and getattr(stack, 'allow_live', False):  # optional hosted synthesis, off by default
                    from ..gateway import BudgetLedger
                    ledger = BudgetLedger(Path(__file__).resolve().parents[3] / 'artifacts/agere-full/provider-budget.sqlite3')
                try:
                    detail = vbc_knowledge.execute(params, encoder=getattr(stack.retriever, 'encoder', None), allow_live=ledger is not None,
                                                   ledger=ledger, out=Path(__file__).resolve().parents[3] / 'artifacts/vbc-knowledge/synthesis')
                finally:
                    if ledger is not None: ledger.close()
                paid_calls += detail['new_paid_api_calls']
            elif op == 'early_signal':
                from . import early_signal
                chosen = memberships[node['inputs'][0]] if node['inputs'] else None
                detail = early_signal.execute(path, sorted(chosen.yes) if chosen else None, params, meta['snapshot']['as_of'])
                if chosen: detail['input_membership'] = membership_receipt(chosen)
            elif op == 'profile':
                from . import profile
                chosen = memberships[node['inputs'][0]] if node['inputs'] else None
                detail = profile.execute(path, sorted(chosen.yes) if chosen else None, params, meta['snapshot']['as_of'])
                if chosen: detail['input_membership'] = membership_receipt(chosen)
            elif op == 'scheduling':
                from . import scheduling
                chosen = memberships[node['inputs'][0]] if node['inputs'] else None
                detail = scheduling.execute(path, sorted(chosen.yes) if chosen else None, params, meta['snapshot']['as_of'])
                if chosen: detail['input_membership'] = membership_receipt(chosen)
            elif op == 'calibration':
                from . import calibration
                chosen = memberships[node['inputs'][0]] if node['inputs'] else None
                detail = calibration.execute(path, sorted(chosen.yes) if chosen else None, params, meta['snapshot']['as_of'])
            elif op == 'monitor':
                from . import monitor
                chosen = memberships[node['inputs'][0]] if node['inputs'] else None
                detail = monitor.execute(path, sorted(chosen.yes) if chosen else None, params, meta['snapshot']['as_of'])
                if chosen: detail['input_membership'] = membership_receipt(chosen)
            elif op == 'claims_review':
                from . import claims_review
                chosen = memberships[node['inputs'][0]] if node['inputs'] else None
                detail = claims_review.execute(path, sorted(chosen.yes) if chosen else None, params, meta['snapshot']['as_of'])
                if chosen: detail['input_membership'] = membership_receipt(chosen)
            elif op == 'risk_adjustment':
                from . import risk_adjustment
                chosen = memberships[node['inputs'][0]] if node['inputs'] else None
                detail = risk_adjustment.execute(path, sorted(chosen.yes) if chosen else None, params, meta['snapshot']['as_of'])
                if chosen: detail['input_membership'] = membership_receipt(chosen)
            elif op == 'market':
                from . import market
                chosen = memberships[node['inputs'][0]] if node['inputs'] else None
                detail = market.execute(path, sorted(chosen.yes) if chosen else None, params, meta['snapshot']['as_of'])
            elif op == 'demand':
                from . import demand
                chosen = memberships[node['inputs'][0]] if node['inputs'] else None
                detail = demand.execute(path, sorted(chosen.yes) if chosen else None, params, meta['snapshot']['as_of'])
                if chosen: detail['input_membership'] = membership_receipt(chosen)
            elif op == 'sql':
                from . import sql_query
                chosen = memberships[node['inputs'][0]] if node['inputs'] else None
                detail = sql_query.execute(path, params, sorted(chosen.yes) if chosen else None)
                if chosen: detail['input_membership'] = membership_receipt(chosen)
            elif op == 'explain':
                from . import explain
                chosen = memberships[node['inputs'][0]]
                detail = explain.execute(path, sorted(chosen.yes), params)
                detail['input_membership'] = membership_receipt(chosen)
            elif op == 'evidence':
                value, detail = evidence_membership(evidence_db, params['plan'], universe)
            elif op in ('intersect', 'union', 'difference'):
                value = combine(op, [memberships[x] for x in node['inputs']])
                detail = {'dependencies': node['inputs'], 'logic': 'Three-valued membership with unresolved predicates retained'}
            elif op == 'compare':
                base, endpoint, *groups = node['inputs']
                detail = compare_populations(memberships[base], memberships[endpoint], [(g, memberships[g]) for g in groups])
            elif op == 'analyze':
                chosen = memberships[node['inputs'][0]]
                detail = analytic_execute(path, params['operator'], drug_class=params['drug_class'] or None,
                                          view=params['view'], analysis_window=params['analysis_window'],
                                          patient_ids=sorted(chosen.yes))
                detail['input_membership'] = membership_receipt(chosen)
            elif op == 'retrieve':
                chosen = memberships[node['inputs'][0]]
                detail = stack.retriever.search(params['query'], patient_ids=sorted(chosen.yes), analysis_window=params['analysis_window'])
                detail['input_membership'] = membership_receipt(chosen)
            elif op == 'forecast':
                from .warehouse_prediction import forecast
                detail=forecast(path,sorted(memberships[node['inputs'][0]].yes),params['model'])
            elif op == 'observation_process':
                from .graph_observation import execute as observe_process
                chosen=memberships[node['inputs'][0]]
                detail=observe_process(path,sorted(chosen.yes),params,observation_preparations[node['id']])
            elif op == 'source_changes':
                from .source_blocks import source_summary
                chosen=memberships[node['inputs'][0]];streams=params['streams']
                rows=[json.loads(raw) for pid,raw in db.execute('SELECT patient,payload FROM records WHERE stream IN ('+','.join('?' for _ in streams)+')',streams) if pid in chosen.yes]
                detail=source_summary(rows,meta['snapshot']['as_of'],streams)
                detail['input_membership']=membership_receipt(chosen)
                detail['source_record_count']=len(rows)
            elif op == 'review':
                chosen=memberships[node['inputs'][0]];ids=sorted(chosen.yes)[:params['maximum_patients']];reviews=[]
                for pid in ids:
                    for provider in params['providers']:
                        review=stack.review(params['question'],pid,provider);reviews.append(review)
                        paid_calls+=review['new_paid_api_calls']
                detail={'providers':params['providers'],'selected_patients':ids,'population_patients':len(chosen.yes),
                        'sampling':'First patient IDs in the selected set; bounded review sample, not full-population adjudication.',
                        'reviews':reviews,'clinical_decision_authority':False}
            elif op=='sequence':
                from .longitudinal import sequence
                chosen=memberships[node['inputs'][0]]
                yes,unknown,detail=sequence(path,sorted(chosen.yes),params)
                value=Membership(universe,yes,unknown|chosen.unknown)
            elif op=='longitudinal':
                from .longitudinal import context
                chosen=memberships[node['inputs'][0]]
                detail=context(path,sorted(chosen.yes),params)
                detail['input_membership']=membership_receipt(chosen)
            elif op == 'appointment_followthrough':
                chosen=memberships[node['inputs'][0]]
                yes,unknown,detail=appointment_graph.membership(path,sorted(chosen.yes),params)
                detail['input_membership']=membership_receipt(chosen)
                value=Membership(universe,yes,unknown|chosen.unknown)
            elif op == 'functional_review':
                from .functional_programs import membership as functional_membership
                chosen=memberships[node['inputs'][0]]
                yes,unknown,detail=functional_membership(functional_population,chosen.yes,params)
                detail['input_membership']=membership_receipt(chosen)
                value=Membership(universe,yes,unknown|chosen.unknown)
            elif op == 'domain_review':
                from .domain_programs import membership as domain_membership
                chosen=memberships[node['inputs'][0]]
                yes,unknown,detail=domain_membership(domain_populations[params['domain']],chosen.yes,params)
                detail['input_membership']=membership_receipt(chosen)
                value=Membership(universe,yes,unknown|chosen.unknown)
            elif op == 'worklist':
                from .program_work import prepare
                chosen = memberships[node['inputs'][0]]
                from .requirement_work import contexts
                ancestors=requirement_ancestors.get(node['inputs'][0],set())
                decisions=contexts(path,chosen.yes,[node_index[k] for k in sorted(ancestors)],interpretation_snapshots,
                    evaluated_patients={k:memberships[node_index[k]['inputs'][0]].yes for k in ancestors}) if ancestors else None
                protocol_nodes=protocol_ancestors.get(node['inputs'][0],set())
                document_nodes=document_ancestors.get(node['inputs'][0],set());documents=None
                if document_nodes:
                    from .program_documents import contexts as document_contexts
                    documents=document_contexts(path,chosen.yes,[node_index[k] for k in sorted(document_nodes)],document_preparations,
                        evaluated_patients={k:memberships[node_index[k]['inputs'][0]].yes for k in document_nodes})
                accountability=None;acquisition=None;measure=None
                for support in node['inputs'][1:]:
                    attached=next(r['result'] for r in results if r['node']['id']==support)
                    if node_index[support]['operation']=='measure_review':
                        from .measure_composition import contexts as measure_contexts
                        measure=measure_contexts(path,attached['id'],chosen.yes)
                    elif node_index[support]['operation']=='program_acquisition':
                        from .program_acquisition import contexts as acquisition_contexts
                        acquisition=acquisition_contexts(path,attached['id'],chosen.yes)
                    else:
                        from .program_accountability import contexts as accountability_contexts
                        accountability=accountability_contexts(path,attached['id'],chosen.yes)
                if protocol_nodes or document_nodes or acquisition is not None or measure is not None:
                    from .selection_context import contexts as selection_contexts
                    decisions=selection_contexts(path,chosen.yes,[node_index[k] for k in sorted(protocol_nodes)],protocol_preparations,
                        evaluated_patients={k:memberships[node_index[k]['inputs'][0]].yes for k in protocol_nodes},documentary=decisions,program_documents=documents,program_acquisition=acquisition,measure=measure)
                binding=membership_receipt(chosen)
                appointment_nodes=appointment_ancestors.get(node['inputs'][0],set())
                if appointment_nodes:
                    reports=[r['result'] for r in results if r['node']['id'] in appointment_nodes]
                    binding['question_context']=appointment_graph.work_binding(path,reports,question)
                detail = prepare(path, params['program'], sorted(chosen.yes), input_membership=binding,decision_contexts=decisions,accountability_contexts=accountability)
            elif op == 'state_review':
                from .clinical_state import review
                chosen=memberships[node['inputs'][0]]
                detail=review(path,sorted(chosen.yes),params['contract'])
                detail['input_membership']=membership_receipt(chosen)
            elif op == 'program_document':
                from .program_documents import execute as evaluate_document
                chosen=memberships[node['inputs'][0]]
                yes,unknown,detail=evaluate_document(path,sorted(chosen.yes),params,prepared=document_preparations[node['id']])
                from .graph_criteria import member_index as criteria_index
                detail['review_members']=criteria_index(path,op,sorted(chosen.yes),params,document_preparations[node['id']],yes,unknown)
                detail['input_membership']=membership_receipt(chosen)
                value=Membership(universe,yes,unknown|chosen.unknown)
            elif op == 'reference_review':
                from .graph_references import execute as execute_reference
                chosen=memberships[node['inputs'][0]]
                detail=execute_reference(path,chosen.yes,params,reference_preparations[node['id']],results,node,question,membership_receipt(chosen))
            elif op == 'measure_review':
                from .measure_composition import execute as execute_measure
                chosen=memberships[node['inputs'][0]]
                detail=execute_measure(path,sorted(chosen.yes),params,prepared=measure_preparations[node['id']],selection=membership_receipt(chosen))
            elif op == 'evidence_portfolio':
                from .evidence_portfolio import execute as execute_portfolio
                chosen=memberships[node['inputs'][0]]
                detail=execute_portfolio(path,sorted(chosen.yes),params,selection=membership_receipt(chosen))
            elif op == 'program_acquisition':
                from .program_acquisition import prepare as prepare_acquisition
                chosen=memberships[node['inputs'][0]]
                detail=prepare_acquisition(path,sorted(chosen.yes),params,prepared=acquisition_preparations[node['id']],selection=membership_receipt(chosen))
            elif op == 'program_document_change':
                from .program_document_change import execute as evaluate_document_change
                chosen=memberships[node['inputs'][0]]
                detail=evaluate_document_change(path,sorted(chosen.yes),params,prepared=document_change_preparations[node['id']])
                detail['input_membership']=membership_receipt(chosen)
            elif op == 'protocol_change':
                from .protocol_change import execute as evaluate_change
                chosen=memberships[node['inputs'][0]]
                detail=evaluate_change(path,sorted(chosen.yes),params,prepared=change_preparations[node['id']])
                detail['input_membership']=membership_receipt(chosen)
            elif op == 'accountability_review':
                from .program_accountability import execute as evaluate_accountability
                chosen=memberships[node['inputs'][0]]
                detail=evaluate_accountability(path,sorted(chosen.yes),params,prepared=accountability_preparations[node['id']],selection=membership_receipt(chosen))
                detail['input_membership']=membership_receipt(chosen)
            elif op == 'financial_accounts':
                from .financial_accounts import execute as evaluate_financial
                chosen=memberships[node['inputs'][0]]
                detail=evaluate_financial(path,sorted(chosen.yes),params,prepared=financial_preparations[node['id']])
                detail['input_membership']=membership_receipt(chosen)
            elif op == 'decision_landmark':
                from .decision_landmarks import execute as evaluate_landmark
                chosen=memberships[node['inputs'][0]]
                detail=evaluate_landmark(path,sorted(chosen.yes),params,prepared=landmark_preparations[node['id']])
                detail['input_membership']=membership_receipt(chosen)
            elif op == 'baseline':
                from .baseline_evidence import execute as evaluate_baseline
                chosen=memberships[node['inputs'][0]]
                detail=evaluate_baseline(path,sorted(chosen.yes),params,prepared=baseline_preparations[node['id']])
                detail['input_membership']=membership_receipt(chosen)
            elif op == 'protocol_review':
                from .care_protocols import execute as evaluate_protocol
                chosen=memberships[node['inputs'][0]]
                yes,unknown,detail=evaluate_protocol(path,sorted(chosen.yes),params,prepared=protocol_preparations[node['id']])
                from .graph_criteria import member_index as criteria_index
                detail['review_members']=criteria_index(path,op,sorted(chosen.yes),params,protocol_preparations[node['id']],yes,unknown)
                detail['input_membership']=membership_receipt(chosen)
                value=Membership(universe,yes,unknown|chosen.unknown)
            elif op == 'outcome':
                from .outcomes import execute as evaluate_outcome
                chosen=memberships[node['inputs'][0]]
                yes,unknown,detail=evaluate_outcome(path,sorted(chosen.yes),params,prepared=outcome_preparations[node['id']])
                detail['input_membership']=membership_receipt(chosen)
                value=Membership(universe,yes,unknown|chosen.unknown)
            elif op == 'requirements':
                from .evidence_requirements import execute as evaluate_requirements
                chosen=memberships[node['inputs'][0]]
                yes,unknown,detail=evaluate_requirements(path,sorted(chosen.yes),params,prepared=interpretation_snapshots[node['id']])
                from .graph_criteria import member_index as criteria_index
                detail['review_members']=criteria_index(path,op,sorted(chosen.yes),params,interpretation_snapshots[node['id']],yes,unknown)
                detail['input_membership']=membership_receipt(chosen)
                value=Membership(universe,yes,unknown|chosen.unknown)
            elif op == 'pre_event_state':
                from .pre_event_state import execute as project_pre_event
                chosen=memberships[node['inputs'][0]]
                yes,unknown,detail=project_pre_event(path,sorted(chosen.yes),params,
                    prepared=interpretation_snapshots[node['id']],anchor_prepared=outcome_preparations[node['inputs'][0]])
                from .graph_decision import member_index
                detail['review_members'] = member_index(path, op, sorted(chosen.yes), params,
                    interpretation_snapshots[node['id']], outcome_preparations[node['inputs'][0]])
                detail['input_membership']=membership_receipt(chosen)
                value=Membership(universe,yes,unknown|chosen.unknown)
            elif op == 'source_state':
                from .source_state import execute as project_source_state
                chosen=memberships[node['inputs'][0]]
                yes,unknown,detail=project_source_state(path,sorted(chosen.yes),params,prepared=interpretation_snapshots[node['id']])
                from .graph_decision import member_index
                detail['review_members'] = member_index(path, op, sorted(chosen.yes), params, interpretation_snapshots[node['id']])
                detail['input_membership']=membership_receipt(chosen)
                value=Membership(universe,yes,unknown|chosen.unknown)
            elif op == 'interpretation':
                from .interpretation_state import execute as interpret_population
                chosen=memberships[node['inputs'][0]]
                selected,detail=interpret_population(path,sorted(chosen.yes),params,prepared=interpretation_snapshots[node['id']])
                detail['processing_counts']=detail.pop('counts')
                detail['input_membership']=membership_receipt(chosen)
                value=Membership(universe,selected,chosen.unknown)
            elif op == 'assertion_review':
                from .assertion_reconciliation import execute as reconcile_assertions
                chosen=memberships[node['inputs'][0]]
                detail=reconcile_assertions(path,sorted(chosen.yes),params)
                detail['input_membership']=membership_receipt(chosen)
            elif op == 'context_passes':
                from .context_passes import execute as build_passes
                chosen=memberships[node['inputs'][0]]
                detail=build_passes(path,sorted(chosen.yes),params)
                detail['input_membership']=membership_receipt(chosen)
            elif op == 'context':
                from .context_builder import execute as build_context
                chosen=memberships[node['inputs'][0]]
                detail=build_context(path,sorted(chosen.yes),params)
                detail['input_membership']=membership_receipt(chosen)
            elif op == 'medication_identity':
                from .medication_identity import execute as medication_execute
                chosen=memberships[node['inputs'][0]]
                detail=medication_execute(path,sorted(chosen.yes))
                detail['input_membership']=membership_receipt(chosen)
            if value is not None:
                memberships[node['id']] = value
                appointment_ancestors[node['id']]=set().union(*(appointment_ancestors.get(x,set()) for x in node['inputs']))|({node['id']} if op=='appointment_followthrough' else set())
                inherited=set().union(*(requirement_ancestors.get(x,set()) for x in node['inputs']))
                requirement_ancestors[node['id']]=inherited|({node['id']} if op=='requirements' else set())
                protocol_ancestors[node['id']]=set().union(*(protocol_ancestors.get(x,set()) for x in node['inputs']))|({node['id']} if op=='protocol_review' else set())
                document_ancestors[node['id']]=set().union(*(document_ancestors.get(x,set()) for x in node['inputs']))|({node['id']} if op=='program_document' else set())
                detail = {**membership_receipt(value), **detail}
            from . import graph_reports
            if op in graph_reports.OPERATIONS:
                prep={'measure_review':measure_preparations,'financial_accounts':financial_preparations,
                      'accountability_review':accountability_preparations,'outcome':outcome_preparations,
                      'observation_process':observation_preparations}[op][node['id']]
                graph_reports.attach(path,op,sorted(chosen.yes),params,prep,detail,membership_receipt(chosen))
            results.append({'node': node, 'result': detail})
            node_seconds.append({'id': node['id'], 'operation': op, 'seconds': round(time.perf_counter() - node_started, 4)})
        answer_started = time.perf_counter()
        result = {'schema': VERSION, 'synthetic': True, 'graph': plan, 'results': results,
                  'answer_coverage': answer_coverage(plan, results),
                  'snapshot': meta['snapshot'], 'source_digest': meta['source_digest'],
                  'evidence_model_identity': evidence_meta['model_identity'], 'cache_hit': False,
                  'result_id': key, 'compute': {'new_graph_nodes': len(results), 'aggregate_result_replayed': False},
                  'new_paid_api_calls': paid_calls, 'elapsed_seconds': time.perf_counter()-started,
                  'clinical_execution_authorized': False, 'limitations': LIMITS}
        if appointment_dependency and appointment_graph.dependency(path) != appointment_dependency:
            raise ValueError('Appointment sources changed during graph execution; result not cached')
        result['question']=question
        result['answer']=build_answer(plan, results, memberships, question)
        import os; from .answer_checks import annotate as quality_annotate; from .oracles import readonly_executor  # FMEA poka-yoke: warnings only
        quality_annotate(result['answer'], question, meta['snapshot']['as_of'],
                         execute=readonly_executor(path) if os.environ.get('AGERE_PARTITION_ORACLE', 'on') != 'off' and (path / 'semantic.sqlite3').exists() else None)
        result['answer']['standards']=standards(result['answer'])
        result['answer']['headline']=headline(result['answer'])
        result['answer']['benchmark']=benchmark_references(result['answer'], question, plan['nodes'][-1]['operation'] if plan.get('nodes') else None)
        if any(n['operation'] in ('source_state','pre_event_state','requirements','protocol_review','program_document','reference_review',*graph_reports.OPERATIONS) for n in plan['nodes']):
            from .graph_decision import source_fingerprint
            result['review_source_fingerprint'] = source_fingerprint(path, result)
            from .graph_criteria import dependencies as criteria_dependencies
            result['review_criteria_dependencies'] = criteria_dependencies(path,result)
            result['review_report_dependencies'] = graph_reports.dependencies(path,result)
            from .graph_references import dependencies as reference_dependencies
            result['review_reference_dependencies'] = reference_dependencies(path,result)
            if result['review_reference_dependencies'] != {k:v['id'] for k,v in reference_preparations.items()}:
                raise ValueError('Reference changed during graph execution; result not cached')
        if functional_population:functional_sources.inspect(path,functional_population['id'])
        for population in domain_populations.values():domain_sources.inspect(path,population['id'])
        # Wall-time receipt per phase/operator (latency budgets read it); not part of the cache key.
        result['compute']['timings'] = {'setup_seconds': round(setup_seconds, 4), 'nodes': node_seconds,
                                        'answer_seconds': round(time.perf_counter() - answer_started, 4)}
        if result_cache_enabled():
            with db:
                db.execute('INSERT OR REPLACE INTO result_cache VALUES (?,?)', (key, json.dumps(result, allow_nan=False, default=_json_default)))
        return result
    finally:
        db.close()
        if evidence_db is not None:
            evidence_db.close()
