"""67 号单：能力卡片的运行。

三种运行方式：联网搜索、大模型 API、命令行工具（AI 助手）。结果由程序查（66 号单的 check_output），
不自动换模型；模型不通、格式不对、积木块的问题、其他错误分开说；每次运行写记录，成果存进文件夹。
Codex 出图找不到要求的文件时，去 Codex 自己的图片文件夹找这次运行之后新出的图。
依据：docs/积木库/能力层/方案.md 技术设计“运行”；docs/积木库/施工单/67-能力卡片运行.md。
全部用假积木块：假搜索、假大模型、假命令行工具，不真联网、不真跑 Codex。
"""
import json
import os
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock

from collab import abilities, abilities_run, blocks, blocks_web
from collab.abilities import AbilityError
from desktop.clerk.client import ModelError

TAVILY_KEY = "tvly-" + "a" * 24
BAILIAN_KEY = "sk-" + "b" * 30
KEYS = {"tavily": TAVILY_KEY, "bailian": BAILIAN_KEY}
BAILIAN_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def png_bytes(w, h):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def model_error(code, detail=""):
    exc = ModelError("模型服务商出错了")
    exc.code = code
    exc.detail = detail
    return exc


class FakeClient:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def chat(self, messages, on_text=None, tools=None):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return {"text": self.reply, "tool_calls": [], "tokens": None}


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.root = base / "积木库"
        self.bin = base / "bin"
        self.bin.mkdir()
        exe = self.bin / "codex"
        exe.write_text("#!/bin/sh\nexit 0\n")
        exe.chmod(0o755)
        self.images = base / "codex_images"
        self.images.mkdir()
        blocks.save_card(blocks.search_card("tavily"), self.root)
        blocks.save_card({"id": "bailian", "kind": "llm_api", "name": "阿里百炼", "provider": "bailian",
                          "base_url": BAILIAN_URL, "key_account": "bailian"}, self.root)
        blocks.save_card(dict(blocks.BUILTIN["codex"], source="builtin"), self.root)
        abilities.ensure_builtin(self.root)
        self.factory_calls = []

    def tearDown(self):
        self._tmp.cleanup()

    def factory(self, client):
        def make(base_url, model, key):
            self.factory_calls.append((base_url, model, key))
            return client
        return make

    def run_ability(self, ability_id, inputs, **kw):
        kw.setdefault("keys", KEYS)
        kw.setdefault("path", str(self.bin))
        kw.setdefault("codex_images", self.images)
        return abilities_run.run(ability_id, inputs, self.root, **kw)

    def last_run(self, ability_id):
        items = abilities.runs(ability_id, self.root)
        return items[-1] if items else None

    def assertNoSecret(self, obj):
        text = json.dumps(obj, ensure_ascii=False)
        self.assertNotIn(TAVILY_KEY, text)
        self.assertNotIn(BAILIAN_KEY, text)


# ---------- 联网搜索 ----------

class Search(Case):
    def test_ok(self):
        got = []

        def fake(query, key, max_results, time_range):
            got.append((query, key, max_results, time_range))
            return [{"title": "公园 A", "url": "https://a.cn", "snippet": "好玩"},
                    {"title": "公园 B", "url": "https://b.cn", "snippet": ""}]
        r = self.run_ability("search-web", {"keywords": "上海 公园", "period": "最近一周"}, search_fn=fake)
        self.assertEqual(got, [("上海 公园", TAVILY_KEY, 8, "week")])
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["output"], [{"title": "公园 A", "url": "https://a.cn", "summary": "好玩"},
                                       {"title": "公园 B", "url": "https://b.cn", "summary": ""}])
        self.assertIsNone(r["model"])
        folder = abilities.result_folder("search-web", r["run_id"], self.root)
        self.assertEqual(json.loads((folder / "结果.json").read_text(encoding="utf-8")), r["output"])
        rec = self.last_run("search-web")
        self.assertEqual((rec["type"], rec["run_id"], rec["ok"], rec["status"]), ("run", r["run_id"], True, "ok"))
        self.assertEqual(rec["inputs"]["keywords"], "上海 公园")
        self.assertNoSecret(rec)
        self.assertNoSecret(r)

    def test_empty_is_format(self):
        r = self.run_ability("search-web", {"keywords": "x"}, search_fn=lambda *a: [])
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "format")
        self.assertIn("一条都没有", r["reason"])

    def test_no_key_is_block(self):
        r = self.run_ability("search-web", {"keywords": "x"}, keys={}, search_fn=lambda *a: [])
        self.assertEqual(r["status"], "block")
        self.assertIn("密钥", r["reason"])

    def test_quota_and_bad_key_are_block(self):
        for status in (429, 432, 401):
            def fake(*a, status=status):
                raise blocks_web.SearchError("Tavily 出错了", status)
            r = self.run_ability("search-web", {"keywords": "x"}, search_fn=fake)
            self.assertEqual(r["status"], "block", status)
            self.assertNoSecret(r)

    def test_other_failure_is_error(self):
        def fake(*a):
            raise blocks.BlockError("连不上 Tavily：检查网络后再试")
        r = self.run_ability("search-web", {"keywords": "x"}, search_fn=fake)
        self.assertEqual(r["status"], "error")


