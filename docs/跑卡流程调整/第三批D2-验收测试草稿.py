"""第三批卡 D2 验收测试（Claude 写；施工方不许改，补充测试写进 tests/test_batch3_d2_extra*.py）。

依据 docs/跑卡流程调整/需求共识-第三批.md、方案-第三批.md、施工单-第三批D2.md：
C5 行政认全你粘贴的话（含“不做什么”），不再说读不了施工单 · C6 “要做什么”只写自己那一格
· C7 卡片标题用施工单的标题，不用原文开头
"""
from pathlib import Path

from acceptance_helpers import ProjectCase, ScriptedClient, tool_call, wait_idle
from desktop.clerk import desk as clerk_desk
from desktop.clerk.desk import ClerkDesk

PASTE = ("按施工单做 C9\n"
         "要做什么：中间区最后一句改成“已放进项目”\n"
         "不做什么：不改右栏\n"
         "怎么算做完：验收命令全过")
HEADINGS = ("按施工单做 C9\n\n## 要做什么\n中间区最后一句改成“已放进项目”\n\n"
            "## 不做什么\n不改右栏\n\n## 怎么算做完\n验收命令全过\n")


class Base(ProjectCase):
    def run_with(self, user_text, args):
        self.client = ScriptedClient([{"tool_calls": [tool_call("propose_task", args)]}, {"text": "你看这样行吗"}])
        self.desk = ClerkDesk(lambda: self.client)
        cid = self.desk.send(self.project, "", user_text)
        events = wait_idle(self.desk, self.project, cid)
        return [e for e in events if e["type"] == "proposal"]


class C5C6Fields(Base):
    def test_pasted_labels_fill_the_missing_field_and_split_do(self):
        shown = self.run_with(PASTE, {"title": "C9 改说法", "do": PASTE, "not_do": " ", "done_when": " "})
        self.assertEqual(len(shown), 1, "粘贴的话里写了“不做什么”，不能再说缺")
        self.assertEqual(shown[0]["not_do"], "不改右栏")
        self.assertEqual(shown[0]["done_when"], "验收命令全过")
        self.assertEqual(shown[0]["do"], "中间区最后一句改成“已放进项目”", "“要做什么”只写自己那一格")

    def test_markdown_headings_also_count(self):
        shown = self.run_with(HEADINGS, {"title": "C9 改说法", "do": HEADINGS, "not_do": "", "done_when": ""})
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0]["not_do"], "不改右栏")
        self.assertNotIn("不做什么", shown[0]["do"])

    def test_plain_request_still_asks_for_missing(self):
        shown = self.run_with("把按钮改蓝", {"title": "按钮", "do": "改蓝", "not_do": " ", "done_when": "看到蓝色"})
        self.assertEqual(shown, [], "没写“不做什么”的普通需求，照旧先问")
        tool_messages = [m for m in self.client.calls[1][0] if m["role"] == "tool"]
        self.assertIn("还缺", tool_messages[0]["content"])

    def test_model_fields_kept_when_user_did_not_label_them(self):
        text = "按施工单做 C9\n不做什么：不改右栏"
        shown = self.run_with(text, {"title": "C9", "do": "改中间区那句", "not_do": "", "done_when": "验收命令全过"})
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0]["do"], "改中间区那句", "用户没标的格子照用模型写的")
        self.assertEqual(shown[0]["not_do"], "不改右栏")


class C5Words(Base):
    def test_role_does_not_claim_it_cannot_read(self):
        self.assertIn("别说读不了", clerk_desk.ROLE)


class C7Title(Base):
    def test_title_comes_from_the_order(self):
        order = Path(self.project) / "docs" / "功能甲" / "施工单-甲.md"
        order.parent.mkdir(parents=True)
        order.write_text("# 施工单 甲：归档和开工\n\n正文\n", encoding="utf-8")
        text = f"按施工单 {order} 做"
        shown = self.run_with(text, {"title": f"按施工单做：{order}", "do": text, "not_do": "不改别的",
                                     "done_when": "验收命令全过"})
        self.assertEqual(shown[0]["title"], "施工单 甲：归档和开工")

    def test_normal_title_untouched(self):
        shown = self.run_with("把按钮改蓝", {"title": "按钮改蓝色", "do": "改蓝", "not_do": "不改别的",
                                           "done_when": "看到蓝色"})
        self.assertEqual(shown[0]["title"], "按钮改蓝色")


# ---------------- C13 进度条只数本卡开工时没过的那几项（用户 11-19：“这里都是满的，形同虚设”） ----------------
import json as _json
import subprocess as _sp
import shutil as _sh
import unittest as _ut

import test_redfirst_acceptance as _rf
from collab.judge import FAIL as _FAIL, Verdict as _Verdict
from collab.state import Card as _Card

_RED_OUT_OLD = ("FAIL: test_a (test_x.T)\n----\nERROR: test_b (test_x.T)\n----\nRan 3 tests in 0.1s\n\n"
                "FAILED (failures=1, errors=1)")
_RED_OUT_NEW = ("FAIL: test_a (test_x.T.test_a)\n----\nERROR: test_b (test_x.T.test_b)\n----\nRan 3 tests in 0.1s\n\n"
                "FAILED (failures=1, errors=1)")


