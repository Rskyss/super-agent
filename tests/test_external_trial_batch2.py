"""外部项目试跑修复 · 第二批（docs/外部项目试跑修复/第二批/，用户 11-23 22:50 外出授权，Claude 替拍板见需求共识第三节）。

样本来自 train CARD-003～006 的真实复查回复（只保留结构，正文缩写）。
"""
import json
import unittest

from collab.dispatch import read_review_list


def _reply(evidence, position="app/x.tsx:12"):
    data = {"结论": "不通过", "问题": [{
        "级别": "【严重】", "来源": "【这次改的】", "类型": "功能问题", "碰上": "常见",
        "现象": "导师看不到最新进度。", "位置": position, "证据": evidence}]}
    return "结论：不通过\n\n复查清单：\n```json\n" + json.dumps(data, ensure_ascii=False) + "\n```\n"


class EvidenceFormatTolerance(unittest.TestCase):
    """㉝ CARD-003/004/005/006 每张都为证据格式白跑一次最高档复查。"""

    def ok(self, evidence, position="app/x.tsx:12"):
        data, errors = read_review_list(_reply(evidence, position))
        self.assertEqual(errors, [], errors)
        return data["问题"][0]["证据"]

    def test_content_key_is_accepted_and_normalized(self):
        proof = self.ok({"方式": "推演", "内容": "lib/a.ts:61 设了 8 秒上限"})   # CARD-003
        self.assertIn("lib/a.ts:61", proof.get("对照", ""), "认下后统一放进“对照”，交代里照常带上")

    def test_explain_key_is_accepted(self):
        self.ok({"方式": "推演", "说明": "app/trainer/rooms/[id]/panel.tsx:86 在复盘阶段挂载"})   # CARD-005

    def test_plan_line_reference_is_accepted(self):
        self.ok({"方式": "推演", "对照": "施工单第 125、140 行要求匿名区不露组号"})   # CARD-004

    def test_position_alone_does_not_back_vague_evidence(self):
        # 老规矩（C1.5）：推演证据自己要写出对照的文件:行号，位置栏有不算（复查第 1 轮全量发现，撤回这条放宽）
        _, errors = read_review_list(_reply({"方式": "推演", "对照": "抬头固定显示各占一半"}, position="app/a.tsx:30"))
        self.assertTrue(errors)

    def test_empty_evidence_still_rejected(self):
        _, errors = read_review_list(_reply({"方式": "推演", "对照": ""}, position="说不清"))
        self.assertTrue(errors, "证据什么都没写还是不合格")


if __name__ == "__main__":
    unittest.main()


from pathlib import Path   # noqa: E402

from collab.agents import AgentResult   # noqa: E402
from collab.dispatch import run_card   # noqa: E402
from collab.flow import read_flow   # noqa: E402
from collab.judge import FAIL, PASS, Verdict   # noqa: E402
from collab.state import Card   # noqa: E402
from review_fmt import listed_ask   # noqa: E402
from test_external_trial_fixes import CMD, Base   # noqa: E402


def _list_reply(position, phen="退出学员能在只读页看到组号。"):
    data = {"结论": "不通过", "问题": [{
        "级别": "【严重】", "来源": "【原来就有的】", "类型": "功能问题", "碰上": "少见",
        "现象": phen, "位置": position, "证据": {"方式": "推演", "对照": position + " 仍显示组号"}}]}
    return "结论：不通过\n1. 【严重】【原来就有的】" + phen + "\n\n复查清单：\n```json\n" + \
        json.dumps(data, ensure_ascii=False) + "\n```\n"


