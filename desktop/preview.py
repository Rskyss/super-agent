"""打开改好的版本：在副本里启动方案写的命令，或只打开文件夹。

同一时间只有一个预览。卡片离开“等你确认”后自动结束。
命令来自 开工.json，用户批准方案时看过；运行的是模型写的代码，所以不把本机服务的口令传给它。
"""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from collab.phase import card_phase, process_is_alive, process_start
from collab.planner import is_local_url

__all__ = ["PreviewHub", "is_local_url"]

_OPEN_STAGES = ("review", "merge_failed")
_RECORD = "预览进程.json"
_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机地址不能走代理


def _worktree(project: str, card_id: str) -> Path:
    root = Path(project)
    return root.parent / f"{root.name}-worktrees" / card_id


def _reachable(url: str) -> bool:
    try:
        _NO_PROXY.open(url, timeout=1)
        return True
    except urllib.error.HTTPError:
        return True  # 有回应就算起来了
    except Exception:
        return False


def _ignored_in_project(project: Path, rel: str) -> bool:
    """项目本来就忽略的依赖才能借：否则可能是要提交的文件，不能把它藏起来。"""
    try:
        return subprocess.run(["git", "check-ignore", "-q", rel + "/"], cwd=str(project),
                              capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _hide_from_git(copy: Path, rels: list) -> None:
    """.gitignore 的 node_modules/ 不匹配软链接，要单独告诉 git，否则会被当成改动。
    只写借的这几个路径，它们在项目里本来就被忽略，对真实项目没有影响。"""
    try:
        path = subprocess.run(["git", "rev-parse", "--git-path", "info/exclude"], cwd=str(copy),
                              capture_output=True, text=True, timeout=5, check=True).stdout.strip()
        exclude = (copy / path).resolve()
        lines = exclude.read_text(encoding="utf-8").splitlines() if exclude.is_file() else []
        fresh = ["/" + rel for rel in rels if "/" + rel not in lines]
        if fresh:
            exclude.parent.mkdir(parents=True, exist_ok=True)
            exclude.write_text("\n".join(lines + fresh) + "\n", encoding="utf-8")
    except (OSError, ValueError, subprocess.SubprocessError):
        pass


def _link_node_modules(project: Path, copy: Path) -> None:
    """把项目里 3 层以内的 node_modules 链到副本相同位置；副本没有上一级就跳过，也不链到副本外面。"""
    root = project.resolve()
    inside = copy.resolve()
    linked = []

    def walk(here: Path, depth: int) -> None:
        if depth > 3:
            return
        try:
            kids = list(here.iterdir())
        except OSError:
            return
        for child in kids:
            if child.name == ".git":
                continue
            if child.name == "node_modules":
                if child.is_dir():
                    rel = child.relative_to(root)
                    parent = copy / rel.parent
                    dst = copy / rel
                    within = parent.resolve() == inside or inside in parent.resolve().parents
                    if (parent.is_dir() and within and not dst.exists() and not dst.is_symlink()
                            and _ignored_in_project(root, rel.as_posix())):
                        try:
                            os.symlink(child, dst)
                            linked.append(rel.as_posix())
                        except OSError:
                            pass
                continue
            if child.is_dir() and not child.is_symlink():
                walk(child, depth + 1)

    walk(root, 0)
    if linked:
        _hide_from_git(copy, linked)


def _group_alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True  # 没权限等情况，当它还在


def _end_group(pid: int, wait: float = 3, proc=None) -> None:
    """先礼后兵：组长退了子进程还可能在，要看整组。"""
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.time() + wait
    while time.time() < deadline:
        if proc is not None:
            proc.poll()  # 收掉已退出的组长，不然它一直算“还活着”，白等满时间
        if not _group_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


class PreviewHub:
    def __init__(self, wait_seconds: float = 45):
        self.wait_seconds = wait_seconds
        self._lock = threading.RLock()
        self._finish_lock = threading.RLock()  # 窗口退出时信号和“父进程没了”两条路都会来，后来的要等收尾做完
        self._gen = 0  # 每次开始或结束加一：旧线程发现变了就只收拾自己，不碰现在的预览
        self._proc: Optional[subprocess.Popen] = None
        self._project = ""
        self._card = ""
        self._url = ""
        self._record: Optional[Path] = None

    def _take(self) -> tuple:
        """取走当前预览并清空状态。调用方在锁里。"""
        taken = (self._proc, self._record)
        self._gen += 1
        self._proc, self._record = None, None
        self._project, self._card, self._url = "", "", ""
        return taken

    def _finish(self, proc, record) -> None:
        with self._finish_lock:
            if proc is not None:
                _end_group(proc.pid, proc=proc)
                try:
                    proc.wait(timeout=2)
                except Exception:
                    pass
            if record is not None:
                try:
                    record.unlink()
                except OSError:
                    pass

    def stop(self) -> None:
        with self._lock:
            proc, record = self._take()
        self._finish(proc, record)

    def status(self, project: str, card_id: str) -> Dict[str, Any]:
        with self._lock:
            running = (self._proc is not None and self._proc.poll() is None
                       and self._project == str(project) and self._card == card_id)
            return {"running": running, "url": self._url if running else ""}

    @staticmethod
    def _clear_leftover(record: Path) -> None:
        """上次本机服务被强杀留下的预览：确认还是那个进程才结束它。"""
        try:
            saved = json.loads(record.read_text(encoding="utf-8"))
            pid, started = int(saved["pid"]), saved.get("started")
        except (OSError, ValueError, KeyError, TypeError):
            return
        if started and process_is_alive(pid, started):  # 对不上启动时间就可能是别的程序，不能动
            _end_group(pid)
        try:
            record.unlink()
        except OSError:
            pass

    def _watch(self, gen: int, project: str, card_id: str) -> None:
        """卡片不管怎么离开“等你确认”（聊天里确认继续改、放进项目成功），预览都要结束。"""
        while True:
            time.sleep(1)
            with self._lock:
                if self._gen != gen:
                    return
            try:
                leaving = card_phase(project, card_id).get("code") not in _OPEN_STAGES
            except Exception:
                leaving = True
            if not leaving:
                continue
            with self._lock:
                if self._gen != gen:
                    return
                proc, record = self._take()
            self._finish(proc, record)
            return

    def start(self, project: str, card_id: str) -> Dict[str, Any]:
        self.stop()
        with self._lock:
            ticket = self._gen  # 准备期间又有人开始或结束，这次就作废，不能留下没人管的进程
        project_path = Path(project)
        copy = _worktree(project, card_id)
        cdir = project_path / ".collab" / "cards" / card_id
        if not copy.is_dir():
            return {"ok": False, "reason": "改好的版本不在了"}
        try:
            allowed = card_phase(project, card_id).get("code") in _OPEN_STAGES
        except Exception:
            allowed = False
        if not allowed:
            return {"ok": False, "reason": "这张卡现在不能打开预览"}
        record = cdir / _RECORD
        for old in (project_path / ".collab" / "cards").glob(f"*/{_RECORD}"):  # 残留可能是别的卡留下的，占着同一个端口
            self._clear_leftover(old)
        try:
            data = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        command = str(data.get("view_command") or "").strip()
        url = str(data.get("view_url") or "").strip()
        folder = {"mode": "folder", "path": str(copy)}
        if not command or not is_local_url(url):
            return dict(folder, ok=True)
        if _reachable(url):
            return dict(folder, ok=False, reason="这个地址已经被占用了，打开的可能是别的程序。")
        try:
            argv = shlex.split(command)
        except ValueError:
            return dict(folder, ok=False, reason="启动命令写得不对，打不开。")
        if not argv:
            return dict(folder, ok=True)
        _link_node_modules(project_path, copy)
        env = dict(os.environ)
        env.pop("SUPERAGENT_CHAT_TOKEN", None)  # 运行的是模型写的代码，不能让它能调本机服务
        with self._lock:
            if self._gen != ticket:
                return dict(folder, ok=False, reason="这次预览已经取消了。")
            mine = self._gen = self._gen + 1
            try:
                with open(cdir / "预览输出.log", "ab") as log:
                    proc = subprocess.Popen(argv, cwd=str(copy), stdout=log, stderr=subprocess.STDOUT,
                                            stdin=subprocess.DEVNULL, start_new_session=True, env=env)
            except OSError as exc:
                return dict(folder, ok=False, reason=f"没能启动：{exc}")
            self._proc, self._project, self._card, self._url = proc, str(project), card_id, url
            try:
                record.write_text(json.dumps({"pid": proc.pid, "started": process_start(proc.pid)}), encoding="utf-8")
                self._record = record
            except OSError:
                self._record = None

        deadline = time.time() + self.wait_seconds
        while time.time() < deadline:
            with self._lock:
                replaced = self._gen != mine
            if replaced:
                return dict(folder, ok=False, reason="这次预览已经取消了。")
            if proc.poll() is not None and not _group_alive(proc.pid):
                return self._give_up(mine, folder, "预览程序提前退出了，改好的版本没打开。")
            if proc.poll() is None and _reachable(url):
                with self._lock:
                    if self._gen != mine:
                        return dict(folder, ok=False, reason="这次预览已经取消了。")
                threading.Thread(target=self._watch, args=(mine, str(project), card_id), daemon=True).start()
                return {"ok": True, "mode": "web", "url": url}
            time.sleep(0.2)
        return self._give_up(mine, folder, "等了很久还打不开，改好的版本可能还没起来。")

    def _give_up(self, mine: int, folder: dict, reason: str) -> Dict[str, Any]:
        with self._lock:
            if self._gen != mine:
                return dict(folder, ok=False, reason="这次预览已经取消了。")
            proc, record = self._take()
        self._finish(proc, record)
        return dict(folder, ok=False, reason=reason)
