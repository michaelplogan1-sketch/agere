"""Controlled natural-language compiler for observed evidence, not clinical intent.

Every clause must fully match a declared grammar. No chart text, retrieval hit or
language-model guess can authorize execution. Unmatched qualifiers are retained
in an unsupported response; they are never discarded to make a query run.
"""
from copy import deepcopy
import re,hashlib
from pathlib import Path
from .evidence_queries import validate_plan,execute

VERSION='evidence-language/1'
TOPICS={
 'heart failure':'diagnosis heart failure','hf':'diagnosis heart failure',
 'ckd':'diagnosis ckd','chronic kidney disease':'diagnosis ckd','kidney disease':'diagnosis ckd',
 'dyspnea':'symptom dyspnea','shortness of breath':'symptom dyspnea','edema':'symptom edema','leg swelling':'symptom edema',
 'ef':'ef','ejection fraction':'ef','financial hardship':'financial hardship','difficulty paying medical bills':'financial hardship',
 'outside records':'outside care','outside care':'outside care','nephrology':'care nephrology',
 'beta blocker':'medication bb','beta blockers':'medication bb','metoprolol':'medication bb',
 'goals of care':'goals of care','patient refusal':'preference declines',
}
# Drug-name-specific queries must not silently become whole-class queries.
TOPICS.pop('metoprolol')
DIAGNOSES={'heart failure':'diagnosis:I50','hf':'diagnosis:I50','ckd':'diagnosis:N18','chronic kidney disease':'diagnosis:N18','kidney disease':'diagnosis:N18'}
MEDICATIONS={'beta blocker':'bb','beta blockers':'bb','arni':'arni','mra':'mra','sglt2':'sglt2'}
TOPIC_PATTERN='(?:'+'|'.join(re.escape(k) for k in sorted(TOPICS,key=len,reverse=True))+')'
DIAG_PATTERN='(?:'+'|'.join(re.escape(k) for k in sorted(DIAGNOSES,key=len,reverse=True))+')'
MED_PATTERN='(?:'+'|'.join(re.escape(k) for k in sorted(MEDICATIONS,key=len,reverse=True))+')'
ASSERTIONS={'affirmed':'affirmed','documented':'affirmed','denied':'negated','negated':'negated','uncertain':'uncertain','retracted':'retracted'}
PREFIX=re.compile(r'^(?:(?:please )?(?:find|show|list|count)(?: me)? (?:all )?(?:the )?patients (?:with|who have|whose records show)|which patients (?:have|show)|who has|how many patients have)\s+')
EXAMPLES=[
 'Find patients with documented heart failure in notes but no structured heart failure diagnosis.',
 'Which patients have nephrology records requested in notes and no completed nephrology referral record?',
 'Find patients with historical EF in notes but no current EF annotations.',
 'Find patients with denied dyspnea in notes.',
 'Show patients with a structured beta blocker order but no structured beta blocker pharmacy claim record.',
 'Find patients with documented financial hardship in notes but no positive structured financial hardship screen.',
 'Find patients with historical EF in notes using source event dates from 2025-01-01 through 2025-12-31.',
]


def normalize(question):
    if not isinstance(question,str) or not question.strip() or len(question)>2000:raise ValueError('A question of 1–2000 characters is required')
    # Only harmless case/whitespace/end punctuation normalization. Do not strip
    # arbitrary punctuation, qualifiers, quoted text, numbers or instruction spans.
    return re.sub(r'\s+',' ',question.strip().lower()).rstrip('?.').strip()


def clause(text):
    original=text
    absence=re.match(r'^(?:no(?: recorded| observed)?|without(?: any)?|no matching)\s+',text)
    observation='not_observed' if absence else 'present'
    if absence:text=text[absence.end():]
    text=re.sub(r'^(?:a|an) ','',text)
    # Structured observations preserve orders, fills and actual care distinctions.
    m=re.fullmatch(r'(?:structured|coded) (?P<topic>'+DIAG_PATTERN+r') (?:diagnosis|code|diagnosis code)',text)
    if not m:m=re.fullmatch(r'(?:structured|coded) (?:diagnosis|code) of (?P<topic>'+DIAG_PATTERN+r')',text)
    if m:return {'source':'structured','concept':DIAGNOSES[m['topic']],'concept_match':'prefix','observation':observation},original
    m=re.fullmatch(r'(?:completed|complete) nephrology referral record',text)
    if m:return {'source':'structured','concept':'referral:nephro','concept_match':'prefix','value_equals':'completed','observation':observation},original
    m=re.fullmatch(r'(?P<value>positive|negative) structured (?:financial hardship|affordability) screen',text)
    if m:return {'source':'structured','concept':'financial_hardship','value_equals':m['value']=='positive','observation':observation},original
    m=re.fullmatch(r'structured (?P<drug>'+MED_PATTERN+r') (?P<event>order|pharmacy claim record|administration record|history record)',text)
    if m:return {'source':'structured','concept':'medication:'+MEDICATIONS[m['drug']],'relation':{'order':'ordered','pharmacy claim record':'claim_transaction','administration record':'administration_record','history record':'history_record'}[m['event']],'observation':observation},original
    # These are documentation relations; a request is never an attended visit.
    if re.fullmatch(r'(?:nephrology records requested|requested nephrology records|requests for nephrology records) in (?:the |their )?notes',text):
        return {'source':'text','concept':'care nephrology','relation':'record_request','observation':observation},original
    m=re.fullmatch(r'notes (?:that )?(?P<verb>mention|mentioning|affirm|affirming|deny|denying|document|documenting) (?P<topic>'+TOPIC_PATTERN+r')',text)
    if m:
        c={'source':'text','concept':TOPICS[m['topic']],'observation':observation}
        if m['verb'] not in ('mention','mentioning'):c.update(assertion='negated' if m['verb'] in ('deny','denying') else 'affirmed',subject='patient')
        return c,original
    m=re.fullmatch(r'(?:(?P<assertion>affirmed|documented|denied|negated|uncertain|retracted) )?(?:(?P<time>current|historical|planned) )?(?P<topic>'+TOPIC_PATTERN+r') (?:in (?:the |their )?notes|in narrative text|annotations)',text)
    if m:
        # 'No dyspnea in notes' could mean a denial or missing documentation.
        # Require explicit absence of annotations, or 'no notes mentioning ...'.
        if absence and not text.endswith(' annotations'):return None,original
        c={'source':'text','concept':TOPICS[m['topic']],'observation':observation}
        if m['assertion']:c.update(assertion=ASSERTIONS[m['assertion']],subject='patient')
        if m['time']:c['time']=m['time']
        return c,original
    return None,original


