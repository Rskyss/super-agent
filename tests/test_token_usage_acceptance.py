"""token 用量 A 卡验收测试（Claude 写；**施工方不许改**，补充测试请另写 test_token_usage_extra*.py）。

依据 docs/token用量/方案.md、docs/token用量/施工单-A.md。
测试绝不读本机真实的 ~/.codex：每个用例把 tests/token_samples 里的样本复制到临时目录，并把 CODEX_HOME 指过去。
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from acceptance_helpers import FAKE_CODEX, GOOD_PLAN, ProjectCase, needs_node, run_node
from collab.flow import read_flow, record

HERE = Path(__file__).resolve().parent
SAMPLES = HERE / "token_samples"
MAIN = "11111111-2222-7333-8444-555555555555"
CHILD = "11111111-2222-7333-8444-666666666666"
HEADER = (SAMPLES / "live_header.log").read_text(encoding="utf-8")
NORMAL = {"total": 1035411, "input": 1019589, "cached": 897152, "output": 15822, "reasoning": 8953}


def put(home: Path, sample: str, sid: str = MAIN, stamp: str = "08-17-57") -> Path:
    day = home / "sessions" / "2026" / "11" / "17"
    day.mkdir(parents=True, exist_ok=True)
    dst = day / f"rollout-2026-11-17T{stamp}-{sid}.jsonl"
    shutil.copy(SAMPLES / sample, dst)
    return dst


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "codex"
        (self.home / "sessions").mkdir(parents=True)
        patcher = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)


# ---------------- A1：读取模块 ----------------

class ReadModule(Home):
    def test_session_id_from_header(self):
        from collab.token_usage import session_id_from_text
        self.assertEqual(session_id_from_text(HEADER), MAIN)
        self.assertEqual(session_id_from_text("没有头部的输出\n结论：通过"), "")

    def test_codex_home_follows_the_environment(self):
        from collab.token_usage import codex_home
        self.assertEqual(Path(codex_home()), self.home)

    def test_find_session_file(self):
        from collab.token_usage import find_session_file
        path = put(self.home, "normal.jsonl")
        self.assertEqual(Path(find_session_file(MAIN)), path)
        self.assertIsNone(find_session_file("99999999-0000-7000-8000-000000000000"))

    def test_read_total_takes_the_last_cumulative_record(self):
        from collab.token_usage import read_total
        got = read_total(put(self.home, "normal.jsonl"))
        self.assertTrue(got["ok"])
        for key, value in NORMAL.items():
            self.assertEqual(got[key], value, key)

    def test_half_written_last_line_is_ignored(self):
        from collab.token_usage import read_total
        got = read_total(put(self.home, "half_line.jsonl"))
        self.assertTrue(got["ok"])
        self.assertEqual(got["total"], 67278)

    def test_unreadable_cases_say_why_and_never_raise(self):
        from collab.token_usage import read_total
        for sample in ("no_usage.jsonl", "bad_format.jsonl"):
            got = read_total(put(self.home, sample))
            self.assertFalse(got["ok"], sample)
            self.assertTrue(got["reason"].strip(), sample)
        for path in (self.home / "没有这个文件.jsonl", self.home):   # 文件不存在、给的是文件夹
            got = read_total(path)
            self.assertFalse(got["ok"])
            self.assertTrue(got["reason"].strip())

    def test_usage_from_text_adds_child_sessions_separately(self):
        from collab.token_usage import usage_from_text
        put(self.home, "normal.jsonl")
        put(self.home, "child.jsonl", sid=CHILD, stamp="08-17-58")
        got = usage_from_text(HEADER)
        self.assertTrue(got["ok"])
        self.assertEqual(got["total"], NORMAL["total"], "主数字只算主会话")
        self.assertEqual(got["children"], 20428, "辅助会话单独记，归“其他”")

    def test_usage_from_text_without_session_id(self):
        from collab.token_usage import usage_from_text
        got = usage_from_text("没有头部的输出")
        self.assertFalse(got["ok"])
        self.assertIn("会话编号", got["reason"])

    def test_usage_from_text_when_session_file_is_missing(self):
        from collab.token_usage import usage_from_text
        got = usage_from_text(HEADER)
        self.assertFalse(got["ok"])
        self.assertTrue(got["reason"].strip())

    def test_usage_from_log(self):
        from collab.token_usage import usage_from_log
        put(self.home, "normal.jsonl")
        log = self.tmp / "实时输出.log"
        log.write_text(HEADER + "很多很多输出\n" * 2000, encoding="utf-8")
        self.assertEqual(usage_from_log(log)["total"], NORMAL["total"])
        self.assertFalse(usage_from_log(self.tmp / "没有.log")["ok"])

    def test_new_tokens_matches_what_codex_prints(self):
        from collab.token_usage import new_tokens
        self.assertEqual(new_tokens(NORMAL), 138259, "新输入 + 输出，等于 Codex 打印的 tokens used")

    def test_sum_tokens(self):
        from collab.token_usage import sum_tokens
        a = dict(NORMAL, ok=True, children=0)
        b = dict(NORMAL, ok=True, children=20428)
        bad = {"ok": False, "reason": "找不到会话记录"}
        got = sum_tokens([a, b, bad])
        self.assertTrue(got["ok"])
        self.assertEqual(got["total"], 2 * NORMAL["total"])
        self.assertEqual(got["cached"], 2 * NORMAL["cached"])
        self.assertEqual(got["children"], 20428)
        self.assertEqual(got["missing"], 1)
        self.assertIsNone(sum_tokens([]))
        none_ok = sum_tokens([bad])
        self.assertFalse(none_ok["ok"])
        self.assertEqual(none_ok["missing"], 1)

    def test_big_session_file_is_read_from_the_tail_quickly(self):
        """会话记录最大约 3.7 MB：最后一条累计后面还跟着 600 KB 别的内容，也要找对、要快。"""
        from collab.token_usage import read_total
        day = self.home / "sessions" / "2026" / "11" / "17"
        day.mkdir(parents=True, exist_ok=True)
        path = day / f"rollout-2026-11-17T08-17-57-{MAIN}.jsonl"
        lines = (SAMPLES / "normal.jsonl").read_text(encoding="utf-8").splitlines(keepends=True)
        filler = json.dumps({"type": "response_item", "payload": {"type": "message", "content": "x" * 900}}) + "\n"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(lines[0])
            fh.write(filler * 3600)          # 前面约 3.3 MB
            fh.writelines(lines[1:])         # 最后一条累计
            fh.write(filler * 650)           # 后面还有约 600 KB 别的内容
        started = time.time()
        got = read_total(path)
        took = time.time() - started
        self.assertTrue(got["ok"])
        self.assertEqual(got["total"], NORMAL["total"])
        self.assertLess(took, 0.5, f"读取用了 {took:.2f} 秒")


# ---------------- A2：记录 ----------------

class Recording(Home):
    def setUp(self):
        super().setUp()
        self.cdir = self.tmp / "card"
        self.cdir.mkdir()
        self.work = self.tmp / "work"
        self.work.mkdir()

    def fake(self, header=HEADER):
        from collab.agents import AgentResult

        def ask_fn(adapter, prompt, cwd, expect_file=None, models=None, live_path=None, **kw):
            if live_path:
                Path(live_path).write_text(header + "干活中\n", encoding="utf-8")
            return AgentResult(ok=True, output=header + "结论：通过\n", attempts=1, final="结论：通过")
        return ask_fn

    def last(self, kind):
        return [e for e in read_flow(self.cdir) if e.get("kind") == kind][-1]

    def test_reply_carries_tokens_for_codex_and_qwen(self):
        from collab.dispatch import _dispatch
        put(self.home, "normal.jsonl")
        put(self.home, "child.jsonl", sid=CHILD, stamp="08-17-58")
        for adapter in ("codex", "qwen"):
            _dispatch(self.fake(), self.cdir, "复查员", adapter, "复查", "看看", self.work, {}, 1)
            tokens = self.last("回复")["tokens"]
            self.assertTrue(tokens["ok"], adapter)
            self.assertEqual(tokens["total"], NORMAL["total"], adapter)
            self.assertEqual(tokens["children"], 20428, adapter)

    def test_reply_has_no_tokens_for_agy_and_cursor(self):
        # token 第二版改口径（2026-11-18 用户批准）：agy、Cursor 也记用量；这里的假输出不是它们的格式，
        # 所以记一条“读不到”并写原因，而不是不记（原断言“不带 tokens 字段”按新口径必变，Claude 改，登记见 docs/token用量第二版/老测试改写登记.md）
        from collab.dispatch import _dispatch
        put(self.home, "normal.jsonl")
        for adapter in ("agy", "cursor"):
            _dispatch(self.fake(), self.cdir, "复查员", adapter, "复查", "看看", self.work, {}, 1)
            tokens = self.last("回复").get("tokens")
            self.assertIsInstance(tokens, dict, adapter)
            self.assertFalse(tokens.get("ok"), adapter)
            self.assertTrue(str(tokens.get("reason") or "").strip(), adapter)

    def test_unreadable_usage_does_not_stop_the_step(self):
        from collab.dispatch import _dispatch
        result = _dispatch(self.fake(), self.cdir, "复查员", "codex", "复查", "看看", self.work, {}, 1)
        reply = self.last("回复")
        self.assertTrue(result.ok)
        self.assertTrue(reply["ok"])
        self.assertFalse(reply["tokens"]["ok"])
        self.assertTrue(reply["tokens"]["reason"].strip())

    def test_handover_carries_tokens(self):
        from collab.dispatch import _make_handover
        put(self.home, "normal.jsonl")
        (self.cdir / "需求卡.md").write_text("# 改按钮\n", encoding="utf-8")
        _make_handover(self.fake(), self.cdir, "codex", {}, self.cdir / "需求卡.md", self.work)
        self.assertEqual(self.last("交接资料")["tokens"]["total"], NORMAL["total"])


class PlanRecording(ProjectCase):
    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp) / "codex"
        (self.home / "sessions").mkdir(parents=True)
        os.environ["CODEX_HOME"] = str(self.home)   # ProjectCase 收尾时会把环境变量还原
        header = Path(self.tmp) / "header.txt"
        header.write_text(HEADER, encoding="utf-8")
        os.environ["FAKE_HEADER"] = str(header)
        script = FAKE_CODEX.replace("args = sys.argv[1:]",
                                    "args = sys.argv[1:]\nsys.stderr.write(open(os.environ['FAKE_HEADER'], encoding='utf-8').read())")
        codex = self.bin / "codex"
        codex.write_text(script, encoding="utf-8")
        codex.chmod(0o755)
        self.make_card(allowed=False)
        (Path(self.project) / "docs").mkdir()
        (Path(self.project) / "docs" / "项目说明.md").write_text("# 项目说明\n做视频\n", encoding="utf-8")

    def test_plan_event_carries_tokens(self):
        put(self.home, "normal.jsonl")
        os.environ["FAKE_PLAN"] = json.dumps(GOOD_PLAN, ensure_ascii=False)
        code, out = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 0, out)
        plan = [e for e in read_flow(self.collab / "cards" / "CARD-001") if e.get("kind") == "方案"][-1]
        self.assertEqual(plan["tokens"]["total"], NORMAL["total"])


# ---------------- A3：右栏取数 ----------------

def T(n, children=0):
    return {"ok": True, "total": n, "input": n, "cached": 0, "output": 0, "reasoning": 0, "children": children}


class PanelData(ProjectCase):
    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp) / "codex"
        (self.home / "sessions").mkdir(parents=True)
        os.environ["CODEX_HOME"] = str(self.home)
        self.cdir = self.make_card(state="开发中")

    def station(self, view, key):
        return next(s for s in view["stations"] if s["key"] == key)

    def ask_and_reply(self, role, tool, tokens=None, round_no=1):
        sent = record(self.cdir, "派活", src="调度员", dst=role, tool=tool, step="写测试", round=round_no,
                      summary="干活", live=True)
        extra = {"tokens": tokens} if tokens is not None else {}
        record(self.cdir, "回复", src=role, dst="调度员", tool=tool, step="写测试", round=round_no,
               ok=True, seconds=10, reply_to=sent["seq"], summary="交了", **extra)

    def test_finished_running_multi_round_and_total(self):
        from desktop.view import card_view
        record(self.cdir, "方案", src="Codex", dst="你", summary="方案", tokens=T(100))
        self.ask_and_reply("测试员", "codex", T(1000))
        record(self.cdir, "交接资料", src="调度员", dst="开发员", ok=True, seconds=5, summary="备好", tokens=T(50))
        self.ask_and_reply("开发员", "qwen", T(2000, children=7), round_no=1)
        self.ask_and_reply("开发员", "qwen", T(3000), round_no=2)
        # 复查正在跑：用实时输出现读
        put(self.home, "normal.jsonl")
        put(self.home, "child.jsonl", sid=CHILD, stamp="08-17-58")
        sent = record(self.cdir, "派活", src="调度员", dst="复查员", tool="codex", step="复查", round=1,
                      summary="复查", live=True)
        (self.cdir / sent["live"]).write_text(HEADER + "看代码\n", encoding="utf-8")

        view = card_view(self.project, "CARD-001")
        self.assertEqual(self.station(view, "disp")["tokens"]["total"], 100)
        self.assertEqual(self.station(view, "test")["tokens"]["total"], 1000)
        self.assertEqual(self.station(view, "dev")["tokens"]["total"], 5000, "多轮相加")
        self.assertEqual(self.station(view, "rev")["tokens"]["total"], NORMAL["total"], "在跑的现读")
        other = 50 + 7 + 20428
        self.assertEqual(view["tokens"]["other"], other)
        self.assertEqual(view["tokens"]["total"], 100 + 1000 + 5000 + NORMAL["total"] + other)

    def test_old_codex_step_without_tokens_shows_unreadable_and_agy_shows_nothing(self):
        from desktop.view import card_view
        self.ask_and_reply("测试员", "codex")          # 老卡：Codex 交了活，但记录里没有用量
        self.ask_and_reply("开发员", "agy")            # agy：第二版起也算用量；没记录、日志也没有 → 读不到（原断言“不显示”按新口径必变，见登记）
        view = card_view(self.project, "CARD-001")
        test = self.station(view, "test")["tokens"]
        self.assertFalse(test["ok"])
        self.assertTrue(test["reason"].strip())
        dev = self.station(view, "dev").get("tokens")
        self.assertIsInstance(dev, dict)
        self.assertFalse(dev.get("ok"))
        self.assertTrue(str(dev.get("reason") or "").strip())

    def test_no_price_anywhere(self):
        from desktop.view import card_view
        self.ask_and_reply("测试员", "codex", T(1000))
        text = json.dumps(card_view(self.project, "CARD-001"), ensure_ascii=False)
        for word in ("元", "¥", "$", "price", "价格", "费用"):
            self.assertNotIn(word, text)


# ---------------- A4：右栏显示 ----------------

@needs_node()
class PanelText(unittest.TestCase):
    def test_node_rules(self):
        out = run_node("require('./tests/token_usage_panel_check.js')")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_page_is_wired(self):
        root = HERE.parent / "desktop" / "static"
        js = (root / "panel.js").read_text(encoding="utf-8")
        html = (root / "panel.html").read_text(encoding="utf-8")
        self.assertIn('id="tokenTotal"', html)
        render = js[js.index("function renderFlow"):]
        render = render[:render.index("\n  }\n")]
        # 11-17 第一批调整：第三行、鼠标提示各只有一个拼法函数
        self.assertIn("PanelCore.metaText(", render)
        self.assertIn("PanelCore.stepTitle(", render)
        self.assertIn("PanelCore.totalText(", js)


if __name__ == "__main__":
    unittest.main()
