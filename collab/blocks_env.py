"""积木库：环境体检（51 号单）。

只看不跑任务：查前提工具（Node、npm、Homebrew、git）和各 CLI 的版本、登录。
超级助手自己的找命令路径和终端的路径各查一遍：终端里有、超级助手这边没有的，
多半是刚装好、超级助手还没重新打开，标成“找不到”。
每项限时、并行跑；工具的原始输出不保存（登录状态里常带邮箱），只留版本那一行。
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from collab import blocks

PREREQS = (
    {"name": "node", "label": "Node", "command": "node", "args": ["--version"], "why": "用 npm 装 Codex 要用"},
    {"name": "npm", "label": "npm", "command": "npm", "args": ["--version"], "why": "用 npm 装 Codex 要用"},
    {"name": "brew", "label": "Homebrew", "command": "brew", "args": ["--version"],
     "why": "用 brew 装 Codex、Node 要用"},
    {"name": "git", "label": "git", "command": "git", "args": ["--version"], "why": "很多工具和项目窗口要用"},
)
TOOL_WHY = "接进基础层的工具"
VERSION_LIMIT = 80


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_VERSIONISH = re.compile(r"\d+\.\d+")


def _kill_group(pid) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def run_command(args, path, timeout):
    """按参数列表运行（不经过 shell），返回（退出码，标准输出和错误输出合在一起的文字）。超时抛 TimeoutExpired。

    输出写进临时文件而不是管道：工具在后台留下的小程序占着管道时，不用干等到限时。
    子程序单独成组，结束（或超时）时整组关掉，不留后台小程序。"""
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                env=dict(os.environ, PATH=path or ""), start_new_session=True)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(proc.pid)
            proc.wait()
            raise
        finally:
            _kill_group(proc.pid)
        out.seek(0)
        return code, out.read().decode("utf-8", "replace")


def _clean_path(path) -> str:
    """只留完整路径：空项和相对目录都会按当前文件夹算，可能跑到冒名程序。"""
    return os.pathsep.join(p for p in (path or "").split(os.pathsep) if p and os.path.isabs(p))


def _which(command, path):
    found = shutil.which(command, path=_clean_path(path)) if path else None
    return found if found and os.path.isabs(found) else None


def _version_line(text: str) -> str:
    """挑像版本号的那一行（警告、提示常在前面），邮箱一律去掉。"""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    pick = next((l for l in lines if _VERSIONISH.search(l) and "@" not in l), None) \
        or next((l for l in lines if _VERSIONISH.search(l)), None) or (lines[0] if lines else "")
    return blocks._SECRET_TEXT.sub("[已隐藏]", _EMAIL.sub("[已隐藏]", pick))[:VERSION_LIMIT]


def _targets(root):
    """要查的工具：先是预置的三家，再是基础层里另外接的 CLI；同编号以基础层里的为准。"""
    saved = {c["id"]: c for c in blocks.list_cards(root) if "problem" not in c and c.get("kind") == "cli"}
    tools = []
    for name in blocks.BUILTIN:
        tools.append(saved.pop(name, None) or blocks.builtin_card(name))
    tools.extend(saved[k] for k in sorted(saved))
    items = [dict(p, kind="prereq", login_check=None) for p in PREREQS]
    for card in tools:
        items.append({"name": card["id"], "label": card["name"], "command": card["command"],
                      "args": list(card["version_args"]), "why": TOOL_WHY, "kind": "tool",
                      "login_check": card.get("login_check")})
    return items


def _check(target, app_path, terminal_path, runner, timeout) -> dict:
    found = _which(target["command"], app_path)
    status, use_path = ("有", _clean_path(app_path)) if found else ("没有", None)
    if not found and terminal_path:
        found = _which(target["command"], terminal_path)
        if found:
            status, use_path = "找不到", _clean_path(terminal_path)
    item = {"name": target["name"], "label": target["label"], "kind": target["kind"], "status": status,
            "path": found or "", "version": "", "login": None, "why": target["why"], "note": "", "fix": ""}
    if found:
        try:
            _, out = runner([found] + target["args"], use_path, timeout)
            item["version"] = _version_line(out)
        except subprocess.TimeoutExpired:
            item["note"] = "查版本超时"
        except (OSError, ValueError) as exc:
            item["note"] = f"查版本出错：{exc}"
        if target["kind"] == "tool":
            item["login"] = _login(target["login_check"], found, use_path, runner, timeout)
    item["fix"] = _fix(item)
    return item


def _login(check, exe, path, runner, timeout) -> str:
    if not check:
        return "查不了"
    try:
        code, out = runner([exe] + list(check["args"]), path, timeout)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return "查不出"
    return "已登录" if code == 0 and any(word in out for word in check["ok_words"]) else "没登录"


def _fix(item) -> str:
    label = item["label"]
    if item["status"] == "没有":
        return f"行政可以带你装 {label}" if item["kind"] == "prereq" else f"跟行政说“帮我装 {label}”"
    if item["status"] == "找不到":
        return "终端里已经装好，但超级助手还没认到：关掉超级助手再重新打开"
    if item["login"] == "没登录":
        return f"{label} 还没登录：在终端里登录一次，行政会告诉你命令"
    return ""


def scan(root=None, app_path=None, terminal_path=None, runner=None, timeout=5.0, record=True) -> dict:
    """体检一次。app_path 默认是本进程的 PATH；terminal_path 由调用方传入（读不到就是 None）。"""
    app_path = os.environ.get("PATH", "") if app_path is None else app_path
    runner = runner or run_command
    targets = _targets(root)
    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
        items = list(pool.map(lambda t: _check(t, app_path, terminal_path, runner, timeout), targets))
    result = {"time": datetime.now().isoformat(timespec="seconds"), "terminal_path_read": bool(terminal_path),
              "items": items}
    if record:
        blocks.add_record("env", {"type": "env", "items": [
            {k: i[k] for k in ("name", "status", "version", "login")} for i in items]}, root=root)
    return result
