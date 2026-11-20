"""第三批卡 D1 验收测试（Claude 写；施工方不许改，补充测试写进 tests/test_batch3_d1_extra*.py）。

依据 docs/跑卡流程调整/需求共识-第三批.md、方案-第三批.md、施工单-第三批D1.md：
C1 不再出现“调度员” · C2 测试超时停下后接着跑只重跑测试 · C3 复查停下红叉挂复查格 · C8 写方案格只写写方案的事
· C9 “已放进项目”不带分支名 · C10 用量小字说明 · C11 Claude 亲自做的卡 · C12 被限流停下说“服务限流”
"""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

import test_redfirst_acceptance as rf
from acceptance_helpers import ROOT, STATIC, ProjectCase
from collab.agents import AgentResult
from collab.flow import read_flow, record
from collab.judge import PASS, TIMEOUT, Verdict
from collab.state import Card
from test_acceptance_20_merge import MergeCase


def view_of(project, card_id="CARD-001"):
    from desktop.view import card_view
    return card_view(project, card_id)


def station(view, key):
    return next(s for s in view["stations"] if s["key"] == key)


class C1NoDispatcherWords(rf.Base):
    def test_stop_words_never_say_dispatcher(self):
        def ask(adapter, prompt, cwd, **kw):
            if adapter == "cursor":
                return AgentResult(ok=False, output="报错了：连不上", attempts=1, final="")
            return self.ask_fn(adapter, prompt, cwd, **kw)
        self.verdicts = [rf.FAIL]
        card = run = None
        from collab.dispatch import run_card
        card = run_card(Card("CARD-001", state="待派发"), self.collab, self.project, rf.CMD, "cursor", "codex",
                        ask_fn=rf.listed_ask(ask), judge_fn=self.judge_fn, reviewer_adapter="agy",
                        require_tests=True)[0]
        self.assertEqual(card.state, "需Claude介入")
        for e in read_flow(self.cdir):
            for field in ("summary", "say"):
                self.assertNotIn("调度员", str(e.get(field) or ""), f"{e.get('kind')} 的 {field} 还写着调度员")


class C1ViewWords(ProjectCase):
    def test_old_records_show_program_not_dispatcher(self):
        cdir = self.make_card(state="需Claude介入", plan=True)
        record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工")
        record(cdir, "交接资料", src="调度员", dst="开发员", ok=True, summary="调度员备好了交接资料（300 字）")
        record(cdir, "派活", src="调度员", dst="开发员", tool="cursor", step="开发", round=1, summary="写代码")
        record(cdir, "转人工", src="程序", dst="调度员", summary="全部测试没过，需要调度员看看")
        view = view_of(self.project)
        self.assertNotIn("调度员", json.dumps(view, ensure_ascii=False), "右栏、交接列表、顶部一句话都不出现调度员")

    def test_chat_line_has_no_dispatcher_prefix(self):
        src = (ROOT / "desktop" / "view.py").read_text(encoding="utf-8")
        self.assertNotIn('f"调度员 {disp_who}', src)
        self.assertNotIn("需要调度员介入", src)


class C2ResumeAfterTestTimeout(rf.Base):
    def test_resume_reruns_tests_only(self):
        self.verdicts = [rf.FAIL, TIMEOUT]   # 先红检查没过（正常）；写完代码判定超时 → 停下
        card = self.go()
        self.assertEqual(card.state, "需Claude介入")
        self.calls.clear()
        card = self.go(card=Card.load(self.cdir / "状态.json"))
        self.assertNotIn("代码", self.who(), "代码已写好、只是测试超时：接着跑不再派写代码的")
        self.assertEqual(card.state, "待验收")


