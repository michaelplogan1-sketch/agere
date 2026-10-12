import copy,json,re,sqlite3,tempfile,unittest
from pathlib import Path
from clinical_core.agere.clinical import medication_identity as m
from clinical_core.agere.clinical.analysis_graph import validate

def catalog():
    return {'schema':'rxnorm-exact-subset/1','version':{'version':'test'},'retrieved_at':'test',
     'entries':[{'query':name,'status':'resolved','concepts':[{'properties':{'rxcui':str(i),'name':name,'tty':'IN'}}],'lookup':{}}
        for i,name in enumerate(['metoprolol succinate','metoprolol','losartan'])]}

class Identity(unittest.TestCase):
    def test_exact_only_no_salt_strength_or_class_substitution(self):
        index=m.term_index(catalog())
        self.assertEqual(m.concept(' Metoprolol  Succinate ',index)['status'],'resolved')
        for value in ['metoprolol tartrate','metoprolol succinate 50 mg','beta blocker','unknown']:
            self.assertEqual(m.concept(value,index)['status'],'unmatched')
    def test_salt_and_daily_dose_do_not_establish_extended_release(self):
        self.assertEqual(m.formulation('metoprolol succinate',{'dose':'50 mg daily'}),'release_not_documented')
        self.assertEqual(m.beta_name_screen('metoprolol succinate','release_not_documented'),'formulation_unresolved')
        self.assertEqual(m.formulation('metoprolol succinate',{'formulation':'extended release'}),'extended_release_documented')
        self.assertEqual(m.formulation('metoprolol succinate',{'formulation':'ER','release_type':'immediate release'}),'conflicting_release_notation')
    def test_name_match_is_not_use_and_preserves_negative_text(self):
        index=m.term_index(catalog());pattern=re.compile('metoprolol succinate|metoprolol|losartan',re.I)
        record={'patient_id':'p','record_id':'r','source_table':'notes','event_date':'2026-09-01','available_date':'2026-09-02',
                'text':'Mother stopped losartan. Patient denies metoprolol succinate use.'}
        hits=m.inspect_record(record,index,pattern)
        self.assertEqual(len(hits),2)
        for h in hits:
            ref=h['source'];self.assertEqual(record['text'][ref['start']:ref['end']],ref['quote'])
            self.assertFalse(h['actual_use_established']);self.assertEqual(h['assertion'],'not_interpreted_by_name_match')
    def test_hidden_document_text_not_used(self):
        r={'patient_id':'p','record_id':'r','source_table':'documents','original_text':'losartan','extracted_text':'received words'}
        self.assertEqual(m.inspect_record(r,m.term_index(catalog()),re.compile('losartan')),[])
    def test_term_ambiguity_is_retained(self):
        c=catalog();c['entries'][0]['status']='ambiguous';c['entries'][0]['concepts'].append({'properties':{'rxcui':'another'}})
        self.assertEqual(m.concept('metoprolol succinate',m.term_index(c))['status'],'ambiguous')
    def test_population_counts_source_spans_empty_scope_and_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);term=p/'terminology';term.mkdir();c=catalog();(term/'catalog.json').write_text(json.dumps(c))
            (p/'medication-terminology-link.json').write_text(json.dumps({'path':str(term),'catalog_hash':m.digest(c)}))
            (p/'run.json').write_text(json.dumps({'synthetic':True,'source_run':'source','source_digest':'digest','snapshot':{'as_of':'2026-09-30'}}))
            db=sqlite3.connect(p/'stack.sqlite3');db.executescript('CREATE TABLE patients(id TEXT);CREATE TABLE records(id TEXT,patient TEXT,payload TEXT);')
            db.executemany('INSERT INTO patients VALUES (?)',[('p',),('q',)])
            for pid,name in [('p','losartan'),('q','metoprolol succinate')]:
                r={'patient_id':pid,'record_id':pid,'source_table':'medication_history','available_date':'2026-09-01',
                   'event_date':'2026-09-01','synthetic':True,'identity_status':'matched','drug':name,'dose':'50 mg daily','drug_class':'arb' if pid=='p' else 'bb'}
                db.execute('INSERT INTO records VALUES (?,?,?)',(pid,pid,json.dumps(r)))
            db.commit();db.close()
            result=m.execute(p,['p','q']);self.assertEqual(result['patients_evaluated'],2)
            self.assertEqual(result['patient_counts']['recorded_raas_arb'],1)
            self.assertEqual(result['patient_counts']['formulation_unresolved'],1)
            self.assertEqual(m.execute(p,[])['patients_evaluated'],0)
            with self.assertRaises(ValueError):m.execute(p,['missing'])
            c['version']['version']='changed';(term/'catalog.json').write_text(json.dumps(c))
            with self.assertRaisesRegex(ValueError,'identity'):m.attached(p)
    def test_graph_cannot_invent_terminology_parameters(self):
        from clinical_core.agere.clinical.graph_workbench import medication_examples
        graph=medication_examples()[0]['graph'];validate(graph,'2026-09-30')
        graph['nodes'][-1]['parameters']['target_dose']=200
        with self.assertRaises(ValueError):validate(graph,'2026-09-30')

if __name__=='__main__':unittest.main()
