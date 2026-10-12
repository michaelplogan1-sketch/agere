"""Protocol-to-work scope and literal medication context regressions."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import population_protocol_review as p, question_decision as q
from test_medication_accounts import chain, by_id, ASOF

PROGRAM = 'protocol:reduced_ef_therapy_review@2'


class PopulationProtocolReview(unittest.TestCase):
    def assessment(self):
        return {'id':'assessment', 'review_candidate':True,
            'program':{'id':PROGRAM}, 'protocol_context':{'protocol_hash':p.SCOPES[PROGRAM]['protocol_hash']},
            'evidence':[{'record_id':'source', 'source_table':'notes'}]}

    def context(self, rows):
        return p.medication_context(p.medications.project('P-1', rows, ASOF))

    def test_protocol_intent_connects_even_when_source_table_has_no_discipline(self):
        topics=q.population_topics(self.assessment())
        self.assertEqual([t['need_tag'] for t in topics],['medication_review'])
        self.assertEqual(topics[0]['authority'],'documentary_protocol_review_only')

    def test_no_nomination_without_candidate_evidence_and_exact_version(self):
        for key,value in [('review_candidate',False),('evidence',[]),('program',{'id':PROGRAM+'new'}),('protocol_context',{'protocol_hash':'changed'})]:
            a=self.assessment();a[key]=value;self.assertEqual(q.population_topics(a),[])

    def test_existing_source_and_program_mappings_unchanged(self):
        a=self.assessment();a['program']={'id':'respiratory_source_review'}
        self.assertEqual([t['need_tag'] for t in q.population_topics(a)],['symptom_support'])
        self.assertIsNone(p.current_dependency('unused','respiratory_source_review'))
        self.assertEqual(q.identity(),'4dab10a6b7d7db6e72bcc5b7b02babc14a30495f697b7173e4e5ddd03374147f')

    def test_reported_nonuse_and_absence_do_not_become_treatment_gap(self):
        rows=chain();by_id(rows,'p')['use_statement']='does_not_recognize_as_current'
        c=self.context(rows);item=c['items'][0]
        self.assertEqual(item['values']['reported']['use_statement'],'does_not_recognize_as_current')
        self.assertFalse(item['current_regimen_established']);self.assertFalse(item['ingestion_established'])
        self.assertEqual(item['maximum_tolerated_dose'],'not_established')
        self.assertEqual(self.context([])['items'],[])

    def test_displayed_fields_retain_exact_hashes_paths_and_clocks(self):
        rows=chain();item=self.context(rows)['items'][0];sources={r['record_id']:r for r in rows}
        for ref in item['evidence']:
            self.assertEqual(p.medications.value_at(sources[ref['record_id']],ref['field_path']),ref['value'])
            self.assertEqual(p.digest(sources[ref['record_id']]),ref['record_hash'])
        self.assertTrue(item['copy_lineage'])
        self.assertTrue(all('arrival_date' in r and 'source_date' in r for r in item['source_records']))

    def test_conflicts_and_undated_accounts_remain_visible(self):
        rows=chain();other=copy.deepcopy(by_id(rows,'r'));other['record_id']='r2'
        other['entries'][0]['disposition']='current_regimen_not_verified';rows.append(other)
        c=self.context(rows);self.assertEqual(len(c['items']),2)
        self.assertEqual({i['state'] for i in c['items']},{'conflicting'})
        by_id(rows,'r')['event_date']=None;c=self.context(rows)
        self.assertTrue(all(i['state']=='undated' and i['selected_source_date'] is None for i in c['items']))

    def test_broken_source_chain_remains_unresolved(self):
        rows=chain();rows.remove(by_id(rows,'d'));item=self.context(rows)['items'][0]
        self.assertEqual(item['state'],'source_link_unresolved');self.assertTrue(item['link_issues'])

    def test_changed_medication_dependency_blocks_population_actions(self):
        row={'protocol_review_dependency':{'old':True},'request':{'program':PROGRAM},'selection':{'patient_ids':['P-1']}}
        with patch.object(p,'current_dependency',return_value={'new':True}):
            with self.assertRaisesRegex(q.care.work.WorkConflict,'sources or mapping changed'):
                q.inspect_population(SimpleNamespace(path='unused'),row)

    def test_dependency_changes_only_for_selected_members_including_new_accounts(self):
        snap={'patient_hashes':{'P-1':'a','P-2':'b'},'code_identity':'code','terminology_hash':'terms'}
        before=p.dependency(PROGRAM,snap,['P-1','P-3'])
        snap['patient_hashes']['P-2']='changed outside scope'
        self.assertEqual(before,p.dependency(PROGRAM,snap,['P-1','P-3']))
        snap['patient_hashes']['P-3']='newly received in scope'
        self.assertNotEqual(before,p.dependency(PROGRAM,snap,['P-1','P-3']))


if __name__=='__main__':unittest.main()
