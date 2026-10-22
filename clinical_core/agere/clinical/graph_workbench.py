"""Population composition controls and result display for the live workbench."""
from .semantic_controls import SCRIPT as SEMANTIC_HELPERS
import json
from .cohorts import empty

WINDOW = {'basis': 'all_observed', 'start': '', 'end': ''}


def examples():
    def node(ident, operation, purpose, inputs=None, **parameters):
        return {'id': ident, 'operation': operation, 'inputs': inputs or [], 'parameters': parameters, 'purpose': purpose}
    def evidence(concept, source='text', **clauses):
        return {'match': 'all', 'clauses': [{'source': source, 'concept': concept, **clauses}]}
    base = node('base', 'cohort', 'All observed synthetic patients', specification=empty())
    hardship = node('narrative_hardship', 'evidence', 'Affirmed patient financial-hardship annotations',
                    plan=evidence('financial hardship', assertion='affirmed', subject='patient'))
    screen = node('positive_screen', 'evidence', 'Positive recorded financial-hardship screens',
                  plan=evidence('financial_hardship', source='structured', value_equals=True))
    admission = node('admission_notification', 'cohort', 'An observed ADT A01 admission notification', specification={
        **empty(), 'all': [{'kind': 'record', 'stream': 'adt', 'conditions': [{'field': 'event', 'op': 'eq', 'value': 'A01'}], 'window': WINDOW}]})
    return [{
        'title': 'Compare admission notifications and financial evidence',
        'question': 'Across all observed patients, compare the proportion with an ADT A01 notification between patients with affirmed patient financial-hardship text annotations and those without such annotations, and separately between positive financial screens and no observed positive screen. Show the comparison counts and source evidence for people matching both the narrative and ADT predicates. These are observed-record associations, not causes or clinical negatives.',
        'graph': {'interpretation': 'Compare observed admission notifications across narrative and structured financial-evidence groups.',
                  'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [base, admission, hardship, screen,
            node('comparison', 'compare', 'Observed endpoint rates, nominal statistical comparisons and source limits',
                 ['base', 'admission_notification', 'narrative_hardship', 'positive_screen']),
            node('both', 'intersect', 'Patients matching narrative hardship and an admission notification', ['narrative_hardship', 'admission_notification']),
            node('evidence', 'retrieve', 'Inspect source evidence in that exact intersection', ['both'],
                 query='financial difficulty medication affordability admission', analysis_window=WINDOW)]}}, {
        'title': 'Investigate positive screens without narrative annotations',
        'question': 'Find patients with positive structured financial-hardship screens but no affirmed patient financial-hardship text annotation in the existing reader index. Calculate source completeness for the complete selected set and show source evidence. A missing annotation is not proof the raw notes lack that information.',
        'graph': {'interpretation': 'Find positive financial screens without a matching narrative annotation, then inspect the selected patients and their sources. Missing annotations may reflect reader coverage.',
                  'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [hardship, screen,
            node('screen_only', 'difference', 'Positive screen without a matching narrative annotation', ['positive_screen', 'narrative_hardship']),
            node('source_coverage', 'analyze', 'Inspect all selected patients through the existing source operator', ['screen_only'],
                 operator='source_completeness', drug_class='', view='all', analysis_window=WINDOW),
            node('evidence', 'retrieve', 'Inspect received source evidence within the selected set', ['screen_only'],
                 query='financial difficulty affordability', analysis_window=WINDOW)]}}]


