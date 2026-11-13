"""三家 CLI 适配器：拼命令、执行、检查文件是否真的落盘。

命令已在本机实测通过（见 docs/多agent协作包/联调手册.md）。
不使用 --force / --dangerously-skip-permissions：权限分类器会拒绝，也不必要。
"""
from __future__ import annotations

import json
import os
import re
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

_active_tool = None  # 当前派活里的工具进程，供 collab run 收到结束信号时一并停掉
_active_tools: set = set()  # 同时在干活的全部工具进程（写测试和备交接资料会同时跑，第二轮 ㉚）


_stopping = {"on": False}  # 收到停工后不再起新的工具进程（后台备资料的线程会重试，复查 C 第 1 条）
import threading as _threading
# “看停工标记 → 起进程 → 登记”和“停工”不能交错（第 2 轮 C1）；可重入：停工信号可能正好打在主线程起进程时（第 3 轮 C1）
_tools_lock = _threading.RLock()


def kill_current_tool_process_group() -> None:
    """对正在运行的工具进程组先温和结束，再强制结束；之后本进程不再起新的工具。"""
    with _tools_lock:
        _stopping["on"] = True
    for _ in range(3):
        procs = list(_active_tools) or ([_active_tool] if _active_tool is not None else [])
        if not procs:
            break
        for proc in procs:
            _stop(proc)
            _active_tools.discard(proc)


def allow_tools() -> None:
    """停工收尾完了，本进程又可以起工具（同一个进程里接着跑下一张卡、跑测试时用）。"""
    _stopping["on"] = False


def cursor_command(prompt: str, model: str = DEFAULT_MODELS["cursor"]):
    """沙箱一律打开：派活时会放行本卡测试命令（流程修理 ⑯），沙箱保证它只能写施工副本和临时文件夹。
    2026-11-12 实测：开沙箱后 python3 能跑、能写副本和临时文件夹，写不到用户主目录。"""
    # stream-json：边干边吐一行一行的进展（标题下“此刻在干什么”能看到它在读哪个文件、跑什么命令），最后一行仍是结果
    return ["agent", "-p", "--trust", "--sandbox", "enabled", "--model", model, "--output-format", "stream-json", prompt]


def codex_command(prompt: str, model: str = DEFAULT_MODELS["codex"],
                  effort: str = DEFAULT_MODELS["codex_effort"], last_path=None, sandbox: str = "workspace-write"):
    """last_path：让 Codex 把最后一条回复单独写进这个文件（完整过程照样从输出里看）。
    sandbox：调度员备交接资料时用 read-only，只读不改（流程修理第二轮 ㉚）。"""
    cmd = ["codex", "exec", "--skip-git-repo-check", "-s", sandbox,
           "-m", model, "-c", f'model_reasoning_effort="{effort}"']
    if last_path:
        cmd += ["-o", str(last_path)]
    return cmd + [prompt]


def agy_command(prompt: str, model: str = DEFAULT_MODELS["agy"]):
    # stream-json：每走一步吐一行（㉙：读代码十几分钟不改文件时，看门人和标题行靠它知道 agy 在干活），最后一行是结果
    return ["agy", "-p", prompt, "--model", model, "--output-format", "stream-json"]


_plugin_cache: list = []


def _codex_plugin_names() -> list:
    """用户给 Codex 配的插件名（每个进程只问一次）。问不到就当没有。"""
    if _plugin_cache:
        return _plugin_cache[0]
    # 只能关用户设置文件里写的那些；Codex 应用自带的插件不在文件里，硬关会让 Codex 报错起不来（实测）
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        text = (home / "config.toml").read_text(encoding="utf-8")
    except OSError:
        text = ""
    # 只按设置文件里写的名字关（本来就关着的再关一次无妨）；不去跑 `codex mcp list`：多起一次 Codex，
    # 测试里的假 Codex 还会把它当成一次派活（全量时发现）
    names = _names_in_config(text)
    _plugin_cache.append(names)
    return names


