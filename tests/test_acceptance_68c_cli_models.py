"""68c 验收：预置 CLI 按整家接；只运行临时假程序，绝不调用真实 CLI。

运行：python3 -m unittest discover -s tests -p "test_acceptance_68c*"
验收 1：ModelListTest；2：DefaultCheckTest；3、4：ModelTrialTest/ModelStatusTest；
5：CompatibilityTest；6：CliModelsServerTest；7：PageContractTest。
"""
import copy
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from collab import abilities, blocks, blocks_check
from collab.config import MODEL_OPTIONS
from test_acceptance_55_blocks_server import BlocksCase


STATIC = Path(__file__).resolve().parent.parent / "desktop" / "static"
ARGON = "gemini-4-argon-high"
FLASH = "gemini-3.8-flash-high"
AGY_MODELS = [ARGON, FLASH, "gemini-3.8-flash-low", "claude-sonnet-5-5-high"]
AGY_OUTPUT = "Fetching available models...\n" + "\n".join(
    name + "\t显示名" for name in AGY_MODELS) + "\n"


class FakeCli:
    """真实的可执行假程序：名单、执行参数和结果均由本测试控制。

    Popen 兜底检查只放行这个临时 bin，防止路径回退时误跑用户的程序。
    HOME 和 Path.home 同时隔离，Codex 缓存只会读本测试创建的文件。
    """

    def __init__(self, case, base):
        self.case = case
        self.base = Path(base)
        self.bin = self.base / "fake-cli-bin"
        self.bin.mkdir()
        self.home = self.base / "fake-home"
        self.home.mkdir()
        self.log = self.base / "fake-cli.jsonl"
        self.config = self.base / "fake-cli.json"
        self.settings = {"output": AGY_OUTPUT, "list_code": 0,
                         "probe": "ok", "tool": False, "done_file": blocks.DEFAULT_DONE_FILE}
        self.mode = "normal"
        self.save()
        script = "#!" + sys.executable + "\n" + '''import json, sys
from pathlib import Path
config = json.loads(Path(CONFIG_PATH).read_text())
args = sys.argv[1:]
with Path(LOG_PATH).open("a") as log:
    log.write(json.dumps({"command": Path(sys.argv[0]).name, "args": args,
                          "cwd": str(Path.cwd())}) + "\\n")
if args == ["models"]:
    print(config["output"])
    sys.exit(config["list_code"])
model = None
for flag in ("--model", "-m"):
    if flag in args:
        model = args[args.index(flag) + 1]
probe = config.get("replies", {}).get(model, config["probe"])
if probe == "ok":
    if not config["tool"]:
        Path(config["done_file"]).write_text("OK")
    print("OK")
    sys.exit(0)
if probe == "quota":
    print("quota exceeded")
elif probe == "login":
    print("not logged in; please login")
else:
    print("model not supported")
sys.exit(1)
'''.replace("CONFIG_PATH", repr(str(self.config))).replace("LOG_PATH", repr(str(self.log)))
        for name in ("agy", "agent", "codex", "mytool"):
            target = self.bin / name
            target.write_text(script, encoding="utf-8")
            target.chmod(0o755)
        for p in (patch.dict(os.environ, {"HOME": str(self.home), "PATH": str(self.bin)}),
                  patch.object(Path, "home", return_value=self.home)):
            p.start()
            case.addCleanup(p.stop)
        popen = subprocess.Popen
        run = subprocess.run

        def guarded_popen(args, *positional, **kwargs):
            case.assertIsInstance(args, (list, tuple), "CLI 不能经过 shell")
            case.assertFalse(kwargs.get("shell", False))
            executable = Path(args[0])
            case.assertEqual(executable.parent, self.bin, "拒绝启动临时假程序之外的任何程序")
            if list(args[1:]) == ["models"]:
                case.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL, "拿名单要关闭 stdin")
                folder = Path(kwargs.get("cwd") or ".").resolve()
                case.assertTrue(folder.is_dir())
                case.assertNotIn(folder, (Path.cwd().resolve(), self.home, self.base, self.bin))
                if self.mode == "timeout":
                    raise subprocess.TimeoutExpired(args, 60)
                if self.mode == "oserror":
                    raise OSError("fake unavailable")
            return popen(args, *positional, **kwargs)

        def guarded_run(args, *positional, **kwargs):
            if isinstance(args, (list, tuple)) and list(args[1:]) == ["models"]:
                case.assertEqual(kwargs.get("timeout"), 60, "拿名单最多等 60 秒")
            return run(args, *positional, **kwargs)

        for p in (patch.object(subprocess, "Popen", guarded_popen),
                  patch.object(subprocess, "run", guarded_run)):
            p.start()
            case.addCleanup(p.stop)

    def save(self, **changes):
        self.settings.update(changes)
        self.config.write_text(json.dumps(self.settings, ensure_ascii=False), encoding="utf-8")

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def probes(self):
        return [c for c in self.calls() if c["args"] != ["models"]]

    def cache(self, models):
        cache = self.home / ".codex" / "models_cache.json"
        cache.parent.mkdir(exist_ok=True)
        cache.write_text(json.dumps({"models": models}), encoding="utf-8")

    def runner(self, args, folder, timeout, *unused, **kwargs):
        """替换付费执行边界，仍让假程序真的写出检测文件。"""
        done = subprocess.run(args, cwd=folder, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout)
        return done.returncode, done.stdout + done.stderr


class CliCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.root = self.base / "积木库"
        self.fake = FakeCli(self, self.base)

    def check(self, name="agy", **kwargs):
        card = kwargs.pop("card", None) or blocks.builtin_card(name)
        return blocks_check.check_cli(card, root=self.root, path=str(self.fake.bin),
                                      runner=self.fake.runner, **kwargs)

    def trial(self, model=FLASH, **kwargs):
        self.assertIn("model", inspect.signature(blocks_check.check_cli).parameters,
                      "68c 要求 check_cli 支持可选 model 参数")
        return self.check(model=model, **kwargs)

    def known(self, **changes):
        record = dict(type="check", ok=False, status="listed", model=ARGON,
                      models=AGY_MODELS, models_from="tool", reason="默认模型额度不足", how="", seconds=1)
        record.update(changes)
        return blocks.add_record("agy", record, self.root)


class ModelListTest(CliCase):
    def test_agy_header_tab_names_filter_deduplicate_in_order(self):
        self.fake.save(output=("Fetching available models...\n"
                               + FLASH + "\tGemini Flash\n"
                               + ARGON + "\tGemini Argon\n"
                               + FLASH + "\t重复\n"
                               "-bad\t无效\nname with space\t无效\n模型名\t无效\n"
                               + "x" * 101 + "\t太长\n"))
        result = self.check()
        self.assertEqual(result.get("models"), [FLASH, ARGON])
        self.assertEqual(result.get("models_from"), "tool")
        self.assertEqual([c["args"] for c in self.fake.calls() if c["args"] == ["models"]], [["models"]])

    def test_cursor_header_blank_line_suffix_and_order(self):
        self.fake.save(output=("Available models\n\ncomposer-2.5 - Composer 2.5 (current, default)\n"
                               "auto - Auto\ncomposer-2.5 - 重复\n"
                               "gpt-5.2 - GPT 5.2 (default)\n-bad - 无效\ninvalid name - 无效\n"))
        result = self.check("cursor")
        self.assertEqual(result.get("models"), ["composer-2.5", "auto", "gpt-5.2"])
        self.assertEqual(result.get("models_from"), "tool")
        self.assertEqual(self.fake.calls()[0]["command"], "agent")
        self.assertEqual(self.fake.calls()[0]["args"], ["models"])

    def test_codex_reads_only_visible_valid_cache_slugs_without_models_command(self):
        self.fake.cache([
            {"slug": "test-chat-b", "visibility": "list", "display_name": "B"},
            {"slug": "hidden-chat", "visibility": "hide", "display_name": "Hidden"},
            {"slug": "test-chat-a", "visibility": "list", "display_name": "A"},
            {"slug": "test-chat-b", "visibility": "list"},
            {"slug": "-bad", "visibility": "list"},
            {"slug": "bad name", "visibility": "list"},
            {"slug": "x" * 101, "visibility": "list"},
        ])
        result = self.check("codex")
        self.assertEqual(result.get("models"), ["test-chat-b", "test-chat-a"])
        self.assertEqual(result.get("models_from"), "tool")
        self.assertEqual(len(self.fake.calls()), 1, "Codex 只读缓存，再跑一次默认模型小活")
        self.assertNotEqual(self.fake.calls()[0]["args"], ["models"])

    def test_agy_and_cursor_unavailable_lists_fall_back_to_builtin(self):
        for name in ("agy", "cursor"):
            for mode in ("exit", "empty", "invalid", "timeout", "oserror"):
                with self.subTest(tool=name, failure=mode):
                    self.fake.mode = mode
                    self.fake.save(list_code=1 if mode == "exit" else 0,
                                   output="" if mode == "empty" else
                                   "-bad\t无效\ninvalid name - 无效" if mode == "invalid" else AGY_OUTPUT)
                    result = self.check(name)
                    self.assertEqual(result.get("models"), [m["value"] for m in MODEL_OPTIONS[name]])
                    self.assertEqual(result.get("models_from"), "builtin")

    def test_codex_missing_corrupt_empty_or_hidden_cache_falls_back(self):
        cache = self.fake.home / ".codex" / "models_cache.json"
        for mode in ("missing", "corrupt", "empty", "hidden", "invalid"):
            with self.subTest(cache=mode):
                cache.parent.mkdir(exist_ok=True)
                if mode == "missing":
                    cache.unlink(missing_ok=True)
                elif mode == "corrupt":
                    cache.write_text("{bad json")
                else:
                    self.fake.cache([] if mode == "empty" else [
                        {"slug": "hidden" if mode == "hidden" else "-bad",
                         "visibility": "hide" if mode == "hidden" else "list"}])
                result = self.check("codex")
                self.assertEqual(result.get("models"), [m["value"] for m in MODEL_OPTIONS["codex"]])
                self.assertEqual(result.get("models_from"), "builtin")


