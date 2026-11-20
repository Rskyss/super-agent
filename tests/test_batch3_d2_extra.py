"""第三批 D2 的补充测试：只补边界，不改已有验收测试。"""
import json
import ast
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from acceptance_helpers import ProjectCase, ScriptedClient, tool_call, wait_idle
from desktop.clerk import desk as clerk_desk
from desktop.clerk.desk import ClerkDesk


class FieldsFromText(unittest.TestCase):
    def test_bold_colon_labels_and_multiline_values(self):
        from desktop.clerk.tasks import _fields_from_text

        text = (
            "标题: 左栏加完成时间\n"
            "**要做什么**：只在已合并卡下面显示时间\n"
            "时间要写年月日和时分\n"
            "** 不做什么 **: 不改标题\n"
            "**怎么算做完**：验收命令全过\n"
        )
        self.assertEqual(_fields_from_text(text), {
            "title": "左栏加完成时间",
            "do": "只在已合并卡下面显示时间\n时间要写年月日和时分",
            "not_do": "不改标题",
            "done_when": "验收命令全过",
        })

    def test_markdown_headings_stop_at_next_heading(self):
        from desktop.clerk.tasks import _fields_from_text

        text = (
            "## **标题**\n"
            "左栏时间\n"
            "### 要做什么\n"
            "显示完成时间\n"
            "保留第二行\n"
            "# 不做什么\n"
            "不改标题\n"
            "## 怎么算做完\n"
            "验收全过\n"
            "## 其他说明\n"
            "这段不属于上面的格子\n"
        )
        self.assertEqual(_fields_from_text(text), {
            "title": "左栏时间",
            "do": "显示完成时间\n保留第二行",
            "not_do": "不改标题",
            "done_when": "验收全过",
        })

    def test_empty_and_unknown_labels_are_ignored(self):
        from desktop.clerk.tasks import _fields_from_text

        self.assertEqual(_fields_from_text("标题：\n要做什么：   \n不做什么：不改右栏\n"),
                         {"not_do": "不改右栏"})
        self.assertEqual(_fields_from_text("把按钮改蓝"), {})


class ProposalFields(ProjectCase):
    def run_with(self, user_text, args):
        self.client = ScriptedClient([{"tool_calls": [tool_call("propose_task", args)]}])
        self.desk = ClerkDesk(lambda: self.client)
        cid = self.desk.send(self.project, "", user_text)
        events = wait_idle(self.desk, self.project, cid)
        return [event for event in events if event["type"] == "proposal"]

    def test_last_user_message_wins(self):
        first = "要做什么：旧的错误内容"
        second = "要做什么：只改中间区\n不做什么：不改右栏"
        self.client = ScriptedClient([
            {"text": "我先记下"},
            {"tool_calls": [tool_call("propose_task", {
                "title": "改中间区", "do": "模型写的整段", "not_do": "", "done_when": "验收全过",
            })]},
        ])
        self.desk = ClerkDesk(lambda: self.client)
        cid = self.desk.send(self.project, "", first)
        wait_idle(self.desk, self.project, cid)
        self.desk.send(self.project, cid, second)
        events = wait_idle(self.desk, self.project, cid)

        shown = [event for event in events if event["type"] == "proposal"]
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0]["do"], "只改中间区")
        self.assertEqual(shown[0]["not_do"], "不改右栏")
        self.assertEqual(shown[0]["done_when"], "验收全过")

    def test_order_without_heading_keeps_title(self):
        order = Path(self.project) / "docs" / "功能甲" / "施工单-甲.md"
        order.parent.mkdir(parents=True)
        order.write_text("这份施工单没有一级标题\n", encoding="utf-8")
        title = f"按施工单做：{order}"
        shown = self.run_with(f"按施工单 {order} 做", {
            "title": title, "do": "改中间区", "not_do": "不改别的", "done_when": "验收全过",
        })
        self.assertEqual(shown[0]["title"], title)

    def test_order_path_in_do_can_replace_title(self):
        order = Path(self.project) / "docs" / "施工单-乙.md"
        order.parent.mkdir(parents=True)
        order.write_text("# 施工单乙：修左栏\n\n正文\n", encoding="utf-8")
        shown = self.run_with("按施工单做", {
            "title": "按施工单做", "do": "docs/施工单-乙.md", "not_do": "不改别的", "done_when": "验收全过",
        })
        self.assertEqual(shown[0]["title"], "施工单乙：修左栏")

    def test_bold_label_closing_marker_is_not_part_of_value(self):
        shown = self.run_with(
            "**怎么算做完：**全量通过**并保留原文星号**\n",
            {"title": "改粘贴识别", "do": "照用户写的填格子", "not_do": "不改确认框",
             "done_when": "模型写的"},
        )
        self.assertEqual(shown[0]["done_when"], "全量通过**并保留原文星号**")


