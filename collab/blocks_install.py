"""积木库：核实名单和安装执行（53 号单）。

只有名单里的安装方式能由程序执行，每条都核对过官方来源（2026-11-08）；行政只能按编号选，不能自己写命令。
Cursor 官方只给“下载脚本直接执行”的方式，agy 没查到官方安装说明，都不进名单，由行政带用户自己装。
执行：先查前提工具；按参数执行，不经过 shell；输入关着，要密码的会直接失败而不是卡住；
限时，超时整组关掉；要管理员权限就停下说明，不自动提权；装完再找一次；
结果写记录，不存原始输出；同一时间只装一个（npm、brew 都不喜欢同时装）。
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import tempfile
import threading
import time

from collab import blocks
from collab.blocks import BlockError
from collab.blocks_check import _mask
from collab.blocks_env import _clean_path, _which

INSTALLS = {
    "codex-npm": {"tool": "codex", "command": "codex", "label": "用 npm 装 Codex", "needs": "npm",
                  "args": ["npm", "install", "-g", "@openai/codex"], "minutes": 2,
                  "source": "https://www.npmjs.com/package/@openai/codex"},
    "codex-brew": {"tool": "codex", "command": "codex", "label": "用 Homebrew 装 Codex", "needs": "brew",
                   "args": ["brew", "install", "--cask", "codex"], "minutes": 3,
                   "source": "https://github.com/openai/codex"},
    "node-brew": {"tool": "node", "command": "node", "label": "用 Homebrew 装 Node（自带 npm）", "needs": "brew",
                  "args": ["brew", "install", "node"], "minutes": 5,
                  "source": "https://formulae.brew.sh/formula/node"},
}
DEFAULT_TIMEOUT = 600
_NEEDS_HOW = {
    "npm": "先装 Node（自带 npm）：跟行政说“帮我装 Node”",
    "brew": "Homebrew 要输电脑密码，只能在终端里装：行政会一步步带你做",
}
_NEEDS_NAME = {"npm": "npm（装 Node 时自带）", "brew": "Homebrew"}
_ADMIN_WORDS = ("eacces", "permission denied", "operation not permitted", "sudo", "administrator")

_lock = threading.Lock()
_busy = {"on": False}


def installing() -> bool:
    with _lock:
        return _busy["on"]


def _item(install_id) -> dict:
    if not isinstance(install_id, str) or install_id not in INSTALLS:
        raise BlockError(f"安装方式 {install_id!r} 不在核实名单里：名单外的工具要你按官网自己装，行政带着你做")
    return INSTALLS[install_id]


def options(tool) -> list:
    """某个工具在核实名单里的安装方式，给行政挑。"""
    return [describe(k) for k in sorted(INSTALLS) if INSTALLS[k]["tool"] == tool]


def describe(install_id) -> dict:
    """安装提议卡上要显示的：装什么、来源、原样的命令、前提、大概多久。"""
    item = _item(install_id)
    return {"id": install_id, "tool": item["tool"], "label": item["label"], "source": item["source"],
            "needs": item["needs"], "minutes": item["minutes"], "command": shlex.join(item["args"])}


def _kill_group(pid) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def _run(args, env, timeout, on_output):
    """输出写临时文件，边跑边读给 on_output；返回（退出码或 None 表示超时，全部输出）。"""
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env,
                                start_new_session=True)
        started, sent, pending = time.monotonic(), 0, ""
        code = None

        def pump(final=False):
            nonlocal sent, pending
            out.seek(sent)
            chunk = out.read()
            sent += len(chunk)
            pending += chunk.decode("utf-8", "replace")
            lines = pending.split("\n")
            pending = "" if final else lines.pop()
            if on_output:
                for line in lines:
                    if line.strip():
                        on_output(_mask(line))

        try:
            while True:
                try:
                    code = proc.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    pump()
                    if time.monotonic() - started > timeout:
                        _kill_group(proc.pid)
                        proc.wait()
                        code = None
                        break
        finally:
            _kill_group(proc.pid)
        pump(final=True)
        out.seek(0)
        return code, out.read().decode("utf-8", "replace")


def run_install(install_id, root=None, path=None, timeout=DEFAULT_TIMEOUT, on_output=None, env_extra=None,
                force=False, record=True) -> dict:
    """按核实名单装一个工具。返回 {ok, status, reason, how, seconds, found}。
    status：ok / already（已经装了，没跑）/ missing_prereq / needs_admin / timeout / fail。"""
    item = _item(install_id)
    extra = dict(env_extra or {})
    if any(not isinstance(k, str) or not isinstance(v, str) or k.upper() == "PATH" for k, v in extra.items()):
        raise BlockError("额外的环境设置只能是文字，而且不能改找命令的路径")
    lookup = _clean_path(os.environ.get("PATH", "") if path is None else path)
    with _lock:
        if _busy["on"]:
            raise BlockError("正在安装别的工具，等它装完再装")
        _busy["on"] = True
    try:
        started = time.time()
        if not force and _which(item["command"], lookup):
            result = {"ok": True, "status": "already", "found": True,
                      "reason": f"{item['tool']} 已经装好了，不用再装", "how": ""}
        elif not _which(item["needs"], lookup):
            result = {"ok": False, "status": "missing_prereq", "found": False,
                      "reason": f"要先有 {_NEEDS_NAME[item['needs']]} 才能这样装", "how": _NEEDS_HOW[item["needs"]]}
        else:
            args = [_which(item["needs"], lookup)] + item["args"][1:]
            env = dict(os.environ, PATH=lookup, **extra)
            code, output = _run(args, env, timeout, on_output)
            result = _judge(item, code, output, lookup)
        result["seconds"] = round(time.time() - started, 1)
    finally:
        with _lock:
            _busy["on"] = False
    if record:
        blocks.add_record(item["tool"], dict(type="install", install=install_id, **result), root=root)
    return result


def _judge(item, code, output, lookup) -> dict:
    text = (output or "").lower()
    if code is None:
        return {"ok": False, "status": "timeout", "found": False, "reason": "安装超时：规定时间内没装完",
                "how": "网络可能太慢，稍后再试一次"}
    if code != 0:
        if any(w in text for w in _ADMIN_WORDS):
            return {"ok": False, "status": "needs_admin", "found": False,
                    "reason": "要管理员权限才能装，超级助手不会自动提权",
                    "how": "换一种安装方式（比如用 Homebrew），或者让行政带你在终端里处理"}
        lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
        tail = _mask(lines[-1][:120]) if lines else "没有输出"
        return {"ok": False, "status": "fail", "found": False, "reason": "安装没成功",
                "how": f"稍后再试一次；还不行就跟行政说。安装程序最后说：{tail}"}
    found = bool(_which(item["command"], lookup))
    return {"ok": True, "status": "ok", "found": found, "reason": f"{item['tool']} 装好了",
            "how": "" if found else "装好了，但超级助手还没认到：关掉超级助手再重新打开，然后检测一次"}
