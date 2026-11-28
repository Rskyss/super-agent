"""v0.96 B3：点“继续改”时代码早就写好了，测试一跑就全过是对的，不再当成“测试写松了”退回重写
（train 卡 16，11-27：只要求删一条测试，程序却退回写测试的重写，测试员硬加了一条边角测试，多跑一轮）。
第一次开工照旧先红检查；停下等拍板那次（没改代码）不算写过代码。
"""
from collab.dispatch import run_card
from collab.flow import read_flow, record
from collab.judge import FAIL, PASS
from collab.state import Card
from review_fmt import listed_ask
from test_redfirst_acceptance import CMD, Base


class ReviseAfterCodeWritten(Base):
    def revise(self, words="删掉那条多余的测试", **extra):
        card = Card.load(self.cdir / "状态.json")
        card.rework = 0
        card.transition("开发中")
        card.save(self.cdir / "状态.json")
        record(self.cdir, "追加要求", src="你", dst="调度员", summary=words, text=words, **extra)
        return card

    def after_revise_steps(self):
        flow = read_flow(self.cdir)
        last = max(e["seq"] for e in flow if e.get("kind") == "追加要求")
        return [e.get("step") for e in flow if e.get("kind") == "派活" and e["seq"] > last]

    def test_revise_after_dev_done_skips_red_check(self):
        self.verdicts = [FAIL]          # 第一次开工：先红（还没改代码），之后一直过
        card = self.go()
        self.assertEqual(card.state, "待验收")
        self.calls.clear()
        before = len(self.checks())
        orig = self.ask_fn

        def ask_fn(adapter, prompt, cwd, **kw):   # 继续改这次，写测试的只删掉一条（改了文件、没加新测试）
            if adapter == "codex":
                self.calls.append(("测试", prompt))
                from pathlib import Path
                from collab.agents import AgentResult
                (Path(cwd) / "tests" / "test_new1.py").write_text("# 删掉了多余的那条\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="删掉了")
            return orig(adapter, prompt, cwd, **kw)
        self.ask_fn = ask_fn
        card = self.go(card=self.revise())
        self.assertNotIn("重写测试", [e.get("step") for e in read_flow(self.cdir) if e.get("kind") == "派活"],
                         "代码早就写好了，测试全过是对的，不退回重写")
        self.assertEqual(len(self.checks()), before, "继续改时不再做先红检查")
        self.assertEqual(card.state, "待验收")
        self.assertTrue(any(e.get("kind") == "先红跳过" for e in read_flow(self.cdir)), "流转里写明为什么跳过")
        self.assertNotIn("开发", self.after_revise_steps(), "本卡测试已经全过：先判定，过了直接交复查，不派写代码的")

    def test_revise_changing_criteria_still_red_checks(self):
        """复查第 1 轮中等 3：继续改改了验收标准（加了新要求），新测试本该先不过，照旧查。"""
        self.verdicts = [FAIL]
        self.go()
        before = len(self.checks())
        self.go(card=self.revise("新增 CSV 导出", criteria_changes=[{"op": "add", "text": "能导出 CSV"}]))
        self.assertGreater(len(self.checks()), before)

    def test_revise_adding_new_tests_still_red_checks(self):
        self.verdicts = [FAIL]
        self.go()
        before = len(self.checks())
        orig = self.ask_fn

        def ask_fn(adapter, prompt, cwd, **kw):
            result = orig(adapter, prompt, cwd, **kw)
            if adapter == "codex":
                from pathlib import Path
                (Path(cwd) / "tests" / "test_csv.py").write_text("def test_export_csv():\n    assert False\n",
                                                                  encoding="utf-8")
            return result
        self.ask_fn = ask_fn
        self.go(card=self.revise("新增 CSV 导出"))
        self.assertGreater(len(self.checks()), before, "写测试的加了新测试：照旧先红检查")

    def test_truncated_diff_still_red_checks(self):
        """复查 D 第 2 轮严重 1：存下的改动太长被截掉了，新测试可能在截掉的那段里，照旧先红。"""
        from collab.dispatch import _skip_red_after_revise
        self.verdicts = [FAIL]
        self.go()
        self.revise()
        (self.cdir / "改动").mkdir(exist_ok=True)
        (self.cdir / "改动" / "999.diff").write_text("-# old\n+# x\n…（改动太长，后面没存）\n", encoding="utf-8")
        record(self.cdir, "回复", src="测试员", step="写测试", ok=True, diff="改动/999.diff", summary="写好了")
        self.assertFalse(_skip_red_after_revise(self.cdir, read_flow(self.cdir)))

    def test_first_start_still_red_checks(self):
        self.verdicts = [PASS, FAIL]
        self.go()
        self.assertEqual([c.get("result") for c in self.checks()], [PASS, FAIL], "第一次开工照旧先红")


if __name__ == "__main__":
    import unittest
    unittest.main()
