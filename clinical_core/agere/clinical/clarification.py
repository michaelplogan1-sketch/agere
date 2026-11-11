"""Durable user-intent questions around the existing investigator.

Only server-owned execution checkpoints are resumable. A reply selects intent;
it is never a warehouse fact, a clinical label or permission to execute care.
"""
import copy
import json
import re
import sqlite3
import time
import math
from contextlib import contextmanager,closing
from contextvars import ContextVar
from ...contracts import digest

VERSION='intent-clarification/1'
VALUE_VERSION='clarification-value/1'
# Candidate engineering policy in normalized answer-change units, NOT fitted
# user burden, money, clinical benefit or observed response probabilities.
INTERRUPTION_COST=0.05
EARLY_KINDS={'goal','population','period','definition'}
MODE=ContextVar('agere_interaction_mode',default='interactive')
SPEC={'type':'object','properties':{
    'slot':{'type':'string','pattern':'^[a-z][a-z0-9_]*$'},
    'kind':{'type':'string','enum':['goal','population','period','definition','input','value']},
    'branch':{'type':'string','description':'For input/value only: an existing native result_view consumed by the dependent branch.'},
    'value_model':{'type':'object','properties':{
        'prior':{'type':'object','additionalProperties':{'type':'number'}},
        'outcomes':{'type':'object','additionalProperties':{'type':'object','properties':{
            'population_size':{'type':'integer','minimum':0},'measure_value':{'type':'number'},
            'ranking':{'type':'array','items':{'type':'string'}}},'additionalProperties':False}},
        'basis':{'type':'string','minLength':1}},'required':['prior','outcomes','basis'],'additionalProperties':False},
    'question':{'type':'string','minLength':1},'reason':{'type':'string','minLength':1},
    'options':{'type':'array','minItems':2,'items':{'type':'object','properties':{
        'id':{'type':'string','minLength':1},'label':{'type':'string','minLength':1},
        'meaning':{'type':'string','minLength':1}},'required':['id','label','meaning'],'additionalProperties':False}}},
    'required':['slot','question','reason','options'],'additionalProperties':False}
TOOL={'name':'request_clarification','description':
    'Ask a material question about USER INTENT for this analysis, such as the population, period, analytical definition or output goal. '
    'Assess goal/population/period/definition BEFORE analytical dispatch. Defer input/value choices until their dependent branch has a native result_view. '
    'When supported projections are available, supply explicit assumed option probabilities, alternative answers (population_size, measure_value, ranking) and their basis in value_model. '
    'These are counterfactual assumptions, not source facts. The controller uses their net information value to avoid unnecessary questions. '
    'If projections are unavailable, a material intent question can still use the normal clarification path; never fabricate numbers to obtain an interruption. '
    'Use known defaults. Never ask the user to fix software, judge model confidence, approve routine analysis, '
    'or supply clinical facts that should be verified from records. Finish independent work first. Call this tool alone in a reply. '
    'Completed native evidence is saved; only the unresolved analysis waits. Options describe alternative intentions, never invented source facts. '
    'In autonomous mode this records an unresolved requirement and you must continue independent work, preserving unknowns.',
    'input_schema':SPEC}


@contextmanager
def interaction(mode):
    if mode not in ('interactive','autonomous'):raise ValueError('Choose interactive or autonomous interaction')
    token=MODE.set(mode)
    try:yield
    finally:MODE.reset(token)


def validate(spec):
    if not isinstance(spec,dict)or not {'slot','question','reason','options'}<=set(spec)or set(spec)-{'slot','question','reason','options','kind','branch','value_model'}:
        raise ValueError('Clarification needs a named intent slot, question, reason and options')
    if (not isinstance(spec['slot'],str)or not re.fullmatch(r'[a-z][a-z0-9_]*',spec['slot'])or
        any(not isinstance(spec[k],str)or not spec[k].strip()for k in ('question','reason'))or
        not isinstance(spec['options'],list)or len(spec['options'])<2):raise ValueError('Invalid intent clarification')
    ids=[]
    for option in spec['options']:
        if (not isinstance(option,dict)or set(option)!={'id','label','meaning'}or
            any(not isinstance(v,str)or not v.strip()for v in option.values())):raise ValueError('Intent options need distinct IDs, labels and meanings')
        ids.append(option['id'])
    if len(set(ids))!=len(ids):raise ValueError('Duplicate clarification option')
    return copy.deepcopy(spec)