class DefaultCheckTest(CliCase):
    def test_default_success_records_list_and_only_probes_default(self):
        result = self.check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result.get("models"), AGY_MODELS)
        self.assertEqual(result.get("models_from"), "tool")
        self.assertEqual(result.get("model"), ARGON)
        self.assertRegex(result["reason"], r"通了.*秒.*4 个模型")
        calls = self.fake.calls()
        self.assertEqual(calls[0]["args"], ["models"], "先拿名单再试默认模型")
        self.assertEqual(len(self.fake.probes()), 1)
        self.assertIn(ARGON, self.fake.probes()[0]["args"])
        self.assertNotIn(FLASH, self.fake.probes()[0]["args"])
        record = blocks.last_record("agy", self.root, type="check")
        for key in ("models", "models_from", "model", "ok", "status"):
            self.assertEqual(record[key], result[key])

    def test_default_quota_or_failure_lists_family_without_trying_others(self):
        for reply, wording in (("quota", "额度不足"), ("fail", "没通")):
            with self.subTest(reply=reply):
                before = len(self.fake.probes())
                self.fake.save(probe=reply)
                result = self.check()
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "listed")
                self.assertEqual(result.get("models"), AGY_MODELS)
                self.assertEqual(result.get("model"), ARGON)
                for words in ("已接上", "4 个模型", ARGON, wording):
                    self.assertIn(words, result["reason"])
                self.assertIn("下面的名单里试试别的模型", result["how"])
                self.assertEqual(len(self.fake.probes()) - before, 1)

    def test_builtin_list_also_counts_as_listed_when_default_has_no_quota(self):
        self.fake.save(list_code=1, probe="quota")
        result = self.check()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "listed")
        self.assertEqual(result.get("models_from"), "builtin")
        self.assertEqual(result.get("models"), [m["value"] for m in MODEL_OPTIONS["agy"]])

    def test_missing_and_not_logged_in_keep_existing_status(self):
        (self.fake.bin / "agy").unlink()
        result = self.check()
        self.assertEqual(result["status"], "missing")
        self.assertFalse(result["ok"])
        self.assertEqual(self.fake.calls(), [])
        shutil.copyfile(self.fake.bin / "mytool", self.fake.bin / "agy")
        (self.fake.bin / "agy").chmod(0o755)
        self.fake.save(probe="login")
        result = self.check()
        self.assertEqual(result["status"], "fail")
        self.assertIn("没登录", result["reason"])

    def test_record_false_does_not_write_records(self):
        self.check(record=False)
        self.assertEqual(blocks.records("agy", self.root), [])


