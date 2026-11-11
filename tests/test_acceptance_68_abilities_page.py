"""68 号单：能力层页面——本机服务 /api/abilities/*、规则层 AbilitiesCore（node 检查）、页面结构。

依据：docs/积木库/施工单/68-能力层页面.md。
本机服务用假积木块：假搜索、假大模型、假命令行工具（写出真的 PNG 小图），不真联网、不真跑 Codex。
密钥只用 Mac 窗口交来的那份（blocks_saved_keys），不出现在任何返回内容和文件里（BlocksCase 收尾时查）。
"""
import json
import os
import re
import shutil
import struct
import subprocess
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

from acceptance_helpers import wait_until
from test_acceptance_55_blocks_server import KEY, BlocksCase
from collab import abilities, abilities_run, blocks

HERE = Path(__file__).resolve().parent
STATIC = HERE.parent / "desktop" / "static"
BAILIAN_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODELS = ["qwen3.7-plus", "qwen3.6-plus", "qwen-flash"]


def png_bytes(w, h):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class FakeLlm:
    """假大模型：reply 是要交回的文字；error 不为空就抛它。记下每次用的模型和密钥。"""

    def __init__(self):
        self.reply, self.error, self.seen = "字" * 100, None, []

    def factory(self, base_url, model, key):
        self.seen.append((base_url, model, key))
        owner = self

        class Client:
            def chat(self, messages, on_text=None, tools=None):
                if owner.error:
                    raise owner.error
                return {"text": owner.reply, "tool_calls": [], "tokens": None}
        return Client()


class AbilityCase(BlocksCase):
    def setUp(self):
        super().setUp()
        self.home.mkdir(parents=True, exist_ok=True)
        blocks.save_card(blocks.search_card("tavily"), self.home)
        blocks.save_card({"id": "bailian", "kind": "llm_api", "name": "阿里百炼", "provider": "bailian",
                          "base_url": BAILIAN_URL, "key_account": "bailian"}, self.home)
        blocks.save_card(dict(blocks.BUILTIN["codex"], source="builtin"), self.home)
        self.server.blocks_saved_keys = {"bailian": KEY, "tavily": KEY}
        self.llm = FakeLlm()
        self.server.blocks_client_factory = self.llm.factory
        self.searches = []
        self.search_items = [{"title": "公园 A", "url": "https://a.cn", "snippet": "好玩"}]
        self.release = None  # 设成 threading.Event 时，假命令行工具等它

        def search(query, key, max_results, time_range):
            self.searches.append((query, key, max_results, time_range))
            return list(self.search_items)
        self.server.abilities_search_fn = search
        self.images = Path(self.tmp) / "codex_images"
        self.images.mkdir()
        self.server.abilities_codex_images = self.images
        self.picture = png_bytes(512, 300)

        def runner(args, folder, timeout):
            if self.release is not None:
                self.release.wait(10)
            (Path(folder) / "输出.png").write_bytes(self.picture)
            return 0, "OK"
        self.server.abilities_runner = runner
        self.opened = []
        self.server.blocks_open = self.opened.append

    def models_known(self, models=MODELS):
        blocks.add_record("bailian", {"type": "check", "ok": True, "status": "ok", "models": list(models)}, self.home)

    def run_card(self, ability_id, inputs, **extra):
        status, data = self.bpost("/api/abilities/run", dict(id=ability_id, inputs=inputs, **extra))
        self.assertEqual(status, 200, data)
        job = self.job(data["job"])
        self.assertFalse(job["error"], job)
        return job["result"]

    def raw_get(self, path, **query):
        query.setdefault("token", "口令")
        url = self.base + path + "?" + urllib.parse.urlencode(query)
        try:
            with urllib.request.urlopen(url, timeout=15) as answer:
                return answer.status, answer.headers.get("Content-Type", ""), answer.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read()


# ---------- 列表 ----------

