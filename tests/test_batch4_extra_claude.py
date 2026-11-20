"""第四批 F1：Claude 接手后补的测试（Codex 接手复查 2 件）。"""
import os
import tempfile
import unittest
from pathlib import Path

from collab.flow import record
from collab.planner import _plan_from_order
from collab.state import Card
from collab.workspace import init_collab

ROOT = Path(__file__).resolve().parent.parent


class NoGuessBySectionOrder(unittest.TestCase):
    def test_old_orders_without_not_do_section(self):
        """C1、C1.5 施工单没有“不做什么”、第四节是“验收”：不能把验收清单当成不做什么。"""
        for rel in ("docs/跑卡流程调整/施工单-C1.md", "docs/跑卡流程调整/施工单-C1.5.md"):
            with self.subTest(rel=rel):
                data = _plan_from_order(ROOT, rel)
                if data is None:
                    continue
                self.assertNotIn("验收命令", data["not_do"])

    def test_missing_summary_section_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            order = Path(tmp) / "施工单.md"
            order.write_text("# 施工单\n\n## 二、允许改哪些文件\n\n- `a.py`\n\n验收命令：`python3 -m unittest`\n",
                             encoding="utf-8")
            self.assertIsNone(_plan_from_order(tmp, "施工单.md"), "没写“要做什么”就走原流程，不拿文件名单顶替")


class ProgramPlanInPanel(unittest.TestCase):
    def test_program_steps_not_counted_missing(self):
        from desktop.view import card_view
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "项目"
            project.mkdir()
            collab = init_collab(project)
            cdir = collab / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("# 改一处\n", encoding="utf-8")
            Card("CARD-001", state="待派发").save(cdir / "状态.json")
            record(cdir, "需求", src="你", dst="调度员", summary="改一处")
            record(cdir, "方案", src="程序", dst="你", summary="照施工单生成")
            record(cdir, "批准", src="你", dst="调度员", summary="批准方案")
            record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工", pid=os.getpid(),
                   tools={"测试员": "codex", "开发员": "codex", "复查员": "codex"})
            record(cdir, "交接资料", src="程序", dst="开发员", ok=True, summary="有施工单，跳过（写代码、复查直接读施工单）")
            view = card_view(project, "CARD-001")
            self.assertNotIn("没读到", str(view.get("tokens") or ""), "程序自己做的两步不算没读到用量")
            total = view.get("tokens") or {}
            self.assertFalse((total.get("missing") or 0) if isinstance(total, dict) else 0)


if __name__ == "__main__":
    unittest.main()
