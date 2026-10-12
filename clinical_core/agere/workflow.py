"""User-authorized synthetic evidence workflow; source facts do not require invented human labels."""
from ..decision_kernel import DecisionProgram
from ..contracts import digest

SPEC={'id':'agere-observed-evidence-workflow','version':'1','synthetic':True,
      'effective_from':'2024-01-01','effective_to':'2027-12-31',
      'authority':'User clinician-approved synthetic development; source-observation predicates, not treatment directives',
      'facts':{name:{'question':question,'source':'Dated observable warehouse snapshot'} for name,question in {
          'unread_document':'Does this snapshot contain a received document without usable extracted text?',
          'corrected_report':'Does this snapshot contain a visible superseding document?',
          'reported_financial_difficulty':'Is difficulty paying explicitly reported in the latest available financial screen?',
          'documented_medication_supply':'Does at least one medication have dated dispensing evidence covering the cutoff?'}.items()},
      'constraints':[],
      'branches':[{'when':{'fact':'unread_document'},'outcome':'obtain_document_text'},
                  {'when':{'fact':'corrected_report'},'outcome':'reconcile_corrected_report'},
                  {'when':{'fact':'reported_financial_difficulty'},'outcome':'financial_support_eligibility_review'},
                  {'when':{'fact':'documented_medication_supply'},'outcome':'reconcile_documented_medication_supply'}],
      'default':'review_remaining_information_needs'}
PROGRAM=DecisionProgram(SPEC)

def analyze(records,analysis,as_of):
    snapshot='observed-snapshot:'+digest(records)
    unread=[r['record_id'] for r in records if r['source_table']=='documents' and not r.get('extracted_text')]
    corrected=[r['record_id'] for r in records if r['source_table']=='documents' and r.get('supersedes_record_id')]
    supply=[e['record_id'] for m in analysis['medications'] for s in m['supplies'] for e in s['evidence']]
    financial=analysis['clinical']['facts']['difficulty_paying']
    values={'unread_document':(bool(unread),unread or [snapshot]),'corrected_report':(bool(corrected),corrected or [snapshot]),
            'documented_medication_supply':(bool(supply),supply or [snapshot]),
            'reported_financial_difficulty':(financial,[analysis['clinical']['financial_evidence']['record_id']] if analysis['clinical']['financial_evidence'] else [])}
    facts={k:{'value':value,'status':'source_record' if value is not None else 'unknown','available_date':as_of,
              'valid_from':as_of,'valid_to':as_of,'support_ids':refs} for k,(value,refs) in values.items()}
    result=PROGRAM.analyze(facts,as_of)
    result['fact_scope']='Source presence at cutoff; no inference that undocumented care or financial hardship is absent'
    result['row_review_claimed']=False
    return result
