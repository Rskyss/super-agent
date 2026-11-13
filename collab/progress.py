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

PROGRESS_FILE = "进度.json"
MIN_GAP = 120        # 两次之间至少隔 2 分钟（用户 11-13 定）
CHECK_EVERY = 5      # 多久看一次有没有新改的文件
RUN_TIMEOUT = 600
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".collab", ".venv", "venv"}


def count_written(worktree, files) -> int:
    n = 0
    for rel in files:
        try:
            n += len(re.findall(r"^\s*(?:async\s+)?def test_", (Path(worktree) / rel).read_text(
                encoding="utf-8", errors="replace"), re.M))
        except OSError:
            continue
    return n


def tally(output: str):
    """unittest 输出 → (通过, 总数)；认不出返回 None。"""
    m = re.search(r"^Ran (\d+) tests?", output or "", re.M)
    if not m:
        return None
    total = int(m.group(1))
    bad = re.search(r"FAILED \(([^)]*)\)", output or "")
    failed = sum(int(n) for n in re.findall(r"(?:failures|errors|unexpected successes)=(\d+)", bad.group(1))) if bad else 0
    skipped = re.search(r"skipped=(\d+)", output or "")
    return max(0, total - failed - (int(skipped.group(1)) if skipped else 0)), total


def latest_change(worktree, ignore=()) -> float:
    """工作副本里（本卡测试文件以外）最新改动的时间。"""
    root = Path(worktree)
    newest = 0.0
    skip = {str(root / p) for p in ignore}
    for path in root.rglob("*"):
        parts = path.relative_to(root).parts
        if any(part in _SKIP_DIRS or part.endswith(".app") for part in parts) or not path.is_file():
            continue
        if str(path) in skip:
            continue
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


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
        self.runs = 0

    # ---- 写测试：数 def test_ ----
    def _tester_files(self):
        root = self.worktree / "tests"
        if not root.is_dir():
            return []
        out = []
        for p in root.rglob("test_*"):
            try:
                if p.is_file() and p.stat().st_mtime >= self.started - 1:
                    out.append(p.relative_to(self.worktree).as_posix())
            except OSError:
                continue
        return out

    def due(self, now: float, changed: float) -> bool:
        """第一次马上算；之后要有新改动（本卡测试文件以外）、且离上次至少 gap 秒。"""
        if self.last_run is None:
            return True
        return changed > self.last_run and now - self.last_run >= self.gap

    def compute(self) -> dict:
        tmp = Path(tempfile.mkdtemp(prefix="collab-progress-"))
        try:
            copy = tmp / "copy"
            copy_tree(self.worktree, copy)
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
                got = tally(v.output)
                if got is None or "unittest.loader._FailedTest" in (v.output or ""):
                    # 导入就报错（代码还没写出来）：总数照测试文件里写了几项算，通过 0
                    got = (0, count_written(copy, [rel]))
                passed, total = passed + got[0], total + got[1]
            return {"seq": self.seq, "step": self.step, "passed": passed, "total": total}
        except Exception:  # noqa: BLE001 — 算不出就不显示，不影响干活
            return {}
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
        if not self.due(now, latest_change(self.worktree, ignore=self.files)):
            return
        self.last_run = now
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
        self.event.set()
        if self.thread is not None:
            self.thread.join(30)