class ModelTrialTest(CliCase):
    def test_trial_changes_only_model_and_appends_model_check(self):
        last_check = self.known()
        before_card = blocks.builtin_card("agy")
        result = self.trial(card=before_card)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["status"], "ok")
        records = blocks.records("agy", self.root)
        self.assertEqual([r["type"] for r in records], ["check", "model_check"])
        self.assertEqual(blocks.last_record("agy", self.root, type="check"), last_check)
        trial = records[-1]
        self.assertEqual(trial["model"], FLASH)
        for key in ("ok", "status", "reason", "how", "seconds", "time"):
            self.assertIn(key, trial)
        self.assertEqual(len(self.fake.calls()), 1, "试模型不重新取名单，也不试默认模型")
        self.assertEqual(self.fake.probes()[0]["args"],
                         ["-p", blocks_check.probe_prompt(blocks.DEFAULT_DONE_FILE),
                          "--model", FLASH, "--output-format", "json"])
        self.assertEqual(before_card, blocks.builtin_card("agy"), "不改基础卡的默认模型")

    def test_trial_quota_and_failure_are_per_model_not_listed(self):
        self.known()
        for reply, expected in (("quota", "quota"), ("fail", "fail")):
            with self.subTest(reply=reply):
                self.fake.save(probe=reply)
                result = self.trial()
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], expected)
                last = blocks.last_record("agy", self.root, type="model_check")
                self.assertEqual(last["model"], FLASH)
                self.assertEqual(last["status"], expected)
        self.assertEqual(len([r for r in blocks.records("agy", self.root) if r["type"] == "check"]), 1)

    def test_trial_requires_latest_check_list_and_refuses_without_running(self):
        for setup in ("never", "old-list", "outside"):
            with self.subTest(setup=setup):
                if setup == "old-list":
                    self.known()
                    self.known(models=[ARGON])
                elif setup == "outside":
                    self.known(models=[ARGON])
                before = blocks.records("agy", self.root)
                with self.assertRaisesRegex(blocks.BlockError, "名单"):
                    self.trial()
                self.assertEqual(self.fake.calls(), [])
                self.assertEqual(blocks.records("agy", self.root), before)

    def test_detection_and_trial_share_one_at_a_time_in_both_directions(self):
        self.assertIn("model", inspect.signature(blocks_check.check_cli).parameters,
                      "缺少单模型试用入口")
        self.known()
        for first_is_trial in (False, True):
            with self.subTest(first_is_trial=first_is_trial):
                entered, release = threading.Event(), threading.Event()
                results, errors = [], []

                def slow_runner(args, folder, timeout):
                    entered.set()
                    if not release.wait(5):
                        raise AssertionError("假执行没有释放")
                    return self.fake.runner(args, folder, timeout)

                def work():
                    try:
                        kwargs = {"model": FLASH} if first_is_trial else {}
                        results.append(blocks_check.check_cli(blocks.builtin_card("agy"), root=self.root,
                                       path=str(self.fake.bin), runner=slow_runner, **kwargs))
                    except Exception as exc:
                        errors.append(exc)

                thread = threading.Thread(target=work)
                thread.start()
                try:
                    self.assertTrue(entered.wait(3), errors)
                    self.assertTrue(blocks_check.checking("agy"))
                    calls = len(self.fake.calls())
                    with self.assertRaises(blocks.BlockError):
                        self.check(**({} if first_is_trial else {"model": FLASH}))
                    self.assertEqual(len(self.fake.calls()), calls, "忙时不能再启动任何 CLI")
                finally:
                    release.set()
                    thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertTrue(results[0]["ok"])
                self.assertFalse(blocks_check.checking("agy"))


