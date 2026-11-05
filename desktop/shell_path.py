"""向用户的终端要一份“找命令的路径”，补到本机服务身上。

双击打开的窗口（从程序坞/访达启动）只拿到系统默认的路径；终端启动时还会读用户自己的设置
（zsh 读 .zshrc），像用 npm 装的 Codex 就只写在那里——不补上，检测连通会误报“没装”，
Codex 当调度员的聊天也连不上。补上之后，从本机服务派出去的调度员、执行员都跟着能找到。

只取路径这一项：终端设置里常有各种密钥，不能顺手带给 AI。
已有的顺序原样保留、只在后面补缺的，所以 python3 等原来找到的命令不会换成别的。
"""
from __future__ import annotations

import os
import signal
import subprocess

_START, _END = "<<super-agent-path>>", "<<super-agent-end>>"


def terminal_path(shell=None, timeout: float = 5.0):
    """像打开一个终端那样启动用户的 shell，读出它的路径；读不到（没这个 shell、卡住、没输出）返回 None。"""
    shell = shell or os.environ.get("SHELL") or "/bin/zsh"
    command = f'printf "%s%s%s" "{_START}" "$PATH" "{_END}"'  # 前后加记号：设置里打印的欢迎语等不会混进来
    try:
        proc = subprocess.Popen([shell, "-l", "-i", "-c", command], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return None
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait()  # 只等它退出，不等输出读完：它留下的后台小程序可能还占着输出口
        proc.stdout.close()
        return None
    text = out.decode("utf-8", "replace")
    if _START not in text or _END not in text:
        return None
    value = text.split(_START, 1)[1].split(_END, 1)[0].strip()
    return value or None


def merged_path(current: str, extra: str) -> str:
    """原来的路径顺序不变，只把终端里多出来的补在后面；去掉空项和重复。"""
    seen, parts = set(), []
    for entry in (current or "").split(os.pathsep) + (extra or "").split(os.pathsep):
        if entry and entry not in seen:
            seen.add(entry)
            parts.append(entry)
    return os.pathsep.join(parts)


def adopt_terminal_path(env=None, shell=None, timeout: float = 5.0) -> bool:
    """把终端的路径补进 env（默认是本进程自己的环境）；补上了返回 True，读不到就什么都不改。"""
    env = os.environ if env is None else env
    found = terminal_path(shell, timeout)
    if not found:
        return False
    env["PATH"] = merged_path(env.get("PATH", ""), found)
    return True
