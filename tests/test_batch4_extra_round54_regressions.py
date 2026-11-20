"""第四批 F1 第 54 轮复查退回的三条回归测试。"""
import copy
import json
import os
from pathlib import Path

from acceptance_helpers import GOOD_PLAN, ProjectCase
from collab.scope import read_allowed
from collab.tasks import revise_card


ORDER_PATH = "docs/功能甲/施工单-回归.md"
ORDER = """# 施工单 回归

## 一、要做什么（一句话）

按施工单生成方案。

## 二、允许改哪些文件

- `collab/planner.py`
- `tests/test_batch4_extra*.py`

## 三、具体做法

### R1 生成方案
- 读施工单。

## 四、不做什么

不改别的文件。

## 六、验收

- 验收命令：`python3 -m unittest discover -s tests -p "test_batch4*.py"`
"""


class OrderRework(ProjectCase):
    def setUp(self):
        super().setUp()
        self.cdir = self.make_card(allowed=False)
        docs = Path(self.project) / "docs"
        docs.mkdir()
        (docs / "项目说明.md").write_text("# 项目说明\n做协作工具\n", encoding="utf-8")
        order = docs / ORDER_PATH.removeprefix("docs/")
        order.parent.mkdir(parents=True, exist_ok=True)
        order.write_text(ORDER, encoding="utf-8")
        (self.cdir / "需求卡.md").write_text(
            f"# 回归\n\n## 要做什么\n按施工单 {ORDER_PATH} 做\n", encoding="utf-8"
        )

    def set_fake_plan(self, allowed):
        answer = copy.deepcopy(GOOD_PLAN)
        answer["summary"] = "按施工单生成方案"
        answer["steps"] = ["读施工单"]
        answer["allowed"] = allowed
        os.environ["FAKE_PLAN"] = json.dumps(answer, ensure_ascii=False)

    def plan(self):
        return self.cli("plan", self.project, "CARD-001")

    def test_order_rewrite_goes_back_to_ai_flow(self):
        """点过“继续改”（有追加要求）就照旧叫 AI 重写：新要求随卡片原文交给 AI，范围按 AI 新方案定（Claude 接手改口径）。"""
        code, out = self.plan()
        self.assertEqual(code, 0, out)
        self.assertFalse(self.codex_log.exists() and self.codex_log.read_text(encoding="utf-8").strip(),
                         "第一次写方案照施工单直接生成，不叫 AI")

        revise_card(self.project, "CARD-001", "只改登录页，不改首页", files=["desktop/view.py"])
        self.set_fake_plan(["collab/planner.py", "desktop/view.py"])
        code, out = self.plan()
        self.assertEqual(code, 0, out)
        calls = self.codex_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), 1, "点过继续改要叫 AI 重写")
        self.assertIn("只改登录页，不改首页", json.loads(calls[-1])[-1], "新要求随卡片原文交给 AI")
        plan = (self.cdir / "方案.md").read_text(encoding="utf-8")
        self.assertNotIn("没有让 AI 再写一遍", plan)
        self.assertEqual(
            read_allowed((self.cdir / "需求卡.md").read_text(encoding="utf-8")),
            ["collab/planner.py", "desktop/view.py"],
            "范围按 AI 新方案整体替换，和没有施工单的卡一样",
        )


class OrdinaryRework(ProjectCase):
    def setUp(self):
        super().setUp()
        docs = Path(self.project) / "docs"
        docs.mkdir()
        (docs / "项目说明.md").write_text("# 项目说明\n做协作工具\n", encoding="utf-8")
        self.cdir = self.make_card(allowed=False)
        (self.cdir / "需求卡.md").write_text(
            "# 回归\n\n## 要做什么\n改页面\n\n"
            "## 允许改动的范围\n- src/app.py\n- src/theme.py\n",
            encoding="utf-8",
        )

    def test_revision_can_narrow_the_scope_without_order(self):
        revise_card(self.project, "CARD-001", "不要改配色")
        answer = copy.deepcopy(GOOD_PLAN)
        answer["allowed"] = ["src/app.py"]
        os.environ["FAKE_PLAN"] = json.dumps(answer, ensure_ascii=False)

        code, out = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 0, out)
        self.assertEqual(
            read_allowed((self.cdir / "需求卡.md").read_text(encoding="utf-8")),
            ["src/app.py"],
            "没有施工单的卡，重写方案缩小范围后不能把旧范围合并回来",
        )


if __name__ == "__main__":
    import unittest
    unittest.main()