class ModelStatusTest(CliCase):
    def status(self):
        reader = getattr(blocks, "model_status", None)
        self.assertTrue(callable(reader), "68c 缺少 blocks.model_status(card_id, root=None)")
        return reader("agy", root=self.root)

    def append(self, **record):
        blocks.append_jsonl(blocks._record_path("agy", self.root), record)

    def test_no_records_returns_empty_status(self):
        self.assertEqual(self.status(), {})

    def test_latest_check_and_trial_win_per_model_and_ignore_other_records(self):
        self.append(type="check", model=ARGON, ok=False, status="listed", reason="默认额度不足",
                    models=AGY_MODELS, time="2026-11-12T08:00:00")
        self.append(type="model_check", model=FLASH, ok=True, status="ok", reason="Flash 通了",
                    time="2026-11-12T08:01:00")
        self.append(type="model_check", model=ARGON, ok=True, status="ok", reason="Argon 通了",
                    time="2026-11-12T08:02:00")
        self.append(type="check", model=FLASH, ok=False, status="listed", reason="默认模型没通",
                    models=AGY_MODELS, time="2026-11-12T08:03:00")
        self.append(type="install", model=ARGON, ok=False, status="fail", reason="无关安装",
                    time="2026-11-12T08:04:00")
        self.append(type="check", ok=True, status="ok", reason="旧格式没有模型",
                    time="2026-11-12T08:05:00")
        statuses = self.status()
        self.assertEqual(set(statuses), {ARGON, FLASH}, "没试过的模型不能凭空标成能用")
        self.assertEqual(statuses[ARGON], {"status": "ok", "reason": "Argon 通了", "time": "2026-11-12T08:02:00"})
        self.assertEqual(statuses[FLASH]["reason"], "默认模型没通")
        self.assertEqual(statuses[FLASH]["time"], "2026-11-12T08:03:00")
        self.assertNotEqual(statuses[FLASH]["status"], "ok")

    def test_two_records_in_same_second_use_last_record(self):
        for status in ("ok", "quota", "fail"):
            self.append(type="model_check", model=FLASH, ok=status == "ok", status=status,
                        reason=status, time="2026-11-12T08:00:00")
        self.assertEqual(self.status()[FLASH], {"status": "fail", "reason": "fail", "time": "2026-11-12T08:00:00"})

    def test_default_listed_record_preserves_per_model_quota_or_failure(self):
        for reason, expected in (("已接上：这家有 4 个模型。默认的 " + ARGON + " 额度不足", "quota"),
                                 ("已接上：这家有 4 个模型。默认的 " + ARGON + " 没通，原因模型名不对", "fail")):
            with self.subTest(expected=expected):
                self.known(reason=reason)
                self.assertEqual(self.status()[ARGON]["status"], expected)


class CompatibilityTest(CliCase):
    def test_clerk_cli_does_not_fetch_models_or_gain_family_fields(self):
        card = blocks.validate_card(dict(id="mytool", name="管家起草的工具", kind="cli", command="mytool",
                                         run=["--model", "{model}", "{prompt}"], model="my-model", source="clerk"))
        approved = blocks_check.preview(card, path=str(self.fake.bin))
        result = self.check(card=card, approved=approved)
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.fake.calls()), 1)
        self.assertNotIn("models", result)
        self.assertNotIn("models_from", result)

    def test_preset_command_under_other_id_does_not_get_family_list(self):
        card = dict(blocks.builtin_card("agy"), id="my-agy", source="clerk")
        result = self.check(card=card, approved=blocks_check.preview(card, path=str(self.fake.bin)))
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.fake.calls()), 1)
        self.assertNotIn("models", result)

    def test_preset_id_with_changed_command_does_not_get_family_list(self):
        card = dict(blocks.builtin_card("agy"), command="mytool", source="clerk")
        result = self.check(card=card, approved=blocks_check.preview(card, path=str(self.fake.bin)))
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.fake.calls()), 1)
        self.assertNotIn("models", result)

    def test_clerk_cli_quota_keeps_original_quota_status(self):
        self.fake.save(probe="quota")
        card = dict(blocks.builtin_card("agy"), id="my-agy", source="clerk")
        result = self.check(card=card, approved=blocks_check.preview(card, path=str(self.fake.bin)))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "quota")
        self.assertEqual(len(self.fake.calls()), 1)
        self.assertNotIn("models", result)

    def test_ordinary_tool_keeps_probe_and_does_not_fetch_models(self):
        self.fake.save(tool=True)
        card = blocks.validate_card(dict(id="ordinary", kind="cli", role="tool", name="普通工具",
                                         command="mytool", probe=["--version"], ok_words=["OK"], source="clerk"))
        result = self.check(card=card, approved=blocks_check.preview(card, path=str(self.fake.bin)))
        self.assertTrue(result["ok"])
        self.assertEqual([c["args"] for c in self.fake.calls()], [["--version"]])
        self.assertNotIn("models", result)
        self.assertNotIn("models_from", result)

    def test_unverified_cli_still_requires_matching_approval(self):
        card = dict(blocks.builtin_card("agy"), command="mytool", source="clerk")
        with self.assertRaises(blocks.BlockError):
            self.check(card=card)
        with self.assertRaises(blocks.BlockError):
            self.check(card=card, approved="not the preview")
        self.assertEqual(self.fake.calls(), [])