def _names_in_config(text: str) -> list:
    """Codex 设置文件里写的插件名：[mcp_servers.名字]、["名字"]、['名字'] 三种写法。
    写成一行 mcp_servers = {...} 的认不出（少见），这种照旧开着。"""
    names = []
    pat = r"""^\s*\[mcp_servers\.(?:"([^"]+)"|'([^']+)'|([A-Za-z0-9_-]+))\]\s*(?:#.*)?$"""
    found = [m.group(1) or m.group(2) or m.group(3) for m in re.finditer(pat, text, re.MULTILINE)]
    # 写成一行的：mcp_servers = { playwright = {...}, "a b" = {...} }，以及 mcp_servers.名字.command = ...（第 2 轮 C 第 4 条）
    for m in re.finditer(r"^\s*mcp_servers\s*=\s*\{(.*)$", text, re.MULTILINE | re.DOTALL):
        body, depth, top = m.group(1), 1, []
        for ch in body:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            top.append(ch if depth == 1 else (" " if ch not in "{}" else ch))
        flat = "".join(top)
        found += [a or b or c for a, b, c in
                  re.findall(r"""(?:"([^"]+)"|'([^']+)'|([A-Za-z0-9_-]+))\s*=\s*\{""", flat)]
    found += [a or b or c for a, b, c in re.findall(
        r"""^\s*mcp_servers\.(?:"([^"]+)"|'([^']+)'|([A-Za-z0-9_-]+))\s*[.=]""", text, re.MULTILINE)]  # 第 3 轮 C3
    for name in found:
        if name not in names:
            names.append(name)
    return names


def _no_plugin_flags(names: list) -> list:
    """只对这一次关掉插件，不改用户自己的设置（第二轮 ㉘ ㊶）。名字带空格、点的要加引号。"""
    if not names:
        return []
    # 用一整张表合并进去：点号写法认不了带空格的名字（实测会当成新插件报错）
    table = ",".join(f"{json.dumps(n, ensure_ascii=False)}={{enabled=false}}" for n in names)
    return ["-c", f"mcp_servers={{{table}}}"]


def _is_codex_shell(adapter: str) -> bool:
    """用 Codex 的壳干活的：Codex 本身，和接千问 Token Plan 的“千问”。输出格式、最后回复都按 Codex 认。"""
    return adapter in ("codex", "qwen")


def qwen_command(prompt: str, model: str, effort: str = DEFAULT_MODELS["codex_effort"], last_path=None) -> list:
    """千问：Codex 的壳，模型来源换成千问 Token Plan（responses 接法；chat 接法 Codex 已不支持，11-13 实测）。
    密钥放进环境变量交给 Codex，命令行里不出现。"""
    from collab.config import QWEN_BASE_URL, QWEN_KEY_ENV
    # 密钥不放进本程序的环境（否则之后起的每个工具、每次测试都会继承，复查发现可能被印进测试结果和交接），
    # 只在起千问这一个进程时单独交给它（ask → runner 的 env）；Codex 默认不把名字带 KEY 的变量传给它跑的命令，这里再明说一次
    cmd = codex_command(prompt, model=model, effort=effort, last_path=last_path)
    provider = ["-c", 'model_providers.qwtp.name="千问 Token Plan"',
                "-c", f'model_providers.qwtp.base_url="{QWEN_BASE_URL}"',
                "-c", f'model_providers.qwtp.env_key="{QWEN_KEY_ENV}"',
                "-c", 'model_providers.qwtp.wire_api="responses"',
                "-c", 'model_provider="qwtp"',
                "-c", f'shell_environment_policy.exclude=["{QWEN_KEY_ENV}"]']
    # Codex 给自家模型的附加功能（多智能体、ChatGPT 应用连接、操作电脑、插件、内置浏览器）会打包成千问部分模型
    # 不认的工具格式，Qwen4 Max Preview 因此整条请求被拒（11-13 抓包逐项比出来）；干活也用不到，接千问时一律关掉
    for feature in ("multi_agent", "apps", "computer_use", "plugins", "browser_use", "in_app_browser"):
        provider += ["-c", f"features.{feature}=false"]
    return cmd[:-1] + provider + cmd[-1:]


def _build(adapter: str, prompt: str, models: dict, last_path=None):
    if _is_codex_shell(adapter) and models.get("no_plugins"):
        cmd = _build(adapter, prompt, {k: v for k, v in models.items() if k != "no_plugins"}, last_path)
        return cmd[:-1] + _no_plugin_flags(_codex_plugin_names()) + cmd[-1:]
    if adapter == "cursor":
        return cursor_command(prompt, model=models["cursor"])
    if adapter == "codex":
        return codex_command(prompt, model=models["codex"], effort=models["codex_effort"], last_path=last_path,
                             sandbox=models.get("codex_sandbox") or "workspace-write")
    if adapter == "agy":
        return agy_command(prompt, model=models["agy"])
    if adapter == "qwen":
        return qwen_command(prompt, model=models.get("qwen") or DEFAULT_MODELS["qwen"],
                            effort=models.get("codex_effort") or DEFAULT_MODELS["codex_effort"], last_path=last_path)
    raise KeyError(adapter)  # 未知适配器直接报错