class ReturnOutsideScopeStops(Base):
    """㊲ train CARD-005：复查退回的问题落在允许范围外，不能退给写代码的空转。"""

    def go(self, reviews, dev_writes=("app.py",)):
        reviews = list(reviews)
        calls = []

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                calls.append("复查")
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            if "编写自动化测试" in prompt:
                calls.append("测试员")
                (wt / "tests" / "test_x.py").write_text(f"# {len(calls)}\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            calls.append("开发员")
            self.prompts.append(prompt)
            for f in dev_writes:
                (wt / f).parent.mkdir(parents=True, exist_ok=True)
                (wt / f).write_text(f"# {len(calls)}\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card = run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "codex",
                        ask_fn=listed_ask(ask_fn), judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
                        reviewer_adapter="codex", require_tests=True, allowed=["app.py"])[0]
        return card, calls

    def test_all_outside_scope_hands_to_person(self):
        card, calls = self.go([_list_reply("app/lobby/[room_id]/withdrawn-student-view.tsx:22")])
        self.assertEqual(card.state, "需Claude介入")
        self.assertEqual(calls.count("开发员"), 1, "不再退给写代码的")
        stop = [e for e in read_flow(self.cdir) if e.get("kind") == "转人工"][-1]
        self.assertIn("withdrawn-student-view.tsx", stop.get("say", ""))
        self.assertIn("允许", stop.get("say", ""))

    def test_inside_scope_still_returns(self):
        card, calls = self.go([_list_reply("app.py:3", "按钮没反应。"), "结论：通过"])
        self.assertEqual(card.state, "待验收")
        self.assertEqual(calls.count("开发员"), 2)

    def test_reverted_files_are_told_next_time(self):
        state = {"n": 0}

        def judge(*a, **k):
            state["n"] += 1
            return Verdict(FAIL, 1, "AssertionError: 产品没写好") if state["n"] == 1 else Verdict(PASS, 0, "ok")

        reviews = ["结论：通过"]

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            if "编写自动化测试" in prompt:
                (wt / "tests" / "test_x.py").write_text("# t\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            self.prompts.append(prompt)
            (wt / "app.py").write_text(f"A = {len(self.prompts)}\n", encoding="utf-8")
            (wt / "other.py").write_text("x\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "codex",
                 ask_fn=listed_ask(ask_fn), judge_fn=judge, reviewer_adapter="codex",
                 require_tests=True, allowed=["app.py"])
        self.assertGreaterEqual(len(self.prompts), 2)
        self.assertIn("other.py", self.prompts[1])
        self.assertIn("不在任务卡“允许改动的范围”", self.prompts[1])


class OldTestBackupPathIsSpelledOut(unittest.TestCase):
    """㊳ train CARD-005：交代只写“备份下同名文件”，复查员拼成 测试前备份/x.test.ts 漏了 tests/。"""

    def test_note_shows_full_relative_example(self):
        import subprocess
        import tempfile
        from collab.dispatch import _old_tests_changed_note
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "wt"
            (root / "tests").mkdir(parents=True)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "tests" / "a.test.ts").write_text("new\n", encoding="utf-8")
            cdir = Path(tmp) / "card"
            (cdir / "测试前备份" / "tests").mkdir(parents=True)
            (cdir / "测试前备份" / "tests" / "a.test.ts").write_text("old\n", encoding="utf-8")
            note = _old_tests_changed_note(cdir, root)
        self.assertIn("测试前备份/tests/a.test.ts", note)


class StopTerminalStartedCard(unittest.TestCase):
    """㉖ train CARD-002：从终端起的卡（跑卡进程不是进程组带头的），窗口“停下”按组发信号找不到组就当停了，实际还在跑。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)
        (self.project / ".collab" / "后台").mkdir(parents=True)

    def tearDown(self):
        import os
        import signal
        try:
            os.kill(getattr(self, "pid", 0) or 0, signal.SIGKILL) if getattr(self, "pid", 0) else None
        except OSError:
            pass
        self.tmp.cleanup()

    def start(self, script="import time; time.sleep(60)"):
        import subprocess
        import sys
        from desktop.clerk.tasks import process_start as _proc_start
        # 经 sh 后台起一个孙进程：不是测试进程的孩子（停了由 sh 那边收尸，不会留成僵尸），也不是进程组带头的，和终端里起的一样
        out = subprocess.run(["/bin/sh", "-c", f"{sys.executable} -c '{script}' >/dev/null 2>&1 & echo $!"],
                             capture_output=True, text=True, check=True)
        self.pid = int(out.stdout.strip())
        self.proc = None
        (self.project / ".collab" / "后台" / "run.json").write_text(json.dumps({
            "card": "CARD-002", "pid": self.pid, "started": _proc_start(self.pid)}), encoding="utf-8")

    def test_stop_really_stops_non_group_leader(self):
        from desktop.clerk.tasks import stop_construction
        self.start()
        from desktop.clerk.tasks import process_is_alive
        stop_construction(str(self.project), "CARD-002")
        self.assertFalse(process_is_alive(self.pid), "进程应该真的停了")

    def test_stop_that_fails_is_not_reported_as_stopped(self):
        import desktop.clerk.tasks as t
        self.start()
        real = t.process_is_alive
        try:
            t.process_is_alive = lambda pid, started=None: True   # 模拟发了信号也停不掉
            with self.assertRaises(t.StopFailed):
                t.stop_construction(str(self.project), "CARD-002")
        finally:
            t.process_is_alive = real


class TesterWrapUpSaysWhatHappened(Base):
    """㉗ train CARD-002 起：“测试收尾”不管有没有越界都写“已还原越界改动”。"""

    def go(self, tester_touches_product):
        reviews = ["结论：不通过\n1. 【中等】【这次改的】按钮没反应。", "结论：通过"]
        n = {"t": 0}

        def ask_fn(adapter, prompt, cwd, **kw):
            wt = Path(cwd)
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final=reviews.pop(0))
            if "编写自动化测试" in prompt:
                n["t"] += 1
                (wt / "tests" / "test_x.py").write_text(f"# {n['t']}\n", encoding="utf-8")
                if tester_touches_product and n["t"] == 2:
                    (wt / "app.py").write_text("A = 'tester'\n", encoding="utf-8")
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            (wt / "app.py").write_text("A = 2\n", encoding="utf-8")
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        run_card(Card("CARD-001", state="待派发"), self.collab, self.wt, CMD, "cursor", "codex",
                 ask_fn=listed_ask(ask_fn), judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"),
                 reviewer_adapter="codex", require_tests=True, allowed=["app.py"])
        return [e.get("summary", "") for e in read_flow(self.cdir) if e.get("kind") == "测试收尾"]

    def test_no_crossing_no_claim(self):
        sums = self.go(False)
        self.assertTrue(sums)
        self.assertFalse(any("越界" in x for x in sums), sums)

    def test_crossing_is_named(self):
        sums = self.go(True)
        self.assertTrue(any("还原了 1 处越界改动（app.py）" in x for x in sums), sums)


class DraftSummaryShowsCause(unittest.TestCase):
    """㉙ train CARD-002/004/005：起草回复的流转摘要只截了最后一句套话。"""

    def test_draft_summary_is_first_paragraph(self):
        from collab.flow import reply_summary
        text = ("这几轮是看到一种组号写法就补一种，测试也只验已碰到的例子。\n\n---\n\n**继续改**\n\n"
                "只改这一部分，别的不动；改完跑本卡测试，过了就交。")
        got = reply_summary("起草", text, True)
        self.assertTrue(got.startswith("这几轮是看到一种组号写法"), got)

    def test_other_steps_unchanged(self):
        from collab.flow import reply_summary
        self.assertEqual(reply_summary("写测试", "第一行\n结论：写好了", True), "结论：写好了")


class PlanFromWorkOrderIsProgram(unittest.TestCase):
    """① train CARD-001：照施工单生成的方案，右栏“写方案”仍标 Codex · GPT-6.2-Sol（低）。"""

    def stations(self, src):
        from desktop.view import _add_models, _stations
        st = _stations({"测试员": "codex", "开发员": "qwen", "复查员": "codex"}, True)
        _add_models(st, [{"kind": "方案", "src": src, "summary": "照施工单"}], None)
        return next(x for x in st if x["key"] == "disp")

    def test_program_plan(self):
        disp = self.stations("程序")
        self.assertEqual(disp["who"], "程序")
        self.assertNotIn("GPT", disp.get("model", ""))

    def test_codex_plan_unchanged(self):
        self.assertEqual(self.stations("Codex")["who"], "Codex")


class TakeoverShowsWho(unittest.TestCase):
    """⑬ 接手记录一律写成“Claude 接手”，外部项目其实是 Gemini 接手。"""

    def test_station_uses_recorded_name(self):
        from desktop.view import _insert_takeover_station
        st = [{"key": "accept"}]
        _insert_takeover_station(st, {"kind": "接手", "src": "Gemini"})
        self.assertEqual(st[0]["role"], "Gemini 接手")

    def test_cli_by_option(self):
        import tempfile
        from collab.cli import main
        with tempfile.TemporaryDirectory() as tmp:
            cdir = Path(tmp) / ".collab" / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            main(["takeover", tmp, "CARD-001", "--by", "Gemini", "--note", "修好了"])
            self.assertEqual(read_flow(cdir)[-1].get("src"), "Gemini")


class FreshTokens(unittest.TestCase):
    """㉓ ⑨ 合计只显示含缓存的总数（train CARD-001 8000 多万，96% 是重复读缓存）。"""

    def test_view_counts_fresh(self):
        import shutil
        import subprocess
        import tempfile
        from collab.workspace import init_collab
        from desktop.view import card_view
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "项目"
            project.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=project, check=True)
            init_collab(str(project))
            cdir = project / ".collab" / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("# 卡\n", encoding="utf-8")
            (cdir / "状态.json").write_text('{"card_id": "CARD-001", "state": "待验收", "rework": 0}', encoding="utf-8")
            (cdir / "流转.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in [
                {"seq": 1, "ts": 1.0, "kind": "派活", "src": "调度员", "dst": "测试员", "step": "写测试"},
                {"seq": 2, "ts": 2.0, "kind": "回复", "src": "测试员", "dst": "调度员", "step": "写测试", "ok": True,
                 "reply_to": 1, "tokens": {"ok": True, "total": 1000, "input": 950, "cached": 900, "output": 50,
                                           "children": 200}},
            ]) + "\n", encoding="utf-8")
            v = card_view(str(project), "CARD-001")
        self.assertEqual(v.get("tokens_fresh"), 50 + 50 + 200, v)
        self.assertNotIn("fresh", v["tokens"], "合计那份照旧，老检查不动")

    def test_panel_shows_fresh(self):
        import subprocess
        root = Path(__file__).resolve().parents[1]
        proc = subprocess.run(["node", str(root / "tests" / "fresh_tokens_page_check.js")], cwd=root,
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class RoundsCountFromLastRevise(unittest.TestCase):
    """㉑ train CARD-001：右栏写代码“第 10 轮”是整张卡累计（含改要求之前 7 轮），看着像失控。"""

    def test_rounds_after_revise(self):
        import subprocess
        import tempfile
        from collab.workspace import init_collab
        from desktop.view import card_view
        events, seq = [], 0

        def add(**e):
            nonlocal seq
            seq += 1
            e.setdefault("ts", float(seq))
            events.append(dict(e, seq=seq))
            return seq

        for _ in range(3):
            d = add(kind="派活", src="调度员", dst="开发员", step="开发")
            add(kind="回复", src="开发员", dst="调度员", step="开发", ok=True, reply_to=d, summary="汇报：改了")
        add(kind="追加要求", src="你", dst="调度员", summary="换个做法")
        d = add(kind="派活", src="调度员", dst="开发员", step="开发")
        add(kind="回复", src="开发员", dst="调度员", step="开发", ok=True, reply_to=d, summary="汇报：改了")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "项目"
            project.mkdir()
            subprocess.run(["git", "init", "-q"], cwd=project, check=True)
            init_collab(str(project))
            cdir = project / ".collab" / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("# 卡\n", encoding="utf-8")
            (cdir / "状态.json").write_text('{"card_id": "CARD-001", "state": "待验收", "rework": 0}', encoding="utf-8")
            (cdir / "流转.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
                                           encoding="utf-8")
            dev = next(s for s in card_view(str(project), "CARD-001")["stations"] if s["key"] == "dev")
        self.assertEqual(dev.get("rounds"), 1)
        self.assertEqual(dev.get("earlier"), 3)


class Round1ReviewFixes(unittest.TestCase):
    """第二批复查第 1 轮挑出、核实成立的几条。"""

    def test_plan_version_is_not_line_evidence(self):   # 9
        _, errors = read_review_list(_reply({"方式": "推演", "对照": "施工单第 2 版写了"}))
        self.assertTrue(errors)

    def test_paths_in_brackets_and_lists(self):   # 6
        from collab.dispatch import _outside_scope_problems
        p = lambda pos: {"级别": "严重", "类型": "功能问题", "现象": "x", "位置": pos}
        self.assertEqual(_outside_scope_problems([p("(app.py:12)")], ["app.py"]), [])
        self.assertEqual(_outside_scope_problems([p("other.py:5、app.py")], ["app.py"]), [], "有一个在范围内就不算全在外")
        self.assertTrue(_outside_scope_problems([p(".github/x.yml:3")], ["app.py"]))
        self.assertEqual(_outside_scope_problems([p("./app.py:3")], ["app.py"]), [])

    def test_fresh_unknown_with_old_records(self):   # 10
        from desktop.view import _add_tokens, _stations
        st = _stations({"测试员": "codex", "开发员": "qwen", "复查员": "codex"}, True)
        events = [
            {"seq": 1, "kind": "派活", "dst": "开发员"},
            {"seq": 2, "kind": "回复", "src": "开发员", "ok": True, "reply_to": 1, "tokens": {"ok": True, "total": 1000}},
            {"seq": 3, "kind": "派活", "dst": "开发员"},
            {"seq": 4, "kind": "回复", "src": "开发员", "ok": True, "reply_to": 3,
             "tokens": {"ok": True, "total": 100, "input": 80, "cached": 60, "output": 10}},
        ]
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = _add_tokens(st, events, Path(tmp))
        self.assertNotIn("fresh", out, "老记录算不出新消耗，就不显示")

    def test_draft_heading_skipped(self):   # 15
        from collab.flow import reply_summary
        self.assertTrue(reply_summary("起草", "# 共同原因\n\n每轮只补一个时间点。", True).startswith("每轮只补"))

    def test_codex_rewrite_after_program_plan(self):   # 14
        from desktop.view import _add_models, _stations
        st = _stations({"测试员": "codex", "开发员": "qwen", "复查员": "codex"}, True)
        _add_models(st, [{"kind": "写方案"}, {"kind": "方案", "src": "程序"}, {"kind": "写方案"}], None)
        self.assertEqual(next(x for x in st if x["key"] == "disp")["who"], "Codex")


class StopRound1(StopTerminalStartedCard):
    def test_failed_draft_stop_blocks_followup(self):   # 4
        import desktop.clerk.tasks as t
        self.start()
        real = t.process_is_alive
        try:
            t.process_is_alive = lambda pid, started=None: True
            with self.assertRaises(t.StopFailed):
                t.end_drafting(str(self.project), "CARD-002")
        finally:
            t.process_is_alive = real

    def test_group_members_left_behind_are_killed(self):   # 1
        import os
        import subprocess
        import sys
        import time
        from desktop.clerk.tasks import process_is_alive, process_start, stop_construction
        # 窗口开的卡：自带进程组，组长收到停止信号就退，同组的子工具不理会停止信号
        # 经一个中间进程起组长，中间进程马上退出：组长归系统收养（和窗口开的卡一样，停了不会留成测试进程的僵尸）
        body = ("import subprocess,sys,time;"
                "subprocess.Popen([sys.executable,'-c','import signal,time;"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)']);time.sleep(60)")
        launcher = (f"import subprocess,sys;p=subprocess.Popen([sys.executable,'-c',{body!r}],start_new_session=True,"
                    "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print(p.pid)")
        leader_pid = int(subprocess.run([sys.executable, "-c", launcher], capture_output=True, text=True,
                                        check=True).stdout.strip())
        self.pid = leader_pid
        time.sleep(0.5)
        table = subprocess.run(["ps", "-A", "-o", "pid=,pgid="], capture_output=True, text=True).stdout.split("\n")
        kids = [row.split()[0] for row in table if len(row.split()) == 2 and row.split()[1] == str(leader_pid)]
        (self.project / ".collab" / "后台" / "run.json").write_text(json.dumps({
            "card": "CARD-002", "pid": leader_pid, "started": process_start(leader_pid)}), encoding="utf-8")
        stop_construction(str(self.project), "CARD-002")
        time.sleep(0.3)
        self.assertTrue(len(kids) >= 2, kids)
        alive = [k for k in kids if process_is_alive(int(k))]
        for k in alive:
            os.kill(int(k), 9)
        self.assertEqual(alive, [], "同组留下的子工具也要停掉")



class PlanFromOrderCarriesAcceptance(unittest.TestCase):
    """② train CARD-001：照施工单生成的方案，“你会看到什么结果”抄了“要做什么”，“做完后怎么看结果”写“方案里没写”。"""

    ORDER = """# 施工单

## 一、要做什么（一句话）

终局加综合总分开关。

## 二、允许改哪些文件

- `lib/a.ts`

## 三、具体做法

### 1. 加开关

## 八、验收

- 验收命令：`npm test`
- 走查：打开导师后台评分维度页，对照原型图 06 看一遍
"""

    def test_acceptance_is_brought_in(self):
        import tempfile
        from collab.planner import _plan_from_order, _render_plan_md
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "施工单.md").write_text(self.ORDER, encoding="utf-8")
            data = _plan_from_order(tmp, "施工单.md")
        md = _render_plan_md(data)
        self.assertIn("npm test", md.split("## 你会看到什么结果")[1].split("##")[0])
        self.assertIn("对照原型图 06", md.split("## 做完后怎么看结果")[1])
        self.assertNotIn("方案里没写", md)

    def test_order_without_acceptance_unchanged(self):
        import tempfile
        from collab.planner import _plan_from_order
        order = self.ORDER.split("## 八、验收")[0] + "验收命令：`npm test`\n"
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "施工单.md").write_text(order, encoding="utf-8")
            data = _plan_from_order(tmp, "施工单.md")
        self.assertEqual(data["result"], data["summary"])
        self.assertEqual(data.get("view_how"), "")


class Round2ReviewFixes(unittest.TestCase):
    """第二批复查第 2 轮挑出、核实成立的几条。"""

    def test_setext_heading_skipped_but_separator_kept(self):   # 15
        from collab.flow import reply_summary
        self.assertTrue(reply_summary("起草", "共同原因\n---\n每轮只补一个点。", True).startswith("每轮只补"))
        self.assertTrue(reply_summary("起草", "每轮只补一个点。\n\n---\n\n只改这一部分。", True).startswith("每轮只补"))

    def test_scope_location_parsing(self):   # 6、B5
        from collab.dispatch import _outside_scope_problems as f
        p = lambda pos: {"级别": "严重", "类型": "功能问题", "现象": "x", "位置": pos}
        self.assertEqual(f([p("[app.py:12]、outside.ts:5")], ["app.py"]), [])
        self.assertEqual(f([p("src/、outside.ts:5")], ["app.py"]), [], "认不出的目录：拿不准就照常退回")
        self.assertEqual(f([p("GET /api/v1.usage")], ["app.py"]), [], "接口地址不是文件")

    def test_null_cached_is_unknown(self):   # B3
        import tempfile
        from desktop.view import _add_tokens, _stations
        st = _stations({"测试员": "codex", "开发员": "qwen", "复查员": "codex"}, True)
        events = [{"seq": 1, "kind": "派活", "dst": "开发员"},
                  {"seq": 2, "kind": "回复", "src": "开发员", "ok": True, "reply_to": 1,
                   "tokens": {"ok": True, "total": 1000, "input": 1000, "cached": None, "output": 0}}]
        with tempfile.TemporaryDirectory() as tmp:
            self.assertNotIn("fresh", _add_tokens(st, events, Path(tmp)))

    def test_revert_note_survives_resume(self):   # 8
        import tempfile
        from collab.dispatch import _pending_revert_note
        from collab.flow import record
        with tempfile.TemporaryDirectory() as tmp:
            cdir = Path(tmp)
            record(cdir, "范围", src="程序", ok=True, reverted=["other.py"], summary="已自动撤销范围外的改动：other.py")
            self.assertIn("other.py", _pending_revert_note(cdir))
            record(cdir, "派活", src="调度员", dst="开发员", summary="改")
            self.assertIn("other.py", _pending_revert_note(cdir), "只记了派活、还没回话就停：续跑仍要带（第 3 轮 B7）")
            record(cdir, "回复", src="开发员", dst="调度员", ok=False, summary="看门人：15 分钟没动静，停了")
            self.assertIn("other.py", _pending_revert_note(cdir), "被停掉的回复不算送到（收尾确认复查 A4）")
            record(cdir, "回复", src="开发员", dst="调度员", ok=True, summary="汇报：改了")
            self.assertEqual(_pending_revert_note(cdir), "")


class Round2StopFixes(StopTerminalStartedCard):
    def test_second_stop_waits_and_reports_truth(self):   # 3
        import threading
        import desktop.clerk.tasks as t
        self.start()
        key = f"{self.project}\0CARD-002"
        with t._stop_lock:
            t._stopping.add(key)   # 模拟第一次停下还在进行、最后没停住
        threading.Timer(0.3, lambda: t._stopping.discard(key)).start()
        with self.assertRaises(t.StopFailed):
            t.stop_construction(str(self.project), "CARD-002")


class ScopeStopResumeRechecks(ReturnOutsideScopeStops):   # 复查第 2 轮 7
    def test_resume_without_widening_scope_stops_again(self):
        card, calls = self.go([_list_reply("app/lobby/[room_id]/withdrawn-student-view.tsx:22")])
        self.assertEqual(card.state, "需Claude介入")
        card.transition("开发中")
        dev = []

        def ask_fn(adapter, prompt, cwd, **kw):
            dev.append(prompt)
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card2 = run_card(card, self.collab, self.wt, CMD, "cursor", "codex", ask_fn=listed_ask(ask_fn),
                         judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex",
                         require_tests=True, allowed=["app.py"])[0]
        self.assertEqual(card2.state, "需Claude介入")
        self.assertEqual(dev, [], "范围没扩就不派人")
        self.assertIn("范围还没扩", read_flow(self.cdir)[-1].get("summary", ""))


class Round3PlanFixes(unittest.TestCase):
    """第 3 轮 B4：方案里写的验收命令要和真跑的同一条。"""

    def test_plan_shows_the_command_actually_run(self):
        import tempfile
        from collab.planner import _plan_from_order, _render_plan_md
        order = PlanFromOrderCarriesAcceptance.ORDER.replace(
            "## 三、具体做法", "旧验收命令：`npm run old`\n\n## 三、具体做法")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "施工单.md").write_text(order, encoding="utf-8")
            data = _plan_from_order(tmp, "施工单.md")
        md = _render_plan_md(data)
        self.assertIn(data["test_command"], md.split("## 你会看到什么结果")[1].split("##")[0])


class ScopeStopSaysWorkingWay(ReturnOutsideScopeStops):
    """第二批欠账（复查第 3 轮 B2）：停卡说明给的办法要走得通——“继续改”写明这件不做后，接着做不再停在原处；
    说明里不再提“撤掉测试、记以后再修”这条走不通的路。"""

    def test_revise_saying_not_this_card_continues(self):
        card, calls = self.go([_list_reply("app/lobby/[room_id]/withdrawn-student-view.tsx:22")])
        self.assertEqual(card.state, "需Claude介入")
        stop = [e for e in read_flow(self.cdir) if e.get("kind") == "转人工"][-1]
        self.assertNotIn("撤掉测试", stop.get("say", "") + stop.get("summary", ""))
        self.assertIn("接受剩下的问题", stop.get("say", ""))
        from collab.phase import card_phase
        self.assertIn("accept_issues", card_phase(str(self.collab.parent), "CARD-001")["actions"],
                      "认定不归本卡可以直接接受剩下的问题，不用绕继续改")
        self.assertNotIn("复查第", stop.get("say", ""), "给用户的话里不带内部编号")
        from collab.flow import record
        record(self.cdir, "追加要求", src="你", dst="程序", summary="退学学生页那件不归本卡，这次不做", text="那件不归本卡，这次不做")
        card.transition("开发中")
        dev = []

        def ask_fn(adapter, prompt, cwd, **kw):
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            dev.append(prompt)
            return AgentResult(ok=True, output="", attempts=1, final="汇报：改了")

        card2 = run_card(card, self.collab, self.wt, CMD, "cursor", "codex", ask_fn=listed_ask(ask_fn),
                         judge_fn=lambda *a, **k: Verdict(PASS, 0, "ok"), reviewer_adapter="codex",
                         require_tests=True, allowed=["app.py"])[0]
        self.assertNotEqual(card2.state, "需Claude介入", "写明不做之后要能接着做，不能又停在原处")
        self.assertTrue(dev, "接着派写代码的")

    def test_resume_stop_summary_has_no_dead_end(self):
        src = (Path(__file__).resolve().parent.parent / "collab" / "dispatch.py").read_text(encoding="utf-8")
        self.assertNotIn("撤掉对应测试", src)
