"""C2 第 3 轮复查里碰得上的三件（Claude 接手补）：施工单路径在反引号里也要认；
另一张卡正在放进项目时不许“先存再开工”；项目处在合并冲突中途时不许保存。"""
import subprocess
from pathlib import Path
from unittest import mock

from acceptance_helpers import ProjectCase, ServerCase


class OrderInBackticks(ProjectCase):
    def test_backticked_relative_path_is_found(self):
        from collab.planner import existing_orders
        order = Path(self.project) / "docs" / "功能甲" / "施工单-甲.md"
        order.parent.mkdir(parents=True)
        order.write_text("# 施工单\n", encoding="utf-8")
        text = "按施工单 `docs/功能甲/施工单-甲.md` 做。\n"
        self.assertEqual(existing_orders(self.project, text), ["docs/功能甲/施工单-甲.md"])


class SaveGuards(ServerCase):
    def save(self):
        return self.post("/api/clerk/save_pending", {"project": self.project, "card": "CARD-001"})

    def test_refused_while_another_card_is_being_merged(self):
        self.make_card()
        Path(self.project, "src", "app.py").write_text("x = 2\n", encoding="utf-8")
        before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.project, capture_output=True, text=True).stdout
        with mock.patch("desktop.server.merge_unfinished", return_value=True):
            status, answer = self.save()
        self.assertEqual(status, 409)
        self.assertIn("放进项目", answer.get("reason", ""))
        after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.project, capture_output=True, text=True).stdout
        self.assertEqual(before, after)

    def test_refused_in_the_middle_of_a_merge_conflict(self):
        self.make_card()
        g = lambda *a: subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=self.project,
                                      capture_output=True, text=True)
        g("checkout", "-q", "-b", "other")
        Path(self.project, "src", "app.py").write_text("x = 'other'\n", encoding="utf-8")
        g("commit", "-qam", "other")
        g("checkout", "-q", "-")
        Path(self.project, "src", "app.py").write_text("x = 'main'\n", encoding="utf-8")
        g("commit", "-qam", "main")
        g("merge", "other")
        self.assertTrue(g("ls-files", "-u").stdout.strip(), "要先真的处在冲突中")
        status, answer = self.save()
        self.assertEqual(status, 409)
        self.assertIn("冲突", answer.get("reason", ""))
        self.assertTrue(g("ls-files", "-u").stdout.strip(), "冲突条目不能被动掉")
