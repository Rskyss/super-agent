"""派发主流程（测试先行）：
先由测试员按验收标准写测试 → 开发员写代码让测试通过 → 程序按退出码判定。
没过则退回开发（带测试原文），满 3 轮转"需Claude介入"。

所有外部调用（agent 执行、测试判定）都通过参数注入，便于用假实现测试。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from collab.agents import ask as default_ask
from collab.flow import read_flow, record, reply_summary, text_of
from collab.judge import ENV_ERROR, FAIL, PASS, TIMEOUT, judge as default_judge
from collab.scope import out_of_scope
from collab.state import Card
from collab.vcs import revert_paths
from collab.workspace import render_board

_TEST_TIMEOUT = 900  # 测试 15 分钟（D5）
# 规则书随包一起走：无论包装在哪个项目里，这个路径都存在
RULES = Path(__file__).resolve().parent / "规则.md"
_READ_RULES = f"开工前先读规则：{RULES}。"


def _card_dir(collab: Path, card: Card) -> Path:
    return collab / "cards" / card.card_id


def _save(collab: Path, card: Card) -> None:
    card.save(_card_dir(collab, card) / "状态.json")
    render_board(collab)


def _append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)


def _changed_files(worktree) -> str:
    """副本里当前有哪些改动（累计）。读不到时说明原因，不抛错。"""
    try:
        proc = subprocess.run(["git", "-c", "core.quotepath=off", "status", "--porcelain"], cwd=worktree,
                              capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "（无法读取改动）"
    files = [line[3:] for line in proc.stdout.splitlines() if line.strip()]
    return "、".join(files) if files else "（无改动）"  # 注意：这是副本里累计的全部改动


def _changed_list(worktree) -> list:
    """副本里改动过的文件路径列表（不带引号，中文路径也原样保留）。"""
    try:
        proc = subprocess.run(["git", "-c", "core.quotepath=off", "status", "--porcelain"],
                              cwd=worktree, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return []
    return [line[3:] for line in proc.stdout.splitlines() if line.strip()]


def readable(output: str) -> str:
    """各家工具的回复可能是 JSON 包装（agy 用 response，Cursor 用 result），只取里面的文字。"""
    text = (output or "").strip()
    candidates = [text] + [line.strip() for line in reversed(text.splitlines())
                           if line.strip().startswith("{") and line.strip().endswith("}")]
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            for key in ("response", "result", "message", "text"):
                if isinstance(data.get(key), str):
                    return data[key].strip()
    return text


_REPORT_ASK = "完成后，在回复的最后一行写“汇报：”加不超过100字的说明（改了什么、测试情况）。不要另建汇报文件。"
_TEST_FILES = "测试文件.json"
_BEFORE_TESTER = "测试前改动.json"
_TEST_SNAPSHOT = "测试快照"
_DEV_TEST_NOTE = "测试文件由测试员负责，不要修改；改了也会在判定前被恢复。"


def _plan_hint(cdir: Path) -> str:
    plan = cdir / "方案.md"
    if plan.is_file():
        return f"请严格按方案文件 {plan} 执行，尤其遵守其中「明确不做什么」。"
    return "请严格按任务卡执行，尤其遵守其中「明确不做什么」。"


def _load_json_list(path: Path) -> list:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [str(p) for p in data if str(p).strip()]


def _load_test_files(cdir: Path) -> list:
    return _load_json_list(cdir / _TEST_FILES)


def _save_json_list(path: Path, files: list) -> None:
    path.write_text(json.dumps(list(files), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _save_test_files(cdir: Path, files: list) -> None:
    _save_json_list(cdir / _TEST_FILES, files)


def _remember_before_tester(cdir: Path, worktree) -> list:
    """第一次派测试员前记下改动；已有文件不覆盖，接着跑时仍以第一次为准。"""
    path = cdir / _BEFORE_TESTER
    if path.is_file():
        return _load_json_list(path)
    before = _changed_list(worktree)
    _save_json_list(path, before)
    return before


def _expand_to_files(worktree, paths: list) -> list:
    """目录展开成具体文件；只保留文件路径，目录本身不进允许范围。"""
    root = Path(worktree)
    seen = set()
    out = []
    for raw in paths:
        rel = str(raw).replace("\\", "/").strip()
        if not rel or rel in seen:
            continue
        full = root / rel
        if full.is_dir():
            for child in sorted(full.rglob("*")):
                if not child.is_file():
                    continue
                item = child.relative_to(root).as_posix()
                if item not in seen:
                    seen.add(item)
                    out.append(item)
            continue
        if rel.endswith("/"):
            continue
        seen.add(rel)
        out.append(rel)
    return out


def _snapshot_test_files(cdir: Path, worktree, files: list) -> None:
    snap = cdir / _TEST_SNAPSHOT
    root = Path(worktree)
    for rel in files:
        src = root / rel
        if not src.is_file():
            continue
        dest = snap / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())


def _restore_test_snapshot(cdir: Path, worktree) -> list:
    """判定前把测试快照写回副本。没有快照的旧卡跳过。"""
    snap = cdir / _TEST_SNAPSHOT
    if not snap.is_dir():
        return []
    root = Path(worktree)
    restored = []
    for path in sorted(snap.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(snap).as_posix()
        dest = root / rel
        data = path.read_bytes()
        try:
            same = dest.is_file() and dest.read_bytes() == data
        except OSError:
            same = False
        if same:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        restored.append(rel)
    return restored


def _review_reject_pending(cdir: Path, past: list) -> tuple:
    """最近一次复查结论是不通过，且之后没有成功的开发员回复 → 必须带着意见重改。"""
    last_reject = None
    notes = ""
    for e in reversed(past):
        if e.get("kind") != "回复" or e.get("src") != "复查员":
            continue
        body = text_of(cdir, e)
        kind = review_verdict(body) if body else review_verdict(e.get("summary") or "")
        if kind == "reject":
            last_reject = e
            notes = body or (e.get("summary") or "")
            break
        if kind == "pass":
            return False, ""
    if last_reject is None:
        return False, ""
    reject_seq = int(last_reject.get("seq") or 0)
    for e in past:
        if int(e.get("seq") or 0) <= reject_seq:
            continue
        if e.get("kind") == "回复" and e.get("src") == "开发员" and e.get("ok") is True:
            return False, ""
    return True, notes


def _save_report(cdir: Path, output: str) -> None:
    """从开发员回复里取最后一段“汇报：……”，存进卡片文件夹。"""
    text = readable(output)
    if "汇报：" not in text:
        return
    lines = text.rsplit("汇报：", 1)[1].strip().splitlines()
    summary = lines[0].strip() if lines else ""
    if summary:
        _append(cdir / "汇报.md", f"- {datetime.now():%Y-%m-%d %H:%M} {summary[:200]}\n")


def _log_step(cdir: Path, step: str, adapter: str, worktree) -> None:
    _append(cdir / "执行记录.md",
            f"- {datetime.now():%Y-%m-%d %H:%M} | {step} | 执行者：{adapter} | 副本累计改动：{_changed_files(worktree)}\n")


_STEP_SUMMARY = {
    "写测试": "按验收标准先写测试（只写测试，不改产品代码）",
    "开发": "按测试写代码，让测试全部通过",
    "返工修改": "测试没过，带着测试输出退回修改",
    "复查": "只读复查：对照需求检查改动",
}

_REVIEW_VERDICT = re.compile(r"结论[:：]\s*(不通过|通过)")


def review_verdict(text: str) -> str:
    """从复查员回复里读结论：pass / reject / unclear。"""
    found = _REVIEW_VERDICT.findall(text or "")
    if "不通过" in found:
        return "reject"
    if "通过" in found:
        return "pass"
    return "unclear"


def _dispatch(ask_fn, cdir: Path, role: str, adapter: str, step: str, prompt: str,
              worktree, models, round_no: int):
    """派一次活：开始时记“派活”（含原文、预留实时输出），干完记“回复”（含全文、用时）。"""
    sent = record(cdir, "派活", src="调度员", dst=role, tool=adapter, step=step, round=round_no,
                  summary=_STEP_SUMMARY[step], text=prompt, live=True)
    started = time.time()
    result = ask_fn(adapter, prompt, worktree, expect_file=None, models=models,
                    live_path=str(cdir / sent["live"]))
    text = readable(result.output)
    record(cdir, "回复", src=role, dst="调度员", tool=adapter, step=step, round=round_no,
           ok=bool(result.ok), seconds=round(time.time() - started), reply_to=sent["seq"],
           summary=reply_summary(step, text, result.ok), text=text)
    return result


def _escalate(collab: Path, cdir: Path, card: Card, reason: str) -> None:
    """转需Claude介入，并在流转记录里写明原因。"""
    record(cdir, "转人工", src="程序", dst="调度员", summary=reason)
    card.transition("需Claude介入")
    _save(collab, card)


def _worker_failed(collab: Path, cdir: Path, card: Card, role: str, adapter: str, result):
    """干活的人没完成（报错、超时、被看门人停掉）：把原因写进返工记录，再转人工。"""
    reason = (readable(result.output) or "没有返回任何内容").strip().splitlines()
    tail = reason[-1][:300] if reason else "没有返回任何内容"
    _append(cdir / "返工记录.md",
            f"- {datetime.now():%Y-%m-%d %H:%M} {role}（{adapter}）没有完成：{tail}\n")
    _escalate(collab, cdir, card, f"{role}（{adapter}）没有完成：{tail}")
    return card, None


_VERDICT_SUMMARY = {PASS: "测试全部通过", FAIL: "测试没过", ENV_ERROR: "测试环境出错（不是代码的问题）",
                    TIMEOUT: "测试超时"}


def run_card(card: Card, collab_dir, worktree, test_command, dev_adapter, test_adapter,
             ask_fn=default_ask, judge_fn=default_judge, models=None,
             allowed=None, changed_fn=None, page_command=None, reviewer_adapter=None,
             revert_fn=None):
    collab = Path(collab_dir)
    cdir = _card_dir(collab, card)
    cdir.mkdir(parents=True, exist_ok=True)
    needs = cdir / "需求卡.md"
    changed_fn = changed_fn or _changed_list
    revert_fn = revert_fn or revert_paths

    if card.state != "开发中":
        if card.state == "待测试":
            card.state = "开发中"
        else:
            card.transition("开发中")
    _save(collab, card)

    past = read_flow(cdir)
    test_done = any(e.get("kind") == "回复" and e.get("src") == "测试员" and e.get("ok") is True for e in past)
    plan_hint = _plan_hint(cdir)

    # 复查不通过后接着跑：看复查结论，不看最新转人工原因；之后没有成功开发员回复才强制重改
    force_after_review_reject, review_notes = _review_reject_pending(cdir, past)

    # 第一步：测试员按验收标准先写测试（测试先行；如果断点续跑前已经写完交活，直接跳过）
    if not test_done:
        before_tester = set(_remember_before_tester(cdir, worktree))
        prep = _dispatch(ask_fn, cdir, "测试员", test_adapter, "写测试",
                         f"{_READ_RULES}按任务卡 {needs} 的验收标准编写自动化测试，放进项目的测试目录。"
                         f"只写测试，不改产品代码。{plan_hint}",
                         worktree, models, 1)
        # 用真实改动列表记测试文件（不走注入的 changed_fn；中途失败也先快照，接着跑才不会当越界删掉）
        added = _expand_to_files(worktree, [p for p in _changed_list(worktree) if p not in before_tester])
        if added:
            _save_test_files(cdir, added)
            _snapshot_test_files(cdir, worktree, added)
        if not prep.ok:
            return _worker_failed(collab, cdir, card, "测试员", test_adapter, prep)
        _log_step(cdir, "写测试", test_adapter, worktree)

    tester_files = _load_test_files(cdir)
    effective_allowed = list(allowed) + tester_files if allowed is not None else None

    # 第二步：开发员照着测试写代码，直到测试通过
    dev_prompt = (f"{_READ_RULES}按任务卡实现功能：{needs}。测试已经写好，请让它们全部通过。"
                  f"{_DEV_TEST_NOTE}{plan_hint}{_REPORT_ASK}")
    last_output = None  # 上一轮测试的报错，用来发现“原地打转”
    past_fails = [e for e in past if e.get("kind") in ("判定", "页面检查") and e.get("result") == FAIL]
    if past_fails and card.rework > 0:
        last_output = text_of(cdir, past_fails[-1])
        dev_prompt = (f"{_READ_RULES}上一版测试没过，测试输出如下，请修复后重新提交。\n\n{last_output}\n\n"
                      f"任务卡：{needs}。{_DEV_TEST_NOTE}{plan_hint}{_REPORT_ASK}")
    if force_after_review_reject:
        notes = review_notes.strip() or "（复查员没有写下详细意见）"
        dev_prompt = (f"{_READ_RULES}上一次复查不通过。请按复查意见修改后再提交，不算一次返工。\n\n"
                      f"{notes}\n\n任务卡：{needs}。{_DEV_TEST_NOTE}{plan_hint}{_REPORT_ASK}")

    first_iter = True
    while True:
        rnd = card.rework + 1
        dev_already_done = False
        if first_iter:
            first_iter = False
            if force_after_review_reject:
                force_after_review_reject = False
                # 强制开发员带着复查意见重做；不走“已做过就跳过”
            else:
                dev_replies = [i for i, e in enumerate(past)
                               if e.get("kind") == "回复" and e.get("src") == "开发员"
                               and e.get("round") == rnd and e.get("ok") is True]
                if dev_replies:
                    after = past[dev_replies[-1] + 1:]
                    has_fail_after = any(e.get("kind") in ("判定", "页面检查") and e.get("result") != PASS
                                        for e in after)
                    if not has_fail_after:
                        # 复查没给结论时：跳过开发员，只重新复查
                        dev_already_done = True

        if not dev_already_done:
            # 复查打回重改：返工次数不增加，步骤记为“开发”
            if "上一次复查不通过" in dev_prompt or card.rework == 0:
                step_name = "开发"
            else:
                step_name = "返工修改"
            dev = _dispatch(ask_fn, cdir, "开发员", dev_adapter, step_name,
                            dev_prompt, worktree, models, rnd)
            if not dev.ok:
                return _worker_failed(collab, cdir, card, "开发员", dev_adapter, dev)
            _save_report(cdir, dev.output)
            _log_step(cdir, step_name, dev_adapter, worktree)

        if effective_allowed is not None:
            changed = changed_fn(worktree)
            bad = out_of_scope(changed, effective_allowed)
            if bad:
                # 越界改动由程序直接撤销（开发员在自动模式下不能执行命令，删不掉文件），不算返工
                revert_fn(worktree, bad)
                still = out_of_scope(changed_fn(worktree), effective_allowed)
                stamp = f"{datetime.now():%Y-%m-%d %H:%M}"
                if still:
                    _append(cdir / "返工记录.md",
                            f"- {stamp} 范围外的改动撤销失败：{'、'.join(still)}，转需Claude介入\n")
                    record(cdir, "范围", src="程序", ok=False,
                           summary=f"范围外的改动撤销失败：{'、'.join(still)}", text="\n".join(changed))
                    _escalate(collab, cdir, card, f"范围外的改动撤销失败：{'、'.join(still)}")
                    return card, None
                _append(cdir / "返工记录.md", f"- {stamp} 已自动撤销范围外的改动：{'、'.join(bad)}\n")
                record(cdir, "范围", src="程序", ok=True,
                       summary=f"已自动撤销范围外的改动：{'、'.join(bad)}", text="\n".join(changed))
            else:
                record(cdir, "范围", src="程序", ok=True,
                       summary=f"改动范围正常（{len(changed)} 个文件）", text="\n".join(changed))

        restored = _restore_test_snapshot(cdir, worktree)
        if restored:
            record(cdir, "范围", src="程序", ok=True,
                   summary=f"已恢复开发员改动的测试文件：{'、'.join(restored)}",
                   text="\n".join(restored))

        card.transition("待测试")
        card.transition("测试中")
        _save(collab, card)
        verdict = judge_fn(test_command, cwd=worktree, timeout=_TEST_TIMEOUT)
        _append(cdir / "测试结果.md",
                f"\n## 第 {card.rework + 1} 轮判定\n结论：{verdict.result}\n"
                f"退出码：{verdict.returncode}\n\n{verdict.output}\n")
        record(cdir, "判定", src="程序", dst="" if verdict.result == PASS else "开发员",
               result=verdict.result, round=card.rework + 1, returncode=verdict.returncode,
               summary=f"第 {card.rework + 1} 轮：{_VERDICT_SUMMARY.get(verdict.result, verdict.result)}"
                       + ("" if verdict.result == PASS else f"（退出码 {verdict.returncode}）"),
               text=verdict.output)

        if verdict.result == PASS and page_command:
            # 真实页面检查：由项目自己定义的检查命令判断，过了才算待验收
            page = judge_fn(page_command, cwd=worktree, timeout=_TEST_TIMEOUT)
            _append(cdir / "页面检查结果.md",
                    f"\n## 第 {card.rework + 1} 轮页面检查\n结论：{page.result}\n\n{page.output}\n")
            record(cdir, "页面检查", src="程序", dst="" if page.result == PASS else "开发员",
                   result=page.result, round=card.rework + 1,
                   summary=f"页面检查：{page.result}", text=page.output)
            verdict = page

        if verdict.result == PASS and reviewer_adapter:
            # 独立复查：由另一个 AI 只读检查改动是否符合需求，写“结论：通过/不通过”
            review = _dispatch(ask_fn, cdir, "复查员", reviewer_adapter, "复查",
                               f"{_READ_RULES}只读复查：对照任务卡 {needs} 检查本次改动是否符合需求和验收标准。"
                               f"{plan_hint}"
                               f"第一行写“结论：通过”或“结论：不通过”，之后列出问题。不要修改任何文件。",
                               worktree, models, card.rework + 1)
            review_text = readable(review.output)
            _append(cdir / "复查结果.md", f"\n## 复查（{reviewer_adapter}）\n{review_text}\n")
            verdict_kind = review_verdict(review_text)
            if not review.ok or verdict_kind == "unclear":
                _escalate(collab, cdir, card,
                          f"复查员（{reviewer_adapter}）没有给出结论，不能算通过")
                return card, verdict
            if verdict_kind == "reject":
                _escalate(collab, cdir, card,
                          f"复查员（{reviewer_adapter}）复查不通过，需要调度员看原因")
                return card, verdict

        if verdict.result == PASS:
            card.transition("待验收")
            _save(collab, card)
            record(cdir, "结束", src="程序", dst="你", state="待验收",
                   summary=f"做完了，等你验收（返工 {card.rework} 次）")
            return card, verdict

        if verdict.result in (ENV_ERROR, TIMEOUT):
            # 环境或超时问题不是代码的错，不计返工轮数，直接叫 Claude
            _escalate(collab, cdir, card, f"{_VERDICT_SUMMARY[verdict.result]}，需要调度员处理")
            return card, verdict

        if last_output is not None and verdict.output.strip() == last_output.strip():
            # 同样的报错连续出现两次：说明在原地打转，再返工也是白费，立刻转人工
            _append(cdir / "返工记录.md",
                    f"- {datetime.now():%Y-%m-%d %H:%M} 同样的报错连续出现两次，判定为原地打转，转需Claude介入\n")
            _escalate(collab, cdir, card, "同样的报错连续出现两次，判定为原地打转")
            return card, verdict
        last_output = verdict.output

        escalate = card.on_test_failed()  # 没过：返工次数 +1，或转需Claude介入
        stamp = f"{datetime.now():%Y-%m-%d %H:%M}"
        if escalate:
            _append(cdir / "返工记录.md",
                    f"- {stamp} 测试仍没过（退出码 {verdict.returncode}），返工 {card.rework} 次已用完，转需Claude介入\n")
        else:
            _append(cdir / "返工记录.md",
                    f"- {stamp} 第 {card.rework} 次返工：测试没过（退出码 {verdict.returncode}）\n")
        _save(collab, card)
        if escalate:
            # on_test_failed 已经把状态转成“需Claude介入”，这里只补记原因
            record(cdir, "转人工", src="程序", dst="调度员",
                   summary=f"返工 {card.rework} 次后测试仍没过，需要调度员处理")
            return card, verdict
        dev_prompt = (f"{_READ_RULES}上一版测试没过，测试输出如下，请修复后重新提交。\n\n{verdict.output}\n\n"
                      f"任务卡：{needs}。{_DEV_TEST_NOTE}{plan_hint}{_REPORT_ASK}")
