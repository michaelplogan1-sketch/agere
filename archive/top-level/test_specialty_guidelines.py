"""Versioned guideline candidates through the existing offline cited retriever."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import healthcare_learning as L,vbc_knowledge as K
from tools.training import education_library as E

ROOT=Path(__file__).resolve().parent


def cards():
    return json.loads((ROOT/'config/agere/learning/specialty-guidelines.json').read_text())['cards']


def change(card,**fields):
    updated=copy.deepcopy(card);updated['education']['guideline'].update(fields)
    updated['facts']['guideline_provenance']=copy.deepcopy(updated['education']['guideline'])
    return updated


class SpecialtyGuidelines(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.path=Path(temp.name)
        for name in ('socket.socket.connect','socket.create_connection','urllib.request.urlopen','subprocess.Popen'):
            p=patch(name,side_effect=AssertionError('Guideline checks are offline'));p.start();self.addCleanup(p.stop)
        p=patch.dict('os.environ',{'AGERE_KNOWLEDGE_ENCODER':'off','AGERE_KNOWLEDGE_SYNTHESIS':'off'});p.start();self.addCleanup(p.stop)

    def build(self,entries,name):
        manifest=self.path/(name+'.json');manifest.write_text(json.dumps({'schema':E.VERSION,'cards':entries}))
        directory=self.path/name;receipt=E.build(manifest,directory)
        return directory,receipt

    def test_combined_library_preserves_actual_guideline_provenance_in_retrieved_facts(self):
        old=[c for name in ('academic-reading-cards.json','academic-grand-rounds.json') for c in json.loads((ROOT/'config/agere/learning'/name).read_text())['cards']]
        default_digest=K.corpus_digest();directory,receipt=self.build(old+cards(),'all')
        self.assertEqual(receipt['entries'],68);self.assertEqual(receipt['guideline_entries'],22)
        self.assertEqual(L.catalog(directory),{})
        with patch.object(L,'context',side_effect=AssertionError('No legacy store')):
            for card in cards():
                result=K.answer(card['questions'][0],directory=directory)
                hit=next((h for h in result['hits'] if h['id']==card['id'] and h['relevant']),None)
                self.assertIsNotNone(hit,card['id'])
                self.assertEqual(hit['facts']['guideline_provenance'],card['education']['guideline'])
                self.assertTrue(result['sources']);self.assertEqual(result['new_paid_api_calls'],0)
        self.assertEqual(K.corpus_digest(),default_digest)
        self.assertFalse(receipt['model_weights_trained_on_cards'])

    def test_grade_systems_strength_and_certainty_cannot_be_interchanged(self):
        reviewed=[c for c in cards() if c['education']['guideline']['recommendations']]
        self.assertEqual(sum(len(c['education']['guideline']['recommendations']) for c in reviewed),32)
        for card in reviewed:
            updated=copy.deepcopy(card);updated['education']['guideline']['recommendations'][0]['grade']['scheme']='universal_grade'
            updated['facts']['guideline_provenance']=copy.deepcopy(updated['education']['guideline'])
            with self.assertRaisesRegex(ValueError,'original grading scheme'):E.validate([updated])
        pa=next(c for c in cards() if c['id']=='guideline-endocrine-aldosteronism-2025')
        grade=pa['education']['guideline']['recommendations'][0]['grade']
        self.assertEqual(grade,{'scheme':'GRADE','strength':'conditional','evidence':'low'})

    def test_draft_final_consensus_and_review_depth_remain_distinct(self):
        entries=cards();drafts=[c for c in entries if c['education']['guideline']['publication_status']=='draft']
        self.assertEqual(len(drafts),2)
        self.assertTrue(any(c['education']['guideline']['document_kind']=='consensus' for c in entries))
        reviewed=next(c for c in entries if c['education']['guideline']['recommendations'])
        for updated in (change(reviewed,publication_status='draft'),change(reviewed,review_depth='publisher_metadata'),change(reviewed,document_kind='guideline_or_consensus')):
            with self.assertRaises(ValueError):E.validate([updated])
        kidney=next(c for c in entries if c['id']=='guideline-kdigo-diabetes-ckd-2022')
        self.assertEqual(kidney['education']['guideline']['publication_status'],'final')
        self.assertIn('current published',kidney['summary'])

    def test_unread_corrections_cannot_be_reported_as_resolved(self):
        hbp=next(c for c in cards() if c['id']=='guideline-aha-acc-hypertension-2025')
        guide=hbp['education']['guideline'];self.assertEqual(len(guide['corrections']),3)
        self.assertEqual(guide['correction_status'],'contents_reviewed')
        self.assertTrue(all(c['review']=='content_reviewed' for c in guide['corrections']))
        pending=copy.deepcopy(hbp)
        for correction in pending['education']['guideline']['corrections']:correction['review']='metadata_only'
        pending=change(pending,correction_status='identified_content_pending')
        for status in ('contents_reviewed','not_assessed'):
            with self.assertRaisesRegex(ValueError,'correction'):E.validate([change(pending,correction_status=status)])
        altered=copy.deepcopy(hbp);altered['facts']['guideline_provenance']['correction_status']='identified_content_pending'
        with self.assertRaisesRegex(ValueError,'Retrieved facts'):E.validate([altered])

    def test_edition_or_correction_evidence_change_invalidates_corpus_identity(self):
        hbp=next(c for c in cards() if c['id']=='guideline-aha-acc-hypertension-2025')
        directory,_=self.build([hbp],'edition');before=K.corpus_digest(directory)
        first=K.answer(hbp['questions'][0],directory=directory)
        updated=change(hbp,edition='Synthetic changed-edition fixture')
        data=directory/'academic-reading.jsonl';data.write_text(json.dumps(updated)+'\n')
        self.assertNotEqual(before,K.corpus_digest(directory))
        second=K.answer(updated['questions'][0],directory=directory)
        self.assertNotEqual(first['corpus']['digest'],second['corpus']['digest'])
        self.assertEqual(second['hits'][0]['facts']['guideline_provenance']['edition'],'Synthetic changed-edition fixture')
        before=K.corpus_digest(directory)
        updated['education']['guideline']['corrections'][0]['doi']='synthetic-correction-evidence-change'
        updated['facts']['guideline_provenance']=copy.deepcopy(updated['education']['guideline'])
        data.write_text(json.dumps(updated)+'\n');self.assertNotEqual(before,K.corpus_digest(directory))

    def test_applicability_jurisdiction_and_primary_source_are_required(self):
        card=cards()[0]
        for updated in (change(card,population=''),change(card,jurisdiction=''),change(card,source_url='https://heart.org.attacker.example/guideline'),change(card,checked_on='2026-10-28'),change(card,operational_rule_approved=True)):
            with self.assertRaises(ValueError):E.validate([updated])

    def test_reference_guidelines_cannot_enter_the_licensed_training_export(self):
        academic=json.loads((ROOT/'config/agere/learning/academic-reading-cards.json').read_text())['cards']
        exported=E.training_material(academic+cards())
        self.assertEqual(len(exported['eligible']),10)
        self.assertTrue(all(r['license']=='CC-BY-4.0' and not r['clinical_gold'] and r['citations'] for r in exported['eligible']))
        self.assertTrue(all(any(r['id']==card['id'] for r in exported['excluded']) for card in cards()))
        self.assertFalse(exported['trained'])
        card=cards()[0];reuse={**card['education']['guideline']['training_reuse'],'eligible':True}
        with self.assertRaisesRegex(ValueError,'training permission'):E.validate([change(card,training_reuse=reuse)])


if __name__=='__main__':unittest.main()
