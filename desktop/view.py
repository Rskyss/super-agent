"""右边流转面板的数据：读项目的协作记录，整理成“现在 / 流程各站 / 交接 / 卡片”。

只读，不写任何文件。规则只有一条：界面上显示的每一样东西，都来自记录里真实存在的内容。
没有流转记录的旧卡片，只按状态显示大致位置，并明说“只能看到大致状态”。
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from collab.config import ConfigError, load_team
from collab.flow import card_title, read_flow, summarize, text_of, working_now
from collab.phase import card_phase, process_is_alive
from collab.judge import PASS
from collab.state import Card

QUIET_WARN_SECONDS = 10 * 60  # 超过 10 分钟没有任何动静，提示可能卡住
TERMINAL_STATES = {"待验收", "已合并", "需Claude介入", "已放弃"}

TOOL_NAMES = {"claude": "Claude", "codex": "Codex", "cursor": "Cursor", "agy": "agy", "qwen": "千问"}
TOOL_FACES = {"claude": "Cl", "codex": "Cx", "cursor": "Cu", "agy": "ag", "qwen": "千"}
DEFAULT_TOOLS = {"测试员": "codex", "开发员": "cursor", "复查员": "agy"}
ROLE_KEYS = {"测试员": "test", "开发员": "dev", "复查员": "rev"}
_VERBS = {"写测试": "正在写测试", "改测试": "正在改测试", "开发": "正在写代码", "复查": "正在复查"}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

_activity_cache: dict = {}  # 副本目录 -> (算的时间, 最近改动时间)；副本可能很大，10 秒内不重复扫


def _name(tool) -> str:
    return TOOL_NAMES.get(tool or "", tool or "")


def _face(tool) -> str:
    return TOOL_FACES.get(tool or "", (tool or "?")[:2])


def _mmss(seconds) -> str:
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _when(event):
    try:
        return datetime.fromisoformat(event["time"])
    except (KeyError, ValueError, TypeError):
        return None


_title = card_title


def _card_dirs(project: Path) -> dict:
    base = project / ".collab" / "cards"
    if not base.is_dir():
        return {}
    return {d.name: d for d in base.iterdir() if d.is_dir() and (d / "状态.json").exists()}


ARCHIVE_MARK = "已归档.json"


def _updated(cdir: Path) -> float:
    stamps = [p.stat().st_mtime for p in (cdir / "状态.json", cdir / "流转.jsonl") if p.exists()]
    return max(stamps) if stamps else 0.0


def _finished(phase: dict) -> bool:
    return phase.get("tone") == "done" and not phase.get("needs_you")


def _archived(cdir: Path, phase: dict) -> bool:
    """有归档标记，并且卡现在是结束的。要用户处理的卡不能藏着。"""
    return (cdir / ARCHIVE_MARK).exists() and _finished(phase)


def set_archived(project_dir, card_id: str, on: bool) -> None:
    project = Path(project_dir)
    cdir = _card_dirs(project).get(card_id)
    if cdir is None:
        raise LookupError("没有这张卡片")
    mark = cdir / ARCHIVE_MARK
    if on:
        phase = card_phase(str(project), card_id)
        if not _finished(phase):
            raise RuntimeError("这张卡还没结束，不能归档")
        if not mark.exists():
            mark.write_text("{}", encoding="utf-8")
        return
    if mark.exists():
        mark.unlink()


def list_cards(project_dir) -> list:
    """项目里所有卡片，最近有动静的排在最前。读不出来的卡直接跳过。"""
    project = Path(project_dir)
    cards = []
    for card_id, cdir in _card_dirs(project).items():
        try:
            card = Card.load(cdir / "状态.json")
            phase = card_phase(str(project), card_id)
        except (OSError, ValueError, TypeError, KeyError):
            continue
        cards.append({"id": card_id, "title": _title(cdir, card_id), "state": card.state,
                      "rework": card.rework, "updated": _updated(cdir), "phase": phase,
                      "archived": _archived(cdir, phase)})
    cards.sort(key=lambda c: (c["updated"], c["id"]), reverse=True)
    return cards


def event_text(project_dir, card_id: str, seq: int, chat_session=None):
    """某一条交接的全文。卡片编号必须是项目里真实存在的卡，防止借路径读别的文件。"""
    if card_id == "调度中" and chat_session is not None:
        return chat_session.event_text(seq)
    cdir = _card_dirs(Path(project_dir)).get(card_id)
    if cdir is None:
        return None
    for event in read_flow(cdir):
        if event.get("seq") == seq:
            if not event.get("text"):
                return event.get("summary", "")
            target = (cdir / event["text"]).resolve()
            if cdir.resolve() not in target.parents:
                return None
            return text_of(cdir, event)
    return None


def _last_line(path: Path) -> str:
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 4096))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    lines = [_ANSI.sub("", ln).strip() for ln in tail.splitlines()]
    lines = [ln for ln in lines if ln]
    return lines[-1] if lines else ""


_AGY_READ = ("read_file", "view_file")
_AGY_EDIT = ("edit_file", "write_file", "write_to_file", "replace_file_content", "multi_replace_file_content", "sed_file")
_AGY_FIND = ("grep_search", "find_by_name", "list_dir")
_CURSOR_KEYS = (("readToolCall", "read"), ("editToolCall", "edit"), ("writeToolCall", "edit"),
                ("deleteToolCall", "edit"), ("grepToolCall", "find"), ("globToolCall", "find"))


def _command_words(cmd: str, test_command: str = "") -> str:
    """跑命令翻成大白话（㊷：不显示命令原文）。"""
    cmd = (cmd or "").strip()
    if test_command and (cmd == test_command.strip() or test_command.strip() in cmd):
        return "在跑本卡测试"
    low = cmd.lower()
    if "unittest" in low or "pytest" in low:
        return "在跑测试"
    if re.search(r"\b(python3?|node)\s+-[ce]\b", low) or "<<" in cmd:
        return "在跑一段自己写的验证小程序"
    return "在跑命令"


def _stream_action(ev: dict):
    """一行边干边吐的记录 → (动作, 目标, 说的话)。动作：read / edit / find / run；只认“开始做”的那一行。"""
    if not isinstance(ev, dict):
        return None
    if ev.get("event") == "step_update" and isinstance(ev.get("step_update"), dict):
        step = ev["step_update"]
        if step.get("state") != "ACTIVE" or step.get("step_type") != "tool":
            return None
        name = str(step.get("tool_name") or "")
        params = (step.get("tool_info") or {}).get("parameters") or {}
        params = params if isinstance(params, dict) else {}
        target = next((str(v) for k, v in params.items()
                       if isinstance(v, str) and v and ("path" in k.lower() or "file" in k.lower())), "")
        if name in _AGY_READ:
            return ("read", target or str(step.get("step_index")), "")
        if name in _AGY_EDIT:
            return ("edit", str(step.get("step_index")), "")
        if name in _AGY_FIND:
            return ("find", "", "")
        if name == "run_command":
            return ("run", str(params.get("CommandLine") or params.get("command") or ""), "")
        return ("other", "", "")
    if ev.get("type") == "assistant":
        parts = (ev.get("message") or {}).get("content") or []
        text = "".join(c.get("text", "") for c in parts if isinstance(c, dict) and c.get("type") == "text").strip()
        return ("say", "", text.splitlines()[-1].strip()) if text else None
    if ev.get("type") == "tool_call" and ev.get("subtype") == "started":
        call = ev.get("tool_call") or {}
        for key, act in _CURSOR_KEYS:
            if key in call:
                args = (call[key] or {}).get("args") or {}
                target = str(args.get("path") or args.get("pattern") or args.get("globPattern") or "")
                return (act, target if act == "read" else str(ev.get("call_id") or ""), "")
        if "shellToolCall" in call:
            return ("run", str(((call["shellToolCall"] or {}).get("args") or {}).get("command") or ""), "")
    return None


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _stream_summary(lines: list, test_command: str = "") -> str:
    """Cursor、agy 一行一个 JSON：最近在干什么 + 读了几个文件、改了几处（㊷：大白话，不放文件名和命令）。"""
    read, edits, last = set(), set(), None
    for ln in lines:
        if not (ln.startswith("{") and ln.endswith("}")):
            continue
        try:
            act = _stream_action(json.loads(ln))
        except ValueError:
            continue
        if not act:
            continue
        if act[0] == "read":
            read.add(act[1])
        elif act[0] == "edit":
            edits.add(act[1] or f"#{len(edits)}")  # 没有编号的每条算一处（复查 A 第 9 条）
        last = act
    if last is None:
        return ""
    if last[0] == "say":
        return last[2]
    head = {"read": "在读代码", "edit": "在改代码", "find": "在查找代码", "other": "在干活"}.get(last[0]) \
        or _command_words(last[1], test_command)
    counts = [f"已读 {len(read)} 个文件"] if read else []
    if edits:
        counts.append(f"改了 {len(edits)} 处")
    return f"{head}（{'、'.join(counts)}）" if counts else head


def live_hint(path: Path, limit: int = 60, tool: str = "", test_command: str = "") -> str:
    """实时输出里干活的人此刻在干什么，大白话一句（流程修理 ⑪、第二轮 ㊷）。没有就空。
    Codex 只认它自己说的话（每段话前面单独一行 codex；它印出来的 agy、Cursor 日志不算，㉙ 复查第 1 轮）；
    Cursor、agy 只认一行一个的 JSON，并数一数读了几个文件、改了几处；不知道是谁就两种都试。"""
    try:
        with Path(path).open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 16 * 1024 * 1024))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    lines = [_ANSI.sub("", ln).strip() for ln in tail.splitlines()]
    if tool not in ("codex", "qwen"):  # 千问用 Codex 的壳，输出和 Codex 一样
        said = _stream_summary(lines, test_command)
        if said:
            return _cut(said, limit)
    for i in range(len(lines) - 1 if tool in ("", "codex", "qwen") else -1, -1, -1):
        if lines[i] == "codex":
            for ln in lines[i + 1:]:
                if ln:
                    return _cut(ln, limit)
            return ""
    return ""


def _newest_in(worktree: Path):
    hit = _activity_cache.get(str(worktree))
    if hit and time.time() - hit[0] < 10:
        return hit[1]
    newest = None
    for root, dirs, files in os.walk(worktree):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules")]
        for name in files:
            try:
                mtime = os.path.getmtime(os.path.join(root, name))
            except OSError:
                continue
            newest = mtime if newest is None else max(newest, mtime)
    _activity_cache[str(worktree)] = (time.time(), newest)
    return newest


def _quiet_seconds(project: Path, card_id: str, cdir: Path, open_ev, now: datetime):
    """离最近一次动静过了多久：实时输出文件、独立副本里的文件，取最新的那个。"""
    stamps = []
    if open_ev.get("live"):
        live = cdir / open_ev["live"]
        if live.exists():
            stamps.append(live.stat().st_mtime)
    worktree = project.parent / f"{project.name}-worktrees" / card_id
    if worktree.is_dir():
        newest = _newest_in(worktree)
        if newest:
            stamps.append(newest)
    if not stamps:
        started = _when(open_ev)
        return (now - started).total_seconds() if started else None
    return max(0.0, now.timestamp() - max(stamps))


def _label(role: str, tool) -> str:
    if role in ROLE_KEYS:
        return f"{role} {_name(tool)}".strip()
    return role


def _tone(event) -> str:
    kind = event.get("kind")
    summary = event.get("summary", "")
    if kind in ("判定", "页面检查"):
        return "ok" if event.get("result") == PASS else "bad"
    if kind == "回复":
        if not event.get("ok", True) or summary.startswith("结论：不通过"):
            return "bad"
        return "ok" if summary.startswith("结论：通过") else ""
    if kind == "转人工":
        return "bad"
    if kind == "结束":
        return "ok"
    return ""


def _handoffs(events: list) -> list:
    out = []
    for e in events:
        when = _when(e)
        tool = e.get("tool")
        out.append({
            "seq": e.get("seq"), "kind": e.get("kind"), "time": when.strftime("%H:%M:%S") if when else "",
            "from": _label(e.get("src", ""), tool if e.get("kind") == "回复" else None),
            "to": _label(e.get("dst", ""), tool if e.get("kind") == "派活" else None),
            "summary": e.get("summary", ""), "tone": _tone(e), "has_text": bool(e.get("text")),
        })
    return out


def _dispatcher(saved) -> tuple:
    """调度员的名字和头像字：桌面窗口告诉面板用户选的是谁；没说就不猜，只写“调度员”。"""
    saved = (saved or "").strip()
    if not saved:
        return "调度员", "调"
    if saved.startswith("custom:"):
        words = saved[len("custom:"):].split()
        name = words[0] if words else "自定义"
        return name, name[:2]
    return _name(saved), _face(saved)


def _stations(tools: dict, reviewer_on: bool, dispatcher=None) -> list:
    disp_who, disp_face = _dispatcher(dispatcher)
    return [
        {"key": "you", "role": "提需求", "who": "你", "face": "你"},
        {"key": "disp", "role": "调度", "who": disp_who, "face": disp_face},
        {"key": "test", "role": "写测试", "who": _name(tools["测试员"]), "face": _face(tools["测试员"])},
        {"key": "dev", "role": "写代码", "who": _name(tools["开发员"]), "face": _face(tools["开发员"])},
        {"key": "check", "role": "跑测试", "who": "程序", "face": "检"},
        {"key": "rev", "role": "复查", "who": _name(tools["复查员"]) if reviewer_on else "不复查",
         "face": _face(tools["复查员"]) if reviewer_on else "—"},
        {"key": "accept", "role": "确认", "who": "你", "face": "你"},
    ]


_STATION_ROLE = {"test": "测试员", "dev": "开发员", "rev": "复查员"}


def _team_disp_model(project) -> tuple:
    """团队设置里调度员这一岗的（工具, 模型（强度））。"""
    try:
        from collab.config import load_role_models, model_label
        mine = load_role_models(project).get("dispatcher") or {}
    except Exception:  # noqa: BLE001 — 配置读不出就不显示模型
        return "", ""
    tool = str(mine.get("tool") or "")
    return tool, model_label(tool, {tool: mine.get("model"), "codex_effort": mine.get("effort")}) if tool else ""


def _add_models(stations, events, project=None) -> None:
    """右上角“工具 · 模型（强度）”（㉗ ㊳）：开跑时记下的各岗模型、写方案时记下的调度员模型；老卡没有就只写工具。"""
    models, disp_model, disp_tool = {}, "", ""
    for e in events:
        if e.get("kind") == "开跑" and isinstance(e.get("models"), dict):
            models.update({k: v for k, v in e["models"].items() if v})
        if e.get("kind") == "写方案" and e.get("model"):
            disp_model, disp_tool = str(e["model"]), str(e.get("dst") or "")
    for s in stations:
        if s["key"] == "disp":
            if s.get("who") == "调度员" and disp_tool:
                s["who"] = disp_tool  # 窗口没告诉是谁时，用写方案记下的那家
            # 写方案的是 Codex；调度员换成别家时不能把 Codex 的模型安到它头上（复查 A 第 7 条），用团队设置里它自己的
            label = disp_model if disp_tool and s.get("who") == disp_tool else ""
            if not label and project is not None:
                tool, team_label = _team_disp_model(project)
                label = team_label if tool and _name(tool) == s.get("who") else ""
        else:
            label = models.get(_STATION_ROLE.get(s["key"], ""), "")
        # 模型单独放一项，“who”照旧是工具名：点步骤看交接按工具名找（复查 A 第 2 条）
        s["model"] = label if label and s.get("who") not in ("", "不复查") else ""


def _card_test_command(cdir: Path) -> str:
    try:
        raw = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(raw.get("test_command") or "") if isinstance(raw, dict) else ""


def _set(stations, key, state, detail="", **extra):
    for s in stations:
        if s["key"] == key:
            s.update({"state": state, "detail": detail, "live": False, "elapsed": None, "quiet": None})
            s.update(extra)


_FALLBACK_ACTIVE = {"待对齐": 1, "待派发": 1, "开发中": 3, "待测试": 4, "测试中": 4,
                    "待验收": 6, "已合并": 7, "需Claude介入": 1}


def _fallback(stations, state: str):
    """没有流转记录的旧卡：只按状态推一个大致位置。"""
    active = _FALLBACK_ACTIVE.get(state, 0)
    for i, s in enumerate(stations):
        if i < active:
            _set(stations, s["key"], "done")
        elif i == active:
            mark = "fail" if state == "需Claude介入" else ("wait" if state == "待验收" else "active")
            _set(stations, s["key"], mark, state)
        else:
            _set(stations, s["key"], "idle")


def _chat_view(base: dict, chat: dict, tools: dict, reviewer_on: bool, dispatcher=None) -> dict:
    """还没有正式建任务卡片（或旧卡已合并），左边调度员正在排查/对齐时的实时流转视图。"""
    stations = _stations(tools, reviewer_on, dispatcher)
    disp_who, _ = _dispatcher(dispatcher)
    _set(stations, "you", "done", chat["user_detail"])
    if chat["asking"]:
        _set(stations, "disp", "wait", chat["step_text"], elapsed=chat["elapsed"])
        now_text, tone = f"调度员 {disp_who} {chat['step_text']}", "wait"
    elif chat["busy"]:
        _set(stations, "disp", "active", chat["step_text"], live=True,
             elapsed=chat["elapsed"], quiet=chat["quiet"])
        now_text, tone = f"调度员 {disp_who} 正在干活：{chat['step_text']}", "run"
    else:
        _set(stations, "disp", "wait", chat["step_text"])
        now_text, tone = f"调度员 {disp_who}：{chat['step_text']}", "wait"
    _set(stations, "test", "idle", "等建卡派工")
    _set(stations, "dev", "idle", "等测试写完")
    _set(stations, "check", "idle", "等代码写完")
    _set(stations, "rev", "idle", "等测试通过" if reviewer_on else "这次不复查")
    _set(stations, "accept", "idle", "等全部做完")
    state = "调度中" if (chat["busy"] or chat["asking"]) else "待对齐"
    return dict(base, card={"id": "调度中", "title": chat["title"], "state": state, "rework": 0, "round": 1},
                stations=stations, handoffs=chat["handoffs"], elapsed=chat["elapsed"],
                now={"text": now_text, "tone": tone})


def card_view(project_dir, card_id=None, now=None, dispatcher=None, chat_session=None) -> dict:
    project = Path(project_dir)
    now = now or datetime.now()
    installed = (project / ".collab").is_dir()
    tools = dict(DEFAULT_TOOLS)
    team_reviewer_on = True
    if installed:  # 团队配置里的人：没开工的卡也按配置显示；开工后以流转记录为准
        try:
            team = load_team(project)
        except ConfigError:
            team = None
        if team:
            tools.update({"测试员": team["tester"], "开发员": team["developer"]})
            team_reviewer_on = team["reviewer"] != "none"
            if team_reviewer_on:
                tools["复查员"] = team["reviewer"]
            dispatcher = dispatcher or team["dispatcher"]
    chat = chat_session.live_summary(now) if chat_session is not None else None
    base = {"installed": installed, "project": project.name, "card": None, "cards": [], "handoffs": [],
            "back": False, "stale": False, "elapsed": 0, "stations": _stations(tools, team_reviewer_on, dispatcher)}
    for s in base["stations"]:
        _set(base["stations"], s["key"], "idle")
    if not installed:
        if chat:
            return _chat_view(base, chat, tools, team_reviewer_on, dispatcher)
        base["now"] = {"text": "这个项目还没装超级助手", "tone": "idle"}
        return base

    cards = list_cards(project)
    base["cards"] = cards
    dirs = _card_dirs(project)
    explicit_card = card_id
    if explicit_card in dirs:
        chat = None   # 用户点开的是这张卡：只看它自己的记录，主对话正在聊什么和它无关
    top = next((c for c in cards if not c.get("archived")), None)
    if card_id is None and top is not None:
        card_id = top["id"]
    if card_id not in dirs:
        if chat:
            return _chat_view(base, chat, tools, team_reviewer_on, dispatcher)
        base["now"] = {"text": "等你提需求", "tone": "idle"}
        return base
    if explicit_card is None and chat and top is not None and top["state"] == "已合并" \
            and (chat["busy"] or chat["asking"] or chat["updated"] > top["updated"]):
        return _chat_view(base, chat, tools, team_reviewer_on, dispatcher)

    cdir = dirs[card_id]
    card = Card.load(cdir / "状态.json")
    state, rnd = card.state, card.rework + 1
    events = read_flow(cdir)

    reviewer_on = team_reviewer_on
    for e in events:
        if e.get("kind") == "开跑" and isinstance(e.get("tools"), dict):
            tools.update({k: v for k, v in e["tools"].items() if v})
            reviewer_on = bool(e["tools"].get("复查员"))
        if e.get("kind") == "派活" and e.get("dst") in tools and e.get("tool"):
            tools[e["dst"]] = e["tool"]
    stations = _stations(tools, reviewer_on, dispatcher)
    _add_models(stations, events, project)
    card_test_cmd = _card_test_command(cdir)
    phase = card_phase(str(project), card_id)
    view = dict(base, card={"id": card_id, "title": _title(cdir, card_id), "state": state,
                            "rework": card.rework, "round": rnd, "phase": phase},
                stations=stations, handoffs=_handoffs(events))

    if not events:
        _fallback(stations, state)
        tone = {"待验收": "ok", "已合并": "ok", "需Claude介入": "bad"}.get(state, "run")
        view["now"] = {"text": f"{state}（这张卡没有流转记录，只能看到大致状态）", "tone": tone}
        return view

    open_ev = working_now(events)
    replies = {}
    for e in events:
        if e.get("kind") == "回复" and e.get("src") in ROLE_KEYS:
            replies[e["src"]] = e
    verdicts = [e for e in events if e.get("kind") in ("判定", "页面检查")]
    last_verdict = verdicts[-1] if verdicts else None
    escalations = [e for e in events if e.get("kind") == "转人工"]
    requirement = next((e for e in events if e.get("kind") == "需求"), None)

    runs = [e for e in events if e.get("kind") == "开跑"]
    last_run = runs[-1] if runs else None
    last_run_pid = last_run.get("pid") if last_run else None
    last_run_started = last_run.get("started") if last_run else None
    interrupted_run = bool(
        state in ("开发中", "待测试", "测试中")
        and last_run_pid is not None
        and not process_is_alive(last_run_pid, last_run_started)
    )
    disp_busy_after = bool(chat and (chat["busy"] or chat["asking"]) and state == "待验收")

    _set(stations, "you", "done", requirement["summary"] if requirement else view["card"]["title"])
    plan_writes = [e for e in events if e.get("kind") == "写方案"]
    plan_ev = plan_writes[-1] if plan_writes else None

    disp = {"待对齐": ("active", "在和你对齐需求、写方案"), "待派发": ("active", "方案已批准，准备开工"),
            "需Claude介入": ("fail", escalations[-1]["summary"] if escalations else "需要调度员介入"),
            "待验收": ("done", "已向你汇报"), "已合并": ("done", "已放进项目")}
    if chat and (chat["busy"] or chat["asking"]) and state in ("待对齐", "待派发", "待验收"):
        _set(stations, "disp", "wait" if chat["asking"] else "active", chat["step_text"],
             live=bool(chat["busy"]), elapsed=chat["elapsed"], quiet=chat["quiet"])
    elif phase.get("code") == "planning" and plan_ev is not None:
        started = _when(plan_ev)
        _set(stations, "disp", "active", "在读施工单、写方案", live=True,
             elapsed=int((now - started).total_seconds()) if started else None,
             quiet=int(_quiet_seconds(project, card_id, cdir, dict(plan_ev, live="后台输出.log"), now) or 0))
    elif phase.get("code") == "plan_ready":
        _set(stations, "disp", "wait", "方案写好了，等你批准")
    else:
        _set(stations, "disp", *disp.get(state, ("done", "派活后盯进度，有人卡住会叫你")))

    quiet = None
    for role, key in ROLE_KEYS.items():
        idle_text = {"test": "等开工", "dev": "等测试写完", "rev": "等测试通过" if reviewer_on else "这次不复查"}[key]
        if open_ev and open_ev.get("dst") == role:
            if interrupted_run:
                _set(stations, key, "wait", "上次在这里中断，可让调度员继续", live=False)
            else:
                started = _when(open_ev)
                quiet = _quiet_seconds(project, card_id, cdir, open_ev, now)
                live_line = live_hint(cdir / open_ev["live"], limit=150, tool=open_ev.get("tool", ""),
                                      test_command=card_test_cmd) if open_ev.get("live") else ""
                _set(stations, key, "active", live_line or open_ev.get("summary", ""), live=True,
                     elapsed=int((now - started).total_seconds()) if started else None,
                     quiet=int(quiet) if quiet is not None else None)
        elif role in replies:
            r = replies[role]
            failed = not r.get("ok", True) or r.get("summary", "").startswith("结论：不通过")
            took = f" · 用了 {_mmss(r['seconds'])}" if r.get("seconds") is not None else ""
            _set(stations, key, "fail" if failed else "done", r.get("summary", "") + took)
        else:
            _set(stations, key, "idle", idle_text)

    # 复查时卡片状态仍是“测试中”，所以“正在跑测试”要看：开发员交活之后还没出判定、也没人在干
    dev_reply = replies.get("开发员")
    judged = bool(last_verdict and (not dev_reply or last_verdict.get("seq", 0) > dev_reply.get("seq", 0)))
    testing = state in ("待测试", "测试中") and not judged and not open_ev
    if testing:
        if interrupted_run:
            _set(stations, "check", "wait", "上次在跑测试时中断，可让调度员继续")
        else:
            _set(stations, "check", "active", "核对改动范围、跑测试…")
    elif last_verdict:
        passed = last_verdict.get("result") == PASS
        _set(stations, "check", "done" if passed else "fail", last_verdict.get("summary", ""))
    else:
        _set(stations, "check", "idle", "等代码写完")

    # 复查的同时程序在跑全量（第四轮 59）：开始了、还没出结果、这一轮也没被叫停 → 跑测试那一格显示在跑
    fulls = [e for e in events if e.get("kind") == "全量"]
    full_last = fulls[-1] if fulls else None
    full_running = bool(full_last and full_last.get("phase") == "开始" and state not in TERMINAL_STATES
                        and not interrupted_run)
    if full_running:
        _set(stations, "check", "active", "复查的同时在跑全部测试，看有没有改坏老功能（约十几分钟）")
    elif full_last and full_last.get("phase") in ("结果", "跳过") and (
            not last_verdict or full_last.get("seq", 0) > last_verdict.get("seq", 0)):
        bad = full_last.get("result") not in (None, PASS) and "不算这次" not in full_last.get("summary", "")
        _set(stations, "check", "fail" if bad else "done", full_last.get("summary", ""))

    # 新一轮开始（写代码、改测试的又派了活）：上一轮的跑测试、复查结果收进“交接”，不再挂在流程里；
    # 写代码的交活后、复查还没出新结论时也一样（复查 A 第 3 条、第 2 轮 A 第 2 条、㉞）
    redo = [e.get("seq", 0) for e in events if e.get("kind") == "派活" and e.get("dst") in ("开发员", "测试员")
            and e.get("step") != "写测试"]
    if redo:  # 停下（中断）时也一样，不让上一轮结果冒回来（第 3 轮 A 第 1 条）
        round_start = redo[-1]
        for key, ev, words in (("check", last_verdict, "等这一轮代码写完再跑"),
                               ("rev", replies.get("复查员"), "等这一轮测试过了再复查")):
            st = next(x for x in stations if x["key"] == key)
            if ev and ev.get("seq", 0) < round_start and st.get("state") in ("done", "fail"):
                _set(stations, key, "idle", f"{words}（上一轮结果在“交接”里）")

    if disp_busy_after:
        _set(stations, "accept", "idle", "等调度员最终核验完")
    elif phase.get("code") in ("merge_failed", "copy_missing"):
        _set(stations, "accept", "fail", phase.get("detail") or phase.get("label") or "没能放进项目")
    elif state == "待验收":
        _set(stations, "accept", "wait", "做完了，请你确认")
    elif state == "已合并":
        _set(stations, "accept", "done", "已放进项目")
    else:
        _set(stations, "accept", "idle", "等全部做完")

    view["back"] = bool(open_ev and open_ev.get("dst") == "开发员" and last_verdict
                        and last_verdict.get("result") != PASS)

    # 已用时：从开跑算起，做完了就停在“结束”那一刻（如果调度员还在做最终核验则继续走表）
    start_ev = next((e for e in events if e.get("kind") == "开跑"), None) or \
        next((e for e in events if e.get("kind") == "派活"), None)
    start = _when(start_ev) if start_ev else None
    end = now
    if (state in TERMINAL_STATES and not disp_busy_after) or interrupted_run:
        end = _when(events[-1]) or now
    view["elapsed"] = int((end - start).total_seconds()) if start else (chat["elapsed"] if chat else 0)

    # 现在一句话
    if interrupted_run:
        now_text, tone = "上次施工已中断（已完成步骤已保留，可让调度员接着跑完）", "warn"
    elif open_ev:
        role = open_ev.get("dst", "")
        step = open_ev.get("step", "")
        verb = f"正在返工（第 {open_ev.get('round', rnd)} 轮）" if step == "返工修改" else _VERBS.get(step, "正在干活")
        now_text, tone = f"{_label(role, tools.get(role))} {verb}", "run"
    elif testing:
        now_text, tone = f"程序正在跑测试（第 {rnd} 轮）", "run"
    elif full_running:
        now_text, tone = "复查过了，程序在跑全部测试，看有没有改坏老功能", "run"
    elif state in ("待对齐", "待派发") and chat and (chat["busy"] or chat["asking"]):
        disp_who, _ = _dispatcher(dispatcher)
        if chat["asking"]:
            now_text, tone = f"调度员 {disp_who} {chat['step_text']}", "wait"
        else:
            now_text, tone = f"调度员 {disp_who} 正在干活：{chat['step_text']}", "run"
    elif disp_busy_after:
        disp_who, _ = _dispatcher(dispatcher)
        if chat["asking"]:
            now_text, tone = f"调度员 {disp_who} {chat['step_text']}", "wait"
        else:
            now_text, tone = f"调度员 {disp_who} 正在最终核验：{chat['step_text']}", "run"
    elif state == "待对齐":
        now_text, tone = "调度员在和你对齐需求、写方案，等你批准方案", "wait"
    elif state == "待派发":
        now_text, tone = "方案已批准，等调度员开工", "run"
    elif state == "待验收":
        now_text, tone = "做完了，等你确认", "ok"
    elif state == "已合并":
        now_text, tone = "已放进项目", "ok"
    elif state == "需Claude介入":
        reason = escalations[-1]["summary"] if escalations else "原因见交接记录"
        now_text, tone = f"需要调度员介入：{reason}", "bad"
    else:
        now_text, tone = "调度员在安排下一步", "run"

    if open_ev and quiet is not None and quiet > QUIET_WARN_SECONDS and state not in TERMINAL_STATES:
        view["stale"] = True
        now_text, tone = now_text + "（超过 10 分钟没动静，可能卡住）", "warn"
    # 这些阶段顶部一句话跟阶段标签一致；放弃/中断不论还有没有未完成的派活都跟阶段走
    code = phase.get("code")
    chat_live = bool(chat and (chat.get("busy") or chat.get("asking")))
    tone_map = {"wait": "wait", "run": "run", "bad": "bad", "ok": "ok", "done": "idle"}
    if code in ("dropped", "interrupted", "merging", "merge_failed", "copy_missing"):
        now_text = phase.get("label") or now_text
        tone = tone_map.get(phase.get("tone"), tone)
    elif (code in ("plan_failed", "plan_waiting", "planning", "start_failed")
            and not chat_live and not open_ev and not testing):
        now_text = phase.get("label") or now_text
        tone = tone_map.get(phase.get("tone"), tone)
    working = bool(open_ev) and not interrupted_run and state not in TERMINAL_STATES
    detail = live_hint(cdir / open_ev["live"], limit=150, tool=open_ev.get("tool", ""),
                       test_command=card_test_cmd) if working and open_ev.get("live") else ""
    view["now"] = {"text": now_text, "tone": tone}
    # 停下后替你起草的“继续改”（第四轮 56 甲）：最新一条、之后还没用过（没有追加要求）才给
    drafted = [e for e in events if e.get("kind") == "起草" and e.get("phase") == "好了"]
    if drafted and state == "需Claude介入" and not any(
            e.get("kind") == "追加要求" and e.get("seq", 0) > drafted[-1].get("seq", 0) for e in events):
        view["draft"] = text_of(cdir, drafted[-1])
    if detail:
        view["now"]["detail"] = detail
    # 卡片标题下那一行“此刻在干什么”用（流程修理 ⑪）
    view["live"] = working
    view["quiet"] = int(quiet) if working and quiet is not None else None
    started_now = _when(open_ev) if working else None
    view["worker_elapsed"] = int((now - started_now).total_seconds()) if started_now else None  # 当前这个人干了多久
    disp_st = next(s for s in stations if s["key"] == "disp")
    if not working and disp_st.get("state") == "active" and disp_st.get("elapsed") is not None:
        # 调度员在写方案：标题下那一行也带计时（复查 A 第 6 条）
        view["live"] = True
        view["quiet"] = disp_st.get("quiet")
        view["worker_elapsed"] = disp_st.get("elapsed")
    if code in ("review", "merge_failed"):
        start = {}
        start_path = cdir / "开工.json"
        if start_path.is_file():
            try:
                raw = json.loads(start_path.read_text(encoding="utf-8"))
                start = raw if isinstance(raw, dict) else {}
            except (OSError, ValueError):
                start = {}
        how = str(start.get("view_how") or "").strip()
        cmd = str(start.get("view_command") or "").strip()
        url = str(start.get("view_url") or "").strip()
        result = ""
        plan_path = cdir / "方案.md"
        if plan_path.is_file():
            try:
                plan_text = plan_path.read_text(encoding="utf-8")
            except OSError:
                plan_text = ""
            lines, inside = [], False
            for line in plan_text.splitlines():
                if line.startswith("## "):
                    if inside:
                        break
                    inside = line.startswith("## 你会看到什么结果")  # 老方案的标题后面还带着说明
                elif inside:
                    lines.append(line)
            result = "\n".join(lines).strip()
        root = Path(project)
        copy_there = (root.parent / f"{root.name}-worktrees" / card_id).is_dir()
        view["accept"] = {"result": result, "how": how, "can_preview": bool(cmd and url), "copy_there": copy_there}
    return view