def score(spec):
    """Expected answer regret removed by a perfect intent reply, less burden.

    Alternative outcomes and prior are explicit hypotheses. No native reads or
    providers run here. The existing EVSI calculator validates the distributions.
    """
    from ...information_value import expected_information_value
    model=spec.get('value_model')
    if model is None:return {'gross_value':None,'net_value':None,'reason':'impact_unavailable'}
    if not isinstance(model,dict)or set(model)!={'prior','outcomes','basis'}:
        raise ValueError('Value model needs prior, alternative outcomes and assumption basis')
    states=[o['id']for o in spec['options']]
    outcomes=model['outcomes']
    if not isinstance(outcomes,dict)or set(outcomes)!=set(states):raise ValueError('Outcome options differ')
    dimensions=None
    for outcome in outcomes.values():
        if not isinstance(outcome,dict)or not outcome or set(outcome)-{'population_size','measure_value','ranking'}:
            raise ValueError('Supply population size, measure value or ranking projections')
        if dimensions is None:dimensions=set(outcome)
        if set(outcome)!=dimensions:raise ValueError('Alternative outcomes need the same dimensions')
        for key,value in outcome.items():
            if key=='ranking':
                if not isinstance(value,list)or any(not isinstance(x,str)or not x for x in value)or len(set(value))!=len(value):
                    raise ValueError('Ranking needs distinct named entities')
            elif type(value)not in (int,float)or not math.isfinite(value):raise ValueError('Finite outcome projection required')
            elif key=='population_size'and (type(value)is not int or value<0):raise ValueError('Population size needs a nonnegative integer')
    def distance(a,b):
        changes=[]
        for key in sorted(dimensions):
            if key=='ranking':
                ids=set(a[key])|set(b[key]);n=max(len(a[key]),len(b[key]),1)
                pos=lambda r:{v:i for i,v in enumerate(r)}
                x,y=pos(a[key]),pos(b[key])
                changes.append(sum(abs(x.get(v,n)-y.get(v,n))for v in ids)/max(1,len(ids)*n))
            else:changes.append(abs(a[key]-b[key])/max(1,abs(a[key]),abs(b[key])))
        return sum(changes)/len(changes)
    utilities={action:{state:-distance(outcomes[action],outcomes[state])for state in states}for action in states}
    calc=expected_information_value(prior=model['prior'],utilities=utilities,
        channels={'reply':{'available':True,'cost':INTERRUPTION_COST,
            'likelihoods':{s:{o:float(s==o)for o in states}for s in states},
            'basis':'Assumed perfect resolution of the listed user-intent options; an optimistic one-step bound.'}},
        utility_scale='mean normalized answer change; decision cost=1',assumption_source=model['basis'])
    result=calc['channels'][0]
    return {**result,'recommended_assumption':calc['current_action_under_assumptions'],
        'reason':'positive_net_value'if result['net_value']>0 else'nonpositive_net_value',
        'calculation':calc,'outcomes_are':'assumed_counterfactuals_not_observed_facts'}


def record_decision(path,decision):
    """Durable, append-only calibration input even without an API context."""
    from ..request_lineage import execution_observation,ACTIVE
    active=ACTIVE.get()
    decision['request_execution_id']=active.id if active is not None else None
    if path is not None:
        with closing(database(path))as db,db:
            db.execute('INSERT INTO intent_clarification_decisions(payload) VALUES (?)',(json.dumps(decision,allow_nan=False),))
    execution_observation('clarification_decision',**decision)


