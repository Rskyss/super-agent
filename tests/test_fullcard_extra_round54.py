"""第四轮 54 复查退回的七条回归测试。"""

import fcntl
import io
import json
import os
import shlex
import signal
import sys
from contextlib import redirect_stdout
from pathlib import Path
from textwrap import dedent
from unittest import mock

from acceptance_helpers import ProjectCase, needs_node, run_node, wait_until
from collab import cli
from collab.accept import accept_remaining
from collab.flow import read_flow, record, text_of
from collab.fullrun import FullRunner
from collab.judge import PASS
from collab.phase import card_phase
from desktop.clerk.tasks import launch
from desktop.view import card_view


CARD = "CARD-001"
PASSING_TEST = """\
import unittest

class Example(unittest.TestCase):
    def test_passes(self):
        self.assertTrue(True)
"""


class FullCardReviewRound54(ProjectCase):
    def setUp(self):
        super().setUp()
        os.environ["COLLAB_PYTHON"] = sys.executable

    def blocked_card(self):
        cdir = self.make_card(CARD, state="需Claude介入", plan=True)
        record(cdir, "转人工", src="程序", dst="调度员", cause="复查不通过",
               summary="第 2 次复查不通过，需要调度员看原因", text="复查剩下的一条问题")
        return cdir, self.make_copy(CARD)

    def write_test(self, worktree, name, source):
        path = worktree / "tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def start_acceptance_with_paused_test(self, worktree):
        # 信号文件在副本之外，测试进程报到、放行都不会让代码指纹变化。
        ready = Path(self.tmp) / "测试已开始"
        proceed = Path(self.tmp) / "让测试继续"
        lock = Path(self.tmp) / "测试进程锁"
        self.write_test(worktree, "test_paused.py", dedent(f"""\
            import fcntl
            import time
            import unittest
            from pathlib import Path

            class Paused(unittest.TestCase):
                def test_wait_for_release(self):
                    with Path({str(lock)!r}).open("w") as handle:
                        fcntl.flock(handle, fcntl.LOCK_EX)
                        Path({str(ready)!r}).touch()
                        proceed = Path({str(proceed)!r})
                        deadline = time.monotonic() + 30
                        while not proceed.exists() and time.monotonic() < deadline:
                            time.sleep(0.05)
                        self.assertTrue(proceed.exists(), "测试一直没收到放行信号")
            """))
        # 接受进程若被直接终止，来不及清副本时也能随临时项目一并清掉。
        with mock.patch.dict(os.environ, {"TMPDIR": self.tmp}):
            pid = launch(self.project, "accept", CARD, ["accept-issues", self.project, CARD])

        def release_and_wait():
            proceed.touch()
            self.wait_for_acceptance_exit()
            if ready.exists():
                wait_until(lambda: self.worker_stopped(lock), 5, "临时测试进程一直没退出")

        self.addCleanup(release_and_wait)
        wait_until(ready.exists, 15, "接受命令没有真正启动测试进程")
        return pid, proceed, lock

    def wait_for_acceptance_exit(self):
        backend = self.collab / "后台" / "accept.json"
        wait_until(lambda: not backend.exists(), 15, "后台接受任务一直没退出")

    @staticmethod
    def worker_stopped(lock):
        # 进程退出时系统会释放锁，能避开已退出但还没被回收的进程。
        with lock.open("r+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            return True

    def test_01_empty_python_suite_cannot_go_to_pending_confirmation(self):
        cdir, worktree = self.blocked_card()
        self.write_test(worktree, "test_empty.py", "# 文件在，但没有一项测试。\n")

        answer = accept_remaining(self.project, CARD)

        self.assertEqual(self.state(CARD), "需Claude介入", f"只跑了 0 项却返回 {answer}")
        self.assertFalse(answer["ok"], answer)
        events = read_flow(cdir)
        self.assertEqual(events[-1]["kind"], "接受失败")
        self.assertIn("全部测试", events[-1]["summary"])
        self.assertFalse(any(e["kind"] == "接受" or "全部测试也过了" in e.get("summary", "") for e in events))


    def test_03_old_accept_result_cannot_overwrite_cli_resume(self):
        _, worktree = self.blocked_card()
        _, proceed, _ = self.start_acceptance_with_paused_test(worktree)
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.addCleanup(signal.signal, sig, signal.getsignal(sig))

        # 走终端入口，让它保存开发中。派活停在入口，避免调用工具或改副本的代码。
        with mock.patch.object(cli, "run_card", side_effect=lambda card, **_: (card, None)), \
             mock.patch.object(cli, "_remind_upgrades"), redirect_stdout(io.StringIO()):
            cli.main(["run", self.project, CARD, "--resume-blocked", "--test-command", "true",
                      "--dev-tool", "codex", "--test-tool", "codex", "--review-tool", "none"])
        self.assertEqual(self.state(CARD), "开发中", "终端续跑应先保存为开发中")
        proceed.touch()
        self.wait_for_acceptance_exit()

        self.assertEqual(self.state(CARD), "开发中", "旧一轮跑完后覆盖了续跑的状态")
        self.assertNotIn("接受", self.flow_kinds(CARD))

    @needs_node()
    def test_04_default_flow_panel_shows_acceptance_tests_running(self):
        _, worktree = self.blocked_card()
        self.start_acceptance_with_paused_test(worktree)
        payload = card_view(self.project, CARD)

        # 把真实后台数据交给默认流程页的脚本，用最小页面模型观察渲染结果。
        result = run_node("const payload = " + json.dumps(payload) + ";\n" + r'''
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class Element {
  hidden = false;
  textContent = '';
  innerHTML = '';
  children = [];
  selectors = new Map();
  classList = {add() {}, toggle() {}};
  querySelector(selector) {
    if (!this.selectors.has(selector)) this.selectors.set(selector, new Element());
    return this.selectors.get(selector);
  }
  addEventListener() {}
  appendChild(child) { this.children.push(child); }
  setAttribute() {}
}

const html = fs.readFileSync('desktop/static/panel.html', 'utf8');
const elements = new Map();
for (const [tag, id] of html.matchAll(/<[^>]+ id="([^"]+)"[^>]*>/g)) {
  const element = new Element();
  element.hidden = /\shidden\b/.test(tag);
  elements.set(id, element);
}
const get = id => elements.get(id);
vm.runInNewContext(fs.readFileSync('desktop/static/panel.js', 'utf8'), {
  location: {search: '?project=fixture&clerk=1'}, window: {}, URLSearchParams,
  document: {getElementById: get, createElement: () => new Element()},
  fetch: async () => ({json: async () => payload}), setTimeout() {}, setInterval() {},
});

setImmediate(() => {
  assert.ok(get('offline').hidden, '流程面板没有完成渲染');
  assert.ok(get('actions').hidden || !get('actionBtns').innerHTML.includes('data-act='), '跑测试时应收起操作按钮');
  const checkIndex = payload.stations.findIndex(station => station.key === 'check');
  const check = get('steps').children[checkIndex].querySelector('button');
  const detail = get('pane-flow').hidden ? '' : check.querySelector('.d').textContent;
  const hint = get('actions').hidden || get('phaseHint').hidden ? '' : get('phaseHint').textContent;
  assert.ok([detail, hint].some(text => text.includes('正在跑全部测试')),
    `默认流程页缺少“正在跑全部测试”，跑测试格显示 ${JSON.stringify(detail)}，阶段提示显示 ${JSON.stringify(hint)}`);
});
''')

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_05_full_run_uses_python_configured_with_a_relative_path(self):
        worktree = self.make_copy(CARD)
        self.write_test(worktree, "test_passes.py", PASSING_TEST)
        caller = Path(self.tmp)
        python = caller / ".venv" / "bin" / "python3"
        used = caller / "用到了指定的解释器"
        python.parent.mkdir(parents=True)
        python.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(used))}\n"
                          f"exec {shlex.quote(sys.executable)} \"$@\"\n", encoding="utf-8")
        python.chmod(0o755)

        previous = Path.cwd()
        try:
            os.chdir(caller)
            os.environ["COLLAB_PYTHON"] = "./.venv/bin/python3"
            verdict = FullRunner(timeout=20).run(worktree)
        finally:
            os.chdir(previous)

        self.assertTrue(used.is_file(), f"测试没使用相对路径指定的 Python：{verdict.result}\n{verdict.output}")
        self.assertEqual(verdict.result, PASS, verdict.output)

    def test_06_sigterm_stops_worker_and_records_why_acceptance_stopped(self):
        cdir, worktree = self.blocked_card()
        accept_pid, _, worker_lock = self.start_acceptance_with_paused_test(worktree)
        self.assertFalse(self.worker_stopped(worker_lock), "发送信号前测试进程应还在运行")

        os.kill(accept_pid, signal.SIGTERM)
        self.wait_for_acceptance_exit()

        failure = next((e for e in reversed(read_flow(cdir)) if e["kind"] == "接受失败"), None)
        reason = f"{failure.get('summary', '')}\n{text_of(cdir, failure) or ''}" if failure else ""
        self.assertEqual((self.worker_stopped(worker_lock), bool(failure)), (True, True),
                         f"外部叫停应收掉测试进程并留下接受失败记录。交接原因：{reason or '无'}")
        self.assertRegex(reason, r"叫停|停了|停止|终止|中断|取消|SIGTERM")
        self.assertEqual(self.state(CARD), "需Claude介入")
        self.assertIn("accept_issues", card_phase(self.project, CARD)["actions"])

    def test_07_timeout_keeps_partial_test_output_in_handoff(self):
        cdir, worktree = self.blocked_card()
        self.write_test(worktree, "test_passes.py", PASSING_TEST)
        self.write_test(worktree, "test_stuck.py", dedent("""\
            import sys
            import time
            import unittest

            class Stuck(unittest.TestCase):
                def test_prints_then_waits(self):
                    print("进度：开始核对输入文件", flush=True)
                    print("报错：缺少临时输入", file=sys.stderr, flush=True)
                    time.sleep(30)
            """))

        answer = accept_remaining(self.project, CARD, runner=FullRunner(timeout=20, file_timeout=2.5))

        self.assertFalse(answer["ok"], answer)
        failure = read_flow(cdir)[-1]
        self.assertEqual(failure["kind"], "接受失败")
        self.assertIn("超时", failure["summary"])
        handoff = text_of(cdir, failure) or ""
        self.assertIn("test_stuck.py", handoff)
        self.assertIn("test_passes.py", handoff)
        self.assertIn("进度：开始核对输入文件", handoff)
        self.assertIn("报错：缺少临时输入", handoff)