class ListTest(AbilityCase):
    def test_builtin_written_on_first_open(self):
        self.assertFalse((self.home / abilities.ABILITY_DIR).exists())
        status, data = self.bget("/api/abilities/list")
        self.assertEqual(status, 200, data)
        self.assertEqual([a["id"] for a in data["abilities"]], ["draw-image", "search-web", "write-article"])
        self.assertEqual(data["broken"], [])
        by = {a["id"]: a for a in data["abilities"]}
        art = by["write-article"]
        self.assertEqual((art["title"], art["block"], art["block_name"], art["model"], art["kind"], art["output_type"]),
                         ("写文章", "bailian", "阿里百炼", "qwen3.7-plus", "llm_api", "text"))
        self.assertEqual((art["status"], art["status_reason"], art["last_time"]), ("ok", "", ""))
        self.assertIsNone(by["search-web"]["model"])
        self.assertEqual(by["search-web"]["block_name"], "联网搜索 · Tavily")
        self.assertEqual(by["draw-image"]["model"], blocks.BUILTIN["codex"]["model"], "卡上没写模型，用积木块的")
        self.assertEqual(by["draw-image"]["output_type"], "image")

    def test_needs_token(self):
        status, _ = self.get("/api/abilities/list")
        self.assertEqual(status, 403)
        status, _ = self.post("/api/abilities/run", {"id": "write-article", "inputs": {"topic": "x"}}, token="错的")
        self.assertEqual(status, 403)

    def test_unusable_when_block_removed_or_check_failed(self):
        self.bget("/api/abilities/list")
        blocks.remove_card("bailian", self.home)
        art = {a["id"]: a for a in self.bget("/api/abilities/list")[1]["abilities"]}["write-article"]
        self.assertEqual(art["status"], "unusable")
        self.assertIn("移出基础层", art["status_reason"])
        self.assertEqual(art["block_name"], "bailian", "积木块不在了用编号")

        blocks.add_record("codex", {"type": "check", "ok": False, "status": "fail", "reason": "没登录"}, self.home)
        draw = {a["id"]: a for a in self.bget("/api/abilities/list")[1]["abilities"]}["draw-image"]
        self.assertEqual(draw["status"], "unusable")
        self.assertIn("检测没通过", draw["status_reason"])

    def test_quota_or_listed_check_still_usable(self):
        for status in ("quota", "listed"):
            blocks.add_record("bailian", {"type": "check", "ok": False, "status": status}, self.home)
            art = {a["id"]: a for a in self.bget("/api/abilities/list")[1]["abilities"]}["write-article"]
            self.assertEqual(art["status"], "ok", status)

    def test_failed_after_bad_run(self):
        self.llm.reply = "太短了"
        result = self.run_card("write-article", {"topic": "春天", "words": 800})
        self.assertEqual(result["status"], "format")
        art = {a["id"]: a for a in self.bget("/api/abilities/list")[1]["abilities"]}["write-article"]
        self.assertEqual(art["status"], "failed")
        self.assertEqual(art["status_reason"], result["reason"])
        self.assertTrue(art["last_time"])

    def test_broken_card_listed_separately(self):
        self.bget("/api/abilities/list")
        (self.home / abilities.ABILITY_DIR / "bad.json").write_text("{坏的", encoding="utf-8")
        data = self.bget("/api/abilities/list")[1]
        self.assertEqual([b["id"] for b in data["broken"]], ["bad"])
        self.assertEqual(len(data["abilities"]), 3)


# ---------- 详情 ----------

class DetailTest(AbilityCase):
    def test_detail(self):
        self.models_known()
        status, d = self.bget("/api/abilities/detail", id="write-article")
        self.assertEqual(status, 200, d)
        self.assertEqual(d["ability"]["id"], "write-article")
        self.assertEqual(d["block"], {"id": "bailian", "name": "阿里百炼", "exists": True, "kind": "llm_api", "model": ""})
        self.assertEqual(d["models"], MODELS)
        self.assertEqual((d["status"], d["example"], d["runs"], d["running"], d["job"]), ("ok", None, [], False, ""))

    def test_runs_newest_first_max_10(self):
        for i in range(12):
            abilities.add_run("write-article", {"type": "run", "run_id": f"20261111-1200{i:02d}-abcd", "ok": i % 2 == 0,
                                                "status": "ok" if i % 2 == 0 else "format", "reason": f"第{i}次"},
                              self.home)
        d = self.bget("/api/abilities/detail", id="write-article")[1]
        self.assertEqual(len(d["runs"]), 10)
        self.assertEqual(d["runs"][0]["reason"], "第11次")
        self.assertEqual(d["runs"][-1]["reason"], "第2次")

    def test_example(self):
        self.bget("/api/abilities/list")
        abilities.add_run("write-article", {"type": "trial", "run_id": "20261111-120000-abcd", "ok": True,
                                            "status": "ok", "reason": "成功完成"}, self.home)
        card = abilities.load_ability("write-article", self.home)
        card["example_run"] = "20261111-120000-abcd"
        abilities.save_ability(card, self.home)
        d = self.bget("/api/abilities/detail", id="write-article")[1]
        self.assertEqual(d["example"]["run_id"], "20261111-120000-abcd")

    def test_no_models_for_non_api(self):
        d = self.bget("/api/abilities/detail", id="draw-image")[1]
        self.assertEqual(d["models"], [])
        self.assertEqual(d["block"]["kind"], "cli")

    def test_bad_or_missing_id(self):
        self.assertEqual(self.bget("/api/abilities/detail", id="../x")[0], 400)
        self.assertEqual(self.bget("/api/abilities/detail", id="nope")[0], 404)

    def test_block_gone(self):
        self.bget("/api/abilities/list")
        blocks.remove_card("bailian", self.home)
        d = self.bget("/api/abilities/detail", id="write-article")[1]
        self.assertEqual(d["block"]["exists"], False)
        self.assertEqual(d["status"], "unusable")