class CliModelsServerTest(BlocksCase):
    def setUp(self):
        super().setUp()
        self.cli = FakeCli(self, Path(self.tmp))
        self.server.terminal_path = lambda: str(self.cli.bin)
        p = patch.object(blocks_check, "watched_runner", self.cli.runner)
        p.start()
        self.addCleanup(p.stop)
        for name in ("agy", "cursor", "codex"):
            blocks.save_card(blocks.builtin_card(name), self.home)
        abilities.ensure_builtin(self.home)
        ability = copy.deepcopy(abilities.BUILTIN_ABILITIES["draw-image"])
        ability.update(id="agy-picture", title="agy 画图", block="agy")
        abilities.save_ability(ability, self.home)

    def known(self, card_id="agy", **changes):
        record = dict(type="check", ok=False, status="listed", models=AGY_MODELS,
                      models_from="tool", model=ARGON, reason="默认额度不足", how="", seconds=1)
        record.update(changes)
        return blocks.add_record(card_id, record, self.home)

    def request_job(self, body):
        status, data = self.bpost("/api/blocks/check", body)
        self.assertEqual(status, 200, data)
        job = self.job(data["job"])
        self.assertFalse(job["error"], job)
        return job

    def test_check_endpoint_without_model_still_checks_default_and_lists_models(self):
        result = self.request_job({"id": "agy"})["result"]
        self.assertTrue(result["ok"], result)
        self.assertEqual(result.get("model"), ARGON)
        self.assertEqual(result.get("models"), AGY_MODELS)
        self.assertEqual(result.get("models_from"), "tool")

    def test_check_endpoint_model_trial_keeps_last_check_and_family_turns_usable(self):
        original = self.known(status="fail", reason="默认没通")
        result = self.request_job({"id": "agy", "model": FLASH})["result"]
        self.assertTrue(result["ok"])
        self.assertEqual(blocks.last_record("agy", self.home, type="check"), original)
        model_check = blocks.last_record("agy", self.home, type="model_check")
        self.assertIsNotNone(model_check)
        self.assertEqual(model_check["model"], FLASH)
        listing = self.bget("/api/blocks/list")[1]
        item = next(b for b in listing["blocks"] if b["id"] == "agy")
        self.assertEqual(item["status"], "ok", "检测没通过后试通一个模型，整家就能用")
        detail = self.bget("/api/abilities/detail", id="agy-picture")[1]
        self.assertEqual(detail["status"], "ok", detail)
        ability = next(a for a in self.bget("/api/abilities/list")[1]["abilities"] if a["id"] == "agy-picture")
        self.assertEqual(ability["status"], "ok", ability)
        self.assertEqual(len(self.cli.probes()), 1)
        self.assertIn(FLASH, self.cli.probes()[0]["args"])

    def test_trial_endpoint_refuses_unknown_model_before_starting_job(self):
        self.known()
        before = blocks.records("agy", self.home)
        status, data = self.bpost("/api/blocks/check", {"id": "agy", "model": "outside-model"})
        # 若旧实现误开后台任务，先收尾再断言，避免污染下个测试。
        if data.get("job"):
            self.job(data["job"])
        self.assertEqual(status, 400, data)
        self.assertIn("这个模型不在这家的名单里，先检测一次拿名单", data["error"])
        self.assertEqual(self.cli.calls(), [])
        self.assertEqual(blocks.records("agy", self.home), before)

    def test_trial_endpoint_refuses_without_recent_list(self):
        status, data = self.bpost("/api/blocks/check", {"id": "agy", "model": FLASH})
        if data.get("job"):
            self.job(data["job"])
        self.assertEqual(status, 400, data)
        self.assertEqual(self.cli.calls(), [])

    def test_unverified_card_cannot_trial_even_with_approved_command(self):
        card = dict(blocks.builtin_card("agy"), command="mytool", source="clerk")
        blocks.save_card(card, self.home)
        self.known()
        status, data = self.bpost("/api/blocks/check", dict(id="agy", model=FLASH,
                                 approved=blocks_check.preview(card, path=str(self.cli.bin))))
        if data.get("job"):
            self.job(data["job"])
        self.assertEqual(status, 400, data)
        self.assertEqual(self.cli.calls(), [])

    def test_detail_list_and_ability_detail_expose_models_source_and_status(self):
        for name, aid in (("agy", "agy-picture"), ("codex", "draw-image")):
            with self.subTest(tool=name):
                self.known(name, models_from="builtin")
                rec = blocks.add_record(name, dict(type="model_check", model=FLASH, ok=True, status="ok",
                                                   reason="通了", how="", seconds=1), self.home)
                status, detail = self.bget("/api/blocks/detail", id=name)
                self.assertEqual(status, 200, detail)
                # 沿用 API 块的 last_check.models 字段，来源可由同一检测记录读取。
                self.assertEqual(detail["last_check"]["models"], AGY_MODELS)
                self.assertEqual(detail.get("models_from", detail["last_check"].get("models_from")), "builtin")
                self.assertEqual(detail.get("model_status", {}).get(FLASH),
                                 {"status": "ok", "reason": "通了", "time": rec["time"]})
                item = next(b for b in self.bget("/api/blocks/list")[1]["blocks"] if b["id"] == name)
                self.assertEqual(item["models"], 4)
                self.assertEqual(item.get("models_from"), "builtin")
                self.assertEqual(item.get("model_status", {}).get(FLASH, {}).get("status"), "ok")
                status, ability = self.bget("/api/abilities/detail", id=aid)
                self.assertEqual(status, 200, ability)
                self.assertEqual(ability["models"], AGY_MODELS)
                self.assertEqual(ability.get("model_status", {}).get(FLASH, {}).get("status"), "ok")

    def test_family_status_uses_only_successes_after_latest_check(self):
        sequence = [
            (dict(type="check", ok=False, status="listed", model=ARGON, models=AGY_MODELS), "listed"),
            (dict(type="model_check", ok=True, status="ok", model=FLASH), "ok"),
            (dict(type="model_check", ok=False, status="quota", model=ARGON), "ok"),
            (dict(type="check", ok=False, status="fail", model=ARGON, models=AGY_MODELS), "fail"),
            (dict(type="model_check", ok=False, status="fail", model=FLASH), "fail"),
            (dict(type="check", ok=True, status="ok", model=ARGON, models=AGY_MODELS), "ok"),
            (dict(type="model_check", ok=False, status="fail", model=ARGON), "ok"),
            (dict(type="check", ok=False, status="quota", model=ARGON, models=AGY_MODELS), "quota"),
        ]
        for index, (record, expected) in enumerate(sequence):
            with self.subTest(step=index, expected=expected):
                # 故意同一秒：记录顺序必须能区分最近检测前后。
                blocks.append_jsonl(blocks._record_path("agy", self.home),
                                    dict(record, time="2026-11-12T10:00:00", reason="测试原因"))
                item = next(b for b in self.bget("/api/blocks/list")[1]["blocks"] if b["id"] == "agy")
                self.assertEqual(item["status"], expected)
                detail = self.bget("/api/abilities/detail", id="agy-picture")[1]
                self.assertEqual(detail["status"], "unusable" if expected == "fail" else "ok", detail)

    def test_cli_ability_can_save_main_two_backups_and_clear_backups(self):
        for block_id, aid in (("agy", "agy-picture"), ("codex", "draw-image")):
            with self.subTest(block=block_id):
                self.known(block_id)
                before = abilities.load_ability(aid, self.home)
                backups = [ARGON, AGY_MODELS[2]]
                status, data = self.bpost("/api/abilities/model", dict(id=aid, model=FLASH, backup_models=backups))
                self.assertEqual(status, 200, data)
                after = abilities.load_ability(aid, self.home)
                self.assertEqual(after, dict(before, model=FLASH, backup_models=backups))
                self.assertEqual(data["ability"], after)
                status, data = self.bpost("/api/abilities/model", dict(id=aid, model=ARGON, backup_models=[]))
                self.assertEqual(status, 200, data)
                self.assertEqual(abilities.load_ability(aid, self.home)["backup_models"], [])
                self.assertEqual(blocks.load_card(block_id, self.home)["model"], blocks.BUILTIN[block_id]["model"])

    def test_cli_ability_rejects_invalid_choices_without_changing_card(self):
        self.known()
        before = abilities.load_ability("agy-picture", self.home)
        cases = [
            ("outside", [], "主模型"),
            (FLASH, ["outside"], "备用模型"),
            (FLASH, [ARGON, ARGON], "重复"),
            (FLASH, [FLASH], "主模型"),
            (FLASH, [ARGON, AGY_MODELS[2], AGY_MODELS[3]], "最多"),
            (FLASH, ARGON, "列表"),
        ]
        for main, backups, words in cases:
            with self.subTest(main=main, backups=backups):
                status, data = self.bpost("/api/abilities/model", dict(id="agy-picture", model=main, backup_models=backups))
                self.assertEqual(status, 400, data)
                self.assertIn(words, data["error"])
                self.assertEqual(abilities.load_ability("agy-picture", self.home), before)

    def test_cli_ability_without_models_requests_base_layer_detection(self):
        status, data = self.bpost("/api/abilities/model", dict(id="agy-picture", model=FLASH, backup_models=[]))
        self.assertEqual(status, 409, data)
        self.assertIn("基础层检测「agy」", data["error"])

    def test_api_model_saving_and_search_restrictions_still_work(self):
        blocks.save_card(dict(id="bailian", kind="llm_api", name="阿里百炼", provider="bailian",
                              base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                              key_account="bailian"), self.home)
        self.known("bailian", models=["qwen-plus", "qwen-flash"])
        status, data = self.bpost("/api/abilities/model", dict(id="write-article", model="qwen-plus", backup_models=["qwen-flash"]))
        self.assertEqual(status, 200, data)
        status, data = self.bpost("/api/abilities/model", dict(id="search-web", model="qwen-plus", backup_models=[]))
        self.assertEqual(status, 400, data)
        self.assertEqual(self.cli.calls(), [])


