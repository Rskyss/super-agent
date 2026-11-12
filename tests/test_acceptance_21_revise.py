"""12 号单：还要修改。新要求进入新一轮，测试员先补测试，开发员再改。"""
import json
import os
from pathlib import Path

from acceptance_helpers import STATIC, wait_until, write_script
from collab.flow import read_flow, record
from test_acceptance_10_fixes import CONV
from test_acceptance_11_fixes import Case


class ReviseCase(Case):
    def revise(self, text="按钮再大一点", card="CARD-001"):
        return self.post("/api/clerk/card", {"project": self.project, "card": card, "action": "revise", "text": text})

    def need(self):
        return (self.collab / "cards" / "CARD-001" / "需求卡.md").read_text(encoding="utf-8")

    def logged_tools(self):
        self.tester_log = Path(self.tmp) / "tester.log"
        self.dev_log = Path(self.tmp) / "dev.log"
        write_script(self.bin / "codex", f'printf "%s\\n===\\n" "$*" >> "{self.tester_log}"\n'
                                         'mkdir -p tests\n[ -f tests/test_new.py ] || echo 第一版测试 > tests/test_new.py\necho 写好了\n')
        write_script(self.bin / "agent", f'printf "%s\\n===\\n" "$*" >> "{self.dev_log}"\necho "x = 2" > src/app.py\necho 写好了\n')

    def rounds(self, path):
        return [r for r in path.read_text(encoding="utf-8").split("\n===\n") if r.strip()]

    def to_review(self):
        self.start()
        wait_until(lambda: self.state() == "待验收", 25, "第一轮没有做完")

    def wait_second_round(self):
        wait_until(lambda: self.state() == "待验收" and len(self.rounds(self.dev_log)) == 2, 25, "第二轮没有做完")


class Rules(ReviseCase):
    def test_words_are_required(self):
        self.make_card(state="待验收", plan=True)
        status, _ = self.revise("   ")
        self.assertEqual(status, 400)
        self.assertEqual(self.state(), "待验收")

    def test_not_while_building(self):
        cdir = self.make_card(state="开发中", plan=True)
        record(cdir, "开跑", src="调度员", dst="程序", summary="开工", pid=os.getpid())
        self.assertEqual(self.revise()[0], 409)
        self.assertNotIn("按钮再大一点", self.need())

    def test_waits_for_another_building_card(self):
        cdir = self.make_card(state="待验收", plan=True)
        (cdir / "开工.json").write_text('{"test_command": "true"}', encoding="utf-8")
        self.mark("run", "CARD-999", os.getpid())
        status, answer = self.revise()
        self.assertEqual(status, 409)
        self.assertIn("施工", answer["error"])
        self.assertEqual(self.state(), "待验收")
        self.assertNotIn("按钮再大一点", self.need())

    def test_requirement_keeps_old_words_and_rework_starts_over(self):
        from collab.tasks import revise_card
        cdir = self.make_card(state="待验收", plan=True)
        (cdir / "状态.json").write_text(json.dumps({"card_id": "CARD-001", "state": "待验收", "rework": 2}), encoding="utf-8")
        revise_card(self.project, "CARD-001", "第一条")
        self.assertEqual(json.loads((cdir / "状态.json").read_text(encoding="utf-8"))["rework"], 0)
        self.assertEqual(self.state(), "开发中")
        need = self.need()
        for words in ("改颜色", "## 允许改动的范围", "## 追加要求", "第一条"):
            self.assertIn(words, need)
        last = self.last_flow("追加要求")
        self.assertEqual(last["src"], "你")
        self.assertIn("第一条", last["summary"])

    def test_page_has_a_box_not_a_system_prompt(self):
        script = (STATIC / "panel.js").read_text(encoding="utf-8")
        self.assertNotIn("prompt(", script)
        self.assertIn("reviseText", script)
        self.assertIn("text:", script)


