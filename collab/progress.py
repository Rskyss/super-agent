"""右栏“写代码”那步的进度条（流程修理第四轮 53）。

写代码时：程序最多每 2 分钟在一份副本里跑一次本卡的 .py 测试（只跑写测试交出的那几个文件），
算“通过数/总数”写进 cards/<卡>/进度.json；写测试时：数本卡测试文件里有几个 def test_，写“已写 N 项”。
副本跑，不碰写代码的那份、不和它自己跑测试抢；PATH 里藏起真的 AI 工具（照全量的办法）。
说的是“测试过了多少”，不是时间进度。
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from collab.fullrun import copy_tree, run_command, safe_env
from collab.judge import ENV_ERROR, TIMEOUT

PROGRESS_FILE = "进度.json"
MIN_GAP = 120        # 两次之间至少隔 2 分钟（用户 11-13 定）
CHECK_EVERY = 5      # 多久看一次有没有新改的文件
RUN_TIMEOUT = 600
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".collab", ".venv", "venv"}


def count_written(worktree, files) -> int:
    n = 0
    for rel in files:
        try:
            n += len(re.findall(r"^\s*(?:async\s+)?def test_|^\s*(?:it|test)\(", (Path(worktree) / rel).read_text(
                encoding="utf-8", errors="replace"), re.M))
        except OSError:
            continue
    return n


def tally(output: str):
    """unittest 输出 → (通过, 总数)；认不出返回 None。同一个方法里几个子测试没过，只算这个方法没过一次（复查第 2 轮）。"""
    m = re.search(r"^Ran (\d+) tests?", output or "", re.M)
    if not m:
        return None
    total = int(m.group(1))
    names = {f"{a} ({b})" for a, b in re.findall(r"^(?:FAIL|ERROR): (\S+) \(([^)]*)\)", output or "", re.M)}
    bad = re.search(r"FAILED \(([^)]*)\)", output or "")
    unexpected = re.search(r"unexpected successes=(\d+)", bad.group(1)) if bad else None
    failed = len(names) + (int(unexpected.group(1)) if unexpected else 0)
    if bad and not names and not unexpected:   # 有汇总却列不出名字：照汇总扣
        failed = sum(int(n) for n in re.findall(r"(?:failures|errors)=(\d+)", bad.group(1)))
    skipped = re.search(r"skipped=(\d+)", output or "")
    return max(0, total - failed - (int(skipped.group(1)) if skipped else 0)), total


def snapshot(worktree, ignore=()) -> frozenset:
    """工作副本里（本卡测试文件以外）每个文件的（路径、修改时间、大小）。新增、改动、删除都会让它变（复查：只删文件时进度不动）。
    边走边剪掉 .git、依赖、编译产物文件夹，不钻进去（复查：大项目每 5 秒扫一遍很慢）。"""
    import os
    root = str(worktree)
    skip = {str(Path(root) / p) for p in ignore}
    items = []
    for here, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.endswith(".app")]
        for name in files:
            path = os.path.join(here, name)
            if path in skip:
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            items.append((path, st.st_mtime_ns, st.st_size))
    return frozenset(items)


def write(cdir, data: dict) -> None:
    path = Path(cdir) / PROGRESS_FILE
    spare = path.with_suffix(".tmp")
    spare.write_text(json.dumps(dict(data, time=datetime.now().isoformat(timespec="seconds")), ensure_ascii=False),
                     encoding="utf-8")
    spare.replace(path)


def read(cdir) -> dict:
    try:
        data = json.loads((Path(cdir) / PROGRESS_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


class ProgressJob:
    """一次派活一个：start() 开始，stop() 收掉（连同正在跑的测试）。"""

    def __init__(self, cdir, worktree, step: str, seq: int, test_files, gap: int = MIN_GAP,
                 every: float = CHECK_EVERY):
        self.cdir, self.worktree, self.step, self.seq = Path(cdir), Path(worktree), step, seq
        self.files = [f for f in (test_files or [])]
        self.py = [f for f in self.files if Path(f).name.startswith("test_") and f.endswith(".py")]
        self.gap, self.every = gap, every
        self.event = threading.Event()
        self.thread = None
        self.started = time.time()
        self.last_run = None     # 上次开始算的时间
        self.last_seen = None    # 上次算时的文件快照
        self.runs = 0

    # ---- 写测试：数 def test_ ----
    def _tester_files(self):
        """写测试那步新写、新改的测试文件：整个工作副本里 test_*.py、*_test.py、*.test.js 这类（复查第 2 轮：只看 tests/ 会漏）。"""
        import os
        out = []
        for here, dirs, files in os.walk(self.worktree):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.endswith(".app")]
            for name in files:
                if not (name.startswith("test_") or re.search(r"[._-]test\.\w+$|[._-]spec\.\w+$", name)):
                    continue
                path = os.path.join(here, name)
                try:
                    if os.stat(path).st_mtime >= self.started - 1:
                        out.append(os.path.relpath(path, self.worktree))
                except OSError:
                    continue
        return out

    def due(self, now: float, changed: bool) -> bool:
        """第一次马上算；之后要离上次至少 gap 秒、且有新改动（本卡测试文件以外）。"""
        if self.last_run is None:
            return True
        return now - self.last_run >= self.gap and changed

    def compute(self) -> dict:
        tmp = Path(tempfile.mkdtemp(prefix="collab-progress-"))
        try:
            copy = tmp / "copy"
            copy_tree(self.worktree, copy, self.event)
            env = safe_env(tmp / "bin")
            passed = total = 0
            for rel in self.py:
                p = Path(rel)
                if not (copy / p).is_file():
                    continue
                v = run_command(["python3", "-m", "unittest", "discover", "-s", str(p.parent), "-p", p.name],
                                copy, env, RUN_TIMEOUT, self.event)
                if self.event.is_set():
                    return {}
                if v.result in (TIMEOUT, ENV_ERROR):   # 超时、跑不起来不能装成正常进度（复查）
                    return {"seq": self.seq, "step": self.step,
                            "error": "测试跑超时了" if v.result == TIMEOUT else "测试跑不起来"}
                got = tally(v.output)
                if got is None or "unittest.loader._FailedTest" in (v.output or ""):
                    # 导入就报错：照方案说“跑不起来”，不装成“通过 0/N”（复查第 2 轮）
                    return {"seq": self.seq, "step": self.step,
                            "error": "测试跑不起来（多半是代码还没写到能导入）"}
                passed, total = passed + got[0], total + got[1]
            return {"seq": self.seq, "step": self.step, "passed": passed, "total": total}
        except Exception:  # noqa: BLE001 — 算不出就说一声，不影响干活
            return {"seq": self.seq, "step": self.step, "error": "准备副本没成"}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def tick(self) -> None:
        if self.step == "写测试":
            write(self.cdir, {"seq": self.seq, "step": self.step,
                              "written": count_written(self.worktree, self._tester_files())})
            return
        if not self.py:
            return
        now = time.time()
        if self.last_run is not None and now - self.last_run < self.gap:
            return   # 不到时间连文件都不扫
        seen = snapshot(self.worktree, ignore=self.files)
        if not self.due(now, seen != self.last_seen):
            return
        self.last_run, self.last_seen = now, seen
        data = self.compute()
        if data and not self.event.is_set():
            self.runs += 1
            write(self.cdir, data)

    def _loop(self):
        if self.step != "写测试" and self.py:
            write(self.cdir, {"seq": self.seq, "step": self.step, "computing": True})
        while not self.event.is_set():
            self.tick()
            self.event.wait(self.every)

    def start(self):
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        """叫停并等它真的收完：复制有时限、测试会响应叫停（复查：只等 30 秒，线程可能还在）。"""
        self.event.set()
        if self.thread is not None:
            self.thread.join()
