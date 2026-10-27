"""Typed downstream intentions on the same original warehouse request.

These describe synthetic product work, never clinical eligibility or permission
to contact people. Metrics to monitor must be part of the executed answer.
"""
import re

VERSION = 'answer-workflow/1'
FIELDS = {
    'deliverable': {'template'},
    'simulation': {'program'},
    'monitor': {'mode', 'scope', 'outputs'},
}


def initialized(step):
    """A completed initialization has different states for work and monitors.

    Watching establishes a monitor's baseline, not future successful care or
    an improved clinical outcome. Later feedback can still retire reuse.
    """
    states={'deliverable':{'ready','complete'},'simulation':{'ready','complete','no_matches'},'monitor':{'watching'}}
    return isinstance(step,dict) and not step.get('unresolved') and step.get('status') in states.get(step.get('kind'),set())


def _refers_to_selected_patients(question, action_quote, criterion_quote, patient_selection):
    """Resolve an explicit recipient pronoun to a single earlier patient list.

    The caller has validated the patient table. This only connects literal
    source spans; the computed criterion and semantic review remain required.
    """
    from .request_plan import quote_text
    if not patient_selection or not re.search(
            r'\b(?:them|(?:these|those)\s+(?:patients|members|people|individuals)|(?:this|that)\s+(?:cohort|population))\b',
            quote_text(action_quote), re.I):
        return False
    fragments = patient_selection['quote']
    fragments = fragments if isinstance(fragments, list) else [fragments]
    if not fragments or any(not isinstance(s, str) or not s for s in fragments):
        return False
    start, end = None, 0
    for fragment in fragments:
        position = question.find(fragment, end)
        if position < 0:
            return False
        start = position if start is None else start
        end = position + len(fragment)
    action_fragment = action_quote[0] if isinstance(action_quote, list) else action_quote
    action_start = question.find(action_fragment)
    criterion_start = question.find(criterion_quote, start)
    if criterion_start < start or criterion_start + len(criterion_quote) > action_start or end > action_start:
        return False
    # Another explicit patient list makes the antecedent ambiguous. Do not
    # resolve a pronoun by choosing a cohort merely because it was supplied.
    intervening = question[end:action_start]
    return not re.search(r'\b(?:list|show|find|identify|select|return|which)\b[^;.!?]*\b(?:patients|members|people|individuals)\b',
                         intervening, re.I)


