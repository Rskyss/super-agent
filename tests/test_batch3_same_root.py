"""第三批三（⑱）：复查标“病根”。连续两轮“要求没写清”且同根 → 停下定规则；连续两轮“做错了”且同根 → 换备份写代码的。

依据 docs/外部项目试跑修复/第三批/方案.md 第三节。用装修打比方：要求没写清是“防水刷多高没说”，换师傅也没用；
做错了是“瓷砖缝说了 2 毫米贴成 5 毫米”，换个师傅就行。"""
import json
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import HANDOVER, run_card
from collab.flow import read_flow
from collab.judge import PASS, Verdict
from collab.state import Card


def problem(text, root=None, same=None, rule=None, level="严重"):
    p = {"级别": level, "来源": "这次改的", "类型": "功能问题", "碰上": "常见", "现象": text,
         "位置": "lib/a.ts:3", "证据": {"方式": "推演", "对照": "lib/a.ts:3 写的是旧规则"}}
    if root:
        p["病根"] = root
    if same:
        p["同根"] = same
    if rule:
        p["规则"] = rule
    return p


def reply(problems=(), checks=None, verdict=None):
    data = {"结论": verdict or ("不通过" if problems or checks else "通过"), "问题": list(problems)}
    if checks is not None:
        data["上轮核对"] = checks
    head = "结论：" + data["结论"]
    return head + "\n\n复查清单：\n```json\n" + json.dumps(data, ensure_ascii=False) + "\n```\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.collab = self.root / ".collab"
        self.worktree = self.root / "wt"
        (self.worktree / "tests").mkdir(parents=True)
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n## 要做什么\n改\n", encoding="utf-8")
        (self.cdir / HANDOVER).write_text("资料\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def go(self, reviews, draft=False, backup=None, card=None):
        if backup is not None:
            (self.collab / "配置.json").write_text(json.dumps({"backup_developer": backup}), encoding="utf-8")
        reviews = list(reviews)
        calls = {"dev": [], "review": [], "draft": []}
        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                calls["review"].append(prompt)
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0) if reviews else reply())
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_new.py").write_text("# 测试\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写了测试")
            if "拟 2～3 个" in prompt or "起草一段" in prompt:
                calls["draft"].append(prompt)
                return AgentResult(ok=True, output="", attempts=1, final="A：只遮常见写法\n---\n规则选项")
            if "下面这几条是复查退回、要改产品代码的问题" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="补了测试")
            calls["dev"].append(adapter)
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card, _ = run_card(card or Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "qwen", "codex",
                           ask_fn=ask_fn, judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
                           reviewer_adapter="codex", draft=draft)
        return card, calls

    def kinds(self, kind):
        return [e for e in read_flow(self.cdir) if e.get("kind") == kind]


class RuleNotSet(Base):
    def test_two_rounds_same_rule_stops_for_rule(self):
        r1 = reply([problem("评语里写了 G1 没遮住", root="要求没写清", rule="自由文字里组号怎么遮")])
        r2 = reply([problem("写成“第一组”也没遮住", root="要求没写清", same=1, rule="自由文字里组号怎么遮")],
                   checks=[{"编号": 1, "结果": "改好了"}])
        card, calls = self.go([r1, r2], draft=True)
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(len(calls["dev"]), 2, "第一次写代码 + 第一次退回；第二次退回前就停下")
        stop = self.kinds("转人工")[-1]
        self.assertEqual(stop["cause"], "需要定规则")
        self.assertIn("自由文字里组号怎么遮", stop["say"])
        self.assertIn("G1 没遮住", stop["say"])
        self.assertIn("第一组", stop["say"])
        self.assertTrue(calls["draft"], "起草的 AI 拟规则选项")
        self.assertIn("规则", calls["draft"][0])

    def test_different_root_returns_normally(self):
        r1 = reply([problem("评语里写了 G1 没遮住", root="要求没写清", rule="组号怎么遮")])
        r2 = reply([problem("按钮颜色不对", root="要求没写清", rule="按钮配色")], checks=[{"编号": 1, "结果": "改好了"}])
        card, calls = self.go([r1, r2])
        self.assertEqual(len(calls["dev"]), 3, "不同根照常退回")
        self.assertFalse([e for e in self.kinds("转人工") if e.get("cause") == "需要定规则"])

    def test_missing_root_returns_normally(self):
        r1 = reply([problem("A 不对")])
        r2 = reply([problem("B 不对", same=1)], checks=[{"编号": 1, "结果": "改好了"}])
        card, calls = self.go([r1, r2])
        self.assertEqual(len(calls["dev"]), 3)
        self.assertEqual(set(calls["dev"]), {"qwen"}, "病根没填当做错了处理，但没有同根的“做错了”不换人")