@dataclass
class AgentResult:
    ok: bool
    output: str
    attempts: int
    final: str = ""  # 最后一条回复（交给下一个人、认复查结论只看它）；完整过程在 output


def _agy_events(text: str) -> list:
    """agy 边干边吐的每一步（一行一个 {"event": ...}），按先后顺序。"""
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("event"), str):
            events.append(data)
    return events


def _agy_result(text: str, is_agy: bool):
    """agy 最后吐的结果（dict）；不是 agy 边干边吐的输出返回 None。
    知道是 agy（is_agy）：只要有它的步骤，就看它的最后一步——是结果才算，后面混进的报错字、被截断的半行都不算数。
    不知道是谁：只有最后一行是 agy 的一步才当 agy（别家工具印出来的 agy 日志，后面跟着它自己的话）。"""
    events = _agy_events(text)
    if not events:
        return None
    if not is_agy:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        try:
            last = json.loads(lines[-1])
        except ValueError:
            return None
        if not (isinstance(last, dict) and isinstance(last.get("event"), str)):
            return None
    last = events[-1]
    result = last.get("result") if last.get("event") == "result" else None
    return result if isinstance(result, dict) else {}


def agy_error(output: str) -> str:
    """agy 没做成时它自己报的原因（第一行），比如模型名不对、额度不够；没有就空。"""
    result = _agy_result((output or "").strip(), True)
    if not result or result.get("status") == "SUCCESS":
        return ""
    error = result.get("error")
    lines = [ln.strip() for ln in error.splitlines() if ln.strip()] if isinstance(error, str) else []
    return lines[0] if lines else ""


def readable(output: str, adapter: str = "") -> str:
    """各家工具的回复可能是 JSON 包装（agy 用 response，Cursor 用 result），只取里面的文字。
    agy 边干边吐（㉙）：只认它最后吐的结果，SUCCESS 取 response；不是 SUCCESS、没吐出结果（被停掉、被截断）
    就当没回话——宁可算没完成，不能把报错或过程当结论。adapter 给了 agy 就按 agy 的规矩认（复查第 1 轮）。"""
    text = (output or "").strip()
    result = _agy_result(text, adapter == "agy")
    if result is not None:
        reply = result.get("response") if result.get("status") == "SUCCESS" else None
        return reply.strip() if isinstance(reply, str) else ""
    candidates = [text] + [line.strip() for line in reversed(text.splitlines())
                           if line.strip().startswith("{") and line.strip().endswith("}")]
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            for key in ("response", "result", "message", "text"):
                if isinstance(data.get(key), str):
                    return data[key].strip()
    return text


EMPTY_REPLY = "没有回复任何内容"


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
    """连同它拉起的子进程一起停掉；先温和结束，不行再强停。
    工具已正常退出时也要清进程组，避免留下空闲子进程。"""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    deadline = time.time() + 5
    while proc.poll() is None and time.time() < deadline:
        try:
            proc.wait(timeout=min(0.2, max(0.01, deadline - time.time())))
        except subprocess.TimeoutExpired:
            pass
    if proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass
    try:
        proc.wait(timeout=1)
    except (subprocess.TimeoutExpired, OSError):
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _output_file(live_path):
    """给了实时输出位置就写进去（桌面窗口能边看边刷新），否则用用完即删的临时文件。"""
    if live_path:
        Path(live_path).parent.mkdir(parents=True, exist_ok=True)
        return open(live_path, "w+", encoding="utf-8", errors="replace")
    return tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")


REMIND_AT = (5 * 60, 10 * 60)  # 没动静多久提醒一次（行政在卡片对话里说）


def _tail_of(out, size: int = 256 * 1024) -> str:
    """实时输出的最后一截（不动它的读写位置）。"""
    try:
        fd = out.fileno()
        end = os.fstat(fd).st_size
        return os.pread(fd, size, max(0, end - size)).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