def request(spec,*,tools=None):
    path=tools.stack.path if tools is not None else None
    decision={'schema':VALUE_VERSION,'at':time.time(),'candidate_digest':None,
        'decision_type':'clarify_or_not','threshold':0.0,'interruption_cost':INTERRUPTION_COST,
        'calibrated':False,'ask':False,'gross_value':None,'net_value':None}
    try:
        decision['candidate_digest']=digest(spec)
        spec=validate(spec);decision.update(slot=spec['slot'],intent_kind=spec.get('kind'),**score(spec))
        calls=getattr(tools,'native_calls',[])
        dispatched=any(c['name']not in ('request_clarification','list_analyses','describe_analysis','list_metrics','describe_metric')for c in calls)
        if spec['slot']in getattr(tools,'resolved_intent_slots',set()):
            decision['reason']='already_resolved'
        elif spec.get('kind')in EARLY_KINDS and dispatched:decision['reason']='early_intent_after_dispatch'
        elif spec.get('kind')in ('input','value')and spec.get('branch')not in getattr(tools,'executed_results',{}):
            decision['reason']='dependent_branch_not_ready'
        elif spec.get('kind') is not None and spec.get('kind')not in EARLY_KINDS|{'input','value'}:decision['reason']='intent_kind_unavailable'
        else:
            material_unscored = decision['reason']=='impact_unavailable'
            decision['ask']=(material_unscored or decision['net_value']is not None and decision['net_value']>0) and MODE.get()=='interactive'
            if material_unscored:decision['reason']='unscored_material_intent'
            if MODE.get()=='autonomous'and (material_unscored or decision['net_value']is not None and decision['net_value']>0):decision['reason']='autonomous_mode'
        decision['timing']={'dispatched':dispatched,'branch':spec.get('branch')}
        if tools is not None:decision['context_digest']=digest(tools.continuation_identity())
    except (ValueError,TypeError,OverflowError)as exc:
        decision.update(reason='invalid_candidate',error=str(exc));record_decision(path,decision);raise ValueError(str(exc))from exc
    record_decision(path,decision)
    if decision['reason']=='already_resolved':raise ValueError('The user already clarified this intent slot. Use the retained response; do not ask the same question again.')
    if not decision['ask']:
        intent_key='intent_equivalent'if decision['gross_value']==0 else'intent_unresolved'
        return {intent_key:spec,'clarification_decision':decision,'interaction_mode':MODE.get(),
            'authority':'user_intent_only','source_facts_established':False,
            'instruction':'Do not interrupt. Continue independent work; disclose alternative assumptions or leave the dependent answer unresolved. '
                          'A late goal/population/period/definition requires replanning before further dependent dispatch, not guessing.'}
    return {'clarification_required':spec,'interaction_mode':MODE.get(),'clarification_decision':decision,
            'authority':'user_intent_only','source_facts_established':False,
            'instruction':'Save completed evidence and wait for this intent choice. No clinical or source fact is established.'}


def checkpoint(tools,question,payload,run):
    paused=run['clarification_pause']
    if paused['request']!=validate(paused['request']):raise ValueError('Invalid clarification request')
    return {'schema':VERSION,'question':question,'as_of':tools.as_of,'cohort':tools.cohort,
        'guard':tools.continuation_identity(),'input_guard':input_guard(tools.stack.path,tools.cohort),
        'payload':copy.deepcopy(payload),'model':run['model'],
        'pause':copy.deepcopy(paused),'calls':copy.deepcopy(run['tool_calls']),
        'read':copy.deepcopy(tools.read),'judged':copy.deepcopy(tools.judged),
        'document_reviews':copy.deepcopy(tools.document_reviews),'document_field_contracts':copy.deepcopy(tools.document_field_contracts),
        'governed':copy.deepcopy(tools.governed)}


def input_guard(path,cohort):
    from pathlib import Path
    if not (Path(path)/'stack.sqlite3').is_file():return {'kind':'semantic_only'}
    from . import patient_input_index as inputs
    snapshot=inputs.snapshot(path)['patients']
    ids=sorted(snapshot)if cohort is None else sorted(set(cohort))
    if not set(ids)<=set(snapshot):raise ValueError('Saved population is absent from received source inputs')
    return {'kind':inputs.VERSION,'digest':digest([inputs.identity(),{pid:snapshot[pid]for pid in ids}])}