# ---------- 用一次 ----------

class RunTest(AbilityCase):
    def test_write_article(self):
        result = self.run_card("write-article", {"topic": "春天", "words": 100})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["output"], "字" * 100)
        self.assertEqual(result["model"], "qwen3.7-plus")
        self.assertEqual(self.llm.seen, [(BAILIAN_URL, "qwen3.7-plus", KEY)], "用 Mac 窗口交来的密钥")
        rec = abilities.runs("write-article", self.home)[-1]
        self.assertEqual((rec["type"], rec["status"]), ("run", "ok"))

    def test_search_and_draw(self):
        result = self.run_card("search-web", {"keywords": "上海 公园"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.searches[0][:2], ("上海 公园", KEY))
        result = self.run_card("draw-image", {"scene": "猫"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["files"], ["输出.png"])

    def test_page_key_ignored(self):
        self.run_card("write-article", {"topic": "春天", "words": 100}, api_key="sk-pagekey1234567890abcd")
        self.assertEqual(self.llm.seen[-1][2], KEY, "页面带的密钥不理")

    def test_no_saved_key_is_block(self):
        self.server.blocks_saved_keys = {}
        result = self.run_card("write-article", {"topic": "春天", "words": 100})
        self.assertEqual(result["status"], "block")
        self.assertEqual(self.llm.seen, [])

    def test_bad_inputs_rejected_before_start(self):
        for inputs in ({}, {"topic": "春天", "words": 5}, {"topic": "x" * 201}):
            status, data = self.bpost("/api/abilities/run", {"id": "write-article", "inputs": inputs})
            self.assertEqual(status, 400, inputs)
            self.assertRegex(data["error"], r"[\u4e00-\u9fff]")
            self.assertNotIn("job", data)
        self.assertEqual(abilities.runs("write-article", self.home), [], "没开始跑不写记录")

    def test_bad_or_missing_id(self):
        self.assertEqual(self.bpost("/api/abilities/run", {"id": "../x", "inputs": {}})[0], 400)
        self.assertEqual(self.bpost("/api/abilities/run", {"id": "nope", "inputs": {}})[0], 404)

    def test_backup_model_only(self):
        status, data = self.bpost("/api/abilities/run", {"id": "write-article", "inputs": {"topic": "春天", "words": 100},
                                                         "model": "qwen-max"})
        self.assertEqual(status, 400)
        self.assertIn("备用模型", data["error"])
        result = self.run_card("write-article", {"topic": "春天", "words": 100}, model="qwen3.6-plus")
        self.assertEqual(result["model"], "qwen3.6-plus")
        self.assertEqual(abilities.load_ability("write-article", self.home)["model"], "qwen3.7-plus", "只管这一次")

    def test_model_refused_offers_backup_without_switching(self):
        from desktop.clerk.client import ModelError
        err = ModelError("模型没开通")
        err.code = 403
        self.llm.error = err
        result = self.run_card("write-article", {"topic": "春天", "words": 100})
        self.assertEqual(result["status"], "model")
        self.assertEqual(result["next_model"], "qwen3.6-plus")
        self.assertEqual([m for _, m, _ in self.llm.seen], ["qwen3.7-plus"], "不自动换")

    def test_one_at_a_time_and_resume(self):
        self.release = threading.Event()
        try:
            status, data = self.bpost("/api/abilities/run", {"id": "draw-image", "inputs": {"scene": "猫"}})
            self.assertEqual(status, 200, data)
            job_id = data["job"]
            d = wait_until(lambda: (lambda x: x if x["running"] else None)(
                self.bget("/api/abilities/detail", id="draw-image")[1]), 10, "一直没显示正在跑")
            self.assertEqual(d["job"], job_id, "重新打开详情能接着跟")
            status, data = self.bpost("/api/abilities/run", {"id": "draw-image", "inputs": {"scene": "狗"}})
            self.assertEqual(status, 409)
            self.assertIn("正在用", data["error"])
            status, data = self.bpost("/api/abilities/remove", {"id": "draw-image"})
            self.assertEqual(status, 409)
            # 别的卡不受影响
            self.assertTrue(self.run_card("search-web", {"keywords": "x"})["ok"])
        finally:
            self.release.set()
        self.assertTrue(self.job(job_id)["result"]["ok"])
        d = self.bget("/api/abilities/detail", id="draw-image")[1]
        self.assertEqual((d["running"], d["job"]), (False, ""))

    def test_is_running_helper(self):
        self.assertFalse(abilities_run.is_running("draw-image"))


# ---------- 换模型 ----------

class ModelTest(AbilityCase):
    def change(self, **body):
        body.setdefault("id", "write-article")
        return self.bpost("/api/abilities/model", body)

    def test_change(self):
        self.models_known()
        status, data = self.change(model="qwen3.6-plus", backup_models=["qwen3.7-plus", "qwen-flash"])
        self.assertEqual(status, 200, data)
        card = abilities.load_ability("write-article", self.home)
        self.assertEqual((card["model"], card["backup_models"]), ("qwen3.6-plus", ["qwen3.7-plus", "qwen-flash"]))
        self.assertEqual(card["how"], abilities.BUILTIN_ABILITIES["write-article"]["how"], "别的字段原样")
        self.assertEqual(data["ability"]["model"], "qwen3.6-plus")
        status, _ = self.change(model="qwen3.7-plus", backup_models=[])
        self.assertEqual(status, 200)
        self.assertEqual(abilities.load_ability("write-article", self.home)["backup_models"], [])

    def test_only_from_list(self):
        self.models_known()
        self.bget("/api/abilities/list")  # 先让程序写进预置卡片（施工方 2026-11-11 指出：原来没调接口就读卡片）
        before = abilities.load_ability("write-article", self.home)
        for body in ({"model": "qwen-max", "backup_models": []},
                     {"model": "qwen3.7-plus", "backup_models": ["qwen-max"]},
                     {"model": "qwen3.7-plus", "backup_models": ["qwen3.7-plus"]},
                     {"model": "qwen3.7-plus", "backup_models": ["qwen3.6-plus", "qwen3.6-plus"]},
                     {"model": "qwen3.7-plus", "backup_models": ["qwen3.6-plus", "qwen-flash", "qwen3.7-plus"]},
                     {"model": "qwen3.7-plus", "backup_models": "qwen3.6-plus"}):
            status, data = self.change(**body)
            self.assertEqual(status, 400, body)
            self.assertRegex(data["error"], r"[\u4e00-\u9fff]")
        self.assertEqual(abilities.load_ability("write-article", self.home), before, "没改成就不动卡片")

    def test_no_list_yet(self):
        status, data = self.change(model="qwen3.6-plus", backup_models=[])
        self.assertEqual(status, 409)
        self.assertIn("阿里百炼", data["error"])

    def test_not_api_card(self):
        self.models_known()
        for ability_id in ("draw-image", "search-web"):
            status, data = self.change(id=ability_id, model="qwen3.6-plus", backup_models=[])
            self.assertEqual(status, 400, ability_id)
            self.assertIn("不能换模型", data["error"])


# ---------- 移出、成果文件、在访达里打开 ----------

class RemoveFileRevealTest(AbilityCase):
    def test_remove_keeps_records_and_results(self):
        result = self.run_card("draw-image", {"scene": "猫"})
        status, data = self.bpost("/api/abilities/remove", {"id": "draw-image"})
        self.assertEqual(status, 200, data)
        self.assertFalse((self.home / abilities.ABILITY_DIR / "draw-image.json").exists())
        self.assertEqual(len(abilities.runs("draw-image", self.home)), 1, "记录留着")
        self.assertTrue(abilities.result_folder("draw-image", result["run_id"], self.home).is_dir(), "成果留着")
        ids = [a["id"] for a in self.bget("/api/abilities/list")[1]["abilities"]]
        self.assertNotIn("draw-image", ids, "移出后不会被预置卡片补回来")
        status, ctype, body = self.raw_get("/api/abilities/file", id="draw-image", run=result["run_id"], name="输出.png")
        self.assertEqual((status, body), (200, self.picture), "移出后成果还能看")
        self.assertEqual(self.bpost("/api/abilities/remove", {"id": "draw-image"})[0], 404)

    def test_file(self):
        result = self.run_card("draw-image", {"scene": "猫"})
        status, ctype, body = self.raw_get("/api/abilities/file", id="draw-image", run=result["run_id"], name="输出.png")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("image/png"), ctype)
        self.assertEqual(body, self.picture)
        text = self.run_card("write-article", {"topic": "春天", "words": 100})
        status, ctype, body = self.raw_get("/api/abilities/file", id="write-article", run=text["run_id"], name="结果.txt")
        self.assertEqual(status, 200)
        self.assertTrue(ctype.startswith("text/plain"), ctype)
        self.assertEqual(body.decode("utf-8"), "字" * 100)

    def test_file_refuses_outside(self):
        result = self.run_card("draw-image", {"scene": "猫"})
        run = result["run_id"]
        folder = abilities.result_folder("draw-image", run, self.home)
        secret = Path(self.tmp) / "外面.txt"
        secret.write_text("不该被读到", encoding="utf-8")
        os.symlink(secret, folder / "link.txt")
        cases = [
            (dict(id="draw-image", run=run, name="../../../外面.txt"), 400),
            (dict(id="draw-image", run=run, name=".hidden"), 400),
            (dict(id="draw-image", run=run, name="a/b.png"), 400),
            (dict(id="draw-image", run="../x", name="输出.png"), 400),
            (dict(id="../x", run=run, name="输出.png"), 400),
            (dict(id="draw-image", run=run, name="link.txt"), 404),
            (dict(id="draw-image", run=run, name="没有.png"), 404),
            (dict(id="write-article", run=run, name="输出.png"), 404),
        ]
        for query, want in cases:
            status, _, body = self.raw_get("/api/abilities/file", **query)
            self.assertEqual(status, want, query)
            self.assertNotIn("不该被读到".encode("utf-8"), body)
        self.assertEqual(self.raw_get("/api/abilities/file", token="错的", id="draw-image", run=run, name="输出.png")[0], 403)

    def test_reveal(self):
        result = self.run_card("draw-image", {"scene": "猫"})
        status, data = self.bpost("/api/abilities/reveal", {"id": "draw-image", "run": result["run_id"],
                                                            "path": "/etc"})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.opened, [str(abilities.result_folder("draw-image", result["run_id"], self.home))],
                         "只开程序算出来的成果文件夹，页面给的路径不理")
        self.assertEqual(self.bpost("/api/abilities/reveal", {"id": "draw-image", "run": "20261111-000000-abcd"})[0], 404)
        self.assertEqual(self.bpost("/api/abilities/reveal", {"id": "draw-image", "run": "../x"})[0], 400)
        self.assertEqual(len(self.opened), 1)


