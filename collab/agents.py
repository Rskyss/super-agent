"""三家 CLI 适配器：拼命令、执行、检查文件是否真的落盘。

命令已在本机实测通过（见 docs/多agent协作包/联调手册.md）。
不使用 --force / --dangerously-skip-permissions：权限分类器会拒绝，也不必要。
"""
from __future__ import annotations

import json
import os
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


def kill_current_tool_process_group() -> None:
    """对当前正在运行的工具进程组先温和结束，再强制结束。"""
    proc = _active_tool
    if proc is None:
        return
    _stop(proc)


def cursor_command(prompt: str, model: str = DEFAULT_MODELS["cursor"]):
    """沙箱一律打开：派活时会放行本卡测试命令（流程修理 ⑯），沙箱保证它只能写施工副本和临时文件夹。
    2026-11-12 实测：开沙箱后 python3 能跑、能写副本和临时文件夹，写不到用户主目录。"""
    # stream-json：边干边吐一行一行的进展（标题下“此刻在干什么”能看到它在读哪个文件、跑什么命令），最后一行仍是结果
    return ["agent", "-p", "--trust", "--sandbox", "enabled", "--model", model, "--output-format", "stream-json", prompt]


def codex_command(prompt: str, model: str = DEFAULT_MODELS["codex"],
                  effort: str = DEFAULT_MODELS["codex_effort"], last_path=None):
    """last_path：让 Codex 把最后一条回复单独写进这个文件（完整过程照样从输出里看）。"""
    cmd = ["codex", "exec", "--skip-git-repo-check", "-s", "workspace-write",
           "-m", model, "-c", f'model_reasoning_effort="{effort}"']
    if last_path:
        cmd += ["-o", str(last_path)]
    return cmd + [prompt]


def agy_command(prompt: str, model: str = DEFAULT_MODELS["agy"]):
    return ["agy", "-p", prompt, "--model", model, "--output-format", "json"]


def _build(adapter: str, prompt: str, models: dict, last_path=None):
    if adapter == "cursor":
        return cursor_command(prompt, model=models["cursor"])
    if adapter == "codex":
        return codex_command(prompt, model=models["codex"], effort=models["codex_effort"], last_path=last_path)
    if adapter == "agy":
        return agy_command(prompt, model=models["agy"])
    raise KeyError(adapter)  # 未知适配器直接报错


@dataclass
class AgentResult:
    ok: bool
    output: str
    attempts: int
    final: str = ""  # 最后一条回复（交给下一个人、认复查结论只看它）；完整过程在 output


def readable(output: str) -> str:
    """各家工具的回复可能是 JSON 包装（agy 用 response，Cursor 用 result），只取里面的文字。"""
    text = (output or "").strip()
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


def _output_mtime(out) -> float:
    try:
        return os.fstat(out.fileno()).st_mtime
    except (OSError, ValueError):
        return 0.0


def watched_runner(cmd, cwd, timeout, quiet_limit=QUIET_LIMIT, poll=POLL_SECONDS, live_path=None,
                   on_quiet=None, remind_at=REMIND_AT):
    """边跑边看：总时长超过 timeout 抛 TimeoutExpired；连续 quiet_limit 秒没有动静抛 QuietTimeout。
    “动静”= 副本里有文件变化，或者它的输出还在涨（只看不改的复查员靠这个，68b 里被误杀过，㉓）。
    没动静到 remind_at 里的每个时刻调用一次 on_quiet(秒数)。输出写文件，避免管道写满卡住；
    live_path 给了就写到那里并保留，供实时查看。"""
    global _active_tool
    started = time.time()
    last_activity = started
    reminded = set()
    with _output_file(live_path) as out:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, text=True, start_new_session=True)
        _active_tool = proc
        try:
            while proc.poll() is None:
                try:
                    proc.wait(timeout=poll)
                except subprocess.TimeoutExpired:
                    pass
                now = time.time()
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
        runner=watched_runner, models=None, live_path=None, on_quiet=None) -> AgentResult:
    """让某家工具执行一次任务。成功条件：退出码 0、有回复文字，且（若指定）期望文件真的存在。
    models 缺省时用包默认值；派发程序应传入 load_models(项目目录) 的结果。
    默认带看门人：长时间没动静会被停掉，并且不重试（卡住的再跑一遍多半还是卡住）。
    live_path：实时输出写到哪里（流转记录预留的位置），不给就不保留。
    退出码 0 却一个字都没回（68b 里 Cursor 收到超长交代时就这样）：再试一次，还空就算没完成。"""
    extra = {"live_path": live_path} if live_path else {}  # 旧的 runner 不认这个参数，不给就不传
    if on_quiet:
        extra["on_quiet"] = on_quiet
    output = ""
    with tempfile.TemporaryDirectory(prefix="collab-last-") as tmp:
        last_path = Path(tmp) / "最后回复.txt"
        cmd = _build(adapter, prompt, models or DEFAULT_MODELS, last_path=last_path)  # 未知适配器直接报错
        for attempt in range(1, MAX_ATTEMPTS + 1):
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
                final = _codex_last(output) if adapter == "codex" else readable(output)
            if expect_file is None and not final.strip():  # 检测连通看的是建没建出文件，不看回话
                output = (output or "") + f"\n{EMPTY_REPLY}"
                continue
            if expect_file is None or (Path(cwd) / expect_file).exists():
                return AgentResult(ok=True, output=output, attempts=attempt, final=final)
    return AgentResult(ok=False, output=output, attempts=MAX_ATTEMPTS)
