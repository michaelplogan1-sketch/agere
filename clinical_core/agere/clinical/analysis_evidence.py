"""Mechanical evidence completeness for the compiled staged analysis.

Reachability is observed from SQLite's authorizer, not guessed from SQL text or
accepted from model prose. This proves an input participates in the compiled
query, not that it entails a clinical conclusion or that all relevant sources
were discovered. Semantic review and source-coverage contracts still apply.
"""

VERSION = 'analysis-evidence/1'


def assess(requirements, unresolved, step_names, final, trace, sources):
    active = set(trace.get('steps', ())) & set(step_names)
    active.add(final)  # single-step analyses do not need a CTE wrapper
    rows, gaps = [], []
    for index, requirement in enumerate(requirements):
        missing = sorted(set(requirement['applied_in']) - active)
        reason = ('Requirement maps to steps not used by the final result: ' + ', '.join(missing)) if missing else None
        rows.append({'id': f'requirement_{index + 1}', 'quote': requirement['quote'],
                     'applied_in': requirement['applied_in'], 'outputs': requirement.get('outputs', []),
                     'status': 'not_executed' if missing else 'compiled', 'reason': reason})
        if reason:
            gaps.append({'quote': requirement['quote'], 'reason': reason})
    return {'schema': VERSION, 'status': 'incomplete' if gaps or unresolved else 'compiled',
            'requirements': rows, 'active_steps': sorted(active),
            'unused_steps': sorted(set(step_names) - active), 'sources': sources,
            'gaps': [*unresolved, *gaps],
            'scope': 'Compiled requirement participation; semantic sufficiency and clinical truth are not established.'}