def model_examples():
    base={'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'All observed synthetic patients'}
    return [{
        'title':'Run local forecasts + source change points',
        'question':'Run all attached research forecast models over the complete population and investigate changes in the arrival of ADT, notes and lab records. Show model limitations and exact input evidence.',
        'graph':{'interpretation':'Local fitted models and Poisson source-change segmentation over the same observed population.',
                 'coverage':'supported_observation','unmet_requirements':[],'nodes':[base,
            {'id':'forecasts','operation':'forecast','inputs':['population'],'parameters':{'model':'all'},'purpose':'Compare fitted local research predictors'},
            {'id':'source_changes','operation':'source_changes','inputs':['population'],'parameters':{'streams':['adt','notes','labs']},'purpose':'Find shifts in received-record intensity'}]}}, {
        'title':'Review one matching chart with Jev + Perplexity (API)',
        'question':'Use Jev and Perplexity to review one synthetic patient with affirmed financial-hardship text and an ADT A01 notification. Does the supplied evidence document financial hardship for this patient?',
        'live':True,
        'graph':{'interpretation':'Compare two specialist proposals against source evidence for one patient in a complete observed intersection.',
                 'coverage':'supported_observation','unmet_requirements':[],'nodes':examples()[0]['graph']['nodes'][1:3]+[
            {'id':'overlap','operation':'intersect','inputs':['admission_notification','narrative_hardship'],'parameters':{},'purpose':'Observed admission notification and narrative financial hardship'},
            {'id':'specialists','operation':'review','inputs':['overlap'],
             'parameters':{'providers':['jev','perplexity'],'maximum_patients':1,'question':'Does the supplied evidence document financial hardship for this patient?'},
             'purpose':'Independent Jev and Perplexity evidence proposals'}]}}]


def longitudinal_examples():
    def node(ident,op,inputs,**p):
        return {'id':ident,'operation':op,'inputs':inputs,'parameters':p,'purpose':ident.replace('_',' ')}
    base=node('observed_population','cohort',[],specification=empty())
    anchor={'kind':'record','event_type':'admission'}
    sequence=node('recorded_admission_to_discharge','sequence',['observed_population'],first=anchor,
                  second={'kind':'record','event_type':'discharge'},min_days=0,max_days=30,
                  same_organization=True,basis='source_event',available_by='2026-09-30')
    def context(ident,inputs,basis):
        return node(ident,'longitudinal',inputs,anchor=anchor,before_days=30,after_days=30,
                    concepts=['financial hardship','outside care','medication bb','goals of care'],
                    basis=basis,available_by='2026-09-30')
    return [{
        'title':'Reconstruct records around admissions',
        'question':'Across all observed synthetic patients, find recorded admission notifications followed by a discharge notification from the same organization within 0 to 30 days using source-event dates, with records available by 2026-09-30. For that complete patient group, reconstruct evidence 30 days before and after admission. Include financial hardship, outside care, medication bb and goals of care annotations, repeated or conflicting accounts, and provisional episode links. These are recorded sequences, not clinical causes or adjudicated hospital stays.',
        'graph':{'interpretation':'Follow admission/discharge source-event sequences and inspect surrounding evidence across the complete matching population.',
                 'coverage':'supported_observation','unmet_requirements':[],
                 'nodes':[base,sequence,context('surrounding_evidence',['recorded_admission_to_discharge'],'source_event')]}}, {
        'title':'Expose missing clinical timing',
        'question':'For all synthetic patients with admission notifications, reconstruct surrounding evidence using explicit clinical dates only, with records available by 2026-09-30. Expose admissions and evidence that cannot be placed in clinical time. Never replace a missing clinical date with a source timestamp.',
        'graph':{'interpretation':'Separate source chronology from clinically dated evidence and expose unresolved timing.',
                 'coverage':'supported_observation','unmet_requirements':[],
                 'nodes':[base,context('clinical_timing_gaps',['observed_population'],'clinical')]}}]


def state_examples():
    return [{'title':'Reconcile clinical evidence prerequisites',
      'question':'Across every observed synthetic patient, reconcile the documentary evidence prerequisites for heart failure therapy, renal review and affordability assistance. Separate known reports, conflicts, unobserved or undated evidence and inputs whose interpretation is not implemented. Do not infer clinical eligibility or missing care.',
      'graph':{'interpretation':'Reconcile versioned review prerequisites across the complete population.',
        'coverage':'supported_observation','unmet_requirements':[],'nodes':[
          {'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'All observed synthetic patients'},
          {'id':'prerequisites','operation':'state_review','inputs':['population'],'parameters':{'contract':'all'},'purpose':'Clinical evidence and unresolved prerequisites'}]}}]


def medication_examples():
    return [{'title':'Normalize medication identities locally',
      'question':'Across all observed synthetic patients, normalize recorded medication names against the pinned RxNorm subset, inspect literal formulation and dose notation, and retain named drugs in received free text. Show recorded ACEi, ARB and ARNi evidence separately. Do not infer current use, eligibility or maximum tolerated dose.',
      'graph':{'interpretation':'Resolve documentary medication identities locally while preserving formulation and exposure uncertainty.',
      'coverage':'supported_observation','unmet_requirements':[],'nodes':[
       {'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'Complete observed synthetic population'},
       {'id':'medications','operation':'medication_identity','inputs':['population'],'parameters':{},'purpose':'Medication identity and protocol context'}]}}]


def context_examples():
    presets=[('medication','Build medication context',
        'What do the received medication records and notes say about use, holds and uncertainty?',
        ['medication_orders','medication_history','pharmacy_claims','medication_administration','notes','documents'],
        ['medication bb'],''),
      ('outside_care','Build outside-care context',
        'What is documented about outside care, referrals and whether completion is known?',
        ['referrals','encounters','notes','documents'],['outside care','care nephrology'],''),
      ('access','Build affordability context',
        'What is documented about affordability and access, and what assistance prerequisites remain unknown?',
        ['financial','sdoh','coverage','notes','documents'],['financial hardship'],'affordability')]
    result=[]
    for key,title,question,streams,concepts,contract in presets:
        result.append({'title':title,'question':'Across all observed synthetic patients, assemble task-specific source context: '+question,
          'graph':{'interpretation':'Assemble '+key.replace('_',' ')+' evidence with history, uncertainty, source limits and explicit budget omissions.',
            'coverage':'supported_observation','unmet_requirements':[],'nodes':[
              {'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'All observed synthetic patients'},
              {'id':'task_context','operation':'context','inputs':['population'],
               'parameters':{'question':question,'streams':streams,'concepts':concepts,'contract':contract,'program_ids':[],'max_bytes':24000},
               'purpose':'Assemble auditable source context; no clinical decision or paid model call'}]}})
    return result


def pass_examples():
    assertions=[('Prepare medication chart passes','Does the received evidence explicitly document a medication hold for this patient?'),
                ('Prepare outside-care chart passes','Does the received evidence explicitly document care received outside the contributing system for this patient?'),
                ('Prepare affordability chart passes','Does the received evidence explicitly document an affordability barrier for this patient?')]
    rows=[]
    for base,(title,assertion) in zip(context_examples(),assertions):
        row=json.loads(json.dumps(base));row['title']=title
        row['question']='Across all observed synthetic patients, prepare bounded source-evidence passes for this documentary assertion: '+assertion
        row['graph']['interpretation']='Partition complete selected charts into whole-statement passes with exact source accounting.'
        node=row['graph']['nodes'][-1];node['operation']='context_passes';node['id']='chart_passes'
        node['parameters'].update(question=assertion,max_bytes=10000)
        node['purpose']='Preserve every received unit or expose the indivisible source blockage'
        rows.append(row)
    return rows


def assertion_examples():
    rows=[]
    for base,label in zip(context_examples(),('medication','outside-care','affordability')):
        row=json.loads(json.dumps(base));row['title']='Reconcile '+label+' assertions'
        row['question']='Across all observed synthetic patients, reconcile '+label+' source assertions, dates, repeated statements and conflicting accounts. Show unresolved evidence and the next evidence-review steps; do not infer current clinical state.'
        row['graph']['interpretation']='Reconcile source-bound '+label+' documentary accounts across the complete population.'
        node=row['graph']['nodes'][-1];node['operation']='assertion_review';node['id']='source_assertions'
        node['parameters']={k:node['parameters'][k] for k in ('streams','concepts')}
        if label=='medication':node['parameters']['concepts']=['medication '+c for c in ('bb','acei','arb','arni','mra','sglt2')]
        node['purpose']='Separate recorded statements, proposed interpretations, timeline changes and unresolved evidence'
        rows.append(row)
    return rows



def interpretation_examples():
    rows=[]
    for row in assertion_examples():
        label=row['title'].replace('Reconcile ', '').replace(' source assertions', '').replace(' assertions','')
        row['title']='Use reviewed '+label+' interpretations'
        row['question']='Across all observed synthetic patients, combine saved '+label+' statement interpretations and explicit source reviews with structured records and documentary timelines. Preserve disagreements, source gaps and unresolved clinical state.'
        row['graph']['interpretation']='Join saved source interpretations and reviews to complete-population '+label+' documentary evidence.'
        node=row['graph']['nodes'][-1];node['operation']='interpretation';node['id']='interpreted_sources';node['parameters']['filter']='all'
        if label=='medication':node['parameters']['concepts'].append('preference declines')
        node['purpose']='Reconcile reviewed meaning while retaining machine proposals and unknown care state'
        rows.append(row)
    from copy import deepcopy
    row=deepcopy(rows[0]);row['title']='Investigate medication reader disagreements'
    row['question']='Across all observed synthetic patients, select those with saved medication or dose-preference reader disagreements. Inspect source completeness for that entire selected group. Reader disagreement is a review priority, not a clinical care gap.'
    row['graph']['interpretation']='Select the complete saved-reader-disagreement group and inspect its sources.'
    row['graph']['nodes'][-1]['parameters']['filter']='model_disagreement'
    row['graph']['nodes'].append({'id':'inspect_sources','operation':'analyze','inputs':['interpreted_sources'],
        'parameters':{'operator':'source_completeness','drug_class':'','view':'all','analysis_window':WINDOW},'purpose':'Inspect sources for the entire review-status group'})
    rows.append(row)
    return rows


def source_state_examples():
    rows=[]
    for title,concept,relation,clock,streams in [
        ('Inspect medication use reports','medication bb','reported_use','source_statement_date',['notes','medication_history','pharmacy_claims','medication_orders','medication_administration']),
        ('Inspect dated nephrology completion','care nephrology','care_completion','explicit_clinical_date',['notes','referrals']),
        ('Find documented affordability reports','financial hardship','reported_barrier','source_statement_date',['notes','financial']),
    ]:
        question=('Across all observed synthetic patients, select affirmed '+relation.replace('_',' ')+' accounts for '+concept+
            ' using the latest usable '+clock.replace('_',' ')+'. Keep unresolved evidence as unknown. '+
            'Run source completeness for the entire matched population. Do not infer current care or eligibility.')
        rows.append({'title':title,'question':question,'graph':{
            'interpretation':'Resolve '+concept+' documentary state, then inspect the complete matched group.',
            'coverage':'supported_observation','unmet_requirements':[], 'nodes':[
                {'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'Complete observed synthetic population'},
                {'id':'documented','operation':'source_state','inputs':['population'],
                 'parameters':{'streams':streams,'concept':concept,'relation':relation,'clock':clock,'filter':'affirmed'},
                 'purpose':'Select source-supported affirmative accounts; preserve unknowns'},
                {'id':'source_coverage','operation':'analyze','inputs':['documented'],
                 'parameters':{'operator':'source_completeness','drug_class':'','view':'all','analysis_window':WINDOW},
                 'purpose':'Inspect the entire matched group'}]}})
    return rows


def requirement_examples():
    from .evidence_requirements import definitions
    rows=[]
    for key,spec in definitions().items():
        rows.append({'title':spec['title'],
            'question':'Across the complete synthetic population, evaluate the documentary contract '+spec['title']+'. Show supported, contradicted and unresolved results, exact evidence and the missing inputs that could resolve an answer.',
            'graph':{'interpretation':spec['purpose'],'coverage':'supported_observation','unmet_requirements':[],
                     'nodes':[{'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'Complete observed synthetic population'},
                              {'id':'requirements','operation':'requirements','inputs':['population'],
                               'parameters':{'contract':key,'filter':'all'},'purpose':'Evaluate source-bound requirements without inferring missing care'}]}})
    from copy import deepcopy
    linked=deepcopy(rows[0]);linked['title']='Connect affordability evidence to program review'
    linked['question']='Select patients whose documentary affordability barrier contract is supported, and prepare affordability review work for that complete selected population. Preserve unknown membership and do not infer eligibility or contact patients.'
    linked['graph']['nodes'][1]['parameters']['filter']='supported'
    linked['graph']['nodes'].append({'id':'review_work','operation':'worklist','inputs':['requirements'],
        'parameters':{'program':'affordability'},'purpose':'Prepare evidence-filtered review work'})
    rows.append(linked)
    linked=deepcopy(next(r for r in rows if r['graph']['nodes'][1]['parameters']['contract']=='native_affordability_report'))
    linked['title']='Connect warehouse affordability reports to program review'
    linked['question']='Select the complete population with supported received affordability-screen reports and prepare affordability review work with the source evidence attached. Keep unknowns visible and do not infer program eligibility.'
    linked['graph']['nodes'][1]['parameters']['filter']='supported'
    linked['graph']['nodes'].append({'id':'review_work','operation':'worklist','inputs':['requirements'],
        'parameters':{'program':'affordability'},'purpose':'Prepare review work from native documentary evidence'})
    rows.append(linked)
    return rows


def protocol_change_examples():
    return [{'title':'Compare affordability protocol versions',
        'question':'Compare versions 1 and 2 of the affordability action review across all synthetic patients. Show which documentary criteria, readiness and review candidates changed, with both rule versions and patient source receipts. These are changes in data interpretation, not changes in patient outcomes. Do not activate rules or advance work.',
        'graph':{'interpretation':'Compare the archived legacy binding and pinned received-screen requirement over the same complete population.',
            'coverage':'supported_observation','unmet_requirements':[],'nodes':[
                {'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'Same full received population for both versions'},
                {'id':'version_impact','operation':'protocol_change','inputs':['population'],
                 'parameters':{'before':'affordability_action_review@1','after':'affordability_action_review@2'},'purpose':'Preserve every patient transition and both source receipts'}]}}]


def protocol_work_examples():
    from .protocol_programs import catalog
    return [{'title':spec['name']+' → persistent work',
        'question':'Across all synthetic patients, prepare persistent source-review work using the registered '+spec['name']+' protocol. Preserve supported, unresolved and nonmatching states and exact source evidence. Do not infer eligibility, contact patients or change treatment.',
        'graph':{'interpretation':'Use the versioned '+spec['name']+' definition as the source of persistent review work.',
            'coverage':'supported_observation','unmet_requirements':[],
            'nodes':[{'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'Complete selected synthetic population'},
                {'id':'work','operation':'worklist','inputs':['population'],'parameters':{'program':spec['id']},'purpose':'Retain all protocol assessment states and candidate evidence; no outreach'}]}} for spec in catalog()]


def accountability_examples():
    from copy import deepcopy
    base={'title':'Program evidence → accountable source review',
        'question':'Across all synthetic patients, prepare source-review requests for unresolved financial accounts and program payment evidence available by 2026-09-30. Keep coverage and care-team context separate from contract terms, attribution, measures and outcome evidence. Show exact missing prerequisites without inferring eligibility, savings or settlement.',
        'graph':{'interpretation':'Turn received account discrepancies and unbound contract prerequisites into immutable internal review work.',
            'coverage':'supported_observation','unmet_requirements':[],
            'nodes':[{'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'Complete selected synthetic population'},
                {'id':'accountability','operation':'accountability_review','inputs':['population'],'parameters':{'contract':'program_payment_evidence','available_by':'2026-09-30'},'purpose':'Persist financial evidence and unresolved program prerequisites'}]}}
    linked=deepcopy(base);linked['title']='Reduced-EF review → program and financial evidence'
    linked['question']='Select the supported Reduced-EF history and therapy review population, prepare financial and program evidence review requests available by 2026-09-30, and attach those immutable receipts to the heart failure review worklist. Keep clinical candidacy separate from attribution, program payment and account reconciliation. Do not contact, enroll or record clinical completion.'
    linked['graph']['nodes'].insert(1,{'id':'review','operation':'protocol_review','inputs':['population'],'parameters':{'protocol':'reduced_ef_therapy_review','filter':'supported'},'purpose':'Source-supported clinical review population'})
    linked['graph']['nodes'][-1]['inputs']=['review']
    linked['graph']['nodes'].append({'id':'work','operation':'worklist','inputs':['review','accountability'],'parameters':{'program':'hf_review'},'purpose':'Retain separate clinical and financial accountability evidence in immutable assessments'})
    return [base,linked]


def financial_examples():
    rows=[{'title':'Reconcile accounts and actual payment reports',
        'question':'Across all synthetic patients, reconcile received billing account snapshots and patient payments, refunds and payer cash allocations available by 2026-09-30. Separate claim adjudication from reported cash, show integer-cent accounting differences, unresolved source links and exact evidence. Do not infer hardship, collectability, savings or contract settlement.',
        'graph':{'interpretation':'Reconcile source-bound financial stocks and flows; repeated account snapshots are not cash transactions.',
            'coverage':'supported_observation','unmet_requirements':[],
            'nodes':[{'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'Complete selected synthetic population'},
                {'id':'accounts','operation':'financial_accounts','inputs':['population'],'parameters':{'available_by':'2026-09-30'},'purpose':'Financial source reconciliation and explicit review needs'}]}}]

    from copy import deepcopy
    linked=deepcopy(rows[0]);linked['title']='Reduced-EF review population → financial sources'
    linked['question']='Select all synthetic patients meeting the supported Reduced-EF history and therapy review criterion, then reconcile their billing snapshots, patient payments, refunds and payer cash allocations available by 2026-09-30. Keep clinical review criteria separate from hardship, program eligibility, intervention benefit and contract settlement. Show full denominators and source discrepancies.'
    linked['graph']['interpretation']='Join source-supported clinical review membership to received financial accounts without inferring treatment eligibility, hardship or intervention savings.'
    linked['graph']['nodes'].insert(1,{'id':'review','operation':'protocol_review','inputs':['population'],
        'parameters':{'protocol':'reduced_ef_therapy_review','filter':'supported'},'purpose':'Select the complete source-supported review criterion population'})
    linked['graph']['nodes'][-1]['inputs']=['review']
    return rows+[linked]


def landmark_examples():
    from .decision_landmarks import definitions
    rows=[]
    for key,spec in definitions().items():
        rows.append({'title':spec['title'],
            'question':'Across all synthetic patients, apply '+spec['title']+' using first index receipts from 2026-01-01 through 2026-09-30 and records available by 2026-09-30. Start the decision window the next day, keep only later clinical endpoints, and assemble evidence available before that decision. Show late historical endpoints, unresolved clocks, baseline sources and mature recorded targets. Do not infer clinical risk.',
            'graph':{'interpretation':spec['meaning'],'coverage':'supported_observation','unmet_requirements':[],
                'nodes':[{'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'Complete synthetic roster'},
                    {'id':'decision','operation':'decision_landmark','inputs':['population'],
                     'parameters':{'contract':key,'receipt_from':'2026-01-01','receipt_through':'2026-09-30','available_by':'2026-09-30'},
                     'purpose':'Separate first receipt, prior evidence and future clinically dated endpoints'}]}})
    return rows


def baseline_examples():
    from copy import deepcopy
    rows=[]
    for ex,title in zip(outcome_examples()[:3],['Evidence before admission notifications','Evidence before discharge notifications','Evidence before clinical-date discharge observations']):
        row=deepcopy(ex);row['title']=title
        row['question']=ex['question']+' Then assemble the registered received_prior_180d baseline: evidence received and source-dated before each index day, with measurements, note annotations, medication supply and source arrival blocks. Keep targets out of feature values and expose missing indices.'
        row['graph']['interpretation']='Connect the outcome index to individually dated baseline evidence; later-arriving data cannot become pre-index features.'
        row['graph']['nodes'][1]['parameters']['filter']='all'
        row['graph']['nodes'].append({'id':'baseline','operation':'baseline','inputs':[row['graph']['nodes'][1]['id']],
            'parameters':{'profile':'received_prior_180d'},'purpose':'Assemble candidate inputs with patient-specific pre-index cutoffs and exact source accounting'})
        rows.append(row)
    return rows


def protocol_examples():
    from .care_protocols import definitions
    rows=[]
    for key,spec in definitions().items():
        rows.append({'title':spec['title'],'question':'Across all synthetic patients, apply the registered care review '+spec['title']+'. Show supported criteria, source interpretations needing review, missing inputs and exact evidence. Propose review steps without inferring missing care or treatment eligibility.',
            'graph':{'interpretation':spec['purpose'],'coverage':'supported_observation','unmet_requirements':[],
                'nodes':[{'id':'population','operation':'cohort','inputs':[],'parameters':{'specification':empty()},'purpose':'Full synthetic population'},
                    {'id':'protocol','operation':'protocol_review','inputs':['population'],'parameters':{'protocol':key,'filter':'all'},'purpose':'Evaluate registered source-bound review criteria and unresolved inputs'}]}})
    from copy import deepcopy
    linked=deepcopy(rows[-1]);linked['title']='Affordability evidence → review queue'
    linked['question']='Across all synthetic patients, select the supported received affordability reports in protocol version 2 and prepare its persistent review worklist with the exact documentary requirement and source evidence attached. Do not infer current hardship, assistance eligibility, enroll or contact patients.'
    linked['graph']['nodes'][1]['parameters']['filter']='supported'
    linked['graph']['nodes'].append({'id':'work','operation':'worklist','inputs':['protocol'],'parameters':{'program':'protocol:affordability_action_review@2'},'purpose':'Attach current source-bound protocol receipts to immutable program review proposals'})
    return rows+[linked]


def outcome_examples():
    from .outcomes import definitions
    from copy import deepcopy
    rows=[]
    for key,spec in definitions().items():
        rows.append({'title':spec['title'],
            'question':'Across all synthetic patients, apply the registered outcome definition '+spec['title']+' to first index observations from 2026-01-01 through 2026-09-30, using records available by 2026-09-30. Show every denominator, incomplete windows, unavailable dates, provisional links and exact source evidence. No observed event is not proof of no care.',
            'graph':{'interpretation':spec['purpose'],'coverage':'supported_observation','unmet_requirements':[],
                'nodes':[{'id':'population','operation':'cohort','inputs':[],
                          'parameters':{'specification':empty()},'purpose':'Complete observed synthetic population'},
                         {'id':'outcomes','operation':'outcome','inputs':['population'],
                          'parameters':{'contract':key,'index_from':'2026-01-01','index_through':'2026-09-30',
                                        'available_by':'2026-09-30','filter':'all'},
                          'purpose':'Define index events, follow-up and recorded endpoints'}]}})
    linked=deepcopy(rows[1]);linked['title']='Compare post-discharge observations across financial screens'
    linked['question']='Compare admission notifications 1 to 30 days after the first discharge notification among patients with and without a positive recorded financial-hardship screen. Use index observations from 2026-01-01 through 2026-09-30 and records available by 2026-09-30. Require calendar-mature follow-up for both positive and zero labels and exclude unresolved labels. This is a retrospective source-observation association, not a causal or predictive effect of a baseline financial condition.'
    nodes=linked['graph']['nodes'];nodes[1]['id']='indexed';nodes[1]['parameters']['filter']='mature_labeled'
    endpoint=deepcopy(nodes[1]);endpoint['id']='endpoint';endpoint['parameters']['filter']='observed';nodes.append(endpoint)
    nodes.append({'id':'positive_screen','operation':'evidence','inputs':[],'purpose':'Positive recorded financial-hardship screen',
        'parameters':{'plan':{'match':'all','clauses':[{'source':'structured','concept':'financial_hardship','value_equals':True}]}}})
    nodes.append({'id':'comparison','operation':'compare','inputs':['indexed','endpoint','positive_screen'],
        'parameters':{},'purpose':'Compare received outcomes in known indexed patients; financial screens are not restricted to baseline'})
    linked['graph']['interpretation']='Connect versioned outcome membership to existing exploratory comparisons; no baseline or causal interpretation.'
    rows.append(linked)
    return rows


def work_examples():
    return [{
        'title': 'Prepare ' + title + ' review work',
        'question': 'Prepare a review worklist across the complete observed synthetic population using the attached ' + title + ' workflow. Preserve unresolved inputs. Do not contact, enroll or change treatment.',
        'graph': {'interpretation': 'Prepare persistent review work from the entire observed population.',
                  'coverage': 'supported_observation', 'unmet_requirements': [], 'nodes': [
            {'id': 'population', 'operation': 'cohort', 'inputs': [], 'parameters': {'specification': empty()}, 'purpose': 'All observed synthetic patients'},
            {'id': 'review_work', 'operation': 'worklist', 'inputs': ['population'], 'parameters': {'program': program}, 'purpose': 'Prepare ' + title + ' review work'}]}}
        for program, title in [('hf_review', 'heart failure'), ('affordability', 'affordability')]]


PANEL = '''<section class="lab composition"><span class="pill">POPULATION COMPOSITION · LONGITUDINAL EVIDENCE · MODELS</span>
<h2>Follow a pattern across sources.</h2><p>Combine structured observations and chart annotations into complete patient groups. Compare recorded patterns, inspect the evidence, and run another analysis on the resulting population.</p><p><a href="/semantics">Statement interpretation and source-review workspace ↗</a></p>
<div class="controls"><button class="primary" id="compose">Plan with the economical stack</button></div>
<p id="model-policy" class="meta">Local plans → compact model → typed specialist → frontier when needed.</p>
<div class="chips" id="graph-examples"></div><p class="meta">Questions try supported local plans and cheap routing before frontier escalation. The specialist example uses paid APIs; the other examples execute locally. Examples use their displayed population definition; filters above do not alter them. Forecasts are research outputs. Statistical comparisons describe observations, not causes.</p>
<h3>Find what an answer still needs.</h3><p class="meta">Prepare specific evidence-review requests for the population filters above. Missing records, unresolved interpretations and conflicting accounts stay distinct. No requests are sent.</p>
<div class="controls"><label>Documentary checks (select one to eight)<select id="acquisition-contract" aria-label="Evidence acquisition contracts" multiple size="4"></select></label><button id="prepare-acquisition" class="chip">Prepare evidence-review queue</button></div></section>
<style>.composition{margin-top:20px}.composition h2{font-size:28px;font-weight:450}.graph-node{position:relative;border-left:2px solid #486e70;margin:12px 0;padding:8px 18px}.graph-node strong{color:#abf5d4}.graph-result-table{overflow:auto}.graph-result-table table{min-width:580px}.graph-node .meta{margin:5px 0}.bar{height:5px;background:#27484e;border-radius:8px;margin:7px 0;max-width:300px}.bar span{display:block;height:5px;background:#abf5d4;border-radius:8px}</style>'''

SCRIPT = SEMANTIC_HELPERS + r'''
function renderInputReuse(box,r){if(r.input_fingerprints){const f=r.input_fingerprints;box.append(N('p','Exact input fingerprints: '+f.patient_cache_hits.toLocaleString()+' patients reused · '+f.patients_recomputed.toLocaleString()+' recomputed · '+f.record_payloads_hashed.toLocaleString()+' source records rehashed.','meta'),details('Source change tracking and measured preparation cost',f))}}
function renderResolution(box,r){
 const panel=N('div',undefined,'source');panel.append(N('h3','What would resolve this answer?'));
 if(!r.resolution.complete)panel.append(N('p','The expression exceeded the bounded logic budget; no complete minimal-set claim.','missing'));
 else if(r.resolution.status==='already_resolved')panel.append(N('p','The declared documentary condition is already resolved. Other unassessed content remains separate.'));
 else for(const c of r.resolution.cases){const terms=Object.entries(c.binding_values).map(([name,v])=>name.replaceAll('_',' ')+' = '+(v?'supported':'contradicted')).join(' + ');panel.append(N('p','If '+terms+' → the contract becomes '+(c.contract_value?'supported':'contradicted')+'.'))}
 for(const request of r.requests)panel.append(N('h4',request.kind.replaceAll('_',' ')),N('p',request.instruction),details('Source entity and exact dependency',request.detail));
 panel.append(N('p','Conditional logic, not predicted benefit or clinical priority. No evidence requests were sent.','meta'),details('Conditional sets and source-bound request IDs',r));box.append(panel);
}
async function renderAcquisition(proposal,offset=0,container=null){
 const r=await semanticFetch('/api/stack/evidence-acquisition?id='+proposal.id+'&offset='+offset),a=container||N('article');
 if(!container){a.append(N('h2','Evidence-review queue'),N('span',r.counts.requests.toLocaleString()+' source-review requests','pill'),N('p',r.patients_evaluated.toLocaleString()+' selected synthetic patients evaluated · '+r.counts.patients_with_requests.toLocaleString()+' have unresolved inputs.','meta'));if(r.selection?.counts.unknown)a.append(N('p',r.selection.counts.unknown.toLocaleString()+' patients have unresolved cohort membership and are reported outside the selected set.','missing'));a.append(N('p','Proposals only. No requests sent; no missing-care or clinical-priority conclusion.','missing'),details('Scope, source version and complete counts',proposal));$('results').append(a)}
 if(!container)addPortfolioComparison(a,r);
 for(const item of r.items){const box=N('div',undefined,'source');box.append(N('h3',item.patient_id+' · '+item.kind.replaceAll('_',' ')),N('p',item.instruction),N('p',[item.detail.field?.replaceAll('_',' '),item.detail.entity,item.detail.dependency?.record_id,item.detail.relation?.replaceAll('_',' ')].filter(Boolean).join(' · '),'meta'),N('p',item.can_resolve_any_contract_alone?'Resolving this binding could settle a documentary condition.':'Other evidence may also be needed to settle the condition.','meta'));const inspect=N('button','Inspect blocking evidence','chip');inspect.onclick=async()=>{inspect.disabled=true;try{const data=await semanticFetch('/api/stack/evidence-acquisition/item?id='+r.id+'&item='+item.id);for(const row of data.requirements){box.append(N('h4',row.contract.title));for(const b of Object.values(row.bindings)){const view=structuredClone(b);if(item.detail.entity){view.state.selected_assertions=(view.state.selected_assertions||[]).filter(s=>s.entity===item.detail.entity);view.state.pending_accounts=(view.state.pending_accounts||[]).filter(s=>s.entity===item.detail.entity)}renderRequirementBinding(box,view)}renderResolution(box,await semanticFetch('/api/stack/evidence-resolution?id='+row.id))}box.append(details('Request provenance and conditional impact',data))}catch(e){box.append(N('p',String(e),'warn'))}finally{inspect.disabled=false}};box.append(inspect);a.append(box)}
 if(r.next_offset!==null){const more=N('button','More evidence requests','chip');more.onclick=async()=>{more.disabled=true;try{await renderAcquisition(proposal,r.next_offset,a);more.remove()}catch(e){more.disabled=false;$('status').textContent=String(e)}};a.append(more)}
}
fetch('/api/stack/requirement-contracts').then(r=>r.json()).then(r=>{for(const c of r.contracts)$('acquisition-contract').add(new Option(c.title,c.id,false,$('acquisition-contract').options.length===0))}).catch(e=>{$('status').textContent=String(e)});
$('prepare-acquisition').onclick=async()=>{const button=$('prepare-acquisition');button.disabled=true;try{const filters=scope();$('status').textContent='Resolving documentary inputs across the selected population…';const proposal=await semanticFetch('/api/stack/evidence-acquisition/prepare',{contracts:Array.from($('acquisition-contract').selectedOptions,o=>o.value),cohort:filters.cohort,patient:$('patient').value.trim()});$('results').replaceChildren();await renderAcquisition(proposal);$('status').textContent='Evidence-review queue prepared · 0 paid calls · 0 requests sent.'}catch(e){$('status').textContent=String(e)}finally{button.disabled=false}};
function renderAssertionReceipt(box,r){
 box.append(N('p',r.counts.structured_assertions+' literal structured assertions · '+r.counts.machine_proposals+' machine proposals · '+r.coverage.units_without_in_scope_assertions+' received units without an in-scope assertion','meta'));
 for(const g of r.groups){const line=N('div',undefined,'source');line.append(N('h3',g.concept+' · '+g.relation.replaceAll('_',' ')),N('p',g.status.replaceAll('_',' '),'meta'));for(const d of Object.values(g.latest_by_clock))line.append(N('p',d.date+' · '+d.date_basis.replaceAll('_',' ')+' · '+d.status.replaceAll('_',' ')));box.append(line)}
 const steps=N('ul');for(const s of r.next_evidence_steps)steps.append(N('li',s.detail));box.append(N('h3','Next evidence steps'),steps,details('Exact statements, sources, clocks, exclusions and interpretation authority',r));
 const machine=r.assertions.filter(a=>a.authority==='machine_annotation');if(machine.length){const section=N('details');section.append(N('summary','Refine individual source statements ('+machine.length+')'));for(const a of machine){const item=N('div',undefined,'source');item.append(N('p',a.concept+' · previous proposal: '+a.relation.replaceAll('_',' '),'meta'),N('blockquote',a.evidence.quote));semanticControl(item,{patient_id:r.patient_id,record_id:a.source.record_id,field:a.evidence.field,start:a.evidence.start,end:a.evidence.end,concept:a.concept});section.append(item)}box.append(section)}
}
function renderRequirementBinding(box,v){
 box.append(N('h3',v.label),N('p',v.result===null?'Unresolved':v.result?'Supported':'Contradicted'));
 for(const s of v.state.selected_assertions){
  if(s.entity)box.append(N('p',s.entity+' · source date '+(s.source_date||'unresolved'),'meta'));
  box.append(N('blockquote',s.evidence.quote||JSON.stringify(s.evidence.value)));
  if(s.evidence.fields)box.append(N('p',[...new Set(s.evidence.fields.map(e=>e.source_table+' / '+e.record_id))].join(' · '),'meta'));
  if(s.reconciliation){
   const chain=s.reconciliation,panel=N('div',undefined,'medication-chain');
   panel.append(N('h4','Medication evidence chain · '+(chain.reported_name_identity.raw||s.entity)),N('p','Each source keeps its own meaning. A pharmacy label does not verify the current regimen.','meta'));
   for(const source of chain.nodes.filter(n=>n.role!=='reference_origin')){
    const node=N('div',undefined,'graph-node');node.append(N('strong',source.role.replaceAll('_',' ')),N('p',(source.source_date||'Source date unknown')+' · received '+(source.arrival_date||'unknown'),'meta'),N('p',source.record_id+(source.field_path.length?' / '+source.field_path.join('.'):'') ,'meta'),details('Literal source entry',source.value));panel.append(node);
   }
   if(chain.copy_lineage.length)panel.append(N('p',chain.copy_lineage.length+' copied item link(s) retained as lineage, not independent corroboration.','meta'));
   panel.append(details('Exact edges, dose notation, terminology and limits',chain));box.append(panel);
  }
 }
 for(const pending of v.state.pending_accounts||[]){
  box.append(N('h4','Evidence needed · '+pending.entity),N('p',pending.account_status.replaceAll('_',' ')));
  for(const d of pending.source_dependencies)box.append(N('p',d.reason.replaceAll('_',' ')+': '+d.record_id+' ('+d.field+')','meta'));
  if(pending.missing_fields.length)box.append(N('p','Unresolved fields: '+pending.missing_fields.join(', '),'meta'));
 }
 for(const step of v.state.next_evidence_steps)box.append(N('p',step,'meta'));
}
function renderProtocolRequirement(box,v){
 const ref=v.requirement_reference;if(!ref)return;
 box.append(N('p','Documentary input: '+ref.contract_id+' · version '+ref.version+' · '+ref.status,'meta'),N('p','This source requirement supplies the declared review criterion. Current need, consent and program eligibility remain separate.','missing'));
 const b=N('button','Inspect decision source account','chip');b.onclick=async()=>{b.disabled=true;try{const row=await semanticFetch('/api/stack/requirements?id='+encodeURIComponent(ref.receipt_id));for(const binding of Object.values(row.bindings))renderRequirementBinding(box,binding);renderResolution(box,await semanticFetch('/api/stack/evidence-resolution?id='+encodeURIComponent(ref.receipt_id)));box.append(details('Pinned documentary rule and exact source receipt',row))}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};box.append(b);
}
function renderStateReview(r){
 const a=N('article');a.append(N('h2','Clinical evidence prerequisites'),N('span',r.patients_evaluated.toLocaleString()+' patients evaluated','pill'),N('p','An interpretation we have not implemented is different from evidence we have not observed. Documented findings alone do not establish current clinical state.','missing'));
 const labels=Object.fromEntries(r.contracts.flatMap(c=>c.requirements.map(x=>[x.binding,x.label]))),states={documented:'Documented',unobserved:'Not observed',undated:'Clinical date unresolved',historical_or_excluded:'Historical / excluded',unsupported:'Interpretation not implemented',unknown_semantics:'Clinical meaning unresolved',conflicting:'Conflicting accounts'};
 const table=N('table'),head=N('tr');['Required input','Observed documentation states'].forEach(x=>head.append(N('th',x)));table.append(head);
 for(const [key,counts] of Object.entries(r.requirement_counts)){const row=N('tr');row.append(N('td',labels[key]||key),N('td',Object.entries(counts).map(([k,v])=>(states[k]||k.replaceAll('_',' '))+': '+v.toLocaleString()).join(' · ')));table.append(row)}a.append(table);
 for(const p of r.patients.slice(0,3)){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id));for(const e of p.evaluations){box.append(N('p',e.contract.title+' · '+e.status.replaceAll('_',' '),'meta'),details('Requirement states, exact source evidence and missing prerequisites',e))}if(p.therapy.length)box.append(details('Supply, reported use, exceptions and dose notation',p.therapy));a.append(box)}
 a.append(details('Complete population counts and versioned contract receipt',r));$('results').append(a);
}
function renderWorkProposal(r){
 const a=N('article');a.append(N('h2',r.program.name+' · review work'),N('span',r.counts.candidates.toLocaleString()+' candidates','pill'),N('p',r.counts.selected.toLocaleString()+' selected patients assessed across the complete set · '+r.counts.missing_assessment+' missing assessments.','meta'),N('p',r.scope));
 const actor=N('input');actor.placeholder='Recorded by';actor.setAttribute('aria-label','Recorded by');actor.maxLength=120;const save=N('button','Save / refresh persistent work','primary');
 save.onclick=async()=>{save.disabled=true;try{const response=await fetch('/api/stack/work/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({proposal_id:r.proposal_id,actor:actor.value})});const d=await response.json();if(!response.ok)throw Error(d.error);$('status').textContent=d.changes.created+' created · '+d.changes.unchanged+' unchanged · '+d.changes.evidence_changed+' changed assessments. Open the program workspace to assign and review.'}catch(e){$('status').textContent=e.message}finally{save.disabled=false}};
 const link=N('a','Open program workspace ↗');link.href='/programs';link.style.marginLeft='15px';a.append(actor,save,link,details('Full-population proposal and unresolved inputs',r));$('results').append(a);
}
function renderGraph(d){
 const intro=N('article');intro.append(N('h2','A connected population analysis'),N('p',d.graph.interpretation),N('span',(d.answer_coverage?.status||d.graph.coverage).replaceAll('_',' '),'pill'));
 if(d.answer_coverage){intro.append(N('p',d.answer_coverage.meaning,'meta'));for(const c of d.answer_coverage.source_state_nodes){intro.append(N('p',c.concept+': '+c.patients_without_scoped_records+' patients without selected source records; '+c.patients_with_unmapped_received_units+' with received units outside the mapped assertions.','missing'),details('Received evidence and mapping coverage · '+c.concept,c))}}
 if(d.planner_receipt){const r=d.planner_receipt,labels={local_compiler:'Local compiler · no hosted planning',exact_question_cache:'Exact question replay · no hosted planning',compact_retrieval_typed_router:'MiniLM → Perplexity · frontier skipped',frontier_escalation:'Frontier escalation'};intro.append(N('p',labels[r.route]||r.model||'Recorded planner','meta'));if(r.escalation_reason)intro.append(N('p','Escalation reason: '+r.escalation_reason.replaceAll('_',' '),'meta'));const cost=d.planning_economics;if(cost)intro.append(N('span',cost.new_api_estimate_usd===null?'Planning API cost incomplete':'New planning API estimate: $'+cost.new_api_estimate_usd.toFixed(8),'pill'));intro.append(N('p','API estimates exclude local hardware and human review. Routing confidence is not clinical confidence.','meta'))}
 if(d.cache_hit)intro.append(N('span','Cached calculation','pill'));
 if(d.graph.unmet_requirements.length){intro.append(N('p','Still required to answer the whole question','warn'));const list=N('ul');d.graph.unmet_requirements.forEach(x=>list.append(N('li',x)));intro.append(list)}
 if(d.results.some(x=>['observation_process','source_state','pre_event_state','requirements','protocol_review','program_document','measure_review','financial_accounts','accountability_review','outcome','reference_review'].includes(x.node.operation)))addGraphDecision(intro,d);
 intro.append(details('Executable dependency graph and model usage',{graph:d.graph,planner_receipt:d.planner_receipt,compute:d.compute,result_id:d.result_id}));$('results').append(intro);
 for(const item of d.results){const n=item.node,r=item.result;
  if(n.operation==='appointment_followthrough'){
   const a=N('article');a.append(N('h2','Received appointment follow-through'),N('p',r.patients_evaluated+' patients assessed · '+r.source_count+' source reports · received by '+r.parameters.available_by,'scope-notice'),N('p','First received booking per patient since '+r.contract.receipt_from+'. Missing, conflicting or immature follow-up remains unresolved; an explicit no-show does not establish its cause or current care needs.','missing'));
   for(const [state,count] of Object.entries(r.status_counts))a.append(N('span',count+' '+state.replaceAll('_',' '),'pill'));
   const select=N('select'),label=N('label','Appointment observation patient'),inspect=N('button','Inspect exact booking evidence'),work=N('button','Prepare question-linked appointment work'),out=N('div');select.setAttribute('aria-label','Graph appointment patient');
   for(const p of r.patients){const o=N('option',p.patient_id+' · '+p.status.replaceAll('_',' '));o.value=p.patient_id;select.append(o)}label.append(select);
   const canWork=()=>{work.disabled=!d.question||!r.appointment_review_id||!r.selected_patient_ids.includes(select.value)||!r.patients.find(p=>p.patient_id===select.value)?.source_count;inspect.disabled=!select.value};select.onchange=canWork;canWork();
   const call=async(action,body)=>{const response=await fetch('/api/stack/appointment-graph/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const value=await response.json();if(!response.ok)throw Error(value.error);return value};
   inspect.onclick=async()=>{inspect.disabled=true;try{const row=await call('inspect',{receipt_id:r.appointment_graph_id,patient_id:select.value});out.replaceChildren(N('h3',row.patient.patient_id+' · '+row.patient.status.replaceAll('_',' ')),details('Booking, terminal reports, exact source hashes and unresolved evidence',row));}catch(e){out.replaceChildren(N('p',e.message,'missing'))}finally{canWork()}};
   work.onclick=async()=>{work.disabled=true;try{renderWorkProposal(await call('work',{result_id:d.result_id,node_id:n.id,patient_id:select.value}))}catch(e){out.replaceChildren(N('p',e.message,'missing'))}finally{canWork()}};
   a.append(label,inspect,work,out);if(r.appointment_review_id){const link=N('a','Open existing appointment review and buyer navigation');link.href='/stack?appointment='+encodeURIComponent(r.appointment_review_id);a.append(link)}else a.append(N('p','Historical receipt cutoff: current-snapshot work is unavailable.','meta'));
   a.append(details('Complete membership, observation contract and limitations',r));$('results').append(a);continue;
  }
  if(n.operation==='measure_review'){const a=N('article');renderMeasure(a,r,semanticFetch);$('results').append(a);continue;}
  if(n.operation==='evidence_portfolio'){const a=N('article');renderEvidencePortfolio(a,r);$('results').append(a);continue;}
  if(n.operation==='program_acquisition'){const a=N('article');renderProgramAcquisition(a,r,semanticFetch);$('results').append(a);continue;}
  if(n.operation==='program_document_change'){
   const a=N('article');a.append(N('h2','Impact of the quoted specification change'),N('p',r.packages.before.program+' · v'+r.packages.before.version+' → v'+r.packages.after.version),N('span',r.patients_evaluated.toLocaleString()+' synthetic patients compared','pill'),N('p',r.limits.join(' '),'missing'));
   for(const row of r.status_transitions)a.append(N('p',row.patients.toLocaleString()+' patients: '+row.before.replaceAll('_',' ')+' → '+row.after.replaceAll('_',' ')));
   for(const p of r.patients){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id),N('p','Changed inputs: '+(p.changed_inputs.join(', ')||'none')));const b=N('button','Inspect both quoted-rule versions','chip');b.onclick=async()=>{b.disabled=true;try{const row=await semanticFetch('/api/stack/program-document-change?id='+encodeURIComponent(r.id)+'&patient='+encodeURIComponent(p.patient_id));for(const side of ['before','after']){const receipt=row.program_receipts[side];box.append(N('h3',side+' · '+receipt.status.replaceAll('_',' ')));for(const v of Object.values(receipt.inputs)){box.append(N('p',v.label+': '+(v.value===null?'unresolved':String(v.value))),N('blockquote',v.quote.quote));if(v.reason)box.append(N('p',v.reason,'missing'))}}box.append(details('Before and after patient evidence, quoted inputs and review state',row))}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};box.append(b);a.append(box)}
   a.append(details('Complete version impact receipt',r));$('results').append(a);continue;
  }
  if(n.operation==='program_document'){
   const a=N('article');a.append(N('h2',r.package.purpose+' · quoted rule v'+r.package.version),N('span',r.patients_evaluated.toLocaleString()+' synthetic patients assessed','pill'),N('p','Mapping review: '+r.review.status.replaceAll('_',' ')+'. '+r.limits.join(' '),'missing'));
   for(const [state,count] of Object.entries(r.status_counts))a.append(N('span',count.toLocaleString()+' '+state.replaceAll('_',' '),'pill'));
   const link=N('a','Read the source specification and mappings ↗');link.href='/program-documents?package='+encodeURIComponent(r.package.id);a.append(N('p'),link);
   for(const p of r.patients){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id+' · '+p.status.replaceAll('_',' ')));const b=N('button','Inspect quoted rule and patient evidence','chip');b.onclick=async()=>{b.disabled=true;try{const row=await semanticFetch('/api/stack/program-documents?receipt='+encodeURIComponent(p.id));if(row.patient_id!==p.patient_id)throw Error('Program patient mismatch');for(const v of Object.values(row.inputs)){box.append(N('h3',v.label),N('blockquote',v.quote.quote),N('p',v.value===null?'Unresolved input':String(v.value)));if(v.reason)box.append(N('p',v.reason,'missing'));if(v.kind==='requirement')for(const binding of Object.values(v.evidence.bindings))renderRequirementBinding(box,binding);box.append(details('Exact mapped input and patient evidence',v))}box.append(details('Complete quoted-rule receipt',row))}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};box.append(b);a.append(box)}
   a.append(details('Complete population, immutable specification and reuse',r));$('results').append(a);continue;
  }
  if(n.operation==='accountability_review'){
   const a=N('article');a.append(N('h2','From source discrepancies to accountable review'),N('p',r.patients_evaluated.toLocaleString()+' synthetic patients · '+r.requests.toLocaleString()+' internal evidence requests'),N('p','Payment readiness remains unresolved. Contract requirements stay separate from clinical candidacy and received account amounts.','missing'));
   for(const [kind,count] of Object.entries(r.request_counts))a.append(N('p',kind.replaceAll('_',' ')+': '+count.toLocaleString(),'meta'));
   for(const task of r.contract_requests)a.append(N('h3',task.requirement_id.replaceAll('_',' ')),N('p',task.next_step));
   const queue=N('div'),more=N('button','Inspect saved review requests','chip');let offset=0;more.onclick=async()=>{more.disabled=true;try{const page=await semanticFetch('/api/stack/accountability?id='+r.id+'&offset='+offset);for(const task of page.items)queue.append(N('h3',task.patient_id||'Contract-level prerequisite'),N('p',task.next_step),details('Request, source identifiers and dependency',task));if(page.next_offset===null)more.remove();else {offset=page.next_offset;more.textContent='Load more review requests'}}catch(e){queue.append(N('p',String(e),'warn'))}finally{more.disabled=false}};a.append(more,queue);
   for(const p of r.patients){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id),N('p',p.requests+' internal requests','meta'));const b=N('button','Inspect program and financial evidence','chip');b.onclick=async()=>{b.disabled=true;try{const row=await semanticFetch('/api/stack/accountability?id='+r.id+'&patient='+encodeURIComponent(p.patient_id));box.append(details('Unresolved prerequisites, exact context and financial evidence',row))}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};box.append(b);a.append(box)}
   a.append(details('Immutable proposal and full-population counts',r));$('results').append(a);continue;
  }
  if(n.operation==='financial_accounts'){
   const a=N('article');a.append(N('h2','Accounts, decisions and payment reports'),N('p',r.patients_evaluated.toLocaleString()+' synthetic patients · '+r.accounts_evaluated.toLocaleString()+' accounts assessed'));
   for(const [status,count] of Object.entries(r.account_status_counts))a.append(N('p',status.replaceAll('_',' ')+': '+count.toLocaleString(),'meta'));
   a.append(N('p','Account snapshots are stocks. Payment records are flows. Claim decisions are neither proof of cash received nor realized savings. Differences call for source review; they do not establish hardship.','missing'));
   a.append(details('Accounting identities and transaction comparisons',{identities:r.equation_status_counts,cash:r.cash_comparison_counts}),details('One selected snapshot per account, separated by currency',r.selected_snapshot_totals_by_currency));
   for(const p of r.patients){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id),N('p',p.accounts+' accounts · '+p.unassigned_sources+' sources without resolved account binding','meta'));const b=N('button','Inspect financial sources','chip');
    b.onclick=async()=>{b.disabled=true;try{const row=await semanticFetch('/api/stack/financial-accounts?id='+encodeURIComponent(p.id));for(const account of row.accounts.slice(0,8)){box.append(N('h3',account.account_id),N('p',account.status.replaceAll('_',' ')),N('p',account.next_step),details('Snapshot amounts, cash comparisons and exact source links',account))}box.append(details('All patient accounts and unresolved sources',row))}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};box.append(b);a.append(box)}
   a.append(details('Complete financial reconciliation receipt',r));$('results').append(a);continue;
  }
  if(n.operation==='decision_landmark'){
   const a=N('article');a.append(N('h2',r.contract.title),N('p',r.patients_evaluated.toLocaleString()+' synthetic patients evaluated; decision day starts after the first index receipt.'));
   for(const [status,count] of Object.entries(r.status_counts))a.append(N('p',status.replaceAll('_',' ')+': '+count.toLocaleString(),'meta'));
   a.append(details('Mature targets with baseline evidence',r.candidate_label_counts),details('Endpoints excluded from the future window',r.excluded_endpoint_counts));
   a.append(N('p','A recorded zero means no matching received future event. Source capture and clinical outcomes remain unverified. No predictor is fitted by this question.','missing'));
   for(const p of r.patients){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id));
    if(p.decision)box.append(N('p','Clinical index '+p.decision.clinical_index_date+' · received '+p.decision.feature_cutoff+' · decision '+p.decision.date),N('p','Receipt lag: '+p.decision.receipt_lag_calendar_days+' calendar days. Baseline sources: '+p.baseline_source_count+'.','meta'));
    box.append(N('p',p.status.replaceAll('_',' ')));const b=N('button','Inspect decision-window evidence','chip');
    b.onclick=async()=>{b.disabled=true;try{box.append(details('Exact index, prior evidence, future endpoints and exclusions',await semanticFetch('/api/stack/decision-landmarks?id='+encodeURIComponent(p.id))))}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};
    box.append(b);a.append(box)}
   addPredictionReadiness(a,'receipt_day',r.identity);
   a.append(details('Complete decision-window receipt',r));$('results').append(a);continue;
  }
  if(n.operation==='reference_review'){
   const a=N('article');a.append(N('h2',r.reference.title),N('p',r.reference.paraphrase),N('span',r.patients_evaluated.toLocaleString()+' people retained','pill'),N('p','Published '+r.reference.published_on+' · inspected '+r.reference.checked_on+' · evidence available by '+n.parameters.available_by,'meta'),N('p',r.meaning,'missing'));
   for(const q of r.reference.questions)a.append(N('h3',q.question),N('p',(r.linked_patient_counts_by_question[q.key]||0).toLocaleString()+' people have linked context; applicability remains unresolved.','meta'));
   a.append(N('p','Continue this question into review to inspect exact patient evidence and assign supported internal review.'),details('Reference, lifecycle and population evidence',r));$('results').append(a);continue;
  }
  if(n.operation==='baseline'){
   const a=N('article');a.append(N('h2','Evidence before the index'),N('p',r.profile.purpose),N('span',r.patients_evaluated.toLocaleString()+' synthetic patients assessed','pill'),N('span','No paid model calls','pill'));
   for(const [status,count] of Object.entries(r.status_counts))a.append(N('span',count.toLocaleString()+' '+status.replaceAll('_',' '),'pill'));
   renderInputReuse(a,r);
   a.append(N('p','Both receipt date and source date must precede the index day. Outcome labels stay outside feature values. Source changes describe incoming records; narrative meanings remain unvalidated proposals.','missing'));
   a.append(details('Population source exclusions and reuse',{excluded:r.excluded_source_counts,processing:r.processing_counts}));
   if(r.prediction_readiness){const q=r.prediction_readiness;a.append(N('h3','Can these observations support prediction?'),N('p',q.timing_eligible+' of '+q.patients_assessed+' patients have baseline evidence, a timely index and calendar-mature recorded targets.'));for(const gap of q.data_gaps)a.append(N('p',gap,'missing'));a.append(details('Timing, target classes and remaining training requirements',q))}
   for(const p of r.patients){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id),N('p',p.cutoff?'Evidence window: '+p.window_start+' → '+p.cutoff:'No resolved index: baseline feature values remain unavailable'));
    if(p.cutoff)box.append(N('p',(p.record_accounting.included||0)+' included source records; later and otherwise ineligible inputs remain excluded.','meta'));
    const b=N('button','Inspect baseline evidence','chip');b.onclick=async()=>{b.disabled=true;try{const row=await semanticFetch('/api/stack/baseline?id='+encodeURIComponent(p.id));box.append(N('h3','Candidate values and their status'),details('Feature values',row.features));for(const [concept,clocks] of Object.entries(row.measurements)){const state=clocks.explicit_clinical_date;box.append(N('p',concept+': '+state.status.replaceAll('_',' '),'meta'))}for(const e of (row.narrative.events||[]).slice(0,3))box.append(N('blockquote',e.source.quote),N('p',e.source.record_id+' · '+e.authority,'meta'));box.append(details('Input sources, excluded records, medication supply, narrative proposals and arrival blocks',row))}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};box.append(b);a.append(box)}
   addPredictionReadiness(a,'index_day',r.identity);
   a.append(details('Registered baseline profile and complete receipt',r));$('results').append(a);continue;
  }
  if(n.operation==='protocol_change'){
   const a=N('article');a.append(N('h2','What changed between rule versions?'),N('p',r.parameters.before+' → '+r.parameters.after),N('span',r.patients_evaluated.toLocaleString()+' synthetic patients compared','pill'),N('p',r.limits.join(' '),'missing'));
   for(const [key,value] of Object.entries(r.change_counts))a.append(N('span',value.toLocaleString()+' '+key.replaceAll('_',' '),'pill'));
   for(const row of r.status_transitions)a.append(N('p',row.patients.toLocaleString()+' patients: '+row.before.replaceAll('_',' ')+' → '+row.after.replaceAll('_',' ')));
   a.append(N('p','Changed definition fields: '+r.definition_fields_changed.join(', '),'meta'));
   for(const p of r.patients){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id),N('p',p.before.status.replaceAll('_',' ')+' → '+p.after.status.replaceAll('_',' ')));const b=N('button','Inspect both versions','chip');b.onclick=async()=>{b.disabled=true;try{const row=await semanticFetch('/api/stack/protocol-change?id='+encodeURIComponent(r.id)+'&patient='+encodeURIComponent(p.patient_id));for(const side of ['before','after']){const receipt=row.protocol_receipts[side];box.append(N('h3',side+' · version '+receipt.protocol.version),N('p',receipt.protocol.purpose));for(const v of Object.values(receipt.bindings)){box.append(N('p',v.label+' · '+v.status));renderProtocolRequirement(box,v)}}box.append(details('Both rules and exact patient evidence',row))}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};box.append(b);a.append(box)}
   a.append(details('Complete-population version comparison',r));$('results').append(a);continue;
  }
  if(n.operation==='protocol_review'){
   const a=N('article');a.append(N('h2',r.protocol.title),N('p',r.protocol.purpose),N('span',r.patients_evaluated.toLocaleString()+' synthetic patients assessed','pill'),N('span','No paid model calls','pill'));
   const names={review_criterion_supported:'criterion supported',source_interpretation_required:'source review needed',conflicting_evidence:'conflicting evidence',insufficient_evidence:'insufficient evidence',criterion_not_supported:'criterion not supported'};
   for(const [status,count] of Object.entries(r.status_counts))a.append(N('span',count.toLocaleString()+' '+names[status],'pill'));
   renderInputReuse(a,r);
   a.append(N('p',r.protocol.boundary,'missing'));
   for(const p of r.patients){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id+' · '+names[p.status]));for(const action of p.proposed_actions)box.append(N('p',action.text));
    if(p.missing_inputs.length)box.append(N('p','Unresolved: '+p.missing_inputs.map(x=>x.label).join('; '),'meta'));
    const b=N('button','Inspect protocol evidence','chip');b.onclick=async()=>{b.disabled=true;try{const row=await semanticFetch('/api/stack/protocol-review?id='+encodeURIComponent(p.id));for(const v of Object.values(row.bindings)){box.append(N('h3',v.label),N('p',v.status.replaceAll('_',' ')+' · '+(v.value===null?'unresolved':v.value?'supported':'not supported')));for(const ob of v.selected_observations||[])for(const ev of ob.evidence)box.append(N('blockquote',ev.quote||String(ev.value)+' '+(ev.raw_unit||'')),N('p',ev.record_id+' · '+ob.authority.replaceAll('_',' '),'meta'));for(const s of v.state?.selected_assertions||[])box.append(N('blockquote',s.evidence.quote||JSON.stringify(s.evidence.value)));renderProtocolRequirement(box,v)}for(const support of row.supporting_documentary_evidence||[]){const section=N('div',undefined,'source');section.append(N('h3',support.title),N('p',support.status+' · '+support.next_step),N('p','Documentary context only. This does not satisfy the protocol’s clinical trigger, current-regimen or tolerance requirements.','missing'));const inspect=N('button','Inspect linked medication/source account','chip');inspect.onclick=async()=>{inspect.disabled=true;try{const evidence=await semanticFetch('/api/stack/requirements?id='+support.receipt_id);for(const v of Object.values(evidence.bindings))renderRequirementBinding(section,v);renderResolution(section,await semanticFetch('/api/stack/evidence-resolution?id='+support.receipt_id))}catch(e){section.append(N('p',String(e),'warn'))}finally{inspect.disabled=false}};section.append(inspect);box.append(section)}box.append(details('Exact inputs, exclusions, rule version and remaining clinical requirements',row))}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};box.append(b);a.append(box)}
   a.append(details('Full-population protocol receipt and reuse',r));$('results').append(a);continue;
  }
  if(n.operation==='requirements'){
   const a=N('article');a.append(N('h2',r.contract.title),N('p',r.contract.purpose),N('span',r.patients_evaluated.toLocaleString()+' synthetic patients assessed','pill'));
   for(const key of ['supported','contradicted','unresolved'])a.append(N('span',(r.status_counts[key]||0).toLocaleString()+' '+key,'pill'));
   a.append(N('p','These results evaluate explicit documentary conditions. Current clinical state, treatment and program eligibility remain separate.','missing'));
   if(r.acquisition_priority.length){a.append(N('h3','What evidence could resolve an answer?'));for(const p of r.acquisition_priority)a.append(N('p',p.label+': '+p.patients_with_unresolved_binding.toLocaleString()+' unresolved; '+p.decisions_resolvable_by_this_binding_alone.toLocaleString()+' answers could resolve from this input alone.','meta'))}
   for(const p of r.patients){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id+' · '+p.status),N('p',p.next_step));const b=N('button','Inspect requirement evidence','chip');b.onclick=async()=>{b.disabled=true;try{const row=await semanticFetch('/api/stack/requirements?id='+p.id);for(const v of Object.values(row.bindings))renderRequirementBinding(box,v);renderResolution(box,await semanticFetch('/api/stack/evidence-resolution?id='+p.id));box.append(details('Exact sources, rule version and conditional evidence impact',row))}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};box.append(b);a.append(box)}
   a.append(details('Contract logic, full membership and processing reuse',r));$('results').append(a);continue;
  }
  if(n.operation==='source_state'||n.operation==='pre_event_state'){
   const a=N('article');a.append(N('h2','What does the evidence establish?'),N('p',r.parameters.concept+' · '+r.parameters.relation.replaceAll('_',' ')+' · '+r.parameters.clock.replaceAll('_',' ')),N('span',r.patients_evaluated.toLocaleString()+' synthetic patients assessed','pill'),N('span',r.counts.matched.toLocaleString()+(r.parameters.filter==='all'?' retained across all states':' match this filter'),'pill'),N('p','A report, an order and completed care mean different things. This result selects dated documentary accounts; it does not establish current clinical state.','missing'));
   if(n.operation==='pre_event_state')a.append(N('h3','Evidence received before the event'),N('p',r.anchor_contract.title+' · first index '+r.anchor_parameters.index_from+'–'+r.anchor_parameters.index_through+' · received by '+r.anchor_parameters.available_by),N('p','Both report and receipt must precede the event day. Same-day ordering and missing clocks remain unresolved. Retrospective reconstruction; no historical system knowledge, cause or risk is established.','missing'),details('Late evidence and unresolved timing',r.temporal_exclusion_counts));
   if(r.care_account_counts&&Object.keys(r.care_account_counts).length)a.append(N('h3','Follow each care request'),N('p','Request counts are separate from patient counts. A reported service may be a limited assessment; other requested care can remain unresolved.','missing'),details('Linked request evidence',{requests:r.care_account_counts,reports:r.care_report_counts}));
   for(const [status,count] of Object.entries(r.status_counts))a.append(N('span',count.toLocaleString()+' '+status.replaceAll('_',' '),'pill'));
   a.append(details('All status counts and computation reuse',{statuses:r.status_counts,processing:r.processing_counts,interpretation:r.interpretation_processing_counts}));
   for(const p of r.patients){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id+' · '+p.membership.replaceAll('_',' ')),N('p',p.status.replaceAll('_',' ')+(p.value?' · '+p.value:'')+' · '+(p.selected_date||'no selected date')));if(p.temporal_scope)box.append(N('p','Before '+(p.temporal_scope.before_date_exclusive||'unresolved index')+' · '+p.anchor.basis.replaceAll('_',' '),'meta'));const b=N('button','Inspect evidence and unresolved meaning','chip');b.onclick=async()=>{b.disabled=true;try{const row=await semanticFetch((n.operation==='pre_event_state'?'/api/stack/pre-event-state?id=':'/api/stack/source-state?id=')+p.id);for(const s of row.selected_assertions){box.append(N('p',s.authority.replaceAll('_',' ')+' · '+s.relation.replaceAll('_',' ')),N('blockquote',s.evidence.quote||JSON.stringify(s.evidence.value)))}for(const c of row.care_accounts||[]){box.append(N('h3',(c.service_concept||'Request meaning unresolved').replace(/^care /,'')+' · '+c.status.replaceAll('_',' ')),N('p','Request '+c.request_id,'meta'),N('p','Completion of all requested care is unresolved; this is received documentation, not clinical verification.','missing'),details('Request, status, booking and service evidence',c))}for(const s of row.next_evidence_steps)box.append(N('p',s));for(const s of row.interpretation.effective_assertions.filter(x=>row.pending_assertions.some(p=>p.assertion_id===x.id))){box.append(N('blockquote',s.evidence.quote||JSON.stringify(s.evidence.value)),N('p','Unresolved: '+s.relation.replaceAll('_',' '),'meta'))}box.append(details('Exact evidence, exclusions, related records and original interpretation',row))}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};box.append(b);a.append(box)}
   a.append(details('Source-state receipt and limitations',r));$('results').append(a);continue;
  }
  if(n.operation==='interpretation'){
   const a=N('article');a.append(N('h2','Connect meaning across the population'),N('span',r.patients_evaluated.toLocaleString()+' synthetic patients assessed','pill'),N('span',r.counts.matched.toLocaleString()+' match this review-status filter','pill'),N('p','Explicit source reviews can replace an earlier machine interpretation of the same occurrence. Unreviewed proposals stay separate; conflicts and missing evidence remain visible.','missing'));
   for(const [status,count] of Object.entries(r.status_counts))if(status!=='all')a.append(N('p',status.replaceAll('_',' ')+': '+count.toLocaleString()+' patients','meta'));
   for(const p of r.patients){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id),N('p',p.counts.saved_proposals+' saved model readings · '+p.counts.reviewed_source_groups+' source-reviewed occurrences · '+p.counts.remaining_machine_assertions+' baseline assertions still unreviewed','meta'));const b=N('button','Inspect interpreted timeline','chip');b.onclick=async()=>{b.disabled=true;try{const receipt=await semanticFetch('/api/stack/interpretation?id='+p.id);for(const group of receipt.source_groups){box.append(N('h3',group.target.concept+' · '+group.status.replaceAll('_',' ')),N('p','Model differences: '+(group.model_disagreement_axes.join(', ')||'none on these axes'),'meta'));for(const id of group.reading_ids){const read=N('button','Inspect source reading '+id.slice(0,8));read.onclick=async()=>{try{renderSemantic(box,await semanticFetch('/api/stack/semantic?id='+id))}catch(e){box.append(N('p',String(e)))}};box.append(read)}}for(const step of receipt.next_evidence_steps)box.append(N('p',step.detail));box.append(details('Source-bound timeline, superseded assertions and remaining gaps',receipt))}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};box.append(b);a.append(box)}
   a.append(details('Complete membership, processing and cache accounting',r));$('results').append(a);continue;
  }
  if(n.operation==='assertion_review'){
   const a=N('article');a.append(N('h2','Turn records into traceable accounts'),N('span',r.patients_evaluated.toLocaleString()+' synthetic patients','pill'),N('span','Local reconciliation · no paid calls','pill'),N('p',r.counts.structured_assertions.toLocaleString()+' literal structured assertions · '+r.counts.machine_proposals.toLocaleString()+' machine proposals · '+r.counts.same_date_conflict_groups.toLocaleString()+' same-date conflict groups.','meta'),N('p','A later note does not make copied history current. An order or fill does not establish use. Proposals retain their interpretation uncertainty.','missing'));
   for(const [kind,count] of Object.entries(r.patients_needing_evidence_step))a.append(N('p',kind.replaceAll('_',' ')+': '+count.toLocaleString()+' patients','meta'));
   for(const p of r.patients){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id));const b=N('button','Inspect reconciled accounts','chip');b.onclick=async()=>{b.disabled=true;try{const response=await fetch('/api/stack/assertions?id='+encodeURIComponent(p.id));if(!response.ok)throw new Error('Assertion receipt unavailable or stale');renderAssertionReceipt(box,await response.json())}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};box.append(b);a.append(box)}
   a.append(details('Complete population accounting and limits',r));$('results').append(a);continue;
  }
  if(n.operation==='context_passes'){
   const a=N('article');a.append(N('h2','Read the whole chart in bounded passes'),N('span',r.patients_evaluated.toLocaleString()+' synthetic patients','pill'),N('span','Preparation: no paid calls','pill'));
   for(const [key,count] of Object.entries(r.statuses))a.append(N('p',key.replaceAll('_',' ')+': '+count.toLocaleString()+' patients'));
   a.append(N('p',r.counts.packed_units.toLocaleString()+' source units assigned to '+r.counts.passes.toLocaleString()+' passes · '+r.counts.blocked_units.toLocaleString()+' indivisible units exceed the budget.','meta'),N('p','Whole source statements stay intact. Specialist results are proposals; disagreement and missing support remain unresolved.','missing'));
   for(const p of r.patients){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id),N('p',p.counts.passes+' passes · '+p.status.replaceAll('_',' ')),details('Coverage and source gaps',p));
    const inspect=N('button','Inspect chart passes','chip');inspect.onclick=async()=>{inspect.disabled=true;try{const response=await fetch('/api/stack/passes?id='+encodeURIComponent(p.id));if(!response.ok)throw new Error('Pass bundle unavailable or stale');box.append(details('All passes and source accounting',await response.json()))}catch(e){box.append(N('p',String(e),'warn'))}finally{inspect.disabled=false}};box.append(inspect);
    const reconcile=N('button','Reconcile across passes (local)','chip');reconcile.onclick=async()=>{reconcile.disabled=true;try{const response=await fetch('/api/stack/reconcile-passes',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bundle_id:p.id})});const result=await response.json();if(!response.ok)throw new Error(result.error||'Reconciliation failed');renderAssertionReceipt(box,result)}catch(e){box.append(N('p',String(e),'warn'))}finally{reconcile.disabled=false}};box.append(reconcile);
    if(p.review_ready&&p.counts.passes<=64){const review=N('button','Review all '+p.counts.passes+' passes with Perplexity (API)','chip');review.disabled=!live;review.onclick=async()=>{review.disabled=true;const status=N('p','Reviewing bounded passes; validated calls are cached.','meta');box.append(status);try{const response=await fetch('/api/stack/pass-review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({bundle_id:p.id,provider:'perplexity',maximum_passes:p.counts.passes})});const result=await response.json();if(!response.ok)throw new Error(result.error||'Review failed');status.textContent=result.status.replaceAll('_',' ')+' · '+result.new_paid_api_calls+' new API calls · clinical state remains unknown';box.append(details('Source-linked proposals and usage',result))}catch(e){status.textContent=String(e)}finally{review.disabled=!live}};box.append(review)}a.append(box)}
   a.append(details('Complete population pass receipt',r));$('results').append(a);continue;
  }
  if(n.operation==='context'){
   const a=N('article');a.append(N('h2','Context that shows its limits'),N('span',r.patients_evaluated.toLocaleString()+' synthetic patients','pill'),N('span','No paid model calls','pill'),N('p','Exact received evidence, documentary requirements and source chronology. No generated clinical summary.','meta'));
   for(const [key,count] of Object.entries(r.statuses))a.append(N('p',key.replaceAll('_',' ')+': '+count.toLocaleString()+' patients'));
   a.append(N('p',r.counts.selected_units.toLocaleString()+' evidence units included · '+r.counts.omitted_units.toLocaleString()+' omitted by the declared byte budget. Omissions are retained in the audit receipt.','meta'),N('p','Missing annotations are not missing care. Complete source scope is not a complete chart or a validated clinical interpretation.','missing'));
   for(const patient of r.patients){const box=N('div',undefined,'source');box.append(N('h3',patient.patient_id),N('p',patient.status.replaceAll('_',' ')),details('Budget, gaps and dispatch guard',patient));const b=N('button','Inspect exact context and omissions','chip');b.onclick=async()=>{b.disabled=true;try{const response=await fetch('/api/stack/context?id='+encodeURIComponent(patient.id));if(!response.ok)throw new Error('Context unavailable or stale');const receipt=await response.json();box.append(details('Source-bound context receipt',receipt));if(typeof addCaseSynthesis==='function')addCaseSynthesis(box,receipt)}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};box.append(b);a.append(box)}
   a.append(details('Full population context receipt',r));$('results').append(a);continue;
  }
  if(n.operation==='state_review'){renderStateReview(r);continue}
  if(n.operation==='medication_identity'){
   const a=N('article');a.append(N('h2','Medication identity, without a model call'),N('span',r.patients_evaluated.toLocaleString()+' synthetic patients','pill'),N('p','Pinned RxNorm release '+r.terminology_version.version+'. Names are resolved locally; a name or fill does not establish current medication use.','meta'));
   const table=N('table'),head=N('tr');['Medication name','Source representation','Occurrences'].forEach(x=>head.append(N('th',x)));table.append(head);
   for(const v of r.names){const row=N('tr');[v.name,v.kind.replaceAll('_',' '),v.occurrences.toLocaleString()].forEach(x=>row.append(N('td',x)));table.append(row)}a.append(table);
   for(const [key,count] of Object.entries(r.patient_counts))a.append(N('p',key.replaceAll('_',' ')+': '+count.toLocaleString()+' patients','meta'));
   a.append(N('p','Patient groups can overlap. Narrative counts include negated, historical and other-person mentions. Daily dosing does not resolve release formulation or maximum tolerated dose.','missing'));
   for(const patient of r.patients.slice(0,4))a.append(N('h3',patient.patient_id),details('Exact identities, source spans, formulations and dose notation',patient));
   a.append(details('Versioned source, protocol context and complete counts',r));$('results').append(a);continue;
  }
  if(n.operation==='worklist'){renderWorkProposal(r);continue}
  if(n.operation==='analyze'){analytic(r);continue}if(n.operation==='retrieve'){retrieval(r);continue}
  const a=N('article');a.append(N('h2',n.purpose),N('p',n.id+' · '+n.operation+(n.inputs.length?' · from '+n.inputs.join(' + '):''),'meta'));
  if(n.operation==='outcome'){
   a.append(N('span',r.patients_evaluated.toLocaleString()+' synthetic patients','pill'),N('span','No model call','pill'),N('p',r.contract.meaning,'meta'));
   a.append(N('p','Index: first unambiguous observation from '+r.parameters.index_from+' through '+r.parameters.index_through+'. Follow-up: days '+r.contract.min_days+'–'+r.contract.max_days+' on the '+r.contract.basis.replaceAll('_',' ')+' clock. Records received by '+r.parameters.available_by+'.','meta'));
   const table=N('table'),head=N('tr');['Outcome accounting','Patients'].forEach(x=>head.append(N('th',x)));table.append(head);
   for(const [key,count] of Object.entries(r.status_counts)){const row=N('tr');row.append(N('td',key.replaceAll('_',' ')),N('td',count.toLocaleString()));table.append(row)}a.append(table);
   a.append(N('p','Calendar-mature windows: '+r.outcome_counts.calendar_mature.toLocaleString()+'. Provisional observation links: '+r.outcome_counts.provisional_observation_links.toLocaleString()+'. All evaluated patients have a saved evidence receipt.','meta'),N('p','No matching received event is not a clinical negative. Calendar maturity does not establish complete capture. These are development definitions, not CMS measures.','missing'));
   for(const patient of r.patients.slice(0,4)){
    const box=N('div',undefined,'source');box.append(N('h3',patient.patient_id),N('p',patient.status.replaceAll('_',' ')+(patient.index?' · index '+patient.index.date+' → window ends '+patient.window.end:'')));
    const b=N('button','Inspect outcome evidence','chip');b.onclick=async()=>{b.disabled=true;try{const response=await fetch('/api/stack/outcomes?id='+encodeURIComponent(patient.id));if(!response.ok)throw Error(await response.text());box.append(details('Index, endpoints, exclusions and exact received records',await response.json()))}catch(e){box.append(N('p',String(e),'warn'))}finally{b.disabled=false}};box.append(b);a.append(box);
   }
   a.append(details('Versioned outcome definition, full counts and execution receipt',r));$('results').append(a);continue;
  }
  if(n.operation==='longitudinal'){
   const s=r.summary;a.append(N('span',(s.patients_with_anchor||0).toLocaleString()+' patients with anchor evidence','pill'),N('span',(s.provisional_paired_episodes||0)+' provisional episode pairs','pill'),N('p','Clock: '+r.parameters.basis.replaceAll('_',' ')+'. Records available by '+r.parameters.available_by+'. '+(s.context_observations||0).toLocaleString()+' surrounding observations; '+(s.anchors_without_selected_date||0)+' anchors without a usable date on this clock.','meta'));
   a.append(N('p',(s.conflicting_dated_accounts||0)+' conflicting documentary accounts · '+(s.repeated_text_families||0)+' repeated text families. Full selected population computed; '+r.patients_returned+' examples returned, first '+Math.min(4,r.patients_returned)+' shown below. All returned examples are in the complete receipt.','meta'));
   for(const p of r.patients.slice(0,4)){
    const box=N('div',undefined,'source');box.append(N('h3',p.patient_id));
    for(const e of p.episodes.episodes.slice(0,3)){const clock={source_event:'source_date',arrival:'arrival_date',clinical:'clinical_date',reference:'reference_date'}[e.date_basis];box.append(N('p',(e.admission[clock]||'Undated')+' admission → '+(e.discharge?.[clock]||'unresolved discharge')+' · '+e.status.replaceAll('_',' ')+(e.observed_span_days!==null?' · '+e.observed_span_days+' days on '+e.date_basis.replaceAll('_',' '):'')))}
    const snippets=p.context.filter(e=>e.kind==='text');for(const e of snippets.slice(0,3)){box.append(N('p',e.concept+' · '+e.assertion+' · '+e.relation.replaceAll('_',' '),'meta'),N('blockquote',e.statement.text),N('p','Source '+e.source.record_id+' · record date '+e.source_date+' · received '+e.arrival_date+' · literal reference '+(e.reference_date||'not resolved'),'meta'))}
    box.append(N('p',p.context_total+' surrounding observations; '+p.undated_total+' cannot be placed on this clock.','meta'),details('Exact sources, documentary accounts and missingness alternatives',p));a.append(box);
   }
   a.append(N('p','These are documentary accounts. Missing evidence can mean missing care, outside care, delayed data or a reader failure; none is assumed to be the cause.','missing'),details('Complete counts, clocks and execution receipt',r));$('results').append(a);continue;
  }
  if(n.operation==='forecast'){
   a.append(N('span',r.patients_evaluated.toLocaleString()+' patients scored locally','pill'),N('span','Research only','pill'),N('p','Forecast window: '+r.as_of+' to '+r.through+'. Recorded target: '+r.target.stream+' / '+r.target.conditions.map(c=>c.field+' '+c.op+' '+c.value).join(', ')+'. '+r.target_meaning,'meta'));
   const t=N('table'),h=N('tr');['Fitted model','Inputs','Held-out AUROC','Held-out Brier'].forEach(x=>h.append(N('th',x)));t.append(h);
   for(const [name,m] of Object.entries(r.models)){const row=N('tr');[name.replaceAll('_',' '),m.feature_count,m.test_calibrated.auroc?.toFixed(3)??'—',m.test_calibrated.brier.toFixed(5)].forEach(x=>row.append(N('td',String(x))));t.append(row)}a.append(t,N('p','These metrics concern a recorded source endpoint in synthetic data. There is no validated clinical risk score or demonstrated predictive benefit from the change-point features.','missing'));
   for(const p of r.patients.slice(0,5)){const box=N('div',undefined,'source');box.append(N('h3',p.patient_id),N('p',Object.entries(p.model_scores).map(([k,v])=>k.replaceAll('_',' ')+': '+(100*v).toFixed(2)+'%').join(' · ')),details('Input values, missingness and exact source IDs',p));a.append(box)}
   a.append(details('Model artifact, full limitations and forecast receipt',r));$('results').append(a);continue;
  }
  if(n.operation==='source_changes'){
   for(const s of r.sources){const box=N('div',undefined,'source'),plot=chart(s.months.map((m,i)=>({date:m+'-01',value:s.record_counts[i],unit:'received records'})));plot.setAttribute('aria-label','Monthly received '+s.source+' records');box.append(N('h3',s.source+' · '+s.blocks.length+' intensity blocks'),plot,N('p','Change boundaries: '+(s.change_indices.map(i=>s.months[i]).join(', ')||'none'),'meta'));a.append(box)}a.append(N('p','Poisson segmentation follows received records. Source batching and missing feeds can produce changes without any change in patient health.','missing'),details('Block calculations, assumptions and source IDs',r));$('results').append(a);continue;
  }
  if(n.operation==='review'){
   a.append(N('p',r.sampling,'meta'),N('p',r.selected_patients.join(', ')+' · selected from '+r.population_patients+' matching patients'));
   for(const review of r.reviews){a.append(N('h3',(review.model_proposal.provider||'Specialist')+' · '+(review.model_proposal.model||'')),N('p',(review.model_proposal.answers||[]).map(x=>'Proposal: '+x.label+' · support IDs: '+x.evidence_ids.join(', ')+' · semantic review pending').join(' ')),details('Typed answer, support and usage',review.model_proposal))}a.append(details('Evidence packets and complete specialist receipts',r));$('results').append(a);continue;
  }
  if(r.counts){a.append(N('span',r.counts.matched.toLocaleString()+' matched','pill'),N('span',r.counts.unknown.toLocaleString()+' unresolved','pill'),N('p',r.counts.evaluated.toLocaleString()+' patients evaluated. Nonmatch means no matching indexed observation; it does not prove absent care.','meta'));const ids=r.examples.matched;if(ids.length)a.append(N('p','Example patients: '+ids.join(', '),'meta'));}
  if(r.comparisons){const scroll=N('div',undefined,'graph-result-table'),table=N('table'),head=N('tr');['Observed group','Endpoint in group','Endpoint in complement','Difference'].forEach(t=>head.append(N('th',t)));table.append(head);
   for(const c of r.comparisons){const row=N('tr');row.append(N('td',c.group_node.replaceAll('_',' ')));for(const rate of c.rates){row.append(N('td',rate.events+' / '+rate.eligible+' ('+(100*rate.observed_rate).toFixed(2)+'%)'))}if(!c.rates.length){row.append(N('td','Insufficient groups'),N('td','—'))}row.append(N('td',c.risk_difference===null?'—':(100*c.risk_difference).toFixed(2)+' percentage points'));table.append(row)}scroll.append(table);a.append(scroll,N('p','Each denominator uses complete eligible membership. Unknown predicates are excluded explicitly. Groups may overlap. Differences describe recorded evidence; they do not explain causes.','missing'));
   a.append(details('Nominal Fisher tests, BH adjustments, beta-binomial intervals and assumptions',r));
  } else a.append(details('Membership, exact source evidence and dependency receipt',r));
  $('results').append(a);
 }
 const limitations=N('article');limitations.append(N('h2','What this analysis establishes'));const list=N('ul');d.limitations.forEach(x=>list.append(N('li',x)));limitations.append(list);$('results').append(limitations);
}
$('compose').onclick=()=>{try{run('/api/stack/compose',{question:scopedQuestion()})}catch(e){$('status').textContent=e.message}};
for(const example of GRAPH_EXAMPLES){const b=N('button',example.title,'chip');if(example.live){b.dataset.live='true';b.disabled=true}if(example.graph.nodes.some(n=>n.operation==='forecast')){b.dataset.requiresForecast='true';b.disabled=true}b.onclick=()=>{$('question').value=example.question;run('/api/stack/execute-plan',example.graph)};$('graph-examples').append(b)}
fetch('/api/stack/meta').then(r=>r.json()).then(d=>{$('compose').disabled=false;document.querySelectorAll('[data-live]').forEach(b=>b.disabled=!d.allow_live);document.querySelectorAll('[data-requires-forecast]').forEach(b=>{b.disabled=!d.population_composition.forecast.available||(b.dataset.live&&!d.allow_live);if(!d.population_composition.forecast.available)b.title='A predictor must be fitted and evaluated for this warehouse before this example can run.'});if(d.model_roles){$('model-policy').textContent='Planning: '+d.model_roles.planning_order.join(' → ')+(d.model_roles.qwen3_4b_active?'. Qwen3-4B local tiers are active.':'. Qwen3-4B local tiers are off on this server.')}}).catch(()=>$('compose').disabled=true);
'''


def snapshot_examples(rows, as_of=None):
    """Bind example cutoffs to this snapshot; preserve the declared start date."""
    if not as_of or as_of=='2026-09-30':return rows
    from .clinical_events import iso
    if not iso(as_of):raise ValueError('Canonical snapshot date required')
    def convert(value):
        if isinstance(value,str):return value.replace('2026-09-30',as_of)
        if isinstance(value,list):return [convert(v) for v in value]
        if isinstance(value,dict):return {k:convert(v) for k,v in value.items()}
        return value
    return convert(rows)


def enhance(html,as_of=None):
    from .evidence_portfolio_workbench import SCRIPT as PORTFOLIO_SCRIPT
    from .program_acquisition_workbench import SCRIPT as ACQUISITION_SCRIPT
    from .measure_workbench import SCRIPT as MEASURE_SCRIPT
    html = html.replace('<div id="status"', '<p><a href="/program-documents">Quoted program / measure specifications ↗</a> · <a href="/measures">Measure composition ↗</a></p><div id="status"', 1)
    html = html.replace('<div id="status"', PANEL + '<div id="status"', 1)
    html = html.replace('function render(d){', "function render(d){if(d.graph)return renderGraph(d);if(d.schema==='program-work/1'&&d.proposal_id)return renderWorkProposal(d);", 1)
    payload = json.dumps(snapshot_examples(protocol_change_examples()+protocol_work_examples()+accountability_examples()+financial_examples()+landmark_examples()+baseline_examples()+protocol_examples()+outcome_examples()+requirement_examples()+source_state_examples()+interpretation_examples()+assertion_examples()+pass_examples()+context_examples()+medication_examples()+state_examples()+work_examples()+longitudinal_examples()+examples()+model_examples(),as_of)).replace('<', '\\u003c')
    from .semantic_reader import AXES
    semantic_axes=json.dumps({k:list(v) for k,v in AXES.items()}).replace('<','\\u003c')
    return html.replace('</script></html>', '\nconst SEMANTIC_AXES='+semantic_axes+';\nconst GRAPH_EXAMPLES=' + payload + ';\n' + ACQUISITION_SCRIPT + MEASURE_SCRIPT + PORTFOLIO_SCRIPT + SCRIPT + '</script></html>')
