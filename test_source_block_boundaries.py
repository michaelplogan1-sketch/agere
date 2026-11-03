"""Source excerpts retain complete tokens, offsets, negation and full context."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from clinical_core.agere.clinical import typed_document_review as T


class SourceBlockBoundaries(unittest.TestCase):
    def check_coverage(self,text):
        spans=T.blocks(text)
        self.assertEqual(''.join(c for s in spans for c in s['text'] if not c.isspace()),
                         ''.join(c for c in text if not c.isspace()))
        previous=0
        for s in spans:
            a,b=s['start'],s['end']
            self.assertGreaterEqual(a,previous);self.assertGreater(b,a)
            self.assertEqual(text[a:b],s['text']);previous=b
            self.assertFalse(a and text[a-1].isalnum() and text[a].isalnum(),repr(text[max(0,a-15):a+15]))
            self.assertFalse(b<len(text) and text[b-1].isalnum() and text[b].isalnum(),repr(text[b-15:b+15]))
        return spans

    def test_negation_and_medication_tokens_survive_long_source(self):
        text=('Earlier source. '*132)+'Agent A was not withheld. Agent B was withheld because supply was unavailable.'
        spans=self.check_coverage(text)
        self.assertTrue(any('Agent A was not withheld.'in s['text']for s in spans))
        self.assertTrue(any('Agent B was withheld because supply was unavailable.'in s['text']for s in spans))

    def test_line_boundary_keeps_new_entity_statement_together(self):
        text='Historical context: '+('stable '*285)+'\nAgent A: no reason is established. Agent B: patient declined.'
        spans=self.check_coverage(text)
        self.assertTrue(any(s['text'].startswith('Agent A:')for s in spans))

    def test_distinct_entity_statements_are_not_packed_into_one_assertion(self):
        text='Agent A was stopped. Current use of it is not established. Agent B ran out.'
        spans=self.check_coverage(text)
        self.assertEqual([s['text']for s in spans],
                         ['Agent A was stopped.','Current use of it is not established.','Agent B ran out.'])

    def test_clinician_titles_initials_and_decimal_doses_stay_intact(self):
        text='Dr. A. Lee documented 1.25 mg. Ms. Noor reported no actual use.'
        spans=self.check_coverage(text)
        self.assertEqual([s['text']for s in spans],
                         ['Dr. A. Lee documented 1.25 mg.','Ms. Noor reported no actual use.'])
        self.assertEqual([s['text']for s in T.blocks('This concerns patient A. Another account concerns patient B.')],
                         ['This concerns patient A.','Another account concerns patient B.'])

    def test_single_long_token_is_preserved_instead_of_truncated(self):
        text='X'*3500+'\nNext statement.'
        spans=self.check_coverage(text)
        self.assertTrue(any('X'*3500 in s['text']for s in spans))

    def test_unicode_decimal_and_long_unpunctuated_source(self):
        self.check_coverage(('Observed αβγ 1.25mg—not-confirmed 🧪 '*190)+'No actual use established.')

    def test_paragraph_boundaries_and_nonempty_tail_preserved(self):
        text='  First account.\r\nSecond line.\r\n\r\n'+('word '*1100)+'\n\n  No reason established.  '
        spans=self.check_coverage(text)
        self.assertEqual(spans[-1]['text'],'No reason established.')
        self.assertEqual(T.blocks(' \t\r\n '),[])

    def test_unknown_judgments_keep_complete_source_and_exact_excerpt(self):
        text=('Earlier source. '*132)+'Agent A was NOT withheld. Agent B remains unclear.'
        seen=[]
        def judge(subject,questions,evidence,**kwargs):
            self.assertEqual(evidence[0]['text'],text)
            seen.extend(q['instructions']['target_block']for q in questions.values())
            return {'judgments':{k:{'label':'unknown','decided_by':None}for k in questions},'steps':[],'cost_usd':0}
        tools=SimpleNamespace(as_of='2030-05-27',stack=SimpleNamespace(allow_live=False))
        with patch.dict('os.environ',{'AGERE_DOCUMENT_LOCAL_READERS':''}):
            result=T.read(tools,'A',[{'record_id':'source','date':'2030-01-01','text':text}],
                          {'reason':'Is an explicit reason for nonuse established?'},None,judge=judge,ranking=False)
        self.assertTrue(seen);self.assertTrue(all(s in text for s in seen))
        self.assertEqual(result['findings']['reason'],[])
        self.assertEqual(len(result['unresolved']),len(T.blocks(text)))


if __name__=='__main__':unittest.main()
