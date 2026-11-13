"""积木库：按接法说明检测（52 号单）。

CLI：在新建的临时文件夹里让工具干一件小事（建一个文件），以文件真的建出来为准，不听工具自己说。
没核实过的卡（命令或参数和预置不一样），用户看过完整命令、确认之前一律不跑（用户 2026-11-08 拍板）。
大模型 API：发一句话；当管家用的（need_tools）还要测会不会用工具。
结果写进记录：不存工具原始输出，不存密钥，邮箱和像密钥的字一律打码。同一块同时只跑一个检测。
"""
from __future__ import annotations

import contextlib
import difflib
import json
import urllib.request
import urllib.error
import functools
import re
import shlex
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from collab import blocks, blocks_web
from collab.agents import QuietTimeout, watched_runner
from collab.blocks import BlockError
from collab.blocks_env import _clean_path, _which, mask_emails
from collab.blocks_env import remove_folder as _remove
from collab.config import MODEL_OPTIONS
from collab.providers import find as find_provider

DEFAULT_TIMEOUT = 180
TOOL_TIMEOUT = 60  # 普通工具的检测命令只查不改，一分钟足够
SEARCH_PROBE = "超级助手 连通检测"
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
    text = mask_emails(text).replace(str(Path.home()), "~")
    return blocks._SECRET_TEXT.sub("[已隐藏]", text)


def _mask_cut(text: str, limit: int) -> str:
    """先打码再截断：截断在前会把半截密钥漏出来。多打码一截；截过的，最后那个不完整的词也去掉
    （打码会让字变少，原来切在边上的半截密钥会被拉进显示范围）。截过的结尾加“…”。"""
    text = text or ""
    cut = len(text) > limit + 200
    shown = _mask(text[:limit + 200])
    if len(shown) > limit - 1:
        shown, cut = shown[:limit - 1], True
    if not cut:
        return shown
    # 密钥都是英文字符：只去掉末尾那串英文字符（汉字不动），再标一个省略号说明截过
    return re.sub(r"[!-~]+$", "", shown).rstrip() + "…"


QUOTA_REASON = "额度用完或被限流"
# 能放进基础层的“额度不足”只认很明确的说法（第四轮复查：单独的 insufficient、单独的 429 会把参数写错、
# 权限不够、磁盘配额满这类真故障放进去）；宽的那组只用来写失败原因
_QUOTA_CLEAR = re.compile(
    r"credits?\s+balance|balance\s+is\s+too\s+low|out\s+of\s+credits?|(?<!disk\s)quota\s+exceeded"
    r"|exceeded\s+(?:your\s+)?(?:current\s+)?quota|(?<![-\w])usage\s+limit|(?<![-\w])rate\s+limit|too\s+many\s+requests"
    r"|resource_exhausted[^\n]{0,40}(?:\b429\b|quota)", re.IGNORECASE)


def is_quota(output: str) -> bool:
    """工具的输出是不是很明确地在说额度、余额不够或被限流"""
    return bool(_QUOTA_CLEAR.search(output or ""))


def _explain(output: str, command: str, name: str):
    """积木库自己的失败说法：(原因, 怎么办)。不提项目里的配置文件，改法一律跟管家说。"""
    raw = output or ""
    text = raw.lower()
    if raw.startswith("超时"):
        return "超时：规定时间内没做完", "网络或工具可能卡住了，稍后再检测一次"
    if raw.startswith("看门人"):
        return "长时间没有任何动静，被停下了", "稍后再检测一次"
    if raw.startswith("运行不了") or "command not found" in text:
        return "工具运行不起来", f"{name} 可能没装好，跟管家说“帮我重新装 {name}”"
    if any(k in text for k in ("not logged", "please log in", "please login", "unauthorized",
                               "not authenticated", "authentication failed", "please sign in", "codex login")):
        return "没登录", f"在终端里运行一次 {command}，按提示登录后再检测一次"
    if any(k in text for k in ("quota", "rate limit", "usage limit", "insufficient", "out of credit",
                               "balance is too low", "resource_exhausted", "resource exhausted")) \
            or re.search(r"\b429\b", text):
        return QUOTA_REASON, "等额度恢复后再检测一次"
    if "model" in text and any(k in text for k in ("not found", "invalid", "unknown", "does not exist",
                                                   "not supported")):
        return "模型名不对", "跟管家说换一个这家支持的模型名"
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    tail = lines[-1][:120] if lines else "没有输出"
    return "命令跑完了，但没建出文件", f"看看这家工具在自动模式下能不能写文件。它最后说：{tail}"


