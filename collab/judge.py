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
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                stdout, stderr = proc.communicate(timeout=5)
                break
            except subprocess.TimeoutExpired:
                stdout, stderr = "", ""
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
