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
    tests = Path(worktree) / "tests"
    return tests.is_dir() and any(tests.glob("test_*.py"))


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


def copy_tree(src, dst) -> None:
    """复制一份再在副本里建一个新仓库。是 git 仓库就只复制仓库里的文件和新建的（不含 .gitignore 忽略的
    编译产物、密钥文件——本项目 desktop/mac 下的编译产物有 1.6G）；不是就 rsync 并排除 .git、依赖、运行记录。"""
    Path(dst).mkdir(parents=True, exist_ok=True)
    listed = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=str(src),
                            capture_output=True)
    args = ["rsync", "-a"]
    for ex in _EXCLUDES:
        args += ["--exclude", ex]
    if listed.returncode == 0:
        names = [n for n in listed.stdout.split(b"\0") if n and (Path(src) / n.decode("utf-8", "replace")).exists()]
        with tempfile.NamedTemporaryFile("wb", delete=False) as fh:
            fh.write(b"\0".join(names))
            listing = fh.name
        try:
            _quick(args + ["--from0", f"--files-from={listing}", f"{src}/", f"{dst}/"], check=True)
        finally:
            os.unlink(listing)
    else:
        _quick(args + [f"{src}/", f"{dst}/"], check=True)
    quiet = ["-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "core.fsmonitor=false"]  # 不留后台整理进程
    for cmd in (["git", "init", "-q"], ["git", *quiet, "add", "-A"],
                ["git", *quiet, "-c", "user.name=collab", "-c", "user.email=collab@local", "commit", "-qm", "副本"]):
        _quick(cmd, cwd=str(dst))


COPY_TIMEOUT = 300


def _quick(cmd, cwd=None, check=False):
    """复制副本用的命令：有时限、自成一组，超时连同子进程一起停（复查：大项目复制慢时线程收不掉）。"""
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    try:
        _, err = proc.communicate(timeout=COPY_TIMEOUT)
    except subprocess.TimeoutExpired:
        _stop_group(proc)
        raise subprocess.CalledProcessError(-1, cmd, stderr="超时".encode())
    finally:
        _kill_group(proc.pid)
    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, cmd, stderr=err)
    return proc.returncode


def _kill_group(pgid: int) -> None:
    """正常退出也把整组收掉：测试自己拉起的后台进程不能留下（复查：sleep 20 还活着）。"""
    import signal
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
    try:
        try:
            proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdout=out, stderr=subprocess.STDOUT, text=True,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
        except FileNotFoundError as exc:
            return Verdict(ENV_ERROR, None, f"找不到命令：{exc}")
        deadline = time.time() + timeout
        while proc.poll() is None:
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
    keep = parts if loader or len(parts) == 1 else parts[:-1]
    return "tests/" + "/".join(keep) + ".py"


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
            copy_tree(worktree, copy)
            return run_command(["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
                               copy, safe_env(tmp / "bin"), self.timeout, stop)
        except subprocess.CalledProcessError as exc:
            return Verdict(ENV_ERROR, None, f"复制副本没成：{exc}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def base(self, worktree, files, stop=None) -> dict:
        tmp = Path(tempfile.mkdtemp(prefix="collab-full-base-"))
        head = tmp / "head"
        results = {}
        try:
            add = subprocess.run(["git", "worktree", "add", "-q", "--detach", str(head), "HEAD"], cwd=str(worktree),
                                 capture_output=True, text=True)
            if add.returncode != 0:
                return {}
            copy = tmp / "copy"
            copy_tree(head, copy)
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
            subprocess.run(["git", "worktree", "remove", "--force", str(head)], cwd=str(worktree),
                           capture_output=True)
            subprocess.run(["git", "worktree", "prune"], cwd=str(worktree), capture_output=True)
            shutil.rmtree(tmp, ignore_errors=True)


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
