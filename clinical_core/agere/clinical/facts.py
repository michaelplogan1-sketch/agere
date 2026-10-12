"""Canonical observed facts. Source grounding is distinct from semantic correctness."""
from datetime import date
import json,re,math
from ...contracts import digest
from ..narrative import read, _context
from ..narrative_sources import text_fields

VERSION='clinical-facts/1'
DRUGS={'bb':r'metoprolol(?: succinate| tartrate)?|carvedilol|bisoprolol',
       'arni':r'sacubitril(?:[/ -]+valsartan)?|entresto',
       'mra':r'spironolactone|eplerenone',
       'sglt2':r'dapagliflozin|empagliflozin|canagliflozin|ertugliflozin'}
TEXT_CONCEPTS={
 'diagnosis:heart_failure':r'\b(?:heart failure|HFrEF|HFpEF|CHF)\b',
 'diagnosis:ckd':r'\b(?:chronic kidney disease|CKD)\b',
 'symptom:dyspnea':r'\b(?:dyspnea|shortness of breath|breathless(?:ness)?)\b',
 'symptom:edema':r'\b(?:edema|oedema|leg swelling)\b',
 'preference:declines':r'\b(?:declines?|declined|does not want|prefers? not to)\b',
 'care:nephrology':r'\b(?:nephrology|nephrologist)\b',
}


def source(r,field,value):
    return {'record_id':r['record_id'],'source_table':r['source_table'],'field':field,
            'event_date':r['event_date'],'available_date':r['available_date'],'value':value}


def fact(pid,concept,value,evidence,*,assertion='affirmed',subject='patient',temporality='unspecified',method='structured_source',**extra):
    obj={'patient_id':pid,'concept':concept,'value':value,'assertion':assertion,'subject':subject,
         'temporality':temporality,'evidence':evidence,'method':method,'clinical_time':None,
         'clinical_time_basis':'Source event date is not necessarily the physiological observation date',**extra}
    return {'fact_id':digest(obj),**obj}


def eligible(f):
    return f['assertion']=='affirmed' and f['subject'] in {'patient','patient_context'} and f['temporality']!='resolved'