class C3AndC8Stations(ProjectCase):
    def stopped_by_review(self):
        cdir = self.make_card(state="需Claude介入", plan=True)
        record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工")
        record(cdir, "派活", src="调度员", dst="复查员", tool="agy", step="复查", round=1, summary="复查")
        record(cdir, "回复", src="复查员", dst="调度员", tool="agy", step="复查", round=1, ok=True,
               summary="结论：不通过")
        record(cdir, "复查结论", src="程序", result="退回", summary="复查退回 2 件")
        record(cdir, "转人工", src="程序", dst="调度员", cause="复查不通过", summary="复查不通过（已自动退回 2 次）")
        return cdir

    def test_review_stop_marks_review_not_plan(self):
        self.stopped_by_review()
        view = view_of(self.project)
        self.assertNotEqual(station(view, "disp")["state"], "fail", "写方案没错，不挂红叉")
        self.assertEqual(station(view, "rev")["state"], "fail", "红叉挂在复查格")

    def test_plan_station_after_done_says_plan_only(self):
        for state in ("待验收", "已合并"):
            with self.subTest(state=state):
                cdir = self.make_card(card_id="CARD-002" if state == "已合并" else "CARD-001", state=state, plan=True)
                record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工")
                view = view_of(self.project, cdir.name)
                detail = station(view, "disp")["detail"]
                self.assertIn("方案已定", detail)
                self.assertNotIn("已向你汇报", detail)
                self.assertNotIn("已放进项目", detail)


class C9MergeWords(MergeCase):
    def test_merge_line_has_no_branch_name(self):
        self.prepared()
        self.assertTrue(self.merge()["ok"])
        merged = [e for e in read_flow(self.collab / "cards" / "CARD-001") if e.get("kind") == "合并"]
        self.assertEqual(merged[-1]["summary"], "已放进项目")


class C10UsageNote(unittest.TestCase):
    def test_usage_note(self):
        js = (STATIC / "panel.js").read_text(encoding="utf-8")
        self.assertIn("每答完一轮才更新一次", js)


class C11ByClaude(ProjectCase):
    def test_cli_marks_card_and_phase_has_no_buttons(self):
        req = Path(self.tmp) / "需求.md"
        req.write_text("# Claude 亲自做的活\n\n## 要做什么\n改一处\n", encoding="utf-8")
        code, out = self.cli("init", self.project)   # 先接入：建卡不替用户自动接入（11-19 复查第 8 条，Claude 改检查项）
        self.assertEqual(code, 0, out)
        code, out = self.cli("new-card", self.project, "CARD-005", "--requirement", str(req), "--by-claude")
        self.assertEqual(code, 0, out)
        from collab.phase import card_phase
        phase = card_phase(self.project, "CARD-005")
        self.assertIn("Claude 亲自做", phase["label"])
        self.assertEqual(phase["actions"], [], "不出批准、开工按钮")

    def test_normal_cards_unchanged(self):
        self.make_card(state="待对齐")
        from collab.phase import card_phase
        self.assertNotIn("Claude 亲自做", card_phase(self.project, "CARD-001")["label"])


class C12RateLimitWords(rf.Base):
    def test_rate_limited_stop_says_so(self):
        def ask(adapter, prompt, cwd, **kw):
            if adapter == "cursor":
                return AgentResult(ok=False, attempts=1, final="",
                                   output="ERROR: exceeded retry limit, last status: 429 Too Many Requests")
            return self.ask_fn(adapter, prompt, cwd, **kw)
        self.verdicts = [rf.FAIL, rf.FAIL]
        from collab.dispatch import run_card
        card = run_card(Card("CARD-001", state="待派发"), self.collab, self.project, rf.CMD, "cursor", "codex",
                        ask_fn=rf.listed_ask(ask), judge_fn=self.judge_fn, reviewer_adapter="agy",
                        require_tests=True)[0]
        self.assertEqual(card.state, "需Claude介入")
        stop = [e for e in read_flow(self.cdir) if e.get("kind") == "转人工"][-1]
        words = (stop.get("summary") or "") + (stop.get("say") or "")
        self.assertIn("限流", words)
        self.assertNotIn("没回话", words)


if __name__ == "__main__":
    unittest.main()
