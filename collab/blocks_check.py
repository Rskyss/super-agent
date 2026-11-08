"""积木库：按接法卡检测（52 号单）。

CLI：在新建的临时文件夹里让工具干一件小事（建一个文件），以文件真的建出来为准，不听工具自己说。
没核实过的卡（命令或参数和预置不一样），用户看过完整命令、确认之前一律不跑（用户 2026-11-08 拍板）。
大模型 API：发一句话；当行政用的（need_tools）还要测会不会用工具。
结果写进记录：不存工具原始输出，不存密钥，邮箱和像密钥的字一律打码。同一块同时只跑一个检测。
"""
from __future__ import annotations

import functools
import re
import shlex
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from collab import blocks
from collab.agents import QuietTimeout, watched_runner
from collab.blocks import BlockError
from collab.blocks_env import _EMAIL, _clean_path, _which
from collab.providers import find as find_provider

DEFAULT_TIMEOUT = 180
API_TIMEOUT = 30
PING = "只回复两个字：收到"
TOOL_NAME = "report_ready"
TOOL_ASK = f"请调用 {TOOL_NAME} 工具，参数 ok 填 true。不要回复文字。"
_TOOL = {"type": "function", "function": {
    "name": TOOL_NAME, "description": "报告已经准备好",
    "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}}

_lock = threading.Lock()
_running = set()


def checking(card_id) -> bool:
    with _lock:
        return card_id in _running


class _OneAtATime:
    def __init__(self, card_id):
        self.card_id = card_id

    def __enter__(self):
        with _lock:
            if self.card_id in _running:
                raise BlockError(f"{self.card_id} 正在检测，稍等它做完")
            _running.add(self.card_id)

    def __exit__(self, *exc):
        with _lock:
            _running.discard(self.card_id)


def _mask(text: str, secret: str = "") -> str:
    """去掉邮箱、像密钥的字和这次用的密钥本身，记录才不会被拒、也不泄露。"""
    text = text or ""
    if secret:
        text = text.replace(secret, "[已隐藏]")
    text = _EMAIL.sub("[已隐藏]", text).replace(str(Path.home()), "~")
    return blocks._SECRET_TEXT.sub("[已隐藏]", text)


def _explain(output: str, command: str, name: str):
    """积木库自己的失败说法：(原因, 怎么办)。不提项目里的配置文件，改法一律跟行政说。"""
    raw = output or ""
    text = raw.lower()
    if raw.startswith("超时"):
        return "超时：规定时间内没做完", "网络或工具可能卡住了，稍后再检测一次"
    if raw.startswith("看门人"):
        return "长时间没有任何动静，被停下了", "稍后再检测一次"
    if raw.startswith("运行不了") or "command not found" in text:
        return "工具运行不起来", f"{name} 可能没装好，跟行政说“帮我重新装 {name}”"
    if any(k in text for k in ("not logged", "please log in", "please login", "unauthorized",
                               "not authenticated", "authentication failed", "please sign in", "codex login")):
        return "没登录", f"在终端里运行一次 {command}，按提示登录后再检测一次"
    if any(k in text for k in ("quota", "rate limit", "usage limit", "insufficient", "out of credit")) \
            or re.search(r"\b429\b", text):
        return "额度用完或被限流", "等额度恢复后再检测一次"
    if "model" in text and any(k in text for k in ("not found", "invalid", "unknown", "does not exist",
                                                   "not supported")):
        return "模型名不对", "跟行政说换一个这家支持的模型名"
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    tail = lines[-1][:120] if lines else "没有输出"
    return "命令跑完了，但没建出文件", f"看看这家工具在自动模式下能不能写文件。它最后说：{tail}"


def _remove(folder: Path) -> None:
    """删检测用的临时文件夹；工具留下打不开的子文件夹，先把权限改回来再删。"""
    shutil.rmtree(folder, ignore_errors=True)
    if folder.exists():
        for dirpath, dirnames, _ in os.walk(folder):
            for d in dirnames:
                target = os.path.join(dirpath, d)
                if os.path.islink(target):
                    continue  # 不顺着链接去改外面文件夹的权限
                try:
                    os.chmod(target, 0o700)
                except OSError:
                    pass
        shutil.rmtree(folder, ignore_errors=True)


def _probe_args(card, lookup):
    """检测要跑的参数和找到的程序：preview 和 check_cli 用同一份，比对的就是要跑的。"""
    args = blocks.build_args(card, probe_prompt(card["done_when"]["file"]))
    found = _which(card["command"], lookup)
    if found:
        args[0] = found
    return args, found


def _shown(args) -> str:
    return " ".join(shlex.quote(a) for a in args)


def preview(card, path=None) -> str:
    """用户确认前看到的完整检测命令：带实际要运行的完整路径，交代写出原文（就是检测时真正交给工具的话）。
    用户确认的就是这一串；check_cli 跑之前重算一遍，对不上就不跑。"""
    card = blocks.validate_card(card)
    args, _ = _probe_args(card, os.environ.get("PATH", "") if path is None else path)
    return _shown(args)


def probe_prompt(file_name: str) -> str:
    return f"在当前文件夹里新建一个文件 {file_name}，内容只写 OK。只做这一件事，不要做别的。"


def _save(card_id, result, root, record):
    if record:
        blocks.add_record(card_id, dict(type="check", **result), root=root)
    return result


def check_cli(card, root=None, approved=None, runner=None, timeout=DEFAULT_TIMEOUT, path=None,
              terminal_path=None, record=True) -> dict:
    """检测一张 CLI 接法卡。返回 {ok, status(ok/missing/fail), reason, how, seconds, verified}。
    没核实过的卡，approved 必须是用户确认时看到的那条完整命令（preview 的结果），
    和现在要跑的对不上（卡改过、换了程序）就抛 BlockError，什么都不跑。"""
    card = blocks.validate_card(card)
    if card["kind"] != "cli":
        raise BlockError("这是大模型 API，不是 CLI，要用 API 的检测")
    verified = blocks.is_verified(card)
    lookup = os.environ.get("PATH", "") if path is None else path
    args, found = _probe_args(card, lookup)
    if not verified and (not isinstance(approved, str) or approved != _shown(args)):
        raise BlockError(f"{card['name']} 的接法没核实过：要把完整命令给用户看，用户确认的必须就是现在要跑的这一条")
    with _OneAtATime(card["id"]):
        if not found:
            if terminal_path and _which(card["command"], terminal_path):
                missing = {"reason": f"{card['name']} 装好了，但超级助手还没认到",
                           "how": "关掉超级助手再重新打开，然后再检测一次"}
            else:
                missing = {"reason": f"没装：电脑上找不到 {card['command']} 这个命令",
                           "how": f"先装好 {card['name']} 并登录，再检测一次"}
            return _save(card["id"], dict(ok=False, status="missing", verified=verified, seconds=0, **missing),
                         root, record)
        done_file = card["done_when"]["file"]
        run = runner or functools.partial(watched_runner, poll=0.5, quiet_limit=timeout + 5)
        folder = Path(tempfile.mkdtemp(prefix="积木库检测-"))
        started = time.time()
        output, ok, code = "", False, None
        try:
            code, output = run(args, folder, timeout)
            ok = code == 0 and (folder / done_file).is_file()
        except subprocess.TimeoutExpired:
            output = "超时"
        except QuietTimeout as exc:
            output = f"看门人：{exc}"
        except OSError as exc:
            output = f"运行不了：{exc}"
        finally:
            _remove(folder)
        seconds = round(time.time() - started, 1)
        if ok:
            result = {"ok": True, "status": "ok", "reason": f"通了 · {seconds:g} 秒", "how": ""}
        else:
            # 工具说成功了却没建文件：不去猜输出里的字眼，免得把“已登录”之类的话当成原因
            reason, how = (_explain("", card["command"], card["name"]) if code == 0
                           else _explain(output, card["command"], card["name"]))
            result = {"ok": False, "status": "fail", "reason": _mask(reason), "how": _mask(how)}
        result.update(seconds=seconds, verified=verified)
        return _save(card["id"], result, root, record)


class _Unreadable(Exception):
    pass


def _default_client(base_url, model, api_key, provider=""):
    """行政已有的模型连接，复用它的报错说法。关不关“思考”跟着服务商走：不认识的服务商不关，免得它不认这个参数。"""
    from desktop.clerk.client import ModelClient
    known = find_provider(provider) if provider else None
    thinking_off = bool(known and known.get("thinking_off"))
    return ModelClient(base_url=base_url, model=model, api_key=api_key, timeout=API_TIMEOUT,
                       thinking_off=thinking_off)


def check_api(card, api_key, root=None, need_tools=False, client_factory=None, record=True) -> dict:
    """检测一张大模型 API 接法卡：先发一句话；need_tools 时再测会不会用工具。
    返回 {ok, status(ok/fail/no_tools), reason, how, seconds, tools}。密钥只在这次调用里用，不保存。"""
    card = blocks.validate_card(card)
    if card["kind"] != "llm_api":
        raise BlockError("这是 CLI，不是大模型 API，要用 CLI 的检测")
    if not isinstance(api_key, str) or not api_key.strip():
        raise BlockError("还没填密钥：先在密钥框里填好再检测")
    key = api_key.strip()
    with _OneAtATime(card["id"]):
        started = time.time()
        tools = None
        try:
            if client_factory:
                client = client_factory(card["base_url"], card["model"], key)
            else:
                client = _default_client(card["base_url"], card["model"], key, card["provider"])
            reply = client.chat([{"role": "user", "content": PING}])
            if not isinstance(reply, dict):
                result = {"ok": False, "status": "fail", "reason": "模型回的内容看不懂",
                          "how": "检查地址是不是这家的对话接口，再检测一次"}
            elif not (reply.get("text") or "").strip():
                result = {"ok": False, "status": "fail", "reason": "模型没有回话",
                          "how": "检查模型名对不对，或者换一个模型再检测"}
            elif need_tools:
                answer = client.chat([{"role": "user", "content": TOOL_ASK}], tools=[_TOOL])
                if not isinstance(answer, dict):
                    raise _Unreadable()
                calls = answer.get("tool_calls")
                tools = any(isinstance(c, dict) and c.get("name") == TOOL_NAME for c in calls or [])
                result = ({"ok": True, "status": "ok", "reason": "通了，也会用工具", "how": ""} if tools else
                          {"ok": False, "status": "no_tools", "reason": "这个模型不会用工具，当不了行政",
                           "how": "换一个支持工具调用的模型（例如各家的主力对话模型）"})
            else:
                result = {"ok": True, "status": "ok", "reason": "通了", "how": ""}
        except _Unreadable:
            result = {"ok": False, "status": "fail", "reason": "模型回的内容看不懂",
                      "how": "检查地址是不是这家的对话接口，再检测一次"}
        except Exception as exc:  # noqa: BLE001 — 行政的模型连接报错已是中文大白话，其余也不能让检测崩
            message = str(exc) if getattr(exc, "__module__", "").startswith("desktop.clerk") else "连接出错"
            if "代码 400" in message:  # 400 多半是模型名或参数不对，等多久都不会好
                message = "模型名或参数不对（服务商返回代码 400）：跟行政说换一个这家支持的模型名"
            result = {"ok": False, "status": "fail", "reason": _mask(message, key) or "连接出错",
                      "how": "按提示改好后再检测一次"}
        result["reason"] = _mask(result["reason"], key)
        result["how"] = _mask(result["how"], key)
        result.update(seconds=round(time.time() - started, 1), tools=tools)
        return _save(card["id"], result, root, record)
