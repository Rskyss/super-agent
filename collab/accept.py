"""接受复查剩下的问题：先跑全部测试，全过且结果有效才进入待确认。"""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path

from collab.flow import read_flow, record
from collab.fullrun import FullRunner, applicable, zero_tests
from collab.judge import FAIL, PASS, STALE, TIMEOUT, Verdict
from collab.ledger import ledger_add
from collab.phase import _blocked_detail, _last_stop, _stop_text, is_review_stop
from collab.state import Card, InvalidTransition
from collab.workspace import render_board

_OUTPUT_LIMIT = 8000

_FAIL_REASONS = {
    FAIL: "全部测试没过，卡片留在卡住了，先看是哪项没过",
    TIMEOUT: "全部测试有文件超时，卡片留在卡住了",
    STALE: "跑全部测试时代码变了，结果作废，再点一次接受",
}
_OTHER_FAIL_REASON = "全部测试没跑起来，卡片留在卡住了"
_ZERO_TEST_REASON = "全部测试只跑了 0 项，不能进待确认"
_STOPPED_REASON = "接受任务被外部叫停（收到结束信号），全部测试已停止，卡片还留在卡住了"


@contextmanager
def card_lock(state_path):
    """卡片状态的“读→判断→写”要在一把锁里做完：终端续跑和接受任务同时改状态时，
    后拿到锁的必须看到先拿到锁的结果，不能拿旧状态盖掉（复查 54 第 2、7 条）。"""
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _fail_record(cdir: Path, reason: str, output: str | None = None) -> dict:
    """记录接受失败并返回失败结果。"""
    kwargs = {"src": "程序", "dst": "你", "summary": reason}
    if output is not None:
        kwargs["text"] = output[-_OUTPUT_LIMIT:]
    record(cdir, "接受失败", **kwargs)
    return {"ok": False, "reason": reason}


def record_stopped(project, card_id: str, signum=None) -> None:
    """接受进程被外部叫停时留一笔，交接里能看见为什么没跑完。"""
    cdir = Path(project) / ".collab" / "cards" / card_id
    try:
        record(cdir, "接受失败", src="程序", dst="你", summary=_STOPPED_REASON, text=f"收到结束信号：{signum}")
    except OSError:
        pass


def accept_remaining(project, card_id: str, runner=None) -> dict:
    """复查不通过转人工后，先跑全部测试，全过才把卡片转成待验收。"""
    project_path = Path(project)
    cdir = project_path / ".collab" / "cards" / card_id
    state_path = cdir / "状态.json"
    if not state_path.is_file():
        return {"ok": False, "reason": "这张卡现在不能这样做。"}

    card = Card.load(state_path)
    events = read_flow(cdir)
    stop = _last_stop(events)
    if card.state != "需Claude介入" or not is_review_stop(stop):
        return {"ok": False, "reason": "这张卡现在不能这样做。"}

    issues = _stop_text(project_path, card_id, stop) or _blocked_detail(events)
    worktree = project_path.parent / f"{project_path.name}-worktrees" / card_id
    if not worktree.is_dir():
        return _fail_record(cdir, "找不到改好的版本，没法跑全部测试")

    try:
        if not applicable(worktree):
            verdict = Verdict(PASS, 0, "")
        else:
            verdict = (runner or FullRunner()).run(worktree)
    except Exception as exc:  # noqa: BLE001 — 准备或运行意外出错都要留下可见失败记录
        return _fail_record(cdir, _OTHER_FAIL_REASON, str(exc))

    if verdict.result != PASS:
        reason = _FAIL_REASONS.get(verdict.result, _OTHER_FAIL_REASON)
        return _fail_record(cdir, reason, verdict.output or "")
    if zero_tests(verdict.output):
        return _fail_record(cdir, _ZERO_TEST_REASON, verdict.output or "")

    try:
        with card_lock(state_path):
            fresh = Card.load(state_path)
            fresh_stop = _last_stop(read_flow(cdir))
            if (fresh.state != "需Claude介入" or not is_review_stop(fresh_stop)
                    or fresh_stop.get("seq") != stop.get("seq")):
                return {"ok": False, "reason": "这张卡现在不能这样做。"}
            fresh.transition("待验收")
            fresh.save(state_path)
    except (InvalidTransition, OSError, ValueError):
        return {"ok": False, "reason": "这张卡现在不能这样做。"}
    record(cdir, "接受", src="你", dst="程序", summary="接受剩下的问题，进入待确认", text=issues)
    record(
        cdir,
        "结束",
        src="程序",
        dst="你",
        state="待验收",
        summary="你接受了复查剩下的问题，全部测试也过了，等你确认放进项目",
    )
    render_board(project_path / ".collab")
    ledger_add(project_path, f"{card_id}：接受复查剩下的问题，全部测试通过，进入待确认")
    return {"ok": True}