class SearchFunction(unittest.TestCase):
    """blocks_web.search 多两个本事：最多 20 条、时间范围。老的叫法（默认 5 条、不带时间范围）不变。"""

    def body(self, n):
        return {"results": [{"title": f"t{i}", "url": f"https://x.cn/{i}", "content": "c"} for i in range(n)]}

    def test_more_results_and_time_range(self):
        with mock.patch.object(blocks_web, "_post_json", return_value=(200, self.body(25))) as post:
            items = blocks_web.search("上海", "tvly-abc", 12, "week")
        payload = post.call_args[0][1]
        self.assertEqual(payload["max_results"], 12)
        self.assertEqual(payload["time_range"], "week")
        self.assertEqual(len(items), 12)

    def test_old_call_unchanged(self):
        with mock.patch.object(blocks_web, "_post_json", return_value=(200, self.body(25))) as post:
            items = blocks_web.search("上海", "tvly-abc")
        payload = post.call_args[0][1]
        self.assertLessEqual(payload["max_results"], 5)
        self.assertNotIn("time_range", payload)
        self.assertEqual(len(items), 5)

    def test_none_time_range_not_sent(self):
        with mock.patch.object(blocks_web, "_post_json", return_value=(200, self.body(3))) as post:
            blocks_web.search("上海", "tvly-abc", 3, "none")
        self.assertNotIn("time_range", post.call_args[0][1])

    def test_bad_args(self):
        for args in (("x", "k", 0), ("x", "k", 21), ("x", "k", 5, "year"), ("x", "k", True)):
            with self.assertRaises(blocks.BlockError):
                blocks_web.search(*args)


# ---------- 大模型 API ----------

