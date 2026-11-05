"""聊天会话：一个项目一个会话，连着这个项目的调度员；把调度员吐出的聊天事件编号、存好，供页面来取。

- 页面用“从第几条之后”来取事件（等不到新的就最多等一会儿再回），断线重连不丢、不重复；
- 图片只放行调度员在事件里报出来的那些文件；
- 一次只连一个项目：打开别的项目时，前一个会话会先关掉（让出对话，免得和终端视图抢同一个对话）。
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from collab.config import DEFAULT_MODELS, ConfigError, load_models
from collab.flow import summarize
from desktop.dispatchers.codex import CodexDispatcher
from desktop.opening import opening

_NAMES = {"codex": "Codex", "claude": "Claude", "cursor": "Cursor", "agy": "agy"}


def _codex_factory(project, emit, background, marker):
    try:
        models = load_models(project)
    except ConfigError:
        models = DEFAULT_MODELS
    return CodexDispatcher(project, emit, background=background, marker=marker,
                           model=models.get("codex"), effort=models.get("codex_effort"))


def default_factories() -> dict:
    return {"codex": _codex_factory}


class ChatSession:
    def __init__(self, project, dispatcher: str, factory):
        self.project = str(Path(project).resolve())
        self.dispatcher = dispatcher
        self._factory = factory
        self._events = []
        self._stamps = {}
        self._turn_start = None
        self._last_active = None
        self._turn_end = None
        self._cond = threading.Condition()
        self._images = set()
        self.adapter = None
        self.closed = False
        self.failed = False

    # ---------- 事件 ----------

    def emit(self, event: dict) -> None:
        now = datetime.now()
        with self._cond:
            seq = len(self._events) + 1
            event = dict(event, seq=seq)
            self._events.append(event)
            self._stamps[seq] = now
            if event.get("type") == "image" and event.get("path"):
                self._images.add(str(Path(event["path"]).resolve()))
            if not event.get("history"):
                kind = event.get("type")
                if kind in ("user", "opening"):
                    self._turn_start = now
                    self._last_active = now
                    self._turn_end = None
                elif kind == "turn_started":
                    if self._turn_start is None:
                        self._turn_start = now
                    self._last_active = now
                    self._turn_end = None
                elif kind in ("delta", "message", "activity", "image", "approval", "approval_done"):
                    if self._turn_start is None:
                        self._turn_start = now
                    self._last_active = now
                elif kind in ("turn_done", "error"):
                    self._turn_end = now
            self._cond.notify_all()

    def events_since(self, seq: int, timeout: float = 20) -> list:
        """第 seq 条之后的事件；暂时没有就最多等 timeout 秒。"""
        with self._cond:
            if len(self._events) <= seq and timeout > 0 and not self.closed:
                self._cond.wait(timeout)
            return list(self._events[seq:])

    def image_allowed(self, path: str) -> bool:
        try:
            return str(Path(path).resolve()) in self._images
        except OSError:
            return False

    # ---------- 连接 ----------

    def open(self, mode: str = "continue", with_opening: bool = True, thread_id: str = None) -> None:
        """在后台连上调度员（连 Codex 可能要十几秒），页面通过事件看到进度。
        with_opening=False：从终端视图切回来或切历史对话时用，不重复发开场交代。"""
        words = opening(self.project)
        name = _NAMES.get(self.dispatcher, self.dispatcher)
        self.emit({"type": "status", "text": f"正在连接 {name}…"})
        self.adapter = self._factory(self.project, self.emit, words["background"], words["marker"])
        task = "" if thread_id else ((words["first"] if words["first_time"] else words["every"]) if with_opening else "")

        def run():
            try:
                try:
                    self.adapter.start(task, resume=(mode != "fresh"), thread_id=thread_id)
                except TypeError:
                    self.adapter.start(task, resume=(mode != "fresh"))
                if not self.closed:
                    self.emit({"type": "status", "text": f"已连上 {name}",
                               "thread_id": getattr(self.adapter, "thread_id", None)})
            except Exception as exc:  # 连不上：把原因告诉用户，并给出退路
                self.failed = True
                if not self.closed:
                    self.emit({"type": "status", "text": f"未连上 {name}"})
                    self.emit({"type": "error", "text": f"连不上 {name}：{exc}。可以切到终端视图继续。"})

        threading.Thread(target=run, daemon=True).start()

    def list_threads(self) -> list:
        """列出当前项目的全部历史对话。"""
        if self.adapter is None or not hasattr(self.adapter, "list_threads"):
            return []
        try:
            return self.adapter.list_threads()
        except Exception:
            return []

    @property
    def usable(self) -> bool:
        """还能不能接着用：没关、没连败，且底层进程没掉线。"""
        if self.closed or self.failed:
            return False
        proc = getattr(self.adapter, "proc", None)
        if proc is not None and getattr(self.adapter, "thread_id", None) is not None and proc.poll() is not None:
            return False
        return True

    @property
    def busy(self) -> bool:
        return bool(self.adapter and self.adapter.busy)

    def send(self, text: str) -> None:
        self._need_adapter().send(text)

    def answer(self, ask_id: str, decision: str) -> None:
        self._need_adapter().answer(ask_id, decision)

    def stop(self) -> None:
        self._need_adapter().interrupt()

    def close(self) -> None:
        self.closed = True
        if self.adapter is not None:
            self.adapter.close()
        with self._cond:
            self._cond.notify_all()

    def _need_adapter(self):
        if self.adapter is None or getattr(self.adapter, "thread_id", None) is None:
            raise RuntimeError("调度员还没连上，请稍等")
        return self.adapter

    # ---------- 给右边流转面板看的实时状态 ----------

    def event_text(self, seq: int):
        """右边交接列表点开某一条时，取它的全文。"""
        with self._cond:
            if seq < 1 or seq > len(self._events):
                return None
            ev = self._events[seq - 1]
        kind = ev.get("type")
        if kind in ("user", "opening", "message", "error", "notice"):
            return ev.get("text") or ""
        if kind == "activity":
            detail = (ev.get("detail") or "").strip()
            return f"{ev.get('label', '')}\n\n{detail}".strip()
        if kind == "approval":
            reason = f"\n理由：{ev['reason']}" if ev.get("reason") else ""
            return f"{ev.get('command') or ''}{reason}".strip()
        return ""

    def live_summary(self, now: datetime = None):
        """把左边聊天里正在发生的事整理成右边面板能看懂的进度与交接。"""
        now = now or datetime.now()
        with self._cond:
            events = list(self._events)
            stamps = dict(self._stamps)
            turn_start = self._turn_start
            last_active = self._last_active
            turn_end = self._turn_end
        busy = self.busy
        who = f"调度员 {_NAMES.get(self.dispatcher, self.dispatcher)}"

        last_user_idx = None
        last_open_idx = None
        for i, e in enumerate(events):
            if e.get("type") == "user":
                last_user_idx = i
            elif e.get("type") == "opening":
                last_open_idx = i
        start_idx = last_user_idx if last_user_idx is not None else last_open_idx
        if start_idx is None:
            return None

        if last_user_idx is not None:
            user_text = events[last_user_idx].get("text") or ""
            title = summarize(user_text, 40) or "和调度员对话中"
            user_detail = summarize(user_text, 80) or title
        else:
            title = "熟悉项目与读取交接"
            user_detail = "自动交代：先读项目说明、台账和任务板"

        asks = {}
        going = None
        last_act = None
        last_msg = None
        interrupted = False
        for e in events[start_idx + 1:]:
            t = e.get("type")
            if t == "approval":
                asks[str(e.get("id"))] = e
            elif t == "approval_done":
                asks.pop(str(e.get("id")), None)
            elif t == "activity":
                last_act = e.get("label") or last_act
                going = e.get("label") if e.get("status") == "started" else (
                    None if going == e.get("label") else going)
            elif t == "message" and (e.get("text") or "").strip():
                last_msg = summarize(e["text"], 70)
            elif t == "turn_done" and e.get("status") == "interrupted":
                interrupted = True

        asking = bool(asks)
        if asking:
            ask = list(asks.values())[-1]
            what = ask.get("command") or ask.get("reason") or "执行操作"
            step_text = f"等你允许：{ summarize(what, 60) }"
        elif busy:
            if going:
                step_text = going if going.endswith("…") else f"{going}…"
            elif last_act and last_msg:
                step_text = f"{last_act} · {last_msg}"
            else:
                step_text = last_act or last_msg or "正在分析与检查…"
        elif interrupted:
            step_text = "已停止"
        else:
            step_text = last_msg or last_act or "已回复，等你指示下一步"

        elapsed = 0
        quiet = None
        if turn_start is not None:
            end_t = now if busy else (turn_end or last_active or now)
            elapsed = max(0, int((end_t - turn_start).total_seconds()))
            if busy:
                quiet = max(0, int((now - (last_active or turn_start)).total_seconds()))

        # 整理交接列表：用户提问、调度员的阶段回复、关键动作
        handoffs = []
        seen_items = {}
        for e in events:
            seq = e.get("seq")
            when = stamps.get(seq)
            tstr = when.strftime("%H:%M:%S") if when else ""
            t = e.get("type")
            if t == "user":
                handoffs.append({"seq": seq, "kind": "需求", "time": tstr, "from": "你", "to": who,
                                 "summary": summarize(e.get("text") or "", 80), "tone": "", "has_text": True})
            elif t == "opening" and not e.get("history"):
                handoffs.append({"seq": seq, "kind": "交代", "time": tstr, "from": "超级助手", "to": who,
                                 "summary": "自动交代：读项目说明与交接记录", "tone": "", "has_text": True})
            elif t == "message" and (e.get("text") or "").strip():
                handoffs.append({"seq": seq, "kind": "回复", "time": tstr, "from": who, "to": "你",
                                 "summary": summarize(e.get("text") or "", 80), "tone": "ok", "has_text": True})
            elif t == "activity" and not e.get("history"):
                iid = e.get("item")
                tone = "bad" if e.get("status") in ("failed", "declined") else ""
                row = {"seq": seq, "kind": "动作", "time": tstr, "from": who, "to": "",
                       "summary": e.get("label") or "执行动作", "tone": tone,
                       "has_text": bool((e.get("detail") or "").strip())}
                if iid and iid in seen_items:
                    handoffs[seen_items[iid]] = row
                else:
                    if iid:
                        seen_items[iid] = len(handoffs)
                    handoffs.append(row)
            elif t == "approval" and not e.get("history"):
                handoffs.append({"seq": seq, "kind": "询问", "time": tstr, "from": who, "to": "你",
                                 "summary": "请求允许：" + (e.get("command") or e.get("reason") or ""),
                                 "tone": "", "has_text": True})

        return {
            "title": title,
            "user_detail": user_detail,
            "busy": busy,
            "asking": asking,
            "step_text": step_text,
            "elapsed": elapsed,
            "quiet": quiet,
            "handoffs": handoffs,
            "updated": (last_active or turn_start).timestamp() if (last_active or turn_start) else 0.0,
        }


class ChatHub:
    """管着当前的会话。一次只连一个项目。"""

    def __init__(self, factories=None):
        self.factories = factories if factories is not None else default_factories()
        self._session = None
        self._lock = threading.Lock()

    def open(self, project, dispatcher: str, mode: str = "continue", with_opening: bool = True,
             thread_id: str = None) -> ChatSession:
        if dispatcher not in self.factories:
            raise ValueError("这个调度员暂时还不能用聊天视图，请用终端视图")
        key = str(Path(project).resolve())
        with self._lock:
            s = self._session
            same_thread = (not thread_id) or (getattr(getattr(s, "adapter", None), "thread_id", None) == thread_id)
            if s is not None and s.project == key and s.dispatcher == dispatcher and s.usable \
                    and mode != "fresh" and same_thread:
                return s
            if s is not None:
                s.close()
            s = ChatSession(key, dispatcher, self.factories[dispatcher])
            self._session = s
        s.open(mode, with_opening=with_opening, thread_id=thread_id)
        return s

    def get(self, project):
        s = self._session
        if s is not None and not s.closed and s.project == str(Path(project).resolve()):
            return s
        return None

    def close(self, project) -> None:
        s = self.get(project)
        if s is not None:
            s.close()

    def close_all(self) -> None:
        if self._session is not None:
            self._session.close()
