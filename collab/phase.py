"""卡片阶段：页面、按钮和后台任务共用这一份判断。"""
from __future__ import annotations

import fcntl
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


def _backend_cleaning_up(project: str, kind: str) -> bool:
    """后台进程已经退出、桌面还在收尾（停掉剩下的测试、补写原因）时返回真。
    任务期间桌面一直占着这把锁，进程一死锁就没了，所以不会误判成“还在跑”。"""
    path = _collab(project) / "后台" / f"{kind}.json"
    lock = path.with_suffix(".cleanup.lock")
    if not lock.is_file():
        return False
    try:
        with open(lock, "a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return True
    return False


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


def _merge_not_started(project: str, cdir: Path) -> bool:
    """合并进行中的记录在，但项目还停在合并前。"""
    try:
        saved = json.loads((cdir / "合并进行中.json").read_text(encoding="utf-8"))
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=project, stderr=subprocess.DEVNULL,
                                       text=True).strip()
    except (OSError, ValueError, subprocess.CalledProcessError):
        return False
    return isinstance(saved, dict) and head == saved.get("base")


def _branch_name(project: str) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project, stderr=subprocess.DEVNULL, text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    name = (out or "").strip()
    return "" if name == "HEAD" else name


def _after_last(events: list, kind: str) -> list:
    last = -1
    for ev in events:
        if ev.get("kind") == kind:
            last = int(ev.get("seq") or 0)
    return [ev for ev in events if int(ev.get("seq") or 0) > last]


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


def _last_stop(events: list):
    for e in reversed(events):
        if e.get("kind") == "转人工":
            return e
    return None


def is_review_stop(stop: Optional[dict]) -> bool:
    """判断最近一次转人工是否因复查不通过停下（含缺 cause 的旧格式兼容）。"""
    if not stop:
        return False
    old_review_stop = not stop.get("cause") and str(stop.get("summary") or "").endswith(
        "复查不通过，需要调度员看原因"
    )  # 更新前停下的卡没有标记，按当时程序写的原话认（复查第 2 轮）
    return stop.get("cause") == "复查不通过" or old_review_stop


