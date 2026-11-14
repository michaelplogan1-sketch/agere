"""Primary reading provenance and actual existing-retriever integration, offline."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clinical_core.agere.clinical import healthcare_learning as L,vbc_knowledge as K
from tools.training import education_library as E

ROOT=Path(__file__).resolve().parent


def reading_cards():
    return [card for name in ('academic-reading-cards.json','academic-grand-rounds.json')
            for card in json.loads((ROOT/'config/agere/learning'/name).read_text())['cards']]


class EducationLibrary(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.path=Path(temp.name)
        for name in ('socket.socket.connect','socket.create_connection','urllib.request.urlopen','subprocess.Popen'):
            p=patch(name,side_effect=AssertionError('Reading library checks are offline'));p.start();self.addCleanup(p.stop)

    def test_all_readings_retrieve_through_existing_library_without_rule_or_default_activation(self):
        cards=reading_cards();self.assertEqual(len(cards),46)
        manifest=self.path/'manifest.json';manifest.write_text(json.dumps({'schema':E.VERSION,'cards':cards}))
        default_digest=K.corpus_digest();out=self.path/'library'
        receipt=E.build(manifest,out)
        self.assertEqual(receipt['entries'],46);self.assertEqual(L.catalog(out),{})
        # Explicit directory, no historical learning/feedback store, no encoder
        # download and no synthesis. Each cited card must actually be found.
        with patch.dict('os.environ',{'AGERE_KNOWLEDGE_ENCODER':'off','AGERE_KNOWLEDGE_SYNTHESIS':'off'}),patch.object(L,'context',side_effect=AssertionError('No legacy learning store')):
            for card in cards:
                result=K.answer(card['questions'][0],directory=out)
                self.assertTrue(result['answered'],card['id'])
                self.assertTrue(any(hit['id']==card['id'] and hit['relevant'] for hit in result['hits']),card['id'])
                self.assertEqual(result['new_paid_api_calls'],0)
                self.assertTrue(result['sources'])
        self.assertEqual(K.corpus_digest(),default_digest)
        self.assertFalse(receipt['model_weights_trained_on_cards'])
        with self.assertRaises(FileExistsError):E.build(manifest,out)

    def test_sources_cannot_impersonate_clinical_approval_or_open_reuse(self):
        card=reading_cards()[0]
        for field,value in [('clinical_rule_approved',True),('copied_full_text',True),('video_transcript_ingested',True)]:
            changed=copy.deepcopy(card);changed['education'][field]=value
            with self.assertRaises(ValueError):E.validate([changed])
        changed=copy.deepcopy(card);changed['learning']={'approved':True}
        with self.assertRaisesRegex(ValueError,'operational fact'):E.validate([changed])
        changed=copy.deepcopy(card);changed['citations'][0]['url']='https://www.hbs.edu.attacker.example/reading'
        with self.assertRaisesRegex(ValueError,'primary'):E.validate([changed])
        changed=copy.deepcopy(card);changed['education'].update(use_mode='cc_by_text_summary',license='CC-BY-4.0',license_evidence_url='https://unverified.example/license')
        with self.assertRaisesRegex(ValueError,'License evidence'):E.validate([changed])
        with self.assertRaises(ValueError):E.validate([card,card])
        with self.assertRaises(ValueError):E.validate([])

    def test_lecture_metadata_keeps_access_date_review_and_publisher_evidence_distinct(self):
        lectures=[c for c in reading_cards() if c['education'].get('lecture')]
        self.assertEqual(len(lectures),28)
        card=next(c for c in lectures if c['id']=='lecture-emory-fonarow-gdmt')
        self.assertEqual(card['education']['lecture']['transcript_status'],'button_opened_load_stalled')
        self.assertFalse(card['education']['lecture']['full_lecture_watched'])
        for field,value in [('access_evidence',''),('event_date','2027-01-01'),('content_review','fully_reviewed'),('recording_url','https://unverified.example/video'),('full_lecture_watched',True),('transcript_status','reviewed')]:
            changed=copy.deepcopy(card);changed['education']['lecture'][field]=value
            with self.assertRaises(ValueError):E.validate([changed])
        youtube=next(c for c in lectures if c['id']=='lecture-methodist-kittleson-gdmt')
        changed=copy.deepcopy(youtube);changed['education']['lecture'].pop('publisher_identity_evidence')
        with self.assertRaisesRegex(ValueError,'primary'):E.validate([changed])


if __name__=='__main__':unittest.main()
