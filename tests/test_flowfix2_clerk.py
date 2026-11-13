"""流程修理第二轮 ㉞：行政在卡片对话里像人一样汇报——同一个人的“没动静”提醒只留一条（后来的改写它），
退回、卡住时说复查挑出了什么（用 say 那句完整的话，不用只有 80 字的摘要）。"""
import json

from collab.flow import record
from desktop.clerk.tasks import TaskWatcher
from test_acceptance_22_card_chat import ChatCase


class Reminders(ChatCase):
    def _history(self, cid):
        path = self.desk._folder(self.project) / f"{cid}.jsonl"
        return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def test_quiet_reminders_for_one_worker_become_one_message(self):
        cdir = self.building()
        cid = self.open()
        watcher = TaskWatcher(self.desk)
        record(cdir, "派活", src="调度员", dst="开发员", tool="agy", step="开发", summary="写代码")
        record(cdir, "提醒", src="程序", dst="你", summary="开发员（agy）已经 5 分钟没有动静。")
        watcher.tick(self.project)
        record(cdir, "提醒", src="程序", dst="你", summary="开发员（agy）已经 10 分钟没有动静。")
        watcher.tick(self.project)
        msgs = [e for e in self.events(cid) if e["type"] == "message" and "没有动静" in (e.get("text") or "")]
        self.assertEqual(len({e["item"] for e in msgs}), 1, "同一个人的提醒改写同一条，不刷屏")
        self.assertIn("10 分钟", msgs[-1]["text"])
        kept = [h for h in self._history(cid) if "没有动静" in h.get("text", "")]
        self.assertEqual(len(kept), 1, "重新打开对话也只看到一条")
        self.assertIn("10 分钟", kept[0]["text"])

    def test_new_worker_gets_new_reminder(self):
        cdir = self.building()
        cid = self.open()
        watcher = TaskWatcher(self.desk)
        record(cdir, "派活", src="调度员", dst="开发员", tool="agy", step="开发", summary="写代码")
        record(cdir, "提醒", src="程序", dst="你", summary="开发员（agy）已经 5 分钟没有动静。")
        watcher.tick(self.project)
        record(cdir, "派活", src="调度员", dst="复查员", tool="codex", step="复查", summary="复查")
        record(cdir, "提醒", src="程序", dst="你", summary="复查员（codex）已经 5 分钟没有动静。")
        watcher.tick(self.project)
        msgs = [e for e in self.events(cid) if e["type"] == "message" and "没有动静" in (e.get("text") or "")]
        self.assertEqual(len({e["item"] for e in msgs}), 2)

    def test_return_says_what_review_found(self):
        cdir = self.building()
        cid = self.open()
        record(cdir, "退回", src="程序", dst="开发员", summary="复查不通过，自动退回写代码的（第 1 次，最多 2 次）",
               say="复查挑出 2 件事：① 编号写错也能出草稿；② 结尾提醒被截掉。已退回写代码的改（第 1 次，最多 2 次）。")
        TaskWatcher(self.desk).tick(self.project)
        said = [w for w in self.words(cid) if w and "复查挑出 2 件事" in w]
        self.assertTrue(said, self.words(cid))

    def test_reminder_still_merges_after_reopen(self):
        """复查 A 第 4 条：重开（换一个 ClerkDesk）后，再来的提醒仍改写原来那条。"""
        from desktop.clerk.desk import ClerkDesk
        cdir = self.building()
        cid = self.open()
        record(cdir, "派活", src="调度员", dst="开发员", tool="agy", step="开发", summary="写代码")
        record(cdir, "提醒", src="程序", dst="你", summary="开发员（agy）已经 5 分钟没有动静。")
        TaskWatcher(self.desk).tick(self.project)
        fresh = ClerkDesk(lambda: self.client)
        record(cdir, "提醒", src="程序", dst="你", summary="开发员（agy）已经 10 分钟没有动静。")
        TaskWatcher(fresh).tick(self.project)
        msgs = [e for e in fresh.events(self.project, cid, 0, wait=0)["events"]
                if e["type"] == "message" and "没有动静" in (e.get("text") or "")]
        self.assertEqual(len({e["item"] for e in msgs}), 1, msgs)
        self.assertIn("10 分钟", msgs[-1]["text"])

    def test_file_writes_share_one_lock(self):
        import inspect
        from desktop.clerk import desk
        self.assertIn("self._write_lock", inspect.getsource(desk.ClerkDesk._record))
        self.assertIn("self._write_lock", inspect.getsource(desk.ClerkDesk._rewrite_last))

