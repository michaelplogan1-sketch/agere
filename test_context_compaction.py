"""Constraint-preserving context compaction: pins survive, protected units are never elided, every demoted unit has a pointer."""
import copy
import json
import unittest

from clinical_core.agere.clinical import context_builder as ctx, context_compaction as CC

META = {'snapshot': {'as_of': '2030-05-27'}}
QUESTION = 'Which of the 2 patients with A1c above 9.5 on or after 2029-03-01 declined statin therapy?'
PARAMS = {'question': QUESTION, 'streams': ['notes'], 'concepts': ['statin'], 'contract': '', 'program_ids': [], 'max_bytes': 60000}
LONG = ' '.join(f'routine follow up item {i} without change.' for i in range(40))


def rec(i, text, day):
    return {'record_id': f'n{i}', 'patient_id': 'p', 'source_table': 'notes', 'event_date': day, 'available_date': day, 'text': text}


def context(n=30, protect=(3, 17)):
    records = []
    for i in range(n):
        text = (f'Patient declined statin after intolerance. {LONG}' if i in protect else LONG)
        records.append(rec(i, text, f'2029-{1 + i % 12:02d}-{1 + i % 27:02d}'))
    units = ctx.units('p', records, [], PARAMS, '2030-05-27')
    return ctx.pack('p', PARAMS, units, META)['model_context']


def counter(text):
    return len(text.encode()) // 4          # a stand-in tokenizer: the module only needs a monotone count


