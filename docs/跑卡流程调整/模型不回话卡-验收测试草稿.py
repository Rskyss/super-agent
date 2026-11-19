"""模型不回话卡验收测试（Claude 写；**施工方不许改**，补充测试另写 test_noreply_extra*.py）。
依据 docs/跑卡流程调整/方案-模型不回话卡.md、施工单-模型不回话卡.md。

观察记录第 41 条：千问那条路一次请求发出后服务一个字不回，进程空等 15 分钟才被停下；
用户要：①分清“模型没回话（工具在空等）”和“工具在干活（在跑命令）”；②前者提醒里直接建议换成今天跑得正常的模型；
③停下的说明也带这条建议；④同一个模型连续卡两次，下次派活前就提醒。另：千问这条路让工具自己“没回话就重发”。
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from collab.agents import AgentResult, QuietTimeout, qwen_command, watched_runner
from collab.flow import read_flow, record
from collab.judge import FAIL, PASS, Verdict
from collab.state import Card
from review_fmt import listed_ask

WAIT_ONLY = "import time\ntime.sleep(30)\n"                                        # 不跑命令，干等
RUNS_COMMAND = "import subprocess\nsubprocess.run(['/bin/sh', '-c', 'sleep 30'])\n"   # 在跑一条命令


class ToolRetriesItself(unittest.TestCase):
    def test_qwen_route_resends_when_stream_goes_quiet(self):
        cmd = " ".join(qwen_command("你好", model="m"))
        self.assertIn("model_providers.qwtp.stream_idle_timeout_ms=120000", cmd)
        self.assertIn("model_providers.qwtp.stream_max_retries=3", cmd)


class TellWaitingFromWorking(unittest.TestCase):
    def quiet(self, script):
        said = []
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(QuietTimeout) as caught:
                watched_runner([sys.executable, "-c", script], tmp, timeout=60, quiet_limit=3, poll=0.2,
                               on_quiet=lambda seconds, waiting: said.append(waiting), remind_at=(1.5,))
        return said, caught.exception

    def test_waiting_on_model(self):
        said, stop = self.quiet(WAIT_ONLY)
        self.assertEqual(said, [True], "没在跑命令：算模型没回话")
        self.assertTrue(stop.waiting)
        self.assertIn("没回话", str(stop))

    def test_running_a_command_is_working(self):
        said, stop = self.quiet(RUNS_COMMAND)
        self.assertEqual(said, [False], "在跑命令：算工具在干活")
        self.assertFalse(stop.waiting)
        self.assertNotIn("没回话", str(stop))

    def test_old_one_argument_reminder_still_called(self):
        said = []
        with tempfile.TemporaryDirectory() as tmp:
            watched_runner([sys.executable, "-c", "import time; time.sleep(2.5)"], tmp, timeout=30, quiet_limit=20,
                           poll=0.2, on_quiet=said.append, remind_at=(1,))
        self.assertEqual(len(said), 1)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.wt = root / "wt"
        self.wt.mkdir()
        self.collab = root / ".collab"
        self.cdir = self.collab / "cards" / "CARD-002"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡\n", encoding="utf-8")
        self.prompts = []

    def tearDown(self):
        self.tmp.cleanup()

    def worked_today(self, tool="agy", model="Gemini 3.8 Flash（高）", when=None):
        """另一张卡今天有人顺利交过活：派活记了模型，回复成功。"""
        other = self.collab / "cards" / "CARD-001"
        other.mkdir(parents=True, exist_ok=True)
        sent = record(other, "派活", src="调度员", dst="开发员", tool=tool, step="开发", round=1, model=model,
                      summary="按测试写代码")
        record(other, "回复", src="开发员", dst="调度员", tool=tool, step="开发", round=1, ok=True,
               reply_to=sent["seq"], summary="改好了")
        if when:   # 改成别的日子
            path = other / "流转.jsonl"
            lines = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            for ev in lines:   # 11-19 起记录只记运行时间（秒数 ts），不记钟点
                ev.pop("time", None)
                ev["ts"] = datetime.fromisoformat(when).timestamp()
            path.write_text("".join(json.dumps(ev, ensure_ascii=False) + "\n" for ev in lines), encoding="utf-8")

    def go(self, dev):
        def ask_fn(adapter, prompt, cwd, on_quiet=None, **kw):
            self.prompts.append(prompt)
            if "只读复查" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="结论：通过")
            if "编写自动化测试" in prompt:
                return AgentResult(ok=True, output="", attempts=1, final="写好了")
            result = dev(on_quiet)
            self.stuck = result is STUCK   # 被停下时程序先跑一次本卡测试：让它不过，才会停下
            return result
        self.stuck = False
        return run_card_for(self, ask_fn)

    def kinds(self, kind):
        return [e for e in read_flow(self.cdir) if e.get("kind") == kind]


def run_card_for(case, ask_fn):
    from collab.dispatch import run_card
    return run_card(Card("CARD-002", state="待派发"), case.collab, case.wt, "true", "qwen", "codex",
                    ask_fn=listed_ask(ask_fn),
                    judge_fn=lambda *a, **k: Verdict(FAIL, 1, "FAILED (failures=1)") if case.stuck else Verdict(PASS, 0, "ok"),
                    reviewer_adapter="codex", models={"qwen": "deepseek-v4.1-flash"})[0]


STUCK = AgentResult(ok=False, output="看门人：超过 15 分钟模型没回话（工具在等模型，没在跑命令、没改文件），已停止",
                    attempts=1)


class Reminders(Base):
    def test_waiting_reminder_suggests_model_that_worked_today(self):
        self.worked_today()

        def dev(on_quiet):
            on_quiet(300, True)
            return AgentResult(ok=True, output="", attempts=1, final="好了")
        self.go(dev)
        tip = self.kinds("提醒")[-1]["summary"]
        self.assertIn("没回话", tip)
        self.assertIn("换成", tip)
        self.assertIn("agy", tip)
        self.assertIn("Gemini 3.8 Flash", tip, "建议今天跑得正常的那个")

    def test_working_reminder_says_it_is_running_a_command(self):
        self.worked_today()

        def dev(on_quiet):
            on_quiet(300, False)
            return AgentResult(ok=True, output="", attempts=1, final="好了")
        self.go(dev)
        tip = self.kinds("提醒")[-1]["summary"]
        self.assertIn("在跑命令", tip)
        self.assertNotIn("换成", tip, "在干活就不劝换模型")

    def test_old_style_reminder_keeps_old_words(self):
        def dev(on_quiet):
            on_quiet(300)
            return AgentResult(ok=True, output="", attempts=1, final="好了")
        self.go(dev)
        self.assertIn("已经 5 分钟没有动静", self.kinds("提醒")[-1]["summary"])

    def test_only_yesterdays_model_is_not_suggested(self):
        self.worked_today(when="2020-01-01T10:00:00")

        def dev(on_quiet):
            on_quiet(300, True)
            return AgentResult(ok=True, output="", attempts=1, final="好了")
        self.go(dev)
        tip = self.kinds("提醒")[-1]["summary"]
        self.assertNotIn("Gemini 3.8 Flash", tip, "不是今天跑过的不算")
        self.assertIn("换", tip, "没有可推荐的也要说换一个模型")


class StopWords(Base):
    def test_stop_after_no_reply_suggests_switch(self):
        self.worked_today()
        card = self.go(lambda on_quiet: STUCK)
        self.assertEqual(card.state, "需Claude介入")
        stop = self.kinds("转人工")[-1]["summary"]
        self.assertIn("没回话", stop)
        self.assertIn("换成", stop)
        self.assertIn("Gemini 3.8 Flash", stop)


class TwiceInARow(Base):
    def test_second_stuck_in_a_row_warns_before_next_dispatch(self):
        self.worked_today()
        for _ in range(2):
            self.go(lambda on_quiet: STUCK)
        before = len(read_flow(self.cdir))
        self.go(lambda on_quiet: AgentResult(ok=True, output="", attempts=1, final="好了"))
        new = read_flow(self.cdir)[before:]
        first_dev = next(i for i, e in enumerate(new) if e.get("kind") == "派活" and e.get("dst") == "开发员")
        warns = [e for e in new[:first_dev] if e.get("kind") == "提醒" and "连续" in (e.get("summary") or "")]
        self.assertTrue(warns, "派活前就提醒：这个模型连续两次没回话")
        self.assertIn("换成", warns[-1]["summary"])

    def test_success_resets_the_count(self):
        self.worked_today()
        self.go(lambda on_quiet: STUCK)
        self.go(lambda on_quiet: AgentResult(ok=True, output="", attempts=1, final="好了"))
        self.go(lambda on_quiet: STUCK)
        before = len(read_flow(self.cdir))
        self.go(lambda on_quiet: AgentResult(ok=True, output="", attempts=1, final="好了"))
        warns = [e for e in read_flow(self.cdir)[before:] if e.get("kind") == "提醒" and "连续" in (e.get("summary") or "")]
        self.assertEqual(warns, [], "中间顺利交过一次活，就不算连续")


if __name__ == "__main__":
    unittest.main()