def _output_mtime(out) -> float:
    try:
        return os.fstat(out.fileno()).st_mtime
    except (OSError, ValueError):
        return 0.0


COMMAND_LIMIT = 120  # 干活的人自己跑的一条命令最多多久（第二轮 ㊹）
TEST_COMMAND_LIMIT = 300  # 跑本卡测试那条给长一点
HELPER_GRACE = 15  # 工具刚启动这几秒里拉起的子进程是它自己的帮手（插件、语言服务），不算命令
# 插件、帮手（连同它们拉起的子进程）不算命令；不再按“启动头几秒”豁免（第 2 轮 C 第 2 条）
# 只认明确是插件的字样；npx、npm exec 也会用来跑普通检查（npx tsc），不能一律当插件（第 3 轮 C2）
_HELPER_WORDS = ("mcp", "modelcontextprotocol", "node_repl", "code-mode-host", "language_server", "language-server",
                 "/plugins/", "/extensions/")
_COMMAND_NAMES = {"bash", "sh", "zsh", "python", "python3", "node"}


def _etime_seconds(text: str) -> int:
    days, _, rest = text.strip().rpartition("-")
    parts = [int(p) for p in rest.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    return (int(days) if days else 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def _process_table() -> dict:
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,etime=,command="], capture_output=True,
                             text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    table = {}
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4:
            try:
                table[int(parts[0])] = (int(parts[1]), _etime_seconds(parts[2]), parts[3])
            except ValueError:
                continue
    return table


def _is_command(line: str) -> bool:
    low = line.lower()
    if any(w in low for w in _HELPER_WORDS):
        return False
    first = line.split()[0] if line.split() else ""
    name = os.path.basename(first).lower()
    return name in _COMMAND_NAMES or bool(re.fullmatch(r"python3?(\.\d+)?", name))


def _kill_tree(table: dict, root: int) -> None:
    family = [root]
    for pid in family:
        family.extend(p for p, (pp, _e, _c) in table.items() if pp == pid and p not in family)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in reversed(family):
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        if sig == signal.SIGTERM:
            time.sleep(0.5)


def _strip_env(cmd: str) -> str:
    """去掉打头的 KEY=值（值可以带引号和空格）：交给 shell 跑以后，进程参数里就看不到这些了（复查 C 第 4 条）。"""
    import shlex
    try:
        words = shlex.split(cmd or "")
    except ValueError:
        return (cmd or "").strip()
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0], re.S):
        words.pop(0)
    return " ".join(words)


def _stuck_commands(table: dict, tool_pid: int, tool_age: float, limits: tuple, test_command: str,
                    grace: float) -> list:
    """哪些命令跑超时了：[(进程号, 跑了几秒)]。只看干活的人拉起的最外面那层命令；
    插件、帮手进程（连同它们拉起的子进程）一律不算；工具刚启动那几秒拉起的 node 当帮手。"""
    command_limit, test_limit = limits
    parent = {pid: row[0] for pid, row in table.items()}
    mine = {tool_pid}
    changed = True
    while changed:
        changed = False
        for pid, ppid in parent.items():
            if ppid in mine and pid not in mine:
                mine.add(pid)
                changed = True
    test = _strip_env(test_command)

    def chain(pid):  # 从它往上到工具为止（不含工具）
        out = []
        while pid in table and pid != tool_pid and len(out) < 50:
            out.append(pid)
            pid = parent.get(pid)
        return out

    found = []
    for pid in sorted(mine - {tool_pid}):
        ppid, age, line = table[pid]
        up = chain(pid)
        if any(any(w in table[p][2].lower() for w in _HELPER_WORDS) for p in up):
            continue  # 插件、帮手，以及它们拉起的子进程
        if not _is_command(line):
            continue
        if ppid != tool_pid and ppid in table and _is_command(table[ppid][2]):
            continue  # 只看最外面那一层命令，里面的跟着它算
        is_test = bool(test) and any(test in _strip_env(table[p][2]) or test in table[p][2] for p in up)
        if age > (test_limit if is_test else command_limit):
            found.append((pid, age))
    return found