class LlmApi(Case):
    def test_ok(self):
        client = FakeClient(reply="字" * 600)
        r = self.run_ability("write-article", {"topic": "春天", "words": 800}, client_factory=self.factory(client))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["model"], "qwen3.7-plus")
        self.assertEqual(self.factory_calls, [(BAILIAN_URL, "qwen3.7-plus", BAILIAN_KEY)])
        messages = client.calls[0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[-1]["role"], "user")
        self.assertIn("春天", messages[-1]["content"])
        self.assertEqual(r["output"], "字" * 600)
        folder = abilities.result_folder("write-article", r["run_id"], self.root)
        self.assertEqual((folder / "结果.txt").read_text(encoding="utf-8"), "字" * 600)
        rec = self.last_run("write-article")
        self.assertEqual((rec["status"], rec["model"]), ("ok", "qwen3.7-plus"))
        self.assertNoSecret(rec)

    def test_long_text_kept_whole_but_record_cut(self):
        r = self.run_ability("write-article", {"topic": "春天", "words": 3000},
                             client_factory=self.factory(FakeClient(reply="字" * 5000)))
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["output"]), 5000)
        self.assertLessEqual(len(self.last_run("write-article")["output"]), 2000)

    def test_too_short_is_format(self):
        r = self.run_ability("write-article", {"topic": "春天", "words": 800},
                             client_factory=self.factory(FakeClient(reply="太短了")))
        self.assertEqual(r["status"], "format")
        self.assertIn("400", r["reason"])

    def test_model_refused_no_auto_switch(self):
        for code in (400, 403, 404, 402, 429):
            self.factory_calls.clear()
            err = model_error(code, f"model not enabled for key {BAILIAN_KEY}")
            r = self.run_ability("write-article", {"topic": "春天"}, client_factory=self.factory(FakeClient(error=err)))
            self.assertEqual(r["status"], "model", code)
            self.assertEqual(r["next_model"], "qwen3.6-plus")
            self.assertEqual(len(self.factory_calls), 1, "不自动换模型")
            self.assertIn("model not enabled", r["platform_said"])
            self.assertNoSecret(r)
            self.assertNoSecret(self.last_run("write-article"))

    def test_bad_key_is_block(self):
        r = self.run_ability("write-article", {"topic": "春天"},
                             client_factory=self.factory(FakeClient(error=model_error(401))))
        self.assertEqual(r["status"], "block")
        self.assertIn("阿里百炼", r["how"])

    def test_other_errors(self):
        for err in (model_error(500), ModelError("模型回复超时了，请再试一次。")):
            r = self.run_ability("write-article", {"topic": "春天"}, client_factory=self.factory(FakeClient(error=err)))
            self.assertEqual(r["status"], "error")

    def test_no_key_is_block(self):
        r = self.run_ability("write-article", {"topic": "春天"}, keys={"tavily": TAVILY_KEY},
                             client_factory=self.factory(FakeClient(reply="x")))
        self.assertEqual(r["status"], "block")
        self.assertEqual(self.factory_calls, [])

    def test_switch_to_backup_on_request(self):
        r = self.run_ability("write-article", {"topic": "春天", "words": 100}, model="qwen3.6-plus",
                             client_factory=self.factory(FakeClient(reply="字" * 100)))
        self.assertTrue(r["ok"])
        self.assertEqual(r["model"], "qwen3.6-plus")
        self.assertEqual(self.factory_calls[0][1], "qwen3.6-plus")
        self.assertEqual(abilities.load_ability("write-article", self.root)["model"], "qwen3.7-plus", "换模型只管这一次")
        # 备用模型也不通：没有下一个了
        r = self.run_ability("write-article", {"topic": "春天"}, model="qwen3.6-plus",
                             client_factory=self.factory(FakeClient(error=model_error(403))))
        self.assertEqual(r["status"], "model")
        self.assertIsNone(r["next_model"])

    def test_only_backup_models_allowed(self):
        with self.assertRaises(AbilityError):
            self.run_ability("write-article", {"topic": "春天"}, model="qwen-max",
                             client_factory=self.factory(FakeClient(reply="x")))
        self.assertIsNone(self.last_run("write-article"))

    def test_model_not_in_block_list(self):
        blocks.add_record("bailian", {"type": "check", "ok": True, "status": "ok",
                                      "models": ["qwen3.6-plus", "qwen-flash"]}, self.root)
        r = self.run_ability("write-article", {"topic": "春天"}, client_factory=self.factory(FakeClient(reply="x")))
        self.assertEqual(r["status"], "model")
        self.assertIn("qwen3.7-plus", r["reason"])
        self.assertEqual(r["next_model"], "qwen3.6-plus")
        self.assertEqual(self.factory_calls, [], "名单里没有就不去试")


# ---------- 命令行工具（Codex 出图） ----------

