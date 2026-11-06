"""Codex 调度员：通过 Codex 给聊天界面用的官方接口（codex app-server，标着“试验中”）对话。

用的是用户现在的 Codex 登录，不另配密钥。一问一答走 JSON 消息（标准输入输出上一行一条）：
我们问它（初始化、开对话、发消息、停止），它回答；它也会反过来问我们（“允许吗？”），我们代用户回答。
这里只做翻译：把 Codex 的消息翻成聊天事件（见 dispatchers/__init__.py），不做任何判断。
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from pathlib import Path

APPROVAL_POLICY = "on-request"   # 用户选 A：调度员自己判断，有风险才问
SANDBOX = "workspace-write"      # 只能改项目文件夹里的东西；想动外面的，必须先问
# “允许吗？”交给谁：一律问用户本人。用户自己的 Codex 可能设成让 AI 审批员代答（guardian），
# 那样聊天里永远看不到询问；这里只对超级助手里的对话生效，不改用户的 Codex 设置。
APPROVALS_REVIEWER = "user"
REQUEST_TIMEOUT = 60
WRITER_WAIT_SECONDS = 90.0       # 切回聊天时，上一段终端可能还没松手，最多等 90 秒
WRITER_POLL_SECONDS = 2.0

_STATUS = {"inProgress": "started", "completed": "done", "failed": "failed", "declined": "declined"}
_LEGACY_DECISION = {"accept": "approved", "acceptForSession": "approved_for_session", "decline": "denied"}


def short_command(cmd, limit: int = 80) -> str:
    """去掉外层的 shell 包装，只留用户看得懂的那条命令。"""
    cmd = (cmd or "").strip()
    for prefix in ("/bin/zsh -lc ", "/bin/bash -lc ", "zsh -lc ", "bash -lc "):
        if cmd.startswith(prefix):
            cmd = cmd[len(prefix):]
            if len(cmd) >= 2 and cmd[0] == cmd[-1] and cmd[0] in "'\"":
                cmd = cmd[1:-1]
            break
    return cmd if len(cmd) <= limit else cmd[:limit - 1] + "…"


def command_label(item: dict) -> str:
    actions = item.get("commandActions") or []
    if actions:
        a = actions[0]
        kind = a.get("type")
        if kind == "read":
            return "读 " + (a.get("name") or Path(a.get("path") or "").name or "文件")
        if kind == "listFiles":
            return "看文件夹 " + (a.get("path") or ".")
        if kind == "search":
            return "搜索 " + (a.get("query") or "")
    return "运行 " + short_command(item.get("command"))


def _user_text(item: dict) -> str:
    return "\n".join(c.get("text", "") for c in item.get("content") or [] if c.get("type") == "text")


def _clean_title(text: str, limit: int = 42) -> str:
    for line in str(text or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line if len(line) <= limit else line[:limit - 1] + "…"
    return ""


# Codex 自己附带的内容。下划线标签网页代码不用；带短横线的按真实记录逐个列，用户贴的 <el-button> 不能算进来
_SYSTEM_TAG = re.compile(r"<[a-z]+(_[a-z]+)+[\s>]|<in-app-browser-context\b|<heartbeat\b|<image name=\[")


def _is_real_user_text(txt: str, marker: str) -> bool:
    txt = (txt or "").strip()
    if not txt or txt.startswith(marker) or _SYSTEM_TAG.match(txt):
        return False
    if txt.startswith("# AGENTS.md instructions") or "<INSTRUCTIONS>" in txt or txt.startswith("# Files pasted"):
        return False
    return True


def _is_super_agent_thread(item: dict, marker: str, current_id: str = None) -> bool:
    """只认在超级助手里聊过的对话，过滤掉用户以前在 Codex 独立客户端里聊的旧记录。"""
    if current_id and item.get("id") == current_id:
        return True
    orig = item.get("originator")
    if orig in (None, "super-agent"):
        return True
    if (item.get("preview") or "").startswith(marker):
        return True
    rollout = item.get("path")
    if rollout and Path(rollout).is_file():
        try:
            text = Path(rollout).read_text(encoding="utf-8", errors="replace")
            if marker in text or '"super-agent"' in text:
                return True
        except OSError:
            pass
    return False


NO_USER_WORDS = "新对话（刚接手项目）"  # 这段对话里用户一句话都没说过


def _thread_title(item: dict, marker: str) -> str:
    return _user_words(item, marker) or NO_USER_WORDS


def _user_words(item: dict, marker: str) -> str:
    """给历史对话起一个用户一眼认得出的标题：优先取这段对话里用户最近说的一句话。用户没说过话就返回空。"""
    rollout = item.get("path")
    if rollout and Path(rollout).is_file():
        last_user = ""
        try:
            with open(rollout, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"user"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    pl = d.get("payload") or {}
                    if pl.get("type") == "message" and pl.get("role") == "user":
                        txt = "\n".join(c.get("text", "") for c in (pl.get("content") or [])
                                        if isinstance(c, dict)).strip()
                        if _is_real_user_text(txt, marker) and _clean_title(txt):
                            last_user = txt
        except OSError:
            pass
        if last_user:
            return _clean_title(last_user)

    for turn in reversed(item.get("turns") or []):
        for it in reversed(turn.get("items") or []):
            if it.get("type") == "userMessage":
                txt = _user_text(it).strip()
                if _is_real_user_text(txt, marker) and _clean_title(txt):
                    return _clean_title(txt)

    preview = (item.get("preview") or "").strip()
    if _is_real_user_text(preview, marker):
        return _clean_title(preview)
    name = (item.get("name") or "").strip()
    if name and not name.startswith("AGENTS.md") and not preview.startswith(marker):
        return _clean_title(name)  # 只有自动交代的对话，Codex 也会起名，这不算用户说过话
    return ""


def item_events(item: dict, marker: str) -> list:
    """把 Codex 的一个条目翻成聊天事件。不认识的条目（思考过程等）不显示。"""
    kind, iid = item.get("type"), item.get("id")
    status = _STATUS.get(item.get("status"), "done")
    if kind == "userMessage":
        text = _user_text(item)
        if text.startswith(marker):
            return [{"type": "opening", "text": text[len(marker):]}]
        return [{"type": "user", "text": text}]
    if kind == "agentMessage":
        return [{"type": "message", "item": iid, "text": item.get("text") or ""}]
    if kind == "commandExecution":
        return [{"type": "activity", "item": iid, "kind": "command", "label": command_label(item), "status": status,
                 "detail": (item.get("aggregatedOutput") or "")[-2000:]}]
    if kind == "fileChange":
        paths = [c.get("path", "") for c in item.get("changes") or []]
        names = "、".join(Path(p).name for p in paths[:3]) + ("等" if len(paths) > 3 else "")
        label = f"改 {len(paths)} 个文件：{names}" if paths else "改文件"
        return [{"type": "activity", "item": iid, "kind": "file", "label": label, "status": status,
                 "detail": "\n".join(paths)}]
    if kind == "imageGeneration":
        if item.get("savedPath"):
            return [{"type": "image", "item": iid, "path": item["savedPath"], "prompt": item.get("revisedPrompt") or ""}]
        if item.get("failure"):
            return [{"type": "activity", "item": iid, "kind": "image", "label": f"画图没成功：{item['failure']}",
                     "status": "failed", "detail": ""}]
        return [{"type": "activity", "item": iid, "kind": "image", "label": "画图中…", "status": "started", "detail": ""}]
    if kind == "imageView":
        return [{"type": "activity", "item": iid, "kind": "view", "label": "看图片 " + Path(item.get("path") or "").name,
                 "status": "done", "detail": item.get("path") or ""}]
    if kind == "webSearch":
        return [{"type": "activity", "item": iid, "kind": "search", "label": "搜索网页 " + (item.get("query") or ""),
                 "status": status, "detail": ""}]
    if kind == "mcpToolCall":
        return [{"type": "activity", "item": iid, "kind": "tool", "label": f"用工具 {item.get('tool') or ''}",
                 "status": status, "detail": ""}]
    return []


class CodexDispatcher:
    """一个项目一个。emit(事件) 由聊天会话提供；command 可换成测试用的“假 Codex”。"""

    name = "Codex"

    def __init__(self, project, emit, background: str = "", marker: str = "【超级助手自动交代】",
                 command=("codex", "app-server"), writer_wait: float = WRITER_WAIT_SECONDS,
                 writer_poll: float = WRITER_POLL_SECONDS, model: str = None, effort: str = None):
        self.project = str(Path(project).resolve())
        self.emit = emit
        self.background = background
        self.marker = marker
        self.model = model
        self.effort = effort
        cmd = list(command)
        if cmd == ["codex", "app-server"]:
            if model:
                cmd.extend(["-c", f'model="{model}"'])
            if effort:
                cmd.extend(["-c", f'model_reasoning_effort="{effort}"'])
        self.command = cmd
        self.writer_wait = writer_wait
        self.writer_poll = writer_poll
        self.proc = None
        self.thread_id = None
        self.turn_id = None
        self.busy = False
        self._lock = threading.Lock()
        self._pending = {}     # 我们问它、还没回答的：编号 -> 盒子
        self._asks = {}        # 它问我们、还没回答的：编号 -> (种类, 参数)
        self._next = 0
        self._closing = False  # 我们主动关掉时，不算“连接断了”

    # ---------- 底层：一行一条 JSON ----------

    def _write(self, obj) -> None:
        with self._lock:
            self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()

    def _request(self, method: str, params: dict, timeout: float = REQUEST_TIMEOUT) -> dict:
        with self._lock:
            self._next += 1
            rid = self._next
        box = {"event": threading.Event()}
        self._pending[rid] = box
        self._write({"id": rid, "method": method, "params": params})
        if not box["event"].wait(timeout):
            self._pending.pop(rid, None)
            raise TimeoutError(f"Codex 没有回应（{method}）")
        if "error" in box:
            message = (box["error"] or {}).get("message") if isinstance(box["error"], dict) else box["error"]
            raise RuntimeError(f"Codex 出错（{method}）：{message}")
        return box.get("result") or {}

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
                elif "id" in msg:
                    self._on_ask(msg["id"], msg["method"], msg.get("params") or {})
                elif "method" in msg:
                    self._on_note(msg["method"], msg.get("params") or {})
        except ValueError:
            pass
        # 读到头：Codex 退出了
        self.busy = False
        for box in list(self._pending.values()):
            box["error"] = {"message": "连接断了"}
            box["event"].set()
        self._pending.clear()
        if self.proc is not None and not self._closing:
            self.emit({"type": "status", "text": f"未连上 {self.name}"})
            self.emit({"type": "error", "text": "和 Codex 的连接断了。可以重新打开这个项目，或切到终端视图继续。"})

    # ---------- 对外 ----------

    def _resume_thread(self, params: dict) -> dict:
        """接着上一段对话；如果终端或后台服务还没松手（active writer），自动等它让出来。"""
        deadline = time.time() + max(0.0, self.writer_wait)
        notified = False
        while True:
            if self._closing:
                raise RuntimeError("连接已取消")
            try:
                return self._request("thread/resume", params)["thread"]
            except RuntimeError as exc:
                if "already has an active writer" not in str(exc):
                    raise
                if time.time() >= deadline:
                    raise RuntimeError("这段对话正在别处打开（如终端或其他窗口），请先关掉那边，再点一下左边这段对话重连") from exc
                if not notified:
                    notified = True
                    self.emit({"type": "status", "text": "正在等对话让出来…"})
                wait_until = min(deadline, time.time() + max(0.02, self.writer_poll))
                while time.time() < wait_until:
                    if self._closing:
                        raise RuntimeError("连接已取消") from exc
                    time.sleep(min(0.05, max(0.01, wait_until - time.time())))

    def start(self, opening_text: str = "", resume: bool = True, thread_id: str = None) -> str:
        """连上 Codex；thread_id 指定时切到那段历史对话；否则 resume=True 接着最近一次对话。"""
        self.proc = subprocess.Popen(self.command, cwd=self.project, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
        threading.Thread(target=self._read_loop, daemon=True).start()
        try:
            if self._closing:  # 启动途中就被取消了：close 当时还看不到这个进程，这里自己收掉
                raise RuntimeError("连接已取消")
            self._request("initialize", {"clientInfo": {"name": "super-agent", "title": "超级助手", "version": "0.2"}})
            self._write({"method": "initialized"})

            settings = {"cwd": self.project, "approvalPolicy": APPROVAL_POLICY, "sandbox": SANDBOX,
                        "approvalsReviewer": APPROVALS_REVIEWER, "developerInstructions": self.background or None}
            thread = None
            target_id = thread_id
            last_input = None
            if not target_id and resume:
                found = self._request("thread/list", {"cwd": self.project, "limit": 30,
                                                      "sortKey": "updated_at", "sortDirection": "desc"})
                items = found.get("data") or []
                # 接着用户真正聊过的那段；一段都没有才退回超级助手自己最新的，别的工具的对话不碰
                ours = [item for item in items if _is_super_agent_thread(item, self.marker)]
                latest = next((item for item in ours if _user_words(item, self.marker)), ours[0] if ours else None)
                if latest:
                    target_id = latest["id"]
            if target_id:
                thread = self._resume_thread(dict(settings, threadId=target_id))
                for turn in thread.get("turns") or []:
                    for item in turn.get("items") or []:
                        for event in item_events(item, self.marker):
                            if event.get("type") in ("opening", "user"):
                                last_input = event
                            self.emit(dict(event, history=True))
            if thread is None:
                thread = self._request("thread/start", settings)["thread"]
            self.thread_id = thread["id"]
            already_opened = (last_input is not None and last_input.get("type") == "opening"
                              and last_input.get("text") == opening_text)
            if opening_text and not thread_id and not already_opened:
                self.send(opening_text, opening=True)
            return self.thread_id
        except Exception:
            self.close()
            raise

    def _talked_in(self, item: dict) -> bool:
        return _is_super_agent_thread(item, self.marker) and bool(_user_words(item, self.marker))

    def list_threads(self, limit: int = 30) -> list:
        """列出当前项目在超级助手里的历史对话（最近聊过的排最前，按 id 去重）。"""
        if self.proc is None or self.proc.poll() is not None:
            return []
        found = self._request("thread/list", {"cwd": self.project, "limit": limit,
                                              "sortKey": "updated_at", "sortDirection": "desc"})
        seen = set()
        result = []
        for item in found.get("data") or []:
            tid = item.get("id")
            if not tid or tid in seen:
                continue
            seen.add(tid)
            if not _is_super_agent_thread(item, self.marker, self.thread_id):
                continue
            title = _user_words(item, self.marker)
            if not title:
                continue  # 用户没说过话的对话不进列表
            updated = item.get("updatedAt") or item.get("recencyAt") or item.get("createdAt") or 0
            result.append({
                "id": tid,
                "title": title,
                "updated": int(updated) if updated else 0,
                "current": (tid == self.thread_id),
            })
        return result

    def read_thread(self, thread_id: str) -> list:
        """只读取一段历史对话的内容，不切过去。切换前先备好，点的时候就不用等连接。"""
        if self.proc is None or self.proc.poll() is not None:
            return []
        thread = self._request("thread/read", {"threadId": thread_id, "includeTurns": True})["thread"]
        return [dict(event, history=True) for turn in thread.get("turns") or [] for item in turn.get("items") or []
                for event in item_events(item, self.marker)]

    def send(self, text: str, opening: bool = False) -> None:
        if self.busy:
            raise RuntimeError("调度员还在干活：等它说完，或先点“停止”")
        self.emit({"type": "opening" if opening else "user", "text": text})
        self.busy = True
        try:
            res = self._request("turn/start", {"threadId": self.thread_id, "input": [
                {"type": "text", "text": (self.marker + text) if opening else text}]})
        except Exception:
            self.busy = False
            raise
        self.turn_id = (res.get("turn") or {}).get("id") or self.turn_id

    def interrupt(self) -> None:
        if self.busy and self.turn_id:
            self._request("turn/interrupt", {"threadId": self.thread_id, "turnId": self.turn_id})

    def answer(self, ask_id: str, decision: str) -> None:
        """代用户回答“允许吗？”：accept / acceptForSession / decline。"""
        if decision not in ("accept", "acceptForSession", "decline"):
            raise ValueError(f"不认识的回答：{decision}")
        key = next((k for k in self._asks if str(k) == str(ask_id)), None)
        if key is None:
            raise KeyError("这个询问已经回答过或不存在")
        kind, params = self._asks.pop(key)
        if kind == "permissions":
            granted = {} if decision == "decline" else (params.get("permissions") or {})  # 拒绝 = 什么也不给
            result ={"permissions": granted, "scope": "session" if decision == "acceptForSession" else "turn"}
        elif kind.startswith("legacy"):
            result = {"decision": _LEGACY_DECISION[decision]}
        else:
            result = {"decision": decision}
        self._write({"id": key, "result": result})
        self.emit({"type": "approval_done", "id": str(key), "decision": decision})

    def close(self) -> None:
        self._closing = True
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait(timeout=2)
            for pipe in (self.proc.stdin, self.proc.stdout):
                try:
                    if pipe:
                        pipe.close()
                except OSError:
                    pass

    # ---------- Codex 说的 ----------

    def _on_note(self, method: str, p: dict) -> None:
        if method == "turn/started":
            self.turn_id = (p.get("turn") or {}).get("id") or self.turn_id
            self.emit({"type": "turn_started"})
        elif method == "item/agentMessage/delta":
            self.emit({"type": "delta", "item": p.get("itemId"), "text": p.get("delta") or ""})
        elif method == "item/started":
            item = p.get("item") or {}
            if item.get("type") in ("commandExecution", "fileChange", "imageGeneration", "webSearch", "mcpToolCall"):
                for event in item_events(dict(item, status="inProgress"), self.marker):
                    self.emit(event)
        elif method == "item/completed":
            item = p.get("item") or {}
            if item.get("type") != "userMessage":  # 用户的话发出去时已经显示过了
                for event in item_events(item, self.marker):
                    self.emit(event)
        elif method == "thread/tokenUsage/updated":
            total = ((p.get("tokenUsage") or {}).get("total") or {}).get("totalTokens")
            if total is not None:
                self.emit({"type": "usage", "total": total})
        elif method == "turn/completed":
            turn = p.get("turn") or {}
            self.busy = False
            self.emit({"type": "turn_done", "status": turn.get("status") or "completed",
                       "error": (turn.get("error") or {}).get("message")})
        elif method == "error":
            self.emit({"type": "error", "text": (p.get("error") or {}).get("message") or "Codex 报错",
                       "retry": bool(p.get("willRetry"))})

    def _on_ask(self, rid, method: str, p: dict) -> None:
        kinds = {"item/commandExecution/requestApproval": "command", "item/fileChange/requestApproval": "file",
                 "item/permissions/requestApproval": "permissions", "execCommandApproval": "legacy-command",
                 "applyPatchApproval": "legacy-file"}
        if method in kinds:
            kind = kinds[method]
            self._asks[rid] = (kind, p)
            command = p.get("command")
            if isinstance(command, list):
                command = " ".join(command)
            self.emit({"type": "approval", "id": str(rid), "kind": kind.replace("legacy-", ""),
                       "command": short_command(command, 300) if command else "",
                       "reason": p.get("reason") or ""})
        elif method == "item/tool/requestUserInput":
            self._write({"id": rid, "result": {"answers": {}}})
            self.emit({"type": "notice", "text": "调度员想问你几个选择题，聊天视图暂时还不支持，已跳过；需要时切到终端视图。"})
        else:
            self._write({"id": rid, "error": {"code": -32601, "message": f"超级助手不支持：{method}"}})
