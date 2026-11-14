"""Source/clock/unit/copy boundaries for the existing assertion review path."""
import copy,sqlite3,unittest
from unittest.mock import patch
from clinical_core.agere.clinical import assertion_reconciliation as A,graph_contracts as G
from test_task_context import record

ASOF='2030-05-27'
META={'snapshot':{'as_of':ASOF}}
WINDOW={'basis':'source_event','start':'2029-01-01','end':'2029-12-31'}
P={'streams':['notes'],'concepts':[],'received_annotations':True,'assertion_window':WINDOW}

def source(text,rid='r',day='2029-01-01'):
    return record(text,rid=rid,event_date=day,available_date='2029-12-31')

def fact(r,*,value='pain',unit=None,negated=0,time='current',subject='patient',start=0,end=None):
    end=len(r['text'])if end is None else end
    return {'native_row_id':r['record_id'],'patient_id':'p','record_id':r['record_id'],'stream':'notes',
        'fact_type':'symptom','value':value,'unit':unit,'negated':negated,'temporality':time,
        'event_date':r['event_date'],'evidence_text':r['text'][start:end],'extractor':'unqualified-fixture',
        'confidence':.99,'qualifier':None,'experiencer':subject,'source_field':'text','span_start':start,'span_end':end}

def review(records,facts,p=P):
    return A.patient_receipt('p',records,[],p,META,received_facts=facts)

