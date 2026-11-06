"""卡片阶段：页面、按钮和后台任务共用这一份判断。"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from collab.flow import read_flow
from collab.scope import read_allowed
from collab.state import Card


def process_start(pid) -> str:
    """读进程启动时间；读不到返回空字符串。固定英文和 UTC，避免语言/时区影响比对。"""
    if pid is None:
        return ""
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    env["TZ"] = "UTC"
    try:
        out = subprocess.check_output(
            ["ps", "-o", "lstart=", "-p", str(int(pid))],
            stderr=subprocess.DEVNULL, text=True, env=env,
        )
    except (OSError, ValueError, TypeError, subprocess.CalledProcessError):
        return ""
    return (out or "").strip()


def process_is_alive(pid, started=None) -> bool:
    """全项目唯一的判活。给了 started 时，启动时间对不上算没活着。"""
    if pid is None:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (OSError, ValueError, TypeError):
        return True
    if started is not None and str(started) != "":
        if process_start(pid) != str(started):
            return False
    return True


def _collab(project: str) -> Path:
    return Path(project) / ".collab"


def _card_dir(project: str, card_id: str) -> Path:
    return _collab(project) / "cards" / card_id


def _read_backend(project: str, kind: str) -> Optional[dict]:
    """只读后台记录，不删文件。清理过期记录由 running() 负责。"""
    path = _collab(project) / "后台" / f"{kind}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _plan_record(project: str, card_id: str) -> Optional[dict]:
    data = _read_backend(project, "plan")
    if data is None or data.get("card") != card_id:
        return None
    return data


def _plan_process_alive(project: str, card_id: str) -> bool:
    data = _plan_record(project, card_id)
    if data is None:
        return False
    pid = data.get("pid")
    if pid is None:
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    return process_is_alive(pid, data.get("started"))


def _last_of(events: list, kind: str) -> Optional[dict]:
    for e in reversed(events):
        if e.get("kind") == kind:
            return e
    return None


def _has_complete_plan(card_dir: Path, events: list) -> bool:
    plan_path = card_dir / "方案.md"
    if not plan_path.is_file():
        return False
    try:
        req = (card_dir / "需求卡.md").read_text(encoding="utf-8")
    except OSError:
        return False
    allowed = read_allowed(req)
    if not allowed:
        return False
    if not (card_dir / "开工.json").is_file():
        return False
    last_plan = _last_of(events, "方案")
    if last_plan is None:
        return False
    fail = _last_of(events, "方案失败")
    if fail is not None and fail.get("seq", 0) > last_plan.get("seq", 0):
        return False
    return True


def _failure_detail(events: list) -> str:
    ev = _last_of(events, "方案失败")
    if ev:
        return str(ev.get("summary") or "").strip()
    return ""


def _blocked_detail(events: list) -> str:
    for e in reversed(events):
        if e.get("kind") == "转人工":
            return str(e.get("summary") or "").strip()
    return ""


def _plan_phase(project: str, card_id: str, cdir: Path, events: list) -> dict:
    """待对齐阶段判断。顺序固定，同一输入多次调用结果一致。"""
    failed = {
        "code": "plan_failed", "label": "方案没写成", "tone": "bad", "needs_you": True,
        "detail": "", "actions": ["replan", "discard"],
    }
    # 1. 写方案进程活着
    if _plan_process_alive(project, card_id):
        return {
            "code": "planning", "label": "写方案中", "tone": "run", "needs_you": False,
            "detail": "", "actions": [],
        }

    succ = _last_of(events, "方案")
    fail = _last_of(events, "方案失败")
    succ_seq = succ.get("seq", -1) if succ else -1
    fail_seq = fail.get("seq", -1) if fail else -1

    # 2. 最新结果是方案，且内容齐全（残留 plan.json 也不影响）
    if succ is not None and succ_seq >= fail_seq and _has_complete_plan(cdir, events):
        return {
            "code": "plan_ready", "label": "等你批准方案", "tone": "wait", "needs_you": True,
            "detail": "", "actions": ["approve", "discard"],
        }

    # 3. 最新结果是方案失败
    if fail is not None and fail_seq > succ_seq:
        out = dict(failed)
        out["detail"] = _failure_detail(events)
        return out

    # 4. 有写方案记录或残留 plan.json，进程没了，之后没有结果
    writes = [e for e in events if e.get("kind") == "写方案"]
    leftover = _plan_record(project, card_id) is not None
    if writes or leftover:
        last_write_seq = writes[-1].get("seq", -1) if writes else -1
        later = any(
            e.get("kind") in ("方案", "方案失败") and e.get("seq", -1) > last_write_seq
            for e in events
        )
        # 只有残留文件、没有任何写方案/结果：也算中途断了
        if not later:
            out = dict(failed)
            out["detail"] = "写方案中途断了，可以重写"
            return out

    # 5. 其余：等待写方案
    return {
        "code": "plan_waiting", "label": "等待写方案", "tone": "run", "needs_you": False,
        "detail": "", "actions": [],
    }


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

    run_rec = _read_backend(project, "run")
    run_live = (
        run_rec is not None
        and run_rec.get("card") == card_id
        and process_is_alive(run_rec.get("pid"), run_rec.get("started"))
    )

    runs = [e for e in events if e.get("kind") == "开跑"]
    last_run = runs[-1] if runs else None
    last_pid = last_run.get("pid") if last_run else None
    last_started = last_run.get("started") if last_run else None
    run_proc_dead = last_pid is not None and not process_is_alive(last_pid, last_started)

    if state == "待对齐":
        return _plan_phase(project, card_id, cdir, events)

    if state == "待派发":
        start_results = [e for e in events if e.get("kind") in ("开工", "开工失败")]
        if start_results and start_results[-1].get("kind") == "开工失败":
            return {
                "code": "start_failed", "label": "开工失败", "tone": "bad", "needs_you": True,
                "detail": "", "actions": ["resume", "discard"],
            }
        started = False
        if run_rec is not None and run_rec.get("card") == card_id:
            started = True
            if process_is_alive(run_rec.get("pid"), run_rec.get("started")):
                return {
                    "code": "ready", "label": "准备开工", "tone": "run", "needs_you": False,
                    "detail": "", "actions": [],
                }
        starts = [e for e in events if e.get("kind") == "开工"]
        if starts:
            started = True
            last_start = starts[-1]
            if process_is_alive(last_start.get("pid"), last_start.get("started")):
                return {
                    "code": "ready", "label": "准备开工", "tone": "run", "needs_you": False,
                    "detail": "", "actions": [],
                }
        if started:
            return {
                "code": "start_failed", "label": "开工失败", "tone": "bad", "needs_you": True,
                "detail": "", "actions": ["resume", "discard"],
            }
        return {
            "code": "ready", "label": "准备开工", "tone": "run", "needs_you": False,
            "detail": "", "actions": [],
        }

    if state in ("开发中", "待测试", "测试中"):
        if run_live:
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