class Cli(Case):
    def runner(self, code=0, output="OK", write=None, where=None, sleep=0):
        seen = {}

        def run(args, folder, timeout):
            seen.update(args=args, folder=Path(folder), timeout=timeout)
            if sleep:
                time.sleep(sleep)
            if write is not None:
                target = (where or Path(folder)) / write[0]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(write[1])
            return code, output
        run.seen = seen
        return run

    def test_ok(self):
        run = self.runner(write=("输出.png", png_bytes(512, 300)))
        r = self.run_ability("draw-image", {"scene": "一只橘猫", "shape": "竖版"}, runner=run)
        self.assertTrue(r["ok"], r)
        args = run.seen["args"]
        self.assertEqual(Path(args[0]).name, "codex")
        self.assertIn("exec", args)
        self.assertTrue(any("一只橘猫" in a and "竖版" in a for a in args))
        self.assertIn(blocks.BUILTIN["codex"]["model"], args, "卡上没写模型，用积木块自己的")
        self.assertEqual(run.seen["timeout"], 300)
        self.assertFalse(run.seen["folder"].exists(), "临时文件夹用完要删")
        self.assertEqual(r["output"], "输出.png")
        folder = abilities.result_folder("draw-image", r["run_id"], self.root)
        self.assertEqual(abilities.image_size(folder / "输出.png"), (512, 300))
        self.assertIn("输出.png", r["files"])

    def test_image_saved_elsewhere_is_found(self):
        old = self.images / "old" / "before.png"
        old.parent.mkdir()
        old.write_bytes(png_bytes(512, 512))
        os.utime(old, (time.time() - 3600, time.time() - 3600))
        run = self.runner(write=("sub/new.png", png_bytes(640, 480)), where=self.images)
        r = self.run_ability("draw-image", {"scene": "猫"}, runner=run)
        self.assertTrue(r["ok"], r)
        self.assertIn("别处", r["reason"])
        folder = abilities.result_folder("draw-image", r["run_id"], self.root)
        self.assertEqual(abilities.image_size(folder / "输出.png"), (640, 480))

    def test_old_image_elsewhere_not_taken(self):
        old = self.images / "old.png"
        old.write_bytes(png_bytes(512, 512))
        os.utime(old, (time.time() - 3600, time.time() - 3600))
        r = self.run_ability("draw-image", {"scene": "猫"}, runner=self.runner())
        self.assertEqual(r["status"], "format")
        self.assertIn("输出.png", r["reason"])

    def test_small_image_is_format(self):
        r = self.run_ability("draw-image", {"scene": "猫"}, runner=self.runner(write=("输出.png", png_bytes(100, 100))))
        self.assertEqual(r["status"], "format")
        self.assertIn("100×100", r["reason"])

    def test_not_found_is_block(self):
        r = self.run_ability("draw-image", {"scene": "猫"}, path=str(Path(self._tmp.name) / "empty"),
                             runner=self.runner())
        self.assertEqual(r["status"], "block")
        self.assertIn("codex", r["reason"])

    def test_quota_is_model(self):
        r = self.run_ability("draw-image", {"scene": "猫"},
                             runner=self.runner(code=1, output="ERROR: You've hit your usage limit. Try again later."))
        self.assertEqual(r["status"], "model")
        self.assertIn("usage limit", r["platform_said"])

    def test_not_logged_in_is_block(self):
        r = self.run_ability("draw-image", {"scene": "猫"}, runner=self.runner(code=1, output="Not logged in. Run codex login"))
        self.assertEqual(r["status"], "block")

    def test_timeout_is_error(self):
        def run(args, folder, timeout):
            run.folder = Path(folder)
            raise subprocess.TimeoutExpired(args, timeout)
        r = self.run_ability("draw-image", {"scene": "猫"}, runner=run)
        self.assertEqual(r["status"], "error")
        self.assertIn("超时", r["reason"])
        self.assertFalse(run.folder.exists())

    def test_output_masked(self):
        r = self.run_ability("draw-image", {"scene": "猫"},
                             runner=self.runner(code=1, output=f"failed with key {BAILIAN_KEY} for a@b.com"))
        self.assertNoSecret(r)
        self.assertNotIn("a@b.com", json.dumps(r, ensure_ascii=False))
        self.assertNoSecret(self.last_run("draw-image"))


# ---------- 积木块的问题 ----------