class DoneWrong(Base):
    def test_not_fixed_twice_switches_to_backup(self):
        r1 = reply([problem("分数少了 0.1", root="做错了")])
        r2 = reply([], checks=[{"编号": 1, "结果": "没改好"}])
        card, calls = self.go([r1, r2, reply()])
        self.assertEqual(card.state, "待验收")
        self.assertEqual(calls["dev"], ["qwen", "qwen", "codex"], "第三次写代码换成备份 Codex")
        sw = self.kinds("换写代码的")
        self.assertTrue(sw)
        self.assertEqual(sw[-1]["to"], "codex")
        self.assertIn("连续两次", sw[-1]["say"])

    def test_new_same_root_wrong_switches(self):
        r1 = reply([problem("分数少了 0.1", root="做错了")])
        r2 = reply([problem("排名也跟着错", root="做错了", same=1)], checks=[{"编号": 1, "结果": "改好了"}])
        card, calls = self.go([r1, r2, reply()])
        self.assertEqual(calls["dev"][-1], "codex")

    def test_backup_same_as_dev_does_not_switch(self):
        r1 = reply([problem("分数少了 0.1", root="做错了")])
        r2 = reply([], checks=[{"编号": 1, "结果": "没改好"}])
        card, calls = self.go([r1, r2, reply()], backup="qwen")
        self.assertEqual(set(calls["dev"]), {"qwen"})
        self.assertFalse(self.kinds("换写代码的"))


class ReviewPromptAsksRoot(unittest.TestCase):
    def test_prompt_mentions_root_fields(self):
        text = (Path(__file__).resolve().parent.parent / "agents" / "交代" / "复查" / "每轮复查.md").read_text(
            encoding="utf-8")
        self.assertIn("病根", text)
        self.assertIn("要求没写清", text)
        self.assertIn("做错了", text)
        self.assertIn("同根", text)


if __name__ == "__main__":
    unittest.main()


class Round1RootFixes(Base):
    """第三批复查第 1 轮 8、9。"""

    def test_identical_text_with_same_root_still_counts(self):
        p1 = problem("评语里写了 G1 没遮住", root="要求没写清", rule="组号怎么遮")
        p2 = dict(p1, 同根=1)
        card, calls = self.go([reply([p1]), reply([p2], checks=[{"编号": 1, "结果": "改好了"}])])
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(self.kinds("转人工")[-1]["cause"], "需要定规则")

    def test_rule_stop_cannot_just_resume(self):
        r1 = reply([problem("A 没遮住", root="要求没写清", rule="组号怎么遮")])
        r2 = reply([problem("B 没遮住", root="要求没写清", same=1, rule="组号怎么遮")],
                   checks=[{"编号": 1, "结果": "改好了"}])
        self.go([r1, r2])
        from collab.phase import card_phase
        phase = card_phase(str(self.root), "CARD-001")
        self.assertNotIn("resume", phase["actions"], "规则没定不能原样接着跑")
        self.assertIn("revise", phase["actions"])
        card, calls = self.go([], card=Card.load(self.cdir / "状态.json"))   # 硬点接着跑（终端）：照样停
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(calls["dev"], [], "没补规则，不派写代码的")