def extract(pid,records,as_of,*,narrative=None):
    date.fromisoformat(as_of)
    for r in records:
        if r['patient_id']!=pid:raise ValueError('Cross-patient facts')
        if (r['event_date'] is not None and r['event_date']>as_of) or r['available_date']>as_of or r.get('identity_status')!='matched':
            raise ValueError('Fact extraction requires an identity-matched visible snapshot')
    facts=[]
    for r in records:
        tab=r['source_table']
        if tab=='labs':
            try:v=float(r['value'])
            except (ValueError,TypeError,KeyError):continue
            if math.isfinite(v):facts.append(fact(pid,'lab:'+str(r.get('test',r.get('code','unknown'))).lower(),{'value':v,'unit':r.get('unit')},[source(r,'value',r['value']),source(r,'unit',r.get('unit'))]))
        elif tab in {'financial','financial_screens'} and type(r.get('difficulty_paying_medical_bills')) is bool:
            facts.append(fact(pid,'financial_hardship',r['difficulty_paying_medical_bills'],[source(r,'difficulty_paying_medical_bills',r['difficulty_paying_medical_bills'])]))
        elif tab in {'medication_orders','medication_history','pharmacy_claims','medication_administration'} and r.get('drug_class'):
            relation={'medication_orders':'ordered','medication_administration':'administration_record','medication_history':'history_record','pharmacy_claims':'claim_transaction'}[tab]
            fields=('drug','drug_class','dose','status','days_supply','transaction_type','dispense_id','medication_taking_observed')
            facts.append(fact(pid,'medication:'+r['drug_class'],{k:r.get(k) for k in fields},[source(r,k,r[k]) for k in fields if k in r],relationship=relation))
        elif tab=='problem_lists':
            for i,p in enumerate(r.get('problems',[])):
                facts.append(fact(pid,'diagnosis:'+str(p.get('code',p.get('name','unknown'))),p,[source(r,f'problems[{i}]',p)],assertion='affirmed' if r.get('assertion_status')=='documented' else 'uncertain'))
        elif tab=='referrals':
            facts.append(fact(pid,'referral:'+r.get('service','unknown'),r.get('status'),[source(r,'service',r.get('service')),source(r,'status',r.get('status'))],relationship='referral_record_not_visit'))
    narrative=read(records) if narrative is None else narrative
    for m in narrative['mentions']:
        value={'lower':m['value'],'upper':m['upper_value'],'unit':'%'} if m['concept']=='ef' else (False if m['assertion']=='negated' else True)
        # Duplicates keep earliest source date; copies cannot independently refresh clinical state.
        evidence=sorted(m['occurrences'],key=lambda x:(x['event_date'] or '',x['available_date'],x['record_id']))
        facts.append(fact(pid,m['concept'],value,evidence,assertion=m['assertion'],subject=m['subject'],temporality=m['temporality'],method='context_rules',repeat_count=m['repeat_count']))
    for r in records:
        for part in text_fields(r):
            text=part['text']
            patterns={**TEXT_CONCEPTS,**{'medication:'+k:r'\b(?:'+v+r')\b(?:\s+\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?\s*mg(?:\s+(?:daily|twice daily|bid|qd))?)?' for k,v in DRUGS.items()}}
            for concept,pattern in patterns.items():
                for m in re.finditer(pattern,text,re.I):
                    ctx=_context(text,m,part['field']);ev={**source(r,part['field'],None),'start':m.start(),'end':m.end(),'quote':m[0],**ctx}
                    extra={}
                    if concept.startswith('medication:'):
                        context=ctx['context']
                        hold=re.search(r'\b(?:held|stopped|discontinued|contraindicated|intoleran\w*)\b',context,re.I)
                        neg_hold=re.search(r'\b(?:not|never)\s+(?:held|stopped|discontinued|contraindicated|intoleran\w*)',context,re.I)
                        taking=re.search(r'\b(?:takes|taking|continues|on)\b',context,re.I)
                        unconfirmed=re.search(r'\b(?:not confirmed|unconfirmed|uncertain|unknown|not taking)\b',context,re.I)
                        relation='hold_or_exception_mention' if hold and not neg_hold else 'reported_use' if taking and not unconfirmed else 'mention_only'
                        # Multi-drug clauses require relation disambiguation, rather than assigning the hold to all drugs.
                        drug_classes=[k for k,v in DRUGS.items() if re.search(r'\b(?:'+v+r')\b',context,re.I)]
                        if len(drug_classes)>1:relation='ambiguous_multi_drug_context'
                        extra={'relationship':relation,'clinical_appropriateness':'not_established'}
                    facts.append(fact(pid,concept,m[0],[ev],assertion=ctx['assertion'],subject=ctx['subject'],temporality=ctx['temporality'],method='context_rules',**extra))
    return {'version':VERSION,'as_of':as_of,'patient_id':pid,'facts':facts,
            'coverage':narrative['coverage'],'general_semantics_complete':False,
            'model_extraction_proposals':[],'care_gap_from_absence':False}


MODEL_TYPES=['diagnosis','medication','laboratory','symptom','treatment_exception','preference','social_barrier','care_event','coverage']
MODEL_ASSERTIONS=['affirmed','negated','uncertain','historical','retracted','hypothetical']


def validate_model_facts(proposals,passages):
    """Validate arbitrary clinical extraction proposals against EXACT submitted passages.

    Validation proves quotation/source identity, not clinical entailment. These facts
    remain quarantined from deterministic decision authority until reviewed.
    """
    if not isinstance(proposals,list) or len(proposals)>100:raise ValueError('Bounded fact list required')
    index={p['id']:p for p in passages};accepted=[]
    if len(index)!=len(passages):raise ValueError('Duplicate passage IDs')
    for p in proposals:
        required={'kind','concept','value','assertion','subject','source_id','quote'}
        if not isinstance(p,dict) or set(p)!=required:raise ValueError('Invalid extraction schema')
        if any(not isinstance(p[k],str) or len(p[k])>1000 for k in required):raise ValueError('Bounded strings required')
        if p['kind'] not in MODEL_TYPES or p['assertion'] not in MODEL_ASSERTIONS or p['subject'] not in {'patient','other','uncertain'}:raise ValueError('Invalid fact context')
        if not p['concept'].strip() or not p['value'].strip() or not p['quote'].strip():raise ValueError('Empty clinical fact')
        if p['source_id'] not in index:raise ValueError('Invented source')
        text=index[p['source_id']]['text'];starts=[m.start() for m in re.finditer(re.escape(p['quote']),text)]
        if not starts:raise ValueError('Invented quote')
        accepted.append({**p,'source_spans':[{'start':s,'end':s+len(p['quote'])} for s in starts],
                         'status':'source_grounded_model_proposal','semantic_validation':'pending',
                         'decision_authority':False,'unique_span':len(starts)==1})
    return accepted
