"""行政人员的对话：保存在项目 .collab/行政对话/ 里，只留本机。

页面用“从第几条之后”来取事件，和调度员聊天用同一种事件，所以页面的显示逻辑不用另写一套。
每段对话互不影响：切走时回复继续，切回来接着看。用户说第一句之前不保存，不留空对话。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path

from desktop.clerk.client import ModelError
from desktop.clerk import blocks_tools, tasks as clerk_tasks
from desktop.clerk.tools import LABELS, PROPOSE_REVISE, TOOLS, run_tool, team_incomplete_hint
from collab import blocks

FOLDER = Path(".collab") / "行政对话"
INDEX = "索引.json"
HISTORY_LIMIT = 40  # 带给模型的最近几句话，够接上话，也不让费用越滚越大
TITLE_LIMIT = 24
MAX_TOOL_ROUNDS = 5
ROLE = ("你是超级助手的行政人员，用户的前台和行政。用大白话简短回答，不说技术术语。"
        "你能查项目说明、台账、卡片和团队设置；也能查本机 PATH 里有没有装 Codex、Cursor（agent 命令）、agy，只查不运行。"
        "问进度必须先查，不能凭记忆。"
        "问这个项目是什么、怎么用时，先读项目说明；没有或不够，再调用 read_project_docs 读项目自带资料，只按资料回答。"
        "项目说明还没写时，读完资料后可以调用 propose_profile 起草。不知道的项不要编，先问用户。"
        "你不能改代码、运行命令、推送或把改动放进项目。"
        "用户明确要改产品或写代码时，先问清楚，再调用 propose_task。"
        "用户明确要改团队分工（调度员、写测试、写代码、复查用谁）时，先问清楚，再调用 propose_team_change。"
        "不要私自做工具连通检测；需要的话提醒用户去「设置」里点「检测」。"
        "闲聊、问进度不调用 propose_task 或 propose_team_change。不能说“已经建好卡”或“已经改好团队”，用户点确认才算。"
        "不清楚时一次只问一个问题，给出推荐答案和一句话理由。"
        "不知道就直说，不编造。"
        "这个项目叫：{name}。")
NAMING = "用不超过 12 个字概括这段对话的主题，只回答标题本身。\n用户：{user}\n回答：{reply}"
_ID_SHAPE = re.compile(r"[0-9a-f]{32}")


def _section(text: str, heading: str) -> str:
    mark = f"## {heading}"
    if mark not in text:
        return ""
    body = text.split(mark, 1)[1]
    lines = []
    for line in body.splitlines():
        if line.startswith("## "):
            break
        lines.append(line)
    return "\n".join(lines).strip()


class _Stopped(Exception):
    """用户点了停止：这一轮后面到的内容都不要了。"""


class _Conversation:
    def __init__(self, project: str, cid: str):
        self.project, self.id = project, cid
        self.events, self.messages, self.partial = [], [], []
        self.cond = threading.Condition()
        self.busy = False
        self.turn = 0
        self.deleted = False   # 删掉后，还在路上的回答和标题都不再写

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
        if conv.deleted:
            return
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
            if not create and not path.exists():  # 读的时候被删了：不能放回内存，否则之后的通知会把文件写回来
                return None
            return self._open.setdefault(key, conv)

    def _card_conversations(self, project: str) -> set:
        """卡片自己的对话。编号由卡片算出来，索引坏了也认得出。"""
        cards = Path(project) / ".collab" / "cards"
        if not cards.is_dir():
            return set()
        return {clerk_tasks.card_conversation_id(item.name) for item in cards.iterdir()}

    # ---------- 给页面用 ----------

    def list(self, project: str) -> dict:
        current = self._focus.get(str(project))
        pending = clerk_tasks.conversations_with_open(project)
        of_cards = self._card_conversations(project)
        threads = []
        with self._lock:
            opened = dict(self._open)
        for cid, entry in self._load_index(project).items():
            if isinstance(entry, dict) and _ID_SHAPE.fullmatch(cid) and entry.get("title") \
                    and (self._folder(project) / f"{cid}.jsonl").exists():
                if entry.get("card") or cid in of_cards:
                    continue
                conv = opened.get((str(project), cid))
                threads.append({
                    "id": cid, "title": entry["title"], "updated": entry.get("updated") or 0,
                    "current": cid == current,
                    "busy": bool(conv and conv.busy),
                    "pending": cid in pending,
                })
        threads.sort(key=lambda item: item["updated"], reverse=True)
        return {"threads": threads, "current": current, "cached": False}

    def delete(self, project: str, cid: str) -> None:
        """删掉一段主对话。顺序：作废提议 → 移出内存 → 删文件 → 最后改索引。"""
        gone = LookupError("这段对话已经不在了")
        if not _ID_SHAPE.fullmatch(cid or ""):
            raise gone
        path = self._folder(project) / f"{cid}.jsonl"
        if not path.exists():
            raise gone
        if self._index_entry(project, cid).get("card") or cid in self._card_conversations(project):
            raise gone
        key = (str(project), cid)
        with self._lock:
            conv = self._open.get(key)
            busy = bool(conv and conv.busy)
        if busy:
            raise RuntimeError("这段对话还在回复，等说完或先点停止再删")
        clerk_tasks.cancel_open(project, cid)
        with self._lock:  # 移出内存和删文件要一起：中间不能有人重新打开它
            conv = self._open.get(key)
            if conv is not None and conv.busy:
                raise RuntimeError("这段对话还在回复，等说完或先点停止再删")
            try:
                path.unlink()
            except OSError as exc:
                raise RuntimeError("没能删掉这段对话，请检查文件夹权限。") from exc
            if conv is not None:
                conv.deleted = True
            self._open.pop(key, None)
            if self._focus.get(str(project)) == cid:
                self._focus[str(project)] = None
            index = self._load_index(project)
            if cid in index:
                index.pop(cid, None)
                folder = self._folder(project)
                folder.mkdir(parents=True, exist_ok=True)
                spare = folder / (INDEX + ".tmp")
                spare.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
                spare.replace(folder / INDEX)

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
        
        # 54号单：积木库密钥打码保护
        has_secret = False
        if blocks_tools.is_blocks_home(project):
            if blocks._SECRET_TEXT.search(text):
                has_secret = True
                text = blocks._SECRET_TEXT.sub("[密钥已隐藏]", text)
                
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
        if has_secret:
            conv.emit({"type": "notice", "text": "你刚才贴的像是密钥，我没有发给模型。为了安全，请到那家平台把这把密钥作废，换一把新的，以后填在提议卡的密钥框里。"})
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

    def _index_entry(self, project: str, cid: str) -> dict:
        entry = self._load_index(project).get(cid)
        return entry if isinstance(entry, dict) else {}

    def _card_of(self, conv: _Conversation) -> str:
        return str(self._index_entry(conv.project, conv.id).get("card") or "")

    def _tools_for(self, conv: _Conversation) -> list:
        if blocks_tools.is_blocks_home(conv.project):
            return blocks_tools.TOOLS
        if not self._card_of(conv):
            return TOOLS
        kept = [tool for tool in TOOLS
                if tool.get("function", {}).get("name") not in ("propose_task", "propose_team_change")]
        return kept + [PROPOSE_REVISE]

    def open_card(self, project: str, card_id: str) -> dict:
        """打开这张卡自己的对话。重复打开还是同一段，不把旧进展再播一遍。"""
        from collab.flow import read_flow
        from collab.phase import card_phase
        from desktop.view import _title
        cdir = Path(project) / ".collab" / "cards" / card_id
        if not (cdir / "状态.json").is_file():
            raise LookupError("没有这张卡片")
        need = ""
        req = cdir / "需求卡.md"
        if req.is_file():
            need = req.read_text(encoding="utf-8")
        title = _title(cdir, card_id)
        cid = hashlib.md5(("card:" + card_id).encode()).hexdigest()
        folder = self._folder(project)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{cid}.jsonl"
        if not path.exists():
            path.write_text("", encoding="utf-8")
        self._save_index(project, cid, title=title, card=card_id, updated=time.time())
        played = cdir / "已播报.json"
        if not played.exists():
            events = read_flow(cdir)
            seq = int(events[-1].get("seq") or 0) if events else 0
            played.write_text(json.dumps({"seq": seq}, ensure_ascii=False), encoding="utf-8")
        return {
            "id": card_id, "conversation": cid, "title": title, "phase": card_phase(project, card_id),
            "do": _section(need, "要做什么"), "not_do": _section(need, "不做什么"),
            "done_when": _section(need, "怎么算做完"),
        }

    def _prompt(self, conv: _Conversation) -> list:
        if blocks_tools.is_blocks_home(conv.project):
            role = blocks_tools.role_text()
        else:
            role = ROLE.format(name=Path(conv.project).name)
            hint = team_incomplete_hint(conv.project)
            if hint:
                role = f"{role} {hint}"
        
        card_id = self._card_of(conv)
        if card_id:
            from collab.phase import card_phase
            phase = card_phase(conv.project, card_id)
            detail = run_tool(conv.project, "read_card", {"card_id": card_id})
            role += (
                " 这段对话只谈这张卡。用户只和你说话，不能直接指挥执行的工具。"
                "新任务请用户回主对话提。"
                f"当前阶段：{phase.get('label') or ''}。\n{detail}"
            )
        return [{"role": "system", "content": role}] + conv.messages[-HISTORY_LIMIT:]

    def _alive(self, conv: _Conversation, turn: int) -> bool:
        with conv.cond:
            return conv.turn == turn

    def _reply(self, conv: _Conversation, client, turn: int, first: bool) -> None:
        holder = {"item": uuid.uuid4().hex}

        def on_text(piece: str) -> None:
            with conv.cond:
                if conv.turn != turn:
                    raise _Stopped
                conv.partial.append(piece)
            conv.emit({"type": "delta", "item": holder["item"], "text": piece})

        def seal(text: str) -> None:
            words = (text or "").strip()
            if not words:
                return
            with conv.cond:
                conv.partial = []  # 这些话已经存好，点停止时不能再存一遍
            conv.emit({"type": "message", "item": holder["item"], "text": words})
            self._keep_reply(conv, words)
            holder["item"] = uuid.uuid4().hex
            holder["said"] = True

        failure, reply = None, ""
        try:
            reply = self._tool_loop(conv, client, turn, on_text, seal)
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
        if not (reply or "").strip() and not failure and not holder.get("said"):
            reply = "这次没有得到回复，请再说一次。"
        if reply:
            conv.emit({"type": "message", "item": holder["item"], "text": reply})
            self._keep_reply(conv, reply)
        if failure:
            conv.emit({"type": "error", "text": failure})
        conv.emit({"type": "turn_done"})
        if first and reply and not failure:
            threading.Thread(target=self._name, args=(conv, client, reply), daemon=True).start()

    def _tool_loop(self, conv: _Conversation, client, turn: int, on_text, seal) -> str:
        """带工具的多轮：有 tool_calls 就执行后继续问；到上限后再问一次，这次不带工具。"""
        messages = self._prompt(conv)
        allowed = self._tools_for(conv)
        names = {tool.get("function", {}).get("name") for tool in allowed}
        parts = []
        for round_no in range(MAX_TOOL_ROUNDS + 1):
            if not self._alive(conv, turn):
                raise _Stopped
            use_tools = round_no < MAX_TOOL_ROUNDS
            try:
                answer = client.chat(messages, on_text=on_text, tools=allowed if use_tools else None)
            except ModelError as exc:
                prior = "\n".join(parts)
                if prior:
                    extra = (exc.partial or "").strip()
                    merged = prior if not extra else f"{prior}\n{extra}"
                    raise ModelError(str(exc), partial=merged) from exc
                raise
            reply = answer.get("text") or ""
            if reply.strip():
                parts.append(reply.strip())
            calls = answer.get("tool_calls") or []
            if not use_tools or not calls:
                return "\n".join(parts) if parts else reply
            if any(str(call.get("name") or "").startswith("propose_") for call in calls):
                seal("\n".join(parts))
                parts = []
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
                    if name not in names:
                        result = "没有这个工具"
                    elif name == "propose_task":
                        result = clerk_tasks.propose(conv, args)
                    elif name == "propose_team_change":
                        result = clerk_tasks.propose_team_change(conv, args)
                    elif name == "propose_profile":
                        result = clerk_tasks.propose_profile(conv, args)
                    elif name == "propose_revise":
                        result = clerk_tasks.propose_revise(conv, args)
                    elif name == "propose_install":
                        result = clerk_tasks.propose_install(conv, args)
                    elif name == "propose_connect_cli":
                        result = clerk_tasks.propose_connect_cli(conv, args)
                    elif name == "propose_connect_api":
                        result = clerk_tasks.propose_connect_api(conv, args)
                    elif blocks_tools.is_blocks_home(conv.project):
                        result = blocks_tools.run(conv.project, name, args)
                    else:
                        result = run_tool(conv.project, name, args)
                except (ValueError, TypeError):
                    result = "参数看不懂"
                label = LABELS.get(name, "读 资料")
                conv.emit({"type": "activity", "item": call_id, "label": label, "status": "done"})
                messages.append({"role": "tool", "tool_call_id": call_id, "content": result})
        return "\n".join(parts)

    def notify(self, project: str, conversation: str, text: str) -> bool:
        """往来源对话写一条行政通知并发 message 事件。对话不存在则跳过（由调用方决定是否仍记已汇报）。"""
        if not _ID_SHAPE.fullmatch(conversation or ""):
            return False
        conv = self._conversation(project, conversation, create=False)
        if conv is None:
            return False
        item = uuid.uuid4().hex
        conv.emit({"type": "message", "item": item, "text": text})
        self._keep_reply(conv, text)
        return True

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
            kind = result.get("kind") or "task"
            if kind == "profile":
                text = "项目说明已保存。" if result["state"] == "confirmed" else "好的，先不保存。"
            elif kind == "team":
                text = "团队设置已更新。" if result["state"] == "confirmed" else "好的，先不改团队设置。"
            elif kind == "revise":
                text = result.get("reply") or ("已交给开发，做完再告诉你。" if result["state"] == "confirmed" else "好的，先不改。")
            elif result["state"] == "confirmed":
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
            if not conv.deleted:
                self._save_index(conv.project, conv.id, title=self._short(title))