class PageContractTest(unittest.TestCase):
    def test_required_model_actions_and_status_words_exist(self):
        for name in ("blocks.js", "abilities.js"):
            text = (STATIC / name).read_text(encoding="utf-8")
            for words in (("试这个模型", "没试过", "额度不足", "能用") if name == "blocks.js"
                          else ("没试过", "额度不足", "能用")):
                with self.subTest(file=name, words=words):
                    self.assertTrue(words in text, f"{name} 缺少施工单要求的界面文案：{words}")
        text = (STATIC / "abilities.js").read_text(encoding="utf-8")
        self.assertFalse("这一版不能换" in text, "abilities.js 还在提示‘这一版不能换’")

    def test_blocks_detail_shows_cli_model_count_source_and_detection_hint(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "此页面验收需要 Node 执行项目现有的 BlocksCore")
        script = r'''
const assert = require('assert');
const B = require(process.argv[1]);
function detail(last) {
  return B.detailView({card: {id: 'agy', kind: 'cli', role: 'agent', name: 'agy', command: 'agy'},
    verified: true, category: 'cli_agent', last_check: last,
    models_from: last && last.models_from, model_status: {}}, null);
}
let view = detail({ok:false, status:'listed', reason:'已接上', how:'试别的模型', seconds:1,
  time:'2026-11-12T10:00:00', models:['gemini-4-argon-high','gemini-3.8-flash-high'], models_from:'builtin'});
let row = view.rows.find(r => r.label === '这家的模型');
assert.ok(row && /2 个/.test(row.text), 'CLI 显示模型数量');
assert.ok(/内置.*可能不全/.test(row.text), '内置名单有提示');
assert.strictEqual(view.status.cls, 'warn');
assert.ok(/已接上/.test(view.status.text));
view = detail(null);
row = view.rows.find(r => r.label === '这家的模型');
assert.ok(row && /检测.*显示名单/.test(row.text), '未检测先提示拿名单');
'''
        done = subprocess.run([node, "-e", script, str(STATIC / "blocks.js")],
                              capture_output=True, text=True, timeout=15)
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
