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
from pathlib import Path, PurePosixPath

from collab.config import ConfigError, load_team
from collab.flow import card_title, read_flow, summarize, text_of, working_now
from collab.phase import card_phase, process_is_alive
from collab.judge import PASS
from collab.state import Card
from collab.token_usage import sum_tokens, usage_for

QUIET_WARN_SECONDS = 10 * 60  # 超过 10 分钟没有任何动静，提示可能卡住
TERMINAL_STATES = {"待验收", "已合并", "需Claude介入", "已放弃"}

TOOL_NAMES = {"claude": "Claude", "codex": "Codex", "cursor": "Cursor", "agy": "agy", "qwen": "千问", "tencent": "腾讯云"}
TOOL_FACES = {"claude": "Cl", "codex": "Cx", "cursor": "Cu", "agy": "ag", "qwen": "千", "tencent": "腾"}
DEFAULT_TOOLS = {"测试员": "codex", "开发员": "cursor", "复查员": "agy"}
ROLE_KEYS = {"测试员": "test", "开发员": "dev", "复查员": "rev"}
_VERBS = {"写测试": "正在写测试", "改测试": "正在改测试", "开发": "正在写代码", "复查": "正在复查"}
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

_activity_cache: dict = {}  # 副本目录 -> (算的时间, 最近改动时间)；副本可能很大，10 秒内不重复扫


def _name(tool) -> str:
    return TOOL_NAMES.get(tool or "", tool or "")


def _face(tool) -> str:
    return TOOL_FACES.get(tool or "", (tool or "?")[:2])


_PLAIN_WORDS = (
    ("需要调度员看看", "需要你看看"),
    ("需要调度员处理", "需要你看看"),
    ("需要调度员看原因", "需要你看看"),
    ("需要调度员看", "需要你看看"),
    ("调度员备好了", "程序备好了"),
)


def _plain(text) -> str:
    """界面上显示的文字：老记录里的“调度员”一律换成人话（记录字段和原文不动）。"""
    text = str(text or "")
    for old, new in _PLAIN_WORDS:
        text = text.replace(old, new)
    return text.replace("调度员", "程序")


def _plain_phase(phase: dict) -> dict:
    """阶段里给页面看的说明也换词；code、actions 这些判断字段原样保留。"""
    if not isinstance(phase, dict):
        return phase
    shown = dict(phase)
    shown["detail"] = _plain(shown.get("detail"))
    shown["label"] = _plain(shown.get("label"))
    return shown