class Round2RootFixes(Base):
    def test_circled_number_does_not_crash(self):
        r1 = reply([problem("A 没遮住", root="要求没写清", rule="组号怎么遮")])
        r2 = reply([problem("B 没遮住", root="要求没写清", same="①", rule="组号怎么遮")],
                   checks=[{"编号": 1, "结果": "改好了"}])
        card, calls = self.go([r1, r2, reply()])
        self.assertEqual(card.state, "待验收", "认不出的编号当没写同根，不能让施工中断")

    def test_whole_draft_is_refused(self):
        from desktop.clerk.tasks import RULE_NOT_CHOSEN, _check_rule_chosen
        from collab.flow import record
        record(self.cdir, "转人工", src="程序", dst="调度员", cause="需要定规则", rule="组号怎么遮", summary="需要定规则")
        record(self.cdir, "起草", src="程序", dst="你", phase="好了", summary="规则选项起草好了", text="A：只遮常见\nB：只看分数")
        with self.assertRaises(RuntimeError) as got:
            _check_rule_chosen(str(self.root), "CARD-001", "A：只遮常见\nB：只看分数")
        self.assertEqual(str(got.exception), RULE_NOT_CHOSEN)
        _check_rule_chosen(str(self.root), "CARD-001", "B：市场组只看分数，不看评语")   # 挑了一个：放行


class Round3RootFixes(Base):
    def stop_for_rule(self):
        r1 = reply([problem("A 没遮住", root="要求没写清", rule="组号怎么遮")])
        r2 = reply([problem("B 没遮住", root="要求没写清", same=1, rule="组号怎么遮")],
                   checks=[{"编号": 1, "结果": "改好了"}])
        self.go([r1, r2])

    def test_touching_work_order_does_not_allow_resume(self):
        """确认复查：碰一下施工单不能当规则补好了；规则只能用“继续改”写进去。"""
        import os as _os, time as _time
        docs = self.root / "docs"
        docs.mkdir()
        order = docs / "施工单.md"
        order.write_text("# 施工单\n", encoding="utf-8")
        req = self.cdir / "需求卡.md"
        req.write_text(req.read_text(encoding="utf-8") + "\n按施工单做：docs/施工单.md\n", encoding="utf-8")
        self.stop_for_rule()
        later = _time.time() + 5
        _os.utime(order, (later, later))
        from collab.phase import card_phase
        self.assertNotIn("resume", card_phase(str(self.root), "CARD-001")["actions"])
        card, calls = self.go([], card=Card.load(self.cdir / "状态.json"))
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(calls["dev"], [])

    def test_queued_revision_waits_for_rule(self):
        self.stop_for_rule()
        (self.cdir / "开工.json").write_text(json.dumps({"test_command": "true"}), encoding="utf-8")   # 有测试命令：别的条件都满足
        from collab.tasks import QUEUED_REVISIONS
        (self.cdir / QUEUED_REVISIONS).write_text(json.dumps([{"text": "把保存按钮改成蓝色"}], ensure_ascii=False),
                                                  encoding="utf-8")
        from desktop.clerk.tasks import _flush_queued_locked
        self.assertFalse(_flush_queued_locked(str(self.root), "CARD-001", self.cdir), "等定规则时排着的修改不能让卡自己开工")


class SecondConfirmRule(Base):
    def test_whole_draft_refused_after_forced_resume(self):
        from desktop.clerk.tasks import _check_rule_chosen
        from collab.flow import record
        record(self.cdir, "转人工", src="程序", dst="调度员", cause="需要定规则", rule="组号怎么遮", summary="需要定规则")
        record(self.cdir, "起草", src="程序", dst="你", phase="好了", summary="规则选项起草好了", text="A：只遮常见\nB：只看分数")
        record(self.cdir, "转人工", src="程序", dst="调度员", cause="需要定规则", rule="组号怎么遮", summary="规则还没定")
        with self.assertRaises(RuntimeError):
            _check_rule_chosen(str(self.root), "CARD-001", "A：只遮常见\nB：只看分数")
