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
        real = shutil.which(name)
        if real and not (folder / name).exists():
            os.symlink(real, folder / name)
    return f"{folder}:/usr/bin:/bin:/usr/sbin:/sbin"


def safe_env(folder: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "SUPERAGENT_QWEN_KEY"}
    env.update(PATH=_safe_bin(folder), SUPERAGENT_NO_HANDOVER="1", SUPERAGENT_NO_FULL="1",
               SUPERAGENT_NO_PROGRESS="1", SUPERAGENT_NO_DRAFT="1", PYTHONDONTWRITEBYTECODE="1")
    return env


def copy_tree(src, dst) -> None:
    """rsync 一份（排除 .git、依赖、运行记录），再在副本里建一个新仓库。"""
    args = ["rsync", "-a", "--delete"]
    for ex in _EXCLUDES:
        args += ["--exclude", ex]
    subprocess.run(args + [f"{src}/", f"{dst}/"], check=True, capture_output=True)
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"],
                ["git", "-c", "user.name=collab", "-c", "user.email=collab@local", "commit", "-qm", "副本"]):
        subprocess.run(cmd, cwd=str(dst), capture_output=True)


def run_command(cmd, cwd, env, timeout, stop=None, log=None) -> Verdict:
    """整组跑；被叫停或超时就连同子进程一起停掉。输出全留（全量的报错在末尾，不能截到 4000 字）。"""
    out = open(log, "w+", encoding="utf-8") if log else tempfile.TemporaryFile("w+", encoding="utf-8")
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
        out.close()
    if proc.returncode == 0:
        return Verdict(PASS, 0, text)
    return Verdict(ENV_ERROR if proc.returncode in (126, 127) else FAIL, proc.returncode, text)


def failing_names(output: str) -> set:
    return set(re.findall(r"^(?:FAIL|ERROR): (\S+ \([^)]*\))", output or "", re.M))


def failing_files(output: str) -> dict:
    """没过的项按测试文件分组：{tests/x.py: {项, …}}。导入就报错的（unittest.loader._FailedTest）项名就是模块名。"""
    files: dict = {}
    for name in failing_names(output):
        test, _, where = name.partition(" (")
        where = where.rstrip(")")
        module = test if where.startswith("unittest.loader") else where.rsplit(".", 1)[0]
        files.setdefault("tests/" + module.replace(".", "/") + ".py", set()).add(name)
    return files


class FullRunner:
    """run(worktree, stop) 跑全量；base(worktree, files, stop) 在改之前的样子上逐个跑这些文件。"""

    def __init__(self, timeout: int = FULL_TIMEOUT):
        self.timeout = timeout

    def _python(self) -> str:
        return shutil.which("python3") or sys.executable

    def run(self, worktree, stop=None) -> Verdict:
        tmp = Path(tempfile.mkdtemp(prefix="collab-full-"))
        try:
            copy = tmp / "copy"
            copy_tree(worktree, copy)
            return run_command([self._python(), "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
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
                if not (copy / p).is_file():
                    results[rel] = Verdict(ENV_ERROR, None, "改之前没有这个测试文件")
                    continue
                results[rel] = run_command([self._python(), "-m", "unittest", "discover", "-s", str(p.parent),
                                            "-p", p.name], copy, env, self.timeout, stop)
            return results
        except subprocess.CalledProcessError:
            return results
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(head)], cwd=str(worktree),
                           capture_output=True)
            subprocess.run(["git", "worktree", "prune"], cwd=str(worktree), capture_output=True)
            shutil.rmtree(tmp, ignore_errors=True)


def culprits(now: Verdict, base: dict) -> list:
    """现在没过的文件里，哪些是这次改坏的：改之前过、跑不起来、没有这个文件，或者同一文件里多坏了几项。"""
    out = []
    for rel, names in sorted(failing_files(now.output).items()):
        before = base.get(rel)
        if before is None or before.result != FAIL or names - failing_names(before.output):
            out.append(rel)
    return out