# ---------- 原有接口照旧 ----------

class OldApiTest(AbilityCase):
    def test_blocks_list_unchanged(self):
        self.bget("/api/abilities/list")
        status, data = self.bget("/api/blocks/list")
        self.assertEqual(status, 200)
        self.assertEqual(sorted(b["id"] for b in data["blocks"]), ["bailian", "codex", "web-search"],
                         "能力卡片不混进基础层")

    def test_unknown_ability_path(self):
        self.assertEqual(self.bget("/api/abilities/nope")[0], 404)
        self.assertEqual(self.bpost("/api/abilities/nope", {})[0], 404)


# ---------- 规则层（node） ----------

class RulesTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node，跳过页面规则层检查")
    def test_rules(self):
        proc = subprocess.run(["node", str(HERE / "abilities_page_check.js")], capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)

    @unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
    def test_old_page_rules_still_pass(self):
        for name in ("blocks_page_check.js", "blocks_detail_check.js"):
            proc = subprocess.run(["node", str(HERE / name)], capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, name + ": " + (proc.stderr or proc.stdout))


# ---------- 页面结构和纪律 ----------

def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


class PageTest(unittest.TestCase):
    def test_left_column(self):
        html = read("blocks.html")
        self.assertIn('id="abilitiesList"', html)
        side = html[html.index('id="side"'):html.index("</aside>")]
        self.assertIn("能力层", side)
        self.assertLess(side.index("基础层"), side.index("能力层"), "能力层在基础层下面")
        self.assertIn('id="abilitiesList"', side)
        scripts = re.findall(r'<script src="([^"]+)"', html)
        self.assertIn("/static/abilities.js", scripts)
        self.assertIn("/static/blocks.js", scripts)

    def test_detail_area(self):
        html = read("blocks.html")
        for element in ('id="abilityDetail"', 'id="abilityBack"', 'id="abilityTitle"', 'id="abilityStatus"',
                        'id="abilityForm"', 'id="abilityRun"', 'id="abilityResult"', 'id="abilityRuns"',
                        'id="abilityPrompt"', 'id="abilityRemove"'):
            self.assertIn(element, html)
        section = re.search(r"<section[^>]*id=\"abilityDetail\"[^>]*>", html).group(0)
        self.assertIn("hidden", section, "一开始显示聊天，能力卡片详情藏着")
        self.assertRegex(html, r"<details[^>]*id=\"abilityPrompt\"[^>]*>\s*<summary>交代原文</summary>")
        for words in ("返回聊天", "移出能力层"):
            self.assertIn(words, html)
        stage = html[html.index('<main class="stage">'):]
        self.assertIn('id="abilityDetail"', stage, "详情在中间区域")

    def test_endpoints_and_switch(self):
        script = read("abilities.js")
        for part in ("/api/abilities/list", "/api/abilities/detail", "/api/abilities/run", "/api/abilities/model",
                     "/api/abilities/remove", "/api/abilities/reveal", "/api/abilities/file", "/api/blocks/job",
                     "window.AbilitiesPage", "BlocksStage", "navigator.clipboard", "rel=\"noopener\"",
                     "换这个试", "先不试", "在访达里打开", "复制", "还没用过", "还没有试做示例"):
            self.assertIn(part, script, part)
        self.assertRegex(script, r"window\.confirm\(\s*AbilitiesCore\.removeConfirm\(", "移出前先确认")
        blocks_js = read("blocks.js")
        self.assertIn("window.BlocksStage", blocks_js)
        self.assertGreaterEqual(blocks_js.count("AbilitiesPage"), 3, "打开基础层详情、坏卡详情、回到聊天时都关能力卡片详情")

    def test_page_rules(self):
        script = read("abilities.js")
        self.assertNotRegex(script, r"localStorage|sessionStorage|indexedDB|document\.cookie")
        self.assertNotIn("console.log", script)
        self.assertNotRegex(script, r"type=\\?\"password\\?\"|api_key|apiKey", "能力层页面没有密钥框、不带密钥")
        self.assertIn("module.exports", script, "规则层能给 node 检查")


