"""第七批 7-1：任务跑着时点“确认改”要能生效（对照 04）。

用户 11-16 定：复查已经通过时才收到修改，也按新要求再走一轮。
做法：跑着时确认的修改先排队（不报错）；调度员在两个停顿点取走——回去写代码之前、复查过了要交你验收之前——
取走时走现成的“接着改”（记追加要求，写测试的只为新要求补测试，再写代码、跑测试、复查）。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.flow import read_flow
from collab.judge import PASS, Verdict
from collab.state import Card
from collab.workspace import init_collab
from collab import tasks as collab_tasks
from desktop.clerk import tasks as clerk_tasks


class _Script:
    def __init__(self, verdicts, outputs=None, on_ask=None):
        self.verdicts = list(verdicts)
        self.outputs = outputs or {}
        self.prompts = []
        self.on_ask = on_ask

    def ask(self, adapter, prompt, cwd, **kwargs):
        self.prompts.append((adapter, prompt))
        if self.on_ask:
            self.on_ask(adapter, prompt)
        return AgentResult(ok=True, output=self.outputs.get(adapter, f"{adapter} 的回复"), attempts=1)

    def judge(self, command, cwd, timeout):
        result = self.verdicts.pop(0) if self.verdicts else PASS
        return Verdict(result=result, returncode=0 if result == PASS else 1, output=f"测试输出-{result}")


class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        self.collab = init_collab(self.tmp.name)
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("做一个函数\n\n## 验收标准\n1. 能算加法\n", encoding="utf-8")
        Card("CARD-001", state="开发中").save(self.cdir / "状态.json")

    def tearDown(self):
        self.tmp.cleanup()

    def _kinds(self):
        return [e["kind"] for e in read_flow(self.cdir)]

    def test_confirm_while_this_card_runs_is_queued_not_refused(self):
        with mock.patch.object(clerk_tasks, "running", side_effect=lambda p, k: {"card": "CARD-001"} if k == "run" else None), \
                mock.patch.object(clerk_tasks, "card_phase", return_value={"code": "running", "actions": ["stop"]}):
            msg = clerk_tasks.apply_revise(str(self.project), "CARD-001", "汇总放在按钮旁边")
        self.assertIn("这一轮跑完", msg)
        queued = collab_tasks.queued_revisions(self.project, "CARD-001")
        self.assertEqual([q["text"] for q in queued], ["汇总放在按钮旁边"])
        self.assertIn("改要求排队", self._kinds())
        self.assertNotIn("汇总放在按钮旁边", (self.cdir / "需求卡.md").read_text(encoding="utf-8"),
                         "还没生效，需求卡不能先改")

    def test_queued_change_with_screenshot_is_refused_honestly(self):
        with mock.patch.object(clerk_tasks, "running", side_effect=lambda p, k: {"card": "CARD-001"} if k == "run" else None), \
                mock.patch.object(clerk_tasks, "card_phase", return_value={"code": "running", "actions": ["stop"]}):
            with self.assertRaises(RuntimeError) as ctx:
                clerk_tasks.apply_revise(str(self.project), "CARD-001", "按图改", attachments=[{"name": "a.png"}])
        self.assertIn("截图", str(ctx.exception))
        self.assertEqual(collab_tasks.queued_revisions(self.project, "CARD-001"), [])

    def test_other_card_running_still_refused(self):
        with mock.patch.object(clerk_tasks, "running", side_effect=lambda p, k: {"card": "CARD-009"} if k == "run" else None), \
                mock.patch.object(clerk_tasks, "card_phase", return_value={"code": "running", "actions": ["stop"]}):
            with self.assertRaises(RuntimeError):
                clerk_tasks.apply_revise(str(self.project), "CARD-001", "改一下")
        self.assertEqual(collab_tasks.queued_revisions(self.project, "CARD-001"), [])

    def _run(self, script, **extra):
        card = Card("CARD-001", state="待派发")
        run_card(card, collab_dir=self.collab, worktree=self.tmp.name, test_command=["t"],
                 dev_adapter="cursor", test_adapter="codex", ask_fn=script.ask,
                 judge_fn=script.judge, reviewer_adapter="agy", **extra)
        return card

    def test_change_confirmed_during_review_takes_effect_after_review_passes(self):
        queued = {"done": False}

        def on_ask(adapter, prompt):
            # 复查员在复查时，用户确认了一条修改
            if adapter == "agy" and not queued["done"]:
                queued["done"] = True
                collab_tasks.queue_revision(self.project, "CARD-001", "汇总放在按钮旁边")

        script = _Script([PASS, PASS], outputs={"agy": "结论：通过\n没问题"}, on_ask=on_ask)
        card = self._run(script)
        kinds = self._kinds()
        self.assertIn("改要求生效", kinds)
        self.assertLess(kinds.index("追加要求"), kinds.index("改要求生效"), "先记成追加要求，再说已生效")
        after = kinds[kinds.index("改要求生效"):]
        self.assertEqual(after[-1], "结束")
        # 生效后又走了一轮：写测试（只补新要求）→ 写代码 → 判定 → 复查 → 结束
        testers = [p for a, p in script.prompts if a == "codex"]
        self.assertEqual(len(testers), 2)
        self.assertIn("汇总放在按钮旁边", testers[1], "第二次写测试要带上新要求")
        self.assertEqual(card.state, "待验收")
        self.assertEqual(collab_tasks.queued_revisions(self.project, "CARD-001"), [])
        self.assertIn("汇总放在按钮旁边", (self.cdir / "需求卡.md").read_text(encoding="utf-8"))

    def test_change_confirmed_before_rework_rides_along_next_round(self):
        queued = {"done": False}

        def on_ask(adapter, prompt):
            if adapter == "cursor" and not queued["done"]:
                queued["done"] = True
                collab_tasks.queue_revision(self.project, "CARD-001", "再加一条减法")

        script = _Script(["没过", PASS, PASS], outputs={"agy": "结论：通过"}, on_ask=on_ask)
        card = self._run(script)
        self.assertIn("改要求生效", self._kinds())
        self.assertEqual(card.state, "待验收")
        self.assertIn("再加一条减法", (self.cdir / "需求卡.md").read_text(encoding="utf-8"))

    def test_bad_queued_change_is_reported_and_dropped_not_stuck(self):
        def on_ask(adapter, prompt):
            if adapter == "agy":
                path = self.cdir / collab_tasks.QUEUED_REVISIONS
                if not path.exists():
                    path.write_text(json.dumps({"text": "", "time": 1}, ensure_ascii=False) + "\n", encoding="utf-8")

        script = _Script([PASS], outputs={"agy": "结论：通过"}, on_ask=on_ask)
        card = self._run(script)
        self.assertIn("改要求没生效", self._kinds())
        self.assertEqual(card.state, "待验收")
        self.assertEqual(collab_tasks.queued_revisions(self.project, "CARD-001"), [])


if __name__ == "__main__":
    unittest.main()


class QueueReviewFixTest(QueueTest):
    """复查第 1 轮（严重 2、3，中等 4、5、6、7）补的场景。"""

    def test_two_structured_changes_both_take_effect(self):
        (self.cdir / "需求卡.md").write_text("做一个页面\n\n## 验收标准\n1. 标题黑色\n2. 单击保存\n", encoding="utf-8")
        collab_tasks.queue_revision(self.project, "CARD-001", "标题改蓝色", criteria_base=["标题黑色", "单击保存"],
                                    criteria_changes=[{"replaceNth": 1, "text": "标题蓝色"}])
        collab_tasks.queue_revision(self.project, "CARD-001", "改双击保存", criteria_base=["标题黑色", "单击保存"],
                                    criteria_changes=[{"replaceNth": 2, "text": "双击保存"}])
        applied, failed = collab_tasks.apply_queued_items(
            self.project, "CARD-001", collab_tasks.take_queued_revisions(self.project, "CARD-001"))
        self.assertEqual(failed, [])
        self.assertEqual(collab_tasks.current_criteria(self.project, "CARD-001"), ["标题蓝色", "双击保存"])

    def test_append_while_taking_is_not_lost(self):
        import threading
        import time as _time
        collab_tasks.queue_revision(self.project, "CARD-001", "第一条")
        real = collab_tasks._read_queue
        reading = threading.Event()

        def slow_read(path):
            items = real(path)
            reading.set()
            _time.sleep(0.3)
            return items

        with mock.patch.object(collab_tasks, "_read_queue", side_effect=slow_read):
            taker = threading.Thread(target=lambda: taken.extend(collab_tasks.take_queued_revisions(self.project, "CARD-001")))
            taken = []
            taker.start()
            reading.wait(2)
            adder = threading.Thread(target=lambda: collab_tasks.queue_revision(self.project, "CARD-001", "第二条"))
            adder.start()
            taker.join(5)
            adder.join(5)
        self.assertEqual([t["text"] for t in taken], ["第一条"])
        self.assertEqual([q["text"] for q in collab_tasks.queued_revisions(self.project, "CARD-001")], ["第二条"],
                         "取走时刚追加的那条要留在队里，不能跟着一起删掉")

    def test_new_round_gets_its_own_rework_allowance(self):
        queued = {"done": False}

        def on_ask(adapter, prompt):
            if adapter == "agy" and not queued["done"]:
                queued["done"] = True
                collab_tasks.queue_revision(self.project, "CARD-001", "再加减法")

        script = _Script([PASS, "没过", PASS], outputs={"agy": "结论：通过"}, on_ask=on_ask)
        card = Card("CARD-001", state="待派发", rework=3)
        run_card(card, collab_dir=self.collab, worktree=self.tmp.name, test_command=["t"], dev_adapter="cursor",
                 test_adapter="codex", ask_fn=script.ask, judge_fn=script.judge, reviewer_adapter="agy")
        self.assertEqual(card.state, "待验收", "新要求这一轮第一次测试没过，要有返工机会")

    def test_review_notes_kept_when_change_arrives_during_rejected_review(self):
        reviews = iter(["结论：不通过\n1. 【严重】标记XYZ 按钮点了没反应", "结论：通过", "结论：通过"])

        class Script(_Script):
            def ask(self, adapter, prompt, cwd, **kwargs):
                self.prompts.append((adapter, prompt))
                if adapter == "agy":
                    if len([a for a, _ in self.prompts if a == "agy"]) == 1:
                        collab_tasks.queue_revision(self.project_dir, "CARD-001", "标题改蓝色")
                    return AgentResult(ok=True, output=next(reviews), attempts=1)
                return AgentResult(ok=True, output=f"{adapter} 的回复", attempts=1)

        script = Script([PASS, PASS, PASS])
        script.project_dir = self.project
        card = self._run(script)
        devs = [p for a, p in script.prompts if a == "cursor"]
        self.assertIn("XYZ", devs[1], "复查退回后写代码的要拿到复查意见")
        self.assertIn("改要求生效", self._kinds())
        self.assertEqual(card.state, "待验收")

    def test_stopped_card_flushes_queue_and_relaunches(self):
        (self.cdir / "开工.json").write_text(json.dumps({"test_command": "python3 -m unittest"}), encoding="utf-8")
        collab_tasks.queue_revision(self.project, "CARD-001", "汇总放按钮旁边")
        launched = []
        with mock.patch.object(clerk_tasks, "running", return_value=None), \
                mock.patch.object(clerk_tasks, "merge_unfinished", return_value=False), \
                mock.patch.object(clerk_tasks, "card_phase", return_value={"code": "review", "actions": ["accept", "revise"]}), \
                mock.patch.object(clerk_tasks, "sync_copy", return_value=[]), \
                mock.patch.object(clerk_tasks, "launch", side_effect=lambda *a: launched.append(a) or 1):
            self.assertTrue(clerk_tasks.flush_queued_if_idle(str(self.project), "CARD-001"))
        self.assertEqual(len(launched), 1)
        self.assertIn("汇总放按钮旁边", (self.cdir / "需求卡.md").read_text(encoding="utf-8"))
        self.assertIn("改要求生效", self._kinds())
        self.assertEqual(collab_tasks.queued_revisions(self.project, "CARD-001"), [])

    def test_still_running_card_is_not_flushed(self):
        collab_tasks.queue_revision(self.project, "CARD-001", "等会儿")
        with mock.patch.object(clerk_tasks, "running", side_effect=lambda p, k: {"card": "CARD-001"} if k == "run" else None):
            self.assertFalse(clerk_tasks.flush_queued_if_idle(str(self.project), "CARD-001"))
        self.assertEqual(len(collab_tasks.queued_revisions(self.project, "CARD-001")), 1)

    def test_change_news_reaches_task_dialogue(self):
        for kind in ("改要求排队", "改要求生效", "改要求没生效"):
            self.assertIn(kind, clerk_tasks._NEWS_KINDS)


class QueueRound2Test(QueueTest):
    """复查第 2 轮严重 1、中等 2。"""

    def _idle_patches(self):
        return [
            mock.patch.object(clerk_tasks, "running", return_value=None),
            mock.patch.object(clerk_tasks, "merge_unfinished", return_value=False),
            mock.patch.object(clerk_tasks, "card_phase", return_value={"code": "review", "actions": ["accept", "revise"]}),
            mock.patch.object(clerk_tasks, "sync_copy", return_value=[]),
            mock.patch.object(clerk_tasks, "launch", return_value=1),
        ]

    def test_two_flushes_at_once_apply_both_changes(self):
        import contextlib
        import threading
        import time as _time
        (self.cdir / "需求卡.md").write_text("做一个页面\n\n## 验收标准\n1. 标题黑色\n2. 单击保存\n", encoding="utf-8")
        (self.cdir / "开工.json").write_text(json.dumps({"test_command": "python3 -m unittest"}), encoding="utf-8")
        collab_tasks.queue_revision(self.project, "CARD-001", "标题改蓝色", criteria_changes=[{"replaceNth": 1, "text": "标题蓝色"}])
        real = collab_tasks._apply_criteria_changes

        def slow(body, base, changes):
            out = real(body, base, changes)
            _time.sleep(0.4)   # 算好了还没写回：没有锁时第二个调用会拿同一份旧内容
            return out

        with contextlib.ExitStack() as stack:
            for p in self._idle_patches():
                stack.enter_context(p)
            stack.enter_context(mock.patch.object(collab_tasks, "_apply_criteria_changes", side_effect=slow))
            a = threading.Thread(target=clerk_tasks.flush_queued_if_idle, args=(str(self.project), "CARD-001"))
            a.start()
            _time.sleep(0.15)
            collab_tasks.queue_revision(self.project, "CARD-001", "改双击保存", criteria_changes=[{"replaceNth": 2, "text": "双击保存"}])
            b = threading.Thread(target=clerk_tasks.flush_queued_if_idle, args=(str(self.project), "CARD-001"))
            b.start()
            a.join(10)
            b.join(10)
        self.assertEqual(collab_tasks.current_criteria(self.project, "CARD-001"), ["标题蓝色", "双击保存"])
        self.assertEqual(collab_tasks.queued_revisions(self.project, "CARD-001"), [])

    def test_patrol_flushes_queue_without_task_page_open(self):
        import contextlib
        (self.cdir / "开工.json").write_text(json.dumps({"test_command": "python3 -m unittest"}), encoding="utf-8")
        (self.cdir / "来源.json").write_text("{}", encoding="utf-8")
        collab_tasks.queue_revision(self.project, "CARD-001", "汇总放按钮旁边")
        launched = []
        watcher = clerk_tasks.TaskWatcher(mock.MagicMock())
        with contextlib.ExitStack() as stack:
            for p in self._idle_patches():
                stack.enter_context(p)
            stack.enter_context(mock.patch.object(clerk_tasks, "launch", side_effect=lambda *a: launched.append(a) or 1))
            stack.enter_context(mock.patch.object(watcher, "_play_card_news"))
            stack.enter_context(mock.patch.object(watcher, "_offer_saved_revise"))
            watcher._tick_card(str(self.project), self.cdir)
        self.assertEqual(len(launched), 1, "卡停下了，不用打开任务页，巡视也要把排着的修改生效并开工")
        self.assertEqual(collab_tasks.queued_revisions(self.project, "CARD-001"), [])


class QueueRound3Test(QueueRound2Test):
    """复查第 3 轮严重：卡刚停下时，自动生效和用户再点“确认改”不能互相覆盖。"""

    def test_direct_confirm_and_auto_flush_do_not_overwrite_each_other(self):
        import contextlib
        import threading
        import time as _time
        (self.cdir / "需求卡.md").write_text("做一个页面\n\n## 验收标准\n1. 标题黑色\n2. 单击保存\n", encoding="utf-8")
        (self.cdir / "开工.json").write_text(json.dumps({"test_command": "python3 -m unittest"}), encoding="utf-8")
        collab_tasks.queue_revision(self.project, "CARD-001", "标题改蓝色", criteria_changes=[{"replaceNth": 1, "text": "标题蓝色"}])
        real_write = collab_tasks._atomic_write_text

        def slow_write(path, text):
            _time.sleep(0.4)   # 读完算好、还没写回：没有锁时另一个入口会拿同一份旧内容
            return real_write(path, text)

        errors = []

        def direct():
            try:
                clerk_tasks.apply_revise(str(self.project), "CARD-001", "改双击保存",
                                         criteria_base=["标题黑色", "单击保存"],
                                         criteria_changes=[{"replaceNth": 2, "text": "双击保存"}])
            except RuntimeError as exc:
                errors.append(str(exc))

        with contextlib.ExitStack() as stack:
            for p in self._idle_patches():
                stack.enter_context(p)
            stack.enter_context(mock.patch.object(collab_tasks, "_atomic_write_text", side_effect=slow_write))
            a = threading.Thread(target=clerk_tasks.flush_queued_if_idle, args=(str(self.project), "CARD-001"))
            a.start()
            _time.sleep(0.15)
            b = threading.Thread(target=direct)
            b.start()
            a.join(10)
            b.join(10)
        final = collab_tasks.current_criteria(self.project, "CARD-001")
        self.assertEqual(final[0], "标题蓝色", "自动生效的那条不能被直接确认覆盖掉")
        # 第二条要么也生效了，要么照实告诉你“验收标准变了，请刷新”（不是悄悄丢掉再说已交给开发）
        self.assertTrue(final[1] == "双击保存" or any("变化" in e for e in errors), (final, errors))
