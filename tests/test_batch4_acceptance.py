"""第四批 F1 验收测试（Claude 写；施工方不许改，补充测试写进 tests/test_batch4_extra*.py）。

依据 docs/跑卡流程调整/需求共识-第四批.md E1、E2：有施工单的卡跳过写方案（程序直接从施工单读允许改的文件和验收命令）、
跳过备交接资料；施工单读不全或没有施工单时照旧。
"""
import json
import os
from pathlib import Path

import test_flowfix2_dispatch as f2
from acceptance_helpers import GOOD_PLAN, ProjectCase
from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.flow import read_flow
from collab.judge import PASS, Verdict
from collab.scope import read_allowed
from collab.state import Card
from review_fmt import listed_ask

ORDER = """# 施工单 甲：右栏改说法

> 依据方案。

## 一、要做什么（一句话）

右栏不再写调度员。

## 二、允许改哪些文件

- `desktop/view.py`
- `collab/dispatch.py`（只改一处）
- `tests/test_jia_extra*.py`（写测试的新建）

**不许改**：`tests/test_jia_acceptance.py`。

## 三、具体做法

### C1 换说法
- 改 view。

### C2 停下说明
- 改 dispatch。

## 四、不做什么

不改团队设置页。

## 六、验收

- 验收命令：`python3 -m unittest discover -s tests -p "test_jia*.py" && python3 -m unittest discover -s tests -p "test_desktop_view.py"`
- 只跑上面这条验收命令。
"""
CMD = 'python3 -m unittest discover -s tests -p "test_jia*.py" && python3 -m unittest discover -s tests -p "test_desktop_view.py"'


class SkipPlanWithOrder(ProjectCase):
    def setUp(self):
        super().setUp()
        self.cdir = self.make_card(allowed=False)
        (Path(self.project) / "docs").mkdir()
        (Path(self.project) / "docs" / "项目说明.md").write_text("# 项目说明\n做视频\n", encoding="utf-8")

    def with_order(self, text=ORDER):
        order = Path(self.project) / "docs" / "功能甲" / "施工单-甲.md"
        order.parent.mkdir(parents=True, exist_ok=True)
        order.write_text(text, encoding="utf-8")
        (self.cdir / "需求卡.md").write_text("# 右栏改说法\n\n## 要做什么\n按施工单 docs/功能甲/施工单-甲.md 做\n",
                                           encoding="utf-8")

    def codex_planned(self):
        if not self.codex_log.exists():
            return False
        return any("--output-schema" in line for line in self.codex_log.read_text(encoding="utf-8").splitlines())

    def test_order_gives_the_start_sheet_without_ai(self):
        self.with_order()
        code, out = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 0, out)
        self.assertFalse(self.codex_planned(), "有施工单就不再叫 AI 写方案")
        start = json.loads((self.cdir / "开工.json").read_text(encoding="utf-8"))
        self.assertEqual(start["test_command"], CMD)
        allowed = read_allowed((self.cdir / "需求卡.md").read_text(encoding="utf-8"))
        self.assertEqual(allowed, ["desktop/view.py", "collab/dispatch.py", "tests/test_jia_extra*.py"])
        plan = (self.cdir / "方案.md").read_text(encoding="utf-8")
        self.assertIn("右栏不再写调度员", plan)
        self.assertIn("C1 换说法", plan)
        self.assertIn("不改团队设置页", plan)
        self.assertIn("照施工单", plan, "让人知道这份是程序照施工单直接生成的")
        ev = [e for e in read_flow(self.cdir) if e.get("kind") == "方案"][-1]
        self.assertEqual(ev.get("src"), "程序")

    def test_order_missing_test_command_falls_back_to_ai(self):
        self.with_order(ORDER.replace("- 验收命令：`", "- 命令：`"))
        os.environ["FAKE_PLAN"] = json.dumps(GOOD_PLAN, ensure_ascii=False)
        code, out = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.codex_planned(), "施工单读不全就照旧让 AI 写方案")

    def test_no_order_unchanged(self):
        os.environ["FAKE_PLAN"] = json.dumps(GOOD_PLAN, ensure_ascii=False)
        code, out = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 0, out)
        self.assertTrue(self.codex_planned())


class SkipHandoverWithOrder(f2.Base):
    def go(self):
        asked = []
        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, **kw):
            if "准备交接资料" in prompt:
                asked.append("handover")
                return AgentResult(ok=True, output="", attempts=1, final="## 要改的文件\n- app.py")
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写了测试")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "agy", "codex",
                 ask_fn=listed_ask(ask_fn), judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
                 reviewer_adapter="codex", handover=("codex", {"codex": "gpt-6.1-sol", "codex_effort": "low"}))
        return asked

    def test_order_skips_handover(self):
        root = self.collab.parent
        order = root / "docs" / "功能甲" / "施工单-甲.md"
        order.parent.mkdir(parents=True)
        order.write_text(ORDER, encoding="utf-8")
        (self.cdir / "需求卡.md").write_text("# 卡\n## 要做什么\n按施工单 docs/功能甲/施工单-甲.md 做\n", encoding="utf-8")
        self.assertEqual(self.go(), [], "有施工单就不备交接资料")
        notes = [e for e in read_flow(self.cdir) if e.get("kind") == "交接资料"]
        self.assertTrue(notes and "施工单" in notes[-1].get("summary", ""), "记一条：有施工单，跳过")

    def test_no_order_still_prepares(self):
        self.assertEqual(self.go(), ["handover"])
