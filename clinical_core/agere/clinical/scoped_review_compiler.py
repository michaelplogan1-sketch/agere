"""Explicit, abstaining documentary commands; no learned or clinical authority."""
import hashlib
import re
import sqlite3
from pathlib import Path

from ...contracts import digest
from . import analysis_graph, cohorts

VERSION = 'scoped-review-compiler/1'
POLICY = 'scoped_local_candidate'
EXAMPLES = [
    'Review respiratory sources for patient P-1002',
    'Review behavioral sources for patients P-1002, P-1003',
    'Prepare respiratory source-review work across all observed patients excluding patient P-1003',
]
_COMMAND = re.compile(
    r'(?:(?:Review (?P<review>respiratory|behavioral) sources)|'
    r'(?:Prepare (?P<work>respiratory|behavioral) source-review work)) '
    r'(?P<scope>across all observed patients|for patients? [A-Za-z0-9_, -]+?)'
    r'(?: excluding (?P<exclude>patients? [A-Za-z0-9_, -]+))?', re.IGNORECASE)
_IDS = re.compile(r'(?P<plural>patients?) (?P<ids>[A-Za-z0-9][A-Za-z0-9_-]{0,149}'
                  r'(?:, ?[A-Za-z0-9][A-Za-z0-9_-]{0,149})*)', re.IGNORECASE)


def identity():
    return digest([VERSION, hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])


def _ids(text):
    match = _IDS.fullmatch(text)
    if not match:
        raise ValueError('Use exact patient IDs separated by commas.')
    ids = match['ids'].split(',')
    ids = [x.strip() for x in ids]
    if len(set(ids)) != len(ids) or (match['plural'].casefold() == 'patient' and len(ids) != 1):
        raise ValueError('Patient IDs must be distinct; use patients for a list.')
    return ids


def compile_question(question, *, as_of, known_patient_ids):
    """Preserve the entire command or abstain. IDs are case-sensitive roster IDs."""
    if not isinstance(question, str) or not 1 <= len(question) <= 1500:
        raise ValueError('A bounded source-review command is required.')
    command = ' '.join(question.split())
    if command.endswith(('.', '?')):
        command = command[:-1]
    match = _COMMAND.fullmatch(command)
    if not match:
        raise ValueError('Scoped local candidate abstained: use an exact source-review command. '
                         'Dates, clinical recommendations and other qualifiers require the ordinary planner.')
    domain = (match['review'] or match['work']).casefold()
    included = None if match['scope'].casefold() == 'across all observed patients' else _ids(match['scope'][4:])
    excluded = _ids(match['exclude']) if match['exclude'] else []
    if included is not None and set(included) & set(excluded):
        raise ValueError('Scoped local candidate abstained: a patient is both included and excluded.')
    requested = set((included or []) + excluded)
    if requested - set(known_patient_ids):
        raise ValueError('Scoped local candidate abstained: an exact patient ID is not in the received roster.')
    atom = lambda pid: {'kind':'patient', 'field':'patient_id', 'op':'eq', 'value':pid}
    cohort = cohorts.empty()
    if included is not None:
        cohort['any'] = [atom(pid) for pid in included]
    cohort['exclude'] = [atom(pid) for pid in excluded]
    cohorts.validate(cohort, as_of)
    nodes = [
        {'id':'population', 'operation':'cohort', 'inputs':[], 'parameters':{'specification':cohort},
         'purpose':'Exact requested patient scope within the received roster'},
        {'id':'domain', 'operation':'domain_review', 'inputs':['population'],
         'parameters':{'domain':domain, 'filter':'source_review_candidates'},
         'purpose':'Documentary review candidates; absent sources leave need unknown'},
    ]
    if match['work']:
        nodes.append({'id':'work', 'operation':'worklist', 'inputs':['domain'],
                      'parameters':{'program':domain+'_source_review'},
                      'purpose':'Prepare a source-bound internal review proposal; no external execution'})
    graph = {'interpretation':question.strip()+'. Current received-snapshot documentary review only.',
             'coverage':'partial', 'unmet_requirements':[
                 'Current clinical status, service indication, patient goals, consent and exact benefits remain unverified.',
                 'No historical date window, clinical recommendation or automatic care execution is supported by this command.'],
             'nodes':nodes}
    graph = analysis_graph.validate(graph, as_of)
    seed = {'question':question, 'program':domain+'_source_review', 'cohort':cohort, 'as_of':as_of}
    receipt = {'schema':VERSION, 'route':POLICY, 'compiler_identity':identity(),
               'input_digest':digest(question), 'graph_digest':digest(graph),
               'decision_seed_digest':digest(seed), 'included_patient_ids':included,
               'excluded_patient_ids':excluded, 'as_of':as_of, 'new_paid_api_calls':0,
               'default_activation':False, 'learned_model':False,
               'qualification':'Development command-contract checks only; no clinical or natural-language accuracy qualification.'}
    return graph, receipt, seed


def prepare_decision(retriever, request):
    """Recompile and bind the exact candidate receipt to existing decision storage."""
    from . import question_decision as q
    if set(request) != {'question','buyer_id','compiler_identity','as_of'}:
        raise ValueError('Exact scoped command, buyer, compiler version and snapshot required')
    as_of = retriever.meta['snapshot']['as_of']
    if request['compiler_identity'] != identity() or request['as_of'] != as_of:
        raise ValueError('Scoped command compiler or snapshot changed; compile the command again')
    with sqlite3.connect(retriever.path/'stack.sqlite3') as db:
        roster = {row[0] for row in db.execute('SELECT id FROM patients')}
    graph, receipt, seed = compile_question(request['question'],as_of=as_of,known_patient_ids=roster)
    decision = q.prepare_population(retriever,{**seed,'buyer_id':request['buyer_id']})
    q.save_followup(retriever.path,{'schema':'scoped-command-plan/1','decision_id':decision['id'],
        'question':seed['question'],'graph':graph,'compiler_receipt':receipt,
        'decision_request_digest':digest(decision['request']), 'snapshot':retriever.meta['snapshot'],
        'scope':'Exact command-contract provenance. No learned-model or clinical qualification.',
        'new_paid_api_calls':0})
    return decision
