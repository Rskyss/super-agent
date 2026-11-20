"""右栏显示小修返工：复查结论同一行带原因时，交接摘要不能丢原因。"""
import os
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import _dispatch
from collab.flow import read_flow, record, text_of
from collab.state import Card
from collab.workspace import init_collab
from desktop.view import card_view


class ReviewReasonInHandoff(unittest.TestCase):
    def test_saved_and_viewed_review_summary_keeps_reason_after_reject(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "项目"
            project.mkdir()
            collab = init_collab(project)
            card_id = "CARD-001"
            worktree = Path(directory) / "worktrees" / card_id
            worktree.mkdir(parents=True)
            cdir = collab / "cards" / card_id
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("# 右栏显示\n", encoding="utf-8")
            Card(card_id, state="测试中").save(cdir / "状态.json")
            record(cdir, "需求", src="你", dst="调度员", summary="右栏显示")
            record(cdir, "批准", src="你", dst="调度员", summary="批准方案")
            record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工", pid=os.getpid(),
                   tools={"测试员": "codex", "开发员": "codex", "复查员": "codex"})
            review_text = "结论：不通过（有一项回归）\n\n复查清单：\n```json\n{}\n```\n"

            def ask_fn(adapter, prompt, cwd, **kwargs):
                return AgentResult(ok=True, output="", attempts=1, final=review_text)

            _dispatch(ask_fn, cdir, "复查员", "codex", "复查", "复查", worktree, {}, 1)
            saved_reply = next(e for e in reversed(read_flow(cdir))
                               if e.get("kind") == "回复" and e.get("src") == "复查员")
            self.assertIn("有一项回归", text_of(cdir, saved_reply))
            self.assertEqual(saved_reply["summary"], "结论：不通过（有一项回归）")

            handoff = next(h for h in card_view(project, card_id)["handoffs"]
                           if h["kind"] == "回复" and h["from"] == "复查员 Codex")
            self.assertEqual(handoff["summary"], "结论：不通过（有一项回归）")


if __name__ == "__main__":
    unittest.main()
