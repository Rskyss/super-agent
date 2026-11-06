"""行政提议与后台任务：模型只能提议，用户确认才建卡；后台子进程与本机服务分离。"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from collab.config import ConfigError, load_team, _check_member  # noqa: SLF001
from collab.phase import card_phase, process_is_alive
from collab.tasks import TaskError, new_card

COLLAB_PACKAGE = Path(__file__).resolve().parent.parent.parent / "collab"

FOLDER = Path(".collab") / "行政提议"
_FIELDS = ("title", "do", "not_do", "done_when")
_FIELD_NAMES = {"title": "标题", "do": "要做什么", "not_do": "不做什么", "done_when": "怎么算做完"}
_TEAM_ROLES = frozenset({"dispatcher", "tester", "developer", "reviewer"})
_TEAM_TOOLS = frozenset({"codex", "cursor", "agy"})
_TEAM_REVIEWER_TOOLS = _TEAM_TOOLS | {"none"}
_lock = threading.Lock()


class TaskBusy(Exception):
    """同一种后台任务已经在跑。"""


def _folder(project: str) -> Path:
    return Path(project) / FOLDER


def _config_path(project: str) -> Path:
    return Path(project) / ".collab" / "配置.json"


def _apply_team_change(project: str, role: str, tool: str) -> None:
    """合并写入 .collab/配置.json 的 team；未选调度员时不能走 save_team（会拒绝 None）。"""
    path = _config_path(project)
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    team = raw.get("team")
    if not isinstance(team, dict):
        team = {}
    _check_member(role, tool)
    team[role] = tool
    raw["team"] = team
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    load_team(project)


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


def propose_team_change(conv, arguments: dict) -> str:
    """模型调用 propose_team_change：角色与工具合法才生成团队设置提议。"""
    args = arguments if isinstance(arguments, dict) else {}
    role = str(args.get("role") or "").strip()
    tool = str(args.get("tool") or "").strip()
    if role not in _TEAM_ROLES:
        return "角色必须是调度员、写测试、写代码、复查之一，先问用户要改哪一项。"
    allowed = _TEAM_REVIEWER_TOOLS if role == "reviewer" else _TEAM_TOOLS
    if tool not in allowed:
        return "工具不对：复查可选 Codex、Cursor、agy 或 none；其他角色只能是 codex、cursor、agy。先问用户。"
    try:
        team = load_team(conv.project)
    except ConfigError as exc:
        return str(exc)
    before = team.get(role)
    before_s = before if isinstance(before, str) else ("" if before is None else str(before))
    with _lock:
        for old in _open_in_conversation(conv.project, conv.id):
            old["state"] = "replaced"
            _save(conv.project, old)
            conv.emit({"type": "proposal_done", "id": old["id"], "state": "replaced", "card": old.get("card") or ""})
        pid = uuid.uuid4().hex
        data = {
            "id": pid, "conversation": conv.id, "kind": "team",
            "role": role, "tool": tool, "before": before_s,
            "state": "open", "card": "", "time": time.time(),
        }
        _save(conv.project, data)
        _append_proposal_line(conv, pid)
        event = {
            "type": "proposal", "id": data["id"], "kind": "team",
            "role": data["role"], "tool": data["tool"], "before": data["before"],
            "state": data["state"], "card": data["card"],
        }
        conv.emit(event)
    return "已向用户出示确认框，等用户点确认或先不做。确认后才会写入团队配置。"


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
            if decision == "confirm" and data.get("state") == "confirmed":
                if data.get("kind") == "team":
                    return {"ok": True, "card": "", "state": "confirmed", "repeat": True, "kind": "team"}
                if data.get("card"):
                    return {"ok": True, "card": data["card"], "state": "confirmed", "repeat": True, "kind": "task"}
            raise RuntimeError("这个提议已经处理过了")
        if decision == "cancel":
            data["state"] = "cancelled"
            _save(project, data)
            return {"ok": True, "card": "", "state": "cancelled", "repeat": False, "kind": data.get("kind") or "task"}
        kind = data.get("kind") or "task"
        if kind == "team":
            role = str(data.get("role") or "").strip()
            tool = str(data.get("tool") or "").strip()
            if role not in _TEAM_ROLES:
                raise RuntimeError("这个提议的角色不对")
            allowed = _TEAM_REVIEWER_TOOLS if role == "reviewer" else _TEAM_TOOLS
            if tool not in allowed:
                raise RuntimeError("这个提议的工具不对")
            try:
                _apply_team_change(project, role, tool)
            except ConfigError as exc:
                raise RuntimeError(str(exc)) from exc
            data["state"] = "confirmed"
            _save(project, data)
            return {"ok": True, "card": "", "state": "confirmed", "repeat": False, "kind": "team"}
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
        return {"ok": True, "card": card, "state": "confirmed", "repeat": False, "kind": "task"}


def event_from_proposal(data: dict) -> dict:
    kind = data.get("kind") or "task"
    base = {
        "type": "proposal", "id": data["id"], "kind": kind,
        "state": data.get("state") or "open", "card": data.get("card") or "",
    }
    if kind == "team":
        base["role"] = data.get("role") or ""
        base["tool"] = data.get("tool") or ""
        if "before" in data:
            base["before"] = data["before"]
    else:
        base["title"] = data.get("title") or ""
        base["do"] = data.get("do") or ""
        base["not_do"] = data.get("not_do") or ""
        base["done_when"] = data.get("done_when") or ""
    return base


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


def stop_construction(project: str) -> None:
    """对施工进程组先发 SIGTERM，5 秒后仍在则 SIGKILL。"""
    rec = running(project, "run")
    if not rec:
        return
    try:
        pid = int(rec["pid"])
    except (TypeError, ValueError):
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
    deadline = time.time() + 5
    while time.time() < deadline:
        if not _pid_alive(pid):
            break
        time.sleep(0.05)
    if _pid_alive(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    while _pid_alive(pid):
        time.sleep(0.05)


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


_REPORT_CODES = frozenset({"plan_ready", "plan_failed", "review", "blocked", "interrupted"})


def _card_title(cdir: Path, card_id: str) -> str:
    req = cdir / "需求卡.md"
    if req.is_file():
        for line in req.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("# "):
                t = line[2:].strip()
                if t:
                    return t
    return card_id


def _conversation_from_source(cdir: Path) -> str:
    path = cdir / "来源.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("conversation") or "").strip()


def _load_reported_code(cdir: Path) -> Optional[str]:
    path = cdir / "已汇报.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    code = data.get("code")
    return str(code) if isinstance(code, str) else None


def _save_reported_code(cdir: Path, code: str) -> None:
    path = cdir / "已汇报.json"
    spare = path.with_suffix(".tmp")
    spare.write_text(json.dumps({"code": code}, ensure_ascii=False), encoding="utf-8")
    spare.replace(path)


def _flow_events(cdir: Path) -> list:
    from collab.flow import read_flow
    return read_flow(cdir)


def _user_stop_after_last_run(events: list) -> bool:
    runs = [e for e in events if e.get("kind") == "开跑"]
    if not runs:
        return False
    last = runs[-1]
    try:
        pid_i = int(last.get("pid"))
    except (TypeError, ValueError):
        return False
    if process_is_alive(pid_i):
        return False
    last_seq = int(last.get("seq") or 0)
    for e in events:
        if e.get("kind") != "停下":
            continue
        if int(e.get("seq") or 0) <= last_seq:
            continue
        summary = str(e.get("summary") or "")
        if "用户" in summary:
            return True
    return False


def _report_text(code: str, title: str, phase: dict) -> str:
    reason = str(phase.get("detail") or "").strip()
    if code == "plan_ready":
        return f"「{title}」的方案写好了，点左边的卡片看方案，批准后才开工。"
    if code == "plan_failed":
        base = f"「{title}」的方案没写成"
        if reason:
            base += f"：{reason}"
        else:
            base += "：原因不明"
        return base + "。可以点卡片重写。"
    if code == "review":
        return f"「{title}」做完了，等你验收。"
    if code == "blocked":
        base = f"「{title}」卡住了"
        if reason:
            base += f"：{reason}"
        else:
            base += "：原因不明"
        return base + "。"
    if code == "interrupted":
        return f"「{title}」中断了，可以点卡片接着跑。"
    return ""


class TaskWatcher:
    """项目级巡视：单线程每 2 秒；业务集中在 tick。"""

    def __init__(self, desk) -> None:
        self._desk = desk
        self._tick_lock = threading.Lock()
        self._started = set()
        self._projects_lock = threading.Lock()

    def ensure_watching(self, project: str) -> None:
        p = str(project)
        if not p:
            return
        with self._projects_lock:
            if p in self._started:
                return
            self._started.add(p)

        def loop():
            while True:
                try:
                    self.tick(p)
                except Exception:
                    pass
                time.sleep(2)

        threading.Thread(target=loop, daemon=True).start()

    def tick(self, project: str) -> None:
        with self._tick_lock:
            self._tick_impl(project)

    def _tick_impl(self, project: str) -> None:
        root = Path(project) / ".collab" / "cards"
        if not root.is_dir():
            return
        for cdir in sorted(root.iterdir(), key=lambda p: p.name):
            if not cdir.is_dir():
                continue
            state_path = cdir / "状态.json"
            if not state_path.is_file():
                continue
            card_id = cdir.name
            has_source = (cdir / "来源.json").is_file()
            phase = card_phase(project, card_id)
            code = str(phase.get("code") or "")

            if has_source and code == "plan_waiting" and running(project, "plan") is None:
                try:
                    launch(project, "plan", card_id, ["plan", project, card_id])
                except TaskBusy:
                    pass

            prev = _load_reported_code(cdir)
            if prev is None:
                if has_source:
                    _save_reported_code(cdir, code)
                continue

            if code == prev:
                continue

            if not has_source:
                _save_reported_code(cdir, code)
                continue

            if code not in _REPORT_CODES:
                _save_reported_code(cdir, code)
                continue

            if code == "interrupted" and _user_stop_after_last_run(_flow_events(cdir)):
                _save_reported_code(cdir, code)
                continue

            conversation = _conversation_from_source(cdir)
            title = _card_title(cdir, card_id)
            text = _report_text(code, title, phase)
            if text and conversation:
                self._desk.notify(project, conversation, text)
            _save_reported_code(cdir, code)
