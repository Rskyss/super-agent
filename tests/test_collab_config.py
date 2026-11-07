import json
import tempfile
import unittest
from pathlib import Path

from collab.agents import codex_command, cursor_command, agy_command, ask
from collab.config import DEFAULT_MODELS, MODEL_OPTIONS, ConfigError, load_model_options, load_models, save_models


class LoadModelsTest(unittest.TestCase):
    def test_no_project_file_uses_package_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_models(tmp), DEFAULT_MODELS)

    def test_project_file_overrides_only_given_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            collab = Path(tmp) / ".collab"
            collab.mkdir()
            (collab / "配置.json").write_text(json.dumps({"codex": "gpt-x"}), encoding="utf-8")
            models = load_models(tmp)
            self.assertEqual(models["codex"], "gpt-x")
            self.assertEqual(models["cursor"], DEFAULT_MODELS["cursor"])  # 其余保持默认

    def test_save_models_writes_and_validates(self):
        with tempfile.TemporaryDirectory() as tmp:
            collab = Path(tmp) / ".collab"
            collab.mkdir()
            save_models(tmp, {"codex": "gpt-6-astra", "codex_effort": "ultra",
                              "cursor": "claude-opus-5-thinking-high", "agy": "claude-opus-5-5-high"})
            models = load_models(tmp)
            self.assertEqual(models["codex"], "gpt-6-astra")
            self.assertEqual(models["codex_effort"], "ultra")
            self.assertEqual(models["cursor"], "claude-opus-5-thinking-high")
            self.assertEqual(models["agy"], "claude-opus-5-5-high")
            self.assertTrue(all(k in MODEL_OPTIONS for k in ("codex", "codex_effort", "cursor", "agy")))
            with self.assertRaises(ConfigError):
                save_models(tmp, {"codex_effort": "not-an-effort"})
            with self.assertRaises(ConfigError):
                save_models(tmp, {"cursor": "   "})

    def test_load_model_options_reads_local_codex_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".codex").mkdir()
            cache = {
                "models": [
                    {"slug": "gpt-7-pro", "display_name": "GPT-7 Pro", "visibility": "list",
                     "supported_reasoning_levels": [{"effort": "medium"}, {"effort": "ultra"}]},
                    {"slug": "hidden-model", "display_name": "Hidden", "visibility": "hide",
                     "supported_reasoning_levels": [{"effort": "low"}]},
                ]
            }
            (home / ".codex" / "models_cache.json").write_text(json.dumps(cache), encoding="utf-8")
            opts = load_model_options(home)
            self.assertEqual([m["value"] for m in opts["codex"]], ["gpt-7-pro"])
            self.assertEqual(opts["codex"][0]["efforts"], ["medium", "ultra"])
            self.assertEqual([e["value"] for e in opts["codex_effort"]], ["medium", "ultra"])

    def test_invalid_json_gives_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            collab = Path(tmp) / ".collab"
            collab.mkdir()
            (collab / "配置.json").write_text("{坏掉的", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_models(tmp)


class CommandUsesModelsTest(unittest.TestCase):
    def test_builders_use_given_model(self):
        self.assertIn("my-cursor", cursor_command("x", model="my-cursor"))
        self.assertIn("my-codex", codex_command("x", model="my-codex"))
        self.assertIn("my-agy", agy_command("x", model="my-agy"))

    def test_ask_passes_models_through(self):
        seen = []

        def runner(cmd, cwd, timeout):
            seen.append(cmd)
            return 0, ""

        with tempfile.TemporaryDirectory() as tmp:
            ask("codex", "做事", tmp, models={"codex": "gpt-override", "codex_effort": "high"},
                runner=runner)
        self.assertIn("gpt-override", seen[0])
        self.assertIn('model_reasoning_effort="high"', seen[0])


if __name__ == "__main__":
    unittest.main()
