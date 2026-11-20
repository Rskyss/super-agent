"""跑卡流程调整第二批 · 卡 C1.5（复查意见固定格式）验收测试，Claude 先写（施工前应为红）。

口径见 docs/跑卡流程调整/方案-C1.5.md（用户 2026-11-18 批准并补两处）：
1 复查员回复末尾附固定格式清单（```json 代码块，含“结论”“问题”），程序只认清单；每条问题写齐
  级别、来源、类型、现象、位置、证据（实跑：命令+输出；推演：对照的 文件:行号）
2 格式不合格：请复查员“按固定格式重写”一次（不用重新复查）；还不合格：说“不通过”的整段原文退回，说“通过”的停下
3 结论“通过”却列了“这次改的”问题，按不通过处理
4 每一轮复查都提醒：老测试被改/删；老测试或本卡新写的测试里新加了“跳过”，要求复查员判断，不合理判不通过
5 收口、补测清单、停下提示、右栏、接着跑都只读清单；C1 遗留第 1–8 条各有场景守住
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from collab.agents import AgentResult
from collab.dispatch import _prior_reviews, _short_test_output, _tester_step_done, run_card
from collab.flow import read_flow, record
from collab.judge import PASS, Verdict
from collab.state import Card
from test_flowfix4_converge import Base

ROOT = Path(__file__).resolve().parents[1]


def read_review_list(text):
    from collab.dispatch import read_review_list as real   # 施工前还没有这个函数：每条测试各自红，不连坐
    return real(text)


def item(level="中等", source="这次改的", kind="功能问题", what="保存后草稿丢失", where="app/save.py:12",
         proof=None):
    return {"级别": level, "来源": source, "类型": kind, "现象": what, "位置": where,
            "证据": proof or {"方式": "实跑", "命令": "python3 -m unittest tests.test_save", "输出": "FAIL: test_draft"}}


def review(verdict, items=(), prose="复查过程：看了改动。"):
    block = json.dumps({"结论": verdict, "问题": list(items)}, ensure_ascii=False, indent=1)
    return f"结论：{verdict}\n\n{prose}\n\n复查清单：\n```json\n{block}\n```\n"


OLD = item(source="原来就有的", what="切换对话时卡在正在取回", where="desktop/chat.js:600")
NOW = item(what="保存后草稿丢失")
SEVERE = item(level="严重", what="重启后数据全丢", where="collab/store.py:40")


class ReadList(unittest.TestCase):
    def test_valid_list(self):
        data, errors = read_review_list(review("不通过", [NOW, OLD]))
        self.assertEqual(errors, [])
        self.assertEqual(data["结论"], "不通过")
        self.assertEqual([p["现象"] for p in data["问题"]], ["保存后草稿丢失", "切换对话时卡在正在取回"])

    def test_inference_evidence_needs_file_and_line(self):
        ok = item(proof={"方式": "推演", "对照": "docs/规则.md:39 与 collab/dispatch.py:2015 说法不一致"})
        self.assertEqual(read_review_list(review("不通过", [ok]))[1], [])
        bad = item(proof={"方式": "推演", "对照": "规则和派活交代对不上"})
        self.assertTrue(read_review_list(review("不通过", [bad]))[1], "推演必须写出对照的 文件:行号")

    def test_missing_fields_are_errors(self):
        for drop in ("级别", "来源", "类型", "现象", "位置", "证据"):
            p = dict(NOW)
            p.pop(drop)
            self.assertTrue(read_review_list(review("不通过", [p]))[1], f"缺“{drop}”要算不合格")
        no_cmd = item(proof={"方式": "实跑", "输出": "FAIL"})
        self.assertTrue(read_review_list(review("不通过", [no_cmd]))[1], "实跑要写命令")
        no_out = item(proof={"方式": "实跑", "命令": "python3 -m unittest"})
        self.assertTrue(read_review_list(review("不通过", [no_out]))[1], "实跑要写输出")

    def test_no_block_or_bad_json_or_empty_reject(self):
        self.assertTrue(read_review_list("结论：不通过\n1. 【中等】按钮没反应。")[1])
        self.assertTrue(read_review_list("结论：不通过\n```json\n{\"结论\": \"不通过\", \n```")[1])
        self.assertTrue(read_review_list(review("不通过", []))[1], "不通过却一条问题都没列，不合格")

    def test_prose_does_not_count(self):
        text = review("不通过", [OLD], prose="**【严重】【这次改的】保存后数据丢失。**（正文里随手写的，不在清单里）")
        data, errors = read_review_list(text)
        self.assertEqual(errors, [])
        self.assertEqual(len(data["问题"]), 1, "程序只认清单，不从正文里猜")


class Flow(Base):
    def run_flow(self, reviews, judge=None, tester_hook=None):
        reviews = list(reviews)
        calls = []
        wt = self.worktree

        def ask_fn(adapter, prompt, cwd, **kw):
            task = prompt.split("【规则完】")[-1]  # 11-20 起每段话前面都带规则包（规则里也有“只改测试”这类字），只按后面的具体交代分角色
            if "按固定格式重写" in task:
                calls.append(("reformat", prompt, kw.get("models") or {}))
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            if "只读复查" in task:
                calls.append(("review", prompt, kw.get("models") or {}))
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            if "编写自动化测试" in task or "只改测试" in task:
                calls.append(("test", prompt, kw.get("models") or {}))
                (wt / "tests" / "test_a.py").write_text("# 测试\n", encoding="utf-8")
                if tester_hook:
                    tester_hook(wt)
                return AgentResult(ok=True, output="", attempts=1, final="汇报：写了测试")
            calls.append(("dev", prompt, kw.get("models") or {}))
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        role = {"复查员": {"codex": "gpt-6.1-sol", "codex_effort": "xhigh"}}
        card, _ = run_card(Card("CARD-001", state="待派发"), self.collab, self.worktree, "true", "qwen", "codex",
                           ask_fn=ask_fn, judge_fn=judge or (lambda *a, **k: Verdict(PASS, 0, "ok")),
                           reviewer_adapter="codex", role_models=role)
        return card, calls

    @staticmethod
    def of(calls, who):
        return [c for c in calls if c[0] == who]

    def conclusions(self):
        return [e for e in read_flow(self.cdir) if e.get("kind") == "复查结论"]


class Format(Flow):
    def test_review_prompt_asks_for_list_with_evidence(self):
        _, calls = self.run_flow([review("通过")])
        p = self.of(calls, "review")[0][1]
        for word in ("复查清单", "```json", "证据", "实跑", "推演", "级别", "来源", "类型", "现象", "位置"):
            self.assertIn(word, p, f"复查交代要写明固定格式：{word}")
        self.assertIn("不许因为跑不了就不报", p)

    def test_bad_format_reformat_once_then_ok(self):
        card, calls = self.run_flow(["结论：通过\n看过了，没问题。", review("通过")])
        self.assertEqual(len(self.of(calls, "reformat")), 1, "格式不合格先请复查员按格式重写一次")
        self.assertIn("不用重新复查", self.of(calls, "reformat")[0][1])
        self.assertEqual(card.state, "待验收")

    def test_bad_twice_reject_returns_whole_text(self):
        # （C1.8 按新口径改，登记见 docs/跑卡流程调整/方案-C1.8.md 第 3 节）：重写还不合格、结论不通过 → 停下转 Claude，不再整段退回写代码的
        raw = "结论：不通过\n保存后草稿丢失，位置 app/save.py:12。另外按钮颜色不对。"
        card, calls = self.run_flow([raw, raw.replace("按钮颜色不对", "重写时也没写清单")])
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(len(self.of(calls, "dev")), 1, "不再把整段原话退回写代码的")
        stop = [e for e in read_flow(self.cdir) if e.get("kind") == "转人工"][-1]
        from collab.flow import text_of
        kept = text_of(self.cdir, stop) or ""
        self.assertIn("按钮颜色不对", kept, "原话存进转人工记录")
        self.assertIn("重写时也没写清单", kept, "重写也存进去")
        self.assertIn("看不懂", stop.get("say") or "")   # C1.8a 复查第 2 轮后定：停下说明不从原话里挑问题（挑就会漏），只提示“原话里写到了严重”，完整原话在转人工记录里

    def test_bad_twice_pass_stops(self):
        card, calls = self.run_flow(["结论：通过\n没问题。", "结论：通过\n真的没问题。"])
        self.assertEqual(card.state, "需Claude介入", "格式不合格一律不自动放行")
        self.assertEqual(len(self.of(calls, "dev")), 1)

    def test_pass_with_this_time_issue_is_reject(self):
        card, calls = self.run_flow([review("通过", [NOW]), review("通过")])
        self.assertEqual(len(self.of(calls, "dev")), 2, "结论通过却列了这次改的问题，按不通过处理")
        self.assertEqual(self.conclusions()[0].get("result"), "退回")


class Leftovers(Flow):
    """C1 遗留问题第 1–8 条（docs/跑卡流程调整/C1遗留问题.md）。"""

    def test_1_old_only_passes_and_now_item_anywhere_returns(self):
        card, _ = self.run_flow([review("不通过", [OLD])])
        self.assertEqual(card.state, "待验收", "清单里只有原来就有的：放行")
        self.assertEqual(self.conclusions()[-1].get("result"), "通过")
        self.tearDown()
        self.setUp()
        card, calls = self.run_flow([review("不通过", [OLD, NOW]), review("通过")])
        self.assertIn("保存后草稿丢失", self.of(calls, "dev")[1][1])

    def test_2_stop_lists_severe_and_does_not_call_it_minor(self):
        from collab.phase import card_phase
        bad = review("不通过", [item(level="轻微", what="提示字太小"), SEVERE])
        card, _ = self.run_flow([bad, bad, bad])
        self.assertEqual(card.state, "需Claude介入")
        ph = card_phase(str(self.collab.parent), "CARD-001")
        self.assertIn("重启后数据全丢", ph.get("issues", ""))
        stop = [e for e in read_flow(self.cdir) if e.get("kind") == "转人工"][-1]
        self.assertNotIn("都是小问题", json.dumps(stop, ensure_ascii=False))

    def test_3_guard_gets_every_returned_item(self):
        two = review("不通过", [NOW, item(what="导出按钮没反应", where="app/export.py:3"), OLD])
        _, calls = self.run_flow([two, review("通过")])
        guard = self.of(calls, "test")[1][1]
        self.assertIn("保存后草稿丢失", guard)
        self.assertIn("导出按钮没反应", guard)
        self.assertNotIn("切换对话时卡在正在取回", guard, "原来就有的不补测")

    def test_3b_test_problem_and_product_problem_both_dispatched(self):
        mixed = review("不通过", [item(kind="测试写错", what="测试把编号写成 tavily"), NOW])
        _, calls = self.run_flow([mixed, review("通过")])
        tests = [c[1] for c in self.of(calls, "test")[1:]]
        self.assertTrue(any("tavily" in t for t in tests), "测试写错的派改测试")
        self.assertTrue(any("保存后草稿丢失" in t for t in tests), "功能问题派补测试")

    def test_4_quoting_tags_in_prose_does_not_return(self):
        card, calls = self.run_flow([review("不通过", [OLD], prose="原因：旧页面把【严重】字样显示错了。")])
        self.assertEqual(card.state, "待验收")
        self.assertEqual(len(self.of(calls, "dev")), 1)

    def test_6_view_follows_conclusion_record(self):
        from desktop.view import card_view
        card, _ = self.run_flow([review("不通过", [OLD])])
        rev = next(s for s in card_view(str(self.collab.parent), "CARD-001")["stations"] if s.get("key") == "rev")
        self.assertEqual(rev.get("state"), "done")
        self.assertIn("通过", rev.get("detail", ""))

    def test_6b_half_width_colon_reject_not_green(self):
        from desktop.view import card_view
        record(self.cdir, "回复", src="复查员", dst="调度员", ok=True, step="复查", summary="结论:不通过", text="结论:不通过")
        record(self.cdir, "复查结论", src="程序", result="退回", summary="复查退回 1 件")
        Card("CARD-001", state="开发中").save(self.cdir / "状态.json")
        rev = next(s for s in card_view(str(self.collab.parent), "CARD-001")["stations"] if s.get("key") == "rev")
        self.assertNotEqual(rev.get("state"), "done", "复查结论是退回，不能亮绿勾")


class LeftoverHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cdir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_5_guard_done_needs_cleanup_record(self):
        record(self.cdir, "回复", src="复查员", dst="调度员", ok=True, step="复查", summary="结论：不通过")
        record(self.cdir, "回复", src="测试员", dst="调度员", ok=True, step="补测试", summary="写了")
        self.assertFalse(_tester_step_done(self.cdir, "补测试"), "只有回复、没收尾记录，不算补测完成")
        record(self.cdir, "测试收尾", src="程序", step="补测试", summary="补测收尾完成")
        self.assertTrue(_tester_step_done(self.cdir, "补测试"))

    def test_7_only_valid_conclusions_count_for_lowering(self):
        record(self.cdir, "回复", src="复查员", dst="调度员", ok=True, step="复查", summary="还没有结论")
        self.assertEqual(_prior_reviews(self.cdir), 0, "没有有效结论的那轮不算，下一轮不能降档")
        record(self.cdir, "复查结论", src="程序", result="退回", summary="复查退回 1 件")
        self.assertEqual(_prior_reviews(self.cdir), 1)

    def test_8_summary_keeps_test_file_line_and_drops_noise(self):
        frames = "".join(f"  File \"/lib/deep{i}.py\", line {i}, in f{i}\n    call{i}()\n" for i in range(10))
        out = ("- 无关日志：连接池已就绪\n- 无关日志：缓存命中\n" + "=" * 70 +
               "\nFAIL: test_draft (tests.test_save.Save)\n" + "-" * 70 +
               "\nTraceback (most recent call last):\n  File \"/w/tests/test_save.py\", line 12, in test_draft\n"
               "    self.assertEqual(save(), 'ok')\n" + frames + "AssertionError: 'lost' != 'ok'\n\n" + "-" * 70 +
               "\nRan 3 tests in 0.1s\n\nFAILED (failures=1)\n")
        short = _short_test_output(out)
        self.assertIn("tests/test_save.py\", line 12", short, "保住测试文件那一行")
        self.assertIn("AssertionError", short)
        self.assertNotIn("连接池已就绪", short, "无关的“- ”日志不要")


class Skips(Flow):
    """口径第 4 条：每一轮复查都提醒老测试改动和新加的跳过（老测试、本卡新写的测试都扫）。"""

    def setUp(self):
        super().setUp()
        wt = self.worktree
        (wt / "tests" / "test_old.py").write_text(
            "import unittest\n\nclass Old(unittest.TestCase):\n    def test_x(self):\n        self.assertTrue(True)\n",
            encoding="utf-8")
        for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                    ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"]):
            subprocess.run(cmd, cwd=str(wt), check=True, capture_output=True)

    def hook(self, wt):
        old = wt / "tests" / "test_old.py"
        old.write_text(old.read_text(encoding="utf-8").replace(
            "    def test_x", "    @unittest.skip(\"先跳过\")\n    def test_x"), encoding="utf-8")
        (wt / "tests" / "test_new_feature.py").write_text(
            "import unittest\n\nclass New(unittest.TestCase):\n    def test_y(self):\n"
            "        self.skipTest(\"以后再说\")\n", encoding="utf-8")

    def test_first_review_lists_changed_old_tests_and_new_skips(self):
        _, calls = self.run_flow([review("通过")], tester_hook=self.hook)
        p = self.of(calls, "review")[0][1]
        self.assertIn("test_old.py", p, "第一轮也提醒老测试被改过")
        self.assertIn("跳过", p)
        self.assertIn("test_new_feature.py", p, "本卡新写的测试里的跳过也要列")
        self.assertIn("不合理", p)
        self.assertIn("不通过", p)

    def test_skip_patterns(self):
        from collab.dispatch import _SKIP_PATTERN
        for line in ("@unittest.skip('x')", "self.skipTest('x')", "@unittest.skipIf(True, 'x')",
                     "@unittest.skipUnless(False, 'x')", "@pytest.mark.skip(reason='x')", "pytest.skip('x')",
                     "it.skip('x', () => {})", "test.skip('x', () => {})", "describe.skip('x', () => {})",
                     "xit('x', () => {})"):
            self.assertTrue(_SKIP_PATTERN.search(line), line)
        self.assertFalse(_SKIP_PATTERN.search("def test_skip_button_hides(self):"))


# 规则文字的查验 11-20 起由规则包体检管（tests/test_rulebook_health.py，要点清单在 agents/README.md）


if __name__ == "__main__":
    unittest.main()
