import os, sys, unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent / 'tools' / 'bottling'))
from clinical_core.agere.clinical import bottled, nl2sql
import swap_base


def rec(correct_list, sig='s'):
    return {'candidates': [{'ok': True, 'signature': sig if c else 'x%d' % i, 'correct': c, 'sql': 'select 1'} for i, c in enumerate(correct_list)]}


class SwapTests(unittest.TestCase):
    def test_default_base_unchanged_and_swap_resolves(self):
        os.environ.pop('AGERE_BOTTLED_BASE', None)
        self.assertEqual(bottled.base_dir(), nl2sql.MODEL_DIR)
        self.assertEqual(bottled.base_adapter_dir(), bottled.ADAPTER_DIR)
        with patch.dict(os.environ, {'AGERE_BOTTLED_BASE': 'omnisql-7b'}):
            self.assertEqual(bottled.base_dir().name, 'omnisql-7b-4bit')
            self.assertIsNone(bottled.base_adapter_dir())      # a Qwen3 LoRA never rides on another base
        with patch.dict(os.environ, {'AGERE_BOTTLED_BASE': '/some/where'}):
            self.assertEqual(bottled.base_dir(), Path('/some/where'))

    def test_download_rule(self):
        with patch.object(swap_base, 'hub_size_gb', return_value=4.28):
            self.assertFalse(swap_base.fetch('omnisql-7b')['fetched'])
        with patch.object(swap_base, 'hub_size_gb', return_value=1.2), patch('huggingface_hub.snapshot_download') as dl:
            self.assertTrue(swap_base.fetch('omnisql-7b')['fetched']); dl.assert_called_once()

    def test_compare_requires_paired_win_and_precision(self):
        qs = [f'q{i}' for i in range(40)]
        inc = {q: rec([i < 20] * 5) for i, q in enumerate(qs)}
        better = {q: rec([i < 32] * 5) for i, q in enumerate(qs)}
        same = {q: rec([i < 21] * 5) for i, q in enumerate(qs)}
        self.assertTrue(swap_base.compare_records(inc, better)['swap'])
        self.assertFalse(swap_base.compare_records(inc, same)['swap'])
        self.assertEqual(swap_base.compare_records(inc, {})['n'], 0)
        self.assertAlmostEqual(swap_base.mcnemar_p(0, 12), 2 / 4096 * 1, places=3)


if __name__ == '__main__':
    unittest.main()
