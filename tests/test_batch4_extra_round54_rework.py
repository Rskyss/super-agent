"""第四批 F1 复查退回的补充测试。"""
import copy
import json
import os
from pathlib import Path

from acceptance_helpers import GOOD_PLAN, ProjectCase
from collab.scope import read_allowed
from collab.tasks import revise_card


ORDER_PATH = "docs/功能甲/施工单-复查.md"
ORDER = """# 施工单 复查

## 一、要做什么（一句话）

按施工单生成方案。

## 二、允许改哪些文件

- `collab/planner.py`
- `tests/test_batch4_extra*.py`

## 三、具体做法

### E1 只取小标题
- 这一条是说明，不能挤进步骤。
- 这一条也是说明。

### E2 还是只取小标题
- 这里同样是说明。

## 四、不做什么

不改验收测试。

## 六、验收

- 验收命令：`python3 -m unittest discover -s tests -p "test_batch4*.py"`
"""


class OrderPlanRework(ProjectCase):
    def setUp(self):
        super().setUp()
        self.cdir = self.make_card(allowed=False)
        docs = Path(self.project) / "docs"
        docs.mkdir()
        (docs / "项目说明.md").write_text("# 项目说明\n做协作工具\n", encoding="utf-8")

    def write_order(self, text=ORDER):
        order = Path(self.project) / ORDER_PATH
        order.parent.mkdir(parents=True, exist_ok=True)
        order.write_text(text, encoding="utf-8")
        (self.cdir / "需求卡.md").write_text(
            f"# 复查补充\n\n## 要做什么\n按施工单 {ORDER_PATH} 做\n", encoding="utf-8"
        )

    def test_plan_rewrite_keeps_revision_and_added_file(self):
        self.write_order()
        code, out = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 0, out)

        revise_card(
            self.project,
            "CARD-001",
            "还要把结果提示写得更明白",
            files=["desktop/view.py"],
        )
        answer = copy.deepcopy(GOOD_PLAN)
        answer["summary"] = "按施工单做，并把结果提示写得更明白"
        answer["steps"] = ["照施工单做", "把结果提示写得更明白"]
        answer["allowed"] = ["collab/planner.py", "tests/test_batch4_extra*.py", "desktop/view.py"]
        os.environ["FAKE_PLAN"] = json.dumps(answer, ensure_ascii=False)

        code, out = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 0, out)
        plan = (self.cdir / "方案.md").read_text(encoding="utf-8")
        self.assertIn("把结果提示写得更明白", plan, "批准前追加的新要求要进重写后的方案")

        requirement = (self.cdir / "需求卡.md").read_text(encoding="utf-8")
        self.assertEqual(
            set(read_allowed(requirement)),
            {"collab/planner.py", "tests/test_batch4_extra*.py", "desktop/view.py"},
            "用户刚加进允许范围的文件不能被施工单盖掉",
        )

    def test_order_steps_keep_only_each_small_title(self):
        self.write_order()
        code, out = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 0, out)

        plan = (self.cdir / "方案.md").read_text(encoding="utf-8")
        section = plan.split("## 拆成几步，每步做什么、产出什么\n", 1)[1]
        section = section.split("\n## 你会看到什么结果", 1)[0]
        steps = [line.strip() for line in section.splitlines() if line.strip()]
        self.assertEqual(steps, ["1. E1 只取小标题", "2. E2 还是只取小标题"])


if __name__ == "__main__":
    import unittest

    unittest.main()
