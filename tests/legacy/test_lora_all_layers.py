import json, tempfile, unittest
from pathlib import Path
import yaml
from tools.training import train_all


class LoraAllLayersTests(unittest.TestCase):
    def test_config_and_command(self):
        c = yaml.safe_load(Path('tools/bottling/lora-v3-all-layers.yaml').read_text())
        self.assertEqual(c['num_layers'], -1)
        self.assertGreater(c['learning_rate'], 1e-4)
        with tempfile.TemporaryDirectory() as d:
            cmd = train_all.lora_command(Path(d) / 'data', Path(d) / 'ad', 'bottled', False, all_layers=True)
            self.assertEqual(cmd[cmd.index('--num-layers') + 1], '-1')
            conf = json.loads(Path(cmd[cmd.index('-c') + 1]).read_text())
            self.assertEqual(conf['lora_parameters']['rank'], 16)
            self.assertIn('mlp.down_proj', conf['lora_parameters']['keys'])
            off = train_all.lora_command(Path(d) / 'data', Path(d) / 'ad2', 'bottled', False, all_layers=False)
            self.assertEqual(off[off.index('--num-layers') + 1], '16'); self.assertNotIn('-c', off)

    def test_lora_covers_every_block_and_the_mlp_on_a_tiny_random_qwen3(self):
        """Dummy model only (never Qwen weights): apply the exact lora_parameters + num_layers=-1 the config sets."""
        from mlx_lm.models import qwen3
        from mlx_lm.tuner.utils import linear_to_lora_layers
        from mlx.utils import tree_flatten
        args = qwen3.ModelArgs(model_type='qwen3', hidden_size=32, num_hidden_layers=4, intermediate_size=64, num_attention_heads=4,
                               rms_norm_eps=1e-6, vocab_size=64, num_key_value_heads=2, max_position_embeddings=128, rope_theta=1e4,
                               head_dim=8, tie_word_embeddings=True)
        model = qwen3.Model(args)
        model.freeze()
        cfg = yaml.safe_load(Path('tools/bottling/lora-v3-all-layers.yaml').read_text())
        linear_to_lora_layers(model, cfg['num_layers'], cfg['lora_parameters'])
        names = [k for k, _ in tree_flatten(model.trainable_parameters())]
        for i in range(4):
            for m in ('self_attn.q_proj', 'self_attn.o_proj', 'mlp.gate_proj', 'mlp.up_proj', 'mlp.down_proj'):
                self.assertTrue(any(f'layers.{i}.{m}.lora_a' in n for n in names), (i, m))
        # the last-16-layers config on the same 4-block model touches fewer blocks only when num_layers < blocks (sanity of the contrast)
        model2 = qwen3.Model(args); model2.freeze()
        linear_to_lora_layers(model2, 2, {'rank': 8, 'scale': 20.0, 'dropout': 0.0})
        n2 = [k for k, _ in tree_flatten(model2.trainable_parameters())]
        self.assertFalse(any('layers.0.' in n for n in n2))


if __name__ == '__main__':
    unittest.main()
