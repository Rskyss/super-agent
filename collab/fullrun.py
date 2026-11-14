"""复查的同时跑全量测试（流程修理第四轮 59）。

69b 复查通过后，Claude 跑全量才发现改坏了 3 项老测试；流程自己只跑本卡测试和“相关老测试”。
这里把 Claude 最后那一步搬进流程：
- 在一份副本里跑（不碰写代码的那份），副本里新建 git 仓库（有些老测试要用）；
- PATH 里只放 python3、node 和系统目录，真的 AI 工具（codex、cursor、agy……）找不到，老测试不会误启动它们；
- 只认 Python 项目（tests/ 下有 test_*.py），别的项目跳过；
- 没过时逐个文件在“改之前”（施工副本的 HEAD）再跑一次，只把这次改坏的算上（照第三批 _broken_by_change 的办法）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from collab.judge import ENV_ERROR, FAIL, PASS, TIMEOUT, Verdict, _stop_group

FULL_TIMEOUT = 1800
STOPPED = "停了"
_EXCLUDES = (".git", "node_modules", ".collab", "__pycache__", "*.app", ".venv", "venv")


def applicable(worktree) -> bool:
    """和全量实际跑的范围一致（discover -s tests）：顶层的 test_*.py，加上带 __init__.py 的子文件夹里的。
    没有 __init__.py 的子文件夹 unittest 不进去，项目自己的测试命令也跑不到，这里也不算（复查第 3 轮）。"""
    tests = Path(worktree) / "tests"
    if not tests.is_dir():
        return False
    for path in tests.rglob("test_*.py"):
        parent = path.parent
        while parent != tests and (parent / "__init__.py").is_file():
            parent = parent.parent
        if parent == tests:
            return True
    return False


_GROUPS: set = set()   # 本进程拉起的独立进程组（复制、跑测试）；停工时一把收掉


_EXITING = {"on": False}


def kill_all_groups() -> None:
    """停工信号来了：之后不再起新的准备命令；登记的独立进程组连同它们另开的进程组一次性强停，不等
    （复查第 3 轮：一组组温和停再等，总时长会超过桌面 8 秒的强杀期限）。"""
    import signal
    _EXITING["on"] = True
    for pgid in list(_GROUPS):
        for pid in _descendants(pgid) + [pgid]:
            for kill in (os.killpg, os.kill):
                try:
                    kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
        _GROUPS.discard(pgid)


def _descendants(pid: int) -> list:
    """一个进程的所有后代（包括它们自己另开新进程组的，例如测试里起的工具）。"""
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    kids: dict = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            kids.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, todo = [], [pid]
    while todo:
        for child in kids.get(todo.pop(), []):
            found.append(child)
            todo.append(child)
    return found


def _safe_bin(folder: Path) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("python3", "node"):
        real = sys.executable if name == "python3" else shutil.which(name)   # python3 用跑超级助手的这一个，不靠 pyenv 垫片
        if real and not (folder / name).exists():
            os.symlink(real, folder / name)
    return f"{folder}:/usr/bin:/bin:/usr/sbin:/sbin"


def safe_env(folder: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "SUPERAGENT_QWEN_KEY"}
    env.update(PATH=_safe_bin(folder), SUPERAGENT_NO_HANDOVER="1", SUPERAGENT_NO_FULL="1",
               SUPERAGENT_NO_PROGRESS="1", SUPERAGENT_NO_DRAFT="1", PYTHONDONTWRITEBYTECODE="1")
    return env


def copy_tree(src, dst, stop=None) -> None:
    """复制一份再在副本里建一个新仓库。是 git 仓库就只复制仓库里的文件和新建的（不含 .gitignore 忽略的
    编译产物、密钥文件——本项目 desktop/mac 下的编译产物有 1.6G）；不是就 rsync 并排除 .git、依赖、运行记录。
    每条命令都有时限、会响应叫停。"""
    Path(dst).mkdir(parents=True, exist_ok=True)
    code, listed = _quick(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=str(src), stop=stop,
                          capture=True)
    args = ["rsync", "-a"]
    for ex in _EXCLUDES:
        args += ["--exclude", ex]
    if code == 0:
        names = [n for n in listed.split(b"\0") if n and (Path(src) / n.decode("utf-8", "replace")).exists()]
        with tempfile.NamedTemporaryFile("wb", delete=False) as fh:
            fh.write(b"\0".join(names))
            listing = fh.name
        try:
            _quick(args + ["--from0", f"--files-from={listing}", f"{src}/", f"{dst}/"], check=True, stop=stop)
        finally:
            os.unlink(listing)
    else:
        _quick(args + [f"{src}/", f"{dst}/"], check=True, stop=stop)
    quiet = ["-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "core.fsmonitor=false"]  # 不留后台整理进程
    for cmd in (["git", "init", "-q"], ["git", *quiet, "add", "-A"],
                ["git", *quiet, "-c", "user.name=collab", "-c", "user.email=collab@local", "commit", "-qm", "副本"]):
        _quick(cmd, cwd=str(dst), stop=stop)


COPY_TIMEOUT = 300


def _quick(cmd, cwd=None, check=False, stop=None, capture=False):
    """复制副本、git 这类准备命令：有时限、自成一组、登记在案、会响应叫停（复查第 2 轮）。"""
    if _EXITING["on"]:   # 停工了：不再起新的（清理留给下次 git worktree prune）
        raise subprocess.CalledProcessError(-1, cmd, stderr="停工了".encode())
    out = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=out if capture else subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    _GROUPS.add(proc.pid)
    try:
        deadline = time.time() + COPY_TIMEOUT
        while proc.poll() is None:
            if (stop is not None and stop.is_set()) or time.time() > deadline:
                raise subprocess.CalledProcessError(-1, cmd, stderr="叫停或超时".encode())
            time.sleep(0.1)
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd)
        out.seek(0)
        return proc.returncode, out.read()
    finally:
        _kill_group(proc.pid)
        _GROUPS.discard(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        out.close()


def _kill_group(pgid: int) -> None:
    """正常退出也把整组收掉：测试自己拉起的后台进程不能留下（复查：sleep 20 还活着）；
    它们另开新进程组的后代也一并收（复查第 3 轮：测试里用 start_new_session 起的工具）。"""
    import signal
    for pid in _descendants(pgid):
        try:
            pgid_of = os.getpgid(pid)
        except (ProcessLookupError, PermissionError, OSError):
            continue
        if pgid_of != pgid and pgid_of != os.getpgid(0):
            try:
                os.killpg(pgid_of, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            return
        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                os.killpg(pgid, 0)
            except (ProcessLookupError, PermissionError, OSError):
                return
            time.sleep(0.1)


def run_command(cmd, cwd, env, timeout, stop=None, log=None) -> Verdict:
    """整组跑；被叫停或超时就连同子进程一起停掉。输出全留（全量的报错在末尾，不能截到 4000 字）。"""
    out = open(log, "w+", encoding="utf-8") if log else tempfile.TemporaryFile("w+", encoding="utf-8")
    proc = None
    seen = set()
    try:
        try:
            proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=out, stderr=subprocess.STDOUT, text=True,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        except FileNotFoundError as exc:
            return Verdict(ENV_ERROR, None, f"找不到命令：{exc}")
        _GROUPS.add(proc.pid)
        deadline = time.time() + timeout
        seen = set()   # 跑的过程中看到的后代进程组：父进程先退了，它们成了孤儿也要收（复查第 3 轮）
        last_scan = 0.0
        while proc.poll() is None:
            if time.time() - last_scan > 1:
                last_scan = time.time()
                for pid in _descendants(proc.pid):
                    try:
                        seen.add(os.getpgid(pid))
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
            if stop is not None and stop.is_set():
                _stop_group(proc)
                return Verdict(STOPPED, None, "")
            if time.time() > deadline:
                _stop_group(proc)
                out.seek(0)
                return Verdict(TIMEOUT, None, out.read()[-8000:])
            time.sleep(0.5)
        out.seek(0)
        text = out.read()
    finally:
        if proc is not None:
            _kill_group(proc.pid)   # 不管怎么结束（正常、出错、被叫停、外层异常），整组都收掉
            for pgid in seen - {proc.pid, os.getpgid(0)}:
                _kill_group(pgid)
            _GROUPS.discard(proc.pid)
        out.close()
    if proc.returncode == 0:
        return Verdict(PASS, 0, text)
    return Verdict(ENV_ERROR if proc.returncode in (126, 127) else FAIL, proc.returncode, text)


_FAIL_LINE = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([^)]*)\)(.*)$", re.M)


def failing_names(output: str) -> list:
    """没过的每一项（含子测试参数，同一项坏几次就算几次）：["test_a (test_old.T) (x=1)", …]。"""
    return [f"{m.group(1)} ({m.group(2)}){m.group(3).rstrip()}" for m in _FAIL_LINE.finditer(output or "")]


def _test_file(test: str, where: str, root) -> str:
    """unittest 写的位置 → tests/ 下的测试文件。兼容老写法 test_a (mod.Class) 和新写法 test_a (mod.Class.test_a)
    （复查：本机 Python 3.15 是新写法）；导入就报错（unittest.loader._FailedTest）时项名就是模块名。
    给了项目就按 tests/ 下真有的文件找最长的那段模块名。"""
    loader = where.startswith("unittest.loader")
    parts = (test if loader else where).split(".")
    if root is not None:
        for n in range(len(parts), 0, -1):
            rel = "tests/" + "/".join(parts[:n]) + ".py"
            if (Path(root) / rel).is_file():
                return rel
    if not loader:
        if len(parts) > 1 and parts[-1] == test:   # 新写法末尾是方法名
            parts = parts[:-1]
        if len(parts) > 1 and parts[-1][:1].isupper():   # 再去掉类名
            parts = parts[:-1]
    return "tests/" + "/".join(parts) + ".py"


def failing_files(output: str, root=None) -> dict:
    """没过的项按测试文件分组：{tests/x.py: [项, …]}。"""
    files: dict = {}
    for m in _FAIL_LINE.finditer(output or ""):
        name = f"{m.group(1)} ({m.group(2)}){m.group(3).rstrip()}"
        files.setdefault(_test_file(m.group(1), m.group(2), root), []).append(name)
    return files


class FullRunner:
    """run(worktree, stop) 跑全量；base(worktree, files, stop) 在改之前的样子上逐个跑这些文件。"""

    def __init__(self, timeout: int = FULL_TIMEOUT):
        self.timeout = timeout

    def run(self, worktree, stop=None) -> Verdict:
        tmp = Path(tempfile.mkdtemp(prefix="collab-full-"))
        try:
            copy = tmp / "copy"
            copy_tree(worktree, copy, stop)
            if stop is not None and stop.is_set():
                return Verdict(STOPPED, None, "")
            return run_command(["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
                               copy, safe_env(tmp / "bin"), self.timeout, stop)
        except subprocess.CalledProcessError as exc:
            if stop is not None and stop.is_set():
                return Verdict(STOPPED, None, "")
            return Verdict(ENV_ERROR, None, f"复制副本没成：{exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def base(self, worktree, files, stop=None) -> dict:
        tmp = Path(tempfile.mkdtemp(prefix="collab-full-base-"))
        head = tmp / "head"
        results = {}
        try:
            code, _ = _quick(["git", "worktree", "add", "-q", "--detach", str(head), "HEAD"], cwd=str(worktree),
                             stop=stop)
            if code != 0:
                return {}
            copy = tmp / "copy"
            copy_tree(head, copy, stop)
            env = safe_env(tmp / "bin")
            for rel in files:
                p = Path(rel)
                if stop is not None and stop.is_set():
                    break
                if not (copy / p).is_file():
                    results[rel] = Verdict(ENV_ERROR, None, "改之前没有这个测试文件")
                    continue
                # 和全量同一个起点（-s tests），子文件夹里的测试名字才对得上（复查：sub.test_x 和 test_x）
                results[rel] = run_command(["python3", "-m", "unittest", "discover", "-s", "tests",
                                            "-p", p.name], copy, env, self.timeout, stop)
            return results
        except subprocess.CalledProcessError:
            return results
        finally:
            for cmd in (["git", "worktree", "remove", "--force", str(head)], ["git", "worktree", "prune"]):
                try:
                    _quick(cmd, cwd=str(worktree))
                except subprocess.CalledProcessError:
                    pass
            shutil.rmtree(tmp, ignore_errors=True)


def summary_count(output: str):
    """unittest 汇总里让它判失败的项数（失败、出错、意外通过）；没有汇总返回 None。"""
    m = re.search(r"FAILED \(([^)]*)\)", output or "")
    if not m:
        return None
    return sum(int(n) for n in re.findall(r"(?:failures|errors|unexpected successes)=(\d+)", m.group(1)))


def culprits(now: Verdict, base: dict, root=None) -> list:
    """现在没过的文件里，哪些是这次改坏的：改之前过、跑不起来、没有这个文件，或者同一文件里多坏了（按项数比，
    同一个方法里的子测试多坏一个也算，复查第 2 条）。"""
    from collections import Counter
    out = []
    for rel, names in sorted(failing_files(now.output, root).items()):
        before = base.get(rel)
        if before is None or before.result != FAIL:
            out.append(rel)
            continue
        was = Counter(n for names_b in failing_files(before.output, root).values() for n in names_b)
        if Counter(names) - was:
            out.append(rel)
    return out
