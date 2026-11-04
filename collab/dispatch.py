"""派发主流程（测试先行）：
先由测试员按验收标准写测试 → 开发员写代码让测试通过 → 程序按退出码判定。
没过则退回开发（带测试原文），满 3 轮转"需Claude介入"。

所有外部调用（agent 执行、测试判定）都通过参数注入，便于用假实现测试。
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from collab.agents import ask as default_ask
from collab.judge import ENV_ERROR, PASS, TIMEOUT, judge as default_judge
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


def _worker_failed(collab: Path, cdir: Path, card: Card, role: str, adapter: str, result):
    """干活的人没完成（报错、超时、被看门人停掉）：把原因写进返工记录，再转人工。"""
    reason = (readable(result.output) or "没有返回任何内容").strip().splitlines()
    tail = reason[-1][:300] if reason else "没有返回任何内容"
    _append(cdir / "返工记录.md",
            f"- {datetime.now():%Y-%m-%d %H:%M} {role}（{adapter}）没有完成：{tail}\n")
    card.transition("需Claude介入")
    _save(collab, card)
    return card, None


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

    card.transition("开发中")
    _save(collab, card)

    # 第一步：测试员按验收标准先写测试（测试先行）
    prep = ask_fn(test_adapter,
                  f"{_READ_RULES}按任务卡 {needs} 的验收标准编写自动化测试，放进项目的测试目录。只写测试，不改产品代码。",
                  worktree, expect_file=None, models=models)
    if not prep.ok:
        return _worker_failed(collab, cdir, card, "测试员", test_adapter, prep)
    _log_step(cdir, "写测试", test_adapter, worktree)

    # 第二步：开发员照着测试写代码，直到测试通过
    dev_prompt = (f"{_READ_RULES}按任务卡实现功能：{needs}。测试已经写好，请让它们全部通过。"
                  f"{_REPORT_ASK}")
    last_output = None  # 上一轮测试的报错，用来发现“原地打转”
    while True:
        dev = ask_fn(dev_adapter, dev_prompt, worktree, expect_file=None, models=models)
        if not dev.ok:
            return _worker_failed(collab, cdir, card, "开发员", dev_adapter, dev)
        _save_report(cdir, dev.output)
        _log_step(cdir, "开发" if card.rework == 0 else "返工修改", dev_adapter, worktree)

        if allowed is not None:
            bad = out_of_scope(changed_fn(worktree), allowed)
            if bad:
                # 越界改动由程序直接撤销（开发员在自动模式下不能执行命令，删不掉文件），不算返工
                revert_fn(worktree, bad)
                still = out_of_scope(changed_fn(worktree), allowed)
                stamp = f"{datetime.now():%Y-%m-%d %H:%M}"
                if still:
                    _append(cdir / "返工记录.md",
                            f"- {stamp} 范围外的改动撤销失败：{'、'.join(still)}，转需Claude介入\n")
                    card.transition("需Claude介入")
                    _save(collab, card)
                    return card, None
                _append(cdir / "返工记录.md", f"- {stamp} 已自动撤销范围外的改动：{'、'.join(bad)}\n")

        card.transition("待测试")
        card.transition("测试中")
        _save(collab, card)
        verdict = judge_fn(test_command, cwd=worktree, timeout=_TEST_TIMEOUT)
        _append(cdir / "测试结果.md",
                f"\n## 第 {card.rework + 1} 轮判定\n结论：{verdict.result}\n"
                f"退出码：{verdict.returncode}\n\n{verdict.output}\n")

        if verdict.result == PASS and page_command:
            # 真实页面检查：由项目自己定义的检查命令判断，过了才算待验收
            page = judge_fn(page_command, cwd=worktree, timeout=_TEST_TIMEOUT)
            _append(cdir / "页面检查结果.md",
                    f"\n## 第 {card.rework + 1} 轮页面检查\n结论：{page.result}\n\n{page.output}\n")
            verdict = page

        if verdict.result == PASS and reviewer_adapter:
            # 独立复查：由另一个 AI 只读检查改动是否符合需求，写“结论：通过/不通过”
            review = ask_fn(reviewer_adapter,
                            f"{_READ_RULES}只读复查：对照任务卡 {needs} 检查本次改动是否符合需求和验收标准。"
                            f"第一行写“结论：通过”或“结论：不通过”，之后列出问题。不要修改任何文件。",
                            worktree, expect_file=None, models=models)
            review_text = readable(review.output)
            _append(cdir / "复查结果.md", f"\n## 复查（{reviewer_adapter}）\n{review_text}\n")
            if "结论：不通过" in review_text:
                card.transition("需Claude介入")
                _save(collab, card)
                return card, verdict

        if verdict.result == PASS:
            card.transition("待验收")
            _save(collab, card)
            return card, verdict

        if verdict.result in (ENV_ERROR, TIMEOUT):
            # 环境或超时问题不是代码的错，不计返工轮数，直接叫 Claude
            card.transition("需Claude介入")
            _save(collab, card)
            return card, verdict

        if last_output is not None and verdict.output.strip() == last_output.strip():
            # 同样的报错连续出现两次：说明在原地打转，再返工也是白费，立刻转人工
            _append(cdir / "返工记录.md",
                    f"- {datetime.now():%Y-%m-%d %H:%M} 同样的报错连续出现两次，判定为原地打转，转需Claude介入\n")
            card.transition("需Claude介入")
            _save(collab, card)
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
            return card, verdict
        dev_prompt = (f"{_READ_RULES}上一版测试没过，测试输出如下，请修复后重新提交。\n\n{verdict.output}\n\n"
                      f"任务卡：{needs}。{_REPORT_ASK}")