class AssertionWindowAnnotations(unittest.TestCase):
    def test_explicit_mode_and_clock_contract_preserve_legacy_concept_guard(self):
        with self.assertRaises(ValueError):A.validate({'streams':['notes'],'concepts':[]})
        self.assertEqual(A.validate(P),P)
        for bad in ({**P,'received_annotations':'true'},{**P,'assertion_window':{**WINDOW,'basis':'reference'}},
                    {**P,'assertion_window':{**WINDOW,'start':'2031-01-01'}}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):review([],[],bad)

    def test_exact_span_keeps_values_units_and_unvalidated_authority(self):
        r=source('Walk distance was 40 m.');f=fact(r,value=40,unit='m')
        result=review([r],[f]);a=result['assertions'][0]
        self.assertEqual((a['value'],a['unit']),(40,'m'))
        self.assertEqual(a['received_annotation'],f);self.assertEqual(a['evidence']['quote'],r['text'])
        self.assertEqual(a['authority'],'machine_annotation');self.assertEqual(a['semantic_validation'],'pending')
        self.assertIsNone(a['clocks']['clinical_date']);self.assertFalse(a['current_clinical_state_established'])

    def test_negation_history_other_subject_and_family_section_change_candidate_inclusion(self):
        rows=[];facts=[]
        for rid,text,kw in [('n','No pain.',{'negated':1}),('h','Prior pain.',{'time':'historical'}),
                            ('f','Mother has pain.',{'subject':'family'}),('section','Family history:\nMother has pain.',{})]:
            r=source(text,rid);rows.append(r);facts.append(fact(r,start=text.find('Mother')if rid=='section'else 0,**kw))
        result=review(rows,facts)
        self.assertTrue(all(not a['positive_under_received_qualifiers']for a in result['assertions']))
        self.assertIn('source_subject_unresolved',[e['reason']for g in result['groups']for e in g['excluded_from_dated_accounts']])

    def test_bad_identity_clock_or_span_is_retained_as_unbound_evidence(self):
        r=source('No pain.');base=fact(r,negated=1)
        for key,value in [('patient_id','someone_else'),('event_date','2028-01-01'),('span_end',999),('source_field','missing')]:
            f={**base,key:value};result=review([r],[f])
            self.assertEqual(result['assertions'],[]);u=result['coverage']['unbound_annotation_rows'][0]
            self.assertEqual(u['annotation'],f);self.assertEqual(u['assertion_window_state'],'selected')

    def test_window_selects_source_statement_and_keeps_old_literal_reference_distinct(self):
        text='Prior note (2028-03-04):\nPain was present.';r=source(text)
        result=review([r],[fact(r,start=text.index('Pain'),time='historical')])
        a=result['assertions'][0];self.assertEqual(a['assertion_window_state'],'selected')
        self.assertEqual(a['clocks']['source_date'],'2029-01-01');self.assertEqual(a['clocks']['reference_date'],'2028-03-04')
        self.assertTrue(a['copied_history']);self.assertNotIn('source_statement_date',result['groups'][0]['latest_by_clock'])

    def test_repetition_outside_window_cannot_become_new_current_evidence(self):
        old=source('Pain reported.','old','2028-12-31');new=source('Pain reported.','new','2029-01-01')
        result=review([old,new],[fact(old),fact(new)])
        self.assertEqual(len(result['assertions']),1);a=result['assertions'][0]
        self.assertTrue(a['source_repetition']['cannot_refresh_state'])
        self.assertEqual(a['source_repetition']['anchors'][0]['source']['record_id'],'old')
        self.assertFalse(a['source_repetition']['copying_established']);self.assertFalse(result['groups'][0]['dated_accounts'])
        self.assertEqual(result['coverage']['assertion_window']['assertions_outside_window'],1)

    def test_literal_reference_does_not_become_snapshot_current_by_annotation_label(self):
        r=source('Pain was present [2028-01-02].');a=review([r],[fact(r)])['assertions'][0]
        self.assertEqual(a['temporality'],'current')  # preserve the received proposal
        self.assertEqual(a['clocks']['reference_date'],'2028-01-02')
        self.assertFalse(a['positive_under_received_qualifiers'])
        self.assertIn('reference_date_differs_from_source_statement',a['qualifier_effects'])

    def test_unknown_window_date_is_preserved_without_arrival_substitution(self):
        r=source('Pain reported.',day=None);result=review([r],[fact(r)])
        a=result['assertions'][0];self.assertEqual(a['assertion_window_state'],'unknown')
        self.assertIsNone(a['clocks']['source_date']);self.assertFalse(result['groups'][0]['dated_accounts'])
        self.assertEqual(result['coverage']['assertion_window']['assertions_with_unknown_date'],1)

    def test_value_and_unit_domains_do_not_become_false_polarity_contradictions(self):
        rows=[source('40 metres.','m'),source('40 feet.','f'),source('50 metres.','other')]
        result=review(rows,[fact(rows[0],value=40,unit='m'),fact(rows[1],value=40,unit='ft',negated=1),fact(rows[2],value=50,unit='m')])
        self.assertEqual(len(result['groups']),3);self.assertEqual(result['counts']['same_date_conflict_groups'],0)

    def test_different_entity_qualifiers_cannot_become_opposing_accounts(self):
        a=source('First product was not taken.','a');b=source('Second product was taken.','b')
        fa={**fact(a,negated=1),'qualifier':'first product'}
        fb={**fact(b),'qualifier':'second product'}
        result=review([a,b],[fa,fb]);self.assertEqual(len(result['groups']),2)
        self.assertEqual(result['counts']['same_date_conflict_groups'],0)

    def test_native_schema_and_validator_agree_on_explicit_mode(self):
        catalog={'cohort_fields':{'record_fields':{'notes':{}}},'source_streams':['notes'],
            'evidence_catalog':{'assertion_concepts':['symptom pain']}}
        schema=G.parameter_schema('assertion_review',catalog)
        self.assertEqual(schema['required'],['streams','concepts'])
        self.assertEqual(schema['anyOf'][1]['properties']['received_annotations'],{'const':True})
        self.assertEqual(G.validate_parameters('assertion_review',P),P)
        with self.assertRaises(ValueError):G.validate_parameters('assertion_review',{'streams':['notes'],'concepts':[]})

    def test_full_source_scope_is_partitioned_without_a_thirty_stream_workflow_cap(self):
        streams=['source_'+str(i)for i in range(45)];p={'streams':streams,'concepts':['outside care']}
        captured=[]
        def connect(_):return sqlite3.connect(':memory:'),{'vocabulary':{'concept':['outside care']}}
        def dependency(path,params):
            captured.extend(params['streams']);return str(params['streams']),META,None,None
        with patch.object(A.longitudinal,'connect',side_effect=connect),patch.object(A.context,'dependency_identity',side_effect=dependency)as calls:
            A.dependency('/unused',p)
        self.assertEqual(calls.call_count,2);self.assertEqual(sorted(captured),sorted(streams))

if __name__=='__main__':unittest.main()
