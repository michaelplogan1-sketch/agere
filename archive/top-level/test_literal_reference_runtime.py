import re,unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import test_synthesis_availability as fixture
from clinical_core.agere.clinical import literal_reference_runtime as cache,synthesis_availability as availability

class LiteralPatterns(unittest.TestCase):
    def test_regex_semantics_match_original_across_identifiers_and_flags(self):
        memo=cache.RegexCache()
        for key in ['a','a-b','a.b','a(b)','a+b','a[b]','r_1','é','空','a|b','\\name']:
            pattern=r'(?<![\w-])'+re.escape(key)+r'(?![\w-])'
            for text in [key,' '+key+' ',key+'-other','before'+key,'é'+key,'('+key+')','other',key.upper()]:
                for flags in (0,re.IGNORECASE,re.ASCII):
                    expected=re.search(pattern,text,flags);actual=memo.search(pattern,text,flags)
                    self.assertEqual(actual.span() if actual else None,expected.span() if expected else None)
        self.assertGreater(memo.status()['counters']['hits'],0)
    def test_new_text_always_searched_no_result_cache(self):
        memo=cache.RegexCache();self.assertIsNotNone(memo.search('source','source'));self.assertIsNone(memo.search('source','changed'))
        self.assertEqual(memo.status()['counters'],{'misses':1,'hits':1})
    def test_bounded_eviction_and_error_semantics(self):
        memo=cache.RegexCache(maximum=2)
        for key in ('a','b','c','a'):memo.search(key,key)
        self.assertEqual(memo.status()['entries'],2);self.assertEqual(memo.status()['counters']['evictions'],2)
        with self.assertRaises(re.error):memo.search('(','x')
        with self.assertRaises(TypeError):memo.search('x',b'x')
    def test_thread_safe_compilation_and_scoped_install(self):
        standard_search=re.search;original=availability.re;self.addCleanup(setattr,availability,'re',original);memo=cache.install();self.assertIs(cache.install(),memo);self.assertIs(re.search,standard_search)
        with ThreadPoolExecutor(4) as pool:results=list(pool.map(lambda text:bool(memo.search('literal',text)),['literal','no']*100))
        self.assertEqual(results,[True,False]*100);self.assertEqual(memo.counts['misses'],1)
    def test_token_rejection_matches_regex_for_unicode_and_overlapping_ids(self):
        import random
        rng=random.Random(81);memo=cache.RegexCache();alphabet='abé空012_- .:()\n'
        for _ in range(1000):
            key=''.join(rng.choice(alphabet) for _ in range(rng.randint(1,12)))
            text=''.join(rng.choice(alphabet) for _ in range(80))+' '+key+' other-'+key
            pattern=r'(?<![\w-])'+re.escape(key)+r'(?![\w-])'
            memo.search(pattern,'unrelated-only')
            expected=re.search(pattern,text);actual=memo.search(pattern,text)
            self.assertEqual(actual.span() if actual else None,expected.span() if expected else None)
        self.assertGreater(memo.status()['counters'].get('exact_token_rejections',0),0)
        self.assertLessEqual(memo.status()['tokenized_text_entries'],32)
    def test_exact_request_bodies_and_identities_unchanged(self):
        f=fixture.Availability();f.setUp()
        try:
            before=f.packet();identity=availability.identity()
            with patch.object(availability,'re',cache.RegexCache()):self.assertEqual(f.packet(),before);self.assertEqual(availability.identity(),identity)
        finally:f.doCleanups()
if __name__=='__main__':unittest.main()