class BlockProblems(Case):
    def test_block_removed(self):
        blocks.remove_card("bailian", self.root)
        r = self.run_ability("write-article", {"topic": "春天"}, client_factory=self.factory(FakeClient(reply="x")))
        self.assertEqual(r["status"], "block")
        self.assertIn("基础层", r["reason"])
        self.assertEqual(self.factory_calls, [])

    def test_block_last_check_failed(self):
        blocks.add_record("codex", {"type": "check", "ok": False, "status": "fail", "reason": "没登录"}, self.root)
        run = Cli.runner(self, write=("输出.png", png_bytes(512, 512)))
        r = self.run_ability("draw-image", {"scene": "猫"}, runner=run)
        self.assertEqual(r["status"], "block")
        self.assertIn("检测", r["how"])
        self.assertNotIn("args", run.seen, "积木块上次检测没通过就不跑")

    def test_quota_or_listed_block_still_runs(self):
        for status in ("quota", "listed"):
            blocks.add_record("bailian", {"type": "check", "ok": False, "status": status}, self.root)
            r = self.run_ability("write-article", {"topic": "春天", "words": 100},
                                 client_factory=self.factory(FakeClient(reply="字" * 100)))
            self.assertTrue(r["ok"], status)


# ---------- 总的规矩 ----------

class General(Case):
    def test_input_error_not_recorded(self):
        with self.assertRaises(AbilityError):
            self.run_ability("write-article", {}, client_factory=self.factory(FakeClient(reply="x")))
        self.assertIsNone(self.last_run("write-article"))

    def test_unknown_ability(self):
        with self.assertRaises(AbilityError):
            self.run_ability("nope", {"x": "y"})

    def test_kind(self):
        r = self.run_ability("search-web", {"keywords": "x"}, kind="trial",
                             search_fn=lambda *a: [{"title": "t", "url": "https://t.cn", "snippet": ""}])
        self.assertEqual(self.last_run("search-web")["type"], "trial")
        self.assertTrue(r["ok"])
        with self.assertRaises(AbilityError):
            self.run_ability("search-web", {"keywords": "x"}, kind="other", search_fn=lambda *a: [])

    def test_inputs_masked_in_record(self):
        r = self.run_ability("write-article", {"topic": "春天", "notes": "我的密钥是 " + BAILIAN_KEY, "words": 100},
                             client_factory=self.factory(FakeClient(reply="字" * 100)))
        self.assertTrue(r["ok"])
        rec = self.last_run("write-article")
        self.assertNoSecret(rec)
        self.assertIn("[已隐藏]", rec["inputs"]["notes"])

    def test_one_at_a_time(self):
        started, release = threading.Event(), threading.Event()

        def slow(args, folder, timeout):
            started.set()
            release.wait(5)
            (Path(folder) / "输出.png").write_bytes(png_bytes(512, 512))
            return 0, "OK"
        results = []
        t = threading.Thread(target=lambda: results.append(self.run_ability("draw-image", {"scene": "猫"}, runner=slow)))
        t.start()
        self.assertTrue(started.wait(5))
        try:
            with self.assertRaises(AbilityError) as ctx:
                self.run_ability("draw-image", {"scene": "狗"}, runner=slow)
            self.assertIn("正在", str(ctx.exception))
            # 别的卡不受影响
            r = self.run_ability("search-web", {"keywords": "x"},
                                 search_fn=lambda *a: [{"title": "t", "url": "https://t.cn", "snippet": ""}])
            self.assertTrue(r["ok"])
        finally:
            release.set()
            t.join(10)
        self.assertTrue(results and results[0]["ok"])
        # 跑完了可以再跑
        r = self.run_ability("draw-image", {"scene": "猫"}, runner=Cli.runner(self, write=("输出.png", png_bytes(512, 512))))
        self.assertTrue(r["ok"])

    def test_result_shape(self):
        r = self.run_ability("search-web", {"keywords": "x"},
                             search_fn=lambda *a: [{"title": "t", "url": "https://t.cn", "snippet": ""}])
        for key in ("run_id", "ok", "status", "reason", "how", "model", "seconds", "output", "next_model",
                    "platform_said", "files"):
            self.assertIn(key, r)
        self.assertRegex(r["run_id"], r"^\d{8}-\d{6}-[0-9a-f]{4}$")
        self.assertIsInstance(r["seconds"], (int, float))

    def test_base_layer_untouched(self):
        before = blocks.records("bailian", self.root)
        self.run_ability("write-article", {"topic": "春天", "words": 100},
                         client_factory=self.factory(FakeClient(reply="字" * 100)))
        self.assertEqual(blocks.records("bailian", self.root), before, "运行能力卡片不写积木块的检测记录")
        self.assertEqual(sorted(c["id"] for c in blocks.list_cards(self.root)), ["bailian", "codex", "web-search"])


