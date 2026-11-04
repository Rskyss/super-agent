"""三家 CLI 适配器：拼命令、执行、检查文件是否真的落盘。

命令已在本机实测通过（见 docs/多agent协作包/联调手册.md）。
不使用 --force / --dangerously-skip-permissions：权限分类器会拒绝，也不必要。
"""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from collab.config import DEFAULT_MODELS

MAX_ATTEMPTS = 2  # 一次 + 一次重试（Cursor 偶发不落盘）
DEFAULT_TIMEOUT = 1800  # 开发环节 30 分钟（D5）
QUIET_LIMIT = 15 * 60  # 看门人：副本里连续 15 分钟没有任何文件变化，判定为卡住
POLL_SECONDS = 15  # 看门人每隔多久看一次


def cursor_command(prompt: str, model: str = DEFAULT_MODELS["cursor"]):
    return ["agent", "-p", "--trust", "--model", model, "--output-format", "json", prompt]


def codex_command(prompt: str, model: str = DEFAULT_MODELS["codex"],
                  effort: str = DEFAULT_MODELS["codex_effort"]):
    return ["codex", "exec", "--skip-git-repo-check", "-s", "workspace-write",
            "-m", model, "-c", f'model_reasoning_effort="{effort}"', prompt]


def agy_command(prompt: str, model: str = DEFAULT_MODELS["agy"]):
    return ["agy", "-p", prompt, "--model", model, "--output-format", "json"]


def _build(adapter: str, prompt: str, models: dict):
    if adapter == "cursor":
        return cursor_command(prompt, model=models["cursor"])
    if adapter == "codex":
        return codex_command(prompt, model=models["codex"], effort=models["codex_effort"])
    if adapter == "agy":
        return agy_command(prompt, model=models["agy"])
    raise KeyError(adapter)  # 未知适配器直接报错


@dataclass
class AgentResult:
    ok: bool
    output: str
    attempts: int


class QuietTimeout(Exception):
    """看门人：干活的 AI 长时间没有改动任何文件，判定为卡住。"""

    def __init__(self, quiet_seconds: int):
        self.quiet_seconds = quiet_seconds
        super().__init__(f"超过 {max(1, quiet_seconds // 60)} 分钟没有任何文件变化")


def _newest_mtime(root) -> float:
    """副本里最新一次文件改动的时间（跳过 .git 和依赖目录，它们不代表“在干活”）。"""
    newest = 0.0
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules")]
        for name in files:
            try:
                mtime = os.path.getmtime(os.path.join(base, name))
            except OSError:
                continue
            newest = max(newest, mtime)
    return newest


def _stop(proc) -> None:
    """连同它拉起的子进程一起停掉（agent 会再启动 node 等子进程）。"""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
    proc.wait()


def watched_runner(cmd, cwd, timeout, quiet_limit=QUIET_LIMIT, poll=POLL_SECONDS):
    """边跑边看：总时长超过 timeout 抛 TimeoutExpired；副本里连续 quiet_limit 秒
    没有任何文件变化抛 QuietTimeout。输出写临时文件，避免管道写满卡住。"""
    started = time.time()
    last_activity = started
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as out:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, start_new_session=True)
        try:
            while proc.poll() is None:
                time.sleep(poll)
                now = time.time()
                last_activity = max(last_activity, _newest_mtime(cwd))
                if now - started > timeout:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                if now - last_activity > quiet_limit:
                    raise QuietTimeout(int(now - last_activity))
        except BaseException:
            _stop(proc)
            raise
        out.seek(0)
        return proc.returncode, out.read()


def subprocess_runner(cmd, cwd, timeout):
    """旧的一次性执行方式（不看进度），保留给需要时使用。"""
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def ask(adapter: str, prompt: str, cwd, expect_file=None, timeout=DEFAULT_TIMEOUT,
        runner=watched_runner, models=None) -> AgentResult:
    """让某家工具执行一次任务。成功条件：退出码 0，且（若指定）期望文件真的存在。
    models 缺省时用包默认值；派发程序应传入 load_models(项目目录) 的结果。
    默认带看门人：长时间没动静会被停掉，并且不重试（卡住的再跑一遍多半还是卡住）。"""
    cmd = _build(adapter, prompt, models or DEFAULT_MODELS)  # 未知适配器直接报错
    output = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            code, output = runner(cmd, cwd, timeout)
        except QuietTimeout as exc:
            return AgentResult(ok=False, output=f"看门人：{exc}，已停止", attempts=attempt)
        except subprocess.TimeoutExpired:
            return AgentResult(ok=False, output="超时", attempts=attempt)
        if code != 0:
            continue
        if expect_file is None or (Path(cwd) / expect_file).exists():
            return AgentResult(ok=True, output=output, attempts=attempt)
    return AgentResult(ok=False, output=output, attempts=MAX_ATTEMPTS)