class Compaction(unittest.TestCase):
    def setUp(self):
        self.c = context()
        self.before = counter(CC.encode(self.c))

    def test_fits_unchanged_when_budget_allows(self):
        got = CC.compact(self.c, counter, self.before + 10)
        self.assertFalse(got['compacted']); self.assertTrue(got['fit']); self.assertEqual(got['context'], self.c)

    def test_compacts_to_budget_keeping_pins_protected_units_and_pointers(self):
        budget = self.before // 4
        got = CC.compact(self.c, counter, budget)
        out = got['context']
        self.assertTrue(got['compacted'] and got['fit'] and got['verified'], got['failures'])
        self.assertLessEqual(got['tokens'], budget); self.assertLess(got['tokens'], got['before_tokens'])
        for k in CC.PINNED_KEYS:
            if k in self.c: self.assertEqual(out[k], self.c[k], k)               # pinned fields byte-identical
        self.assertEqual(out['task']['question'], QUESTION)
        for lit in ('2', '9.5', '2029-03-01'): self.assertIn(lit, json.dumps(out['question_requirements']) + QUESTION)
        self.assertTrue(any(r['kind'] == 'comparator' for r in out['question_requirements']))
        protected = {u['id'] for u in self.c['evidence'] if u.get('protected_reasons')}
        kept = {u['id'] for u in out['evidence']}
        self.assertTrue(protected and protected <= kept)                          # protected units are never elided
        elided = set(CC.pointers(out))
        allids = {u['id'] for u in self.c['evidence']}
        self.assertEqual({i[:CC.ID] for i in allids}, {i[:CC.ID] for i in kept} | elided)  # nothing is lost: kept or pointed to
        for u in out['evidence']:
            if u.get('compacted'): self.assertIn('expand', u)
        self.assertTrue(out['compaction']['retrieval_aid_only'])

    def test_demotion_order_keeps_recent_and_requested_whole_longest(self):
        got = CC.compact(self.c, counter, int(self.before * 0.7))
        tiers = got['tiers']
        self.assertGreater(tiers['t1_trimmed'] + tiers['t2_digest'] + tiers['t3_pointer'], 0)
        whole = [u for u in got['context']['evidence'] if not u.get('compacted') and u['kind'] == 'narrative']
        trimmed = [u for u in got['context']['evidence'] if u.get('compacted') and u['kind'] == 'narrative']
        src = self.c['sources']
        wd = min(src[u['source_ref']]['source']['event_date'] for u in whole if not u.get('protected_reasons')) if any(not u.get('protected_reasons') for u in whole) else '9'
        td = max((src[u['source_ref']]['source']['event_date'] for u in trimmed if not u.get('protected_reasons')), default='0')
        self.assertGreaterEqual(wd, td)                                           # whole units are newer than demoted ones

    def test_protected_units_become_reasoned_pointers_only_as_a_last_resort(self):
        got = CC.compact(self.c, counter, 60)
        self.assertFalse(got['fit'])                                              # the pins alone do not fit 60 tokens: say so
        out = got['context']
        protected = {u['id']: u for u in self.c['evidence'] if u.get('protected_reasons')}
        kept = {u['id'] for u in out['evidence']}
        ptrs = CC.pointers(out)
        for uid, u in protected.items():
            self.assertTrue(uid in kept or ptrs[uid[:CC.ID]]['protected_reasons'] == sorted(u['protected_reasons']))   # never silently gone
        # with room for the pins and digests the protected units stay as units, not pointers
        roomy = CC.compact(self.c, counter, self.before // 3)['context']
        self.assertTrue(set(protected) <= {u['id'] for u in roomy['evidence']})

    def test_mostly_protected_context_still_fits_through_reasoned_pointers(self):
        records = [rec(i, f'No change. Patient denies chest pain, not taking aspirin. {LONG}', f'2029-{1 + i % 12:02d}-{1 + i % 27:02d}') for i in range(25)]
        c = ctx.pack('p', PARAMS, ctx.units('p', records, [], PARAMS, '2030-05-27'), META)['model_context']
        self.assertTrue(all(u.get('protected_reasons') for u in c['evidence'] if u['kind'] == 'narrative'))
        full = counter(CC.encode(c))
        got = CC.compact(c, counter, full // 4)
        self.assertTrue(got['fit'] and got['verified'], got['failures'])
        self.assertEqual(CC.verify(c, got['context']), [])
        self.assertTrue(any(e['protected_reasons'] for e in CC.pointers(got['context']).values()) or got['tiers']['t2_digest'])

    def test_verify_catches_each_kind_of_loss(self):
        good = CC.compact(self.c, counter, self.before // 3)['context']
        self.assertEqual(CC.verify(self.c, good), [])
        bad = copy.deepcopy(good); bad['task']['question'] = 'Which patients declined statin therapy?'
        self.assertTrue(any('pinned field task' in f for f in CC.verify(self.c, bad)))
        bad = copy.deepcopy(good); bad['limits'] = 'none'
        self.assertTrue(any('limits' in f for f in CC.verify(self.c, bad)))
        bad = copy.deepcopy(good)
        bad['compaction']['elided']['rows'] = bad['compaction']['elided']['rows'][1:]
        bad['evidence'] = bad['evidence'][1:]
        self.assertTrue(CC.verify(self.c, bad))
        bad = copy.deepcopy(good); prot = next(u for u in bad['evidence'] if u.get('protected_reasons')); bad['evidence'].remove(prot)
        self.assertTrue(any('protected unit' in f or 'neither retained' in f for f in CC.verify(self.c, bad)))
        bad = copy.deepcopy(good); prot = next(u for u in bad['evidence'] if u.get('protected_reasons')); bad['evidence'].remove(prot)
        bad['compaction']['elided']['rows'].append([prot['id'][:CC.ID], prot['source_ref'], None, None, [], []])   # elided with no reasons recorded
        self.assertTrue(any('without its protected reasons' in f for f in CC.verify(self.c, bad)))
        bad = copy.deepcopy(good); ref = next(iter(bad['sources'])); bad['sources'][ref]['source']['record_hash'] = 'tampered'
        self.assertTrue(any('record hash' in f for f in CC.verify(self.c, bad)))

    def test_digest_tier_holds_fields_not_generated_text(self):
        got = CC.compact(self.c, counter, self.before // 5)
        digests = [u for u in got['context']['evidence'] if u.get('compacted') == 't2_digest']
        self.assertTrue(all('text' not in u and u.get('retrieval_aid_only') for u in digests))


class ForModel(unittest.TestCase):
    def test_for_model_heals_overflow_instead_of_refusing(self):
        import sqlite3, tempfile
        from pathlib import Path
        from unittest.mock import patch
        records = [rec(i, (f'Patient declined statin after intolerance. {LONG}' if i in (3, 17) else LONG), f'2029-{1 + i % 12:02d}-{1 + i % 27:02d}') for i in range(14)]
        result = ctx.pack('p', PARAMS, ctx.units('p', records, [], PARAMS, '2030-05-27'), META)
        c = result['model_context']
        tokenizer = type('T', (), {'encode': lambda self, text: list(text.encode())})()
        full = len(tokenizer.encode(CC.encode(c)))
        with tempfile.TemporaryDirectory() as d:
            db = sqlite3.connect(Path(d) / 'stack.sqlite3')
            db.execute('CREATE TABLE records(id TEXT,patient TEXT,payload TEXT)')
            for r in records: db.execute('INSERT INTO records VALUES (?,?,?)', (r['record_id'], 'p', json.dumps(r)))
            db.commit(); db.close()
            with patch.object(ctx, 'inspect', return_value=result):
                out = ctx.for_model(Path(d), 'id', tokenizer, full // 2, 500)
                self.assertEqual(ctx.for_model(Path(d), 'id', tokenizer, full + 1000, 500)['compaction'], None)   # fits: untouched
                with self.assertRaisesRegex(ValueError, 'Native model token budget exceeded'):
                    ctx.for_model(Path(d), 'id', tokenizer, 700, 100)                                              # cannot keep the pins
        self.assertTrue(out['compaction']['verified'])
        self.assertLessEqual(out['context_tokens'] + out['reserved_tokens'], full // 2)
        self.assertEqual(json.loads(out['text'])['task']['question'], QUESTION)


if __name__ == '__main__':
    unittest.main()