def _stop_text(project, card_id: str, event: dict) -> str:
    """转人工时存下的全文（复查员剩下的全部问题），流转摘要只有前 80 字。"""
    rel = event.get("text")
    if not rel:
        return ""
    try:
        return (Path(project) / ".collab" / "cards" / card_id / rel).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _blocked_detail(events: list) -> str:
    for e in reversed(events):
        if e.get("kind") == "转人工":
            return str(e.get("say") or e.get("summary") or "").strip()  # say：给人看的完整一句（第二轮 ㉞）
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
    extra = _last_of(events, "追加要求")
    succ_seq = succ.get("seq", -1) if succ else -1
    fail_seq = fail.get("seq", -1) if fail else -1
    extra_seq = extra.get("seq", -1) if extra else -1
    waiting = {
        "code": "plan_waiting", "label": "等待写方案", "tone": "run", "needs_you": False,
        "detail": "", "actions": [],
    }
    # 新要求比现有方案新：不再出示旧方案，等巡视重新写
    if extra is not None and extra_seq > succ_seq and extra_seq > fail_seq:
        return waiting

    # 2. 最新结果是方案，且内容齐全（残留 plan.json 也不影响）
    if succ is not None and succ_seq >= fail_seq and _has_complete_plan(cdir, events):
        return {
            "code": "plan_ready", "label": "等你批准方案", "tone": "wait", "needs_you": True,
            "detail": "", "actions": ["approve", "revise", "discard"],
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


def _later_items(events: list) -> list:
    """这张卡复查记进“以后再修”的事，大白话（第四轮 57）。去重；最后一次继续改之前记的，标“继续改之前记的，可能已经修了”
    （复查第 2 轮：清单没有“修好了”的标记，不能宣称这次不改）。"""
    last_revise = max((i for i, e in enumerate(events) if e.get("kind") == "追加要求"), default=-1)
    out, seen = [], set()
    for i, e in enumerate(events):
        if e.get("kind") != "以后再修":
            continue
        items = [str(x) for x in (e.get("items") or [])] or [str(e.get("summary") or "")]
        keys = e.get("item_keys") if isinstance(e.get("item_keys"), list) else None
        for idx, t in enumerate(items):
            k = str(keys[idx]).strip() if keys and idx < len(keys) else t
            if k in seen:
                continue
            seen.add(k)
            out.append(t + ("（继续改之前记的，可能已经修了）" if i < last_revise else ""))
    return out


def _count_current_later(events: list, items_field: str, keys_field: str, *, fallback_items: bool = False) -> int:
    """本次开工（最后一次追加要求之后）记进“以后再修”的去重件数；优先用带位置的键去重。"""
    last_revise = max((i for i, e in enumerate(events) if e.get("kind") == "追加要求"), default=-1)
    seen = set()
    for i, e in enumerate(events):
        if i <= last_revise or e.get("kind") != "以后再修":
            continue
        keys = e.get(keys_field)
        if isinstance(keys, list) and keys:
            for k in keys:
                s = str(k).strip()
                if s and s not in seen:
                    seen.add(s)
            continue
        raw = e.get(items_field)
        if fallback_items and raw is None and e.get("new_items") is None and e.get("old_items") is None:
            raw = e.get("items")
            keys = e.get("item_keys")
            if isinstance(keys, list) and keys:
                for k in keys:
                    s = str(k).strip()
                    if s and s not in seen:
                        seen.add(s)
                continue
        for idx, t in enumerate(raw or []):
            s = str(t).strip()
            if s:
                seen.add((idx, s))
    return len(seen)


def _current_new_later_count(events: list) -> int:
    """本次开工（最后一次追加要求之后）记进“以后再修”的这次改出来的小问题件数（去重）。"""
    return _count_current_later(events, "new_items", "new_keys")


def _current_old_later_count(events: list) -> int:
    """本次开工（最后一次追加要求之后）记进“以后再修”的开工前就有的问题件数（去重）。"""
    return _count_current_later(events, "old_items", "old_keys", fallback_items=True)


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
        accept_rec = _read_backend(project, "accept")
        if (
            accept_rec is not None
            and accept_rec.get("card") == card_id
            and (process_is_alive(accept_rec.get("pid"), accept_rec.get("started"))
                 or _backend_cleaning_up(project, "accept"))
        ):
            return {
                "code": "accepting", "label": "正在跑全部测试", "tone": "run", "needs_you": False,
                "detail": "全过就进待确认，约几分钟", "actions": [],
            }
        base_detail = _blocked_detail(events)
        detail = base_detail
        actions = ["resume", "revise", "discard"]
        after_stop = _after_last(events, "转人工")
        drafts = [e for e in after_stop if e.get("kind") == "起草"]
        if drafts and drafts[-1].get("phase") == "开始":
            detail = (detail.rstrip("。") + "。" if detail else "") + "正在替你起草“继续改”的话，约 3–5 分钟"
        elif drafts and drafts[-1].get("phase") == "好了":
            detail = (detail.rstrip("。") + "。" if detail else "") + "“继续改”的话已起草好，点“不满意，继续改”就能看到"
        accept_fails = [e for e in after_stop if e.get("kind") == "接受失败"]
        if accept_fails:
            fail_summary = str(accept_fails[-1].get("summary") or "").strip()
            if fail_summary:
                detail = (detail.rstrip("。") + "。" if detail else "") + fail_summary
        out = {"code": "blocked", "label": "卡住了", "tone": "bad", "needs_you": True, "detail": detail}
        stop = _last_stop(events)
        is_review_stop_flag = is_review_stop(stop)
        has_stop_issues = bool(stop) and (
            isinstance(stop.get("退回问题"), list)
            or (isinstance(stop.get("清单"), dict) and isinstance(stop["清单"].get("问题"), list))
            or isinstance(stop.get("issues"), list)
        )
        if is_review_stop_flag or has_stop_issues:
            # 只在复查不通过而停下时（看程序记的标记，不看文字）：用户可以带着剩下的问题进入待确认
            if is_review_stop_flag:
                actions = ["resume", "revise", "accept_issues", "discard"]
            from collab.flow import plain_issues
            manifest = stop.get("清单") if isinstance(stop.get("清单"), dict) else None
            stored_back = stop.get("退回问题") if isinstance(stop.get("退回问题"), list) else None
            listed, list_hidden, list_severe_hidden = [], 0, False
            if stored_back:
                from collab.dispatch import plain_problems
                listed, list_hidden, list_severe_hidden = plain_problems(
                    [p for p in stored_back if isinstance(p, dict)]
                )
            elif manifest and isinstance(manifest.get("问题"), list):
                from collab.dispatch import _needs_return, plain_problems
                back = [p for p in manifest["问题"] if isinstance(p, dict) and _needs_return(p)]
                listed, list_hidden, list_severe_hidden = plain_problems(back)   # 大白话，严重的标出来（11-18）
            plain = listed or (stop.get("issues") if isinstance(stop.get("issues"), list) else [])
            raw_stop = _stop_text(project, card_id, stop)
            has_list = bool(stored_back) or bool(manifest and isinstance(manifest.get("问题"), list))
            has_formal_list = (not stop.get("整段退回")) and has_list
            if has_list and not listed and list_hidden:   # 有正式清单就只认清单，一条都翻不成也不回头塞原话（11-18 复查 2、6）
                plain = [f"{list_hidden} 件都没写成大白话{'（其中有严重的）' if list_severe_hidden else ''}"]
                list_hidden = 0
            if is_review_stop_flag:
                plain = plain or plain_issues(raw_stop)  # 老卡没存 issues：现算
                if not has_formal_list and raw_stop:
                    import re as _re
                    from collab.dispatch import _raw_list_problems
                    extra = []
                    raw_problems, _ = _raw_list_problems(raw_stop)
                    for problem in raw_problems:
                        if problem.get("来源") != "原来就有的" or problem.get("级别") == "严重" \
                                or problem.get("类型") == "测试写错":
                            phen = str(problem.get("现象") or "").strip()
                            if phen and not any(phen in p for p in plain + extra):   # 只跳过被完全包含的，长句里的严重问题不能被短句盖掉（11-18）
                                level = problem.get("级别")
                                extra.append(f"【{level}】{phen}" if level else phen)
                    for ln in _re.sub(r"```.*?```", "", raw_stop, flags=_re.S).splitlines():
                        s = ln.strip()
                        if not s or _re.match(r"^[ \t*#]*结论", s):
                            continue
                        if _re.search(r"P[01]\b|严重|高危|安全|丢失|崩溃|测试的问题", s):
                            body = _re.sub(r"^\s*(?:[-*•]\s*|\d+[.、)）]\s*)?(?:【[^】]{1,12}】\s*)*", "", s)
                            head = _re.split(r"[。！？!?]", body, maxsplit=1)[0].strip()
                            if head and not any(head in p for p in plain + extra):
                                extra.append(head)
                    if extra:
                        plain = extra + list(plain)
            # 确认框里只放大白话，原话去“交接”里看（第二轮 ㊲）；认不出编号就不塞原文
            hidden = list_hidden if has_list else (int(stop.get("hidden") or 0) if plain and stop.get("issues") else 0)
            severe_note = "，其中有严重的" if has_list and list_severe_hidden else ""
            if plain or is_review_stop_flag:
                out["issues"] = ("\n".join(f"{i + 1}. {t}" for i, t in enumerate(plain)) if plain
                                 else "复查员没按格式写剩下的问题") + \
                    (f"\n另有 {hidden} 件没写成大白话{severe_note}" if hidden else "") + "\n（复查员原话在右边“交接”里）"
            if is_review_stop_flag:
                out["issues_raw"] = raw_stop or base_detail  # 接受时留档用原话（第 3 轮 A 第 2 条）
        out["actions"] = actions
        return out

    if state == "待验收":
        branch = _branch_name(project)
        tail = _after_last(events, "结束")
        merge_rec = _read_backend(project, "merge")
        merging = (
            merge_rec is not None
            and merge_rec.get("card") == card_id
            and process_is_alive(merge_rec.get("pid"), merge_rec.get("started"))
        )
        if not merging:
            for ev in reversed(tail):
                if ev.get("kind") != "合并开始":
                    continue
                if process_is_alive(ev.get("pid"), ev.get("started")):
                    merging = True
                break
        if merging:
            return {
                "code": "merging", "label": "正在放进项目", "tone": "run", "needs_you": False,
                "detail": "", "actions": [],
            }
        last_result = None
        for ev in tail:
            if ev.get("kind") in ("合并失败", "合并开始", "合并"):
                last_result = ev
        if (cdir / "合并进行中.json").is_file():
            told = last_result.get("summary") if last_result and last_result.get("kind") == "合并失败" else ""
            if not told and _merge_not_started(project, cdir):
                told = "放进项目中途断了，项目没有变，可以再试一次。"
            return {
                "code": "merge_failed", "label": "没能放进项目", "tone": "bad", "needs_you": True,
                "detail": str(told or "放进项目停在了半路，项目里的代码还没验证完。再点一次继续验证，或点“放弃这个需求”退回。"),
                "actions": ["accept", "discard"], "branch": branch, "later": _later_items(events),
            }
        worktree = Path(project).parent / f"{Path(project).name}-worktrees" / card_id
        if any(ev.get("kind") == "开跑" for ev in events) and not worktree.is_dir():
            return {
                "code": "copy_missing", "label": "改好的版本不在了", "tone": "bad", "needs_you": True,
                "detail": "改好的版本不在了。可以重新提这个需求。", "actions": ["discard"],
            }
        if last_result and last_result.get("kind") == "合并失败":
            return {
                "code": "merge_failed", "label": "没能放进项目", "tone": "bad", "needs_you": True,
                "detail": str(last_result.get("summary") or ""), "actions": ["accept", "revise", "discard"],
                "branch": branch, "later": _later_items(events),
            }
        if last_result and last_result.get("kind") == "合并开始":
            return {
                "code": "merge_failed", "label": "没能放进项目", "tone": "bad", "needs_you": True,
                "detail": "放进项目中途断了，可以再试一次", "actions": ["accept", "revise", "discard"],
                "branch": branch, "later": _later_items(events),
            }
        later = _later_items(events)
        new_count = _current_new_later_count(events)
        old_count = _current_old_later_count(events)
        if new_count:
            detail = f"还剩 {new_count} 件小问题已记进以后再修和台账，这次不改"
        elif old_count:
            detail = f"另有 {old_count} 件开工前就有的问题，已记进台账，这次不改"
        else:
            detail = ""
        return {
            "code": "review", "label": "等你确认", "tone": "ok", "needs_you": True,
            "detail": detail,
            "actions": ["accept", "revise", "discard"], "branch": branch, "later": later,
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