# ---------- 第 2 轮补充（Codex 第 1 轮复查查出来的，见施工单“第 2 轮补充”） ----------

def _cli_card(card_id, output_file, output):
    card = json.loads(json.dumps(abilities.BUILTIN_ABILITIES["draw-image"]))
    card.update(id=card_id, title="命令行" + card_id, description="测试用")
    card["how"]["output_file"] = output_file
    card["how"]["prompt"] = "按要求写：{scene}。存成当前文件夹里的 " + output_file
    card["output"] = output
    return card


class Round2(Case):
    runner = Cli.runner

    def setUp(self):
        super().setUp()
        abilities.save_ability(_cli_card("cli-note", "输出.txt", {"type": "text", "check": {}}), self.root)
        abilities.save_ability(_cli_card("cli-file", "报告.md",
                                         {"type": "file", "check": {"extensions": ["md"]}}), self.root)
        self.outside = Path(self._tmp.name) / "别处"
        self.outside.mkdir()

    def folder_files(self, ability_id, r):
        folder = abilities.result_folder(ability_id, r["run_id"], self.root)
        return sorted(p for p in folder.rglob("*") if p.is_file()) if folder.exists() else []

    def assertFolderNoSecret(self, ability_id, r):
        for p in self.folder_files(ability_id, r):
            data = p.read_bytes()
            self.assertFalse(TAVILY_KEY.encode() in data or BAILIAN_KEY.encode() in data, f"{p.name} 里有密钥")

    # 1. 成果文件里不能有这次用的密钥
    def test_secret_not_in_text_result(self):
        r = self.run_ability("write-article", {"topic": "春天", "words": 800},
                             client_factory=self.factory(FakeClient(reply="字" * 600 + " " + BAILIAN_KEY)))
        self.assertTrue(r["ok"], r)
        self.assertEqual([p.name for p in self.folder_files("write-article", r)], ["结果.txt"])
        self.assertFolderNoSecret("write-article", r)
        self.assertNoSecret(r)

    def test_secret_not_in_list_result(self):
        r = self.run_ability("search-web", {"keywords": "x"},
                             search_fn=lambda *a: [{"title": "t " + TAVILY_KEY, "url": "https://t.cn",
                                                    "snippet": "s " + BAILIAN_KEY}])
        self.assertTrue(r["ok"], r)
        self.assertFolderNoSecret("search-web", r)
        self.assertNoSecret(r)

    def test_secret_not_in_file_result(self):
        r = self.run_ability("cli-file", {"scene": "x"},
                             runner=self.runner(write=("报告.md", ("报告正文 " + BAILIAN_KEY).encode())))
        self.assertFolderNoSecret("cli-file", r)
        self.assertNoSecret(r)

    # 2. 不跟着“文件链接”去拿别处的文件；运行前就在的图不拿（哪怕修改时间被改到以后）
    def test_link_in_image_folder_not_taken(self):
        target = self.outside / "外面.png"

        def run(args, folder, timeout):
            target.write_bytes(png_bytes(512, 512))  # 运行之后才出现，修改时间是新的
            os.symlink(target, self.images / "link.png")
            return 0, "OK"
        r = self.run_ability("draw-image", {"scene": "猫"}, runner=run)
        self.assertFalse(r["ok"])
        self.assertEqual(self.folder_files("draw-image", r), [])

    def test_link_in_work_folder_not_taken(self):
        target = self.outside / "外面.png"
        target.write_bytes(png_bytes(512, 512))

        def run(args, folder, timeout):
            os.symlink(target, Path(folder) / "输出.png")
            return 0, "OK"
        r = self.run_ability("draw-image", {"scene": "猫"}, runner=run)
        self.assertFalse(r["ok"])
        self.assertEqual(self.folder_files("draw-image", r), [])

    def test_old_image_with_future_time_not_taken(self):
        old = self.images / "old.png"
        old.write_bytes(png_bytes(512, 512))
        os.utime(old, (time.time() + 60, time.time() + 60))
        r = self.run_ability("draw-image", {"scene": "猫"}, runner=self.runner())
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "format")
        self.assertEqual(self.folder_files("draw-image", r), [])

    # 3. 不传 client_factory 时，真的能建出大模型客户端（120 秒、按服务商设思考开关）
    def test_default_client(self):
        from collab import providers
        made = []

        class FakeModelClient:
            def __init__(self, **kw):
                made.append(kw)

            def chat(self, messages, on_text=None, tools=None):
                return {"text": "字" * 100, "tool_calls": [], "tokens": None}
        with mock.patch.object(abilities_run, "ModelClient", FakeModelClient), \
                mock.patch("desktop.clerk.client.ModelClient", FakeModelClient):
            r = self.run_ability("write-article", {"topic": "春天", "words": 100})
        self.assertTrue(r["ok"], r)
        self.assertEqual(len(made), 1)
        kw = made[0]
        self.assertEqual((kw.get("base_url"), kw.get("model"), kw.get("api_key")),
                         (BAILIAN_URL, "qwen3.7-plus", BAILIAN_KEY))
        self.assertEqual(kw.get("timeout"), 120)
        self.assertEqual(bool(kw.get("thinking_off")), bool((providers.find("bailian") or {}).get("thinking_off")))

    # 4. 命令行工具交回文字：读文件里的正文
    def test_cli_text_card(self):
        r = self.run_ability("cli-note", {"scene": "x"}, runner=self.runner(write=("输出.txt", "一段正文".encode())))
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["output"], "一段正文")
        self.assertEqual([p.name for p in self.folder_files("cli-note", r)], ["结果.txt"])
        self.assertEqual(self.folder_files("cli-note", r)[0].read_text(encoding="utf-8"), "一段正文")

    # 5. 拿到了结果但格式不对：结果照样存（status 还是 format）；什么都没拿到才不建文件夹
    def test_format_result_still_saved(self):
        r = self.run_ability("write-article", {"topic": "春天", "words": 800},
                             client_factory=self.factory(FakeClient(reply="太短了")))
        self.assertEqual(r["status"], "format")
        self.assertEqual(r["files"], ["结果.txt"])
        self.assertEqual(self.folder_files("write-article", r)[0].read_text(encoding="utf-8"), "太短了")

        r = self.run_ability("draw-image", {"scene": "猫"}, runner=self.runner(write=("输出.png", png_bytes(100, 100))))
        self.assertEqual(r["status"], "format")
        self.assertEqual(r["files"], ["输出.png"])
        self.assertEqual(abilities.image_size(self.folder_files("draw-image", r)[0]), (100, 100))

        r = self.run_ability("search-web", {"keywords": "x"}, search_fn=lambda *a: [])
        self.assertEqual(r["status"], "format")
        self.assertEqual(r["files"], [])
        self.assertFalse(abilities.result_folder("search-web", r["run_id"], self.root).exists())

    # 6. 存成果失败：不抛出去，算 error、写记录、能再跑
    def test_save_failure_is_error_and_recorded(self):
        blocked = abilities.home(self.root) / abilities.RESULT_DIR / "write-article"
        blocked.parent.mkdir(parents=True, exist_ok=True)
        blocked.write_text("挡路的文件")
        before = len(abilities.runs("write-article", self.root))
        for _ in range(2):
            r = self.run_ability("write-article", {"topic": "春天", "words": 100},
                                 client_factory=self.factory(FakeClient(reply="字" * 100)))
            self.assertFalse(r["ok"])
            self.assertEqual(r["status"], "error")
            self.assertRegex(r["reason"], r"[一-鿿]")
        self.assertEqual(len(abilities.runs("write-article", self.root)), before + 2)
        self.assertEqual(self.last_run("write-article")["status"], "error")


if __name__ == "__main__":
    unittest.main()