def _when(event):
    """记录的时刻，只用来相减算用了多久。新记录只有秒数 ts（跟时区无关）；老记录才有钟点 time。"""
    ts = event.get("ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return datetime.fromtimestamp(ts)
    try:
        return datetime.fromisoformat(event["time"])
    except (KeyError, ValueError, TypeError):
        return None


def _since_start(seconds: float) -> str:
    """开工后第几分钟：+12:34，超过一小时 +3:05:09。"""
    total = max(0, int(seconds))
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"+{h}:{m:02d}:{s:02d}" if h else f"+{m}:{s:02d}"


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


_WALK_CARD_ID = re.compile(r"^CARD-\d{3,}$")


def resolve_walk_image(project, card_id, kind, rel):
    """把卡片里记下的相对图片路径换成真实路径；不安全的路径一律返回 None。

    这个函数同时给卡片视图和只读看图接口用，保证两边的安全边界一样：
    截图只认本卡“走查截图”目录里的 png；设计图只认主项目或本卡副本里的 png，
    并且文件本身经软链接解析后也不能落进 .git / .collab。
    """
    try:
        project_text = str(project or "").strip()
        if not project_text:
            return None
    except (TypeError, ValueError):
        return None
    card = str(card_id or "").strip()
    if not _WALK_CARD_ID.fullmatch(card) or kind not in ("shot", "design"):
        return None
    text = str(rel or "").strip()
    if not text or text.startswith("/") or "\\" in text:
        return None
    try:
        if Path(text).is_absolute():
            return None
    except (OSError, ValueError):
        return None
    raw_parts = text.split("/")
    if not raw_parts or any(part in ("", ".", "..") for part in raw_parts):
        return None
    parts = PurePosixPath(text).parts
    suffix = PurePosixPath(text).suffix.lower()
    if not parts or not (suffix == ".png" or (kind == "design" and suffix in (".jpg", ".jpeg", ".webp"))):
        return None   # 设计图多收 jpg、webp（第三批复查第 2 轮：施工单里的原型图不全是 png）

    try:
        if not Path(project_text).is_dir():
            return None
        proj_real = Path(project_text).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    collab_dir = proj_real / ".collab"
    cards_dir = collab_dir / "cards"
    cdir = cards_dir / card
    for folder in (collab_dir, cards_dir, cdir):
        if folder.is_symlink() or not folder.is_dir():
            return None
    if not (cdir / "状态.json").is_file():
        return None

    if kind == "shot":
        if parts[0] != "走查截图" or len(parts) < 2:
            return None
        shots_dir = cdir / "走查截图"
        if shots_dir.is_symlink() or not shots_dir.is_dir():
            return None
        try:
            shots_real = shots_dir.resolve()
            if shots_real != (cdir.resolve() / "走查截图"):
                return None
            target = cdir.joinpath(*parts).resolve()
            target.relative_to(shots_real)
        except (OSError, ValueError, RuntimeError):
            return None
        if target == shots_real or not target.is_file() or target.suffix.lower() != ".png":
            return None
        return target

    if parts[0].lower() in (".git", ".collab"):
        return None
    roots = []
    wt_base = proj_real.parent / f"{proj_real.name}-worktrees"
    wt_dir = wt_base / card
    if wt_base.is_dir() and not wt_base.is_symlink() and wt_dir.is_dir() and not wt_dir.is_symlink():
        try:
            roots.append(wt_dir.resolve())
        except (OSError, ValueError, RuntimeError):
            pass
    roots.append(proj_real)
    for root_real in roots:
        try:
            cand = root_real.joinpath(*parts).resolve()
            rel_resolved = cand.relative_to(root_real)
        except (OSError, ValueError, RuntimeError):
            continue
        if len(rel_resolved.parts) < 1 or rel_resolved.parts[0].lower() in (".git", ".collab"):
            continue
        if cand.is_file() and cand.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            return cand   # 设计图：真实文件的后缀也要是图片（链接指过去的也一样）
    return None


def list_cards(project_dir) -> list:
    """项目里所有卡片，最近有动静的排在最前。读不出来的卡直接跳过。"""
    project = Path(project_dir)
    cards = []
    for card_id, cdir in _card_dirs(project).items():
        try:
            card = Card.load(cdir / "状态.json")
            phase = card_phase(str(project), card_id)
            # 左栏完成时间：已合并取最后一条合并记录、已放弃取最后一条记录；别的状态没有（施工单只改这个函数）
            finished = None
            if card.state in ("已合并", "已放弃"):
                events = read_flow(cdir)
                if card.state == "已合并":
                    events = [event for event in events if event.get("kind") == "合并"]
                if events:
                    when = _when(events[-1])
                    finished = when.timestamp() if when is not None else None
        except (OSError, ValueError, TypeError, KeyError):
            continue
        cards.append({"id": card_id, "title": _title(cdir, card_id), "state": card.state,
                      "rework": card.rework, "updated": _updated(cdir), "phase": _plain_phase(phase),
                      "archived": _archived(cdir, phase),
                      "finished": finished})
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
                return _plain(event.get("summary", ""))
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
    if tool not in ("codex", "qwen", "tencent"):  # 千问、腾讯云用 Codex 的壳，输出和 Codex 一样
        said = _stream_summary(lines, test_command)
        if said:
            return _cut(said, limit)
    for i in range(len(lines) - 1 if tool in ("", "codex", "qwen", "tencent") else -1, -1, -1):
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
    if role == "调度员":
        return "程序"
    if role in ROLE_KEYS:
        return f"{role} {_name(tool)}".strip()
    return role


def _tone(event) -> str:
    kind = event.get("kind")
    summary = event.get("summary", "")
    if kind in ("判定", "页面检查"):
        return "ok" if event.get("result") == PASS else "bad"
    if kind == "回复":
        concl = re.match(r"结论[：:]\s*(不通过|通过)", summary)
        if not event.get("ok", True) or (concl and concl.group(1) == "不通过"):
            return "bad"
        return "ok" if concl and concl.group(1) == "通过" else ""
    if kind == "转人工":
        return "bad"
    if kind == "结束":
        return "ok"
    return ""


def _handoffs(events: list) -> list:
    out = []
    # 从第一次开跑算起（开工前的对齐、等批准不算）；还没开跑过就从第一条算
    opened = next((e for e in events if e.get("kind") == "开跑" and _when(e) is not None), None)
    start = _when(opened) if opened else next((w for w in (_when(e) for e in events) if w is not None), None)
    for e in events:
        when = _when(e)
        tool = e.get("tool")
        out.append({
            "seq": e.get("seq"), "kind": e.get("kind"),
            "time": _since_start((when - start).total_seconds()) if when and start and when >= start else "",
            "from": _plain(_label(e.get("src", ""), tool if e.get("kind") == "回复" else None)),
            "to": _plain(_label(e.get("dst", ""), tool if e.get("kind") == "派活" else None)),
            "summary": _plain(e.get("summary", "")), "tone": _tone(e), "has_text": bool(e.get("text")),
        })
    return out


def _dispatcher(saved) -> tuple:
    """聊天里干活的人的名字和头像字：桌面窗口告诉面板用户选的是谁；没说就是 Codex。"""
    saved = (saved or "").strip()
    if not saved:
        return "Codex", "Cx"
    if saved.startswith("custom:"):
        words = saved[len("custom:"):].split()
        name = words[0] if words else "自定义"
        return name, name[:2]
    return _name(saved), _face(saved)


def _stations(tools: dict, reviewer_on: bool, dispatcher=None) -> list:
    # dispatcher：老窗口、老项目还会传（以前选过调度员）；这一站现在固定是 Codex 写方案（去掉调度员），用不着它。
    # 站位键 disp 不改名：前端、记录、测试里都在用。
    return [
        {"key": "you", "role": "提需求", "who": "你", "face": "你"},
        {"key": "disp", "role": "写方案", "who": _name("codex"), "face": _face("codex")},
        {"key": "test", "role": "写测试", "who": _name(tools["测试员"]), "face": _face(tools["测试员"])},
        {"key": "dev", "role": "写代码", "who": _name(tools["开发员"]), "face": _face(tools["开发员"])},
        {"key": "check", "role": "跑测试", "who": "程序", "face": "检"},
        {"key": "rev", "role": "复查", "who": _name(tools["复查员"]) if reviewer_on else "不复查",
         "face": _face(tools["复查员"]) if reviewer_on else "—"},
        {"key": "accept", "role": "确认", "who": "你", "face": "你"},
    ]


_STATION_ROLE = {"test": "测试员", "dev": "开发员", "rev": "复查员"}


def _plan_model_label(project) -> str:
    """写方案这一站的“模型（强度）”：没有写方案记录时用固定的低档设置（collab.config.plan_models）。"""
    try:
        from collab.config import model_label, plan_models
        models = plan_models(project)
    except Exception:  # noqa: BLE001 — 配置读不出就不显示模型
        return ""
    return model_label("codex", {"codex": models.get("codex"), "codex_effort": models.get("codex_effort")})


def _add_models(stations, events, project=None) -> None:
    """右上角“工具 · 模型（强度）”（㉗ ㊳）：开跑时记下的各岗模型、写方案时记下的模型；老卡没有就只写工具。
    每个岗位优先用最后一次派活记下的实际模型（复查降档后要跟着变）；没有就回退到“开跑”记录。
    写方案那一站固定是 Codex：优先用写方案记录里的模型，没有就用固定的低档设置。"""
    models, dispatched, plan_model, plan_src = {}, {}, "", ""
    for e in events:
        if e.get("kind") == "开跑" and isinstance(e.get("models"), dict):
            models.update({k: v for k, v in e["models"].items() if v})
        if e.get("kind") == "派活" and e.get("dst") in _STATION_ROLE.values() and e.get("model"):
            dispatched[e["dst"]] = e["model"]
        if e.get("kind") == "写方案" and e.get("model"):
            plan_model = str(e["model"])
        if e.get("kind") == "方案":
            plan_src = str(e.get("src") or "")
        elif e.get("kind") == "写方案":
            plan_src = ""   # 每次开写方案先清掉：程序生成后又让 Codex 重写时，重写那一轮按 Codex 显示（复查第 1 轮 14）
    for s in stations:
        if s["key"] == "disp" and plan_src == "程序":
            # 照施工单直接生成的方案没有模型参与（外部项目观察 ①：标成 Codex · GPT 让人看错谁干的活）
            s["who"], s["face"], s["model"] = "程序", "程", "照施工单生成"
            continue
        if s["key"] == "disp":
            label = plan_model or (_plan_model_label(project) if project is not None else "")
        else:
            role = _STATION_ROLE.get(s["key"], "")
            label = dispatched.get(role) or models.get(role, "")
        # 模型单独放一项，“who”照旧是工具名：点步骤看交接按工具名找（复查 A 第 2 条）
        s["model"] = label if label and s.get("who") not in ("", "不复查") else ""



def _token_num(value) -> int:
    """用量数字统一在这儿转：无穷大、NaN、非数字一律当 0，不让坏记录带垮汇总。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _dispatches_by_seq(events) -> dict:
    return {e.get("seq"): e for e in events if e.get("kind") == "派活" and e.get("seq") is not None}


def _reply_who(reply: dict, sent) -> str:
    tool = reply.get("tool") or (sent.get("tool") if isinstance(sent, dict) else "") or ""
    tool_label = _name(tool)
    model = (sent.get("model") if isinstance(sent, dict) else "") or reply.get("model") or ""
    if tool_label and model:
        return f"{tool_label} · {model}"
    return tool_label or str(model or "")


_UNSET = object()
_STOPPED_AFTER_KINDS = ("停下", "开跑", "转人工", "结束", "判定", "页面检查")


def _normalize_saved_tokens(saved):
    """把已记录的用量统一成标准结果；已经结束的轮次不再保留等待状态。"""
    if not isinstance(saved, dict):
        return None
    if saved.get("ok"):
        return saved
    if "ok" not in saved and isinstance(saved.get("total"), (int, float)) and not isinstance(saved.get("total"), bool):
        return dict(saved, ok=True)
    if saved.get("pending"):
        return {"ok": False, "reason": "这一轮已经结束，但没有读到结果用量"}
    reason = str(saved.get("reason") or "").strip() or "这一步没有用量记录"
    return {"ok": False, "reason": reason}


def _saved_step_tokens(event: dict):
    """取「方案」「交接资料」这类不走派活/回复配对的事件用量：缺字段时除明确不支持工具外都计为读不到。"""
    saved = _normalize_saved_tokens(event.get("tokens"))
    if saved is not None:
        return saved
    if event.get("src") == "程序":
        return None   # 程序自己做的（照施工单生成方案、有施工单跳过交接资料）：没叫 AI，不算“没读到”
    tool = event.get("tool")
    if tool and usage_for(tool, output="") is None:
        return None
    return {"ok": False, "reason": "这一步没有用量记录"}


def _active_dispatch_index(events, state=None, interrupted_run: bool = False):
    """当前仍在运行且尚未回复的那一条派活的下标；已停下、中断或后继重开时返回 None。"""
    if state is not None and (state not in ("开发中", "待测试", "测试中") or interrupted_run):
        return None
    answered = {e.get("reply_to") for e in events if e.get("kind") == "回复"}
    for idx in range(len(events) - 1, -1, -1):
        ev = events[idx]
        if ev.get("kind") != "派活":
            continue
        if ev.get("seq") in answered:
            return None
        for later in events[idx + 1:]:
            kind = later.get("kind")
            if kind in _STOPPED_AFTER_KINDS:
                return None
            if ev.get("dst") not in _STATION_ROLE.values() and kind == "起草" and later.get("phase") in ("好了", "没成"):
                return None
        return idx
    return None


def _live_dispatch_tokens(event: dict, cdir: Path, is_active: bool):
    """读取未回复派活的实时用量；已经停下的旧派活不再保留等待状态。"""
    try:
        tokens = usage_for(event.get("tool"), live=Path(cdir) / str(event["live"]))
    except Exception as exc:  # noqa: BLE001 — 读实时用量失败不挡流程
        return {"ok": False, "reason": f"读实时用量时出问题（{exc}）"}
    if tokens is None:
        return None
    if isinstance(tokens, dict) and tokens.get("pending") and not is_active:
        return {"ok": False, "reason": "这一轮已经结束，实时输出里没有结果用量"}
    return tokens


def _resolve_reply_tokens(reply: dict, dispatches: dict, cdir: Path):
    """取一条已回复轮次的用量：优先已记录字段，缺失时按 reply_to 找对应派活实时输出补算。"""
    saved = _normalize_saved_tokens(reply.get("tokens"))
    if saved is not None:
        return saved
    sent = dispatches.get(reply.get("reply_to"))
    tool = reply.get("tool") or (sent.get("tool") if isinstance(sent, dict) else "") or ""
    live = sent.get("live") if isinstance(sent, dict) else None
    if live:
        try:
            got = usage_for(tool, live=Path(cdir) / str(live))
        except Exception as exc:  # noqa: BLE001 — 补算失败不挡面板显示
            return {"ok": False, "reason": f"读实时用量时出问题（{exc}）"}
        if got is None:
            return None
        if got.get("pending"):
            return {"ok": False, "reason": "这一轮已经结束，实时输出里没有结果用量"}
        return got
    probe = usage_for(tool, output="")
    if probe is None:
        return None
    return {"ok": False, "reason": "这一步没有用量记录，也找不到实时输出"}


_WORK_END_KINDS = ("停下", "转人工", "结束")
_NOT_WORK_KINDS = ("开工", "接手", "追加要求", "同步", "起草", "接受", "合并")


def _work_seconds(events, running: bool, now=None):
    """本卡干活总耗时（秒）：每次“开跑”到它后面第一条停下/转人工/结束；停卡、等人点按钮的时间不算（外部项目试跑 ⑪）。
    最后一段还没结束：正在跑就算到现在，进程已经没了就算到最后一条记录。从没开跑过返回 None。"""
    total, start, last = 0.0, None, None
    seen = False
    for e in events:
        when = _when(e)
        if when is None:
            continue
        t = when.timestamp()
        kind = e.get("kind")
        if kind == "开跑":
            if start is not None and last is not None:
                total += max(0.0, last - start)   # 上一段没记停下就又开跑：算到它最后一条记录
            start, last, seen = t, t, True
            continue
        if start is None:
            continue
        if kind in _NOT_WORK_KINDS:
            # 人在操作的记录（重开记的“开工”、接手、追加要求、起草……）出现时，上一段没记停下就断了：
            # 算到它最后一条干活记录，等人的时间不算（复查第 1 轮第 9 条、第 2 轮第 9 条）
            total += max(0.0, last - start)
            start = None
            continue
        last = t
        if kind in _WORK_END_KINDS:
            total += max(0.0, t - start)
            start = None
    if start is not None:
        end = now if running and now is not None else last
        total += max(0.0, (end or start) - start)
    return int(round(total)) if seen else None


def _add_tokens(stations, events, cdir: Path, resolved_replies=None, active_dispatch_idx=_UNSET) -> dict:
    """把各站位的用量加起来，另算交接资料、其他回复和辅助会话；只加 tokens 字段，不改原有字段。"""
    dispatches = _dispatches_by_seq(events)
    answered = {e.get("reply_to") for e in events if e.get("kind") == "回复"}
    active_idx = _active_dispatch_index(events) if active_dispatch_idx is _UNSET else active_dispatch_idx
    station_total = 0
    children = 0
    card_missing = 0
    fresh = 0   # 没走缓存的读入 + 写出（外部项目观察 ㉓：8000 万里 96% 是重复读缓存，合计看着吓人）

    fresh_known = True   # 有一轮缺读入/缓存/写出明细（老记录）就算不出，整项不显示，免得少算多算（复查第 1 轮 10）

    def add_fresh(tok):
        nonlocal fresh, fresh_known
        if not (isinstance(tok, dict) and tok.get("ok")):
            return
        if not all(tok.get(k) is not None for k in ("input", "cached", "output")):   # 空值也算不出（复查第 2 轮 B3）
            fresh_known = False
            return
        fresh += max(0, _token_num(tok.get("input")) - _token_num(tok.get("cached"))) + _token_num(tok.get("output"))

    def reply_tokens(e):
        seq = e.get("seq")
        if resolved_replies is not None and seq in resolved_replies:
            return resolved_replies[seq]
        tok = _resolve_reply_tokens(e, dispatches, cdir)
        if resolved_replies is not None and seq is not None:
            resolved_replies[seq] = tok
        return tok

    def take_children(items):
        nonlocal children
        for item in items:
            if isinstance(item, dict) and item.get("ok"):
                children += _token_num(item.get("children"))

    def put(key, items):
        nonlocal station_total, card_missing
        if not items:
            return
        pending_item = next((x for x in items if isinstance(x, dict) and x.get("pending")), None)
        non_pending = [x for x in items if not (isinstance(x, dict) and x.get("pending"))]
        merged = sum_tokens(non_pending) if non_pending else None
        if merged is not None:
            if merged.get("ok"):
                station_total += _token_num(merged.get("total"))
                for one in non_pending:   # 一轮一轮算，合并后缺的字段会被当成 0
                    add_fresh(one)
            card_missing += _token_num(merged.get("missing"))
            take_children(non_pending)
        station = next((s for s in stations if s.get("key") == key), None)
        if pending_item is not None:
            out_tok = {"ok": False, "pending": True,
                       "reason": str(pending_item.get("reason") or "这一轮结束后出数")}
            if merged and _token_num(merged.get("missing")) > 0:
                out_tok["missing"] = _token_num(merged.get("missing"))
            if station is not None:
                station["tokens"] = out_tok
            return
        if merged is None:
            return
        if station is not None:
            station["tokens"] = merged

    plan_items = [tok for e in events if e.get("kind") == "方案"
                  for tok in (_saved_step_tokens(e),) if tok is not None]
    put("disp", plan_items)

    for key, role in _STATION_ROLE.items():
        items = []
        for e in events:
            if e.get("kind") != "回复" or e.get("src") != role:
                continue
            tok = reply_tokens(e)
            if tok is not None:
                items.append(tok)
        for idx, e in enumerate(events):
            if e.get("kind") != "派活" or e.get("dst") != role:
                continue
            if not e.get("live") or e.get("seq") in answered:
                continue  # 已经交活的看回复记录，不再读日志重复累计
            tokens = _live_dispatch_tokens(e, cdir, is_active=(idx == active_idx))
            if tokens is not None:
                items.append(tokens)
        put(key, items)

    other = 0
    for idx, e in enumerate(events):
        kind = e.get("kind")
        tok = None
        if kind == "交接资料":
            tok = _saved_step_tokens(e)
        elif kind == "回复" and e.get("src") not in _STATION_ROLE.values():
            tok = reply_tokens(e)
        elif kind == "派活" and e.get("dst") not in _STATION_ROLE.values() and e.get("live") and e.get("seq") not in answered:
            tok = _live_dispatch_tokens(e, cdir, is_active=(idx == active_idx))
        if not isinstance(tok, dict):
            continue
        if tok.get("ok"):
            other += _token_num(tok.get("total")) + _token_num(tok.get("children"))
            add_fresh(tok)
            fresh += _token_num(tok.get("children"))
        elif not tok.get("pending"):
            card_missing += 1

    other += children
    result = {"total": station_total + other, "other": other}
    if fresh_known and fresh + children > 0:
        result["fresh"] = fresh + children   # 小帮手的用量拆不出缓存，整笔算进新消耗（宁可多算）
    if card_missing > 0:
        result["missing"] = card_missing
    return result


def _card_test_command(cdir: Path) -> str:
    try:
        raw = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(raw.get("test_command") or "") if isinstance(raw, dict) else ""


def _set(stations, key, state, detail="", **extra):
    for s in stations:
        if s["key"] == key:
            s.update({"state": state, "detail": _plain(detail), "live": False, "elapsed": None, "quiet": None})
            s.update(extra)


def _count(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _insert_takeover_station(stations, event):
    """有“接手”记录时，在复查和确认之间加一格（CARD-020；只认接手命令记下的）。"""
    detail = (f"改了 {_count(event.get('changed'))} 处 · Codex 复查 {_count(event.get('reviews'))} 轮"
              f" · {_count(event.get('later'))} 条记进以后再修")
    who = str(event.get("src") or "Claude")   # 接手记录写的是谁就显示谁（观察 ⑬：外部项目是 Gemini 接手）
    station = {"key": "takeover", "role": f"{who} 接手", "who": who, "face": who[:2],
               "state": "done", "detail": detail, "live": False, "elapsed": None, "quiet": None}
    index = next((i for i, s in enumerate(stations) if s["key"] == "accept"), len(stations))
    stations.insert(index, station)


def _review_conclusion(events, reply):
    """最后一次复查员回复之后、和它对应的那条“复查结论”（第二批 A3）：老卡没有就返回 None。"""
    if not isinstance(reply, dict):
        return None
    reply_seq = int(reply.get("seq") or 0)
    found = None
    for e in events:
        if e.get("kind") != "复查结论" or int(e.get("seq") or 0) <= reply_seq:
            continue
        if e.get("reply") and int(e.get("reply") or 0) != reply_seq:
            continue   # 有 reply 时，明确指向上一条回复的迟到结论不能盖这一轮
        found = e
    return found


def _is_deferred_plan(cdir: Path) -> bool:
    """检查卡是否在等待接入调度员后再开始写方案。"""
    try:
        data = json.loads((cdir / "来源.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get("deferred_plan"))


def _apply_plan_disp_station(stations, cdir: Path, phase: dict) -> bool:
    """按方案等待/失败阶段诚实更新调度员站位；未命中时返回 False。"""
    code = phase.get("code")
    if code == "plan_waiting":
        detail = "等待接入调度员后开始写方案" if _is_deferred_plan(cdir) else "排队等待写方案"
        _set(stations, "disp", "wait", detail)
        return True
    if code == "plan_failed":
        reason = str(phase.get("detail") or "").strip()
        detail = reason if reason.startswith("方案没写成") else (f"方案没写成：{reason}" if reason else "方案没写成")
        _set(stations, "disp", "fail", detail)
        return True
    return False


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
    if state in ("待验收", "已合并"):
        _set(stations, "disp", "done", "方案已定")


def _chat_view(base: dict, chat: dict, tools: dict, reviewer_on: bool, dispatcher=None) -> dict:
    """还没有正式建任务卡片（或旧卡已合并），左边聊天正在排查/对齐时的实时流转视图。"""
    stations = _stations(tools, reviewer_on, dispatcher)
    disp_who, _ = _dispatcher(dispatcher)
    _set(stations, "you", "done", chat["user_detail"])
    if chat["asking"]:
        _set(stations, "disp", "wait", chat["step_text"], elapsed=chat["elapsed"])
        now_text, tone = f"{disp_who} {chat['step_text']}", "wait"
    elif chat["busy"]:
        _set(stations, "disp", "active", chat["step_text"], live=True,
             elapsed=chat["elapsed"], quiet=chat["quiet"])
        now_text, tone = f"{disp_who} 正在干活：{chat['step_text']}", "run"
    else:
        _set(stations, "disp", "wait", chat["step_text"])
        now_text, tone = f"{disp_who}：{chat['step_text']}", "wait"
    _set(stations, "test", "idle", "等建卡派工")
    _set(stations, "dev", "idle", "等测试写完")
    _set(stations, "check", "idle", "等代码写完")
    _set(stations, "rev", "idle", "等测试通过" if reviewer_on else "这次不复查")
    _set(stations, "accept", "idle", "等全部做完")
    state = "调度中" if (chat["busy"] or chat["asking"]) else "待对齐"
    handoffs = [dict(h, **{k: _plain(h.get(k, "")) for k in ("from", "to", "summary")})
                for h in chat["handoffs"]]
    return dict(base, card={"id": "调度中", "title": chat["title"], "state": state, "rework": 0, "round": 1},
                stations=stations, handoffs=handoffs, elapsed=chat["elapsed"],
                now={"text": _plain(now_text), "tone": tone})


def _walk_shots(project: Path, card_id: str, cdir: Path, events: list) -> list:
    """这次交活最后一次通过的走查截图；只在文件仍存在且没越界时返回。"""
    since = 0
    for e in events:
        if (e.get("kind") == "回复" and e.get("src") == "开发员") or e.get("kind") == "追加要求":
            seq = e.get("seq")
            if isinstance(seq, int) and not isinstance(seq, bool) and seq > since:
                since = seq
    walk = None
    for e in events:
        seq = e.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq <= since:
            continue
        if e.get("kind") == "页面检查" and e.get("walk") is True:
            walk = e
    if not walk or walk.get("result") != PASS:
        return []
    shots = walk.get("shots")
    if not isinstance(shots, list) or not shots:
        return []
    out = []
    for item in shots:
        if not isinstance(item, dict):
            continue
        step = str(item.get("步骤") or "").strip()
        if not step:
            continue
        shot = str(item.get("截图") or "").strip()
        if resolve_walk_image(str(project), card_id, "shot", shot) is None:
            continue
        design = str(item.get("设计图") or "").strip()
        if design and resolve_walk_image(str(project), card_id, "design", design) is None:
            design = ""
        out.append({"step": step, "shot": shot, "design": design})
    return out


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
    chat = chat_session.live_summary(now) if chat_session is not None else None
    base = {"installed": installed, "project": project.name, "card": None, "cards": [], "handoffs": [],
            "back": False, "stale": False, "elapsed": 0, "tokens": {"total": 0, "other": 0},
            "stations": _stations(tools, team_reviewer_on, dispatcher), "walk_shots": []}
    for s in base["stations"]:
        _set(base["stations"], s["key"], "idle")
    if not installed:
        if chat:
            return _chat_view(base, chat, tools, team_reviewer_on, dispatcher)
        base["now"] = {"text": "这个项目还没接入", "tone": "idle"}
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
    takeovers = [e for e in events if e.get("kind") == "接手"]
    if takeovers:
        _insert_takeover_station(stations, takeovers[-1])
    runs = [e for e in events if e.get("kind") == "开跑"]
    last_run = runs[-1] if runs else None
    last_run_pid = last_run.get("pid") if last_run else None
    last_run_started = last_run.get("started") if last_run else None
    interrupted_run = bool(
        state in ("开发中", "待测试", "测试中")
        and last_run_pid is not None
        and not process_is_alive(last_run_pid, last_run_started)
    )
    active_idx = _active_dispatch_index(events, state=state, interrupted_run=interrupted_run)
    dispatches = _dispatches_by_seq(events)
    resolved_replies = {}
    tokens = _add_tokens(stations, events, cdir, resolved_replies=resolved_replies, active_dispatch_idx=active_idx)
    tokens_fresh = tokens.pop("fresh", None)   # 单独一项给右栏（㉓），合计那份照旧只有 total/other/missing
    card_test_cmd = _card_test_command(cdir)
    phase = _plain_phase(card_phase(str(project), card_id))
    finished_at = None
    if state == "已合并":
        merged_records = [e for e in events if e.get("kind") == "合并"]
        # 完成时间就是卡片放进项目的那一刻：之后再补记接手，也不改这个时间（CARD-020 复查退回）
        if merged_records:
            when = _when(merged_records[-1])
            finished_at = when.timestamp() if when is not None else None
    running = bool(state in ("开发中", "待测试", "测试中") and last_run_pid is not None and not interrupted_run)
    view = dict(base, card={"id": card_id, "title": _title(cdir, card_id), "state": state,
                            "rework": card.rework, "round": rnd, "phase": phase},
                stations=stations, handoffs=_handoffs(events), tokens=tokens, tokens_fresh=tokens_fresh,
                finished_at=finished_at, work_seconds=_work_seconds(events, running, time.time()))

    if not events:
        _fallback(stations, state)
        if phase.get("code") == "by_claude":
            view["now"] = {"text": phase.get("label") or "Claude 亲自做",
                           "tone": phase.get("tone") or "run"}
            return view
        tone = {"待验收": "ok", "已合并": "ok", "需Claude介入": "bad"}.get(state, "run")
        if _apply_plan_disp_station(stations, cdir, phase):
            tone = "bad" if phase.get("code") == "plan_failed" else "wait"
        view["now"] = {"text": f"{state}（这张卡没有流转记录，只能看到大致状态）", "tone": tone}
        return view

    open_ev = working_now(events)
    replies, reply_list = {}, {role: [] for role in ROLE_KEYS}
    for e in events:
        if e.get("kind") == "回复" and e.get("src") in ROLE_KEYS:
            replies[e["src"]] = e
            reply_list[e["src"]].append(e)
    verdicts = [e for e in events if e.get("kind") in ("判定", "页面检查")]
    last_verdict = verdicts[-1] if verdicts else None
    escalations = [e for e in events if e.get("kind") == "转人工"]
    requirement = next((e for e in events if e.get("kind") == "需求"), None)

    disp_busy_after = bool(chat and (chat["busy"] or chat["asking"]) and state == "待验收")
    # 卡已过了开发，最后那次派活还没交活（被停下、Claude 接手）：当它作废，各步照最后一次做完的结果显示，
    # 顶部一句话跟卡片状态走，不再“正在返工”、不再转圈计时（试跑记录 70）
    moved_on = state not in ("开发中", "待测试", "测试中")
    stopped_ev = open_ev if moved_on else None
    if moved_on:
        open_ev = None

    _set(stations, "you", "done", requirement["summary"] if requirement else view["card"]["title"])
    plan_writes = [e for e in events if e.get("kind") == "写方案"]
    plan_ev = plan_writes[-1] if plan_writes else None

    disp = {"待对齐": ("active", "在和你对齐需求、写方案"), "待派发": ("active", "方案已批准，准备开工"),
            # 开工后才停下的：停下原因顶部已经说了，不挂在“写方案”这步，免得看着像写方案失败了（11-18 用户定）；
            # 还没开工（写方案阶段就停下）才算写方案这步没成
            "需Claude介入": (("done", "方案已定，后面有人卡住会叫你") if any(e.get("kind") == "开跑" for e in events)
                           else ("fail", escalations[-1]["summary"] if escalations else "卡停下了，等你处理")),
            "待验收": ("done", "方案已定"), "已合并": ("done", "方案已定")}
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
    elif not _apply_plan_disp_station(stations, cdir, phase):
        _set(stations, "disp", *disp.get(state, ("done", "方案已定，后面有人卡住会叫你")))

    quiet = None
    # 轮数按最后一次“改要求”之后算（外部项目观察 ㉑：右栏“第 10 轮”是整张卡从头累计，看着像失控；
    # 退回次数、停卡上限都按这一口径），整张卡累计放鼠标提示
    revise_seq = max((int(e.get("seq") or 0) for e in events if e.get("kind") == "追加要求"), default=0)
    for role, key in ROLE_KEYS.items():
        idle_text = {"test": "等开工", "dev": "等测试写完", "rev": "等测试通过" if reviewer_on else "这次不复查"}[key]
        history = []
        before_revise = sum(1 for r in reply_list[role] if int(r.get("seq") or 0) <= revise_seq)
        for reply in reply_list[role]:
            seq = reply.get("seq")
            resolved = (resolved_replies[seq] if seq in resolved_replies
                        else _resolve_reply_tokens(reply, dispatches, cdir))
            sent = dispatches.get(reply.get("reply_to"))
            who = _reply_who(reply, sent)
            item = {
                "seconds": reply.get("seconds"),
                "tokens": _token_num(resolved.get("total")) if isinstance(resolved, dict) and resolved.get("ok") else None,
            }
            if who:
                item["who"] = who
            if isinstance(resolved, dict) and not resolved.get("ok"):
                item["missing"] = True
                item["reason"] = str(resolved.get("reason") or "这一步没有读到用量")
            history.append(item)
        times = [h["seconds"] for h in history if h["seconds"] is not None]
        spent = sum(times) if times else None
        if open_ev and open_ev.get("dst") == role:
            if interrupted_run:
                _set(stations, key, "wait", "上次在这里中断，可以点“接着跑”继续", live=False,
                     history=history, spent=spent, rounds=len(history) + 1 - before_revise, earlier=before_revise)
            else:
                started = _when(open_ev)
                quiet = _quiet_seconds(project, card_id, cdir, open_ev, now)
                live_line = live_hint(cdir / open_ev["live"], limit=150, tool=open_ev.get("tool", ""),
                                      test_command=card_test_cmd) if open_ev.get("live") else ""
                _set(stations, key, "active", live_line or open_ev.get("summary", ""), live=True,
                     elapsed=int((now - started).total_seconds()) if started else None,
                     quiet=int(quiet) if quiet is not None else None,
                     history=history, spent=spent, rounds=len(history) + 1 - before_revise, earlier=before_revise)
        elif role in replies:
            r = replies[role]
            failed = not r.get("ok", True) or bool(re.match(r"结论[：:]\s*不通过", r.get("summary", "")))
            detail = r.get("summary", "")
            conclusion = _review_conclusion(events, r) if role == "复查员" else None
            if conclusion is not None:
                result = conclusion.get("result")
                failed = result != "通过"
                detail = conclusion.get("summary") or ("复查通过" if result == "通过" else "复查退回")
            elif role == "复查员":
                r_seq = int(r.get("seq") or 0)
                stopped_after = next((e for e in reversed(events)
                                      if e.get("kind") == "转人工" and int(e.get("seq") or 0) > r_seq), None)
                if stopped_after is not None and not any(
                        (e.get("kind") in ("判定", "页面检查")
                         or (e.get("kind") == "全量" and e.get("phase") == "结果"))
                        and r_seq < int(e.get("seq") or 0) < int(stopped_after.get("seq") or 0)
                        for e in events):
                    failed = True
                    detail = stopped_after.get("summary") or "复查未放行"
            _set(stations, key, "fail" if failed else "done", detail,
                 history=history, spent=spent, rounds=len(history) - before_revise, earlier=before_revise)
        else:
            _set(stations, key, "idle", idle_text)

    # 复查时卡片状态仍是“测试中”，所以“正在跑测试”要看：开发员交活之后还没出判定、也没人在干
    dev_reply = replies.get("开发员")
    judged = bool(last_verdict and (not dev_reply or last_verdict.get("seq", 0) > dev_reply.get("seq", 0)))
    testing = state in ("待测试", "测试中") and not judged and not open_ev
    # 写代码前程序先跑一遍新测试（先红检查）：最后一条是“先红开始”、没人在干、没中断、卡没到终态，就算在跑
    # 只认这次开跑之后的：中断后接着跑，新进程还没开始先红检查时，不能拿上一进程留下的“先红开始”转圈计时
    run_seq = max([e.get("seq", 0) for e in events if e.get("kind") == "开跑"], default=0)
    last_red = next((e for e in reversed(events) if e.get("kind") in ("先红开始", "先红检查")), None)
    red_running = bool(last_red and last_red.get("kind") == "先红开始" and last_red.get("seq", 0) > run_seq
                       and not open_ev
                       and not interrupted_run and state not in TERMINAL_STATES)
    if red_running:
        started = _when(last_red)
        _set(stations, "check", "active", "写代码前先跑一遍新测试", live=True,
             elapsed=int((now - started).total_seconds()) if started else None)
    elif testing:
        if interrupted_run:
            _set(stations, "check", "wait", "上次在跑测试时中断，可以点“接着跑”继续")
        else:
            _set(stations, "check", "active", "核对改动范围、跑测试…")
    elif last_verdict:
        passed = last_verdict.get("result") == PASS
        _set(stations, "check", "done" if passed else "fail", last_verdict.get("summary", ""))
    else:
        _set(stations, "check", "idle", "等代码写完")

    # 复查的同时程序在跑全量（第四轮 59）：开始了、还没出结果、这一轮也没被叫停 → 跑测试那一格显示在跑
    # 只认这次开跑、这一轮写代码之后的（复查：中断后接着跑，上一进程留下的“开始”不能算在跑）
    since = max([e.get("seq", 0) for e in events if e.get("kind") == "开跑"]
                + [e.get("seq", 0) for e in events if e.get("kind") == "派活" and e.get("dst") == "开发员"], default=0)
    fulls = [e for e in events if e.get("kind") == "全量" and e.get("seq", 0) > since]
    full_last = fulls[-1] if fulls else None
    full_running = bool(full_last and full_last.get("phase") == "开始" and state not in TERMINAL_STATES
                        and not interrupted_run)
    if phase.get("code") == "accepting":
        accepting_detail = "正在跑全部测试"
        if phase.get("detail"):
            accepting_detail += f"，{phase['detail']}"
        _set(stations, "check", "active", accepting_detail)
    elif red_running:
        pass   # 追加要求后同一次开跑接着新一轮：先红检查在跑，不让上一轮的全量结果盖掉转圈
    elif full_running:
        _set(stations, "check", "active", "复查的同时在跑全部测试，看有没有改坏老功能（约十几分钟）")
    elif full_last and full_last.get("phase") in ("结果", "跳过") and (
            not last_verdict or full_last.get("seq", 0) > last_verdict.get("seq", 0)):
        bad = full_last.get("result") not in (None, PASS) and "不算这次" not in full_last.get("summary", "")
        _set(stations, "check", "fail" if bad else "done", full_last.get("summary", ""))

    # 新一轮开始（写代码、改测试的又派了活）：上一轮的跑测试、复查结果收进“交接”，不再挂在流程里；
    # 写代码的交活后、复查还没出新结论时也一样（复查 A 第 3 条、第 2 轮 A 第 2 条、㉞）
    redo = [e.get("seq", 0) for e in events if e.get("kind") == "派活" and e.get("dst") in ("开发员", "测试员")
            and e.get("step") != "写测试"]
    if stopped_ev:
        key = ROLE_KEYS.get(stopped_ev.get("dst"))
        st = next((x for x in stations if x["key"] == key), None)
        if st is not None:
            if st.get("state") in ("done", "fail"):
                st["detail"] = (st.get("detail") or "") + "（之后又开的一轮中途停下，没交活）"
            else:
                _set(stations, key, "idle", "这一轮中途停下了，没交活")
    if state == "已合并":   # 放进项目时程序只重跑本卡测试，过了才放：照实说清哪项复验过（复查第 2 轮）
        st = next(x for x in stations if x["key"] == "check")
        checks = [e for e in events if e.get("kind") in ("判定", "页面检查")
                  or (e.get("kind") == "全量" and e.get("phase") == "结果")]
        if st.get("state") == "fail" and checks:
            before, last_check = st.get("detail", ""), checks[-1]   # 按最后一次检查（红叉的来源）判断
            claude_ok = any(e.get("kind") == "验收" and e.get("src") == "Claude"
                            and e.get("seq", 0) > last_check.get("seq", 0) for e in events)
            own_test = (last_check.get("kind") == "判定"
                        and not any(w in last_check.get("summary", "") for w in ("全部测试", "老测试")))
            if claude_ok:
                _set(stations, "check", "done", f"Claude 最后检查时全部测试已通过（之前一轮：{before}）")
            elif own_test:
                _set(stations, "check", "done", f"放进项目前本卡测试已通过（之前一轮：{before}）")
            else:
                st["detail"] = before + "（放进项目前只重跑了本卡测试，这项没再复验）"
    if redo and not moved_on:  # 停下（中断）时也一样，不让上一轮结果冒回来（第 3 轮 A 第 1 条）
        round_start = redo[-1]
        for key, ev, words in (("check", last_verdict, "等这一轮代码写完再跑"),
                               ("rev", replies.get("复查员"), "等这一轮测试过了再复查")):
            st = next(x for x in stations if x["key"] == key)
            if ev and ev.get("seq", 0) < round_start and st.get("state") in ("done", "fail"):
                _set(stations, key, "idle", f"{words}（上一轮结果在“交接”里）")
    for st in stations:   # 最终没开始的环节不能残留轮数、用时、明细
        if st.get("key") in ROLE_KEYS.values() and st.get("state") == "idle":
            for field in ("history", "spent", "rounds", "earlier"):
                st.pop(field, None)

    # Claude 接手（CARD-020）：最新接手在最后一次复查结论之后、复查原来是红叉，才改成绿勾并写明；
    # 接手之后又被复查退回的，照旧是红叉，接手那格留着。
    if takeovers:
        conclusions = [e for e in events if e.get("kind") == "复查结论"]
        last_conclusion = int(conclusions[-1].get("seq") or 0) if conclusions else 0
        rev_st = next((s for s in stations if s["key"] == "rev"), None)
        if (rev_st is not None and rev_st.get("state") == "fail"
                and int(takeovers[-1].get("seq") or 0) > last_conclusion):
            rev_st["state"] = "done"
            rev_st["detail"] = (rev_st.get("detail") or "") + f"，已由 {takeovers[-1].get('src') or 'Claude'} 接手修好"

    if disp_busy_after:
        _set(stations, "accept", "idle", "等最终核验完")
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
        now_text, tone = "上次施工已中断（已完成步骤已保留，可以点“接着跑”继续）", "warn"
    elif open_ev:
        role = open_ev.get("dst", "")
        step = open_ev.get("step", "")
        verb = f"正在返工（第 {open_ev.get('round', rnd)} 轮）" if step == "返工修改" else _VERBS.get(step, "正在干活")
        now_text, tone = f"{_label(role, tools.get(role))} {verb}", "run"
    elif red_running:
        now_text, tone = "程序在写代码前先跑一遍新测试", "run"
    elif testing:
        now_text, tone = f"程序正在跑测试（第 {rnd} 轮）", "run"
    elif full_running:
        now_text, tone = "复查过了，程序在跑全部测试，看有没有改坏老功能", "run"
    elif state in ("待对齐", "待派发") and chat and (chat["busy"] or chat["asking"]):
        disp_who, _ = _dispatcher(dispatcher)
        if chat["asking"]:
            now_text, tone = f"{disp_who} {chat['step_text']}", "wait"
        else:
            now_text, tone = f"{disp_who} 正在干活：{chat['step_text']}", "run"
    elif disp_busy_after:
        disp_who, _ = _dispatcher(dispatcher)
        if chat["asking"]:
            now_text, tone = f"{disp_who} {chat['step_text']}", "wait"
        else:
            now_text, tone = f"{disp_who} 正在最终核验：{chat['step_text']}", "run"
    elif state == "待对齐":
        now_text, tone = "在和你对齐需求、写方案，等你批准方案", "wait"
    elif state == "待派发":
        now_text, tone = "方案已批准，等开工", "run"
    elif state == "待验收":
        now_text, tone = "做完了，等你确认", "ok"
    elif state == "已合并":
        now_text, tone = "已放进项目", "ok"
    elif state == "需Claude介入":
        # 卡住时中间标题下要放完整原因：用阶段里的说明全文（含剩下问题和建议），不再拿截短的摘要
        reason = str(phase.get("detail") or "")
        if not reason and escalations:
            reason = str(escalations[-1].get("say") or escalations[-1].get("summary") or "")
        now_text, tone = f"卡停下了，等你处理：{reason or '原因见交接记录'}", "bad"
    else:
        now_text, tone = "程序在安排下一步", "run"

    if open_ev and quiet is not None and quiet > QUIET_WARN_SECONDS and state not in TERMINAL_STATES:
        view["stale"] = True
        now_text, tone = now_text + "（超过 10 分钟没动静，可能卡住）", "warn"
    # 这些阶段顶部一句话跟阶段标签一致；放弃/中断不论还有没有未完成的派活都跟阶段走
    code = phase.get("code")
    chat_live = bool(chat and (chat.get("busy") or chat.get("asking")))
    tone_map = {"wait": "wait", "run": "run", "bad": "bad", "ok": "ok", "done": "idle"}
    if code == "by_claude":
        now_text = phase.get("label") or "Claude 亲自做"
        tone = tone_map.get(phase.get("tone"), tone)
    elif code in ("dropped", "interrupted", "merging", "merge_failed", "copy_missing", "accepting"):
        now_text = phase.get("label") or now_text
        tone = tone_map.get(phase.get("tone"), tone)
    elif (code in ("plan_failed", "plan_waiting", "planning", "start_failed")
            and not chat_live and not open_ev and not testing):
        now_text = phase.get("label") or now_text
        tone = tone_map.get(phase.get("tone"), tone)
    working = bool(open_ev) and not interrupted_run and state not in TERMINAL_STATES
    detail = live_hint(cdir / open_ev["live"], limit=150, tool=open_ev.get("tool", ""),
                       test_command=card_test_cmd) if working and open_ev.get("live") else ""
    view["now"] = {"text": _plain(now_text), "tone": tone}
    # 写测试、写代码时的进度（第四轮 53）：程序记在 进度.json，只认这一次派活的
    if working and open_ev.get("dst") in ("测试员", "开发员"):
        from collab.progress import read as read_progress
        prog = read_progress(cdir)
        if prog.get("seq") == open_ev.get("seq"):
            key = "test" if open_ev.get("dst") == "测试员" else "dev"
            st = next(x for x in stations if x["key"] == key)
            st["progress"] = {k: prog[k] for k in ("passed", "total", "written", "computing", "error") if k in prog}
    # 停下后替你起草的“继续改”（第四轮 56 甲）：最新一条、之后还没用过（没有追加要求）才给
    drafted = [e for e in events if e.get("kind") == "起草" and e.get("phase") == "好了"]
    if drafted and state == "需Claude介入" and not any(
            e.get("kind") == "追加要求" and e.get("seq", 0) > drafted[-1].get("seq", 0) for e in events):
        view["draft"] = text_of(cdir, drafted[-1])
    if detail:
        view["now"]["detail"] = _plain(detail)
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
    if code in ("review", "merge_failed", "look_pending"):
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
    from collab.look import last_look, look_info
    look = look_info(cdir)
    if look["required"]:   # 第三批 ③：施工单要求对照的图和原话；图能打开的给出路径（只读看图接口按“设计图”认）
        images = []
        for rel in look["images"]:
            images.append({"path": rel, "name": PurePosixPath(rel).name,
                           "exists": resolve_walk_image(project, card_id, "design", rel) is not None})
        seen = last_look(events)
        view["look"] = {"line": look["line"], "images": images,
                        "done": bool(seen), "choice": seen.get("choice", ""), "note": seen.get("note", "")}
    view["walk_shots"] = _walk_shots(project, card_id, cdir, events)
    return view
