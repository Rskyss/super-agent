"""聊天会话：一个项目一个会话，连着这个项目的调度员；把调度员吐出的聊天事件编号、存好，供页面来取。

- 页面用“从第几条之后”来取事件（等不到新的就最多等一会儿再回），断线重连不丢、不重复；
- 图片只放行调度员在事件里报出来的那些文件；
- 一次只连一个项目：打开别的项目时，前一个会话会先关掉（让出对话，免得和终端视图抢同一个对话）。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from collab.config import DEFAULT_MODELS, ConfigError, load_models
from collab.flow import summarize
from desktop.dispatchers.codex import CodexDispatcher
from desktop.opening import opening

THREAD_LIST_FILE = Path(".collab") / "对话列表.json"  # 打开软件先显示它，不等连接；只留在本机
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
        with self._cond:  # 和 close 互斥：要么这里看到已关，要么 close 能关到这条连接
            if self.closed:
                return
            self.adapter = self._factory(self.project, self.emit, words["background"], words["marker"])
        self.emit({"type": "status", "text": f"正在连接 {name}…"})
        # 打开软件和新对话都不自动发话，等用户说第一句；第一次接入的项目例外，要先起草项目说明
        task = words["first"] if with_opening and not thread_id and words["first_time"] else ""

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
                    self.emit({"type": "error", "text": f"连不上 {name}：{exc}。可以从菜单“对话 → 打开终端（高级）”继续。"})

        threading.Thread(target=run, daemon=True).start()

    def list_threads(self) -> list:
        """列出当前项目的全部历史对话。"""
        return self.read_threads() or []

    def read_threads(self):
        """和 list_threads 一样，但取失败时返回 None：取失败和真的没有不是一回事。"""
        if self.adapter is None or not hasattr(self.adapter, "list_threads"):
            return []
        try:
            return self.adapter.list_threads()
        except Exception:
            return None

    def delete_thread(self, thread_id: str) -> None:
        """删掉列表里的一段对话。不在列表里的（例如 Codex 独立客户端的旧对话）不删。"""
        failed = "没能删除这段对话，列表没有变。可能要升级 Codex。"
        if self.adapter is None or not hasattr(self.adapter, "delete_thread"):
            raise RuntimeError(failed)
        try:
            rows = self.adapter.list_threads()   # 不用 self.list_threads：它把取失败也当成没有
        except Exception as exc:
            raise RuntimeError(failed) from exc
        row = next((t for t in rows if t.get("id") == thread_id), None)
        if row is None:
            raise LookupError("这段对话已经不在了")
        if row.get("deletable") is False:
            raise LookupError("这段对话不是在超级助手里开始的，不能在这里删除")
        current = getattr(self.adapter, "thread_id", None)
        if thread_id == current and self.busy:
            raise RuntimeError("这段对话还在回复，等说完或先点停止再删")
        try:
            self.adapter.delete_thread(thread_id)
        except Exception as exc:
            raise RuntimeError(failed) from exc

    def read_thread(self, thread_id: str) -> list:
        """读取另一段历史对话的内容（不切过去），读不到就返回空。"""
        if self.adapter is None or not hasattr(self.adapter, "read_thread"):
            return []
        try:
            events = self.adapter.read_thread(thread_id)
        except Exception:
            return []
        return [dict(event, seq=index) for index, event in enumerate(events, 1)]

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
        with self._cond:
            self.closed = True
            adapter = self.adapter
            self._cond.notify_all()
        if adapter is not None:
            adapter.close()

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


def _load_thread_list(path: Path) -> dict:
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        threads = saved["threads"]
        if isinstance(threads, list) and all(isinstance(t, dict) for t in threads):
            return {"threads": threads, "current": saved.get("current")}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {"threads": [], "current": None}


def _save_thread_list(path: Path, data: dict) -> None:
    """项目还没有协作区就不存；内容没变也不写。存不了不影响使用。"""
    text = json.dumps(data, ensure_ascii=False).encode("utf-8")
    try:
        if path.parent.is_dir() and (not path.exists() or path.read_bytes() != text):
            spare = path.with_suffix(".tmp")
            spare.write_bytes(text)
            spare.replace(path)
    except OSError:
        pass


class ChatHub:
    """管着当前的会话。一次只连一个项目。"""

    def __init__(self, factories=None):
        self.factories = factories if factories is not None else default_factories()
        self._session = None
        self._lock = threading.Lock()
        self._deleted = set()

    def open(self, project, dispatcher: str, mode: str = "continue", with_opening: bool = True,
             thread_id: str = None) -> ChatSession:
        if dispatcher not in self.factories:
            raise ValueError("这个调度员暂时还不能用聊天，请用终端")
        key = str(Path(project).resolve())
        with self._lock:
            s = self._session
            same_thread = (not thread_id) or (getattr(getattr(s, "adapter", None), "thread_id", None) == thread_id)
            if s is not None and s.project == key and s.dispatcher == dispatcher and s.usable \
                    and mode != "fresh" and same_thread:
                return s
            old = s
            s = ChatSession(key, dispatcher, self.factories[dispatcher])
            self._session = s
        if old is not None:  # 关旧连接要几秒，不能占着锁，否则连续点击会排队、顺序还会乱
            old.close()
        s.open(mode, with_opening=with_opening, thread_id=thread_id)
        return s

    def thread_list(self, project) -> dict:
        """左栏的历史对话：连着调度员就取最新的并存一份；没连上就先给上次存的。"""
        session = self.get(project)
        current = getattr(getattr(session, "adapter", None), "thread_id", None)
        path = Path(project) / THREAD_LIST_FILE
        rows = session.read_threads() if current is not None and session.usable else None
        # 删除前就发出去的取列表，回答可能晚到：不能把删掉的写回去
        threads = [t for t in rows or [] if t.get("id") not in self._deleted]
        # 断了、取失败、取回来是空，都按没连上：显示记住的列表，不给删除。
        # 空不代表真的没有：只取最新 30 段，可能全被外来对话占满。删最后一段时记住的列表已经改过了
        if threads:
            _save_thread_list(path, {"threads": threads, "current": current})
            listed = [dict(t, busy=(t.get("id") == current and session.busy)) for t in threads]
            return {"threads": listed, "current": current, "cached": False}
        return dict(_load_thread_list(path), cached=True)

    def delete_thread(self, project, thread_id: str) -> None:
        """删对话并更新本机记住的列表。列表空了也要写，否则重开软件它会回来。"""
        session = self.get(project)
        connected = session is not None and session.usable and getattr(session.adapter, "thread_id", None) is not None
        if not connected:
            raise RuntimeError("这个项目的调度员还没连上")
        session.delete_thread(thread_id)
        self._deleted.add(thread_id)
        path = Path(project) / THREAD_LIST_FILE
        saved = _load_thread_list(path)
        rest = [t for t in saved["threads"] if t.get("id") != thread_id]
        _save_thread_list(path, {"threads": rest, "current": None if saved["current"] == thread_id else saved["current"]})

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