class ClerkRole(unittest.TestCase):
    def test_role_says_the_writer_will_read_order(self):
        self.assertIn("施工单你不用读，也别说读不了；只说写方案时会读。", clerk_desk.ROLE)


class RedCheckRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cdir = Path(self.tmp.name) / "CARD-001"
        self.cdir.mkdir()

    def check(self, verdict):
        from collab.dispatch import _red_check_one

        return _red_check_one(
            self.cdir, "写测试", ["python3", "-m", "unittest"],
            lambda *args, **kwargs: verdict, Path(self.tmp.name),
        )

    def test_failure_record_overwrites_old_names_and_step(self):
        from collab.judge import FAIL, Verdict

        path = self.cdir / "开工前没过的测试.json"
        path.write_text(json.dumps({"tests": ["old.T.test_old"], "step": "旧步骤"}, ensure_ascii=False),
                        encoding="utf-8")
        output = "FAIL: test_a (test_x.T)\nERROR: test_b (test_x.T.test_b)\n"
        self.check(Verdict(FAIL, 1, output))

        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data, {"tests": ["test_x.T.test_a", "test_x.T.test_b"], "step": "写测试"})

    def test_green_and_run_error_do_not_touch_old_record(self):
        from collab.judge import ENV_ERROR, PASS, Verdict

        path = self.cdir / "开工前没过的测试.json"
        old = {"tests": ["test_x.T.test_a"], "step": "写测试"}
        path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        for verdict in (Verdict(PASS, 0, "OK"), Verdict(ENV_ERROR, 127, "找不到命令")):
            with self.subTest(result=verdict.result):
                self.check(verdict)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), old)

    def test_long_output_and_later_segments_do_not_hide_red_tests(self):
        from collab.dispatch import _red_check_one
        from collab.judge import FAIL, judge

        worktree = Path(self.tmp.name) / "wt"
        (worktree / "tests").mkdir(parents=True)
        (worktree / "tests" / "test_a.py").write_text(
            "import unittest\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_a(self): self.fail('输出很长' + '长' * 5000)\n",
            encoding="utf-8")
        (worktree / "tests" / "test_b.py").write_text(
            "import unittest\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_b(self): self.fail('第二段失败')\n",
            encoding="utf-8")
        command = (
            'python3 -m unittest discover -s tests -p "test_a.py" && '
            'python3 -m unittest discover -s tests -p "test_b.py"'
        )
        verdict = _red_check_one(
            self.cdir, "写测试", ["/bin/bash", "-o", "pipefail", "-c", command],
            judge, worktree,
        )
        self.assertEqual(verdict.result, FAIL)
        data = json.loads((self.cdir / "开工前没过的测试.json").read_text(encoding="utf-8"))
        self.assertEqual(data["tests"], ["test_a.T.test_a", "test_b.T.test_b"])


class ProgressOwnTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write_worktree(self, tests):
        worktree = Path(self.tmp.name) / "wt"
        (worktree / "tests").mkdir(parents=True)
        for name, body in tests.items():
            (worktree / "tests" / name).write_text(body, encoding="utf-8")
        return worktree

    def test_all_command_segments_run_and_only_red_tests_count(self):
        from collab.progress import ProgressJob

        worktree = self.write_worktree({
            "test_a.py": (
                "import unittest\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_a(self): pass\n"
                "    def test_b(self): self.fail('还没做')\n"
                "    def test_c(self): self.fail('新问题')\n"
            ),
            "test_b.py": (
                "import unittest\n\n"
                "class T(unittest.TestCase):\n"
                "    def test_d(self): self.fail('第二段也要跑')\n"
            ),
        })
        cdir = Path(self.tmp.name) / "cards" / "CARD-001"
        cdir.mkdir(parents=True)
        (cdir / "开工前没过的测试.json").write_text(json.dumps({
            "tests": ["test_a.T.test_a", "test_a.T.test_b", "test_b.T.test_d"],
        }, ensure_ascii=False), encoding="utf-8")
        command = (
            'python3 -m unittest discover -s tests -p "test_a.py" && '
            'python3 -m unittest discover -s tests -p "test_b.py"'
        )
        got = ProgressJob(cdir, worktree, "开发", 1,
                          ["tests/test_a.py", "tests/test_b.py"], test_command=command).compute()
        self.assertEqual((got.get("passed"), got.get("total"), got.get("own")), (1, 3, True))

    def test_empty_red_list_falls_back(self):
        from collab.progress import ProgressJob

        worktree = self.write_worktree({
            "test_y.py": "import unittest\n\nclass T(unittest.TestCase):\n    def test_a(self): pass\n",
        })
        cdir = Path(self.tmp.name) / "cards" / "CARD-002"
        cdir.mkdir(parents=True)
        (cdir / "开工前没过的测试.json").write_text(json.dumps({"tests": []}, ensure_ascii=False),
                                                    encoding="utf-8")
        command = 'python3 -m unittest discover -s tests -p "test_y.py"'
        got = ProgressJob(cdir, worktree, "开发", 1, ["tests/test_y.py"],
                          test_command=command).compute()
        self.assertEqual((got.get("passed"), got.get("total")), (1, 1))
        self.assertFalse(got.get("own"))

    def test_import_error_still_reports_it_cannot_run(self):
        from collab.progress import ProgressJob

        worktree = self.write_worktree({"test_bad.py": "import no_such_module_for_d2\n"})
        cdir = Path(self.tmp.name) / "cards" / "CARD-003"
        cdir.mkdir(parents=True)
        (cdir / "开工前没过的测试.json").write_text(json.dumps({"tests": ["test_bad.T.test_a"]},
                                                                ensure_ascii=False), encoding="utf-8")
        command = 'python3 -m unittest discover -s tests -p "test_bad.py"'
        got = ProgressJob(cdir, worktree, "开发", 1, ["tests/test_bad.py"],
                          test_command=command).compute()
        self.assertIn("测试跑不起来", got.get("error", ""))
        self.assertNotIn("total", got)
        self.assertNotIn("own", got)


class FinishedTime(ProjectCase):
    def test_discarded_card_uses_last_record_time(self):
        from collab.flow import record
        from desktop.view import list_cards

        cdir = self.make_card(card_id="CARD-001", state="已放弃")
        record(cdir, "回复", src="测试员", summary="不做了", ts=1_000.0)
        record(cdir, "结束", src="调度员", summary="这张不做了", ts=1_234.5)
        got = {card["id"]: card.get("finished") for card in list_cards(self.project)}
        self.assertEqual(got["CARD-001"], 1_234.5)

    def test_merged_card_uses_last_merge_time_not_later_events(self):
        from collab.flow import record
        from desktop.view import list_cards

        cdir = self.make_card(card_id="CARD-001", state="已合并")
        record(cdir, "合并", src="程序", dst="你", summary="第一次合并", ts=100.0)
        record(cdir, "回复", src="复查员", summary="通过", ts=200.0)
        record(cdir, "合并", src="程序", dst="你", summary="最后一次合并", ts=300.0)
        record(cdir, "归档", src="程序", summary="已归档", ts=400.0)
        got = {card["id"]: card.get("finished") for card in list_cards(self.project)}
        self.assertEqual(got["CARD-001"], 300.0)


class NavFinishLine(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "需要 Node")
    def test_finish_line_only_for_finished_cards_with_time(self):
        script = (
            "const N=require('./desktop/static/nav.js');"
            "const now=new Date(2026,10,19,18,0);"
            "const t=new Date(2026,10,19,17,48).getTime()/1000;"
            "const y=new Date(2026,10,18,22,10).getTime()/1000;"
            "if(N.finishLine({state:'已合并',finished:t},now)!=='完成于 2026-11-19 17:48')process.exit(1);"
            "if(N.finishLine({state:'已放弃',finished:y},now)!=='放弃于 2026-11-18 22:10')process.exit(2);"
            "if(N.finishLine({state:'已合并',finished:null},now)!=='')process.exit(3);"
            "if(N.finishLine({state:'开发中',finished:t},now)!=='')process.exit(4);"
        )
        root = Path(__file__).resolve().parents[1]
        done = subprocess.run(["node", "-e", script], cwd=root, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)


class ViewChangeScope(unittest.TestCase):
    def test_finish_time_helper_is_not_added_outside_list_cards(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "desktop" / "view.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        top_level_names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
        self.assertNotIn("_finished_seconds", top_level_names)
        list_cards = next(node for node in tree.body
                          if isinstance(node, ast.FunctionDef) and node.name == "list_cards")
        self.assertIn("finished", ast.get_source_segment(source, list_cards))


if __name__ == "__main__":
    unittest.main()
