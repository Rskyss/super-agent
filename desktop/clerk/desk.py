"""行政人员的对话：保存在项目 .collab/行政对话/ 里，只留本机。

页面用“从第几条之后”来取事件，和调度员聊天用同一种事件，所以页面的显示逻辑不用另写一套。
每段对话互不影响：切走时回复继续，切回来接着看。用户说第一句之前不保存，不留空对话。
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

from desktop.clerk.client import ModelError
from desktop.clerk import tasks as clerk_tasks
from desktop.clerk.tools import LABELS, TOOLS, run_tool

FOLDER = Path(".collab") / "行政对话"
INDEX = "索引.json"
HISTORY_LIMIT = 40  # 带给模型的最近几句话，够接上话，也不让费用越滚越大
TITLE_LIMIT = 24
MAX_TOOL_ROUNDS = 5
ROLE = ("你是超级助手的行政人员，用户的前台和行政。用大白话简短回答，不说技术术语。"
        "你能查项目说明、台账、卡片和团队设置。"
        "问进度必须先查，不能凭记忆。"
        "你不能改代码、运行命令、推送或合并。"
        "用户明确要改产品或写代码时，先问清楚，再调用 propose_task。"
        "闲聊、问进度不调用。不能说“已经建好卡”，用户点确认才算。"
        "不清楚时一次只问一个问题，给出推荐答案和一句话理由。"
        "不知道就直说，不编造。"
        "这个项目叫：{name}。")
NAMING = "用不超过 12 个字概括这段对话的主题，只回答标题本身。\n用户：{user}\n回答：{reply}"
_ID_SHAPE = re.compile(r"[0-9a-f]{32}")


class _Stopped(Exception):
    """用户点了停止：这一轮后面到的内容都不要了。"""


class _Conversation:
    def __init__(self, project: str, cid: str):
        self.project, self.id = project, cid
        self.events, self.messages, self.partial = [], [], []
        self.cond = threading.Condition()
        self.busy = False
        self.turn = 0

    def emit(self, event: dict) -> None:
        with self.cond:
            self.events.append(dict(event, seq=len(self.events) + 1))
            self.cond.notify_all()


class ClerkDesk:
    def __init__(self, client):
        self._client = client  # 取当前模型连接的函数：密钥可能晚一点才配好
        self._open = {}
        self._focus = {}
        self._lock = threading.Lock()

    # ---------- 存取 ----------

    def _folder(self, project: str) -> Path:
        return Path(project) / FOLDER

    def _load_index(self, project: str) -> dict:
        """索引只是目录：读不了或缺了哪段，就从对话文件补回来，不能让对话消失。"""
        try:
            saved = json.loads((self._folder(project) / INDEX).read_text(encoding="utf-8"))
            index = saved if isinstance(saved, dict) else {}
        except (OSError, ValueError):
            index = {}
        for path in self._folder(project).glob("*.jsonl"):
            entry = index.get(path.stem)
            if _ID_SHAPE.fullmatch(path.stem) and not (isinstance(entry, dict) and entry.get("title")):
                title = self._first_words(path)
                if title:
                    index[path.stem] = {"title": title, "updated": path.stat().st_mtime}
        return index

    def _first_words(self, path: Path) -> str:
        for role, text in self._read_lines(path):
            if role == "user":
                return self._short(text)
        return ""

    @staticmethod
    def _read_lines(path: Path) -> list:
        """读对话文件。提议行 role=proposal 只记位置，不进模型上下文。"""
        lines = []
        try:
            raws = path.read_bytes().splitlines()
        except OSError:
            return lines
        for raw in raws:
            try:
                saved = json.loads(raw.decode("utf-8"))
                role = saved["role"]
            except (ValueError, KeyError, TypeError):
                continue
            if role == "proposal" and isinstance(saved.get("id"), str):
                lines.append(("proposal", saved["id"]))
                continue
            text = saved.get("text")
            if role in ("user", "assistant") and isinstance(text, str):
                lines.append((role, text))
        return lines

    def _save_index(self, project: str, cid: str, **fields) -> None:
        with self._lock:
            index = self._load_index(project)
            entry = index.get(cid) if isinstance(index.get(cid), dict) else {}
            index[cid] = dict(entry, **fields)
            folder = self._folder(project)
            folder.mkdir(parents=True, exist_ok=True)
            spare = folder / (INDEX + ".tmp")
            spare.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
            spare.replace(folder / INDEX)

    def _record(self, conv: _Conversation, role: str, text: str) -> None:
        folder = self._folder(conv.project)
        folder.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"role": role, "text": text, "time": time.time()}, ensure_ascii=False)
        with (folder / f"{conv.id}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        conv.messages.append({"role": role, "content": text})
        self._save_index(conv.project, conv.id, updated=time.time())

    def _conversation(self, project: str, cid: str, create: bool = False):
        if not _ID_SHAPE.fullmatch(cid or ""):
            return None
        key = (str(project), cid)
        with self._lock:
            conv = self._open.get(key)
        if conv is not None:
            return conv
        path = self._folder(project) / f"{cid}.jsonl"
        if not create and not path.exists():
            return None
        conv = _Conversation(str(project), cid)
        for role, payload in self._read_lines(path):
            if role == "proposal":
                data = clerk_tasks.load_proposal(project, payload)
                if data:
                    conv.emit(dict(clerk_tasks.event_from_proposal(data), history=True,
                                   item=f"h{len(conv.events)}"))
                continue
            conv.messages.append({"role": role, "content": payload})
            kind = "user" if role == "user" else "message"
            conv.emit({"type": kind, "text": payload, "item": f"h{len(conv.events)}", "history": True})
        with self._lock:
            return self._open.setdefault(key, conv)

    # ---------- 给页面用 ----------

    def list(self, project: str) -> dict:
        current = self._focus.get(str(project))
        threads = []
        for cid, entry in self._load_index(project).items():
            if isinstance(entry, dict) and _ID_SHAPE.fullmatch(cid) and entry.get("title") \
                    and (self._folder(project) / f"{cid}.jsonl").exists():
                threads.append({"id": cid, "title": entry["title"], "updated": entry.get("updated") or 0,
                                "current": cid == current})
        threads.sort(key=lambda item: item["updated"], reverse=True)
        return {"threads": threads, "current": current, "cached": False}

    def focus(self, project: str, cid: str) -> None:
        self._focus[str(project)] = cid or None

    def events(self, project: str, cid: str, since: int = 0, wait: float = 0) -> dict:
        conv = self._conversation(project, cid)
        if conv is None:
            return {"events": [], "open": False, "busy": False}  # 页面看到 open=False 就不再轮询
        with conv.cond:
            if len(conv.events) <= since and wait > 0:
                conv.cond.wait(wait)
            return {"events": list(conv.events[since:]), "open": True, "busy": conv.busy}

    def send(self, project: str, cid: str, text: str) -> str:
        client = self._client()
        if client is None:
            raise RuntimeError("行政人员还没配置好，请先填写模型密钥")
        first = not cid
        if first:
            cid = uuid.uuid4().hex
        conv = self._conversation(project, cid, create=first)
        if conv is None:
            raise RuntimeError("找不到这段对话")
        with conv.cond:
            if conv.busy:
                raise RuntimeError("行政还在回复：等它说完，或先点“停止”")
            conv.busy = True
            conv.turn += 1
            conv.partial = []
            turn = conv.turn
        try:
            self._record(conv, "user", text)
            if first:
                self._save_index(project, cid, title=self._short(text))
        except OSError:
            with conv.cond:
                conv.busy = False
            raise RuntimeError("这句话没能保存，请检查磁盘空间或文件夹权限。") from None
        conv.emit({"type": "user", "text": text})
        conv.emit({"type": "turn_started"})
        threading.Thread(target=self._reply, args=(conv, client, turn, first), daemon=True).start()
        return cid

    def stop(self, project: str, cid: str) -> None:
        conv = self._conversation(project, cid)
        if conv is None:
            return
        with conv.cond:
            if not conv.busy:
                return
            conv.turn += 1
            conv.busy = False
            partial = "".join(conv.partial)
        if partial:
            self._keep_reply(conv, partial)
        conv.emit({"type": "turn_done"})

    def _keep_reply(self, conv: _Conversation, text: str) -> None:
        try:
            self._record(conv, "assistant", text)
        except OSError:
            conv.emit({"type": "error", "text": "这段回复没能保存，请检查磁盘空间或文件夹权限。"})

    # ---------- 回话 ----------

    @staticmethod
    def _short(text: str) -> str:
        line = next((part.strip() for part in text.splitlines() if part.strip()), "新对话")
        return line[:TITLE_LIMIT]

    def _prompt(self, conv: _Conversation) -> list:
        role = ROLE.format(name=Path(conv.project).name)
        return [{"role": "system", "content": role}] + conv.messages[-HISTORY_LIMIT:]

    def _alive(self, conv: _Conversation, turn: int) -> bool:
        with conv.cond:
            return conv.turn == turn

    def _reply(self, conv: _Conversation, client, turn: int, first: bool) -> None:
        item = uuid.uuid4().hex

        def on_text(piece: str) -> None:
            with conv.cond:
                if conv.turn != turn:
                    raise _Stopped
                conv.partial.append(piece)
            conv.emit({"type": "delta", "item": item, "text": piece})

        failure, reply = None, ""
        try:
            reply = self._tool_loop(conv, client, turn, on_text)
        except _Stopped:
            return
        except ModelError as exc:
            failure, reply = str(exc), exc.partial
        except Exception:  # 回话线程不能悄悄死掉，否则页面一直转
            failure, reply = "行政这次没回复成功，请再试一次。", "".join(conv.partial)
        with conv.cond:
            if conv.turn != turn:
                return
            conv.busy = False
        if reply:
            conv.emit({"type": "message", "item": item, "text": reply})
            self._keep_reply(conv, reply)
        if failure:
            conv.emit({"type": "error", "text": failure})
        conv.emit({"type": "turn_done"})
        if first and reply and not failure:
            threading.Thread(target=self._name, args=(conv, client, reply), daemon=True).start()

    def _tool_loop(self, conv: _Conversation, client, turn: int, on_text) -> str:
        """带工具的多轮：有 tool_calls 就执行后继续问；到上限后再问一次，这次不带工具。"""
        messages = self._prompt(conv)
        reply = ""
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            if not self._alive(conv, turn):
                raise _Stopped
            use_tools = round_no < MAX_TOOL_ROUNDS
            answer = client.chat(messages, on_text=on_text, tools=TOOLS if use_tools else None)
            reply = answer.get("text") or ""
            calls = answer.get("tool_calls") or []
            if not use_tools or not calls:
                return reply
            messages.append({
                "role": "assistant",
                "content": reply or None,
                "tool_calls": [{
                    "id": call.get("id") or f"call{round_no}_{i}",
                    "type": "function",
                    "function": {"name": call.get("name") or "", "arguments": call.get("arguments") or "{}"},
                } for i, call in enumerate(calls)],
            })
            for i, call in enumerate(calls):
                if not self._alive(conv, turn):
                    raise _Stopped
                call_id = call.get("id") or f"call{round_no}_{i}"
                name = call.get("name") or ""
                raw = call.get("arguments") or "{}"
                try:
                    args = json.loads(raw)
                    if not isinstance(args, dict):
                        raise ValueError("not a dict")
                    if name == "propose_task":
                        result = clerk_tasks.propose(conv, args)
                    else:
                        result = run_tool(conv.project, name, args)
                except (ValueError, TypeError):
                    result = "参数看不懂"
                label = LABELS.get(name, "读 资料")
                conv.emit({"type": "activity", "item": call_id, "label": label, "status": "done"})
                messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
        return reply

    def decide_proposal(self, project: str, conversation: str, pid: str, decision: str) -> dict:
        """用户点确认或先不做。不经过模型。"""
        from collab.tasks import TaskError
        try:
            result = clerk_tasks.decide(project, conversation, pid, decision)
        except LookupError as exc:
            raise LookupError(str(exc)) from exc
        except TaskError as exc:
            raise RuntimeError(str(exc)) from exc
        except RuntimeError:
            raise
        conv = self._conversation(project, conversation)
        if conv is None and not result.get("repeat"):
            raise LookupError("找不到这段对话")
        if conv is not None and not result.get("repeat"):
            if result["state"] == "confirmed":
                text = f"已生成卡片 {result['card']}，接下来写方案。"
            else:
                text = "好的，先不做。"
            conv.emit({"type": "proposal_done", "id": pid, "state": result["state"],
                       "card": result.get("card") or ""})
            conv.emit({"type": "message", "text": text, "item": uuid.uuid4().hex})
            self._keep_reply(conv, text)
        return result

    def _name(self, conv: _Conversation, client, reply: str) -> None:
        """起名失败不要紧，标题先用用户的第一句话。"""
        words = NAMING.format(user=conv.messages[0]["content"][:300], reply=reply[:300])
        try:
            title = client.chat([{"role": "user", "content": words}])["text"].strip().strip("《》\"'“”")
        except ModelError:
            return
        if title:
            self._save_index(conv.project, conv.id, title=self._short(title))
