"""agy 改走 ACP（docs/agy改走ACP/）：派活叫 agy 不再用 `agy -p`，改由翻译 collab/agy_acp.py 用 ACP 跟 `agy acp` 说话，
再翻成原来 stream-json 的格式。这里用假 agy（tests/fake_agy_acp.py）把各种回话演一遍，
看后面真正在读的几处（回复原文、报错原因、正在跑的命令、用量、边干边吐、停工）照旧认得。"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collab import agents  # noqa: E402
from collab.agents import _last_command, agy_command, agy_error, ask, readable  # noqa: E402
from collab.config import DEFAULT_MODELS  # noqa: E402
from collab.token_usage import usage_for  # noqa: E402

HERE = Path(__file__).resolve().parent
FAKE = HERE / "fake_agy_acp.py"


class FakeAgy:
    """临时 bin 里放一个叫 agy 的假程序，PATH 指过去。"""

    def __init__(self, scene="ok"):
        self.tmp = tempfile.TemporaryDirectory(prefix="假agy-")
        root = Path(self.tmp.name)
        self.bin = root / "bin"
        self.bin.mkdir()
        self.work = root / "work"
        self.work.mkdir()
        self.log = root / "收到.jsonl"
        self.pidfile = root / "pid"
        self.gate = root / "放行"
        shim = self.bin / "agy"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        self.env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}", FAKE_ACP=scene,
                        FAKE_ACP_LOG=str(self.log), FAKE_ACP_PIDFILE=str(self.pidfile), FAKE_ACP_GATE=str(self.gate))

    def run(self, prompt="修 bug", model="gemini-4-neon-high", timeout=30):
        p = subprocess.run(agy_command(prompt, model=model), cwd=self.work, env=self.env, capture_output=True,
                           text=True, timeout=timeout)
        return p.returncode, p.stdout + p.stderr

    def received(self):
        if not self.log.exists():
            return []
        return [json.loads(ln) for ln in self.log.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def close(self):
        self.tmp.cleanup()


class Base(unittest.TestCase):
    scene = "ok"

    def setUp(self):
        self.fake = FakeAgy(self.scene)
        self.addCleanup(self.fake.close)


class Command(unittest.TestCase):
    def test_no_more_dash_p(self):
        cmd = agy_command("干活", model="gemini-4-neon-high")
        self.assertNotIn("-p", cmd, "派活不能再用 Google 点名不支持的 agy -p")
        self.assertTrue(any(str(c).endswith("agy_acp.py") for c in cmd), cmd)
        self.assertIn("gemini-4-neon-high", cmd)
        self.assertIn("干活", cmd)

    def test_bridge_file_ships_next_to_agents(self):
        """应用打包时 collab/ 整个带走：翻译必须和 agents.py 放在一起"""
        script = next(c for c in agy_command("x") if str(c).endswith("agy_acp.py"))
        self.assertEqual(Path(script).parent, Path(agents.__file__).resolve().parent)


class Success(Base):
    def test_reply_usage_and_command(self):
        code, out = self.fake.run("修 bug")
        self.assertEqual(code, 0, out)
        self.assertEqual(readable(out, "agy"), "我先跑一下测试。测试全过了：修 bug",
                         "和原来 agy -p 一样：这一轮说的全部话原样连起来（11-22 对过 5 份真实记录）")
        self.assertEqual(agy_error(out), "")
        self.assertEqual(_last_command(out), "python3 -m unittest tests.test_x", "看门人要认得它在跑哪条命令")
        used = usage_for("agy", out)
        self.assertTrue(used["ok"], used)
        self.assertEqual(used["input"], 4000, "输入 = 没缓存的 + 读缓存的（11-22 实测 ACP 的 inputTokens 不含缓存）")
        self.assertEqual(used["cached"], 3000)
        self.assertEqual(used["output"], 50)
        self.assertEqual(used["total"], 4050)

    def test_speaks_acp_in_order_and_sets_model(self):
        self.fake.run(model="gemini-3.8-flash-low")
        methods = [m.get("method") for m in self.fake.received() if m.get("method")]
        self.assertEqual(methods, ["initialize", "session/new", "session/set_model", "session/prompt"])
        new = next(m for m in self.fake.received() if m.get("method") == "session/new")
        self.assertEqual(Path(new["params"]["cwd"]).resolve(), self.fake.work.resolve(), "在副本里干活")
        setm = next(m for m in self.fake.received() if m.get("method") == "session/set_model")
        self.assertEqual(setm["params"]["modelId"], "gemini-3.8-flash-low")

    def test_no_model_skips_set_model(self):
        self.fake.run(model="")
        methods = [m.get("method") for m in self.fake.received() if m.get("method")]
        self.assertNotIn("session/set_model", methods)

    def test_every_line_is_json(self):
        """输出写进实时日志，后面一行行当 JSON 读：不能混进别的字"""
        _code, out = self.fake.run()
        for line in out.splitlines():
            if line.strip():
                self.assertIsInstance(json.loads(line), dict, line)

    def test_ask_accepts_it(self):
        res = ask("agy", "修 bug", self.fake.work, models=dict(DEFAULT_MODELS, agy="gemini-4-neon-high"),
                  runner=lambda cmd, cwd, timeout, **k: subprocess_run(cmd, cwd, self.fake.env))
        self.assertTrue(res.ok, res.output)
        self.assertEqual(res.final, "我先跑一下测试。测试全过了：修 bug")


def subprocess_run(cmd, cwd, env):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=30)
    return p.returncode, p.stdout + p.stderr


class BadModel(Base):
    def test_reason_is_agys_own_words(self):
        code, out = self.fake.run(model="no-such-model")
        self.assertNotEqual(code, 0)
        self.assertEqual(readable(out, "agy"), "", "没做成不能把报错当回复")
        self.assertIn("Unknown model ID: no-such-model", agy_error(out))
        methods = [m.get("method") for m in self.fake.received() if m.get("method")]
        self.assertNotIn("session/prompt", methods, "模型设不上就别干活（不悄悄用默认模型）")


class Crash(Base):
    scene = "crash"

    def test_crash_is_failure_with_reason(self):
        code, out = self.fake.run()
        self.assertNotEqual(code, 0)
        self.assertEqual(readable(out, "agy"), "")
        self.assertIn("agy", agy_error(out))
        self.assertIn("退出", agy_error(out))


class CrashUsage(Base):
    scene = "crash"

    def test_no_usage_is_unknown_not_zero(self):
        """复查第 1 轮：没拿到用量就是“读不到”，不能记成 0"""
        _code, out = self.fake.run()
        self.assertFalse(usage_for("agy", out)["ok"])

    def test_team_check_says_agys_reason(self):
        """复查第 1 轮：团队检测碰上 agy 崩了，要说 agy 的原因，不是“命令跑完了但没建出文件”+一截 JSON"""
        from collab.check import explain
        _code, out = self.fake.run()
        reason, how = explain(out, "agy")
        self.assertIn("意外退出", reason + how)
        self.assertNotIn('{"event"', how)


class SplitReply(Base):
    scene = "split"

    def test_conclusion_split_by_a_tool_call_is_kept(self):
        """复查第 1、2 轮：结论和清单之间夹了跑命令，回复照原来 agy -p 的样子给全部原话，结论和清单一个字不丢"""
        code, out = self.fake.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(readable(out, "agy"), "我先看看。结论：不通过\n1. 【中等】保存后数据丢了")


class QuotaInData(Base):
    scene = "quota"

    def test_reason_in_error_data_survives(self):
        code, out = self.fake.run()
        self.assertNotEqual(code, 0)
        self.assertIn("credits balance is too low", agy_error(out))
        from collab.dispatch import _credit_problem
        self.assertIn("额度用完", _credit_problem("复查员", "agy", out))


class Orphan(Base):
    scene = "orphan"

    def test_agy_exit_is_noticed_even_if_a_child_holds_the_pipe(self):
        start = time.time()
        code, out = self.fake.run(timeout=20)
        self.assertLess(time.time() - start, 8, "agy 已经退出，翻译不能干等它的子进程")
        self.assertNotEqual(code, 0)
        self.assertIn("退出码 7", agy_error(out))


class ToolWorking(unittest.TestCase):
    """复查第 1 轮：ACP 服务本身是翻译拉起的常驻进程，不算“在跑命令”；它拉起的命令照算"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="agy-real-")
        self.addCleanup(tmp.cleanup)
        self.agy = Path(tmp.name) / "agy-real"   # 真能执行的程序（看门人认“真程序”要它存在）
        self.agy.write_text("#!/bin/sh\n", encoding="utf-8")
        self.agy.chmod(0o755)

    def table(self, *extra):
        rows = {100: (1, 60, f"{sys.executable} /x/collab/agy_acp.py --model m -- 干活"),
                101: (100, 60, f"{self.agy} --dangerously-skip-permissions acp --dangerously-skip-permissions")}
        rows.update(extra)
        return rows

    def test_waiting_acp_server_is_not_working(self):
        from unittest import mock
        with mock.patch.object(agents, "_process_table", return_value=self.table()):
            self.assertFalse(agents._tool_working(100))

    def test_command_under_acp_server_is_working(self):
        from unittest import mock
        with mock.patch.object(agents, "_process_table", return_value=self.table((102, (101, 5, "/bin/sleep 30")))):
            self.assertTrue(agents._tool_working(100))