def _probe_args(card, lookup):
    """检测要跑的参数和找到的程序：preview 和 check_cli 用同一份，比对的就是要跑的。"""
    if card["role"] == "tool":
        args = blocks.probe_args(card)
    else:
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


QUESTION_FILE = "题目.txt"


def probe_prompt(file_name: str) -> str:
    """一件小活（第四轮 55，和团队检测同一件）：读文件、算数、写文件、跑命令都要真做到。"""
    return (f"当前文件夹里有一个文件 {QUESTION_FILE}，里面是一个数。读出这个数，加 1，写进新文件 {file_name} 的第一行；"
            f"再在终端运行 ls 命令，把它列出的文件个数（一个数字）写在第二行。只做这件事，不要做别的。")


def _seed(folder) -> int:
    import random
    number = random.randint(11, 88)
    (Path(folder) / QUESTION_FILE).write_text(f"{number}\n", encoding="utf-8")
    return number


def _answer_problem(folder, file_name: str, number: int):
    """检查小活做对没有：对了返回 None，不对返回 (原因, 怎么办)。"""
    try:
        text = (Path(folder) / file_name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None   # 没建出文件的照原来的说法
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or lines[0] != str(number + 1):
        return ("能建文件，但读文件、算数没做对", "这个模型做不了要读资料的活；在名单里换一个模型再检测")
    if len(lines) < 2 or not lines[1].isdigit():
        return ("能读写文件，但没跑命令（或跑了没把结果写上）", "这家工具在自动模式下可能不让跑命令；看看它的权限设置")
    return None


def _save(card_id, result, root, record):
    if record:
        blocks.add_record(card_id, dict(type="check", **result), root=root)
    return result


def _secret_env(card, keys) -> dict:
    """普通工具要的密钥：按卡里写的名字，从钥匙串交来的那份里取；缺一把就不跑。"""
    env = {}
    for item in card.get("secrets") or []:
        value = (keys or {}).get(blocks.secret_account(card["id"], item["env"]), "")
        if not isinstance(value, str) or not value.strip():
            raise BlockError(f"还没填 {card['name']} 的「{item['label']}」：先在密钥框里填好再检测")
        env[item["env"]] = value.strip()
    return env


def _run_tool(args, folder, timeout, env):
    """输出写临时文件只读最后 64KB；超时把整组（包括它另开的进程）关掉（复查第 10 条）。"""
    import signal
    with tempfile.TemporaryFile() as out:
        proc = subprocess.Popen(args, cwd=folder, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            code = None
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            proc.wait()
        if code is None:
            raise subprocess.TimeoutExpired(args, timeout)
        size = out.seek(0, os.SEEK_END)
        out.seek(max(0, size - 64 * 1024))
        return code, out.read().decode("utf-8", "replace")


def _mask_all(text, secrets) -> str:
    for value in secrets:
        if value:
            text = text.replace(value, "[已隐藏]")
    return _mask(text)


def _check_tool(card, root, args, found, verified, keys, runner, timeout, terminal_path, record):
    secret_env = _secret_env(card, keys)
    with _OneAtATime(card["id"]):
        if not found:
            how = ("关掉超级助手再重新打开，然后再检测一次" if terminal_path and _which(card["command"], terminal_path)
                   else f"先装好 {card['name']}，再检测一次")
            return _save(card["id"], dict(ok=False, status="missing", verified=verified, seconds=0,
                                          reason=f"电脑上找不到 {card['command']} 这个命令", how=how), root, record)
        folder = Path(tempfile.mkdtemp(prefix="积木库检测-"))
        env = {"PATH": os.path.dirname(found) + os.pathsep + _clean_path(os.environ.get("PATH", "")),
               "HOME": os.environ.get("HOME", ""), **secret_env}
        started = time.time()
        code, output = None, ""
        try:
            code, output = (runner or _run_tool)(args, folder, timeout or TOOL_TIMEOUT, env)
        except subprocess.TimeoutExpired:
            output = "超时"
        except OSError as exc:
            output = f"运行不了：{exc}"
        finally:
            _remove(folder)
        seconds = round(time.time() - started, 1)
        words = card.get("ok_words") or []
        if code == 0 and all(w in output for w in words):
            result = {"ok": True, "status": "ok", "reason": f"通了 · {seconds:g} 秒", "how": ""}
        elif code is None:
            result = {"ok": False, "status": "fail", "reason": "超时：规定时间内没跑完" if output == "超时"
                      else "工具运行不起来", "how": "稍后再检测一次；还不行就跟管家说"}
        else:
            lines = [ln.strip() for ln in (output or "").splitlines() if ln.strip()]
            # 先打码再截短：截断在前会把跨在截断处的半截密钥漏出来（复查第 1 条）
            tail = _mask_cut(_mask_all(lines[-1][:4000], secret_env.values()), 120) if lines else "没有输出"
            reason = "检测命令跑完了，但没看到该有的结果" if code == 0 else f"检测命令出错了（退出码 {code}）"
            result = {"ok": False, "status": "fail", "reason": reason,
                      "how": f"把这句告诉管家，让它改检测命令或换一把密钥。工具最后说：{tail}"}
        result["reason"] = _mask_all(result["reason"], secret_env.values())
        result["how"] = _mask_all(result["how"], secret_env.values())
        result.update(seconds=seconds, verified=verified)
        return _save(card["id"], result, root, record)


def check_search(card, key, root=None, record=True) -> dict:
    """联网搜索检测：真搜一次。额度用完、太频繁算“额度不足”（密钥是对的）。"""
    card = blocks.validate_card(card)
    if card["kind"] != "search":
        raise BlockError("这不是联网搜索")
    if not isinstance(key, str) or not key.strip():
        raise BlockError("还没填密钥：先在密钥框里填好再检测")
    with _OneAtATime(card["id"]):
        started = time.time()
        try:
            blocks_web.search(SEARCH_PROBE, key.strip(), 1)
            result = {"ok": True, "status": "ok", "reason": "通了", "how": ""}
        except blocks_web.SearchError as exc:
            quota = exc.status in (429, 432, 433)
            result = {"ok": False, "status": "quota" if quota else "fail", "reason": _mask(str(exc), key.strip()),
                      "how": "等额度恢复后再检测一次" if quota else "换一把密钥再检测"}
        except BlockError as exc:
            result = {"ok": False, "status": "fail", "reason": _mask(str(exc), key.strip()), "how": "稍后再检测一次"}
        result.update(seconds=round(time.time() - started, 1))
        return _save(card["id"], result, root, record)


def _is_preset_cli(card: dict) -> bool:
    return (card.get("kind") == "cli" and card.get("role") != "tool"
            and card.get("id") in blocks.BUILTIN
            and card.get("command") == blocks.BUILTIN[card["id"]]["command"])


def fetch_cli_models(card: dict, path=None) -> tuple[list[str], str]:
    """获取预置命令行工具的模型名单：agy/Cursor跑命令，Codex读本地缓存。失败退回内置名单。"""
    cid = card.get("id")
    builtin_models = [m["value"] for m in MODEL_OPTIONS.get(cid, [])]
    if cid == "codex":
        home_dir = Path(os.environ.get("HOME", "")) if os.environ.get("HOME") else Path.home()
        cache_file = home_dir / ".codex" / "models_cache.json"
        if not cache_file.is_file():
            return builtin_models, "builtin"
        try:
            raw = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return builtin_models, "builtin"
        items = raw.get("models") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            return builtin_models, "builtin"
        names, seen = [], set()
        for m in items:
            if not isinstance(m, dict) or m.get("visibility") != "list":
                continue
            slug = str(m.get("slug") or "").strip()
            if slug and slug not in seen and blocks._MODEL.fullmatch(slug):
                seen.add(slug)
                names.append(slug)
        if names:
            return names, "tool"
        return builtin_models, "builtin"

    lookup = os.environ.get("PATH", "") if path is None else path
    found = _which(card["command"], lookup)
    if not found:
        return builtin_models, "builtin"
    folder = Path(tempfile.mkdtemp(prefix="积木库-名单-"))
    try:
        args = [found, "models"]
        env = {"PATH": os.path.dirname(found) + os.pathsep + _clean_path(os.environ.get("PATH", "")),
               "HOME": os.environ.get("HOME", "")}
        proc = subprocess.run(args, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return builtin_models, "builtin"
        output = proc.stdout or ""
        lines = output.splitlines()
        names, seen = [], set()
        if cid == "agy":
            for line in lines:
                if not line.strip() or line.startswith("Fetching available models"):
                    continue
                parts = line.split("\t")
                name = parts[0].strip()
                if name and name not in seen and blocks._MODEL.fullmatch(name):
                    seen.add(name)
                    names.append(name)
        elif cid == "cursor":
            for line in lines:
                line_str = line.strip()
                if not line_str or line_str.startswith("Available models"):
                    continue
                if " - " in line_str:
                    name = line_str.split(" - ", 1)[0].strip()
                else:
                    name = line_str.split()[0].strip()
                if name and name not in seen and blocks._MODEL.fullmatch(name):
                    seen.add(name)
                    names.append(name)
        if names:
            return names, "tool"
        return builtin_models, "builtin"
    except Exception:
        return builtin_models, "builtin"
    finally:
        _remove(folder)


def check_cli(card, root=None, approved=None, runner=None, timeout=DEFAULT_TIMEOUT, path=None,
              terminal_path=None, record=True, keys=None, model=None) -> dict:
    """检测一张 CLI 接法说明。返回 {ok, status(ok/missing/fail), reason, how, seconds, verified}。
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
    if card["role"] == "tool":
        return _check_tool(card, root, args, found, verified, keys, runner,
                           None if timeout == DEFAULT_TIMEOUT else timeout, terminal_path, record)

    # 单个模型试用：必须在最近一次检测的名单里，且只写 model_check 记录
    if model is not None:
        last_check = blocks.last_record(card["id"], root, type="check")
        valid_models = (last_check or {}).get("models") or []
        if not valid_models or model not in valid_models:
            raise BlockError("这个模型不在这家的名单里，先检测一次拿名单")
        with _OneAtATime(card["id"]):
            if not found:
                missing = {"reason": f"没装：电脑上找不到 {card['command']} 这个命令",
                           "how": f"先装好 {card['name']} 并登录，再检测一次"}
                result = dict(ok=False, status="fail", verified=verified, seconds=0, model=model, **missing)
                if record:
                    return blocks.add_record(card["id"], dict(type="model_check", **result), root=root)
                return result
            done_file = card["done_when"]["file"]
            run = runner or functools.partial(watched_runner, poll=0.5, quiet_limit=timeout + 5)
            folder = Path(tempfile.mkdtemp(prefix="积木库检测-"))
            trial_args = blocks.build_args(card, probe_prompt(done_file), model=model)
            trial_args[0] = found
            started = time.time()
            output, ok, code, wrong = "", False, None, None
            number = _seed(folder)
            try:
                code, output = run(trial_args, folder, timeout)
                ok = code == 0 and (folder / done_file).is_file()
                wrong = _answer_problem(folder, done_file, number) if ok else None
                ok = ok and wrong is None
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
                reason, how = (wrong or _explain("", card["command"], card["name"])) if code == 0 \
                    else _explain(output, card["command"], card["name"])
                status = "quota" if reason == QUOTA_REASON and code != 0 and is_quota(output) else "fail"
                result = {"ok": False, "status": status, "reason": _mask(reason), "how": _mask(how)}
            result.update(model=model, seconds=seconds, verified=verified)
            if record:
                return blocks.add_record(card["id"], dict(type="model_check", **result), root=root)
            return result

    # 常规检测
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
        is_preset = _is_preset_cli(card)
        models, models_from = None, None
        if is_preset:
            models, models_from = fetch_cli_models(card, path=lookup)

        done_file = card["done_when"]["file"]
        run = runner or functools.partial(watched_runner, poll=0.5, quiet_limit=timeout + 5)
        folder = Path(tempfile.mkdtemp(prefix="积木库检测-"))
        started = time.time()
        output, ok, code, wrong = "", False, None, None
        number = _seed(folder)
        try:
            code, output = run(args, folder, timeout)
            ok = code == 0 and (folder / done_file).is_file()
            wrong = _answer_problem(folder, done_file, number) if ok else None
            ok = ok and wrong is None
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
            if is_preset:
                reason = f"通了 · {seconds:g} 秒，这家有 {len(models)} 个模型"
            else:
                reason = f"通了 · {seconds:g} 秒"
            result = {"ok": True, "status": "ok", "reason": reason, "how": ""}
        else:
            reason, how = (wrong or _explain("", card["command"], card["name"])) if code == 0 \
                else _explain(output, card["command"], card["name"])
            is_connected = False
            wording = ""
            if is_preset and code != 0 and reason == QUOTA_REASON and is_quota(output):
                is_connected = True
                wording = "额度不足"
            elif is_preset and card.get("id") in ("agy", "cursor") and models_from == "tool" and reason == "模型名不对":
                is_connected = True
                wording = f"没通，原因{reason}"

            if is_connected:
                status = "listed"
                reason_text = f"已接上：这家有 {len(models)} 个模型。默认的 {card.get('model', '')} {wording}"
                how_text = "可以在下面的名单里试试别的模型"
                result = {"ok": False, "status": "listed", "reason": _mask(reason_text), "how": _mask(how_text)}
            else:
                status = "quota" if reason == QUOTA_REASON and code != 0 and is_quota(output) else "fail"
                result = {"ok": False, "status": status, "reason": _mask(reason), "how": _mask(how)}
        result.update(seconds=seconds, verified=verified)
        if is_preset:
            result.update(models=models, models_from=models_from, model=card.get("model", ""))
        return _save(card["id"], result, root, record)


class _Unreadable(Exception):
    pass


def _default_client(base_url, model, api_key, provider=""):
    """管家已有的模型连接，复用它的报错说法。关不关“思考”跟着服务商走：不认识的服务商不关，免得它不认这个参数。"""
    from desktop.clerk.client import ModelClient
    known = find_provider(provider) if provider else None
    thinking_off = bool(known and known.get("thinking_off"))
    return ModelClient(base_url=base_url, model=model, api_key=api_key, timeout=API_TIMEOUT,
                       thinking_off=thinking_off)


class _StayHere(urllib.request.HTTPRedirectHandler):
    """不跟着跳转：密钥只发给填的那个地址。"""

    def redirect_request(self, *args, **kwargs):
        return None


_MODELS_OPENER = urllib.request.build_opener(_StayHere)
MODELS_TIMEOUT = 15      # 整个查询最多等这么久（不是每一小段）
MODELS_LIMIT = 400       # 最多收这么多个名字
_MODELS_SCAN = 5000      # 最多看平台回的前这么多条
_MODELS_BYTES = 2_000_000


def _open_models(request, timeout=None):
    return _MODELS_OPENER.open(request, timeout=timeout)


def _download_models(base: str, api_key: str, holder=None):
    """只管把平台回的东西读回来。出什么错都只说固定的话：错误原文里可能带着平台回显的密钥。
    holder 拿着这次的连接：查询到点时由 fetch_models 关掉它，读的那边就停了。"""
    request = urllib.request.Request(base + "/models", headers={"Authorization": "Bearer " + api_key})
    try:
        response = _open_models(request, timeout=MODELS_TIMEOUT)
        if holder is not None:
            holder["response"] = response
            if holder.get("late"):  # 已经到点了：不读了
                response.close()
                raise BlockError("这家平台回得太慢，没问到模型名单")
        with contextlib.closing(response):
            chunks, size = [], 0
            while size < _MODELS_BYTES:
                piece = response.read(65536)
                if not piece:
                    break
                chunks.append(piece)
                size += len(piece)
            return json.loads(b"".join(chunks)[:_MODELS_BYTES].decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise BlockError("密钥不对或已失效，问不到这家的模型名单") from None
        if exc.code == 403:  # 有的平台给聊天权限、不给看名单的权限：不能一口咬定密钥坏了（第二轮复查第 5 条）
            raise BlockError("这家没让这把密钥看模型名单（返回代码 403）") from None
        raise BlockError(f"这家没给模型名单（返回代码 {int(exc.code)}）") from None
    except BlockError:
        raise
    except Exception:  # noqa: BLE001 — 协议出错等各种意外，原文一律不往外带
        raise BlockError("没连上这家平台，问不到模型名单") from None


def fetch_models(base_url: str, api_key: str) -> list:
    """问这家平台它真有哪些模型（大模型平台通用的“模型名单”接口，只读、不花额度）。
    管家凭记忆写的模型名会错（用户真实接腾讯云混元时写了个不存在的名字，2026-11-10），名字一律以这里为准。
    整个查询最多等 MODELS_TIMEOUT 秒：放到另一条线上去问，到点就不等了（连得慢、回得慢、慢慢吐都算在内）。
    名单最多 MODELS_LIMIT 个：正好这么多时，可能还有没拿到的。"""
    base = str(base_url or "").strip().rstrip("/")
    if not base.lower().startswith("https://"):
        raise BlockError("地址要以 https:// 开头，才能问它有哪些模型")
    secret = str(api_key or "").strip()
    box = {}

    def work():
        try:
            box["data"] = _download_models(base, secret, box)
        except BlockError as exc:
            box["error"] = exc
        except Exception:  # noqa: BLE001
            box["error"] = BlockError("没连上这家平台，问不到模型名单")

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(MODELS_TIMEOUT)
    if worker.is_alive():  # 到点了：关掉连接，后台那次读取跟着停，不越积越多（第三轮复查第 6 条）
        box["late"] = True
        try:
            if box.get("response") is not None:
                box["response"].close()
        except Exception:  # noqa: BLE001
            pass
        raise BlockError("这家平台回得太慢，没问到模型名单")
    if "error" in box:
        raise box["error"]
    data = box.get("data")
    items = data.get("data") if isinstance(data, dict) else None
    names, seen = [], set()
    for item in (items if isinstance(items, list) else [])[:_MODELS_SCAN]:
        name = item.get("id") if isinstance(item, dict) else None
        if not isinstance(name, str) or name in seen or not blocks._MODEL.fullmatch(name):
            continue
        # 平台回的东西不可信：名字里带着这次的密钥、或者长得像密钥的，一律不要（不能让密钥跟着名单传到管家的模型那里）
        if (secret and secret in name) or blocks._SECRET_TEXT.search(name) or mask_emails(name) != name:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= MODELS_LIMIT:
            break
    if not names:
        raise BlockError("这家没给模型名单")
    return names


def _real_models_hint(card: dict, key: str) -> str:
    """模型名不对时：把这家真有的名字列几个出来（最像的在前）。问不到就不说。"""
    try:
        names = fetch_models(card["base_url"], key)
    except BlockError:
        return ""
    wanted = str(card.get("model") or "")
    if wanted in names:  # 名字是对的：毛病在别处，不能叫用户反复换模型
        return f"这家有「{wanted}」这个模型，但它不接受这次的请求（服务商返回代码 400），多半是这个模型的参数或用法和普通对话不一样。跟管家说换一个通用的对话模型试试"
    close = difflib.get_close_matches(wanted, names, n=6, cutoff=0.4)
    shown = "、".join(close + [n for n in names if n not in close][:10 - len(close)])
    if len(names) >= MODELS_LIMIT:  # 名单没拿全：不能一口咬定没有
        return (f"在拿到的前 {len(names)} 个模型名里没找到「{wanted}」（可能没拿全），也可能是参数不对。"
                f"拿到的有：{shown}。跟管家核对一下名字")
    return f"这家没有叫「{wanted}」的模型。它有 {len(names)} 个模型，比如：{shown}。跟管家说换成名单里有的"


PROBE_TRIES = 6  # 整家接入时最多挑几个模型试通
# 不会聊天的：向量、画图、视频、3D、语音、翻译、审核等（火山引擎真实名单里的 seedream、seedance、seed3d，2026-11-11）。
# 按整段词认（第二轮复查第 4 条：swan-chat 不能因为带 wan 被挡掉）；名字里有 chat、instruct 这类词的算聊天
_NOT_CHAT_WORD = re.compile(r"embed\w*|rerank\w*|image|images|img|video|speech|audio|tts|asr|ocr|voice|whisper|wan[\d.]*|"
                            r"sd|sdxl|flux|seedream|seedance|\w*3d|t2v|i2v|t2i|paraformer|cosyvoice|sambert|"
                            r"translat\w*|moderation|gen", re.I)
_CHAT_WORD = re.compile(r"chat|instruct", re.I)
_FAMILY = re.compile(r"[a-z]+", re.I)


def _is_chat(name: str) -> bool:
    """名字里明说 chat、instruct 的算聊天；否则带画图、视频这类词的不算（qwen-image-plus 不能因为带 plus 算聊天）"""
    words = [w for w in re.split(r"[-_/.:]+", name) if w]
    if any(_CHAT_WORD.fullmatch(w) for w in words):
        return True
    return not any(_NOT_CHAT_WORD.fullmatch(w) for w in words)


def _probe_order(card: dict, names: list) -> list:
    """挑哪些模型来试通：卡上写的 → 这家自己的聊天模型（名单里最多的那个系列，如火山的豆包、腾讯的混元；
    预置的自家模型排前面）→ 预置的代卖模型 → 别家的聊天模型。平台代卖的别家模型常要单独开通。不会聊天的不试。"""
    known = find_provider(card.get("provider") or "") or {}
    preset = known.get("models") or []
    chat = [n for n in names if _is_chat(n)] or list(names)  # 过滤完一个不剩：名单里的照样试（第三轮复查第 3 条）
    families = {}
    for n in chat:
        fam = _FAMILY.match(n.split("/")[-1]) or _FAMILY.match(n)
        families.setdefault(fam.group(0).lower() if fam else "", []).append(n)
    if known.get("own"):  # 认得的服务商：自家系列写死在服务商清单里，不靠猜（第三轮复查第 2 条）
        own = [n for f in known["own"] for n in families.get(f, [])]
    else:  # 不认得的：名单里最多的那个系列多半是自家的
        own = max(families.values(), key=len) if families else []
    model = card.get("model") or ""
    first = [model] if model and (not names or _is_chat(model)) else []  # 卡上的画图模型也不试；没名单时只有它
    order = first + [n for n in preset if n in own] + own + [n for n in preset if n in chat] + chat
    seen, picked = set(), []
    for name in order:
        if name not in seen:
            seen.add(name)
            picked.append(name)
    if len(picked) > PROBE_TRIES:  # 自家的会占满：留两个位置给别家的，别家没有就还用自家的补上
        head = picked[:PROBE_TRIES - 2]
        others = [n for n in picked if n not in head and n not in own][:2]
        rest = [n for n in picked if n not in head and n not in others]
        picked = head + others + rest
    return picked[:PROBE_TRIES]


def _words(exc, key: str) -> str:
    """平台的原话：先去掉这次的密钥、邮箱和像密钥的字，再截短（截过的去掉最后那串英文，免得留半截）"""
    text = " ".join(str(getattr(exc, "detail", "") or "").split())
    return _mask_cut(text.replace(key, "[已隐藏]") if key else text, 160) if text else ""


def _ping(card, model, key, client_factory, need_tools=False):
    """拿一个模型发一句话。返回 (result, tools, retry)：retry 是说“这个模型不行，换一个可能行”。"""
    tools = None
    try:
        if client_factory:
            client = client_factory(card["base_url"], model, key)
        else:
            client = _default_client(card["base_url"], model, key, card["provider"])
        reply = client.chat([{"role": "user", "content": PING}])
        if not isinstance(reply, dict):
            return ({"ok": False, "status": "fail", "reason": "模型回的内容看不懂",
                     "how": "检查地址是不是这家的对话接口，再检测一次"}, tools, False)
        if not (reply.get("text") or "").strip():
            return ({"ok": False, "status": "fail", "reason": "模型没有回话",
                     "how": "检查模型名对不对，或者换一个模型再检测"}, tools, True)
        if need_tools:
            answer = client.chat([{"role": "user", "content": TOOL_ASK}], tools=[_TOOL])
            if not isinstance(answer, dict):
                raise _Unreadable()
            calls = answer.get("tool_calls")
            tools = any(isinstance(c, dict) and c.get("name") == TOOL_NAME for c in calls or [])
            return (({"ok": True, "status": "ok", "reason": "通了，也会用工具", "how": ""} if tools else
                     {"ok": False, "status": "no_tools", "reason": "这个模型不会用工具，当不了积木库管家",
                      "how": "换一个支持工具调用的模型（例如各家的主力对话模型）"}), tools, False)
        return {"ok": True, "status": "ok", "reason": "通了", "how": ""}, tools, False
    except _Unreadable:
        return ({"ok": False, "status": "fail", "reason": "模型回的内容看不懂",
                 "how": "检查地址是不是这家的对话接口，再检测一次"}, tools, False)
    except Exception as exc:  # noqa: BLE001 — 管家的模型连接报错已是中文大白话，其余也不能让检测崩
        message = str(exc) if getattr(exc, "__module__", "").startswith("desktop.clerk") else "连接出错"
        code = getattr(exc, "code", None)
        # 多半是这个模型不行（名字不对、没开通、没权限），换一个可能就行。403 常是“这个模型没开通”，不一定是密钥坏了
        wrong = "代码 400" in message or "没有这个模型" in message or code == 403
        quota = "额度用完" in message  # desktop.clerk.client 对 429 的说法：密钥对、能连上，只是没额度
        return ({"ok": False, "status": "quota" if quota else "fail", "reason": message or "连接出错",
                 "how": "等额度恢复后再检测一次" if quota else "按提示改好后再检测一次", "wrong_model": wrong,
                 "words": _words(exc, key), "bad_key": "密钥" in message and code != 403, "code": code}, tools, wrong)


def _check_whole(card, key, client_factory, models_fetcher) -> dict:
    """整家接入（用户 2026-11-10 定）：用密钥拿这家的模型名单，再自动挑几个试通。用哪个模型是能力层的事。
    拿到名单就算接上（用户 2026-11-11 选 A）：试的都没通只标黄（status=listed），写出平台的原话，不拦。"""
    names = []
    try:
        if models_fetcher:
            names = list(models_fetcher(card["base_url"], key))
        elif client_factory:  # 用假连接走查、测试时不碰真网络：当作这家只有预置的那几个
            names = list((find_provider(card["provider"]) or {}).get("models") or [])
            if not names:
                raise BlockError("这家没给模型名单")
        else:
            names = fetch_models(card["base_url"], key)
    except BlockError as exc:
        no_list = str(exc)
        if "密钥不对" in no_list:
            return {"ok": False, "status": "fail", "reason": no_list, "how": "换一把这家平台的 API 密钥再检测"}
        if not card.get("model"):
            return {"ok": False, "status": "fail", "reason": f"问不到这家的模型名单，没法自动试通（{no_list}）",
                    "how": "跟管家说一个这家的模型名，只用来试通"}
    tried, last = [], None
    for model in _probe_order(card, names):
        result, _, retry = _ping(card, model, key, client_factory)
        tried.append(model)
        words, bad_key = result.pop("words", "").rstrip(".。 "), result.pop("bad_key", False)
        result.pop("code", None)
        result.pop("wrong_model", None)
        if result["ok"] or result["status"] == "quota":
            if result["ok"] and names:
                result["reason"] = (f"通了，拿到 {len(names)} 个模型名（可能没拿全）" if len(names) >= MODELS_LIMIT
                                    else f"通了，这家有 {len(names)} 个模型")
            return dict(result, models=names, model=model)
        last = dict(result, words=words)
        if bad_key or not names:  # 名单要密钥才给，聊天却说密钥不对：照实说不通；没名单的只有这一个可试
            how = ("换一把这家平台的 API 密钥再检测" if bad_key else
                   "跟管家说换一个这家支持的模型名，只用来试通" if retry else result["how"])
            return dict(result, models=names, model=model, how=how)
        if not retry:
            break
    if not names:
        return {"ok": False, "status": "fail", "models": names, "reason": "这家没有可以拿来试通的模型",
                "how": "跟管家说一个这家的模型名，只用来试通"}
    words, local = (last or {}).get("words") or "", (last or {}).get("reason") or ""
    said = f"，平台说：{words}" if words else (f"（{local}）" if local else "")  # 没拿到平台原话就不说“平台说”
    got = (f"拿到 {len(names)} 个模型名（可能没拿全）" if len(names) >= MODELS_LIMIT
           else f"拿到了这家的模型名单（{len(names)} 个）")
    return {"ok": False, "status": "listed", "models": names,
            "reason": f"已接上：{got}。顺手试了 {len(tried)} 个模型都没通{said}",
            "how": "可以先放进基础层；具体哪个模型能用（有的要先在平台上开通），以后选模型时再验"}


def check_api(card, api_key, root=None, need_tools=False, client_factory=None, record=True, whole=False,
              models_fetcher=None) -> dict:
    """检测一张大模型 API 接法说明。
    whole=True（基础层的块）：拿模型名单，再自动挑一个试通，结果里带 models（名单）和 model（试通的那个）。
    否则（管家绑定自己的模型）：只试卡上写的那一个；need_tools 时再测会不会用工具。
    返回 {ok, status(ok/fail/no_tools/quota), reason, how, seconds, tools}。密钥只在这次调用里用，不保存。"""
    card = blocks.validate_card(card)
    if card["kind"] != "llm_api":
        raise BlockError("这是 CLI，不是大模型 API，要用 CLI 的检测")
    if not isinstance(api_key, str) or not api_key.strip():
        raise BlockError("还没填密钥：先在密钥框里填好再检测")
    if not whole and not card.get("model"):
        raise BlockError("这份接法说明没写模型，只能按整家检测")
    key = api_key.strip()
    with _OneAtATime(card["id"]):
        started = time.time()
        tools = None
        if whole:
            result = _check_whole(card, key, client_factory, models_fetcher)
        else:
            result, tools, _ = _ping(card, card["model"], key, client_factory, need_tools)
            result["words_kept"] = result.pop("words", "")
            result.pop("bad_key", None)
            if result.get("code") == 403:
                words = result.get("words_kept") or ""
                result["reason"] = "服务商拒绝了这个模型（返回代码 403），多半是这个模型在你账号上没开通" + (f"，平台说：{words}" if words else "")
                result["how"] = "到平台上开通这个模型，或者换一个已开通的模型名"
                result.pop("wrong_model", None)
            if result.pop("wrong_model", False):
                result["reason"] = "模型名或参数不对（服务商返回代码 400）：跟管家说换一个这家支持的模型名"
                if not client_factory:  # 真连平台时，顺手问它真有哪些模型
                    result["reason"] = _real_models_hint(card, key) or result["reason"]
        result.pop("words_kept", None)
        result.pop("code", None)
        result["reason"] = _mask_cut(" ".join(result["reason"].split()).replace(key, "[已隐藏]"), 400) or "连接出错"
        result["how"] = _mask(result["how"], key)
        result.update(seconds=round(time.time() - started, 1), tools=tools)
        return _save(card["id"], result, root, record)