def restore(tools,saved,reply):
    from .question_input import validate_question
    validate_question(saved['question'])
    if (saved.get('schema')!=VERSION or saved['question']!=tools.question or saved['guard']!=tools.continuation_identity()
            or saved['input_guard']!=input_guard(tools.stack.path,tools.cohort)):
        raise ValueError('Saved analysis sources, definitions, code or population changed; start a new analysis. No provider call was made.')
    # Rebuild immutable request-local SQL tables from the saved native rows.
    # No analytical engine, source reader or provider is called again.
    for call in saved['calls']:
        original=call['result'];retained=tools.retain_result(copy.deepcopy(original))
        if retained!=original:raise ValueError('Retained native result differs after restoration')
    tools.native_calls=copy.deepcopy(saved['calls'])
    for key in ('read','judged','document_reviews','document_field_contracts','governed'):
        setattr(tools,key,copy.deepcopy(saved[key]))
    payload=copy.deepcopy(saved['payload'])
    answers=payload.setdefault('clarification_answers',[])
    answers.append({'slot':saved['pause']['request']['slot'],'question':saved['pause']['request']['question'],
                    'response':reply,'authority':'user_intent_only','source_facts_established':False})
    tools.resolved_intent_slots={answer['slot']for answer in answers}
    messages=copy.deepcopy(saved['pause']['messages'])
    messages.append({'role':'user','content':[{'type':'tool_result','tool_use_id':saved['pause']['tool_id'],
        'content':json.dumps(answers[-1]),'is_error':False}]})
    return payload,messages


def database(path):
    from .conversation import _store
    db=sqlite3.connect(_store(path),timeout=30)
    db.executescript('''CREATE TABLE IF NOT EXISTS intent_clarifications(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS intent_clarification_runs(id TEXT PRIMARY KEY,response TEXT NOT NULL,status TEXT NOT NULL,result TEXT,error TEXT);
      CREATE TRIGGER IF NOT EXISTS immutable_intent_request BEFORE UPDATE ON intent_clarifications BEGIN SELECT RAISE(ABORT,'Immutable intent request');END;
      CREATE TRIGGER IF NOT EXISTS preserve_intent_request BEFORE DELETE ON intent_clarifications BEGIN SELECT RAISE(ABORT,'Immutable intent request');END;
      CREATE TRIGGER IF NOT EXISTS immutable_intent_response BEFORE UPDATE OF response ON intent_clarification_runs BEGIN SELECT RAISE(ABORT,'Immutable intent response');END;''')
    db.executescript('''CREATE TABLE IF NOT EXISTS intent_clarification_decisions(seq INTEGER PRIMARY KEY,payload TEXT NOT NULL);
      CREATE TRIGGER IF NOT EXISTS immutable_intent_value BEFORE UPDATE ON intent_clarification_decisions BEGIN SELECT RAISE(ABORT,'Immutable intent value decision');END;
      CREATE TRIGGER IF NOT EXISTS preserve_intent_value BEFORE DELETE ON intent_clarification_decisions BEGIN SELECT RAISE(ABORT,'Immutable intent value decision');END;''')
    db.execute("CREATE TRIGGER IF NOT EXISTS preserve_intent_response BEFORE DELETE ON intent_clarification_runs BEGIN SELECT RAISE(ABORT,'Immutable intent response');END")
    return db


def pending(result):
    return isinstance(result,dict)and isinstance(result.get('clarification_checkpoint'),dict)


def registered_input_pause(stack, question):
    """Use the same durable clarification store for a closed input command."""
    from .engine_suite import input_request
    from ..deliverables.answer_context import source_identity
    from .shared_planner import policy_identity
    found = input_request(question)
    if found is None:
        return None
    spec = validate(found['request'])
    source = source_identity(stack.path)
    checkpoint = {'schema': VERSION, 'kind': 'registered_input', 'operation': found['operation'],
                  'question': question, 'source': source, 'policy': policy_identity(), 'cohort': None,
                  'as_of': source['as_of'], 'input_guard': input_guard(stack.path, None),
                  'bindings': found['bindings'], 'pause': {'request': spec}, 'calls': []}
    result = {'schema': VERSION, 'question': question, 'synthetic': True, 'clinical_execution_authorized': False,
              'new_paid_api_calls': 0, 'planning_economics': {'known_api_estimate_usd': 0, 'new_paid_api_calls': 0},
              'answer': {'status': 'clarify' if MODE.get() == 'interactive' else 'partial', 'answer_type': 'unsupported',
                         'headline': spec['question'], 'missing': [spec['reason']], 'patient_ids': [], 'actionable_cohort': False,
                         'limitations': ['No contract, population or settlement term has been chosen or computed.']},
              'results': [], 'workflow': [], 'registered_input_request': spec}
    if MODE.get() == 'interactive':
        result['clarification_checkpoint'] = checkpoint
    return result