class Cancelled(Base):
    scene = "cancelled"

    def test_not_end_turn_is_failure(self):
        code, out = self.fake.run()
        self.assertNotEqual(code, 0)
        self.assertEqual(readable(out, "agy"), "", "没正常做完：宁可算没完成")
        self.assertIn("cancelled", agy_error(out))


class Permission(Base):
    scene = "permission"

    def test_always_allows(self):
        """口径 1B：权限一律自动批，挑“一直允许”"""
        code, out = self.fake.run()
        self.assertEqual(code, 0, out)
        self.assertEqual(readable(out, "agy"), "我先跑一下测试。测试全过了：修 bug")


class ClientRequest(Base):
    scene = "fs_request"

    def test_unknown_request_is_refused_not_hung(self):
        code, out = self.fake.run()
        self.assertEqual(code, 0, out)


class Streaming(Base):
    scene = "slow"

    def test_steps_come_out_before_done(self):
        """㉙：只读不改的长活也要边干边吐，看门人靠输出在涨知道它在干活"""
        proc = subprocess.Popen(agy_command("读代码", model="gemini-4-neon-high"), cwd=self.fake.work, env=self.fake.env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(proc.kill)
        seen = []
        deadline = time.time() + 15
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            seen.append(line)
            ev = json.loads(line)
            step = ev.get("step_update") or {}
            if step.get("tool_name") == "read_file" and step.get("state") == "ACTIVE":
                break
        self.assertTrue(any('"read_file"' in s for s in seen), "还没做完就该看到读文件那一步")
        self.assertFalse(any('"event": "result"' in s or '"event":"result"' in s for s in seen))
        self.fake.gate.write_text("ok")
        rest = proc.stdout.read()
        proc.wait(timeout=15)
        self.assertEqual(proc.returncode, 0, rest)


class Stop(Base):
    scene = "hang"

    def test_sigterm_to_bridge_alone_takes_agy_down(self):
        """复查第 1 轮：只给翻译发 SIGTERM（不是整组停）时，也要把 agy 带走、自己马上退出"""
        proc = subprocess.Popen(agy_command("干活", model="gemini-4-neon-high"), cwd=self.fake.work, env=self.fake.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.addCleanup(lambda: os.killpg(proc.pid, 9) if proc.poll() is None else None)
        deadline = time.time() + 10
        while not self.fake.pidfile.exists() and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.3)
        pid = int(self.fake.pidfile.read_text())
        proc.terminate()
        proc.wait(timeout=5)
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_stop_takes_agy_down_too(self):
        proc = subprocess.Popen(agy_command("干活", model="gemini-4-neon-high"), cwd=self.fake.work, env=self.fake.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        deadline = time.time() + 10
        while not self.fake.pidfile.exists() and time.time() < deadline:
            time.sleep(0.05)
        pid = int(self.fake.pidfile.read_text())
        agents._stop(proc)
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError, msg="停工时 agy 必须跟着停"):
            os.kill(pid, 0)


if __name__ == "__main__":
    unittest.main()