def explain(plan):
    topics={v:k for k,v in TOPICS.items()}
    topics.update({'diagnosis heart failure':'heart failure','diagnosis ckd':'chronic kidney disease','symptom dyspnea':'dyspnea','symptom edema':'edema','ef':'ejection fraction','outside care':'outside records','financial hardship':'financial hardship','care nephrology':'nephrology','medication bb':'beta blocker','diagnosis:I50':'heart failure diagnosis codes (I50 prefix)','diagnosis:N18':'kidney disease diagnosis codes (N18 prefix)','referral:nephro':'nephrology referral','financial_hardship':'financial hardship screen'})
    topics.update({'medication:'+v:k for k,v in MEDICATIONS.items()})
    relations={'ordered':'medication order','claim_transaction':'pharmacy claim transaction','administration_record':'administration record','history_record':'medication history record','record_request':'request for records'}
    assertions={'affirmed':'affirmed in the source','negated':'explicitly denied in the source','uncertain':'uncertain in the source','retracted':'retracted in the source'}
    out=[]
    for c in plan['clauses']:
        singular='narrative annotation' if c['source']=='text' else 'structured observation'
        detail=[topics.get(c['concept'],c['concept'])]
        if 'assertion' in c:detail.append(assertions.get(c['assertion'],c['assertion']))
        if 'subject' in c:detail.append('about the patient' if c['subject']=='patient' else 'subject: '+c['subject'])
        if 'time' in c:detail.append(c['time']+' according to the source account')
        if 'relation' in c:detail.append(relations.get(c['relation'],c['relation'].replace('_',' ')))
        if 'value_equals' in c:detail.append('recorded value: '+str(c['value_equals']))
        if 'event_date_from' in c:detail.append('source event date on/after '+c['event_date_from'])
        if 'event_date_through' in c:detail.append('source event date on/before '+c['event_date_through'])
        out.append(('No matching '+singular+'s' if c['observation']=='not_observed' else 'At least one matching '+singular)+': '+'; '.join(detail))
    return out


def compile_question(question):
    q=normalize(question);base={'schema':VERSION,'question':question,'synthetic_workspace':True,'compiler_hash':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'new_paid_api_calls':0,'clinical_decision_authority':False}
    def unsupported(reason,unmatched):
        return {**base,'status':'unsupported_question','plan':None,'reason':reason,'unmatched_text':unmatched,'examples':EXAMPLES,'automatic_execution':False,'question_fully_answered':False}
    m=PREFIX.match(q)
    if not m:return unsupported('Use an explicit recorded-evidence question. Clinical eligibility, causality, ranking and treatment decisions are outside this compiler.',q)
    q=q[m.end():]
    date_match=re.search(r' using source event dates from (\d{4}-\d{2}-\d{2}) through (\d{4}-\d{2}-\d{2})$',q)
    dates={}
    if date_match:dates={'event_date_from':date_match[1],'event_date_through':date_match[2]};q=q[:date_match.start()]
    # Mixed conjunctions need explicit grouping; never guess precedence.
    parts=re.split(r'\s+(and|but|or)\s+',q)
    connectors=parts[1::2]
    if 'or' in connectors and any(c in ('and','but') for c in connectors):return unsupported('Mixed AND/OR requires an explicit evidence plan with unambiguous grouping.',q)
    compiled=[];bindings=[]
    for raw in parts[::2]:
        c,span=clause(raw)
        if c is None:return unsupported('This clause includes a concept, qualifier or relation that is not fully supported. No partial query was executed.',raw)
        compiled.append({**c,**dates});bindings.append({'question_clause':span,'evidence_clause':{**c,**dates}})
    try:plan=validate_plan({'match':'any' if 'or' in connectors else 'all','clauses':compiled})
    except ValueError as exc:return unsupported(str(exc),q)
    return {**base,'status':'compiled_evidence_plan','plan':plan,'clause_bindings':bindings,'interpretation':explain(plan),'combination':plan['match'],'automatic_execution':True,
        'question_fully_answered':'Only the displayed recorded-evidence interpretation; not clinical eligibility or care delivery',
        'limitations':['Controlled language, not unrestricted medical reasoning','A mention can be negated or about someone else unless qualified','Current/historical are source-relative annotation labels','Source event dates are not verified physiological event dates','No matching indexed evidence does not prove care was absent']}


def answer(path,question,*,limit=20,offset=0):
    proposed=compile_question(question)
    if proposed['plan'] is None:return {**proposed,'result':None}
    try:result=execute(path,proposed['plan'],limit=limit,offset=offset)
    except ValueError as exc:return {**proposed,'status':'unsupported_snapshot_constraint','automatic_execution':False,'question_fully_answered':False,'reason':str(exc),'result':None}
    return {**proposed,'result':result,'status':'evidence_answer'}
