import json
import tempfile
import unittest
from pathlib import Path

from collab.agents import codex_command, cursor_command, agy_command, ask
from collab.config import DEFAULT_MODELS, ConfigError, load_models


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
