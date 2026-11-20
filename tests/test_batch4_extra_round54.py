"""第四批 F1 补充测试：只补验收测试没盯死的边界。

这些测试仍然只盯用户能看到的行为：方案、开工单、需求卡范围和流转记录；
不假设产品代码里将来叫什么函数，也不启动浏览器、真实 AI 或全量测试。
"""
import copy
import json
import os
from pathlib import Path

import test_flowfix2_dispatch as f2
from acceptance_helpers import GOOD_PLAN, ProjectCase
from collab.agents import AgentResult
from collab.dispatch import HANDOVER, run_card
from collab.flow import read_flow
from collab.judge import PASS, Verdict
from collab.scope import read_allowed
from collab.state import Card
from review_fmt import listed_ask


ORDER_PATH = "docs/功能甲/施工单-甲.md"
ORDER = """# 施工单 甲：第四批补充

## 一、要做什么（一句话）

按施工单直接生成方案和开工单。

## 二、允许改哪些文件

- `collab/planner.py`
- `tests/test_batch4_extra*.py`

## 三、具体做法

### C1 程序读施工单
- 提取方案内容。

### C2 写回开工信息
- 更新需求卡范围和开工单。

## 四、不做什么

不改验收测试。

## 六、验收

- 验收命令：`python3 -m unittest discover -s tests -p "test_batch4*.py"`
"""
CMD = 'python3 -m unittest discover -s tests -p "test_batch4*.py"'


class OrderPlan(ProjectCase):
    def setUp(self):
        super().setUp()
        self.cdir = self.make_card(allowed=False)
        (Path(self.project) / "docs").mkdir()
        (Path(self.project) / "docs" / "项目说明.md").write_text("# 项目说明\n做协作工具\n", encoding="utf-8")

    def write_order(self, text=ORDER):
        order = Path(self.project) / ORDER_PATH
        order.parent.mkdir(parents=True, exist_ok=True)
        order.write_text(text, encoding="utf-8")
        (self.cdir / "需求卡.md").write_text(
            f"# 第四批补充\n\n## 要做什么\n按施工单 {ORDER_PATH} 做\n", encoding="utf-8"
        )

    def codex_planned(self):
        if not self.codex_log.exists():
            return False
        return any("--output-schema" in line for line in self.codex_log.read_text(encoding="utf-8").splitlines())

    def fake_plan(self, profile=""):
        answer = copy.deepcopy(GOOD_PLAN)
        answer["profile"] = profile
        os.environ["FAKE_PLAN"] = json.dumps(answer, ensure_ascii=False)

    def test_complete_order_marks_source_and_updates_start_sheet(self):
        self.write_order()
        code, out = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 0, out)
        self.assertFalse(self.codex_planned(), "有完整施工单就不再叫 AI 写方案")

        plan = (self.cdir / "方案.md").read_text(encoding="utf-8")
        self.assertIn("按施工单直接生成方案和开工单", plan)
        self.assertIn("C1 程序读施工单", plan)
        self.assertIn("C2 写回开工信息", plan)
        self.assertIn("不改验收测试", plan)
        self.assertIn(ORDER_PATH, plan, "方案要写清这份内容来自哪份施工单")
        self.assertIn("## 需要你确认的问题\n无", plan)

        start = json.loads((self.cdir / "开工.json").read_text(encoding="utf-8"))
        self.assertEqual(start["test_command"], CMD)
        allowed = read_allowed((self.cdir / "需求卡.md").read_text(encoding="utf-8"))
        self.assertEqual(allowed, ["collab/planner.py", "tests/test_batch4_extra*.py"])

        events = [e for e in read_flow(self.cdir) if e.get("kind") == "方案"]
        self.assertTrue(events)
        self.assertEqual(events[-1].get("src"), "程序")

    def test_order_without_allowed_files_falls_back_to_ai(self):
        self.write_order(ORDER.replace("- `collab/planner.py`\n- `tests/test_batch4_extra*.py`\n", ""))
        self.fake_plan()
        code, out = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.codex_planned(), "施工单缺少允许文件时照旧叫 AI 写方案")
        self.assertIn("把按钮改成蓝色", (self.cdir / "方案.md").read_text(encoding="utf-8"))
        self.assertEqual(read_allowed((self.cdir / "需求卡.md").read_text(encoding="utf-8")), ["src/app.py"])

    def test_order_needing_project_profile_falls_back_to_ai(self):
        (Path(self.project) / "docs" / "项目说明.md").unlink()
        self.write_order()
        self.fake_plan(profile="# 项目说明\n\n做协作工具，测试命令是 true。\n")
        code, out = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.codex_planned(), "项目说明没确认时照旧叫 AI 写方案")
        self.assertIn("做协作工具", (Path(self.project) / "docs" / "项目说明.md").read_text(encoding="utf-8"))


class OrderHandover(f2.Base):
    def go(self):
        asked = []
        worktree = self.worktree

        def ask_fn(adapter, prompt, cwd, **kwargs):
            if "准备交接资料" in prompt:
                asked.append("handover")
                return AgentResult(ok=True, output="", attempts=1, final="## 要改的文件\n- app.py")
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                (worktree / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写了测试")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        run_card(
            Card("CARD-001", state="待派发"), self.collab, self.worktree,
            "true", "agy", "codex", ask_fn=listed_ask(ask_fn),
            judge_fn=lambda *args, **kwargs: Verdict(PASS, 0, "ok"),
            reviewer_adapter="codex",
            handover=("codex", {"codex": "gpt-6.1-sol", "codex_effort": "low"}),
        )
        return asked

    def write_order(self, text=ORDER):
        root = self.collab.parent
        order = root / ORDER_PATH
        order.parent.mkdir(parents=True, exist_ok=True)
        order.write_text(text, encoding="utf-8")
        (self.cdir / "需求卡.md").write_text(
            f"# 卡\n\n## 要做什么\n按施工单 {ORDER_PATH} 做\n", encoding="utf-8"
        )

    def test_handover_is_skipped_even_when_order_content_is_incomplete(self):
        self.write_order("# 施工单 甲\n\n## 一、要做什么（一句话）\n\n没写完整\n")
        self.assertEqual(self.go(), [], "文件里有施工单就不备交接资料")
        self.assertFalse((self.cdir / HANDOVER).exists())
        events = [e for e in read_flow(self.cdir) if e.get("kind") == "交接资料"]
        self.assertTrue(events)
        self.assertEqual(events[-1].get("summary"), "有施工单，跳过（写代码、复查直接读施工单）")


if __name__ == "__main__":
    import unittest

    unittest.main()
