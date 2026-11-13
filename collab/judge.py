"""判定程序：执行测试命令，只看退出码。AI 的自述不参与判定。"""
from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass

PASS = "过"
FAIL = "没过"
ENV_ERROR = "环境错误"
TIMEOUT = "超时"

_OUTPUT_LIMIT = 4000


@dataclass
class Verdict:
    result: str
    returncode: int | None
    output: str


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _stop_group(proc) -> str:
    """先温和停整组，等到整组真的没了（管道关了不代表子进程退了，第三批复查第 3 轮），5 秒还在就强停。"""
    import time
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    deadline = time.time() + 5
    while _group_alive(proc.pid) and time.time() < deadline:
        time.sleep(0.1)
    if _group_alive(proc.pid):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        deadline = time.time() + 3
        while _group_alive(proc.pid) and time.time() < deadline:
            time.sleep(0.1)
    try:
        stdout, _ = proc.communicate(timeout=3)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        stdout = ""
    return stdout or ""


def judge(command, cwd, timeout) -> Verdict:
    """超时时连同它拉起的测试子进程一起停掉（流程修理第三批复查：只停外壳会留下还在跑的测试）。"""
    try:
        proc = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    except FileNotFoundError as exc:
        return Verdict(ENV_ERROR, None, f"找不到命令：{exc}")
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stdout = _stop_group(proc)
        return Verdict(TIMEOUT, None, (stdout or "")[-_OUTPUT_LIMIT:])
    output = (stdout or "") + (stderr or "")
    if proc.returncode == 0:
        result = PASS
    elif proc.returncode in (126, 127):
        # 126 不能执行、127 找不到命令：都算环境错误，不是开发员的错
        result = ENV_ERROR
    else:
        result = FAIL
    return Verdict(result, proc.returncode, output[-_OUTPUT_LIMIT:])
