"""只读查看旧 Codex 对话：短暂连上 app-server，只允许 initialize、thread/list、thread/read。

绝不发送可写请求。Codex 没装或读取失败时，列表和内容都返回空。
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path

from desktop.dispatchers.codex import _is_super_agent_thread, _user_words, item_events

MARKER = "【超级助手自动交代】"
_TIMEOUT = 20


class _Session:
    """一行一条 JSON 的短连接。"""

    def __init__(self, proc):
        self.proc = proc
        self._lock = threading.Lock()
        self._pending = {}
        self._next = 0
        reader = threading.Thread(target=self._read_loop, daemon=True)
        getattr(reader, "start")()

    def _read_loop(self) -> None:
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if "id" in msg and "method" not in msg:
                    box = self._pending.pop(msg["id"], None)
                    if box is not None:
                        box.update({k: msg[k] for k in ("result", "error") if k in msg})
                        box["event"].set()
        except Exception:
            pass
        for box in list(self._pending.values()):
            box["error"] = {"message": "连接断了"}
            box["event"].set()
        self._pending.clear()

    def ask(self, method: str, params: dict) -> dict:
        with self._lock:
            self._next += 1
            rid = self._next
        box = {"event": threading.Event()}
        self._pending[rid] = box
        with self._lock:
            self.proc.stdin.write(json.dumps({"id": rid, "method": method, "params": params},
                                             ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        if not box["event"].wait(_TIMEOUT):
            self._pending.pop(rid, None)
            raise TimeoutError(method)
        if "error" in box:
            raise RuntimeError(str(box["error"]))
        return box.get("result") or {}

    def note(self, method: str, params: dict = None) -> None:
        with self._lock:
            self.proc.stdin.write(json.dumps({"method": method, "params": params or {}},
                                             ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.terminate()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=2)
        except Exception:
            try:
                self.proc.kill()
            except OSError:
                pass
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass


def _open_session(project: str, command) -> _Session:
    proc = subprocess.Popen(list(command), cwd=str(Path(project).resolve()),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)
    sess = _Session(proc)
    try:
        sess.ask("initialize", {"clientInfo": {"name": "super-agent", "title": "超级助手", "version": "0.2"}})
        sess.note("initialized")
    except Exception:
        sess.close()
        raise
    return sess


def _run(project: str, command, work):
    try:
        sess = _open_session(project, command)
    except (OSError, TimeoutError, RuntimeError):
        return None
    try:
        return work(sess)
    except (OSError, TimeoutError, RuntimeError, KeyError, TypeError, ValueError):
        return None
    finally:
        sess.close()


def list_history(project, command=("codex", "app-server")) -> list:
    """列出本项目在超级助手里聊过的旧 Codex 对话。失败返回空列表。"""

    def work(sess: _Session) -> list:
        found = sess.ask("thread/list", {"cwd": str(Path(project).resolve()), "limit": 30,
                                         "sortKey": "updated_at", "sortDirection": "desc"})
        seen, result = set(), []
        for item in found.get("data") or []:
            tid = item.get("id")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            if not _is_super_agent_thread(item, MARKER):
                continue
            title = _user_words(item, MARKER)
            if not title:
                continue
            updated = item.get("updatedAt") or item.get("recencyAt") or item.get("createdAt") or 0
            result.append({"id": tid, "title": title, "updated": int(updated) if updated else 0})
        return result

    return _run(project, command, work) or []


def read_history(project, thread_id, command=("codex", "app-server")) -> list:
    """只读一段旧对话的内容。失败返回空列表。"""
    if not thread_id:
        return []

    def work(sess: _Session) -> list:
        thread = sess.ask("thread/read", {"threadId": thread_id, "includeTurns": True})["thread"]
        return [dict(event, history=True)
                for turn in thread.get("turns") or []
                for item in turn.get("items") or []
                for event in item_events(item, MARKER)]

    return _run(project, command, work) or []