def _last_command(tail: str) -> str:
    """干活的人自己记下的“最近开始跑的那条命令”：agy、Cursor 一行一个 JSON，Codex 是 exec 下一行。"""
    last = ""
    lines = tail.splitlines()
    for i, ln in enumerate(lines):
        ln = ln.strip()
        if ln.startswith("{") and ln.endswith("}"):
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            step = ev.get("step_update") if isinstance(ev.get("step_update"), dict) else None
            if step and step.get("state") == "ACTIVE" and step.get("tool_name") == "run_command":
                params = (step.get("tool_info") or {}).get("parameters") or {}
                last = str(params.get("CommandLine") or params.get("command") or last)
            call = ev.get("tool_call") if ev.get("type") == "tool_call" and ev.get("subtype") == "started" else None
            if isinstance(call, dict) and "shellToolCall" in call:
                last = str(((call["shellToolCall"] or {}).get("args") or {}).get("command") or last)
        elif ln == "exec" and i + 1 < len(lines):
            last = lines[i + 1].strip() or last
    return last


def _same_command(a: str, b: str) -> bool:
    norm = lambda t: " ".join(_strip_env(t).split())
    return bool(a and b) and (norm(a) == norm(b) or norm(b) in norm(a))


def _stop_stuck_commands(tool_pid: int, tool_age: float, limits: tuple, test_command: str, grace: float,
                         note, tail: str = "") -> None:
    """超过时限的命令只掐那一条（连同它的子进程），再在实时输出里记一笔（㊹）。
    它此刻跑的是不是本卡测试，先看它自己记下的最近那条命令：agy 跑命令时进程参数里看不到原文，
    只靠进程列表会把本卡测试按 2 分钟掐掉（69 接着跑时掐了两次）。"""
    if test_command and _same_command(_last_command(tail), test_command):
        limits = (limits[1], limits[1])
    table = _process_table()
    for pid, age in _stuck_commands(table, tool_pid, tool_age, limits, test_command, grace):
        _kill_tree(table, pid)
        note(f"\n[看门人] 这条命令跑了 {age // 60} 分 {age % 60} 秒还没结束，已停掉这一条（不是停掉你），"
             f"请换个办法接着干。\n")


def watched_runner(cmd, cwd, timeout, quiet_limit=QUIET_LIMIT, poll=POLL_SECONDS, live_path=None,
                   on_quiet=None, remind_at=REMIND_AT, test_command="", command_limit=0, env=None,
                   test_limit=TEST_COMMAND_LIMIT, helper_grace=None):
    """边跑边看：总时长超过 timeout 抛 TimeoutExpired；连续 quiet_limit 秒没有动静抛 QuietTimeout。
    “动静”= 副本里有文件变化，或者它的输出还在涨（只看不改的复查员靠这个，68b 里被误杀过，㉓）。
    没动静到 remind_at 里的每个时刻调用一次 on_quiet(秒数)。输出写文件，避免管道写满卡住；
    live_path 给了就写到那里并保留，供实时查看。"""
    global _active_tool
    started = time.time()
    last_activity = started
    reminded = set()
    with _output_file(live_path) as out:
        with _tools_lock:
            if _stopping["on"]:
                raise subprocess.TimeoutExpired(cmd, 0)  # 已经停工：不再起新的
            proc = subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, env=env,
                                    stdin=subprocess.DEVNULL, text=True, start_new_session=True)
            _active_tool = proc
            _active_tools.add(proc)
        try:
            while proc.poll() is None:
                try:
                    proc.wait(timeout=poll)
                except subprocess.TimeoutExpired:
                    pass
                now = time.time()
                if command_limit:
                    _stop_stuck_commands(proc.pid, now - started, (command_limit, test_limit), test_command,
                                         HELPER_GRACE if helper_grace is None else helper_grace,
                                         lambda words: os.write(out.fileno(), words.encode("utf-8")),
                                         tail=_tail_of(out))
                newest = max(last_activity, _newest_mtime(cwd), _output_mtime(out))
                if newest > last_activity:
                    reminded.clear()  # 又有动静了，下次再安静从头算
                last_activity = newest
                if now - started > timeout:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                if now - last_activity > quiet_limit:
                    raise QuietTimeout(int(now - last_activity))
                for mark in remind_at or ():
                    if now - last_activity >= mark and mark not in reminded:
                        reminded.add(mark)
                        if on_quiet:
                            try:
                                on_quiet(int(now - last_activity))
                            except Exception:  # noqa: BLE001 — 提醒出错不能停掉干活的人
                                pass
        finally:
            _stop(proc)
            _active_tools.discard(proc)
            if _active_tool is proc:
                _active_tool = None
        out.seek(0)
        return proc.returncode, out.read()