def validate(steps, question, *, outputs=(), output_quotes=(), output_bindings=None, patient_selection=None):
    from .request_plan import PlanError, quote_text, without_request_scaffolding
    if not isinstance(steps, list) or len(steps) > 8:
        raise PlanError('A request supports up to eight explicit downstream steps')
    for step in steps:
        if not isinstance(step, dict) or step.get('kind') not in FIELDS:
            raise PlanError('Workflow steps are deliverable, simulation or monitor')
        expected = {'kind', 'title', 'quote', 'unresolved'} | FIELDS[step['kind']]
        if step['kind'] == 'simulation' and 'selection' in step:
            expected.add('selection')
        if set(step) != expected:
            raise PlanError('Workflow step needs exact fields: ' + ', '.join(sorted(expected)))
        if not isinstance(step['title'], str) or not 1 <= len(step['title']) <= 160:
            raise PlanError('A short workflow title is required')
        if not isinstance(step['unresolved'], list) or any(not isinstance(x, str) or not 1 <= len(x) <= 500 for x in step['unresolved']):
            raise PlanError('Workflow missing requirements must be explicit short reasons')
        quote = quote_text(step['quote'])
        if not isinstance(quote, str) or not quote.strip() or quote.strip() == question.strip():
            raise PlanError('A workflow step must bind its own clause, not the entire question')
        if step['kind'] == 'deliverable' and step['template'] not in ('command', 'brief', 'explore'):
            raise PlanError('Choose command, brief or explore for the synthetic deliverable')
        if step['kind'] == 'simulation' and step['program'] not in ('custom', 'pharmd', 'rpm'):
            raise PlanError('Choose custom, pharmd or rpm; program suitability remains unverified')
        if step['kind'] == 'monitor' and step['mode'] not in ('answer', 'work'):
            raise PlanError('Monitoring tracks the executed answer or saved simulated work')
        selection = step.get('selection')
        selection_words = set()
        if selection is not None:
            if (not isinstance(selection, dict) or set(selection) != {'output', 'quote'}
                    or selection.get('output') not in outputs or not isinstance(selection.get('quote'), str)
                    or not selection['quote'].strip() or selection['quote'] not in question):
                raise PlanError('Work selection needs a computed boolean output and its literal scope clause')
            if selection['quote'] not in quote and not _refers_to_selected_patients(
                    question, step['quote'], selection['quote'], patient_selection):
                raise PlanError('Work selection must be in the action clause or refer to its explicit earlier patient list')
            bound = (output_bindings or {}).get(selection['output'], [])
            if not any(selection['quote'] in q for q in bound):
                raise PlanError('Work selection must bind to the analytical requirement computing that output')
            selection_words = set(re.findall(r'\w+', selection['quote'].lower()))
        if step['kind'] == 'simulation' and not step['unresolved']:
            # Artifact briefs are content, not executable clinical policy. Their
            # source-bound builder cannot change the cohort or perform actions.
            # Keep this execution vocabulary check for simulated work only.
            # Only generic product intent is consumed by this workflow language.
            # Content describing a different population, eligibility or bespoke
            # program cannot become permission to act on the analytical output.
            syntax = set(('and then please a an the this that these those them their our my its for to of '
                          'patients patient members member population cohort result results answer answers '
                          'create build make generate compose design prepare launch assign start send run '
                          'agent agents simulate simulated simulation synthetic mock mockup demo '
                          'outreach follow up followup program programme review task tasks queue '
                          'enroll enrol enrollment signup sign help '
                          'interactive command center centre dashboard view report brief briefing '
                          'team care clinical evidence source explorer custom pharmd rpm titration remote monitoring virtual '
                          'pharmacist pharmacy medication medications med meds decision work up with').split())
            if step['kind'] == 'deliverable':
                # Bounded presentation vocabulary only. New analytical facts,
                # filters, dates and calculations must still bind upstream.
                syntax |= set(('executive leadership board cmo cfo ceo audience '
                               'concise polished minimal clean accessible responsive printable '
                               'compact spacious layout column columns sidebar card cards tab tabs '
                               'light dark theme navy blue green typography font fonts '
                               'presentation style styled with using html deliverable scoreboard').split())
            extra = set(re.findall(r'\w+', without_request_scaffolding(quote).lower())) - syntax - selection_words
            if extra:
                step['unresolved'].append('Downstream wording needs an explicit scope or deliverable definition: '+', '.join(sorted(extra))+
                                          '. The analytical answer is preserved; this step will not execute.')
        if step['kind'] == 'deliverable' and not step['unresolved']:
            # Accept open presentation/service briefs, while retaining explicit
            # upstream bindings for new cohort restrictions and calculations.
            analytical = (re.search(r'\b(?:only|exclusively|restricted|limited)\s+(?:for|to)\b',quote,re.I)
                or re.search(r'\b(?:for|to|among)\s+(?:\w+\s+){0,2}(?:patients|members|people)\s+(?:who|with|without|not|lacking|having|admitted|discharged|aged)\b',quote,re.I)
                or re.search(r'\b(?:after|before|since|between|above|below|over|under)\s+\d',quote,re.I)
                or re.search(r'\b(?:show(?:ing)?|compute|calculate|compare|report)\b.*\b(?:average|mean|median|cost|rate|percent|percentage|ratio|sum|total)\b',quote,re.I))
            if analytical:
                step['unresolved'].append('Bind the additional population restriction or calculation in the analytical request before building this artifact.')
        if step['kind'] == 'monitor':
            if step['scope'] not in ('query', 'result'):
                raise PlanError('Monitor scope is query (original input scope) or result (frozen output patients)')
            refs = step['outputs']
            if step['mode'] == 'work':
                if step['scope'] != 'result' or refs != ['work_status']:
                    raise PlanError('Work monitoring follows this saved work only: scope=result, outputs=[work_status]')
                if not re.search(r'\b(work|tasks?|agents?|journeys?|progress|loop closure)\b', quote, re.I):
                    raise PlanError('Work monitoring needs an explicit work/progress/loop-closure intention')
                if not re.search(r'\b(monitors?|monitoring|track|watch|follow|measure|trend)\b', quote, re.I):
                    raise PlanError('Work monitoring needs an explicit monitoring verb')
                continue
            if not isinstance(refs, list) or any(not isinstance(x, str) or x not in outputs for x in refs):
                raise PlanError('Monitor outputs must reference actual select aliases or analysis_N nodes')
            if not step['unresolved']:
                if not refs:
                    raise PlanError('A defined monitor must reference at least one computed output')
                # A named metric cannot hide solely inside the workflow quote.
                # It must also be bound to a computed output, not just a cohort filter.
                syntax = set('and then monitor track watch follow measure trend this that the these those them their our my its answer answers result results cohort population patients change changes over time afterwards for of in on please continue continuously ongoing'.split())
                available = set(re.findall(r'\w+', ' '.join(output_quotes).lower()))
                named = set(re.findall(r'\w+', quote.lower())) - syntax - available
                if named:
                    step['unresolved'].append('Monitoring text has no computed output binding: '+', '.join(sorted(named))+
                                              '. Add the metric before starting this monitor.')
        verbs = {
            'deliverable': r'\b(create|build|make|generate|compose|design|dashboard|brief|command cent(?:er|re)|deliverable|report|explorer)\b',
            'simulation': r'\b(agent|simulate|simulation|launch|enroll|enrol|sign|assign|outreach|program|programme|follow.up|action|task)\b',
            'monitor': r'\b(monitors?|monitoring|track|watch|follow|measure|trend)\b',
        }
        if not re.search(verbs[step['kind']], quote, re.I):
            raise PlanError('Workflow kind must have supporting intent in its own original wording')
    return steps


def validate_selection(step):
    """An explicit UI action has its own origin, rather than a fabricated quote."""
    from .request_plan import PlanError
    origin = step.get('origin')
    if not isinstance(origin, dict) or set(origin) != {'kind', 'card_id'} or origin['kind'] != 'briefing_selection':
        raise PlanError('Explicit briefing-selection provenance required')
    if not isinstance(origin['card_id'], str) or not re.fullmatch('[0-9a-f]{20}', origin['card_id']):
        raise PlanError('Exact briefing card identity required')
    if step.get('kind') not in FIELDS or step.get('quote') is not None:
        raise PlanError('UI selection is separate from question-quoted workflow')
    if step['kind'] == 'deliverable' and step.get('template') != 'command':
        raise PlanError('Unknown briefing deliverable')
    if step['kind'] == 'simulation' and step.get('program') != 'custom':
        raise PlanError('Unknown briefing simulation')
    if step['kind'] == 'monitor' and (step.get('mode') != 'answer' or step.get('scope') != 'query'):
        raise PlanError('Briefing monitors retain the original query scope')
