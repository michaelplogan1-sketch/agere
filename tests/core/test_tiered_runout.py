"""Claims runout (IBNR) by chain-ladder: code only, no warehouse, no model."""
import unittest

from clinical_core.agere.tiered import recipes, runout


def claims():
    """Every incurred month 2028-01..2029-06 pays $100 in its month, $50 one month later and $50 two months later."""
    out = []
    for y in (2028, 2029):
        for m in range(1, 13):
            if (y, m) > (2029, 6): break
            for lag, amount in ((0, 100.0), (1, 50.0), (2, 50.0)):
                py, pm = y + (m + lag - 1) // 12, (m + lag - 1) % 12 + 1
                out.append((f'{y}-{m:02d}-10', f'{py}-{pm:02d}-15', amount))
    return out


class ChainLadder(unittest.TestCase):
    def test_complete_months_need_no_factor_and_young_months_are_completed(self):
        got = {r[0]: r for r in runout.chain_ladder(claims(), '2029-06-30', 3)}
        self.assertEqual(list(got), ['2029-04', '2029-05', '2029-06'])
        self.assertEqual(got['2029-04'][1:4], (200.0, 1.0, 200.0))          # fully paid by the valuation date
        self.assertEqual(got['2029-05'][1], 150.0); self.assertAlmostEqual(got['2029-05'][3], 200.0)
        self.assertEqual(got['2029-06'][1], 100.0); self.assertAlmostEqual(got['2029-06'][3], 200.0)
        self.assertAlmostEqual(got['2029-06'][4], 100.0)                     # IBNR = ultimate - paid

    def test_payments_after_the_valuation_date_are_never_used(self):
        late = claims() + [('2029-06-10', '2029-07-20', 10_000.0)]
        self.assertEqual(runout.chain_ladder(late, '2029-06-30', 1), runout.chain_ladder(claims(), '2029-06-30', 1))

    def test_the_question_matches_the_built_in_recipe(self):
        self.assertEqual(recipes.match('What is our IBNR as of 2029-06-30?', db=None), ('claims_runout', {'valuation_date': '2029-06-30', 'months': 6}))
        self.assertEqual(recipes.match('Estimate claims runout for the last 3 months as of 2028-12-31')[1], {'valuation_date': '2028-12-31', 'months': 3})


if __name__ == '__main__':
    unittest.main()
