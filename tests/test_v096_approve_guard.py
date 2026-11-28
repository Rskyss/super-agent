"""v0.96 C1：开工前拦住（docs/跑卡不停半路/方案.md 第 5 条）。

11-27 train 卡 16：施工单整个文件夹没存进项目就开卡，副本里没有施工单，测试员先报“找不到文件”；
Gemini 手写方案后直接 collab approve，绕过了“写方案”这一步（没有开工记录），v0.95 的“施工单缺节”检查也被绕过。
窗口和命令行开卡都经过 cmd_approve，规矩放在这里。窗口原来批准失败一律说“方案里还有空白”，改成说真实原因。
"""
import argparse
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from collab.cli import approve_block_reason, cmd_approve
from collab.state import Card

STATIC = Path(__file__).resolve().parents[1] / "desktop" / "static"
ORDER = "docs/feat/施工单-卡1.md"


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


class ApproveGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "proj"
        self.project.mkdir()
        git(self.project, "init", "-q")
        git(self.project, "config", "user.email", "t@t")
        git(self.project, "config", "user.name", "t")
        (self.project / "app.py").write_text("A = 1\n", encoding="utf-8")
        git(self.project, "add", ".")
        git(self.project, "commit", "-qm", "init")
        self.collab = self.project / ".collab"
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.collab / "配置.json").write_text(json.dumps({"project_profile_confirmed": True}), encoding="utf-8")
        (self.cdir / "需求卡.md").write_text(f"# 卡\n## 要做什么\n照 {ORDER} 做\n## 允许改动的范围\n- app.py\n",
                                          encoding="utf-8")
        (self.cdir / "方案.md").write_text("# 拆解方案\n\n照施工单做。\n", encoding="utf-8")
        Card("CARD-001", state="待对齐").save(self.cdir / "状态.json")
        order = self.project / ORDER
        order.parent.mkdir(parents=True)
        order.write_text("# 施工单\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def approve(self):
        return cmd_approve(argparse.Namespace(project=str(self.project), card_id="CARD-001"))

    def save_order(self):
        git(self.project, "add", ORDER)
        git(self.project, "commit", "-qm", "order")

    def start_record(self):
        (self.cdir / "开工.json").write_text(json.dumps({"test_command": "python3 -m unittest"}), encoding="utf-8")

    def state(self):
        return Card.load(self.cdir / "状态.json").state

    def test_unsaved_order_blocks_approve(self):
        self.start_record()
        why = approve_block_reason(str(self.project), "CARD-001")
        self.assertIn("施工单还没存进项目", why)
        self.assertIn(ORDER, why)
        self.assertEqual(self.approve(), 1)
        self.assertEqual(self.state(), "待对齐", "拦住时卡不能进待派发")

    def test_order_without_start_record_blocks_approve(self):
        self.save_order()
        why = approve_block_reason(str(self.project), "CARD-001")
        self.assertIn("写方案", why)
        self.assertEqual(self.approve(), 1)

    def test_saved_order_with_start_record_passes(self):
        self.save_order()
        self.start_record()
        self.assertEqual(approve_block_reason(str(self.project), "CARD-001"), "")
        self.assertEqual(self.approve(), 0)
        self.assertEqual(self.state(), "待派发")

    def test_ignored_order_counts_as_unsaved(self):
        """复查 C 中等 1：施工单被 .gitignore 忽略，开工副本里照样没有它。"""
        (self.project / ".gitignore").write_text("docs/\n", encoding="utf-8")
        git(self.project, "add", ".gitignore")
        git(self.project, "commit", "-qm", "ignore")
        self.start_record()
        self.assertIn("施工单还没存进项目", approve_block_reason(str(self.project), "CARD-001"))

    def test_card_without_order_is_not_affected(self):
        (self.cdir / "需求卡.md").write_text("# 卡\n## 要做什么\n改一下\n## 允许改动的范围\n- app.py\n", encoding="utf-8")
        self.assertEqual(approve_block_reason(str(self.project), "CARD-001"), "", "没引用施工单的卡照旧")
        self.assertEqual(self.approve(), 0)

    def test_window_uses_real_reason_and_hides_go_when_order_unsaved(self):
        server = (Path(__file__).resolve().parents[1] / "desktop" / "server.py").read_text(encoding="utf-8")
        self.assertIn("approve_block_reason", server, "窗口批准失败要说真实原因，不再一律说“方案里还有空白”")
        script = (STATIC / "panel.js").read_text(encoding="utf-8")
        self.assertIn("orders", script, "确认框要知道哪些是没存的施工单")
        self.assertIn('$("startGo").hidden', script, "施工单没存时不给“直接开工”")


if __name__ == "__main__":
    unittest.main()