class NewRound(ReviseCase):
    def test_tester_then_developer_with_the_new_words(self):
        self.logged_tools()
        self.to_review()
        self.assertEqual(self.revise()[0], 200)
        self.wait_second_round()
        self.assertEqual(len(self.rounds(self.tester_log)), 2)
        self.assertIn("按钮再大一点", self.rounds(self.tester_log)[1])
        self.assertIn("按钮再大一点", self.rounds(self.dev_log)[1])
        kinds = self.flow_kinds()
        after = kinds[kinds.index("追加要求"):]
        self.assertLess(after.index("回复"), after.index("判定"))
        self.assertEqual(after[-1], "结束")

    def test_scope_still_holds_in_the_new_round(self):
        self.logged_tools()
        self.to_review()
        write_script(self.bin / "agent", f'printf "%s\\n===\\n" "$*" >> "{self.dev_log}"\necho 越界 > other.txt\necho 写好了\n')
        self.revise()
        self.wait_second_round()
        self.assertFalse((self.worktree / "other.txt").exists())

    def test_tester_can_update_its_own_test(self):
        self.logged_tools()
        self.to_review()
        write_script(self.bin / "codex", 'echo 第二版测试 > tests/test_new.py\necho another > tests/test_more.py\necho 写好了\n')
        write_script(self.bin / "agent", f'printf "%s\\n===\\n" "$*" >> "{self.dev_log}"\necho 开发员乱改 > tests/test_new.py\necho 写好了\n')
        self.revise()
        self.wait_second_round()
        self.assertEqual((self.worktree / "tests" / "test_new.py").read_text(encoding="utf-8").strip(), "第二版测试")
        self.assertTrue((self.worktree / "tests" / "test_more.py").exists())
        files = json.loads((self.collab / "cards" / "CARD-001" / "测试文件.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(files), ["tests/test_more.py", "tests/test_new.py"])

    def test_old_tests_stay_protected_when_nothing_is_added(self):
        self.logged_tools()
        self.to_review()
        write_script(self.bin / "codex", 'echo 不用再加测试\n')
        write_script(self.bin / "agent", f'printf "%s\\n===\\n" "$*" >> "{self.dev_log}"\nrm -f tests/test_new.py\necho 写好了\n')
        self.revise()
        self.wait_second_round()
        self.assertEqual((self.worktree / "tests" / "test_new.py").read_text(encoding="utf-8").strip(), "第一版测试")

    def test_blocked_card_can_be_revised(self):
        self.logged_tools()
        write_script(self.bin / "agy", 'echo 结论：不通过\n')
        self.start()
        wait_until(lambda: self.state() == "需Claude介入", 25)
        # 流程修理（11-12）：复查不通过先自动退回写代码的 2 次才卡住，所以卡住前写代码的已经干了 3 次
        before = len(self.rounds(self.dev_log))
        self.assertEqual(before, 3)
        write_script(self.bin / "agy", 'echo 结论：通过\n')
        self.assertEqual(self.revise()[0], 200)
        wait_until(lambda: self.state() == "待验收" and len(self.rounds(self.dev_log)) == before + 1, 25,
                   "继续改后没有做完")

    def test_merge_failure_can_be_revised(self):
        self.logged_tools()
        self.to_review()
        record(self.collab / "cards" / "CARD-001", "合并失败", src="程序", dst="你", summary="冲突")
        self.assertEqual(self.phase()["code"], "merge_failed")
        self.assertEqual(self.revise()[0], 200)
        self.wait_second_round()


class PlanStage(ReviseCase):
    def plans(self):
        if not self.codex_log.exists():
            return []
        return [l for l in self.codex_log.read_text(encoding="utf-8").splitlines() if "--output-schema" in l]

    def plan_card(self):
        self.plan()
        cdir = self.make_card(plan=True, source=CONV, reported="plan_ready")
        return cdir

    def test_plan_is_rewritten_with_the_new_words(self):
        self.plan_card()
        self.assertEqual(self.revise("不要改配色")[0], 200)
        wait_until(lambda: self.plans(), 10, "没有重写方案")
        self.assertIn("不要改配色", self.plans()[0])
        self.assertEqual(self.state(), "待对齐")
        wait_until(lambda: self.phase()["code"] == "plan_ready", 10, "方案没有写好")
        self.assertTrue(self.flow_kinds()[-1] == "方案")

    def test_old_plan_is_not_offered_while_waiting(self):
        self.plan_card()
        self.mark("plan", "CARD-999", os.getpid())
        self.assertEqual(self.revise("不要改配色")[0], 200)
        self.assertEqual(self.phase()["code"], "plan_waiting")
        self.assertEqual(self.plans(), [])

    def test_flow_records_the_words(self):
        self.plan_card()
        self.revise("不要改配色")
        self.assertIn("不要改配色", read_flow(self.collab / "cards" / "CARD-001")[-2]["summary"] + self.last_flow("追加要求")["summary"])
