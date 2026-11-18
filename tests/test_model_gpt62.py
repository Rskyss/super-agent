"""11-18 用户要求：团队里能选 GPT-6.2-Sol（Codex 本机模型清单里已有，档位 low…ultra）。只加选项，不改默认。"""
import unittest

from collab.config import DEFAULT_MODELS, MODEL_OPTIONS


class Gpt62Sol(unittest.TestCase):
    def test_option_listed_with_all_efforts(self):
        opt = next((o for o in MODEL_OPTIONS["codex"] if o["value"] == "gpt-6.2-sol"), None)
        self.assertIsNotNone(opt, "团队里能选 GPT-6.2-Sol")
        self.assertEqual(opt["label"], "GPT-6.2-Sol")
        self.assertEqual(opt["efforts"], ["low", "medium", "high", "xhigh", "max", "ultra"])

    def test_default_unchanged(self):
        self.assertEqual(DEFAULT_MODELS["codex"], "gpt-6.1-sol", "只加选项，默认由用户自己选")


if __name__ == "__main__":
    unittest.main()
