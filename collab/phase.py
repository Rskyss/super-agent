"""卡片阶段：页面、按钮和后台任务共用这一份判断。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from collab.flow import read_flow
from collab.scope import read_allowed
from collab.state import Card


def process_is_alive(pid) -> bool:
    if pid is None:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except (OSError, ValueError, TypeError):
        return True


def _collab(project: str) -> Path:
    return Path(project) / ".collab"


def _card_dir(project: str, card_id: str) -> Path:
    return _collab(project) / "cards" / card_id


def _backend_record(project: str, kind: str) -> Optional[dict]:
    path = _collab(project) / "后台" / f"{kind}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if not process_is_alive(pid):
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return data


def _plan_job_state(project: str, card_id: str) -> str:
    """写方案后台：running / lost（进程没了）/ none。"""
    path = _collab(project) / "后台" / "plan.json"
    if not path.is_file():
        return "none"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "none"
    if not isinstance(data, dict) or data.get("card") != card_id:
        return "none"
    pid = data.get("pid")
    if pid is None:
        try:
            path.unlink()
        except OSError:
            pass
        return "lost"
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        try:
            path.unlink()
        except OSError:
            pass
        return "lost"
    if process_is_alive(pid):
        return "running"
    try:
        path.unlink()
    except OSError:
        pass
    return "lost"


def _last_plan_failure(events: list) -> Optional[dict]:
    for e in reversed(events):
        if e.get("kind") == "方案失败":
            return e
    return None


def _last_success_plan(events: list) -> Optional[dict]:
    for e in reversed(events):
        if e.get("kind") == "方案":
            return e
    return None


def _has_complete_plan(card_dir: Path, events: list) -> bool:
    plan_path = card_dir / "方案.md"
    if not plan_path.is_file():
        return False
    req = (card_dir / "需求卡.md").read_text(encoding="utf-8")
    allowed = read_allowed(req)
    if not allowed:
        return False
    start = (card_dir / "开工.json").is_file()
    if not start:
        return False
    last_plan = _last_success_plan(events)
    if last_plan is None:
        return False
    fail = _last_plan_failure(events)
    if fail is not None and fail.get("seq", 0) > last_plan.get("seq", 0):
        return False
    return True


def _failure_detail(events: list) -> str:
    ev = _last_plan_failure(events)
    if ev:
        return str(ev.get("summary") or "").strip()
    return ""


def _blocked_detail(events: list) -> str:
    for e in reversed(events):
        if e.get("kind") == "转人工":
            return str(e.get("summary") or "").strip()
    return ""


def card_phase(project: str, card_id: str) -> dict:
    cdir = _card_dir(project, card_id)
    state_path = cdir / "状态.json"
    if not state_path.is_file():
        return {
            "code": "unknown", "label": card_id, "tone": "done", "needs_you": False,
            "detail": "", "actions": [],
        }
    card = Card.load(state_path)
    events = read_flow(cdir)
    state = card.state

    plan_state = _plan_job_state(project, card_id)
    run_live = _backend_record(project, "run")

    runs = [e for e in events if e.get("kind") == "开跑"]
    last_run = runs[-1] if runs else None
    last_pid = last_run.get("pid") if last_run else None
    run_proc_dead = last_pid is not None and not process_is_alive(last_pid)

    if state == "待对齐":
        if plan_state == "running":
            return {
                "code": "planning", "label": "写方案中", "tone": "run", "needs_you": False,
                "detail": "", "actions": [],
            }
        if plan_state == "lost":
            return {
                "code": "plan_failed", "label": "方案没写成", "tone": "bad", "needs_you": True,
                "detail": "", "actions": ["replan", "discard"],
            }
        fail = _last_plan_failure(events)
        succ = _last_success_plan(events)
        fail_seq = fail.get("seq", -1) if fail else -1
        succ_seq = succ.get("seq", -1) if succ else -1
        if fail is not None and succ is not None and fail_seq > succ_seq:
            return {
                "code": "plan_failed", "label": "方案没写成", "tone": "bad", "needs_you": True,
                "detail": _failure_detail(events), "actions": ["replan", "discard"],
            }
        if _has_complete_plan(cdir, events):
            return {
                "code": "plan_ready", "label": "等你批准方案", "tone": "wait", "needs_you": True,
                "detail": "", "actions": ["approve", "discard"],
            }
        if fail is None:
            return {
                "code": "plan_waiting", "label": "等待写方案", "tone": "run", "needs_you": False,
                "detail": "", "actions": [],
            }
        return {
            "code": "plan_failed", "label": "方案没写成", "tone": "bad", "needs_you": True,
            "detail": _failure_detail(events), "actions": ["replan", "discard"],
        }

    if state == "待派发":
        return {
            "code": "ready", "label": "准备开工", "tone": "run", "needs_you": False,
            "detail": "", "actions": [],
        }

    if state in ("开发中", "待测试", "测试中"):
        if run_live and run_live.get("card") == card_id:
            return {
                "code": "running", "label": "进行中", "tone": "run", "needs_you": False,
                "detail": "", "actions": ["stop"],
            }
        if last_pid is None or run_proc_dead:
            return {
                "code": "interrupted", "label": "中断了", "tone": "bad", "needs_you": True,
                "detail": "", "actions": ["resume", "discard"],
            }
        return {
            "code": "running", "label": "进行中", "tone": "run", "needs_you": False,
            "detail": "", "actions": ["stop"],
        }

    if state == "需Claude介入":
        return {
            "code": "blocked", "label": "卡住了", "tone": "bad", "needs_you": True,
            "detail": _blocked_detail(events), "actions": ["resume", "discard"],
        }

    if state == "待验收":
        return {
            "code": "review", "label": "等你验收", "tone": "ok", "needs_you": True,
            "detail": "", "actions": [],
        }

    if state == "已合并":
        return {
            "code": "done", "label": "已完成", "tone": "done", "needs_you": False,
            "detail": "", "actions": [],
        }

    if state == "已放弃":
        return {
            "code": "dropped", "label": "不做了", "tone": "done", "needs_you": False,
            "detail": "", "actions": [],
        }

    return {
        "code": "unknown", "label": state, "tone": "done", "needs_you": False,
        "detail": "", "actions": [],
    }