def resume_registered_input(stack, saved, reply):
    from . import engine_suite
    from .shared_planner import policy_identity
    from ..deliverables.answer_context import source_identity
    if (saved.get('schema') != VERSION or saved['source'] != source_identity(stack.path)
            or saved['policy'] != policy_identity() or saved['input_guard'] != input_guard(stack.path, None)):
        raise ValueError('Saved input selection sources or definitions changed; start a new analysis. No provider was called.')
    selected = (reply.get('option') or {}).get('id') or str(reply.get('text') or '').strip().lower()
    parameters = saved['bindings'].get(selected)
    if parameters is None:
        return {'schema': VERSION, 'question': saved['question'], 'synthetic': True, 'clinical_execution_authorized': False,
                'answer': {'status': 'partial', 'answer_type': 'unsupported', 'patient_ids': [], 'actionable_cohort': False,
                           'headline': 'Explicit contract inputs are still required.',
                           'missing': ['Supply a registered, versioned contract with explicit payer, period, attribution and terms. The demo was not selected.']},
                'new_paid_api_calls': 0, 'results': [], 'workflow': []}
    module = engine_suite.module(saved['operation'])
    bound = module.validate(parameters)  # a selected old version cannot silently become a new one
    # The module owns its domain wording; the shared continuation only runs an
    # exact command that resolves to the previously offered typed binding.
    command = module.command_for_input(bound) if hasattr(module, 'command_for_input') else None
    if not command or module.registered_input_command(command, saved['as_of'])['nodes'][0]['parameters'] != bound:
        raise ValueError('Selected input has no matching closed command')
    result = stack.compose(command)
    result.setdefault('planner_receipt', {})['clarification_binding'] = {
        'original_question': saved['question'], 'resolved_question': command, 'operation': saved['operation'],
        'parameters': bound, 'authority': 'user_intent_only', 'source_facts_established': False}
    return result


def public(row,status='pending'):
    return {'id':row['id'],'conversation_id':row['conversation_id'],**row['checkpoint']['pause']['request'],
        'status':status,'retained_native_calls':len(row['checkpoint']['calls']),
        'original_question':row['question'],'authority':'user_intent_only','source_facts_established':False}


def publish(path,conversation_id,question,result,*,request_options=None):
    row={'schema':VERSION,'conversation_id':conversation_id,'question':question,
         'checkpoint':copy.deepcopy(result['clarification_checkpoint']),
         'history':copy.deepcopy(result.get('clarification_history')or[]),
         'request_options':copy.deepcopy(request_options or{}),
         'origin':copy.deepcopy(result.get('cohort_transform')),
         'phase_accounting':{k:copy.deepcopy(result.get(k))for k in ('new_paid_api_calls','planning_economics','estimated_cost_usd','known_cost_usd')},
         'created':time.time()}
    row['id']='CL-'+digest(row)
    with closing(database(path))as db,db:db.execute('INSERT INTO intent_clarifications VALUES (?,?)',(row['id'],json.dumps(row,allow_nan=False)))
    out={k:v for k,v in result.items()if k!='clarification_checkpoint'}
    out['clarification']=public(row)
    return out