class C13RedSetRecorded(_rf.Base):
    def test_red_check_writes_the_failing_tests(self):
        for output in (_RED_OUT_OLD, _RED_OUT_NEW):   # 老版和 3.12 起的 Python 写法都认
            with self.subTest(output=output[:30]):
                (self.cdir / "开工前没过的测试.json").unlink(missing_ok=True)
                outs = [_Verdict(_FAIL, 1, output)]

                def judge(*a, **k):
                    return outs.pop(0) if outs else _Verdict("过", 0, "Ran 3 tests\n\nOK")
                from collab.dispatch import run_card
                run_card(_Card("CARD-001", state="待派发"), self.collab, self.project, _rf.CMD, "cursor", "codex",
                         ask_fn=_rf.listed_ask(self.ask_fn), judge_fn=judge, reviewer_adapter="agy",
                         require_tests=True, red_check=True)
                data = _json.loads((self.cdir / "开工前没过的测试.json").read_text(encoding="utf-8"))
                self.assertEqual(sorted(data["tests"]), ["test_x.T.test_a", "test_x.T.test_b"])
                self.tearDown(); self.setUp()


class C13ProgressCountsOwnTests(ProjectCase):
    def test_only_red_tests_are_counted(self):
        from collab.progress import ProgressJob
        wt = Path(self.tmp) / "wt"
        (wt / "tests").mkdir(parents=True)
        (wt / "tests" / "test_x.py").write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n"
            "    def test_a(self): pass\n"            # 开工时没过，现在过了
            "    def test_b(self): self.fail('还没做')\n"   # 开工时没过，现在还没过
            "    def test_c(self): pass\n"            # 老检查，一直能过：不算进本卡
            "    def test_d(self): pass\n", encoding="utf-8")
        cdir = self.make_card()
        (cdir / "开工前没过的测试.json").write_text(
            _json.dumps({"tests": ["test_x.T.test_a", "test_x.T.test_b"]}), encoding="utf-8")
        job = ProgressJob(cdir, wt, "开发", 1, ["tests/test_x.py"],
                          test_command='python3 -m unittest discover -s tests -p "test_x.py"')
        got = job.compute()
        self.assertEqual((got.get("passed"), got.get("total"), got.get("own")), (1, 2, True))

    def test_without_red_record_falls_back(self):
        from collab.progress import ProgressJob
        wt = Path(self.tmp) / "wt2"
        (wt / "tests").mkdir(parents=True)
        (wt / "tests" / "test_y.py").write_text(
            "import unittest\n\nclass T(unittest.TestCase):\n    def test_a(self): pass\n", encoding="utf-8")
        cdir = self.make_card(card_id="CARD-009")
        job = ProgressJob(cdir, wt, "开发", 1, ["tests/test_y.py"],
                          test_command='python3 -m unittest discover -s tests -p "test_y.py"')
        got = job.compute()
        self.assertEqual((got.get("passed"), got.get("total")), (1, 1))
        self.assertFalse(got.get("own"))


class C13PanelWords(_ut.TestCase):
    @_ut.skipUnless(_sh.which("node"), "需要 Node")
    def test_own_progress_words(self):
        script = ("const P=require('./desktop/static/panel.js');"
                  "const t=P.progressText({passed:7,total:20,own:true});"
                  "if(!t||t.text.indexOf('本卡要做的 20 项')<0||t.text.indexOf('已过 7')<0||t.pct!==35)process.exit(1);"
                  "const o=P.progressText({passed:3,total:4});"
                  "if(!o||o.text.indexOf('通过 3/4')<0)process.exit(2);")
        root = Path(__file__).resolve().parents[1]
        done = _sp.run(["node", "-e", script], cwd=root, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)


# ---------------- C14 左栏卡片加一行完成时间（用户 11-19 截图：“增加一行时间吧，就是完成时间”） ----------------
class C14FinishedTime(ProjectCase):
    def test_list_cards_gives_finish_seconds(self):
        from desktop.view import list_cards
        from collab.flow import record as _rec
        done = self.make_card(card_id="CARD-001", state="已合并")
        _rec(done, "合并", src="程序", dst="你", summary="已放进项目", ts=1_800_000_000.0)
        old = self.make_card(card_id="CARD-002", state="已合并")
        lines = [_json.loads(l) for l in (old / "流转.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        lines.append({"seq": 99, "time": "2026-11-17T20:30:00", "kind": "合并", "src": "程序", "dst": "你",
                      "summary": "已放进 dev-assistant"})   # 老记录只有钟点
        (old / "流转.jsonl").write_text("".join(_json.dumps(e, ensure_ascii=False) + "\n" for e in lines),
                                        encoding="utf-8")
        self.make_card(card_id="CARD-003", state="开发中")
        got = {c["id"]: c.get("finished") for c in list_cards(self.project)}
        self.assertEqual(got["CARD-001"], 1_800_000_000.0)
        from datetime import datetime as _dt
        self.assertEqual(got["CARD-002"], _dt(2026, 11, 17, 20, 30).timestamp())
        self.assertIsNone(got["CARD-003"], "没做完的卡不显示完成时间")


class C14NavLine(_ut.TestCase):
    @_ut.skipUnless(_sh.which("node"), "需要 Node")
    def test_finish_line_words(self):
        script = ("const N=require('./desktop/static/nav.js');"
                  "const now=new Date(2026,10,19,18,0);"
                  "const t=new Date(2026,10,19,17,48).getTime()/1000;"
                  "const y=new Date(2026,10,18,22,10).getTime()/1000;"
                  "if(N.finishLine({state:'已合并',finished:t},now)!=='完成于 2026-11-19 17:48')process.exit(1);"
                  "if(N.finishLine({state:'已放弃',finished:y},now)!=='放弃于 2026-11-18 22:10')process.exit(2);"
                  "if(N.finishLine({state:'开发中',finished:null},now)!=='')process.exit(3);")
        root = Path(__file__).resolve().parents[1]
        done = _sp.run(["node", "-e", script], cwd=root, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_card_row_shows_it(self):
        js = (Path(__file__).resolve().parents[1] / "desktop" / "static" / "nav.js").read_text(encoding="utf-8")
        row = js[js.index("function cardRow"):js.index("function cardRow") + 1500]
        self.assertIn("finishLine(", row)