def subprocess_runner(cmd, cwd, timeout):
    """旧的一次性执行方式（不看进度），保留给需要时使用。"""
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _codex_last(output: str) -> str:
    """Codex 没写出最后回复文件时，从过程里认最后回复（复查第 3、4 轮）。
    Codex 的过程结尾是：codex / 最后回复 / tokens used / 用量 / 最后回复再印一遍。只认两份对得上的那段，
    回复正文里引用了单独一行 codex、“结论：通过”甚至整段 Codex 日志也不会截错（取对得上的最长那段）；对不上就当没回话（宁可算没完成，不能错放）。
    整段既没有 codex 也没有 tokens used（测试里的假工具）就用整段。"""
    raw = (output or "").splitlines()
    while raw and not raw[-1].strip():
        raw.pop()
    lines = [ln.rstrip() for ln in raw]  # 只在比对时忽略行尾空格，取出来的照原样
    if "codex" not in lines and "tokens used" not in lines:
        return readable(output)
    for t in range(len(lines) - 1):  # 从前往后找，先对上的那段最长，就是 Codex 真正印的；回复里引用的日志只会是更短的一截
        if lines[t] != "tokens used" or not lines[t + 1].replace(",", "").strip().isdigit():
            continue
        tail = lines[t + 2:]
        start = t - len(tail)
        if tail and start >= 1 and lines[start:t] == tail and lines[start - 1] == "codex":
            return "\n".join(raw[t + 2:]).strip()
    return ""


def _read_last(path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def ask(adapter: str, prompt: str, cwd, expect_file=None, timeout=DEFAULT_TIMEOUT,
        runner=watched_runner, models=None, live_path=None, on_quiet=None, test_command="",
        watch_commands=False) -> AgentResult:
    """让某家工具执行一次任务。成功条件：退出码 0、有回复文字，且（若指定）期望文件真的存在。
    models 缺省时用包默认值；派发程序应传入 load_models(项目目录) 的结果。
    默认带看门人：长时间没动静会被停掉，并且不重试（卡住的再跑一遍多半还是卡住）。
    live_path：实时输出写到哪里（流转记录预留的位置），不给就不保留。
    退出码 0 却一个字都没回（68b 里 Cursor 收到超长交代时就这样）：再试一次，还空就算没完成。"""
    extra = {"live_path": live_path} if live_path else {}  # 旧的 runner 不认这个参数，不给就不传
    if on_quiet:
        extra["on_quiet"] = on_quiet
    if adapter == "qwen":  # 千问的密钥只交给这一个进程（不进本程序的环境）
        from collab.config import QWEN_KEY_ENV, qwen_key
        extra["env"] = dict(os.environ, **{QWEN_KEY_ENV: qwen_key()})
    if watch_commands:  # 派活时才看它自己跑的命令（㊹）；体检、能力运行不受影响
        extra["command_limit"] = COMMAND_LIMIT
        extra["test_command"] = test_command or ""
    output = ""
    with tempfile.TemporaryDirectory(prefix="collab-last-") as tmp:
        last_path = Path(tmp) / "最后回复.txt"
        cmd = _build(adapter, prompt, models or DEFAULT_MODELS, last_path=last_path)  # 未知适配器直接报错
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if _stopping["on"]:
                return AgentResult(ok=False, output="已停工", attempts=attempt)
            if last_path.exists():
                last_path.unlink()
            try:
                code, output = runner(cmd, cwd, timeout, **extra)
            except QuietTimeout as exc:
                return AgentResult(ok=False, output=f"看门人：{exc}，已停止", attempts=attempt)
            except subprocess.TimeoutExpired:
                return AgentResult(ok=False, output="超时", attempts=attempt)
            if code != 0:
                continue
            # Codex 写了最后回复文件就只认它（空的就是没回话），不能退回去拿整段过程当结论
            if last_path.exists():
                final = _read_last(last_path)
            else:
                final = _codex_last(output) if _is_codex_shell(adapter) else readable(output, adapter)
            if expect_file is None and not final.strip():  # 检测连通看的是建没建出文件，不看回话
                output = (output or "") + f"\n{EMPTY_REPLY}"
                continue
            if expect_file is None or (Path(cwd) / expect_file).exists():
                return AgentResult(ok=True, output=output, attempts=attempt, final=final)
    return AgentResult(ok=False, output=output, attempts=MAX_ATTEMPTS)
