"""判定程序：执行测试命令，只看退出码。AI 的自述不参与判定。"""
from __future__ import annotations

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
    try:
        proc = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        return Verdict(ENV_ERROR, None, f"找不到命令：{exc}")
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return Verdict(TIMEOUT, None, out[-_OUTPUT_LIMIT:])
    output = (proc.stdout or "") + (proc.stderr or "")
    result = PASS if proc.returncode == 0 else FAIL
    return Verdict(result, proc.returncode, output[-_OUTPUT_LIMIT:])
