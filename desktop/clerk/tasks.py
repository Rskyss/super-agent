"""行政提议：模型只能提议，用户点确认才建卡；确认不经过模型。"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from collab.tasks import TaskError, new_card

FOLDER = Path(".collab") / "行政提议"
_FIELDS = ("title", "do", "not_do", "done_when")
_FIELD_NAMES = {"title": "标题", "do": "要做什么", "not_do": "不做什么", "done_when": "怎么算做完"}
_lock = threading.Lock()


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
        return {"ok": True, "card": card, "state": "confirmed", "repeat": False}


def event_from_proposal(data: dict) -> dict:
    return {
        "type": "proposal", "id": data["id"], "kind": data.get("kind") or "task",
        "title": data.get("title") or "", "do": data.get("do") or "",
        "not_do": data.get("not_do") or "", "done_when": data.get("done_when") or "",
        "state": data.get("state") or "open", "card": data.get("card") or "",
    }