def claim(path,conversation_id,ident,response):
    if not isinstance(ident,str)or not re.fullmatch('CL-[0-9a-f]{64}',ident):raise ValueError('Exact pending clarification required')
    if not isinstance(response,dict)or set(response)not in ({'option_id'},{'text'}):raise ValueError('Select an option or supply an intent response')
    db=database(path)
    try:
        db.execute('BEGIN IMMEDIATE')
        found=db.execute('SELECT payload FROM intent_clarifications WHERE id=?',(ident,)).fetchone()
        if not found:raise ValueError('Unknown clarification')
        row=json.loads(found[0])
        if row['id']!='CL-'+digest({k:v for k,v in row.items()if k!='id'})or row['conversation_id']!=conversation_id:
            raise ValueError('Clarification belongs to another conversation or changed')
        if 'option_id'in response:
            options=row['checkpoint']['pause']['request']['options']
            selected=next((o for o in options if o['id']==response['option_id']),None)
            if selected is None:raise ValueError('Unknown clarification option')
            reply={'option':selected}
        else:
            from .question_input import validate_question
            reply={'text':validate_question(response['text'])}
        prior=db.execute('SELECT response,status,result FROM intent_clarification_runs WHERE id=?',(ident,)).fetchone()
        if prior:
            if json.loads(prior[0])!=reply:raise ValueError('This clarification already has a different response; start a new analysis to change intent')
            if prior[1]=='complete':return row,reply,json.loads(prior[2])
            raise ValueError('This clarification is already running or failed; the prior attempt is preserved and was not resent')
        db.execute('INSERT INTO intent_clarification_runs VALUES (?,?,?,NULL,NULL)',(ident,json.dumps(reply),'running'));db.commit()
        return row,reply,None
    finally:db.close()


def finish(path,ident,result=None,error=None):
    with closing(database(path))as db,db:
        db.execute('UPDATE intent_clarification_runs SET status=?,result=?,error=? WHERE id=? AND status=?',
            ('failed'if error else'complete',json.dumps(result,allow_nan=False)if result is not None else None,
             str(error)if error else None,ident,'running'))


def refresh_public(path,conversation_id,value):
    from .conversation import _store
    with closing(sqlite3.connect(_store(path).resolve().as_uri()+'?mode=ro',uri=True))as db:
        row=db.execute('SELECT payload FROM intent_clarifications WHERE id=?',(value['id'],)).fetchone()
        if not row:raise ValueError('Saved clarification missing')
        saved=json.loads(row[0])
        if saved['conversation_id']!=conversation_id:raise ValueError('Clarification conversation mismatch')
        result=db.execute('SELECT status FROM intent_clarification_runs WHERE id=?',(value['id'],)).fetchone()
        return public(saved,result[0]if result else'pending')


def resume_investigator(stack,saved,reply,*,origin=None):
    """Resume through the same cascade's final checks, without replanning tools."""
    if saved.get('kind') == 'registered_input':
        return resume_registered_input(stack, saved, reply)
    from . import proposers as P,request_plan as R,shared_planner as S,decision_context as D,together
    from ..gateway import BudgetLedger
    from .. import model_policy
    parent=None
    if origin and origin.get('parent_context_id'):
        from ..deliverables.answer_context import load
        parent=load(stack.path,origin['parent_context_id'],current=True,follow_up=True)
    with D.scope(saved['question'],saved['as_of'])as state:
        ledger=BudgetLedger(together._ledger_path(stack))
        cascade=P.Cascade(stack,R.Catalog.load(stack.path),saved['question'],population=saved['cohort'],ledger=ledger)
        try:
            with model_policy.question_path(),model_policy.first_rung():
                found=cascade.tier_backstop_agent(continuation=saved,clarification_reply=reply)
            if found is None:
                error=RuntimeError('Clarified analysis did not produce a resumable or verified result')
                error.partial_result=getattr(cascade,'last_backstop_run',None)
                raise error
            contract,interpretation=found
            interpretation=cascade.finish(interpretation,'backstop_agent')
            receipt={'route':'backstop_agent','request_contract':contract,'interpretation':interpretation,**cascade.usage()}
            result=P.execute(stack,saved['question'],contract['graph'],receipt,started=time.perf_counter(),population=saved['cohort'],parent=parent)
            return D.finalize(stack.path,result,state)
        finally:cascade.close();ledger.close()