# ---------- 第 2 轮补充（Codex 第 1 轮复查查出） ----------

class Round2Test(AbilityCase):
    def outside_run(self):
        """外面一个文件夹，长得像一次运行的成果：里面有图和 proof.txt。"""
        outside = Path(self.tmp) / "外面"
        (outside / "20261111-120000-ab12").mkdir(parents=True)
        for folder in (outside, outside / "20261111-120000-ab12"):
            (folder / "proof.txt").write_text("不该被读到", encoding="utf-8")
            (folder / "输出.png").write_bytes(self.picture)
        return outside

    def refused(self, ability_id, run):
        for name in ("proof.txt", "输出.png"):
            status, _, body = self.raw_get("/api/abilities/file", id=ability_id, run=run, name=name)
            self.assertEqual(status, 404, (ability_id, run, name))
            self.assertNotIn("不该被读到".encode("utf-8"), body)
            self.assertNotEqual(body, self.picture)
        status, data = self.bpost("/api/abilities/reveal", {"id": ability_id, "run": run})
        self.assertEqual(status, 404, data)
        self.assertEqual(self.opened, [], "链接过去的文件夹不能在访达里打开")

    def test_run_folder_link_refused(self):
        self.bget("/api/abilities/list")
        outside = self.outside_run()
        parent = self.home / abilities.RESULT_DIR / "draw-image"
        parent.mkdir(parents=True)
        os.symlink(outside / "20261111-120000-ab12", parent / "20261111-120000-ab12")
        self.refused("draw-image", "20261111-120000-ab12")

    def test_ability_folder_link_refused(self):
        self.bget("/api/abilities/list")
        outside = self.outside_run()
        (self.home / abilities.RESULT_DIR).mkdir(parents=True, exist_ok=True)
        os.symlink(outside, self.home / abilities.RESULT_DIR / "search-web")
        self.refused("search-web", "20261111-120000-ab12")

    def test_result_root_link_refused(self):
        result = self.run_card("draw-image", {"scene": "猫"})
        root = self.home / abilities.RESULT_DIR
        moved = Path(self.tmp) / "挪走的成果"
        root.rename(moved)
        os.symlink(moved, root)
        status, _, body = self.raw_get("/api/abilities/file", id="draw-image", run=result["run_id"], name="输出.png")
        self.assertEqual(status, 404)
        self.assertEqual(self.bpost("/api/abilities/reveal", {"id": "draw-image", "run": result["run_id"]})[0], 404)
        self.assertEqual(self.opened, [])

    def test_home_itself_behind_link_still_works(self):
        """积木库资料夹本身在链接后面（比如 Mac 的临时文件夹）是正常情况，不能因此拒绝。"""
        real = Path(self.tmp) / "真的积木库"
        self.home.rename(real)
        link = Path(self.tmp) / "积木库链接"
        os.symlink(real, link)
        self.home = link
        os.environ["SUPER_AGENT_BLOCKS_HOME"] = str(link)
        result = self.run_card("draw-image", {"scene": "猫"})
        status, _, body = self.raw_get("/api/abilities/file", id="draw-image", run=result["run_id"], name="输出.png")
        self.assertEqual((status, body), (200, self.picture))
        self.assertEqual(self.bpost("/api/abilities/reveal", {"id": "draw-image", "run": result["run_id"]})[0], 200)
        self.assertEqual(len(self.opened), 1)

    def test_detail_gives_job_inputs(self):
        self.release = threading.Event()
        try:
            status, data = self.bpost("/api/abilities/run", {"id": "draw-image", "inputs": {"scene": "猫"}})
            self.assertEqual(status, 200, data)
            job_id = data["job"]
            d = self.bget("/api/abilities/detail", id="draw-image")[1]
            self.assertEqual(d["job"], job_id, "任务开了还没跑完，不管 running 是不是已经为真，都给任务编号")
            self.assertEqual(d["job_inputs"], {"scene": "猫"}, "重新打开详情时拿得到这次的输入，备用模型重试用它")
        finally:
            self.release.set()
        self.job(job_id)
        d = self.bget("/api/abilities/detail", id="draw-image")[1]
        self.assertEqual((d["job"], d["job_inputs"]), ("", {}))
        d = self.bget("/api/abilities/detail", id="write-article")[1]
        self.assertEqual(d["job_inputs"], {})

    def test_detail_block_model(self):
        d = self.bget("/api/abilities/detail", id="draw-image")[1]
        self.assertEqual(d["block"]["model"], blocks.BUILTIN["codex"]["model"], "命令行卡片显示积木块的模型")
        d = self.bget("/api/abilities/detail", id="search-web")[1]
        self.assertEqual(d["block"]["model"], "")
        self.bget("/api/abilities/list")
        blocks.remove_card("codex", self.home)
        d = self.bget("/api/abilities/detail", id="draw-image")[1]
        self.assertEqual(d["block"]["model"], "")

    def test_page_round2(self):
        script = read("abilities.js")
        self.assertIn("job_inputs", script, "恢复跟踪时用这次任务的输入")
        self.assertIn("inputsView", script[script.index("module.exports"):], "试做示例显示那次填的内容")
        self.assertNotRegex(script, r"alert\([^)]*problem", "坏卡片进详情显示原因，不弹窗")


if __name__ == "__main__":
    unittest.main()
