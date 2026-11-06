"""行政提议与后台任务：模型只能提议，用户确认才建卡；后台子进程与本机服务分离。"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from collab.tasks import TaskError, new_card

COLLAB_PACKAGE = Path(__file__).resolve().parent.parent.parent / "collab"

FOLDER = Path(".collab") / "行政提议"
_FIELDS = ("title", "do", "not_do", "done_when")
_FIELD_NAMES = {"title": "标题", "do": "要做什么", "not_do": "不做什么", "done_when": "怎么算做完"}
_lock = threading.Lock()


class TaskBusy(Exception):
    """同一种后台任务已经在跑。"""


def _folder(project: str) -> Path:
    return Path(project) / FOLDER


def _path(project: str, pid: str) -> Path:
    return _folder(project) / f"{pid}.json"


def load_proposal(project: str, pid: str) -> Optional[dict]:
    path = _path(project, pid)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("id") == pid else None


def _save(project: str, data: dict) -> None:
    folder = _folder(project)
    folder.mkdir(parents=True, exist_ok=True)
    path = _path(project, data["id"])
    spare = path.with_suffix(".tmp")
    spare.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    spare.replace(path)


def _open_in_conversation(project: str, conversation: str) -> list:
    folder = _folder(project)
    if not folder.is_dir():
        return []
    found = []
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("conversation") == conversation and data.get("state") == "open":
            found.append(data)
    return found


def propose(conv, arguments: dict) -> str:
    """模型调用 propose_task：字段齐全才生成提议；否则告诉它还缺什么。"""
    missing = []
    values = {}
    for key in _FIELDS:
        value = str((arguments or {}).get(key) or "").strip()
        values[key] = value
        if not value:
            missing.append(_FIELD_NAMES[key])
    if missing:
        return "还缺：" + "、".join(missing) + "，先问用户"
    with _lock:
        for old in _open_in_conversation(conv.project, conv.id):
            old["state"] = "replaced"
            _save(conv.project, old)
            conv.emit({"type": "proposal_done", "id": old["id"], "state": "replaced", "card": old.get("card") or ""})
        pid = uuid.uuid4().hex
        data = {
            "id": pid, "conversation": conv.id, "kind": "task",
            "title": values["title"], "do": values["do"], "not_do": values["not_do"],
            "done_when": values["done_when"], "state": "open", "card": "", "time": time.time(),
        }
        _save(conv.project, data)
        _append_proposal_line(conv, pid)
        event = {k: data[k] for k in ("id", "kind", "title", "do", "not_do", "done_when", "state", "card")}
        conv.emit(dict(event, type="proposal"))
    return "已向用户出示确认框，等用户点确认或先不做。还没建卡。"


def _append_proposal_line(conv, pid: str) -> None:
    folder = Path(conv.project) / ".collab" / "行政对话"
    folder.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"role": "proposal", "id": pid, "time": time.time()}, ensure_ascii=False)
    with (folder / f"{conv.id}.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def decide(project: str, conversation: str, pid: str, decision: str) -> dict:
    """用户确认或取消。不调用模型。重复确认返回同一张卡。"""
    if decision not in ("confirm", "cancel"):
        raise LookupError("只能确认或先不做")
    with _lock:
        data = load_proposal(project, pid)
        if data is None or data.get("conversation") != conversation:
            raise LookupError("没有这个提议")
        if data.get("state") != "open":
            if decision == "confirm" and data.get("state") == "confirmed" and data.get("card"):
                return {"ok": True, "card": data["card"], "state": "confirmed", "repeat": True}
            raise RuntimeError("这个提议已经处理过了")
        if decision == "cancel":
            data["state"] = "cancelled"
            _save(project, data)
            return {"ok": True, "card": "", "state": "cancelled", "repeat": False}
        try:
            card = new_card(project, data["title"], data["do"], data["not_do"], data["done_when"],
                            conversation=conversation)
        except TaskError:
            raise
        data["state"] = "confirmed"
        data["card"] = card
        _save(project, data)
        try:
            launch(project, "plan", card, ["plan", project, card])
        except TaskBusy:
            pass
        return {"ok": True, "card": card, "state": "confirmed", "repeat": False}


def event_from_proposal(data: dict) -> dict:
    return {
        "type": "proposal", "id": data["id"], "kind": data.get("kind") or "task",
        "title": data.get("title") or "", "do": data.get("do") or "",
        "not_do": data.get("not_do") or "", "done_when": data.get("done_when") or "",
        "state": data.get("state") or "open", "card": data.get("card") or "",
    }


def _collab(project: str) -> Path:
    return Path(project) / ".collab"


def _record_path(project: str, kind: str) -> Path:
    folder = _collab(project) / "后台"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{kind}.json"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except (OSError, ValueError, TypeError):
        return True


def running(project: str, kind: str) -> Optional[Dict[str, Any]]:
    path = _record_path(project, kind)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if pid is None:
        try:
            path.unlink()
        except OSError:
            pass
        return None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        try:
            path.unlink()
        except OSError:
            pass
        return None
    if not _pid_alive(pid):
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return data


def launch(project: str, kind: str, card_id: str, args: list) -> int:
    path = _record_path(project, kind)
    lock_path = path.with_suffix(".lock")
    log_path = _collab(project) / "cards" / card_id / "后台输出.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = None
    tmp_path = path.with_suffix(".json.tmp")
    with open(lock_path, "a+", encoding="utf-8") as lock_fh:
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        if running(project, kind) is not None:
            raise TaskBusy("已有同类任务在跑")
        try:
            log = open(log_path, "a", encoding="utf-8", buffering=1)
            proc = subprocess.Popen(
                [sys.executable, "-B", str(COLLAB_PACKAGE), *args],
                cwd=str(project),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            log.close()
            payload = json.dumps({"card": card_id, "pid": proc.pid, "time": time.time()}, ensure_ascii=False)
            tmp_path.write_text(payload + "\n", encoding="utf-8")
            os.replace(str(tmp_path), str(path))

            def _wait():
                proc.wait()
                try:
                    if path.is_file():
                        cur = json.loads(path.read_text(encoding="utf-8"))
                        if isinstance(cur, dict) and int(cur.get("pid") or 0) == proc.pid:
                            path.unlink()
                except (OSError, ValueError):
                    pass

            threading.Thread(target=_wait, daemon=True).start()
            return proc.pid
        except OSError:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            try:
                path.unlink()
            except OSError:
                pass
            if proc:
                try:
                    proc.kill()
                except OSError:
                    pass
            raise
