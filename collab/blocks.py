"""积木库：资料夹、接法卡和记录（50 号单）。

整台电脑一份资料夹；每个接进来的 CLI 或大模型 API 用一张接法卡描述。
接法卡大多由行政起草，什么都可能写错：一律给中文提示，不能崩。
密钥永远不进接法卡和记录；拼命令只做占位替换，不经过 shell。
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from collab.config import DEFAULT_MODELS


class BlockError(Exception):
    pass


DEFAULT_ROOT = Path.home() / "Library" / "Application Support" / "超级助手" / "积木库"
CARD_DIR = "基础层"
RECORD_DIR = "记录"
DEFAULT_DONE_FILE = "连通检测.txt"

_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_COMMAND = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_SECRET_TEXT = re.compile(r"sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}"
                          r"|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|(?i:bearer)\s+(?=\S*\d)[A-Za-z0-9._~+/=-]{20,}"
                          r"|(?i:x-api-key|authorization)\s*:\s*(?:(?i:bearer|basic)\s+)?\S{6,}"
                          r"|[A-Za-z_]{0,40}(?i:api[_-]?key|_token|_secret|authtoken)\s*[:=]\s*\S{6,}|npm_[A-Za-z0-9]{36}"
                          # 国内平台：腾讯云 AKID、智谱 32位.16位、火山方舟 UUID、百度千帆 bce-v3；还有 JWT 令牌
                          r"|AKID[A-Za-z0-9]{13,40}|\b[0-9a-f]{32}\.[A-Za-z0-9]{16}\b"
                          r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b|bce-v3/[A-Za-z0-9/_-]{20,}"
                          r"|eyJ[A-Za-z0-9_-]{10,500}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}")  # 模型名：不能以横线开头，免得被当成开关
_SECRET_NAME_EXACT = {"key", "auth"}
_SECRET_NAME_PARTS = ("apikey", "secret", "password", "passwd", "authorization", "accesskey", "privatekey")
# 名字以 token 结尾的才算（access_token、token）；tokens_used、max_tokens 是用量，不是密钥
_NOT_SECRET_NAMES = {"keyaccount"}  # llm_api 卡里只存钥匙串账号名，不是密钥
# 接法卡不能变成“运行任意程序”的口子：这些程序要么能执行任意脚本，要么能删改文件、联网下载、装软件
_DANGEROUS_COMMANDS = {
    "sh", "bash", "zsh", "dash", "fish", "ksh", "csh", "tcsh", "env", "sudo", "doas", "su", "exec", "nohup",
    "node", "deno", "bun", "ruby", "perl", "php", "lua", "osascript", "powershell", "pwsh", "java",
    "rm", "rmdir", "mv", "cp", "dd", "chmod", "chown", "ln", "kill", "killall", "launchctl", "open", "xargs", "find",
    "curl", "wget", "nc", "ncat", "scp", "ssh", "rsync", "ftp", "git",
    # 能转跑别的程序、或者能跑脚本的常见工具（复查第三轮补）；名单堵不全，没核实的卡还要用户确认
    "caffeinate", "nice", "time", "timeout", "script", "arch", "tmux", "screen", "vim", "vi", "nvim", "emacs",
    "expect", "sqlite3", "pkill", "make", "docker", "awk", "gawk", "sed", "tar", "unzip", "tee", "install",
    "sandbox-exec", "ipython", "pypy", "pypy3", "swift", "tclsh", "rscript", "julia", "uvx", "bunx", "watch",
    "at", "crontab", "defaults", "security",
    "command", "stdbuf", "taskpolicy", "xcrun", "shortcuts", "automator", "lldb", "gdb", "ex", "view", "zip",
    "pythonw", "r",
    "npm", "npx", "pnpm", "yarn", "brew", "pip", "pip3", "gem", "cargo", "go", "uv", "pipx",
}
_DANGEROUS_PATTERN = re.compile(r"(python|pip|perl|ruby|node)[0-9.]*")
# 参数里出现这些，就是把密钥写进了命令，或者关掉了工具自己的安全限制
# 程序这一层只是第二道网，拦不全；没核实的卡靠用户看完整命令再确认（方案“核实的放行，其余亮出来确认”）
_SECRET_FLAG_EXACT = {"key", "auth", "authtoken", "authorization", "bearer", "cookie", "cred", "credential",
                      "credentials", "pat", "passphrase"}
_SECRET_FLAG_PARTS = ("apikey", "secret", "password", "passwd", "accesstoken", "accesskey", "privatekey",
                      "credential")
_SAFETY_OFF_PARTS = ("dangerously", "yolo", "skippermissions", "bypasspermissions", "bypassapprovals",
                     "bypasssandbox", "nosandbox", "fullaccess")
_SAFETY_OFF_VALUES = ("bypasspermissions", "yolo", "dangerfullaccess", "approvalpolicy=never", "dangerously")
_DONE_FILE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff._-]{0,59}")
_PART_LIMIT = 500


def _flag_name(part: str) -> str:
    return part.lower().lstrip("-").split("=", 1)[0].replace("-", "").replace("_", "")


def _secret_flag(name: str) -> bool:
    return (name in _SECRET_FLAG_EXACT or name.endswith("key") or name.endswith("token")
            or any(w in name for w in _SECRET_FLAG_PARTS))


def _safety_off(part: str, previous_flag: str) -> bool:
    squashed = part.lower().replace("-", "").replace("_", "")
    if part.startswith("-"):
        name = _flag_name(part)
        if name == "bypass" or any(w in name for w in _SAFETY_OFF_PARTS):
            return True
        if "sandbox" in name and "=" in part and part.split("=", 1)[1].lower() in ("none", "off", "false", "disabled"):
            return True
    elif "sandbox" in previous_flag and part.lower() in ("none", "off", "false", "disabled"):
        return True
    return any(w in squashed for w in _SAFETY_OFF_VALUES)


def _common_char(ch: str) -> bool:
    """参数只许常见字符：英文可见字符和空格、汉字、常用中文标点和全角符号。看不见的字、怪空白一律不许。"""
    code = ord(ch)
    return (0x20 <= code <= 0x7E or 0x4E00 <= code <= 0x9FFF or 0x3001 <= code <= 0x303F
            or 0xFF01 <= code <= 0xFF5E or ch in "“”‘’…—·")


def _odd_text(part: str) -> bool:
    return not all(_common_char(ch) for ch in part)
_VERSION_FORMS = (["--version"], ["-V"], ["-v"], ["version"])
_LOGIN_FORMS = (["status"], ["login", "status"], ["auth", "status"], ["whoami"], ["auth", "whoami"])
_FIELDS = {
    "cli": {"id", "kind", "name", "command", "run", "model", "version_args", "login_check", "done_when",
            "source", "note"},
    "llm_api": {"id", "kind", "name", "provider", "base_url", "model", "key_account", "source", "note"},
}

BUILTIN = {
    "codex": {"id": "codex", "kind": "cli", "name": "Codex", "command": "codex",
              "run": ["exec", "--skip-git-repo-check", "-s", "workspace-write", "-m", "{model}", "{prompt}"],
              "model": DEFAULT_MODELS["codex"],
              "login_check": {"args": ["login", "status"], "ok_words": ["Logged in"]}},
    "cursor": {"id": "cursor", "kind": "cli", "name": "Cursor", "command": "agent",
               "run": ["-p", "--trust", "--model", "{model}", "--output-format", "json", "{prompt}"],
               "model": DEFAULT_MODELS["cursor"],
               "login_check": {"args": ["status"], "ok_words": ["Logged in"]}},
    # agy 没有查登录的命令，不写 login_check
    "agy": {"id": "agy", "kind": "cli", "name": "agy", "command": "agy",
            "run": ["-p", "{prompt}", "--model", "{model}", "--output-format", "json"],
            "model": DEFAULT_MODELS["agy"]},
}


def home(root=None) -> Path:
    """积木库资料夹：传了 root 用 root，否则看环境变量，再否则用默认位置。只算路径，不建文件夹。"""
    if root:
        return Path(root).expanduser()
    env = os.environ.get("SUPER_AGENT_BLOCKS_HOME")
    return Path(env).expanduser() if env else DEFAULT_ROOT


def _secret_name(name) -> bool:
    if not isinstance(name, str):
        return False
    norm = name.lower().replace("-", "").replace("_", "")
    if norm in _NOT_SECRET_NAMES:
        return False
    return norm in _SECRET_NAME_EXACT or norm.endswith("token") or any(part in norm for part in _SECRET_NAME_PARTS) \
        or bool(_SECRET_TEXT.search(name))


def _has_secret(value) -> bool:
    if isinstance(value, dict):
        return any(_secret_name(k) or _has_secret(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_secret(v) for v in value)
    return isinstance(value, str) and bool(_SECRET_TEXT.search(value))


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _texts(value, nonempty=False) -> bool:
    """非空的文字列表，每一项都是文字；nonempty 时每一项也不能是空白。"""
    return (isinstance(value, list) and bool(value)
            and all(isinstance(v, str) and (not nonempty or v.strip()) for v in value))


def _json_safe(value) -> bool:
    try:
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return False
    return True


def _check_id(card_id) -> str:
    if not isinstance(card_id, str) or not _ID.fullmatch(card_id):
        raise BlockError(f"编号 {card_id!r} 不对：只能用小写字母、数字和横线，开头不能是横线，最长 40 个字")
    return card_id


def _check_cli(card: dict) -> None:
    command = card.get("command")
    if not isinstance(command, str) or not _COMMAND.fullmatch(command):
        raise BlockError("命令不对：只能写可执行文件的名字，不能带路径、空格或符号，开头不能是横线")
    low = command.lower()
    if low in _DANGEROUS_COMMANDS or _DANGEROUS_PATTERN.fullmatch(low):
        raise BlockError(f"命令 {command} 不能接进基础层：它能运行任意脚本或改动电脑，只能接 AI 工具本身")
    run = card.get("run")
    if not _texts(run) or not any("{prompt}" in part for part in run):
        raise BlockError("run 要写成一组参数，并且其中一处要有 {prompt}，用来放交代")
    previous_flag = ""
    for part in run:
        if len(part) > _PART_LIMIT or _odd_text(part) or "（交代）" in part:
            raise BlockError("参数里只能用英文字符、空格、汉字和常用中文标点，不能有换行和看不见的字，单个参数最长 500 个字，也不能直接写“（交代）”")
        if part.startswith("-") and "{" in part.split("=", 1)[0]:
            raise BlockError(f"参数 {part} 不对：开关名里不能放占位，占位只能写在 = 后面或单独一个参数")
        if part.startswith("-") and _secret_flag(_flag_name(part)):
            raise BlockError(f"参数 {part.split('=', 1)[0]} 是在命令里写密钥：密钥只存在钥匙串里，不能写进接法卡")
        if _safety_off(part, previous_flag):
            raise BlockError(f"参数 {part} 会关掉工具自己的安全限制，不能写进接法卡")
        previous_flag = _flag_name(part) if part.startswith("-") else ""
    used = {name for part in run for name in _PLACEHOLDER.findall(part)}
    unknown = sorted(used - {"prompt", "model"})
    if unknown:
        raise BlockError(f"run 里有不认识的占位：{'、'.join('{' + n + '}' for n in unknown)}，只能用 {{prompt}} 和 {{model}}")
    if "model" in card and not (isinstance(card["model"], str) and _MODEL.fullmatch(card["model"])):
        raise BlockError("模型名不对：要以字母或数字开头，只能用字母、数字和 . _ : / @ + -")
    if "model" in used and not _text(card.get("model")):
        raise BlockError("run 里用了 {model}，就要写默认模型")
    card.setdefault("version_args", ["--version"])
    if card["version_args"] not in _VERSION_FORMS:
        raise BlockError("查版本只能写 --version、-V、-v 或 version 其中一种")
    if "login_check" in card:
        check = card["login_check"]
        if not (isinstance(check, dict) and set(check) == {"args", "ok_words"}
                and _texts(check["args"]) and _texts(check["ok_words"], nonempty=True)
                and all(len(w.strip()) >= 3 for w in check["ok_words"])):
            raise BlockError("登录检查要写成 {\"args\": [参数], \"ok_words\": [已登录时会出现的字]}，两样都不能空，判断字样至少 3 个字，不能多写别的")
        if check["args"] not in _LOGIN_FORMS:
            raise BlockError("查登录只能用 status、login status、auth status、whoami 这几种写法")
    card.setdefault("done_when", {"file": DEFAULT_DONE_FILE})
    done = card["done_when"]
    name = done.get("file") if isinstance(done, dict) and set(done) == {"file"} else None
    if not isinstance(name, str) or not _DONE_FILE.fullmatch(name):
        raise BlockError("算干完活的文件只能写一个普通文件名：字母、数字、汉字和 . _ -，最长 60 个字，不能以点开头")


def _check_api(card: dict) -> None:
    if not isinstance(card.get("base_url"), str) or not card["base_url"].startswith("https://"):
        raise BlockError("地址要以 https:// 开头")
    for field, label in (("provider", "服务商"), ("model", "模型"), ("key_account", "钥匙串账号")):
        if not _text(card.get(field)):
            raise BlockError(f"{label}不能为空")
    if not _MODEL.fullmatch(card["model"]):
        raise BlockError("模型名不对：要以字母或数字开头，只能用字母、数字和 . _ : / @ + -")


def validate_card(card) -> dict:
    """检查一张接法卡，返回补齐默认值的新卡，不改传进来的那份。"""
    if not isinstance(card, dict):
        raise BlockError("接法卡要写成一组字段")
    if _has_secret(card):
        raise BlockError("密钥不能写进接法卡，密钥只存在钥匙串里")
    card = copy.deepcopy(card)
    _check_id(card.get("id"))
    kind = card.get("kind")
    if not isinstance(kind, str) or kind not in _FIELDS:
        raise BlockError(f"类型 {kind!r} 不认识：只能是 cli 或 llm_api")
    extra = sorted(str(k) for k in set(card) - _FIELDS[kind])
    if extra:
        raise BlockError(f"接法卡里有不认识的项：{'、'.join(extra)}")
    if not _text(card.get("name")) or len(card["name"].strip()) > 40:
        raise BlockError("名字不能为空，最长 40 个字")
    card.setdefault("source", "clerk")
    if card["source"] not in ("builtin", "clerk"):
        raise BlockError("来源只能是 builtin 或 clerk")
    if "note" in card and not isinstance(card["note"], str):
        raise BlockError("备注要写成文字")
    (_check_cli if kind == "cli" else _check_api)(card)
    if not _json_safe(card):
        raise BlockError("接法卡里有存不下来的内容（怪字符或不是文字的值）")
    return card


def _card_path(card_id, root) -> Path:
    return home(root) / CARD_DIR / f"{_check_id(card_id)}.json"


def save_card(card, root=None) -> dict:
    clean = validate_card(card)
    path = _card_path(clean["id"], root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{clean['id']}.", suffix=".tmp")  # 每次名字不同，同时保存也不打架
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(clean, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return clean


def load_card(card_id, root=None) -> dict:
    path = _card_path(card_id, root)
    if not path.is_file():
        raise BlockError(f"基础层里没有 {card_id}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise BlockError(f"{card_id} 的接法卡读不出来，文件可能坏了") from exc
    try:
        card = validate_card(data)
    except RecursionError as exc:
        raise BlockError(f"{card_id} 的接法卡嵌套太深，读不了") from exc
    except BlockError as exc:
        raise BlockError(f"{card_id} 的接法卡有问题：{exc}") from exc
    if card["id"] != card_id:
        raise BlockError(f"{card_id} 的接法卡里写的编号是 {card['id']}，对不上")
    return card


def list_cards(root=None) -> list:
    """按文件名排序；坏卡单独标出原因，不影响别的卡。"""
    folder = home(root) / CARD_DIR
    if not folder.is_dir():
        return []
    items = []
    for path in sorted(folder.glob("*.json")):
        try:
            items.append(load_card(path.stem, root))
        except BlockError as exc:
            items.append({"id": path.stem, "problem": str(exc)})
        except Exception:  # noqa: BLE001 — 一张卡出什么意外，都不能让别的卡消失
            items.append({"id": path.stem, "problem": f"{path.stem} 的接法卡读不了"})
    return items


def remove_card(card_id, root=None) -> None:
    """只把卡移出基础层，记录留着。"""
    path = _card_path(card_id, root)
    if not path.is_file():
        raise BlockError(f"基础层里没有 {card_id}")
    path.unlink()


def builtin_card(name) -> dict:
    if not isinstance(name, str) or name not in BUILTIN:
        raise BlockError(f"没有预置的 {name}，预置的只有 {'、'.join(sorted(BUILTIN))}")
    return validate_card(dict(BUILTIN[name], source="builtin"))


def is_verified(card) -> bool:
    """核实过：命令和所有参数都和预置卡一模一样（名字、来源、模型、备注不算）。
    核实过的可以直接检测；没核实的，检测前要把完整命令亮给用户再确认一次（用户 2026-11-08 拍板）。"""
    card = validate_card(card)
    if card["kind"] != "cli":
        return False
    fixed = ("command", "run", "version_args", "login_check", "done_when")
    for name in BUILTIN:
        preset = builtin_card(name)
        if all(card.get(k) == preset.get(k) for k in fixed):
            return True
    return False


_PROMPT_MARK = "（交代）"


def command_preview(card, executable=None) -> str:
    """给用户看的完整命令：交代写成“（交代）”，带空格的参数加引号，看得出是一个整体。
    executable 给了就显示实际要运行的完整路径（电脑上有同名程序时看得出是哪一个）。"""
    import shlex
    args = build_args(card, _PROMPT_MARK)
    if executable:
        args[0] = str(executable)
    return " ".join(_PROMPT_MARK if a == _PROMPT_MARK else shlex.quote(a) for a in args)


def build_args(card, prompt, model=None) -> list:
    """按接法卡拼出参数列表。只替换模板里本来就有的占位，交代原样作为参数，不展开。"""
    card = validate_card(card)
    if card["kind"] != "cli":
        raise BlockError("大模型 API 不用拼命令")
    if not isinstance(prompt, str):
        raise BlockError("交代要是文字")
    if model is not None and not (isinstance(model, str) and _MODEL.fullmatch(model)):
        raise BlockError("模型名不对：要以字母或数字开头，只能用字母、数字和 . _ : / @ + -")
    values = {"prompt": prompt, "model": model or card.get("model", "")}
    return [card["command"]] + [_PLACEHOLDER.sub(lambda m: values[m.group(1)], part) for part in card["run"]]


def _record_path(card_id, root) -> Path:
    return home(root) / RECORD_DIR / f"{_check_id(card_id)}.jsonl"


def add_record(card_id, record, root=None) -> dict:
    path = _record_path(card_id, root)
    if not isinstance(record, dict) or not _text(record.get("type")):
        raise BlockError("记录要写 type，并且是文字")
    if _has_secret(record):
        raise BlockError("记录里不能有密钥")
    item = dict(record, time=datetime.now().isoformat(timespec="seconds"))
    if not _json_safe(item):
        raise BlockError("记录里有存不下来的内容（怪字符或不是文字的值）")
    line = json.dumps(item, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
    return item


def records(card_id, root=None, limit=None) -> list:
    path = _record_path(card_id, root)
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise BlockError("limit 要是大于 0 的整数")
    if not path.is_file():
        return []
    items = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():  # 坏字节只坏那一行
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items[-limit:] if limit else items


def last_record(card_id, root=None, type=None):
    for item in reversed(records(card_id, root)):
        if type is None or item.get("type") == type:
            return item
    return None
