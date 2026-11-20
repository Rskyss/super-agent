"""派发主流程（测试先行）：
先由测试员按验收标准写测试 → 开发员写代码让测试通过 → 程序按退出码判定。
没过则退回开发（带测试原文），满 3 轮转"需Claude介入"。

所有外部调用（agent 执行、测试判定）都通过参数注入，便于用假实现测试。
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import shlex
import shutil
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from collab.agents import QUIET_LIMIT, agy_error, ask as default_ask, readable
from collab.card_attachments import attachment_prompt
from collab.flow import (TEST_PROBLEM, _numbered_items, plain_issues, read_flow, record, reply_summary, rework_words,
                         text_of)
from collab.judge import Verdict, ENV_ERROR, FAIL, PASS, TIMEOUT, judge as default_judge
from collab.scope import _is_secret, is_cache, out_of_scope
from collab.state import Card
from collab.token_usage import usage_for
from collab.vcs import revert_paths
from collab.workspace import render_board
# 给模型的规则在仓库最外层的 agents/ 里，按角色原样放在派活的话最前面（规则包，用户 11-20 批准；原来只叫模型自己去读 collab/规则.md）
from collab.rulebook import rules_for as _rules

_TEST_TIMEOUT = 900  # 测试 15 分钟（D5）


def _card_dir(collab: Path, card: Card) -> Path:
    return collab / "cards" / card.card_id


def _save(collab: Path, card: Card) -> None:
    card.save(_card_dir(collab, card) / "状态.json")
    render_board(collab)


def _append(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)


def uncommitted_count(project) -> int:
    """项目里还没提交的文件数；协作包目录不算。不是 git 或读失败返回 0。"""
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=off", "status", "--porcelain"],
            cwd=str(project), capture_output=True, text=True, check=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return 0
    count = 0
    for line in proc.stdout.splitlines():
        if not line.strip() or len(line) < 4:
            continue
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[-1].strip().strip('"')
        if path == ".collab" or path.startswith(".collab/"):
            continue
        count += 1
    return count


def save_paths(project, card_id: str = "") -> tuple:
    """开工前要存的文件，以及它们改名前的旧路径（改名时旧名字的删除也要进同一次提交）。

    文档默认不算，本卡引用的施工单例外；密钥配置一律不算，免得存进 Git 历史。
    只读 Git 状态，最多等五秒；不是 Git 项目或读失败就返回两个空清单。
    """
    try:
        proc = subprocess.run(
            ["git", "-c", "core.quotepath=off", "status", "--porcelain", "--untracked-files=all"],
            cwd=str(project), capture_output=True, text=True, check=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return [], []

    orders = set()
    if card_id:
        try:
            card_dir = Path(project) / ".collab" / "cards" / str(card_id)
            need = (card_dir / "需求卡.md").read_text(encoding="utf-8")
            from collab.planner import existing_orders
            orders = set(existing_orders(project, need))
        except (OSError, ValueError):
            orders = set()

    found, removed = set(), set()
    for line in proc.stdout.splitlines():
        if not line.strip() or len(line) < 4:
            continue
        path = line[3:]
        old = ""
        if ("R" in line[:2] or "C" in line[:2]) and " -> " in path:
            old, path = path.split(" -> ", 1)
        if path.startswith('"') and path.endswith('"'):
            path = json.loads(path)
        if not path or path == ".collab" or path.startswith(".collab/"):
            continue
        if (path.startswith("docs/") or path.lower().endswith(".md")) and path not in orders:
            continue
        if _is_secret(path):
            continue
        found.add(path)
        if old and "R" in line[:2]:
            if old.startswith('"') and old.endswith('"'):
                old = json.loads(old)
            if old and not _is_secret(old):
                removed.add(old)
    return sorted(found), sorted(removed - found)


def files_to_save(project, card_id: str = "") -> list:
    """项目里还没提交、开工时施工方会看不到的文件；文档默认不算，本卡引用的施工单例外。

    只读 Git 状态，最多等五秒；不是 Git 项目或读失败就返回空清单。
    """
    return save_paths(project, card_id)[0]


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
        proc = subprocess.run(["git", "status", "--porcelain", "-uall", "-z"],
                              cwd=worktree, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return []
    found, items, index = [], proc.stdout.split("\0"), 0
    while index < len(items):
        item = items[index]
        index += 1
        if len(item) < 4:
            continue
        found.append(item[3:])
        if "R" in item[:2] or "C" in item[:2]:
            old_name = items[index] if index < len(items) else ""
            index += 1  # 改名和复制时，下一段是旧名字
            if "R" in item[:2] and old_name:
                found.append(old_name)  # 改名等于动了旧文件，也要过范围检查
    return found


def reply_text(result) -> str:
    """干活的人最后那条回复；旧的假实现没有 final 时从完整输出里取。
    没做成又没回话时，补上 agy 自己报的原因（㉙ 复查第 2 轮：模型名不对、额度不够不能只说“没有返回任何内容”）。"""
    text = getattr(result, "final", "") or readable(result.output)
    if not text.strip() and not getattr(result, "ok", True):
        text = agy_error(result.output)
    return text.strip()


def _command_text(test_command) -> str:
    if isinstance(test_command, (list, tuple)):
        return str(test_command[-1]) if test_command else ""
    return str(test_command or "")


def _run_hint(test_command) -> str:
    cmd = _command_text(test_command).strip()
    if not cmd:
        return ""
    return f"本卡的测试命令：{cmd}。要确认就只跑这一条，不要跑全部测试（全部测试留到最后验收）。"


_REPORT_ASK = ("完成后，在回复的最后一行写“汇报：”加不超过100字的说明（改了什么、测试情况），"
               "用大白话，不写文件名、函数名和命令。不要另建汇报文件。")
_TEST_FILES = "测试文件.json"
_BEFORE_TESTER = "测试前改动.json"
_TEST_SNAPSHOT = "测试快照"
_DEV_TEST_NOTE = "测试文件由测试员负责，不要修改；改了也会在判定前被恢复。"


HANDOVER = "交接资料.md"


def _plan_hint(cdir: Path, with_notes: bool = True) -> str:
    plan = cdir / "方案.md"
    if plan.is_file():
        hint = f"请严格按方案文件 {plan} 执行，尤其遵守其中「明确不做什么」。方案里如果写着只输出方案、不修改文件，那只约束写方案这一步，不约束你。"
    else:
        hint = "请严格按任务卡执行，尤其遵守其中「明确不做什么」。"
    notes = cdir / HANDOVER
    if with_notes and notes.is_file():  # 调度员备好的“改哪里、现在怎么写”（第二轮 ㉚）：先读它；返工轮不带（第三批 ㊾）
        hint += (f"开工前先读交接资料 {notes}（调度员整理的要改的位置、现在的写法、相关规矩），"
                 f"照着直接去看那几处，不用从头找；资料和代码对不上时以代码为准。")
    return hint


def _handover_prompt(needs, cdir: Path) -> str:
    plan = cdir / "方案.md"
    return (f"{_rules('备交接资料')}你是调度员，给写代码的准备交接资料。读任务卡 {needs}"
            f"{f'、方案 {plan}' if plan.is_file() else ''}（施工单路径写在里面的也要读），再读要改的代码。"
            f"只读，不改任何文件。把交接资料作为你最后的回复，照这个格式：\n"
            f"## 要改的文件\n每个文件一节：要改哪个函数、大概第几行；现在是怎么写的（只摘最关键的几行）；要改成什么样。\n"
            f"## 可以照抄的写法\n项目里已有的同类写法在哪。\n"
            f"## 相关规矩\n和这次改动有关的约定、不能动的东西。\n"
            f"## 会跟着变的老测试\n哪些已有测试会受影响（没有就写没有）。\n"
            f"不要贴整份文件，不要写测试怎么写，总长不超过 6000 字。")


def _tokens_for(adapter, output, live=None):
    """按工具算用量；完整输出优先，读不到再试实时日志；不支持的工具返回 None，出错也只留一条读不到。"""
    try:
        got = usage_for(adapter, output or "", live=live)
    except Exception as exc:  # noqa: BLE001 — 算用量不能挡住干活
        return {"ok": False, "reason": f"算用量时出问题（{exc}）"}
    if isinstance(got, dict) and got.get("pending"):
        return {"ok": False, "reason": "这一轮结束了还没有出数"}
    return got


def _make_handover(ask_fn, cdir: Path, adapter: str, models: dict, needs, worktree) -> None:
    """调度员只读备交接资料；没备成不挡流程，只记一笔。"""
    started = time.time()
    try:
        extra = {"watch_commands": True} if _accepts(ask_fn, "watch_commands") else {}
        result = ask_fn(adapter, _handover_prompt(needs, cdir) + _NO_PLUGINS_NOTE, worktree,
                        models=dict(models or {}, codex_sandbox="read-only", no_plugins=True), **extra)
    except Exception as exc:  # noqa: BLE001 — 备资料出错不能停掉施工
        result = None
        why = str(exc)
    text = (getattr(result, "final", "") or "").strip() if result is not None and result.ok else ""
    if result is None:
        tokens = _tokens_for(adapter, "")
        if tokens is not None:
            tokens = {"ok": False, "reason": "这次交接没有返回结果，读不到用量"}
    else:
        tokens = _tokens_for(adapter, getattr(result, "output", "") or "")
    if text:
        (cdir / HANDOVER).write_text(text + "\n", encoding="utf-8")
        record(cdir, "交接资料", src="调度员", dst="开发员", ok=True, seconds=round(time.time() - started),
               summary=f"程序备好了交接资料（{len(text)} 字），写代码、复查都会先读它",
               **({"tokens": tokens} if tokens is not None else {}))
    else:
        why = readable(result.output, adapter)[:200] if result is not None else why
        record(cdir, "交接资料", src="调度员", dst="开发员", ok=False, seconds=round(time.time() - started),
               summary="交接资料没备成，写代码的照旧自己找", text=why or None,
               **({"tokens": tokens} if tokens is not None else {}))


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


def _file_digest(path: Path):
    if not path.is_file() or path.is_symlink():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_before_map(cdir: Path) -> dict:
    """路径 → 内容摘要。旧的列表格式没有摘要，读出来当作未知。"""
    path = cdir / _BEFORE_TESTER
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if isinstance(data, list):
        return {str(p): None for p in data if str(p).strip()}
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items() if str(k).strip()}
    return {}


def _save_before_map(cdir: Path, worktree) -> dict:
    root = Path(worktree)
    before = {}
    for rel in _expand_to_files(worktree, [p for p in _changed_list(worktree) if not is_cache(p)]):
        before[rel] = _file_digest(root / rel)
    (cdir / _BEFORE_TESTER).write_text(json.dumps(before, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return before


def _remember_before_tester(cdir: Path, worktree) -> dict:
    """第一次派测试员前记下每个文件的内容摘要和内容备份；已有记录不覆盖。"""
    if (cdir / _BEFORE_TESTER).is_file():
        return _load_before_map(cdir)
    before = _save_before_map(cdir, worktree)
    _backup_before_tester(cdir, worktree, before)
    return before


_TEST_BACKUP = "测试前备份"
_TEST_DIRS = {"tests", "test", "__tests__", "spec", "specs", "e2e", "cypress", "playwright", "integration"}
_TEST_NAME = re.compile(r"(?:test_.+\.py|.+_test\.\w+|.+\.test\.\w+|.+\.spec\.\w+|.+_spec\.\w+|.+_check\.js)$")


def is_test_path(rel: str) -> bool:
    """一看就是测试的文件：在测试目录里，或名字是测试的写法。写测试的改了原有的非测试文件会被撤掉。"""
    parts = [p for p in str(rel).replace("\\", "/").split("/") if p]
    if not parts:
        return False
    return any(p in _TEST_DIRS for p in parts[:-1]) or bool(_TEST_NAME.match(parts[-1]))


def _in_product_folder(worktree, rel: str) -> bool:
    """新文件放在一个本来就有被管理文件的文件夹里（比如 src/），算产品代码，不算测试。"""
    folder = Path(rel).parent
    while str(folder) not in ("", "."):  # 往上找：src/newpkg/x.py 的 src/ 里有产品代码也算（复查第 2 轮）
        out = subprocess.run(["git", "ls-files", "--", str(folder)], cwd=str(worktree), capture_output=True,
                             text=True).stdout
        if out.strip():
            return True
        folder = folder.parent
    return False


def _tracked(worktree, rel: str) -> bool:
    return subprocess.run(["git", "ls-files", "--error-unmatch", "--", rel], cwd=str(worktree),
                          capture_output=True).returncode == 0


def _backup_before_tester(cdir: Path, worktree, before: dict) -> None:
    """派测试员前把副本里已改动文件的内容存一份：它若越界改了开发员改过的文件，撤的时候还原到这一份。"""
    backup = cdir / _TEST_BACKUP
    shutil.rmtree(backup, ignore_errors=True)
    root = Path(worktree)
    for rel in before:
        src = root / rel
        if src.is_file() and not src.is_symlink():
            dest = backup / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)


def _undo_tester_changes(cdir: Path, worktree, paths: list, before: dict, revert_fn) -> None:
    """撤掉写测试的在测试以外的改动：派活前就改过的还原备份，派活前没改过的恢复成提交时的样子或删掉。"""
    root = Path(worktree)
    fresh_only = []
    for rel in paths:
        saved = cdir / _TEST_BACKUP / rel
        dest = root / rel
        if rel in before and saved.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, dest)
            subprocess.run(["git", "reset", "-q", "--", rel], cwd=str(root), capture_output=True)  # 它 git add 过也拿掉
        elif rel in before and before.get(rel) is None:
            # 派活前这个文件就是被删掉的（开发员删的），它又建回来：照旧删掉（流程修理复查第 1 轮）
            if dest.is_file() or dest.is_symlink():
                dest.unlink()
            subprocess.run(["git", "reset", "-q", "--", rel], cwd=str(root), capture_output=True)
        else:
            fresh_only.append(rel)
    if fresh_only:
        revert_fn(worktree, fresh_only)


def _after_revise(events: list) -> list:
    last = -1
    for ev in events:
        if ev.get("kind") == "追加要求":
            last = int(ev.get("seq") or 0)
    if last < 0:
        return events
    return [ev for ev in events if int(ev.get("seq") or 0) > last]


def _revise_words(cdir: Path, events: list) -> str:
    for ev in reversed(events):
        if ev.get("kind") == "追加要求":
            return text_of(cdir, ev) or str(ev.get("summary") or "")
    return ""


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
        if "node_modules" in rel.split("/"):  # 依赖不是这张卡的产物；预览借的还是软链接，写进去会改到真实依赖
            continue
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
    shutil.rmtree(snap, ignore_errors=True)
    for rel in files:
        src = root / rel
        if not src.is_file() or has_conflict_marks(src):
            continue
        dest = snap / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


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
        if "node_modules" in rel.split("/"):
            continue
        dest = root / rel
        data = path.read_bytes()
        try:
            same = dest.is_file() and not dest.is_symlink() and dest.read_bytes() == data
        except OSError:
            same = False
        if same:
            continue
        if dest.is_dir() and not dest.is_symlink():
            shutil.rmtree(dest, ignore_errors=True)
        elif dest.exists() or dest.is_symlink():
            dest.unlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        restored.append(rel)
    return restored


def _tester_files_hint(cdir: Path) -> str:
    """测试员写的测试是流程要求的，不在“允许改动的范围”里也不算越界（真实走查时被判过不通过）。"""
    files = _load_test_files(cdir)
    if not files:
        return ""
    return "这些是测试员按流程写的测试文件，不算越界改动：" + "、".join(files) + "。"


def _review_reject_pending(cdir: Path, past: list) -> tuple:
    """最近一次复查结论是不通过，且之后没有成功的开发员回复 → 必须带着意见重改。"""
    last_review = max((int(e.get("seq") or 0) for e in past
                       if e.get("kind") == "回复" and e.get("src") == "复查员"), default=0)
    if any(e.get("kind") == "转人工" and e.get("cause") == UNREADABLE and int(e.get("seq") or 0) > last_review
           for e in past):
        return False, ""   # 上次是复查看不懂停下的：接着跑重新复查，不把原话当退回意见交给写代码的（C1.8 复查第 1 轮严重 1）
    conclusion = _latest_review_conclusion(cdir)
    if conclusion is not None:
        if conclusion.get("result") != "退回":
            return False, ""
        reject_seq = int(conclusion.get("seq") or 0)
        for e in past:
            if int(e.get("seq") or 0) > reject_seq and e.get("kind") == "回复" \
                    and e.get("src") == "开发员" and e.get("ok") is True:
                return False, ""
        # 重写失败整段退回的复查结论没有正式清单：若还存着实际退回条目，必须和整段原文一起交回
        body = text_of(cdir, conclusion)
        back = _conclusion_back_problems(conclusion)
        notes = "\n\n".join(_problem_text(p) for p in back) if back else ""
        if body and not isinstance(conclusion.get("清单"), dict):
            return True, (f"{notes}\n\n{body}" if notes else body)
        if notes:
            return True, notes
        if body:
            return True, body
    reformat_seqs = {
        e.get("seq") for e in past
        if e.get("kind") == "派活" and e.get("dst") == "复查员"
        and "按固定格式重写" in (text_of(cdir, e) or "")
    }
    last_reject = None
    notes = ""
    closed = _closed_replies(cdir)
    kept = {e.get("reply"): e.get("back") for e in read_flow(cdir) if e.get("kind") == "以后再修" and e.get("back")}
    for e in reversed(past):
        if e.get("kind") != "回复" or e.get("src") != "复查员":
            continue
        if e.get("reply_to") in reformat_seqs:
            continue
        if e.get("seq") in closed:   # 收口算通过的那次（第四轮 57 复查）：接着跑不再退回
            return False, ""
        body = kept.get(e.get("seq")) or text_of(cdir, e)   # 只退回收口后留下的那几条
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


def _save_report(cdir: Path, text: str) -> None:
    """从开发员回复里取最后一段“汇报：……”，存进卡片文件夹。"""
    if "汇报：" not in text:
        return
    lines = text.rsplit("汇报：", 1)[1].strip().splitlines()
    summary = lines[0].strip() if lines else ""
    if summary:
        _append(cdir / "汇报.md", f"- {datetime.now():%Y-%m-%d %H:%M} {summary[:200]}\n")


def _log_step(cdir: Path, step: str, adapter: str, worktree) -> None:
    _append(cdir / "执行记录.md",
            f"- {datetime.now():%Y-%m-%d %H:%M} | {step} | 执行者：{adapter} | 累计改动：{_changed_files(worktree)}\n")


_STEP_SUMMARY = {
    "写测试": "按要求先写测试（只写测试，不改产品代码）",
    "重写测试": "测试一跑就全过，退回重写（只改测试）",
    "开发": "按测试写代码，让测试全部通过",
    "返工修改": "测试没过，带着测试输出退回修改",
    "复查": "只读复查：对照需求检查改动",
    "改测试": "复查说测试本身写错了：只改测试",
    "补测试": "复查退回：先补测试守住问题（只改测试）",
    "起草": "起草“继续改”：把几轮复查合起来找共同原因",
}

# 只认独立成行的结论：真实 Codex 的输出带着提示词，代码块和引用也不算。
# “通过”后面必须结束或接标点，“通过不了”“通过率”不算。认不出就转人工，不能错放
_REVIEW_VERDICT = re.compile(r"^[ \t*#]*结论\**[:：]\**[ \t]*\**(不通过|通过)\**[ \t]*(?:[。.!！；;，,]|$)", re.MULTILINE)
_CODE_BLOCK = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
# 只认明确的额度错误，并且只看输出末尾：前面可能是提示词或测试输出
_OUT_OF_CREDIT = re.compile(r"credits? balance|RESOURCE_EXHAUSTED|exceeded your (?:current )?quota|quota exceeded"
                            r"|insufficient_quota|usage limit|额度用完|额度不足", re.IGNORECASE)
_CREDIT_TAIL = 1500
CONFLICTS = "同步冲突.json"  # 同步项目新内容时有冲突的文件


_CLOSED_BLOCK = re.compile(r"```.*?```", re.DOTALL)


def review_verdict(text: str) -> str:
    """从复查员回复里读结论：pass / reject / unclear。"""
    found = _REVIEW_VERDICT.findall(_CODE_BLOCK.sub("", text or ""))
    if not found:
        # 没配对的代码块记号会把后面全吞掉（68b 复查第 4–6 轮“写了不通过却显示没给结论”）：
        # 只去掉配好对的代码块再找一次，但这样找到的只认“不通过”，绝不因此放行
        if "不通过" in _REVIEW_VERDICT.findall(_CLOSED_BLOCK.sub("", text or "")):
            return "reject"
        return "unclear"
    return "reject" if "不通过" in found else "pass"


_JSON_LIST_START = re.compile(r"^[ \t]*```json[ \t]*\r?$", re.IGNORECASE | re.MULTILINE)
_JSON_LIST_END = re.compile(r"^[ \t]*```[ \t]*\r?$", re.MULTILINE)
_BARE_PATH_SEG = r"(?:[A-Za-z0-9_.-]+(?: [A-Za-z0-9_.-]+)*|[\w.-]+)"
_BARE_LOCATION = re.compile(
    rf"((?:[A-Za-z]:[/\\]|/)?(?:{_BARE_PATH_SEG}[/\\])+{_BARE_PATH_SEG}\.[A-Za-z][A-Za-z0-9]{{0,7}}"
    rf"|[\w.-]+\.[A-Za-z][A-Za-z0-9]{{0,7}}):(\d+)"
)
_EVIDENCE_LOCATION = re.compile(
    rf"(?:(?:[A-Za-z]:[/\\]|/)?(?:{_BARE_PATH_SEG}[/\\])+{_BARE_PATH_SEG}|[\w./\\-]+)\.[A-Za-z][A-Za-z0-9]{{0,7}}:\d+"
)
_LIST_FIELDS = ("级别", "来源", "类型", "现象", "位置", "证据")
_LEVELS = ("严重", "中等", "轻微")
_SOURCES = ("这次改的", "原来就有的")
_KINDS = ("功能问题", "测试写错", "安全或丢数据")
_HITS = ("常见", "少见", "极巧合")
_CHECK_RESULTS = ("改好了", "没改好")


def _last_json_block(text: str):
    """回复里最后一个 ```json 代码块；开头和结束标记必须独立成行（不能把 ```text 等下一段开头当闭合）。"""
    src = text or ""
    starts = list(_JSON_LIST_START.finditer(src))
    if not starts:
        return None, "没有复查清单（末尾缺 json 代码块）"
    start = starts[-1]
    end_match = _JSON_LIST_END.search(src, start.end())
    if not end_match:
        return None, "最后一个 json 代码块没闭合"
    return src[start.end():end_match.start()].strip(), ""


def _parse_json_loose(body: str):
    """尽量解析末尾 json 块：先试标准 JSON，再清理常见尾随逗号后重试。"""
    try:
        return json.loads(body)
    except ValueError:
        cleaned = re.sub(r",(\s*[}\]])", r"\1", body)
        try:
            return json.loads(cleaned)
        except ValueError:
            return None


def _strip_problem_brackets(problem: dict) -> dict:
    """只把级别、来源、类型中的字符串去掉【】和首尾空白，其余字段与非字符串值保持原样。"""
    for field in ("级别", "来源", "类型"):
        value = problem.get(field)
        if isinstance(value, str):
            problem[field] = value.replace("【", "").replace("】", "").strip()
    return problem


def _raw_list_problems(text: str) -> tuple:
    """从格式不合格或未存正式清单的复查原文里，尽最大可能拆出末尾 json 块中的问题条目与严重标记。
    绝不用于自动放行，只用于在整段原文退回、停下提示和确认框里兜底，防止严重/测试写错问题被代码块剥离吞掉。"""
    src = text or ""
    body, _ = _last_json_block(src)
    if body is None:
        loose = re.findall(r"```json\s*(.*?)```", src, re.IGNORECASE | re.DOTALL)
        if loose:
            body = loose[-1].strip()
    if not body:
        return [], False
    data = _parse_json_loose(body)
    if isinstance(data, dict) and isinstance(data.get("问题"), list):
        return [_strip_problem_brackets(p) for p in data["问题"] if isinstance(p, dict)], False
    severe_in_code = bool(re.search(r"P[01]\b|严重|高危|安全|丢失|崩溃|测试写错|测试的问题", body))
    recovered = []
    if severe_in_code:
        matches = list(re.finditer(r'"现象"\s*:\s*"([^"]+)"', body))
        for idx, m in enumerate(matches):
            phen = m.group(1).strip()
            if not phen:
                continue
            start = body.rfind("{", 0, m.start())
            if start < 0 or (idx > 0 and start < matches[idx - 1].end()):
                start = matches[idx - 1].end() if idx > 0 else 0
            next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
            end = body.find("}", m.end(), next_start)
            chunk = body[start:(end + 1 if end >= 0 else next_start)]
            entry = {"来源": "这次改的", "类型": "功能问题", "现象": phen}
            for key in ("级别", "来源", "类型", "碰上", "位置"):
                km = re.search(rf'"{key}"\s*:\s*"([^"]+)"', chunk)
                if km and km.group(1).strip():
                    entry[key] = km.group(1).strip()
            if "级别" not in entry:
                entry["级别"] = "严重"
            recovered.append(entry)
    return [_strip_problem_brackets(p) for p in recovered], severe_in_code


def read_review_list(text: str, prev_count: int = None) -> tuple:
    """只认回复末尾的复查清单：返回（清单, 不合格原因列表）。不从正文里猜。"""
    body, why = _last_json_block(text)
    if body is None:
        return {}, [why]
    try:
        data = json.loads(body)
    except ValueError as exc:
        return {}, [f"最后一个 json 代码块解析不了：{exc}"]
    if not isinstance(data, dict):
        return data, ["复查清单最外层必须是对象"]
    errors = []
    verdict = data.get("结论")
    if verdict not in ("通过", "不通过"):
        errors.append("“结论”只能是“通过”或“不通过”")
    problems = data.get("问题")
    if not isinstance(problems, list):
        errors.append("“问题”必须是列表")
        problems = []
    else:
        for problem in problems:
            if isinstance(problem, dict):
                _strip_problem_brackets(problem)
    checks = data.get("上轮核对")
    has_unfixed = False
    if "上轮核对" in data:
        if not isinstance(checks, list):
            errors.append("“上轮核对”必须是列表")
        else:
            if prev_count is not None and prev_count == 0 and checks:
                errors.append("本轮没有上轮退回的问题，不能填写“上轮核对”")
            for ci, item in enumerate(checks, 1):
                if not isinstance(item, dict):
                    errors.append(f"上轮核对第 {ci} 项必须是对象")
                    continue
                num = item.get("编号")
                if not isinstance(num, int) or isinstance(num, bool):
                    errors.append(f"上轮核对第 {ci} 项的“编号”必须是数字")
                elif num < 1:
                    errors.append(f"上轮核对第 {ci} 项的“编号”必须大于 0")
                elif prev_count is not None and prev_count > 0 and num > prev_count:
                    errors.append(f"上轮退回的问题只有 {prev_count} 条，没有第 {num} 条")
                ans = item.get("结果")
                if ans not in _CHECK_RESULTS:
                    errors.append(f"上轮核对第 {ci} 项的“结果”只能是改好了、没改好")
                elif ans == "没改好":
                    has_unfixed = True
    if verdict == "通过" and has_unfixed:
        errors.append("清单写的是“通过”，但“上轮核对”里还有“没改好”，两处对不上")
    if verdict == "不通过" and not problems and not has_unfixed:
        errors.append("结论是不通过，却一条问题都没列")
    for index, problem in enumerate(problems, 1):
        if not isinstance(problem, dict):
            errors.append(f"第 {index} 条问题必须是对象")
            continue
        for field in _LIST_FIELDS:
            value = problem.get(field)
            if value is None or not str(value).strip():
                errors.append(f"第 {index} 条问题缺“{field}”")
        level = problem.get("级别")
        if level not in _LEVELS:
            errors.append(f"第 {index} 条问题的级别只能是严重、中等、轻微")
        source = problem.get("来源")
        if source not in _SOURCES:
            errors.append(f"第 {index} 条问题的来源只能是这次改的、原来就有的")
        kind = problem.get("类型")
        if kind not in _KINDS:
            errors.append(f"第 {index} 条问题的类型只能是功能问题、测试写错、安全或丢数据")
        if "碰上" in problem and problem.get("碰上") not in _HITS:
            errors.append(f"第 {index} 条问题的“碰上”只能是常见、少见、极巧合")
        proof = problem.get("证据")
        if not isinstance(proof, dict):
            errors.append(f"第 {index} 条问题的证据必须是对象")
            continue
        how = proof.get("方式")
        if how not in ("实跑", "推演"):
            errors.append(f"第 {index} 条问题的证据方式只能是实跑、推演")
        elif how == "实跑":
            if not str(proof.get("命令") or "").strip():
                errors.append(f"第 {index} 条问题的实跑证据缺命令")
            if not str(proof.get("输出") or "").strip():
                errors.append(f"第 {index} 条问题的实跑证据缺输出")
        elif not _EVIDENCE_LOCATION.search(str(proof.get("对照") or "")):
            errors.append(f"第 {index} 条问题的推演证据要写带扩展名的文件:行号")
    return data, errors


_SKIP_PATTERN = re.compile(
    r"@(?:unittest\.)?skip(?:If|Unless)?\b|skipTest\s*\(|\bpytest\.mark\.skip(?:if)?\b|\bpytest\.skip\s*\("
    r"|\b(?:it|test|describe)\.skip\s*(?:\(|\.|`)|^\s*x(?:it|test|describe)\s*(?:\(|\.)",
    re.MULTILINE,
)


def _new_skips_note(worktree) -> str:
    """已跟踪测试里新加的行、没提交的新测试文件里，命中的跳过写法（带文件、行号、原文）。"""
    root = Path(worktree)
    found = []
    try:
        out = subprocess.run(["git", "-c", "core.quotepath=off", "diff", "-U0", "--no-color", "HEAD"],
                             cwd=str(root), capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
        rel, new_line, in_file = "", 0, False
        for line in out.stdout.splitlines():
            if line.startswith("+++ b/"):
                rel = line[6:].strip().strip('"')
                in_file = is_test_path(rel)
                continue
            if line.startswith("+++ ") or line.startswith("--- "):
                continue
            match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if match:
                new_line = int(match.group(1))
                continue
            if not in_file:
                continue
            if line.startswith("+"):
                content = line[1:]
                if _SKIP_PATTERN.search(content):
                    found.append(f"{rel}:{new_line} {content.strip()}")
                new_line += 1
            elif line.startswith("-"):
                continue
            elif line.strip():
                new_line += 1
    except (subprocess.SubprocessError, OSError):
        pass
    try:
        out = subprocess.run(["git", "-c", "core.quotepath=off", "ls-files", "-o", "--exclude-standard", "-z"],
                             cwd=str(root), capture_output=True, text=True, encoding="utf-8",
                             errors="replace")
        for rel in [p for p in out.stdout.split("\0") if p and is_test_path(p)]:
            path = root / rel
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, content in enumerate(lines, 1):
                if _SKIP_PATTERN.search(content):
                    found.append(f"{rel}:{number} {content.strip()}")
    except (subprocess.SubprocessError, OSError):
        pass
    if not found:
        return ""
    unique = list(dict.fromkeys(found))
    return ("注意：这些测试被加了跳过，逐个判断合不合理；不合理就判不通过：\n"
            + "\n".join(f"- {line}" for line in unique))


def _unreachable(problem: dict) -> bool:
    """复查新规矩（方案-第二批第七节，用户 11-19 批）：碰不上的不退回。口径写在 agents/手册/复查分级.md。
    标“极巧合”的一律不退回，不管实跑还是推演（2b，用户 11-20 定：CARD-019 复查员故意拖慢加载复现的
    加载空档问题，原来因为是实跑就照级别退回）；
    只能推演、标“少见”的（本项目会发生、要特定操作）只有不严重的才不退回——严重的照退，不丢质量（Codex 复查第 2 条）。"""
    hit = problem.get("碰上") or "常见"
    if hit == "极巧合":
        return True
    proof = problem.get("证据")
    how = proof.get("方式") if isinstance(proof, dict) else "推演"
    return how != "实跑" and hit == "少见" and problem.get("级别") != "严重"


def _needs_return(problem: dict, first_round: bool = True) -> bool:
    if problem.get("类型") in ("测试写错", "安全或丢数据"):
        return True   # 测试本身写错、安全或丢数据：哪怕少见也退回
    if _unreachable(problem):
        return False  # 碰不上的记以后再修，不退回
    if problem.get("级别") == "严重":
        return True
    if not first_round:
        return False
    hit = problem.get("碰上") or "常见"
    return problem.get("来源") != "原来就有的" and hit != "极巧合"


def _problem_text(problem: dict) -> str:
    """清单里的一条翻成交代用的文字：开头保留级别、来源、测试问题标签，后面带位置和证据。"""
    tags = f"【{problem.get('级别')}】【{problem.get('来源')}】"
    if problem.get("类型") == "测试写错":
        tags += TEST_PROBLEM
    proof = problem.get("证据")
    if proof is None or proof == "":
        proof = {}
    elif not isinstance(proof, dict):  # 残缺清单里证据写成一句话（或 0、false 这类值）：照推演的样子带上原话，不能在派活前出错（C1.6 第 3 轮复查中等）
        proof = {"方式": "推演", "对照": json.dumps(proof, ensure_ascii=False) if not isinstance(proof, str) else proof.strip()}
    if proof.get("方式") == "实跑":
        evidence = f"证据（实跑）：命令 {proof.get('命令')}；输出 {proof.get('输出')}"
    else:
        evidence = f"证据（推演）：对照 {proof.get('对照')}"
    return (f"{tags}{str(problem.get('现象') or '').strip()}\n"
            f"   位置：{problem.get('位置')}\n    {evidence}")


def _list_problem_texts(data: dict) -> list:
    return [_problem_text(p) for p in (data.get("问题") or []) if isinstance(p, dict)]


def _return_problems(data: dict, first_round: bool = True) -> list:
    return [p for p in (data.get("问题") or []) if isinstance(p, dict) and _needs_return(p, first_round=first_round)]


def _later_problems(data: dict, first_round: bool = True, extra_back=None) -> list:
    back = list(extra_back) if extra_back is not None else _return_problems(data, first_round=first_round)
    out = []
    for problem in (data.get("问题") or []):
        if not isinstance(problem, dict) or _needs_return(problem, first_round=first_round):
            continue
        what = str(problem.get("现象") or "").strip()
        place = _place_of(problem)
        # 只有当某条退回项是“整段原文”（现象真包含了旧问题，且没有写明不同的具体位置）时，才说明已被整段带回；
        # 不同位置但现象相同的两条问题绝不互相排除
        covered_by_whole = bool(what) and any(
            what != str(b.get("现象") or "").strip()
            and what in str(b.get("现象") or "")
            and (_place_of(b) == ("", "") or place == ("", "") or _place_of(b) == place)
            for b in back
        )
        if not covered_by_whole:
            out.append(problem)
    return out


def _last_reject_conclusion(cdir: Path):
    """这次开工（最后一条追加要求之后）最近一条退回的复查结论。"""
    for event in reversed(_after_revise(read_flow(cdir))):
        if event.get("kind") == "复查结论" and event.get("result") == "退回":
            return event
    return None


def _conclusion_back_problems(event) -> list:
    """取一条复查结论里实际退回的问题列表（优先用按轮保存的“退回问题”，兼容旧记录的“清单”）。"""
    if not isinstance(event, dict):
        return []
    if isinstance(event.get("退回问题"), list):
        return [p for p in event["退回问题"] if isinstance(p, dict)]
    data = event.get("清单")
    if isinstance(data, dict) and isinstance(data.get("问题"), list):
        return _return_problems(data)
    return []


_LEVEL_RANK = {"严重": 3, "中等": 2, "轻微": 1}


def _problem_key(problem: dict) -> tuple:
    """按现象与位置生成去重/合并键；无行号时保留原始位置文字，避免不同地方同现象冲突。"""
    phen = str(problem.get("现象") or "").strip()
    place = _place_of(problem)
    if place == ("", ""):
        return (phen, ("", str(problem.get("位置") or "").strip()))
    return (phen, place)


def _merge_problem(old: dict, new: dict) -> dict:
    """同现象同位置合并：保留更重级别，任意一方标为“测试写错”则保留“测试写错”。"""
    old_rank = _LEVEL_RANK.get(old.get("级别"), 0)
    new_rank = _LEVEL_RANK.get(new.get("级别"), 0)
    sticky = ("测试写错", "安全或丢数据")   # 任一方标过就保留：都是“照退、退满也不放行”的类型
    new_is_sticky = new.get("类型") in sticky and old.get("类型") not in sticky
    if new_rank > old_rank or new_is_sticky:
        merged = dict(new)
        if old_rank > new_rank:
            merged["级别"] = old.get("级别")
        if old.get("类型") in sticky:
            merged["类型"] = old.get("类型")
        return merged
    return old


def _merge_back_problems(base: list, incoming: list) -> list:
    """按现象与位置合并退回问题列表，同条目保留更重级别与“测试写错”标记。"""
    back = list(base)
    index = {_problem_key(b): i for i, b in enumerate(back)}
    for p in incoming:
        if not isinstance(p, dict):
            continue
        key = _problem_key(p)
        if key in index:
            idx = index[key]
            back[idx] = _merge_problem(back[idx], p)
        else:
            index[key] = len(back)
            back.append(p)
    return back


def _resolve_review(cdir: Path, data: dict) -> tuple:
    """按 C1.6 规则判定本轮复查清单：返回（实际退回列表, 延期列表, 'reject'/'pass'）。"""
    prev_reject = _last_reject_conclusion(cdir)
    if prev_reject is None:
        back = _return_problems(data, first_round=True)
        later = _later_problems(data, first_round=True, extra_back=back)
        return back, later, ("reject" if back else "pass")
    prev_items = _conclusion_back_problems(prev_reject)
    answers = {}
    for entry in (data.get("上轮核对") or []):
        if not isinstance(entry, dict):
            continue
        num = entry.get("编号")
        ans = entry.get("结果")
        if isinstance(num, int) and not isinstance(num, bool) and ans in _CHECK_RESULTS:
            if num not in answers or ans == "没改好":
                answers[num] = ans
    has_valid_unfixed = any(answers.get(idx) == "没改好" for idx in range(1, len(prev_items) + 1))
    if data.get("结论") == "通过" and not (data.get("问题") or []) and not has_valid_unfixed:
        return [], [], "pass"
    kept_prev = [orig for idx, orig in enumerate(prev_items, 1) if answers.get(idx) != "改好了"]
    new_back = _return_problems(data, first_round=False)
    back = _merge_back_problems(kept_prev, new_back)
    later = _later_problems(data, first_round=False, extra_back=back)
    if back:
        back_keys = {_problem_key(b) for b in back}
        later = [p for p in later if _problem_key(p) not in back_keys]
    return back, later, ("reject" if back else "pass")


def _deferred_entries(problems: list) -> tuple:
    """从延期问题列表里提取 (原现象列表, 大白话列表, 带位置的去重键列表)。"""
    phens = [str(p.get("现象") or "").strip() for p in problems]
    words = _later_words(phens)
    keys = []
    for word, problem in zip(words, problems):
        place = _place_of(problem)
        if place != ("", ""):
            loc = f"{place[0]}:{place[1]}" if place[1] else place[0]
        else:
            loc = str(problem.get("位置") or "").strip()
        keys.append(f"{word}@@{loc}" if loc else f"{word}@@@{str(problem.get('现象') or '').strip()}")
    return phens, words, keys


def _record_deferred(cdir: Path, card: Card, reply_seq: int, problems: list, *,
                     back_texts=None, closed: bool = False) -> tuple:
    """把本轮记进“以后再修”的问题落盘到 以后再修.md、台账和流转记录，区分新小问题与开工前就有的。
    返回 (总大白话列表, 新小问题大白话列表, 旧问题大白话列表)。"""
    all_phen, all_words, all_keys = _deferred_entries(problems)
    new_probs = [p for p in problems if p.get("来源") != "原来就有的"]
    old_probs = [p for p in problems if p.get("来源") == "原来就有的"]
    _, new_words, new_keys = _deferred_entries(new_probs)
    _, old_words, old_keys = _deferred_entries(old_probs)
    already = any(e.get("kind") == "以后再修" and int(e.get("reply") or 0) == reply_seq for e in read_flow(cdir))
    if not problems or already:
        return all_words, new_words, old_words
    _append(cdir / "以后再修.md",
            f"\n## {datetime.now():%Y-%m-%d %H:%M} 复查挑出、这次不改的\n"
            + "\n".join(f"- {t}" for t in all_phen) + "\n")
    if new_words and old_words:
        ledger_msg = (f"{card.card_id}：还剩 {len(new_words)} 件小问题（{'；'.join(new_words)}），"
                      f"另有 {len(old_words)} 件开工前就有的问题（{'；'.join(old_words)}），已记以后再修，这次不改")
    elif new_words:
        ledger_msg = f"{card.card_id}：还剩 {len(new_words)} 件小问题，已记以后再修，这次不改：" + "；".join(new_words)
    else:
        ledger_msg = f"{card.card_id}：复查发现开工前就有的问题 {len(old_words)} 件，这次不改：" + "；".join(old_words)
    try:
        from collab.ledger import ledger_add
        ledger_add(cdir.parents[2], ledger_msg)
    except Exception as exc:  # noqa: BLE001 — 台账写不成不卡住跑卡
        record(cdir, "台账", src="程序", ok=False, summary=f"写台账失败：{exc}", text="\n\n".join(all_phen))
    if back_texts is not None:
        back_text = "结论：不通过\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(back_texts))
        if new_words and old_words:
            summary = f"复查另挑出 {len(new_words)} 件小问题和 {len(old_words)} 件原来就有的，记进“以后再修”，这次只退回 {len(back_texts)} 件"
        elif new_words:
            summary = f"复查另挑出 {len(new_words)} 件小问题，记进“以后再修”，这次只退回 {len(back_texts)} 件"
        else:
            summary = f"复查另挑出 {len(old_words)} 件原来就有的，记进“以后再修”，这次只退回 {len(back_texts)} 件"
        record(cdir, "以后再修", src="程序", dst="你", reply=reply_seq, back=back_text,
               items=all_words, new_items=new_words, old_items=old_words,
               item_keys=all_keys, new_keys=new_keys, old_keys=old_keys,
               summary=summary, text="\n\n".join(all_phen))
    elif closed:
        if new_words and old_words:
            summary = f"还剩 {len(new_words)} 件小问题，另有 {len(old_words)} 件原来就有的，已记进“以后再修”，不退回"
        elif new_words:
            summary = f"还剩 {len(new_words)} 件小问题，已记进“以后再修”，不退回"
        else:
            summary = f"复查剩下的 {len(old_words)} 件都是原来就有的，记进“以后再修”，不退回"
        record(cdir, "以后再修", src="程序", dst="你", reply=reply_seq, closed=True,
               items=all_words, new_items=new_words, old_items=old_words,
               item_keys=all_keys, new_keys=new_keys, old_keys=old_keys,
               summary=summary, text="\n\n".join(all_phen))
    return all_words, new_words, old_words


def _latest_review_conclusion(cdir: Path):
    """这次开工（最后一条追加要求之后）最近一条程序记的复查结论。"""
    for event in reversed(_after_revise(read_flow(cdir))):
        if event.get("kind") == "复查结论":
            return event
    return None


def _latest_conclusion_list(cdir: Path):
    event = _latest_review_conclusion(cdir)
    data = event.get("清单") if event else None
    return (event, data) if isinstance(data, dict) else (event, None)


def _credit_problem(role: str, adapter: str, output: str) -> str:
    """工具额度用完时给一句用户能照做的话；不是额度问题返回空。"""
    if not _OUT_OF_CREDIT.search((output or "")[-_CREDIT_TAIL:]):
        return ""
    return f"{role}（{adapter}）额度用完了：充值后点接着跑，或到设置里换一个工具"


def conflict_names(cdir) -> list:
    try:
        files = json.loads((Path(cdir) / CONFLICTS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(name) for name in files] if isinstance(files, list) else []


def _old_tests_changed_note(cdir: Path, worktree) -> str:
    """继续改那一轮，测试员改了或删了已有的测试：告诉复查员是哪些、备份在哪，逐个核对（Claude 最终验收复查第 1 轮）。
    备份是这一轮派测试员前存的（测试前备份/），只认测试文件。"""
    backup = Path(cdir) / _TEST_BACKUP
    root = Path(worktree)
    changed = []
    for b in (sorted(backup.rglob("*")) if backup.is_dir() else []):
        if not b.is_file() or b.is_symlink():
            continue
        rel = b.relative_to(backup).as_posix()
        if not is_test_path(rel):
            continue
        cur = root / rel
        if not cur.is_file():
            changed.append(f"{rel}（整个删了）")
        elif cur.read_bytes() != b.read_bytes():
            changed.append(rel)
    # 项目里早就提交好、派测试员前没改动的旧测试不在备份里：和提交时比（复查第 2 轮）
    before = _load_before_map(cdir)
    # -z：不转义中文文件名（复查第 3 轮）；输出是“状态\0路径\0”交替
    out = subprocess.run(["git", "diff", "--name-status", "--no-renames", "-z", "HEAD"], cwd=str(root),
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    fields = out.stdout.split("\0")
    from_head = []
    for status, rel in zip(fields[0::2], fields[1::2]):
        if status not in ("M", "D") or not rel or rel in before or not is_test_path(rel):
            continue
        item = f"{rel}（整个删了）" if status == "D" else rel
        if item not in changed:
            changed.append(item)
            from_head.append(rel)
    if not changed:
        return ""
    where = f"改之前的样子：{backup} 下同名文件"
    if from_head:
        where += f"；{'、'.join(from_head)} 不在备份里，用 git show HEAD:<路径> 看提交时的样子"
    return (f"注意：这一轮测试员改动了已有的测试：{'、'.join(changed)}。{where}。"
            f"请逐个对照：被删改的用例必须确实是被追加要求推翻的；删掉或放松了和追加要求无关的旧用例、削弱了防回归，判不通过。")


def _tester_conflict_note(cdir) -> str:
    names = conflict_names(cdir)
    if not names:
        return ""
    return "这些文件有冲突，由开发员处理，你不要改：" + "、".join(names) + "。"


def _conflict_note(worktree, cdir) -> str:
    clash = unresolved_conflicts(worktree, cdir)
    if not clash:
        return ""
    return ("同步项目新内容时，这些文件有冲突，里面留着冲突标记：" + "、".join(clash)
            + "。先把冲突解决掉，既保留项目的新内容，也保留这张卡的改动。")


def has_conflict_marks(path) -> bool:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    return any(line.startswith("<<<<<<< ") for line in lines) and any(line.startswith(">>>>>>> ") for line in lines)


def unresolved_conflicts(worktree, cdir) -> list:
    """同步时有冲突的文件里，还留着冲突标记的。"""
    left = []
    for name in conflict_names(cdir):
        try:
            lines = (Path(worktree) / name).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        if any(line.startswith(("<<<<<<< ", ">>>>>>> ")) or line == "=======" for line in lines):
            left.append(name)
    return left


_NOT_PROGRAMS = {"cd", "export", "env", "true", "set", "source", "."}


def _programs_in(cmd: str) -> list:
    """测试命令里真正要跑的程序名：“cd web && npm test; python3 -m x” → npm、python3。"""
    found = []
    for part in re.split(r"&&|\|\||;|\|", cmd or ""):
        try:
            words = shlex.split(part)
        except ValueError:
            continue
        words = [w for w in words if "=" not in w.split("/")[0]]  # 去掉开头的 A=1 这类环境变量
        if not words or words[0] in _NOT_PROGRAMS:
            continue
        name = Path(words[0]).name
        if name and name not in found:
            found.append(name)
    return found


def cursor_permissions(worktree, test_command):
    """写放行设置，返回一个“还原”函数：Cursor 干完就把设置去掉（原来没有就删掉，原来有就还原），
    复查员和放进项目都看不到它（2026-11-12 端到端试跑：复查员把它当成越界文件打回）。"""
    path = Path(worktree) / ".cursor" / "cli.json"
    folder_was_there = path.parent.is_dir()
    try:
        before = path.read_bytes() if path.is_file() else None
    except OSError:
        before = None
    _write_cursor_permissions(worktree, test_command)

    def restore():
        try:
            if before is None:
                # 它若 git add 过，暂存区里也要拿掉，不然放进项目时被当成越界文件（流程修理复查第 1 轮）
                subprocess.run(["git", "rm", "-q", "--cached", "--ignore-unmatch", "--", ".cursor/cli.json"],
                               cwd=str(worktree), capture_output=True)
                if path.is_file():
                    path.unlink()
                if not folder_was_there and path.parent.is_dir() and not any(path.parent.iterdir()):
                    path.parent.rmdir()
            else:
                path.write_bytes(before)
                # 项目自己的设置被暂存过临时内容：暂存区也还原（复查第 2 轮）
                subprocess.run(["git", "reset", "-q", "--", ".cursor/cli.json"], cwd=str(worktree), capture_output=True)
        except OSError:
            pass
    return restore


def _write_cursor_permissions(worktree, test_command) -> None:
    """Cursor 在后台干活时每条命令都要人点同意，没人点就全被拒（68b 里它因此绕路、开浏览器卡死，⑯⑰）。
    派活前在施工副本写项目级放行设置：只放行本卡测试命令用的那个程序（Cursor 只能按程序名放行），
    浏览器、搜索这类插件一律不许用。2026-11-12 实测：放行后能跑 python3，node 和浏览器仍被拒；不写就全被拒。"""
    programs = _programs_in(_command_text(test_command))
    path = Path(worktree) / ".cursor" / "cli.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    perms = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
    allow = [x for x in perms.get("allow") or [] if isinstance(x, str)]
    deny = [x for x in perms.get("deny") or [] if isinstance(x, str)]
    for program in programs:
        if f"Shell({program})" not in allow:
            allow.append(f"Shell({program})")
    if "Mcp(*:*)" not in deny:
        deny.append("Mcp(*:*)")
    data["permissions"] = {"allow": allow, "deny": deny}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _accepts(fn, name: str) -> bool:
    """假实现（测试里）可能不认新参数，认才传。"""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind == p.VAR_KEYWORD for p in params.values())


# Cursor 沙箱不让在部分位置建 .git（69 里报 Operation not permitted，工作副本里和 /tmp 都不行），设置里没有可靠的开关
# （第三批 51，已知取舍）：让它别为此绕路，程序交活后会完整跑本卡测试
_CURSOR_SANDBOX_NOTE = ("你在沙箱里跑测试时，要建临时 git 仓库的测试可能报 Operation not permitted："
                        "这类测试跑不了就跳过，在汇报里写一句，不要为它换办法绕路；交活后程序会完整跑本卡测试。")
_NO_PLUGINS_NOTE = "不要打开浏览器，不要用搜索、数据库、设计稿这类插件；这单只需要读代码、改文件、跑本卡测试。"


_PROGRESS_ON: set = set()   # 哪些卡开了进度（只在真的施工时开，测试替身不开）


_DIFF_STEPS = ("写测试", "改测试", "补测试", "开发", "返工修改")   # 轨迹“看改动”：写测试、写代码（含返工）都存（第七批 7-2，对照 05t）
_DIFF_DIR = "改动"
_DIFF_LIMIT = 480 * 1024           # 一步的改动最多存 480KB（页面最多读 512KB），再多截掉并写明
_SNAP_BIG = 5 * 1024 * 1024        # 没被 git 管的大文件（>5MB）不进快照，免得每步都整份塞进 git 对象库
_GIT_TIMEOUT = 60
# 不算谁的改动：协作区、依赖、运行缓存、派活前写给工具的放行设置（和 collab.scope.is_cache 同一套）
_SNAP_EXCLUDES = (":(exclude).collab", ":(exclude,glob)**/node_modules/**", ":(exclude,glob)**/__pycache__/**",
                  ":(exclude,glob)**/.pytest_cache/**", ":(exclude,glob)**/.mypy_cache/**",
                  ":(exclude,glob)**/.ruff_cache/**", ":(exclude,glob)**/*.pyc", ":(exclude,literal).cursor/cli.json")


# 上面排除规则的“正面写法”，用来把已经被暂存进来的这些路径从快照里去掉
_SNAP_DROP = (".collab", ":(glob)**/node_modules/**", ":(glob)**/__pycache__/**", ":(glob)**/.pytest_cache/**",
              ":(glob)**/.mypy_cache/**", ":(glob)**/.ruff_cache/**", ":(glob)**/*.pyc", ":(literal).cursor/cli.json")


def _git(root, args, env=None, text=True):
    return subprocess.run(["git", *args], cwd=str(root), env=env, check=True, capture_output=True, text=text,
                          timeout=_GIT_TIMEOUT)


def _tree_snapshot(worktree):
    """给施工副本当前文件拍个快照，返回树编号；不是 git 文件夹或出错返回 None。
    用真正暂存区的一份拷贝当起点（已跟踪又被忽略的文件也照样算，没改的文件不重算），
    只写这份拷贝，不碰真正的暂存区和提交（复查第 1 轮中等 11、12、17）。"""
    try:
        root = Path(worktree)
        if not (root / ".git").exists():
            return None
        index_rel = _git(root, ["rev-parse", "--git-path", "index"]).stdout.strip()
        real_index = Path(index_rel) if Path(index_rel).is_absolute() else root / index_rel
        untracked = _git(root, ["ls-files", "-o", "--exclude-standard", "-z"]).stdout.split("\0")
        big = []
        for rel in untracked:
            try:
                if rel and (root / rel).is_file() and (root / rel).stat().st_size > _SNAP_BIG:
                    big.append(f":(exclude,literal){rel}")
            except OSError:
                continue
        with tempfile.TemporaryDirectory() as tmp:
            tmp_index = Path(tmp) / "index"
            if real_index.is_file():
                # 用 copy2 连时间一起拷：git 靠“暂存区文件的时间”判断哪些文件可能在同一秒里被改过（刚提交就改成同样长度），
                # 只有这部分才重新读内容；拷成“现在”会让这个判断失效（漏记），拨成 24 小时前又会让近期文件每次都重读（大仓库慢约 18 倍，复查第 4 轮）
                shutil.copy2(real_index, tmp_index)
            env = dict(os.environ, GIT_INDEX_FILE=str(tmp_index))
            _git(root, ["add", "-A", "--", ".", *_SNAP_EXCLUDES, *big], env=env)
            # 工具自己 git add 过的缓存、协作区、依赖已经在拷贝的暂存区里，排除规则只管这次 add，要另外从拷贝里去掉
            _git(root, ["rm", "-r", "--cached", "-f", "--ignore-unmatch", "-q", "--", *_SNAP_DROP], env=env)
            tree = _git(root, ["write-tree"], env=env).stdout.strip()
        return tree or None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _save_step_diff(cdir: Path, worktree, before, seq) -> str:
    """这一步前后两个快照比一比，有改动就存进卡片资料 改动/<序号>.diff，返回相对位置；没改动或比不了返回空。"""
    if not before:
        return ""
    after = _tree_snapshot(worktree)
    if not after or after == before:
        return ""
    try:
        out = _git(worktree, ["-c", "core.quotepath=off", "diff", "--no-color", "--no-ext-diff", before, after], text=False)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""
    data = out.stdout
    if not data.strip():
        return ""
    if len(data) > _DIFF_LIMIT:
        data = data[:_DIFF_LIMIT] + "\n…（改动太长，后面没存）\n".encode("utf-8")
    rel = f"{_DIFF_DIR}/{seq}.diff"
    target = Path(cdir) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return rel


_STUCK_COUNTS = "模型卡住.json"   # 项目级小记录：{"<工具>|<模型>": 连续没回话被停下的次数}


def _collab_root(cdir: Path) -> Path:
    """卡片目录往上两级就是项目的 .collab。"""
    return Path(cdir).parent.parent


def _counts_path(cdir: Path) -> Path:
    return _collab_root(cdir) / _STUCK_COUNTS


def _load_counts(cdir: Path) -> dict:
    """读连续次数；文件坏了、内容不是字典都当空的，不影响跑卡。"""
    try:
        data = json.loads(_counts_path(cdir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_counts(cdir: Path, counts: dict) -> None:
    try:
        _counts_path(cdir).write_text(json.dumps(counts, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 写不进去也不挡跑卡


def _count_of(counts: dict, key: str) -> int:
    try:
        return max(0, int(counts.get(key) or 0))
    except (TypeError, ValueError):
        return 0  # 次数写坏了当没记过


def _bump_count(cdir: Path, key: str) -> None:
    counts = _load_counts(cdir)
    counts[key] = _count_of(counts, key) + 1
    _save_counts(cdir, counts)


def _clear_count(cdir: Path, key: str) -> None:
    counts = _load_counts(cdir)
    if key in counts:
        counts.pop(key, None)
        _save_counts(cdir, counts)


def _reply_day(event: dict):
    """一条回复算哪一天：11-19 起只记运行时间秒数 ts，按本机日期算；老记录没有 ts 的才看 time。"""
    ts = event.get("ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return datetime.fromtimestamp(ts).date(), float(ts)
    old = event.get("time")
    if isinstance(old, str) and old.strip():
        try:
            when = datetime.fromisoformat(old.strip())
        except ValueError:
            return None
        return when.date(), when.timestamp()
    return None


def _suggest_model(collab_dir, adapter: str, model_text: str) -> str:
    """今天顺利交过活的工具和模型里，挑最近的一个当换模型建议；跳过卡住的这个工具+模型。
    项目所有卡都看，包括正在跑的这张：测试员先交活、开发员后接活是正常流程，
    本卡里别的岗位跑成功也是“今天跑得正常”的证据（11-19 复查）。
    返回“工具（模型）”；有记型号的优先，都没记型号时只写工具名（施工单）；一个都没有返回空字符串。"""
    cards = Path(collab_dir) / "cards"
    today = datetime.now().date()
    best = None   # ((有没有型号, 先后), 工具, 模型)
    try:
        flows = sorted(cards.glob("*/流转.jsonl"))
    except OSError:
        return ""
    for path in flows:
        try:
            events = read_flow(path.parent)
        except OSError:
            continue
        tasks = {e.get("seq"): e for e in events if e.get("kind") == "派活"}
        for event in events:
            if event.get("kind") != "回复" or event.get("ok") is not True or not event.get("reply_to"):
                continue
            day = _reply_day(event)
            if day is None or day[0] != today:
                continue
            task = tasks.get(event.get("reply_to"))
            if not task:
                continue
            tool = str(task.get("tool") or "")
            model = str(task.get("model") or "")
            if not tool:
                continue
            if tool == adapter and (model == model_text or not model):
                continue  # 就是卡住的这个工具+模型；同一个工具没记模型的，分不清是不是它，也跳过
            rank = (1 if model else 0, day[1])   # 有型号的优先（说得出具体换哪个），同档取最近的
            if best is None or rank >= best[0]:
                best = (rank, tool, model)
    if best is None:
        return ""
    return f"{best[1]}（{best[2]}）" if best[2] else best[1]   # 没记模型的只写工具名（施工单）


def _dispatch_model(cdir: Path, role: str, adapter: str) -> str:
    """这次失败对应的派活记的模型名（用记录里的，不拿团队设置此刻的值来猜）。"""
    for event in reversed(read_flow(cdir)):
        if event.get("kind") == "派活" and event.get("dst") == role and event.get("tool") == adapter:
            return str(event.get("model") or "")
    return ""


def _switch_words(suggest: str) -> str:
    """换模型那句：有推荐就“换成 X”，没有就“换一个模型”。"""
    return f"换成 {suggest}" if suggest else "换一个模型"


def _dispatch(ask_fn, cdir: Path, role: str, adapter: str, step: str, prompt: str,
              worktree, models, round_no: int, test_command=None):
    """派一次活：开始时记“派活”（含原文、预留实时输出），干完记“回复”（含全文、用时）。"""
    models = dict(models or {}, no_plugins=True)  # 派活不带用户配的插件（Codex 能关；agy 关不了，靠交代，㉘ ㊶）
    from collab.config import model_label
    model_text = model_label(adapter, models)
    effort = str(models.get("codex_effort") or "")
    if model_text and effort and adapter == "codex":
        # model_label 已经把强度写在名字后面（中文档位）；这里换成实际用的档位名，右栏按最后一次派活显示（第二批 A5）
        model_text = f"{re.sub(r'（[^（）]*）$', '', model_text)}（{effort}）"
    stuck_key = f"{adapter}|{model_text}"
    counted = _count_of(_load_counts(cdir), stuck_key)
    if counted >= 2:   # 同一个工具+模型连续两次没回话被停下：这次派活前先提醒（11-19 观察记录第 41 条）
        switch = _switch_words(_suggest_model(_collab_root(cdir), adapter, model_text))
        record(cdir, "提醒", src="程序", dst="你", role=role, tool=adapter,
               summary=f"{role}用的 {adapter}（{model_text}）连续 {counted} 次没回话，建议先{switch}；这次照样派给它。",
               say=f"{role}用的 {adapter}（{model_text}）已经连续 {counted} 次没回话被停下，"
                   f"建议先在团队设置{switch}再点“接着跑”；这次照样派给它。")
    prompt = prompt + _NO_PLUGINS_NOTE + (_CURSOR_SANDBOX_NOTE if adapter == "cursor" else "") + attachment_prompt(cdir)
    # 先拍快照，再给 Cursor 写临时放行设置（不然“看改动”里会多出一份删掉设置，复查中等 11）
    before_tree = _tree_snapshot(worktree) if step in _DIFF_STEPS else None
    restore = cursor_permissions(worktree, test_command) if adapter == "cursor" else None
    sent = record(cdir, "派活", src="调度员", dst=role, tool=adapter, step=step, round=round_no,
                  summary=_STEP_SUMMARY[step], text=prompt, live=True, model=model_text or None)
    started = time.time()
    extra = {}
    if _accepts(ask_fn, "on_quiet"):
        def on_quiet(seconds, waiting=None, role=role, adapter=adapter, model_text=model_text):
            minutes = max(1, seconds // 60)
            left = max(1, (QUIET_LIMIT - seconds) // 60)
            if waiting is None:   # 老调用只给秒数：原话照旧
                record(cdir, "提醒", src="程序", dst="你", role=role, tool=adapter,
                       summary=f"{role}（{adapter}）已经 {minutes} 分钟没有动静（没改文件，也没有新输出）。"
                               f"可以再等等，再过约 {left} 分钟还没动静程序会自动停下；也可以点“停下”再点“接着跑”。")
                return
            if waiting:
                switch = _switch_words(_suggest_model(_collab_root(cdir), adapter, model_text))
                record(cdir, "提醒", src="程序", dst="你", role=role, tool=adapter,
                       summary=f"{role}（{adapter}）已经 {minutes} 分钟没回话，建议{switch}再试；剩余 {left} 分钟",
                       say=f"{role}（{adapter}）的模型已经 {minutes} 分钟没回话（工具在等模型，没在跑命令、没改文件），"
                           f"多半是模型服务卡了，也可能在想很久。建议点“停下”，在团队设置{switch}再点“接着跑”；"
                           f"也可以再等等，再过约 {left} 分钟还没动静程序会自动停下。")
            else:
                record(cdir, "提醒", src="程序", dst="你", role=role, tool=adapter,
                       summary=f"{role}（{adapter}）已经 {minutes} 分钟没改文件，但还在跑命令，可以再等等；剩余 {left} 分钟",
                       say=f"{role}（{adapter}）已经 {minutes} 分钟没改文件，但还在跑命令，像是在干活，可以再等等；"
                           f"再过约 {left} 分钟还没动静程序会自动停下。")
        extra["on_quiet"] = on_quiet
    if _accepts(ask_fn, "watch_commands"):
        extra["watch_commands"] = True  # 它自己跑的命令卡住只掐那一条（㊹）
        extra["test_command"] = _command_text(test_command) if test_command else ""  # 本卡测试时限长一点
    job = None
    if str(cdir) in _PROGRESS_ON and step in ("写测试", "开发", "返工修改"):
        from collab.progress import ProgressJob
        job = ProgressJob(cdir, worktree, step, sent["seq"], _load_test_files(cdir),
                          test_command=_command_text(test_command) if test_command else "").start()
    try:
        result = ask_fn(adapter, prompt, worktree, expect_file=None, models=models,
                        live_path=str(cdir / sent["live"]), **extra)
    finally:
        if job is not None:
            job.stop()
        if restore:
            restore()
    text = reply_text(result)
    diff_rel = _save_step_diff(cdir, worktree, before_tree, sent["seq"]) if before_tree else ""
    tokens = _tokens_for(adapter, getattr(result, "output", "") or "", cdir / sent["live"])
    more = {}
    if diff_rel:
        more["diff"] = diff_rel
    if tokens is not None:
        more["tokens"] = tokens
    record(cdir, "回复", src=role, dst="调度员", tool=adapter, step=step, round=round_no,
           ok=bool(result.ok), seconds=round(time.time() - started), reply_to=sent["seq"],
           summary=reply_summary(step, text, result.ok), text=text, **more)
    if result.ok:
        _clear_count(cdir, stuck_key)   # 顺利交活：这个工具+模型清零
    elif "模型没回话" in str(getattr(result, "output", "") or ""):
        _bump_count(cdir, stuck_key)    # 模型没回话被停下：加一次
    return result


def _escalate(collab: Path, cdir: Path, card: Card, reason: str, cause: str = "",
              text=None, say: str = "", **extra) -> None:
    """转需Claude介入，并在流转记录里写明原因。cause="复查不通过" 时用户可以接受剩下的问题（只认这个标记，不认文字）。"""
    clean_extra = {k: v for k, v in extra.items() if v is not None}
    record(cdir, "转人工", src="程序", dst="调度员", summary=reason,
           cause=cause or None, text=text, say=say or None, **clean_extra)
    card.transition("需Claude介入")
    _save(collab, card)


UNREADABLE = "复查看不懂"   # 转人工的标记：清单重写后仍看不懂（C1.8）。接着跑时重新复查，不把原话当退回意见


def _stop_unreadable(collab: Path, cdir: Path, card: Card, reviewer_adapter: str, review_text: str,
                     fixed_text: str, prev_items: list) -> None:
    """清单重写后仍看不懂：停下转人工。原话和重写都存下。给人看的说明**不从原话里挑、也不摘**（挑就会漏，
    C1.8a 复查第 1、2 轮）：原话里出现“严重”字样（包括写坏的清单）就明说，请先看完整原话；
    再列上轮退回、还没确认修好的严重问题（那是正式记录，不是猜的）。这种停下不能“接受剩下的问题”。"""
    prev_severe = [str(p.get("现象") or "").strip() for p in (prev_items or [])
                   if isinstance(p, dict) and p.get("级别") == "严重" and str(p.get("现象") or "").strip()]
    words = f"复查员（{reviewer_adapter}）两次都没按固定格式写清单，程序看不懂，不猜，也不自动退回或放行。"
    if "严重" in (review_text or "") or "严重" in (fixed_text or ""):
        words += "原话里写到了“严重”，请先看完整原话。"
    if prev_severe:
        words += f"上轮退回的严重问题还没确认修好：{'；'.join(prev_severe)}。"
    words += "完整原话在右边“交接”里；看过后可以点“接着跑”让复查员重新复查。"
    _escalate(collab, cdir, card,
              f"复查员（{reviewer_adapter}）两次都没按固定格式写清单，程序看不懂，不猜；原话已存，需要你看看",
              cause=UNREADABLE, say=words,
              text=f"{review_text}\n\n——格式重写——\n\n{fixed_text}".strip())

def _worker_failed(collab: Path, cdir: Path, card: Card, role: str, adapter: str, result):
    """干活的人没完成（报错、超时、被看门人停掉）：把原因写进返工记录，再转人工。"""
    # agy 报错时结果是空的，原因在它自己报的 error 里（㉙ 复查第 1 轮）
    reason = (readable(result.output, adapter) or agy_error(result.output) or "没有返回任何内容").strip().splitlines()
    tail = reason[-1][:300] if reason else "没有返回任何内容"
    _append(cdir / "返工记录.md",
            f"- {datetime.now():%Y-%m-%d %H:%M} {role}（{adapter}）没有完成：{tail}\n")
    words = _credit_problem(role, adapter, result.output)
    output = str(result.output or "")
    # 停下详情（右栏“交接”）统一在这里拼：先放工具报的原话（当时为什么停下），再放能照做的建议。
    # 各分支只准备建议，不许另写详情——返工那次就是把原话换成了短句，右栏丢了等待时长和“在等模型”
    # 的说明（复查第 9 条）。原话永远保留；限流只放那一条报错，后面别的输出不塞进来（复查第 6 条）。
    reason_line = tail
    advice = words   # 额度用完那句本身就是建议
    if not words and ("429" in output or "Too Many Requests" in output):
        # 被服务限流：换模型建议照旧，并说明过一会儿可以接着跑（第三批 C12）
        switch = _switch_words(_suggest_model(_collab_root(cdir), adapter,
                                              _dispatch_model(cdir, role, adapter)))
        words = f"{role}（{adapter}）被服务限流（请求太多）停下了，建议在团队设置{switch}或过一会儿再点“接着跑”"
        raw = next((ln.strip() for ln in output.splitlines()
                    if "429" in ln or "Too Many Requests" in ln), "")
        advice = words
        reason_line = f"原始报错：{raw[:300]}" if raw else tail
    elif not words and "模型没回话" in output:
        # 模型没回话被停下：用失败那次派活记录的模型名排除当前组合，再给换模型建议（11-19 观察记录第 41 条）
        switch = _switch_words(_suggest_model(_collab_root(cdir), adapter,
                                              _dispatch_model(cdir, role, adapter)))
        words = f"{role}（{adapter}）模型没回话被停下，建议在团队设置{switch}再点“接着跑”"
        advice = words
    if not advice:   # 普通报错：没有建议可给，说明里也要写清是谁没完成（D1 复查第 3 轮，Claude 接手）
        reason_line = f"{role}（{adapter}）没有完成：{tail}"
    say = f"{reason_line}。{advice}" if advice and advice not in reason_line else reason_line
    _escalate(collab, cdir, card, words or f"{role}（{adapter}）没有完成：{tail}", say=say)
    return card, None


MAX_REVIEW_RETURNS = 2  # 复查不通过自动退回写代码的最多几次（用户 2026-11-12 定）
_NOTES_LIMIT = 8000  # 交给写代码的复查意见最长多少字


def _review_rejects(cdir: Path) -> int:
    """这次开工（最后一条追加要求之后）复查不通过了几次，含刚才这次。"""
    conclusions = [e for e in _after_revise(read_flow(cdir)) if e.get("kind") == "复查结论"]
    if conclusions:
        return sum(1 for e in conclusions if e.get("result") == "退回")
    count = 0
    closed = _closed_replies(cdir)   # 收口算通过的那次不算退回（第四轮 57）
    flow = _after_revise(read_flow(cdir))
    # 格式重写那次回复不是新的一轮复查，不能再算一次退回（C1.8 复查第 1 轮严重 2：原话 + 重写各算一次，
    # 第 1 轮就被当成退满两次，本该退回的问题被记以后再修直接放行）
    reformat_seqs = {e.get("seq") for e in flow if e.get("kind") == "派活" and e.get("dst") == "复查员"
                     and "按固定格式重写" in (text_of(cdir, e) or "")}
    for e in flow:
        if e.get("kind") == "回复" and e.get("src") == "复查员" and e.get("ok") is True and e.get("seq") not in closed \
                and e.get("reply_to") not in reformat_seqs:
            body = text_of(cdir, e) or str(e.get("summary") or "")
            if review_verdict(body) == "reject":
                count += 1
    return count


def _first_issues(text: str, limit: int = 200) -> str:
    """转卡住时原因里带上复查意见的头几行，用户一眼能看到剩下什么问题。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip() and not _REVIEW_VERDICT.match(ln)]
    joined = "；".join(lines)
    return (joined[:limit] + "……") if len(joined) > limit else (joined or "复查员没有写下详细意见")


def _issues_words(issues: list) -> str:
    marks = "①②③④⑤⑥⑦⑧⑨⑩"
    return "；".join(f"{marks[i] if i < len(marks) else str(i + 1) + '.'} {t}" for i, t in enumerate(issues))


def _dev_rounds(cdir: Path) -> int:
    """整张卡派了写代码的几次（不因“继续改”清零，㊱）。"""
    return sum(1 for e in read_flow(cdir) if e.get("kind") == "派活" and e.get("dst") == "开发员")


def _strip_review_code(text: str) -> str:
    """去掉复查回复里的代码块和末尾“复查清单：”标题，避免清单/示例干扰正文问题抽取。"""
    cleaned = _CLOSED_BLOCK.sub("", text or "")
    return re.sub(r"(?m)^[ \t]*复查清单[：:]?[ \t]*$", "", cleaned).strip()


def _whole_text_issues(text: str) -> list:
    """格式重写仍不合格按整段原文退回时，把正文和残缺清单里的问题都提出来（严重问题绝不漏）。"""
    cleaned = _strip_review_code(text)
    items = _review_items(cleaned)
    out = []
    seen = set()
    severe = re.compile(r"P[012]\b|严重|中等|高危|安全|丢失|崩溃")

    def add_line(raw: str):
        body = re.sub(r"^\s*(?:[-*•]\s*|\d+[.、)）]\s*)?", "", str(raw or ""))
        body = re.sub(r"^\s*(?:【[^】]{1,12}】\s*)+", "", body).strip()
        if not body:
            return
        words = plain_issues(f"1. {body}")
        cand = words[0] if words else re.split(r"[。！？!?]", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body), maxsplit=1)[0].strip()[:80]
        if cand and cand not in seen:
            seen.add(cand)
            out.append(cand)

    for item in items:
        add_line(item)
    if not out or _unparsed_serious(cleaned, items):
        for line in cleaned.splitlines():
            s = line.strip()
            if not s or _REVIEW_VERDICT.match(s):
                continue
            if severe.search(s) or TEST_PROBLEM in s or _new_issue_line(s):
                add_line(s)
    raw_problems, _ = _raw_list_problems(text)
    extra_front = []
    for p in raw_problems:
        if not _needs_return(p) and out:
            continue
        phen = str(p.get("现象") or "").strip()
        # 只在新的一条被已有的完全包含时才跳过；已有的短句被包含在更长的新句里时照样加上，
        # 不然“列表异常”会盖掉“列表异常，切换设备会删掉所有草稿”这种严重问题（11-18 第 3 轮复查严重 2）
        if not phen or phen in seen or any(phen in existing for existing in out):
            continue
        seen.add(phen)
        if p.get("级别") == "严重" or p.get("类型") == "测试写错":
            extra_front.append(phen)
        else:
            out.append(phen)
    return extra_front + out


def _stop_reason(cdir: Path, hints_only: bool = False, has_tests: bool = False) -> str:
    """自动退回用完停下时，帮用户判断为什么不过、该怎么办（㊱）。
    hints_only：只要“同一个问题反复出现”“要改范围外的文件”这两条具体提示（没有就返回空），
    给先说严重问题的那条判断附在后面用（右栏显示小修 2c，Codex 复查 11-20）。"""
    import difflib
    events = _after_revise(read_flow(cdir))
    reformat_seqs = {
        e.get("seq") for e in events
        if e.get("kind") == "派活" and e.get("dst") == "复查员"
        and "按固定格式重写" in (text_of(cdir, e) or "")
    }
    rounds = []
    review_bodies = []
    for e in events:
        if e.get("kind") == "回复" and e.get("src") == "复查员" and e.get("ok") is True:
            if e.get("reply_to") in reformat_seqs:
                continue
            body = text_of(cdir, e) or str(e.get("summary") or "")
            if review_verdict(body) == "reject":
                review_bodies.append(body)
                rounds.append(_whole_text_issues(body) or plain_issues(body))
    if review_bodies:
        last_body = review_bodies[-1]
    else:
        last_body = ""
        for e in reversed(events):
            if e.get("kind") == "回复" and e.get("src") == "复查员" and e.get("ok") is True:
                if e.get("reply_to") in reformat_seqs:
                    continue
                last_body = text_of(cdir, e) or ""
                break
    clean_last = _strip_review_code(last_body)
    raw_last = _review_items(clean_last)
    raw_problems, code_severe = _raw_list_problems(last_body)
    list_test_wrong = any(p.get("类型") == "测试写错" for p in raw_problems)
    list_severe = any(p.get("级别") == "严重" for p in raw_problems)
    list_non_minor = any(
        p.get("级别") != "轻微" and (_needs_return(p) or p.get("级别") in ("严重", "中等"))
        for p in raw_problems
    )
    last = rounds[-1] if rounds else []
    before = [t for r in rounds[:-1] for t in r]
    if not hints_only and (any(TEST_PROBLEM in i for i in raw_last) or TEST_PROBLEM in clean_last
                           or list_test_wrong):  # 先看测试的问题（第三批 ㊿）
        return ("判断：复查说测试本身写错了，写代码的怎么改都会被它卡住。"
                "建议：点“不满意，继续改”，写明让写测试的把哪条测试改对")
    severe = re.compile(r"P[012]\b|严重|中等|高危|安全|丢失|崩溃")  # 有一条是重的就不算小问题（第 2 轮 B 第 3 条）
    if (not hints_only and raw_last and not _unparsed_serious(clean_last, raw_last)
            and all(re.search(r"P3|轻微", i) and not severe.search(i) for i in raw_last)
            and not severe.search(clean_last)
            and not list_non_minor and not list_test_wrong and not code_severe):  # 只认复查员标的级别，且原文与残缺清单都未漏出严重问题
        return "判断：剩下的都是小问题。建议：点“接受剩下的问题，进入待确认”"
    places = []  # 每轮复查写的位置（文件:行），位置一样也算同一问题（第三批 ㊿）
    for body in review_bodies:
        # 只比“问题出在哪”：正文里的位置 + 清单的“位置”；清单“证据”里对照的文件不算（11-18 验收：
        # 证据引用同一个文件会被误判成“同一个问题反复出现”）
        found = set(_extract_locations(_strip_review_code(body)))
        for problem in _raw_list_problems(body)[0]:
            found |= set(_extract_locations(str(problem.get("位置") or "")))
        places.append(found)
    same_place = len(places) >= 2 and bool(places[-1] & set().union(*places[:-1]))
    repeated = same_place or (last and before and
                              any(difflib.SequenceMatcher(None, a, b).ratio() >= 0.6 for a in last for b in before))
    repeat_said = ("判断：同一个问题反复出现，可能是写代码的这个模型做不动。"
                   "建议：去设置里换写代码的工具或模型再点“接着跑”；或者点“不满意，继续改”把这条讲清楚")
    out_of_scope = any(e.get("kind") == "范围" and "范围外" in str(e.get("summary") or "") for e in events)
    scope_said = "判断：写代码的要改允许范围外的文件。建议：点“不满意，继续改”，写明要多改哪个文件"
    if hints_only:
        # 有测试写错时，说不清反复出现的是测试还是代码，不怪写代码的模型（和原来一样：有测试写错就不给换模型建议）；
        # 范围外的提示总是带上（Codex 复查第 2 轮）
        has_test_problem = (has_tests or any(TEST_PROBLEM in i for i in raw_last) or TEST_PROBLEM in clean_last
                            or list_test_wrong)   # has_tests：实际退回清单里有测试写错（Codex 复查第 3 轮）
        hints = ([repeat_said] if repeated and not has_test_problem else []) + ([scope_said] if out_of_scope else [])
        return "；".join(h.replace("判断：", "", 1) for h in hints)
    if repeated:
        return repeat_said
    if out_of_scope:
        return scope_said
    if re.search(r"P[01]\b|严重|高危|安全|丢失|崩溃", clean_last) or list_severe or code_severe:
        return "判断：还有严重问题没解决，不能当成小问题。建议：点“不满意，继续改”，把上面的问题讲清楚"
    if last and before:
        return "判断：每轮都冒出新问题，多半是施工单没写准。建议：点“不满意，继续改”补清要求，或者拆小再做"
    return "建议：看一眼剩下的问题，再选“不满意，继续改”或“接受剩下的问题”"


def first_line_verdict(text: str) -> str:
    """回复第一行（去掉空行、加粗、井号）写的结论：reject / pass / ""。只看第一行：正文里引用的日志、示例不算（11-18 复查 5）。"""
    for line in (text or "").splitlines():
        s = re.sub(r"[*#>`\s]", "", line)
        if not s:
            continue
        m = re.match(r"结论[：:](不通过|通过)(?![\u4e00-\u9fff])", s)   # “通过不了”不算通过（11-18 复查 3-2）
        return ("reject" if m.group(1) == "不通过" else "pass") if m else ""
    return ""


def _list_contradicts(text: str, data) -> bool:
    """第一行写“结论：不通过”，清单却写“通过”：对不上（比如贴的是格式示例），绝不放行（11-18 第 3 轮复查严重 1）。"""
    return first_line_verdict(text) == "reject" and isinstance(data, dict) and data.get("结论") == "通过"

def plain_problems(problems) -> tuple:
    """清单条目翻成给用户看的大白话（和老卡 plain_issues 同一套：去链接、去代号、只取第一句）。
    严重的在句尾标“（严重）”；翻不成大白话的只计数，其中有严重的要单独说（11-18 用户：确认框列技术条目看不懂、严重的没标）。
    返回（大白话列表, 没翻成的件数, 没翻成的里有没有严重的）。"""
    from collab.flow import _plain, _plain_enough
    words, hidden, severe_hidden = [], 0, False
    for problem in problems:
        if not isinstance(problem, dict):
            continue
        word = _plain(str(problem.get("现象") or ""))
        if word and _plain_enough(word):
            words.append(word + ("（严重）" if problem.get("级别") == "严重" else ""))
        else:
            hidden += 1
            severe_hidden = severe_hidden or problem.get("级别") == "严重"
    return words, hidden, severe_hidden


def _hidden_words(hidden: int, severe_hidden: bool) -> str:
    if not hidden:
        return ""
    return f"（另有 {hidden} 件没写成大白话{'，其中有严重的' if severe_hidden else ''}，原话在右边“交接”里）"


def _severe_advice(items: list) -> str:
    """有严重或丢数据的问题：先说这个、说清该谁动（右栏显示小修 2c，CARD-019：先说“测试写错”盖住了严重问题，
    用户差点照着点）。测试写错只附带一提。没有严重、也没丢数据就返回空。"""
    loss = any(p.get("类型") == "安全或丢数据" for p in items)
    if not loss and not any(p.get("级别") == "严重" for p in items):
        return ""
    what = "严重问题" + ("（含丢数据或安全问题）" if loss else "")
    tests = sum(1 for p in items if p.get("类型") == "测试写错")
    side = f"其中 {tests} 件是测试写错。" if tests else ""
    return (f"判断：自动退回用完了，还有{what}没解决，不能当成小问题。{side}"
            "建议：交给 Claude 接手（告诉 Claude 卡号）；或点“不满意，继续改”，把上面的问题讲清楚再跑一轮")


def _stop_reason_list(data: dict, cdir: Path = None, back_problems: list = None) -> str:
    """退回用完停下时按清单判断：有严重问题绝不说成小问题，也不认正文里随手写的轻微。"""
    items = list(back_problems) if back_problems is not None else _return_problems(data)
    severe_said = _severe_advice(items)
    if severe_said:
        tests_left = any(p.get("类型") == "测试写错" for p in items)
        hint = _stop_reason(cdir, hints_only=True, has_tests=tests_left) if cdir is not None else ""
        if hint:   # 反复出现（换模型）、范围外（写明要多改哪个文件）这两条原有提示附在后面不丢（Codex 复查 11-20）
            severe_said += "。另外，" + hint
        return severe_said
    if any(p.get("类型") == "测试写错" for p in items):
        return ("判断：复查说测试本身写错了，写代码的怎么改都会被它卡住。"
                "建议：点“不满意，继续改”，写明让写测试的把哪条测试改对")
    if items and all(p.get("级别") == "轻微" for p in items):
        return "判断：剩下的都是小问题。建议：点“接受剩下的问题，进入待确认”"
    severe = any(p.get("级别") == "严重" for p in items)
    plain_reason = ("判断：还有严重问题没解决，不能当成小问题。建议：点“不满意，继续改”，把上面的问题讲清楚" if severe
                    else "判断：还有需要继续改的问题。建议：点“不满意，继续改”，把上面的问题讲清楚")
    if cdir is not None:   # 不是只剩小问题：沿用老判断看同一问题反复出现、范围外、每轮冒新问题（11-18 验收）
        said = _stop_reason(cdir)
        # 老判断读的是复查原话，可能没看到重写清单里的严重问题：清单说有严重，就不许说成小问题、也不许略过（11-18 复查 2）
        if "剩下的都是小问题" in said or "测试本身写错" in said \
                or (severe and "严重" not in said and "反复出现" not in said):
            return plain_reason   # 清单里没有“测试写错”，老判断从正文里看到的不算（11-18 复查 2-2）
        return said
    if severe:
        return "判断：还有严重问题没解决，不能当成小问题。建议：点“不满意，继续改”，把上面的问题讲清楚"
    return "判断：还有需要继续改的问题。建议：点“不满意，继续改”，把上面的问题讲清楚"


_GUARD_BACKUP_DIR = "派测前备份"


def _latest_review_seq(cdir: Path) -> int:
    return max((int(e.get("seq") or 0) for e in read_flow(cdir)
                if e.get("kind") == "复查结论" or (e.get("kind") == "回复" and e.get("src") == "复查员")), default=0)


def _load_guard_backup(guard_dir: Path, review_seq: int):
    manifest_path = guard_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, dict) or manifest.get("review_seq") != review_seq:
        return None
    before = manifest.get("before")
    old_tests = manifest.get("old_tests")
    if not isinstance(before, dict) or not isinstance(old_tests, list):
        return None
    return {
        "before": before,
        "old_tests": [str(p) for p in old_tests],
        "has_snapshot": bool(manifest.get("has_snapshot")),
        "has_test_files": bool(manifest.get("has_test_files")),
    }


def _copy_if_file(src: Path, dest: Path) -> None:
    if src.is_file() and not src.is_symlink():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _save_guard_backup(guard_dir: Path, cdir: Path, worktree, review_seq: int, before: dict, old_tests: list) -> dict:
    """派改测/补测前把工作区文件、测试名单和主测试快照一起存进回滚目录，收尾完成前绝不丢掉派活前基线。"""
    shutil.rmtree(guard_dir, ignore_errors=True)
    files_dir = guard_dir / "files"
    before_tests_dir = guard_dir / "before_tests"
    old_tests_dir = guard_dir / "old_tests"
    files_dir.mkdir(parents=True, exist_ok=True)
    root = Path(worktree)
    for rel, digest in before.items():
        if digest is None:
            continue
        target_dir = before_tests_dir if is_test_path(rel) else files_dir
        _copy_if_file(root / rel, target_dir / rel)
    for rel in old_tests:
        _copy_if_file(root / rel, old_tests_dir / rel)
    snap_dir = cdir / _TEST_SNAPSHOT
    has_snapshot = snap_dir.is_dir()
    if has_snapshot:
        shutil.copytree(snap_dir, guard_dir / "snapshot")
    has_test_files = (cdir / _TEST_FILES).is_file()
    manifest = {
        "review_seq": review_seq,
        "before": before,
        "old_tests": list(old_tests),
        "has_snapshot": has_snapshot,
        "has_test_files": has_test_files,
    }
    (guard_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _not_card_data(worktree, cdir: Path, paths) -> list:
    """卡片记录目录（.collab）落在项目里、又没被忽略时，它不是谁改的代码：备份、还原都不碰它。
    不然补测前的备份会把备份目录自己也备份进去，一层套一层无限复制，全部测试卡死（11-18 C1.5 验收）。"""
    data = Path(cdir).resolve().parents[1]
    root = Path(worktree).resolve()
    out = []
    for rel in paths:
        full = (root / rel).resolve()
        if full != data and data not in full.parents:
            out.append(rel)
    return out


def _restore_guard_non_tests(worktree, guard_dir: Path, before: dict, revert_fn) -> None:
    root = Path(worktree)
    files_dir = guard_dir / "files"
    after = _not_card_data(worktree, guard_dir.parents[1],
                           _expand_to_files(worktree, [p for p in _changed_list(worktree) if not is_cache(p)]))
    for rel in sorted(set(before) | set(after)):
        if is_test_path(rel):
            continue
        if rel in before and _file_digest(root / rel) == before[rel]:
            continue  # 写代码的改过、它没再动（不在 before 里的都是它新动的，删掉也算，第 2 轮 B 第 1 条）
        dest = root / rel
        if rel in before and before[rel] is None:     # 写代码的删掉的，它又建回来：照旧删掉
            if dest.is_file() or dest.is_symlink():
                dest.unlink()
        elif rel in before and (files_dir / rel).is_file():  # 写代码的改过的（它改回原版也算）：还原
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(files_dir / rel, dest)
        else:                                          # 原来没动过的：恢复成提交时的样子
            revert_fn(worktree, [rel])


def _restore_guard_tests(cdir: Path, worktree, guard_dir: Path, manifest: dict, revert_fn) -> None:
    root = Path(worktree)
    before = manifest["before"]
    old_tests = manifest["old_tests"]
    snap_dir = cdir / _TEST_SNAPSHOT
    shutil.rmtree(snap_dir, ignore_errors=True)
    if manifest.get("has_snapshot") and (guard_dir / "snapshot").is_dir():
        shutil.copytree(guard_dir / "snapshot", snap_dir)
    test_list_path = cdir / _TEST_FILES
    if manifest.get("has_test_files"):
        _save_test_files(cdir, old_tests)
    elif test_list_path.is_file():
        test_list_path.unlink()
    _restore_test_snapshot(cdir, worktree)  # 先还原派活前的主测试快照，不把半成品锁进去
    for rel in old_tests:
        backup = guard_dir / "old_tests" / rel
        if backup.is_file():
            dest = root / rel
            try:
                same = dest.is_file() and not dest.is_symlink() and dest.read_bytes() == backup.read_bytes()
            except OSError:
                same = False
            if not same:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, dest)
    for rel, digest in before.items():
        if not is_test_path(rel):
            continue
        dest = root / rel
        if digest is None:
            if dest.is_file() or dest.is_symlink():
                dest.unlink()
        else:
            backup = guard_dir / "before_tests" / rel
            if backup.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, dest)
    for rel in _not_card_data(worktree, cdir, _changed_list(worktree)):   # 这次它动过的测试：原有的恢复，新建的去掉（第 2 轮 B2、第 3 轮 B1）；卡片记录不算（11-18 复查 3）
        if not is_test_path(rel) or rel in before or rel in old_tests:
            continue
        if _tracked(worktree, rel):
            revert_fn(worktree, [rel])
        elif (root / rel).is_file():
            (root / rel).unlink()


def _tester_work(ask_fn, cdir: Path, adapter: str, worktree, models, round_no: int, test_command, revert_fn,
                 step: str, prompt: str, with_result: bool = False):
    """派写测试的干一件只改测试的活（纠正测试、补测试共用这一套）：
    派活前把基线持久存进卡片目录；写了测试以外的文件一律还原成第一次派活前的样子
    （写代码的成果不能被它动：改回原版、删掉、建回来都算，中断续跑也不覆盖基线）；
    它没交成，测试也退回改之前的样子，不把半成品锁进去。交成了记完测试收尾才清理备份。"""
    root = Path(worktree)
    guard_dir = cdir / _GUARD_BACKUP_DIR / step
    review_seq = _latest_review_seq(cdir)
    loaded = _load_guard_backup(guard_dir, review_seq)
    if loaded is not None:
        before = {rel: loaded["before"][rel] for rel in _not_card_data(worktree, cdir, list(loaded["before"]))}
        old_tests = _not_card_data(worktree, cdir, loaded["old_tests"])
        manifest = dict(loaded, before=before, old_tests=old_tests)   # 老备份里混进的卡片记录：测试、非测试都不回滚（11-18 复查 2-1）
        # 上次在回复后、收尾前中断：先把上次残留的越界改动和未收尾测试/快照还原，再重派
        _restore_guard_non_tests(worktree, guard_dir, before, revert_fn)
        _restore_guard_tests(cdir, worktree, guard_dir, manifest, revert_fn)
    else:
        before = {}
        for rel in _not_card_data(worktree, cdir,
                                  _expand_to_files(worktree, [p for p in _changed_list(worktree) if not is_cache(p)])):
            before[rel] = _file_digest(root / rel)  # None：写代码的删掉了它
        old_tests = [p for p in _load_test_files(cdir)]
        manifest = _save_guard_backup(guard_dir, cdir, worktree, review_seq, before, old_tests)

    result = _dispatch(ask_fn, cdir, "测试员", adapter, step, prompt, worktree, models, round_no,
                       test_command=test_command)
    _restore_guard_non_tests(worktree, guard_dir, before, revert_fn)
    if not result.ok:
        _restore_guard_tests(cdir, worktree, guard_dir, manifest, revert_fn)
        shutil.rmtree(guard_dir, ignore_errors=True)
        return (False, result) if with_result else False
    # 测试文件名单按现在的样子重算：删掉的去掉，新建的加上；快照跟着重做（复查 B 第 2 条）
    tests = [p for p in dict.fromkeys(old_tests + [p for p in _not_card_data(worktree, cdir, _changed_list(worktree))
                                                   if is_test_path(p)])
             if (root / p).is_file()]
    _save_test_files(cdir, tests)
    _snapshot_test_files(cdir, worktree, tests)
    record(cdir, "测试收尾", src="程序", step=step, round=round_no,
           summary=f"{step}收尾完成：已还原越界改动、登记测试、更新快照")
    shutil.rmtree(guard_dir, ignore_errors=True)
    return (True, result) if with_result else True


def _tester_fix(ask_fn, cdir: Path, adapter: str, items: list, needs, worktree, models, round_no: int,
                test_command, revert_fn) -> str:
    """复查说测试本身写错了（㊻）：先派写测试的只改测试，再让写代码的照新测试改。"""
    listed = "\n\n".join(f"{i + 1}. {t.replace(TEST_PROBLEM, '').strip()}" for i, t in enumerate(items))
    prompt = (f"{_rules('写测试')}复查员发现测试本身写错了（测试和任务卡 {needs}、施工单对不上），下面这几条：\n\n"
              f"{listed}\n\n只改测试，让测试和任务卡、施工单一致；不改产品代码，改了也会被撤掉。"
              f"{_run_hint(test_command) if test_command else ''}")
    ok = _tester_work(ask_fn, cdir, adapter, worktree, models, round_no, test_command, revert_fn,
                      "改测试", prompt)
    if not ok:
        return "\n\n复查说有几条是测试写错了，写测试的这次没改成；这几条先别迁就测试，交活时说明。"
    return (f"\n\n复查里标着{TEST_PROBLEM}的几条，测试已由写测试的改好了，照新测试改代码，"
            f"不要为了迁就旧测试去改规矩。")


def _guard_tests(ask_fn, cdir: Path, adapter: str, items: list, needs, worktree, models, round_no: int,
                 test_command, revert_fn, judge_fn=None, red_check: bool = False, already: bool = False) -> tuple:
    """复查退回的产品问题（第二批 A4）：先派写测试的补自动化测试守住，再让写代码的改。
    返回（是否补成，附给写代码的交代）。没补成只记流转，不卡住跑卡。"""
    if already:
        # 接着跑：补测试已经交过，不重派；先红检查没跑就补跑，有结果就沿用
        return True, (_supplement_red_note(cdir, test_command, judge_fn, worktree) if red_check else "")
    listed = "\n\n".join(f"{i + 1}. {t}" for i, t in enumerate(items))
    prompt = (f"{_rules('写测试')}按任务卡 {needs} 的验收标准，下面这几条是复查退回、要改产品代码的问题：\n\n"
              f"{listed}\n\n请针对每条问题各编写自动化测试（一条问题对应一条），写进新的测试文件；"
              f"测试要能抓住这个问题（现在应该不过）。"
              f"只改测试文件，不改产品代码，改了也会被撤掉。{_run_hint(test_command) if test_command else ''}")
    ok = _tester_work(ask_fn, cdir, adapter, worktree, models, round_no, test_command, revert_fn,
                      "补测试", prompt)
    if not ok:
        record(cdir, "补测试", src="程序", ok=False,
               summary="补测试没成功：写测试的没交成，先按复查意见改代码")
        return False, "\n\n补测试没成功，这几条先按复查意见改，别当成已经有测试守住。"
    if red_check and judge_fn is not None and test_command:
        return True, _supplement_red_note(cdir, test_command, judge_fn, worktree) or _GUARD_USUAL_NOTE
    return True, _GUARD_USUAL_NOTE


def _tester_step_done(cdir: Path, step: str) -> bool:
    """最近一次复查退回之后，写测试的有没有把这个步骤交成（中断续跑时用来判断补测是否已完成）。"""
    events = read_flow(cdir)
    conclusion = _latest_review_conclusion(cdir)
    if conclusion is not None:
        last_review = int(conclusion.get("seq") or 0)
    else:
        last_review = max((int(e.get("seq") or 0) for e in events
                           if e.get("kind") == "回复" and e.get("src") == "复查员"), default=0)
    replies = [e for e in events if int(e.get("seq") or 0) > last_review and e.get("kind") == "回复"
               and e.get("src") == "测试员" and e.get("ok") is True and e.get("step") == step]
    if not replies:
        return False
    reply_seq = int(replies[-1].get("seq") or 0)
    return any(int(e.get("seq") or 0) > reply_seq and e.get("kind") == "测试收尾"
               and e.get("step") == step for e in events)


RED_CHECK_GREEN = "还没改代码，本卡测试一跑就全过"
RED_CHECK_RED = "还没改代码，本卡测试没过（正常）"
_GUARD_GREEN_NOTE = ("\n\n写测试的补了测试，但在改代码前一跑就全过，没抓住这几条问题；"
                     "别当成已经有测试守住，改完在汇报里写明怎么确认改好了。")
_GUARD_USUAL_NOTE = "\n\n写测试的已经补了测试守住这几条，跑本卡测试要全部通过。"


def _red_check_one(cdir: Path, step: str, test_command, judge_fn, worktree, reply_seq=None):
    """跑一次本卡测试并记“先红检查”；返回判定结果。没过时把没过的测试记成名单（进度条只数这几项）。
    真跑时本卡命令按段分开跑、输出全留（collab.progress.run_segments，和进度条同一套），名单才不漏；
    有一段超时或跑不起来，这次不写名单（D2 复查第 1、2 轮，Claude 接手：不拿半截名单去猜）。"""
    record(cdir, "先红开始", src="程序", step=step,
           summary="写代码前先跑一遍新测试，确认它们现在真的不过", reply_to=reply_seq)
    shell = ""
    if isinstance(test_command, (list, tuple)):
        parts = [str(part) for part in test_command]
        if parts and parts[0].endswith("bash") and "-c" in parts:
            at = parts.index("-c")
            shell = parts[at + 1] if at + 1 < len(parts) else ""
    elif isinstance(test_command, str):
        shell = test_command
    verdict = None
    sure = judge_fn is not default_judge   # 测试里注入的假判定：照它的输出认名字
    if judge_fn is default_judge and shell.strip():
        from collab.progress import run_segments
        verdict = run_segments(worktree, shell, _TEST_TIMEOUT)
        sure = verdict is not None   # 拆得开、每段都单独跑完才有把握；拆不开退回整条跑，输出可能被截断
    if verdict is None:
        verdict = judge_fn(test_command, cwd=worktree, timeout=_TEST_TIMEOUT)
    if verdict.result == PASS:
        summary = RED_CHECK_GREEN
    elif verdict.result == FAIL:
        summary = RED_CHECK_RED
        from collab.progress import failing_names
        names = failing_names(verdict.output)
        # 只有完全有把握才写名单：拆得开、每段跑完、没有一导入就报错的占位项、认出了名字（D2 复查第 3 轮）
        if sure and names and "unittest.loader._FailedTest" not in (verdict.output or ""):
            try:
                (Path(cdir) / "开工前没过的测试.json").write_text(
                    json.dumps({"tests": names, "step": step}, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
    else:
        summary = f"还没改代码，本卡测试没跑成：{verdict.result}（交给后面的判定）"
    record(cdir, "先红检查", src="程序", step=step, result=verdict.result,
           summary=summary, reply_to=reply_seq)
    return verdict


def _tester_red_reply(events: list):
    """最近一次真正交完的“写测试”或“重写测试”。重写要看测试收尾，免得刚记下回复、还没还原越界改动就往下一步走。"""
    usable = []
    for ev in events:
        if ev.get("kind") != "回复" or ev.get("src") != "测试员" or ev.get("ok") is not True:
            continue
        step = ev.get("step")
        if step not in ("写测试", "重写测试"):
            continue
        seq = int(ev.get("seq") or 0)
        if step == "重写测试" and not any(
                int(e.get("seq") or 0) > seq and e.get("kind") == "测试收尾" and e.get("step") == step
                for e in events):
            continue
        usable.append(ev)
    return usable[-1] if usable else None


def _checks_for_reply(events: list, reply: dict, seq: int) -> list:
    """只认这次交活自己的检查记录：补测试的检查不能当成写测试的（复查第四轮 1）。"""
    step = reply.get("step")
    out = []
    for ev in events:
        if ev.get("kind") != "先红检查" or int(ev.get("seq") or 0) <= seq:
            continue
        if ev.get("step") != step:
            continue
        target = ev.get("reply_to")
        if target is not None and int(target or 0) != seq:
            continue
        out.append(ev)
    return out


def _red_check_after_tests(collab: Path, cdir: Path, card: Card, *, ask_fn, test_adapter: str, test_models,
                           worktree, needs, test_command, revert_fn, judge_fn, require_tests: bool = True):
    """写测试交活后先跑本卡测试：一跑就全过退回重写一次，再全过停下交把关的人。
    第一次跑和接着跑共用；不需要停下/交活失败时返回 None，继续原来的流程。"""
    while True:
        events = _after_revise(read_flow(cdir))
        reply = _tester_red_reply(events)
        if reply is None:
            return None
        seq = int(reply.get("seq") or 0)
        after = [e for e in events if int(e.get("seq") or 0) > seq]
        if any(e.get("kind") == "转人工" and e.get("cause") == "测试先红" for e in after):
            return None   # 把关的人已经点过“接着跑”
        if any(e.get("kind") == "派活" and e.get("dst") == "开发员" for e in after):
            return None   # 已经派过写代码的（不管交没交成、超没超时）：副本可能改过了，不能补做“改代码前”的检查
        if require_tests and not _load_test_files(cdir):
            _escalate(collab, cdir, card,
                      "写测试的交活后本卡没有测试了，没法确认测试能不能抓住问题，需要把关的人看")
            return card, None
        checks = _checks_for_reply(after, reply, seq)
        if not checks:
            _red_check_one(cdir, reply.get("step"), test_command, judge_fn, worktree, seq)
            events = _after_revise(read_flow(cdir))
            checks = _checks_for_reply(events, reply, seq)
        if not checks or checks[-1].get("result") != PASS:
            return None
        green = [e for e in events if e.get("kind") == "先红检查" and e.get("result") == PASS
                 and e.get("step") in ("写测试", "重写测试")]
        if len(green) >= 2:
            _escalate(
                collab, cdir, card,
                "写测试的两次交的测试一跑就全过（还没改代码），抓不到问题，需要把关的人看："
                "测试是不是写松了，或者现有代码已经做到了",
                cause="测试先红",
            )
            return card, None
        prompt = (f"{_rules('写测试')}你写的测试在还没改代码时一跑就全过，说明抓不到问题；"
                  f"也可能是新测试不在本卡测试命令跑的范围里（测试命令：{_command_text(test_command)}）。"
                  f"按任务卡 {needs} 的验收标准把测试改成现在会不过、等写代码的做完才会过的样子；"
                  f"现有代码确实已经做到的那条，在汇报里写明是哪条。只改测试文件，不改产品代码，"
                  f"改了也会被撤掉。{_run_hint(test_command)}")
        ok, result = _tester_work(ask_fn, cdir, test_adapter, worktree, test_models, 1, test_command,
                                  revert_fn, "重写测试", prompt, with_result=True)
        if not ok:
            return _worker_failed(collab, cdir, card, "测试员", test_adapter, result)


def _supplement_red_note(cdir: Path, test_command, judge_fn, worktree) -> str:
    """补测试之后那一次先红检查的交代；没有可沿用的结果又该查时补记一次。"""
    events = _after_revise(read_flow(cdir))
    replies = [e for e in events if e.get("kind") == "回复" and e.get("src") == "测试员"
               and e.get("ok") is True and e.get("step") == "补测试"]
    if not replies:
        return ""
    seq = int(replies[-1].get("seq") or 0)
    checks = [e for e in events if int(e.get("seq") or 0) > seq
              and e.get("kind") == "先红检查" and e.get("step") == "补测试"]
    if not checks:
        if not test_command or judge_fn is None:
            return ""
        _red_check_one(cdir, "补测试", test_command, judge_fn, worktree, seq)
        checks = [e for e in _after_revise(read_flow(cdir)) if int(e.get("seq") or 0) > seq
                  and e.get("kind") == "先红检查" and e.get("step") == "补测试"]
    if not checks:
        return ""
    return _GUARD_GREEN_NOTE if checks[-1].get("result") == PASS else _GUARD_USUAL_NOTE


def _related_old_tests(worktree, cdir: Path, changed_fn) -> list:
    """用到这次改动的 .py 文件的老测试（第三批 ㉜）：只认 Python 项目 tests/ 下的 test_*.py，本卡自己的测试不算。
    认法：老测试里写了模块全名（pkg.money）、from pkg import money、或 import money。挑漏的靠最后全量兜底。"""
    root = Path(worktree)
    tests_dir = root / "tests"
    if not tests_dir.is_dir():
        return []
    try:
        every = [p for p in changed_fn(worktree) if not is_test_path(p) and not is_cache(p)]
    except Exception:  # noqa: BLE001 — 看不出改了什么就不跑
        return []
    changed = [p for p in every if p.endswith(".py")]
    others = [p for p in every if not p.endswith(".py")]
    if not changed and not others:
        return []
    mine = set(_load_test_files(cdir))
    pats = [_name_pattern(Path(p).name) for p in others]  # 页面这类文件按文件名认（第四轮 59）
    helpers = []
    for rel in changed:
        mod = rel[:-3].replace("/", ".")
        if mod.endswith(".__init__"):
            mod = mod[:-len(".__init__")]
        pkg, _, stem = mod.rpartition(".")
        pats.append(re.compile(rf"(?<![\w.]){re.escape(mod)}(?![\w])"))
        if pkg:
            pats.append(re.compile(rf"from\s+{re.escape(pkg)}\s+import\s+[^\n]*(?<![\w]){re.escape(stem)}(?![\w])"))
        else:
            pats.append(re.compile(rf"^\s*(?:import|from)\s+{re.escape(stem)}(?![\w])", re.M))
    texts = {}
    for path in sorted(tests_dir.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or is_cache(rel) or path.stat().st_size > 2_000_000:
            continue
        try:
            texts[rel] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    # 本卡自己的测试文件只在最后排除：它们列着的辅助脚本照样要顺着找（复查：辅助脚本被本卡列入后依赖链断了）
    found = [rel for rel, text in texts.items() if _is_test_file(rel) and any(p.search(text) for p in pats)]
    if others:
        # 辅助脚本（测试用的 .js、不是 test_ 开头的 .py）提到了改动的页面文件：再找用到这个辅助脚本的测试（68c 那层）
        helpers = [rel for rel, text in texts.items() if not _is_test_file(rel)
                   and any(p.search(text) for p in pats[:len(others)])]
        hp = [_name_pattern(Path(h).name) for h in helpers]
        found += [rel for rel, text in texts.items() if _is_test_file(rel) and rel not in found
                  and any(p.search(text) for p in hp)]
    return sorted(f for f in found if f not in mine)  # 不截断：挑出来多少跑多少（复查第 6 条）


def _is_test_file(rel: str) -> bool:
    return Path(rel).name.startswith("test_") and rel.endswith(".py")


def _name_pattern(name: str):
    """整个文件名才算提到：abilities_core.js 不算提到 abilities.js。"""
    return re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])")


def _old_tests_command(olds: list) -> list:
    """每个老测试按它自己所在的文件夹跑：子文件夹没有 __init__.py 也能跑到（复查第 7 条）。"""
    parts = []
    for rel in olds:
        p = Path(rel)
        parts.append(f"python3 -m unittest discover -s {shlex.quote(str(p.parent))} -p {shlex.quote(p.name)} || exit 1")
    return ["/bin/bash", "-c", "; ".join(parts)]


def _failing(output: str):
    """unittest 输出里没过的那几项（FAIL:/ERROR: 行）和总数（FAILED (failures=N, errors=M)）。认不出返回 (None, None)。"""
    names = set(re.findall(r"^(?:FAIL|ERROR): (\S+ \([^)]*\))", output or "", re.M))
    m = re.search(r"FAILED \(([^)]*)\)", output or "")
    total = sum(int(n) for n in re.findall(r"(?:failures|errors)=(\d+)", m.group(1))) if m else None
    return (names or None), total


def _more_failures(now_out: str, base_out: str) -> bool:
    """这次比改之前多坏了：多出没过的项，或没过的总数变多。两边都认不出时不怪这次（照旧算改之前就不过）。"""
    now_names, now_total = _failing(now_out)
    base_names, base_total = _failing(base_out)
    if now_names is not None and base_names is not None and now_names - base_names:
        return True
    return now_total is not None and base_total is not None and now_total > base_total


def _broken_by_change(worktree, olds: list, judge_fn) -> list:
    """一批老测试没过时，逐个看是谁被这次改坏了（第三批复查第 2 轮：整批比会把新改坏的一起放过）：
    现在不过、而在改之前（施工副本提交上的样子）不是“没过”的 → 算这次改坏的（改之前超时、环境出错不算本来就不过）。
    看不出来（不是 git、检出失败）就当改之前是过的。"""
    import tempfile
    now = {o: judge_fn(_old_tests_command([o]), cwd=worktree, timeout=_TEST_TIMEOUT) for o in olds}
    now_bad = [o for o, v in now.items() if v.result != PASS]
    if not now_bad:
        return []
    tmp = tempfile.mkdtemp(prefix="collab-base-")
    try:
        add = subprocess.run(["git", "worktree", "add", "-q", "--detach", tmp, "HEAD"], cwd=str(worktree),
                             capture_output=True, text=True)
        if add.returncode != 0:
            return now_bad
        culprits = []
        for o in now_bad:
            base = judge_fn(_old_tests_command([o]), cwd=tmp, timeout=_TEST_TIMEOUT)
            # 改之前过、超时、环境出错 → 算这次改坏的；改之前也没过 → 看这次是不是多坏了几项（同一文件里新改坏的，第 3 轮第 1 条）
            if base.result != FAIL or _more_failures(now[o].output, base.output):
                culprits.append(o)
        return culprits
    except Exception:  # noqa: BLE001
        return now_bad
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", tmp], cwd=str(worktree), capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
        subprocess.run(["git", "worktree", "prune"], cwd=str(worktree), capture_output=True)


_NOW_TAG = "【这次改的】"
_OLD_TAG = "【原来就有的】"
_TRIAGE_NOTE = (f"每条在级别后面再标{_NOW_TAG}或{_OLD_TAG}。"
                f"{_NOW_TAG}＝问题出在这张卡改动的代码里（和开工前的样子比，用 git diff 看），包括上次复查提的没改好的；"
                f"{_OLD_TAG}＝开工前项目里就有、不是这张卡改出来的。拿不准的标{_NOW_TAG}。")


def _single_line_phen(problem: dict) -> str:
    return " ".join(str(problem.get("现象") or "").splitlines()).strip()


def _review_prompt(cdir: Path, needs, plan_hint: str, run_hint: str, worktree, later_round: bool) -> str:
    """每一轮复查的交代：首行结论 + 末尾固定清单；老测试变动和新跳过每轮都提醒。"""
    changed = _old_tests_changed_note(cdir, worktree)
    skips = _new_skips_note(worktree)
    prev_reject = _last_reject_conclusion(cdir)
    prev_items = _conclusion_back_problems(prev_reject) if prev_reject is not None else []
    check_back_note = ""
    if prev_items:
        listed = "\n".join(f"{i}. {_single_line_phen(p)}" for i, p in enumerate(prev_items, 1))
        check_back_note = (
            f"\n\n上轮退回的问题（按编号）：\n{listed}\n\n"
            f"请在复查清单里加上“上轮核对”列表（每项写 {{\"编号\": 数字, \"结果\": \"改好了\" 或 \"没改好\"}}），"
            f"按上面的编号逐条回答。"
        )
    return (f"{_rules('复查')}只读复查：对照任务卡 {needs} 检查本次改动是否符合需求和验收标准。"
            f"{plan_hint}{run_hint}"
            f"{_tester_files_hint(cdir)}"
            f"{changed}{skips}"
            f"台账、归档这些流程记录由程序在施工结束后补写，现在没有不算问题。"
            f"第一行写“结论：通过”或“结论：不通过”。"
            f"回复末尾附“复查清单：”加一个 ```json 代码块，写清“结论”和“问题”列表；每条问题写齐七个字段："
            f"级别、来源、类型、碰上、现象、位置、证据。"
            f"级别写【严重】【中等】【轻微】；"
            f"来源写【这次改的】【原来就有的】；类型写“功能问题”或"
            f"“测试写错”（测试本身写错时，那一条开头写“{TEST_PROBLEM}”）或“安全或丢数据”；"
            f"“碰上”写“常见”“少见”或“极巧合”，每条都要填；级别、类型、碰上怎么判，照上面规则里的《复查分级》。"
            f"第一句用大白话写用户能看到的现象（不带文件名、代码名），后面再写原因和位置。"
            f"{_TRIAGE_NOTE}"
            f"证据写“方式”：“实跑”要写命令和输出，“推演”要写对照的文件:行号（带扩展名）。"
            f"{'这是退回修改后的复查：只看上一次复查以后改动的地方和它直接牵连的地方，其他部分上一轮已经复查过，不用重看。' if later_round else ''}"
            f"{check_back_note}"
            f"不要修改任何文件。")


def _reformat_prompt(cdir: Path, original: str, errors: list, needs, worktree) -> str:
    """格式不合格：只请复查员按固定格式重写一份清单，不用重新复查。"""
    why = "\n".join(f"- {e}" for e in errors)
    prev_reject = _last_reject_conclusion(cdir)
    prev_items = _conclusion_back_problems(prev_reject) if prev_reject is not None else []
    check_note = ""
    if prev_items:
        listed = "\n".join(f"{i}. {_single_line_phen(p)}" for i, p in enumerate(prev_items, 1))
        check_note = f"\n\n上轮退回的问题（按编号，共 {len(prev_items)} 条）：\n{listed}\n"
    return (f"{_rules('复查')}按固定格式重写：你上一次的复查回复没有合格的复查清单，不用重新复查，"
            f"只把下面这份原回复补成固定格式。\n\n原回复全文：\n{original}\n\n"
            f"不合格原因：\n{why}{check_note}\n\n"
            f"请保留原来的结论、意见和上轮核对结果，在回复末尾附“复查清单：”加一个 ```json 代码块："
            f"“结论”只能是“通过”或“不通过”；若有上轮核对结果，在清单里保留“上轮核对”列表"
            f"（每项写 {{\"编号\": 数字, \"结果\": \"改好了\" 或 \"没改好\"}}）；"
            f"每条问题写齐级别、来源、类型、碰上、现象、位置、证据。"
            f"级别写【严重】【中等】【轻微】；来源写【这次改的】【原来就有的】；类型写“功能问题”“测试写错”或“安全或丢数据”；"
            f"碰上写“常见”“少见”或“极巧合”。"
            f"证据写“方式”：“实跑”要写命令和输出，“推演”要写对照的文件:行号（带扩展名）。"
            f"任务卡：{needs}。不要修改任何文件。")


def _lead_tags(item: str) -> list:
    """条目开头连着的几个【…】（级别、这次改的/原来就有的、测试的问题）；正文里引用的不算（复查第 2 轮）。"""
    m = re.match(r"\s*((?:【[^】]{1,12}】\s*)+)", item)
    return re.findall(r"【([^】]{1,12})】", m.group(1)) if m else []


def _level(item: str) -> str:
    tags = _lead_tags(item)
    for word in ("严重", "轻微", "中等"):
        if word in tags:
            return word
    return "中等"   # 没标级别的按中等算，宁可多修


def _triage(items: list) -> tuple:
    """复查收口（第二批 A1（11-18））：这次改出来的（不分轻重）、测试本身的问题、严重问题一律退回；
    只有明确标了“原来就有的”、不是严重、也不是测试问题的，才记进“以后再修”。"""
    back, later = [], []
    for item in items:
        tags = _lead_tags(item)
        level = _level(item)
        if TEST_PROBLEM.strip("【】") in tags or level == "严重" or _OLD_TAG.strip("【】") not in tags:
            back.append(item)
        else:
            later.append(item)
    return back, later


_EMBEDDED = re.compile(r"\n\s*(?:[-*•]\s*|\d+[.、)]\s*)?【(?:严重|中等|轻微|测试的问题)】")

_ISSUE_NUMBER = re.compile(r"^\s*\d+[.、)）]\s*(.+)$")
_ISSUE_LABEL = re.compile(r"^\s*(?:原因|位置|影响|建议|复现|修法|为什么|现象|依据|改法|出处)\s*[：:]")
_ISSUE_TRAILER = re.compile(r"指定测试|测试命令|共\s*\d+\s*项|全部通过|未修改|未跑全量|核对表|"
                            r"^\s*验证\s*[：:]|^\s*\*\*[^*]+\*\*\s*$")
_ISSUE_LEAD = re.compile(r"^\s*(?:[-*•]\s*|\d+[.、)）]\s*)?((?:【[^】]{1,12}】\s*)+)")
_ISSUE_WORDS = ("严重", "中等", "轻微", TEST_PROBLEM.strip("【】"))


def _new_issue_line(line: str) -> str:
    """这一行本身是不是一条新问题的开头：去掉行首编号或项目符号后，紧跟级别或“测试的问题”标签。
    正文里引用标签的不算。是就返回去掉符号后的正文，不是返回空。
    条目解析和漏项检查都只走这一条判断，不再各认各的。"""
    m = _ISSUE_LEAD.match(line or "")
    if not m:
        return ""
    tags = re.findall(r"【([^】]{1,12})】", m.group(1))
    if not any(t in _ISSUE_WORDS for t in tags):
        return ""
    return re.sub(r"^\s*(?:[-*•]\s*|\d+[.、)）]\s*)", "", line).strip()


def _review_items(text: str) -> list:
    """复查不通过要改的条目（第四轮 54）：编号的几条，加上编号以外没编号的问题段落，按原文顺序。
    没编号的不再因为“编号的已经认出来了”就被丢掉；一段都认不出来时，整段也算一件。"""
    entries = []   # [(原文行号, 文本)]，最后按行号排，编号和没编号的都不乱序
    state = ""     # open=正在写一条编号条目，blank=这条后面空过行
    block, block_at = [], 0

    def flush():
        nonlocal block
        if block:
            entries.append((block_at, "\n".join(block).strip()))
            block = []

    for i, line in enumerate((text or "").splitlines()):
        m = _ISSUE_NUMBER.match(line)
        if m:
            flush()
            entries.append((i, m.group(1).strip()))
            state = "open"
            continue
        if not line.strip():
            flush()
            state = "blank" if state else ""
            continue
        if _REVIEW_VERDICT.match(line):
            flush()
            continue
        new_issue = _new_issue_line(line)
        marked = bool(re.match(r"^\s*(?:[-*•]\s*|\d+[.、)）]\s*)", line))
        if new_issue and (marked or state != "open"):
            # 带项目符号的一律是新的一条；顶格直接写标签的，只有没贴着编号条目时才算新的一条。
            # 紧贴编号条目的裸标签行仍按“没写清楚”处理，由漏项检查整段退回，不能悄悄并进上一条。
            flush()
            entries.append((i, new_issue))
            state = "open"
            continue
        indented = line[:1] in (" ", "\t", "　") or line.startswith(("- ", "* ", "• "))
        if state and (indented or _ISSUE_LABEL.match(line)):
            entries[-1] = (entries[-1][0], entries[-1][1] + "\n" + line.rstrip())
            continue
        if _ISSUE_TRAILER.search(line):
            state = ""
            continue
        if state == "open":
            entries[-1] = (entries[-1][0], entries[-1][1] + "\n" + line.rstrip())
            continue
        if not block:
            block_at = i
        block.append(line.rstrip())
    flush()
    return [t for _, t in sorted(entries, key=lambda e: e[0]) if t.strip()]


def _unparsed_serious(text: str, items: list) -> bool:
    """复查原文没认全：条目外还剩没编号的话（不管写没写级别），或者某一条里另起一行又是一条带级别的问题
    （编号和项目符号混排时会被吞进上一条，复查第 2、3 轮）。认不全就不能自动收口，也要把原文整段交给写代码的。"""
    # 兜底（CARD-003 第 4 轮，观察第 35 条）：不管复查员用加粗、标题、引用还是别的写法，原文里的级别标签
    # 比认出来的条目开头的级别标签多，就算没认全。正文引用标签也会算进来，宁可整段退回，不能漏放。
    said = len(re.findall(r"【(?:严重|中等|轻微)】", text or ""))
    seen = sum(1 for item in items for t in _lead_tags(item) if t in ("严重", "中等", "轻微"))
    if said > seen:
        return True
    if any(_EMBEDDED.search(item) for item in items):
        return True
    rest = text or ""
    for item in items:
        for line in item.splitlines():
            if line.strip():
                rest = rest.replace(line.strip(), "", 1)
    for line in rest.splitlines():
        s = line.strip()
        if not s or _REVIEW_VERDICT.match(s):
            continue
        if re.fullmatch(r"(?:\d+[.、)）]|[-*•])", s):   # 删掉条目正文后剩下的“1.”或项目符号，不是话
            continue
        if _new_issue_line(s):   # 条目外又是一条问题：不管有没有编号，都算没认全
            return True
        if _ISSUE_TRAILER.search(s):
            continue
        return True
    return False


def _loose_issue(text: str) -> str:
    """复查没编号时，把结论以外的话当成一件，补测清单不能因此为空。"""
    lines = [ln.strip() for ln in (text or "").splitlines()
             if ln.strip() and not _REVIEW_VERDICT.match(ln)]
    return "\n".join(lines).strip() or (text or "").strip()


def _fix_items(text: str) -> list:
    """复查不通过后要改的条目（续跑也走这里）：编号的和没编号的都在，保证补测清单一条不丢。"""
    items = _review_items(text)
    if not items:
        loose = _loose_issue(text)
        items = [loose] if loose else []
    back, _ = _triage(items)
    if _unparsed_serious(text, items):
        loose = _loose_issue(text)   # 认不全：整段原文当一件交过去，认出来的几条可能正好漏了要紧的那条
        back = [loose] if loose else items
    return [item for item in back if str(item).strip()]


def _later_words(later: list) -> list:
    """以后再修的问题整理成大白话（写进台账）：认得出就写，认不出就占个位，不能静默漏掉。"""
    out = []
    for t in later:
        clean = re.sub(r"^\s*(?:【[^】]{1,12}】\s*)+", "", str(t))
        words = plain_issues(f"1. {clean}")
        out.append(words[0] if words else "（有一件没写成大白话，原话在右边“交接”里）")
    return out


_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")


def _lower_effort(models):
    """退回后的复查降一档（第二批 A5）：只复制一份改，不动原配置；没设置或不认识的值保持原样。"""
    out = dict(models or {})
    effort = str(out.get("codex_effort") or "")
    if effort in _EFFORTS and _EFFORTS.index(effort) > 0:
        out["codex_effort"] = _EFFORTS[_EFFORTS.index(effort) - 1]
    return out


def _prior_reviews(cdir: Path) -> int:
    """这张卡之前有过几次复查结论（不管通过不通过）。"""
    return sum(1 for e in read_flow(cdir) if e.get("kind") == "复查结论")


def _closed_replies(cdir: Path) -> set:
    return {e.get("reply") for e in read_flow(cdir) if e.get("kind") == "以后再修" and e.get("closed")}


def _normalize_location_path(raw_path: str, prefix: str) -> str:
    path = raw_path.strip().replace("\\", "/")
    if prefix and path.startswith(prefix):
        path = path[len(prefix):]
    return path.lstrip("./").lstrip("/")


def _extract_locations(text: str, root=None) -> list:
    """从文本里提取（相对路径, 行号）；支持带空格目录和链接路径，保留完整相对路径不截断。"""
    src = str(text or "")
    prefix = (str(root).replace("\\", "/").rstrip("/") + "/") if root else ""
    found = []

    def add(raw_path: str, line: str):
        path = _normalize_location_path(raw_path, prefix)
        if path:
            found.append((path, line))

    for raw_path, line in re.findall(r"\]\(([^)\n]+?):(\d+)(?::\d+)?\)", src):
        add(raw_path, line)
    bare = re.sub(r"\[[^\]]*\]\([^)]*\)", " ", src)
    for raw_path, line in re.findall(r"`([^`\n]+?\.[A-Za-z][A-Za-z0-9]{0,7}):(\d+)(?::\d+)?`", bare):
        add(raw_path, line)
    bare = re.sub(r"`[^`\n]+`", " ", bare)
    for raw_path, line in _BARE_LOCATION.findall(bare):
        add(raw_path, line)
    return found


def _places(items: list, root=None) -> set:
    """复查写的位置（文件, 行）。有链接就用链接地址（显示文字不另算），路径按施工副本算成相对路径，不截断
    （复查第 2 轮：只留最后两段会把 apps/admin/src/index.js 和 apps/shop/src/index.js 当成一处）。"""
    out = set()
    for t in items:
        out.update(_extract_locations(t, root))
    return out


def _files_in(items: list, root=None) -> set:
    return {p for p, _ in _places(items, root)}


def _same_place_new(cdir: Path, now_items: list, root=None) -> list:
    """上一次退回和这一次在同一个文件里、但这次全是新问题：返回那些文件名（第四轮 57）。"""
    import difflib
    closed = _closed_replies(cdir)
    replies = [e for e in _after_revise(read_flow(cdir)) if e.get("kind") == "回复" and e.get("src") == "复查员"
               and e.get("ok") is True and e.get("seq") not in closed]
    kept = {e.get("reply"): e.get("back") for e in read_flow(cdir) if e.get("kind") == "以后再修" and e.get("back")}
    bodies = [kept.get(e.get("seq")) or text_of(cdir, e) or "" for e in replies]   # 收口后真正退回的那几条
    bodies = [b for b in bodies if review_verdict(b) == "reject"]
    if len(bodies) < 2:
        return []
    before = _numbered_items(bodies[-2], whole=True)
    shared = _files_in(before, root) & _files_in(now_items, root)
    if not shared or _places(before, root) & _places(now_items, root):   # 同一行又被提：是老问题没改好，不是新问题
        return []
    plain_now = [_plain_words(t) for t in now_items]
    plain_before = [_plain_words(t) for t in before]
    if any(difflib.SequenceMatcher(None, a, b).ratio() >= 0.6 for a in plain_now for b in plain_before):
        return []
    return sorted(shared)


def _place_of(problem: dict, root=None) -> tuple:
    """清单里的一条写的位置 →（相对路径, 行号）；保留目录（含带空格路径），不截成文件名，没写清楚返回空。"""
    locs = _extract_locations(str(problem.get("位置") or ""), root)
    return locs[0] if locs else ("", "")


def _same_place_new_list(cdir: Path, data: dict, current_reply: int, root=None, current_back=None) -> list:
    """连着两轮在同一处冒新问题（按清单里的完整相对路径比），和 C1 的提醒保持一致。"""
    prev_back = None
    for event in reversed(_after_revise(read_flow(cdir))):
        if event.get("kind") != "复查结论" or event.get("result") != "退回":
            continue
        if int(event.get("seq") or 0) >= int(current_reply or 0):
            continue
        items = _conclusion_back_problems(event)
        if items or (isinstance(event.get("清单"), dict) and isinstance(event["清单"].get("问题"), list)):
            prev_back = items
            break
    if prev_back is None:
        return []
    before = {_place_of(p, root) for p in prev_back}
    now_items = list(current_back) if current_back is not None else _return_problems(data)
    now = {_place_of(p, root) for p in now_items}
    before.discard(("", ""))
    now.discard(("", ""))
    if not before or not now or before & now:
        return []
    shared = {path for path, _ in before} & {path for path, _ in now}
    return sorted(shared)


def _plain_words(item: str) -> str:
    first = re.split(r"位置[:：]|原因[:：]|\[", item.split("\n")[0])[0]   # 只比现象那句，位置、原因不算
    return re.sub(r"【[^】]*】", "", first).strip()


def _top_effort(models, adapter: str) -> str:
    """所选模型支持的最高强度（复查第 2 轮：只提到 xhigh，gpt-6.1-sol 其实支持 ultra）；查不到用 xhigh。"""
    order = ["low", "medium", "high", "xhigh", "max", "ultra"]
    best = "xhigh"
    mine = str((models or {}).get("codex_effort") or "")
    if mine in order and order.index(mine) > order.index(best):
        best = mine   # 配得更高就用配的
    try:
        from collab.config import MODEL_OPTIONS
        model = str((models or {}).get(adapter) or "")
        for opt in MODEL_OPTIONS.get(adapter, []):
            if isinstance(opt, dict) and opt.get("value") == model and opt.get("efforts"):
                return opt["efforts"][-1]
    except Exception:  # noqa: BLE001
        pass
    return best


def _draft_revise(ask_fn, cdir: Path, adapter: str, models, worktree, needs, changed_fn, round_no) -> None:
    """复查退回用完停下后（第四轮 56 甲）：调度员用复查员那家工具、最高强度、只读，把这次开工以来的几轮复查
    合起来找共同原因，写好“继续改”的话，预先填进继续改框；用户看一眼再发。起草不成只记一笔，不影响停下。"""
    rounds = []
    for e in _after_revise(read_flow(cdir)):
        if e.get("kind") != "复查结论" or e.get("result") != "退回":
            continue
        back = _conclusion_back_problems(e)
        if back:
            texts = [_problem_text(p) for p in back]
            rounds.append("\n".join(f"- {t}" for t in texts))
    if not rounds:
        for e in _after_revise(read_flow(cdir)):
            if e.get("kind") == "回复" and e.get("src") == "复查员" and e.get("ok") is True:
                body = text_of(cdir, e) or ""
                if review_verdict(body) == "reject":
                    rounds.append(body.strip())
    try:
        files = [p for p in changed_fn(worktree) if not is_cache(p)]
    except Exception:  # noqa: BLE001
        files = []
    record(cdir, "起草", src="程序", dst="你", phase="开始",
           summary="正在替你起草“继续改”的话",
           say="我正在替你起草“继续改”的话（把这几轮复查合起来找共同原因），约 3–5 分钟，好了告诉你。")
    body = "\n\n".join(f"### 第 {i + 1} 次复查\n{t}" for i, t in enumerate(rounds))
    prompt = (f"{_rules('起草交代')}你是调度员，替用户起草一段“继续改”的交代。写代码的人被复查退回了好几次、每次都冒出新问题，"
              f"卡片停下了。下面是这次开工以来每一次复查的结论，任务卡在 {needs}，"
              f"改过的文件：{'、'.join(files) or '（看不出）'}。\n\n{body}\n\n"
              f"请读相关代码，找出这几轮问题的共同原因，写给写代码的人一段交代：先写清这一部分统一的规则（出错、找不到、"
              f"连不上时各怎么办，能合成一处的合成一处），再列要补的测试（每条一句：什么情况、应该怎样），"
              f"最后一句“只改这一部分，别的不动；改完跑本卡测试，过了就交”。"
              f"格式：第一段两句大白话，告诉用户为什么每轮都有新问题（不写文件名、代码名）；然后单独一行 ---；"
              f"下面是给写代码的人的交代正文。只读，不要修改任何文件。")
    if adapter not in ("codex", "qwen", "tencent"):
        adapter = "codex"   # Cursor、agy 没有按次只读的开关：起草改用 Codex（复查第 2 轮）
    effort = dict(models or {}, codex_effort=_top_effort(models, adapter), codex_sandbox="read-only")
    try:
        result = _dispatch(ask_fn, cdir, "调度员", adapter, "起草", prompt, worktree, effort, round_no)
        text = reply_text(result).strip()
    except Exception:  # noqa: BLE001 — 起草失败不影响停下
        result, text = None, ""
    if result is not None and result.ok and text:
        record(cdir, "起草", src="程序", dst="你", phase="好了", summary="“继续改”的话起草好了",
               say="“继续改”的话起草好了：点“不满意，继续改”，框里已经填好，看一眼说得通就发；不对可以改了再发。",
               text=text)
    else:
        record(cdir, "起草", src="程序", dst="你", phase="没成", summary="“继续改”的话没起草成",
               say="“继续改”的话没起草成，只能请你自己写，或者等 Claude 来看。")


def _zero_tests_ran(output: str) -> bool:
    """全部测试末尾汇总显示实际只跑了 0 项（例如只有一个空测试文件），不能当通过放行。"""
    counts = re.findall(r"^\s*Ran (\d+) tests?\b", output or "", re.M)
    return bool(counts) and int(counts[-1]) == 0


class _FullSlot:
    """复查的同时跑全量（第四轮 59）：start() 在后台开跑，stop() 叫停，finish() 等它跑完给结论。
    没给 runner（测试替身、关掉了）就什么都不做；不是 Python 项目就记一笔跳过。"""

    def __init__(self, runner, worktree, cdir):
        import threading
        self.runner, self.worktree, self.cdir = runner, worktree, cdir
        self.event = threading.Event()
        self.thread = None
        self.result = None
        self.last_actual = None
        self.skipped = False

    def start(self):
        import threading
        from collab import fullrun
        if self.runner is None or self.thread is not None:
            return
        if not fullrun.applicable(self.worktree):
            self.last_actual = None
            if not self.skipped:
                self.skipped = True
                record(self.cdir, "全量", src="程序", phase="跳过",
                       summary="不是 Python 项目（没有 tests/test_*.py），全量测试跳过，最后靠人看")
            return
        self.skipped = False
        self.event = threading.Event()
        self.result = None
        self.last_actual = None
        record(self.cdir, "全量", src="程序", phase="开始",
               summary="复查的同时，程序在一份副本里跑全部测试（真 AI 工具藏起来），看有没有改坏老功能")

        def work():
            try:
                self.result = self.runner.run(self.worktree, self.event)
            except Exception as exc:  # noqa: BLE001 — 跑不起来交给 finish 说清
                self.result = Verdict(ENV_ERROR, None, f"全量测试跑不起来：{exc}")

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()

    def stop(self):
        """叫停并等它真的收完（复制、测试都有时限、会响应叫停），不留旧一轮的线程和进程（复查第 5 条）。"""
        self.event.set()
        if self.thread is not None:
            self.thread.join()
            record(self.cdir, "全量", src="程序", phase="停", summary="这一轮全量测试不用跑完了（要改代码）")
        self.thread = None

    def finish(self, judge_round: int):
        """返回 (verdict, 停下原因)。过 → PASS；改坏老功能 → FAIL（交代写明哪几项）；超时、出错、看不懂 → 停下。"""
        from collab import fullrun
        if self.thread is None:
            self.last_actual = None
            return Verdict(PASS, 0, ""), ""
        self.thread.join()
        self.thread = None
        now = self.result or Verdict(ENV_ERROR, None, "全量测试没有结果")
        if now.result == PASS and _zero_tests_ran(now.output):
            bad_zero = Verdict(ENV_ERROR, now.returncode, now.output)
            self.last_actual = bad_zero
            record(self.cdir, "全量", src="程序", phase="结果", result=ENV_ERROR,
                   summary="全部测试只跑了 0 项（没有实际运行任何测试）", text=(now.output or "")[-20000:])
            return bad_zero, "全部测试只跑了 0 项（没有实际运行任何测试），需要你看看"
        self.last_actual = now
        if now.result == PASS:
            record(self.cdir, "全量", src="程序", phase="结果", result=PASS, summary="全部测试都过了")
            return Verdict(PASS, 0, ""), ""
        if now.result != FAIL:
            what = "超时（30 分钟没跑完）" if now.result == TIMEOUT else "跑不起来"
            record(self.cdir, "全量", src="程序", phase="结果", result=now.result,
                   summary=f"全部测试{what}", text=now.output[-20000:])
            return now, f"全部测试{what}，需要你看看"
        files = fullrun.failing_files(now.output, self.worktree)
        listed = sum(len(v) for v in files.values())
        total = fullrun.summary_count(now.output)
        if files and total is not None and total > listed:
            files = {}   # 汇总里还有列不出名字的（例如意外通过）：认不全就交给人看，不能当旧问题放过（复查第 2 轮）
        if not files:
            record(self.cdir, "全量", src="程序", phase="结果", result=FAIL,
                   summary="全部测试没过，但认不出是哪几项", text=now.output[-20000:])
            return now, "全部测试没过，但认不出是哪几项，需要你看看"
        base = self.runner.base(self.worktree, sorted(files), self.event)
        broken_base = [f for f in files if f not in base or (
            base[f].result != FAIL and base[f].result != PASS and "改之前没有这个测试文件" not in (base[f].output or ""))]
        if broken_base:   # 改之前的样子没跑成（超时、准备失败）：比不了，交给人看，不算写代码的错（复查第 3 轮）
            record(self.cdir, "全量", src="程序", phase="结果", result=FAIL,
                   summary="全部测试里有没过的，但改之前的样子没跑成，比不出是不是这次改坏的", text=now.output[-20000:])
            return now, "全部测试里有没过的，但改之前的样子没跑成、比不出来，需要你看看"
        unrunnable = [f for f, names in files.items() if any("unittest.loader" in n for n in names)]
        if unrunnable:   # 测试文件导入就报错：多半是副本缺依赖或解释器不对，不能当“改之前就不过”放过（复查第 3 轮）
            record(self.cdir, "全量", src="程序", phase="结果", result=FAIL,
                   summary=f"全部测试里有测试文件跑不起来（{'、'.join(Path(f).stem for f in unrunnable)}）",
                   text=now.output[-20000:])
            return now, "全部测试里有测试文件跑不起来（导入就报错，可能缺依赖），需要你看看"
        bad = fullrun.culprits(now, base, self.worktree)
        names = "、".join(Path(f).stem for f in (bad or sorted(files)))
        if not bad:
            record(self.cdir, "全量", src="程序", phase="结果", result=FAIL,
                   summary=f"全部测试里有没过的（{names}），但改之前就不过，不算这次改坏的", text=now.output[-20000:])
            return Verdict(PASS, 0, ""), ""
        # 退回写代码的之前，在现在的样子上单独再跑一次：单独跑能过的是测试自己不稳，写代码的修不了（卡 71 白退 3 轮，1-9）
        alone_fn = getattr(self.runner, "alone", None)
        alone = alone_fn(self.worktree, bad, self.event) if alone_fn else {}
        shaky = [f for f in bad if f in alone and alone[f].result == PASS]
        unclear = [f for f in bad if f not in alone or alone[f].result not in (PASS, FAIL)]
        if alone_fn and unclear:   # 单独重跑超时、跑不起来、没跑到：不等于“单独也坏”，交人看（复查第 1 轮）
            names = "、".join(Path(f).stem for f in unclear)
            record(self.cdir, "全量", src="程序", phase="结果", result=FAIL,
                   summary=f"全部测试里 {names} 没过，单独重跑也没得出结论，不退回写代码的", text=now.output[-20000:])
            return now, f"全部测试里 {names} 没过，单独重跑没得出结论（超时或跑不起来），需要你看看"
        if shaky and len(shaky) == len(bad):
            names = "、".join(Path(f).stem for f in shaky)
            record(self.cdir, "全量", src="程序", phase="结果", result=FAIL,
                   summary=f"全部测试里 {names} 没过，但单独跑能过（多半是测试自己不稳），不退回写代码的",
                   text=now.output[-20000:])
            return now, f"全部测试里 {names} 没过，但单独跑能过（多半是测试自己不稳），写代码的修不了，需要你看看"
        if shaky:
            bad = [f for f in bad if f not in shaky]
            names = "、".join(Path(f).stem for f in bad)
        listing = "\n".join(f"- {f}：{'；'.join(sorted(files[f]))}" for f in bad)
        record(self.cdir, "全量", src="程序", phase="结果", result=FAIL,
               summary=f"全部测试里这些老测试没过，改之前它们是过的：{names}", text=now.output)
        record(self.cdir, "判定", src="程序", dst="开发员", result=FAIL, round=judge_round,
               summary=f"第 {judge_round} 轮：复查过了，但全部测试里有老测试被改坏了（{names}）",
               text=now.output)
        _append(self.cdir / "测试结果.md", f"\n## 全部测试复查老测试\n结论：{now.result}\n\n{now.output}\n")
        return Verdict(FAIL, now.returncode,
                       f"本卡测试过了，但全部测试里这些老测试没过，改之前它们是过的（这次改动把原来的功能改坏了，"
                       f"要改代码让它们也过，不要改老测试；跑法：python3 -m unittest discover -s tests -p 文件名）：\n"
                       f"{listing}\n\n{_short_test_note(self.cdir, now.output)}"), ""


_SUMMARY_LIMIT = 4000


def _clip_line(line: str, limit: int) -> str:
    """单行太长时截断，保证汇总行不被挤掉。"""
    if limit <= 0:
        return ""
    return line if len(line) <= limit else line[:limit - 1] + "…"


def _pack_summary(blocks: list, summary: list, limit: int = _SUMMARY_LIMIT) -> str:
    """blocks：每块是[标题, ...内容行]；summary：必须保留的汇总行。
    先按每行长度一档档收紧；还放不下就只留标题和汇总，绝不让前面的长报错挤掉后面的标题和 Ran/FAILED。"""
    summary = [_clip_line(line, limit) for line in summary]
    summary_text = "\n".join(summary)
    if len(summary_text) >= limit:      # 汇总自己就超长：只能截汇总
        return summary_text[:limit]
    for cap in (2000, 1200, 600, 400, 250, 160, 100, 60, 30, 12):
        lines = [_clip_line(line, cap) for block in blocks for line in block]
        text = "\n".join(lines + summary)
        if len(text) <= limit:
            return text
    titles = [_clip_line(block[0], 200) for block in blocks if block]
    text = "\n".join(titles + summary)
    if len(text) <= limit:
        return text
    room = max(0, limit - len(summary_text) - 1)
    return (text[:room] + "\n" + summary_text)[:limit] if room else summary_text[:limit]


def _is_fullrun_listing(line: str) -> bool:
    """全量退回时程序写在失败块前的测试清单行：- tests/test_old.py：用例1；用例2。"""
    m = re.match(r"^- ([^\s：:]+\.py)[：:]", line)
    return bool(m and is_test_path(m.group(1)))


def _short_test_output(output: str) -> str:
    """测试没过时只交“失败项 + 关键几行”（第二批 A6）：认得出 unittest / pytest 就按格式挑，
    都认不出就留最后 60 行；完整输出照旧写进 测试结果.md 和流转记录。"""
    text = str(output or "")
    lines = text.splitlines()
    if any(re.match(r"^(FAIL|ERROR): ", line) for line in lines):
        blocks = []
        in_listing = False
        for line in lines:
            if re.match(r"^(?:FAIL|ERROR): ", line) or re.match(r"^=+\s*$", line):
                break   # 失败块之外（之前）只留程序写的小结和紧跟在后面的失败测试文件清单，不带出普通日志
            if line.startswith("本卡测试"):
                blocks.append([line])
                in_listing = True
            elif in_listing and _is_fullrun_listing(line):
                blocks.append([line])
            else:
                in_listing = False
        index = 0
        while index < len(lines):
            title = lines[index]
            if not re.match(r"^(FAIL|ERROR): ", title):
                index += 1
                continue
            index += 1
            tail = []
            while index < len(lines):
                line = lines[index]
                if (re.match(r"^(FAIL|ERROR): ", line) or re.match(r"^=+\s*$", line)
                        or re.match(r"^Ran \d+ tests?", line) or line.startswith("FAILED (")):
                    break
                if line.strip() and not re.match(r"^-+\s*$", line.strip()):
                    tail.append(line)
                index += 1
            test_lines = [line for line in tail
                          if re.search(r'File "([^"]+)"', line)
                          and (lambda path: "/tests/" in path.replace("\\", "/")
                               or Path(path).name.startswith("test_"))(
                              re.search(r'File "([^"]+)"', line).group(1))]
            keep = []
            for line in [*test_lines, *tail[-6:]]:
                if line not in keep:
                    keep.append(line)
            blocks.append([title] + keep)
        summary = [line for line in lines
                   if re.match(r"^Ran \d+ tests?", line) or line.startswith("FAILED (")]
        return _pack_summary(blocks, summary)
    if any(line.startswith("FAILED ") or line.startswith("E ") for line in lines):
        errors = [[line] for line in lines if line.startswith("E ")]
        failed = [line for line in lines if line.startswith("FAILED ")]
        return _pack_summary(errors, failed)
    return _pack_summary([[line] for line in lines[-60:]], [])


_LOG_FILES = {"判定": "测试结果.md", "页面检查": "页面检查结果.md"}


def _log_file(kind: str) -> str:
    """判定结果和它的完整日志放哪，只认这一张表（第四轮 54）：换判定时不用各处惦记着改文件名，
    没登记的一律当测试结果，不会指到别处的旧日志。"""
    return _LOG_FILES.get(kind, "测试结果.md")


def _with_log(verdict, kind: str) -> tuple:
    """判定和它对应的完整日志文件一起返回，谁换判定就顺手带上自己的日志，不会对不上。"""
    return verdict, _log_file(kind)


def _short_test_note(cdir: Path, output: str, filename: str = "测试结果.md") -> str:
    """交接里附上完整输出的位置，别让写代码的以为只有这几行。"""
    return (f"测试输出摘要如下：\n\n{_short_test_output(output)}\n\n"
            f"完整输出在 {filename}：{Path(cdir) / filename}")


def _trim_return_item(text: str, budget: int) -> str:
    """单条退回意见超长时保住现象、位置、命令和输出末尾报错，不把排在后面的条目挤没。"""
    if len(text) <= budget:
        return text
    lines = text.splitlines()
    head = "\n".join(lines[:2]) if len(lines) >= 2 else lines[0]
    rest = "\n".join(lines[2:]) if len(lines) >= 2 else ""
    if "；输出 " in rest:
        cmd_part, out_part = rest.split("；输出 ", 1)
        room = max(80, budget - len(head) - len(cmd_part) - 30)
        if len(out_part) > room:
            front = min(room // 3, 200)
            back = max(40, room - front)
            out_part = out_part[:front].rstrip() + " …（中间省略）… " + out_part[-back:].lstrip()
        combined = f"{head}\n{cmd_part}；输出 {out_part}"
        if len(combined) <= budget:
            return combined
    return _clip_line(text, budget)


def _fit_return_items(items: list, limit: int = _NOTES_LIMIT) -> str:
    """按条数均分超长空间：前面某条日志再长，也不能把后面的问题、位置、证据吞掉。"""
    numbered = [f"{i + 1}. {t}" for i, t in enumerate(items)]
    body = "\n\n".join(numbered)
    if len(body) <= limit:
        return body
    budget = max(240, (limit - 2 * len(items)) // max(1, len(items)))
    fitted = [_trim_return_item(entry, budget) for entry in numbered]
    body = "\n\n".join(fitted)
    if len(body) <= limit:
        return body
    return body[:limit] + "\n……（后面省略）"


def _review_return_prompt(notes: str, needs, revise_bit: str, plan_hint: str, same_files=None,
                          guard_note: str = "", items=None, review_file=None) -> str:
    """复查退回（第三批 ㊾）：只交要改的这几条（连同复查员写的原因和位置），说清其他已经验过、不要重新核对；
    不带开工时的整卡交接资料（69 里 agy 因此把整张卡又核了一遍，30 分钟改不完一句提示）。"""
    if items is None:
        items = _review_items(notes)
        if items and _unparsed_serious(notes, items):
            items = []   # 认不全：整段原文交过去，不能只留编号的那几条（复查第 3 轮）
    if items:
        body = _fit_return_items(items, _NOTES_LIMIT)
        head = f"上一次复查不通过，只改下面这 {len(items)} 条（不算一次返工）"
    else:
        body = (notes or "").strip() or "（复查员没有写下详细意见）"
        head = "上一次复查不通过，只按下面的复查意见改（不算一次返工）"
    ref_note = f"\n完整复查意见和清单在：{review_file}。" if review_file else ""
    if same_files:
        # 连着两轮在同一处冒新问题（第四轮 57）：一条条补只会在旁边再冒一条，先找共同原因
        head += (f"。注意：这几轮复查的问题都出在同一处（{'、'.join(same_files)}），每轮都是新的——"
                 f"先找它们的共同原因，把这一部分统一改对（比如几处各管各的处理合成一处、出错的规矩说死），"
                 f"再逐条核对下面这些都已解决")
    return (f"{_rules('写代码')}{head}。先想清楚这几条背后的同一类原因，在本卡改动的范围里找出同类写法一起改，"
            f"不要只改被点名的那一处。其他部分已经测过、复查过，不要重新核对，也不要重读整张卡和交接资料；"
            f"改完跑一次本卡测试，本卡测试要全部通过，过了就交。{guard_note}\n\n{body}{ref_note}\n\n"
            f"任务卡：{needs}（只在要查需求原文时看）。{revise_bit}{_DEV_TEST_NOTE}{plan_hint}{_REPORT_ASK}")


def _dispatch_review_return(cdir: Path, ask_fn, test_adapter: str, test_models, worktree, needs,
                            test_command, revert_fn, rnd: int, *, back_problems: list,
                            same_files=None, plan_hint: str = "", run_hint: str = "", revise_bit: str = "",
                            review_file=None, legacy_notes: str = "", check_done: bool = False,
                            judge_fn=None, red_check: bool = False) -> str:
    """复查退回给测试员和开发员的统一派活入口（连续跑与中断续跑共用）。
    C1.8 起不再有“整段原话退回”：清单看不懂的在复查结果那一步就停下转人工了。"""
    fixed = ""
    guard_note = ""
    if back_problems:
        test_items = [_problem_text(p) for p in back_problems if p.get("类型") == "测试写错"]
        normal_items = [_problem_text(p) for p in back_problems if p.get("类型") != "测试写错"]
        if test_items and (not check_done or not _tester_step_done(cdir, "改测试")):
            fixed = _tester_fix(ask_fn, cdir, test_adapter, test_items, needs, worktree,
                                test_models, rnd, test_command, revert_fn)
        if normal_items:
            _, guard_note = _guard_tests(ask_fn, cdir, test_adapter, normal_items, needs, worktree,
                                         test_models, rnd, test_command, revert_fn, judge_fn=judge_fn,
                                         red_check=red_check,
                                         already=check_done and _tester_step_done(cdir, "补测试"))
        dev_items = normal_items or (
            ["复查只指出测试本身写错，测试已改好，照新测试改代码，不要改测试。"] if test_items else [])
        dev_prompt = _review_return_prompt(
            "", needs, revise_bit, plan_hint + run_hint, same_files=same_files,
            guard_note=guard_note, items=dev_items, review_file=review_file,
        )
    else:
        fix_items = _fix_items(legacy_notes)
        test_items = [i for i in fix_items if TEST_PROBLEM.strip("【】") in _lead_tags(i)]
        normal_items = [i for i in fix_items if TEST_PROBLEM.strip("【】") not in _lead_tags(i)]
        if test_items and (not check_done or not _tester_step_done(cdir, "改测试")):
            fixed = _tester_fix(ask_fn, cdir, test_adapter, test_items, needs, worktree,
                                test_models, rnd, test_command, revert_fn)
        if normal_items:
            _, guard_note = _guard_tests(ask_fn, cdir, test_adapter, normal_items, needs, worktree,
                                         test_models, rnd, test_command, revert_fn, judge_fn=judge_fn,
                                         red_check=red_check,
                                         already=check_done and _tester_step_done(cdir, "补测试"))
        dev_prompt = _review_return_prompt(
            legacy_notes, needs, revise_bit, plan_hint + run_hint,
            guard_note=guard_note, review_file=review_file if review_file and review_file.is_file() else None,
        )
    return dev_prompt + fixed  # 冲突提醒由两处调用方各自加（续跑那处放在函数层，非复查退回的续跑也要带上）


_VERDICT_SUMMARY = {PASS: "测试全部通过", FAIL: "测试没过", ENV_ERROR: "测试环境出错（不是代码的问题）",
                    TIMEOUT: "测试超时"}


def run_card(card: Card, collab_dir, worktree, test_command, dev_adapter, test_adapter,
             ask_fn=default_ask, judge_fn=default_judge, models=None,
             allowed=None, changed_fn=None, page_command=None, reviewer_adapter=None,
             revert_fn=None, require_tests=False, role_models=None, handover=None, full_runner=None,
             draft=False, progress=False, red_check=False):
    """交接资料（第二轮 ㉚）：调度员在后台和写测试的同时备；不管施工怎么结束（交活、卡住、出错、被停），
    都在这里收尾——还没备完就停掉它，不留下后台的工具进程（复查 B 第 5 条、C 第 1 条）。"""
    import threading
    from collab import agents as _agents
    cdir = _card_dir(Path(collab_dir), card)
    cdir.mkdir(parents=True, exist_ok=True)
    from collab.rulebook import missing_files
    lost = missing_files()
    if lost:  # 规则包缺文件：照样开工，但派活的话里写明缺失，这里记一条让人看见，不悄悄跑空规则
        record(cdir, "提醒", src="程序", dst="你",
               summary=f"规则包缺文件：{'、'.join(lost)}。这次派活的话里会写明缺失；请补回文件或重新生成应用。")
    notes_job = None
    if handover and not (cdir / HANDOVER).is_file():
        order_found = False
        try:
            need = (cdir / "需求卡.md").read_text(encoding="utf-8")
            from collab.planner import existing_orders
            order_found = bool(existing_orders(Path(collab_dir).parent, need))
        except (OSError, ValueError):
            order_found = False
        if order_found:
            record(cdir, "交接资料", src="程序", dst="开发员", ok=True,
                   summary="有施工单，跳过（写代码、复查直接读施工单）")
        else:
            notes_job = threading.Thread(target=_make_handover, daemon=True,
                                         args=(ask_fn, cdir, handover[0], handover[1], cdir / "需求卡.md", worktree))
            notes_job.start()
    full = _FullSlot(full_runner, worktree, cdir)
    if progress:
        _PROGRESS_ON.add(str(cdir))   # 写测试、写代码时右栏的进度（第四轮 53）
    try:
        return _run_card(card, collab_dir, worktree, test_command, dev_adapter, test_adapter, ask_fn, judge_fn,
                         models, allowed, changed_fn, page_command, reviewer_adapter, revert_fn, require_tests,
                         role_models, notes_job, full, draft, red_check)
    finally:
        full.stop()
        _PROGRESS_ON.discard(str(cdir))
        if notes_job is not None and notes_job.is_alive():
            _agents.kill_current_tool_process_group()
            notes_job.join(30)
            _agents.allow_tools()


def _apply_queued(collab: Path, cdir: Path, card) -> bool:
    """第七批 7-1：取走跑着时确认的修改，走现成的“接着改”记成追加要求；有一条生效就返回 True（要再走一轮）。
    生效的这一轮返工次数从 0 算（和原来“接着改”一样，复查中等 6）；生效不了的照实记下原因，不卡住这张卡。"""
    from collab.tasks import apply_queued_items, card_apply_lock, take_queued_revisions
    project = collab.parent
    with card_apply_lock(cdir):   # 和任务页、巡视的“停下后自动生效”共用一把锁（复查第 2 轮严重 1）
        items = take_queued_revisions(project, card.card_id)
        if not items:
            return False
        applied, failed = apply_queued_items(project, card.card_id, items)
    for words, why in failed:
        record(cdir, "改要求没生效", src="程序", dst="你",
               summary=f"你确认的修改没能生效：{why}", say=f"你确认的修改没能生效：{why}。可以再说一次。", text=words)
    for words in applied:
        record(cdir, "改要求生效", src="程序", dst="你", summary=f"你的修改已生效：{words[:60]}",
               say=f"你的修改已生效：{words[:60]}。按新要求再走一轮：补测试 → 写代码 → 跑测试 → 复查。")
    if applied:
        card.rework = 0
    return bool(applied)


def _pending_review_replies(cdir: Path, past: list) -> list:
    """最近一次开发员成功交活之后、还没写复查结论（或因此转人工）的本轮复查员回复。"""
    last_dev_idx = max((i for i, e in enumerate(past)
                        if e.get("kind") == "回复" and e.get("src") == "开发员" and e.get("ok") is True), default=-1)
    if last_dev_idx < 0:
        return []
    after_dev = past[last_dev_idx + 1:]
    if any(e.get("kind") in ("复查结论", "转人工") for e in after_dev):
        return []
    if any(e.get("kind") in ("判定", "页面检查", "老测试") and e.get("result") != PASS for e in after_dev):
        return []
    replies = [e for e in after_dev if e.get("kind") == "回复" and e.get("src") == "复查员"]
    if not replies:
        return []
    tasks = {e.get("seq"): e for e in past if e.get("kind") == "派活" and e.get("dst") == "复查员"}
    first_task = tasks.get(replies[0].get("reply_to"))
    first_prompt = text_of(cdir, first_task) if first_task else ""
    first_body = text_of(cdir, replies[0]) or ""
    if "复查清单" not in first_prompt and "```json" not in first_body.lower():
        return []
    return replies


def _record_review_history(cdir: Path, heading: str, body: str) -> None:
    """把复查/格式重写全文写进 复查结果.md；续跑时若上次已写到末尾则不重复追加。"""
    entry = f"## {heading}\n{body}"
    path = cdir / "复查结果.md"
    try:
        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        existing = ""
    if existing.rstrip().endswith(entry.strip()):
        return
    _append(path, f"\n{entry}\n")


def _stop_manifest(data: dict, back_problems: list):
    if data and back_problems:
        return dict(data, 问题=back_problems)
    if back_problems:
        return {"结论": "不通过", "问题": back_problems}
    return data


def _plain_summary_items(back_problems: list) -> tuple:
    """把退回问题统一转成大白话列表。"""
    issues, hidden, severe_hidden = plain_problems(back_problems)
    count = max(len(back_problems), len(issues)) if back_problems else len(issues)
    if not issues and hidden:   # 一条都翻不成大白话：照样报件数、标严重，不能说成“没按格式写”（11-18 复查 6）
        issues = [f"{hidden} 件都没写成大白话{'（其中有严重的）' if severe_hidden else ''}，原话在右边“交接”里"]
        hidden = 0
    return issues, hidden, severe_hidden, count


def _handle_review_reject(collab: Path, cdir: Path, card: Card, *, ask_fn, reviewer_adapter: str, by_role: dict,
                          models, worktree, needs, changed_fn, draft: bool, rnd: int,
                          data: dict, back_problems: list, review_text: str) -> tuple:
    """复查不通过时的统一收口：先检查是否已超过自动退回上限，超限停下转人工，未超限记“退回”。
    正常流程与中断续跑共用这一处判断，避免续跑绕过两次上限。返回 (是否已转人工停下, 退回次数)。"""
    rejects = _review_rejects(cdir)
    issues, hidden, severe_hidden, count = _plain_summary_items(back_problems)
    more = _hidden_words(hidden, severe_hidden)
    test_items = [_problem_text(p) for p in back_problems if p.get("类型") == "测试写错"]
    tried_tests = any(e.get("kind") == "派活" and e.get("step") == "改测试"
                      for e in _after_revise(read_flow(cdir)))
    stop_data = _stop_manifest(data, back_problems)
    extra = {"清单": stop_data} if stop_data else {}
    if back_problems:
        extra["退回问题"] = back_problems
    who = reviewer_adapter or "复查员"
    if rejects > MAX_REVIEW_RETURNS and not (test_items and not tried_tests):
        left = (f"复查还剩 {count} 件事：{_issues_words(issues)}{more}。" if issues
                else "复查员没按格式写剩下的问题，原话在右边“交接”里。")
        if back_problems:
            reason = _stop_reason_list(stop_data, cdir, back_problems=back_problems)
        else:
            reason = _stop_reason(cdir)
        say = (f"复查员（{who}）复查不通过（已自动退回 {MAX_REVIEW_RETURNS} 次）。"
               f"{left}{reason}。")
        record(cdir, "转人工", src="程序", dst="调度员", cause="复查不通过", text=review_text, say=say,
               issues=issues or None, hidden=hidden or None, **extra,
               summary=f"复查员（{who}）复查不通过（已自动退回 {MAX_REVIEW_RETURNS} 次）："
                       f"{_first_issues(review_text)}")
        card.transition("需Claude介入")
        _save(collab, card)
        test_left = bool(test_items)
        minor_only = bool(back_problems) and all(
            p.get("级别") == "轻微" and p.get("类型") != "测试写错" for p in back_problems)
        if draft and not test_left and not minor_only:   # 按条目开头的分类判断，不搜正文（复查第 3 轮）
            _draft_revise(ask_fn, cdir, who, by_role.get("复查员", models), worktree,
                          needs, changed_fn, rnd)
        return True, rejects
    # 复查不通过自动退回写代码的（用户 11-12 定最多 2 次），只给复查员最后写的结论，不算返工
    what = (f"复查挑出 {count} 件事：{_issues_words(issues)}{more}。" if issues
            else "复查不通过（复查员没按格式写，原话在右边“交接”里）。")
    record(cdir, "退回", src="程序", dst="开发员",
           summary=f"复查不通过，自动退回写代码的（第 {rejects} 次，最多 {MAX_REVIEW_RETURNS} 次）",
           say=f"{what}已退回写代码的改（第 {rejects} 次，最多 {MAX_REVIEW_RETURNS} 次；"
               f"整张卡第 {_dev_rounds(cdir) + 1} 次写代码）。")
    if card.state != "开发中":
        card.transition("开发中")
        _save(collab, card)
    return False, rejects


def _run_card(card, collab_dir, worktree, test_command, dev_adapter, test_adapter, ask_fn, judge_fn, models,
              allowed, changed_fn, page_command, reviewer_adapter, revert_fn, require_tests, role_models,
              notes_job, full=None, draft=False, red_check=False):
    """role_models：{测试员|开发员|复查员: 模型设置}，每个岗位用自己的模型和强度（流程修理 ①）；没给的岗位用 models。"""
    by_role = role_models or {}
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

    past_all = read_flow(cdir)
    past = _after_revise(past_all)
    revise_note = _revise_words(cdir, past_all)
    revise_bit = f"\n\n追加要求：\n{revise_note}\n" if revise_note else ""
    test_done = any(e.get("kind") == "回复" and e.get("src") == "测试员" and e.get("ok") is True for e in past)
    if require_tests and test_done and (cdir / _BEFORE_TESTER).is_file() and not _load_test_files(cdir):
        # 测试员上次没交活：重新派，并把当前改动算作基线，不能当成它写的测试
        test_done = False
        _backup_before_tester(cdir, worktree, _save_before_map(cdir, worktree))
    plan_hint = _plan_hint(cdir)

    # 复查已回复但复查结论尚未落盘时，先接续完这一轮复查判定；否则才看是否需要带着退回意见重改
    pending_reviews = _pending_review_replies(cdir, past)
    if pending_reviews:
        force_after_review_reject, review_notes = False, ""
    else:
        force_after_review_reject, review_notes = _review_reject_pending(cdir, past)

    # 第一步：测试员按验收标准先写测试（测试先行；如果断点续跑前已经写完交活，直接跳过）
    run_hint = _run_hint(test_command)
    if not test_done:
        before_map = _remember_before_tester(cdir, worktree)
        old_tests = [p for p in _load_test_files(cdir) if (Path(worktree) / p).is_file()]
        if revise_note and old_tests:
            # 继续改：只为追加要求补测试，不整个重写（68b 里重写一遍 15 分钟，⑭）
            tester_prompt = (f"{_rules('写测试')}任务卡 {needs} 的测试之前已经写好：{'、'.join(old_tests)}。"
                             f"现在有追加要求，只为追加要求补测试（可以在原测试文件里追加，或新建测试文件）。"
                             f"已有测试里和追加要求冲突的用例（追加要求改掉了原来的行为），按追加要求改掉或删掉，"
                             f"并在汇报里列出改了哪些；其余已有的测试不要重写。追加要求不需要新测试就直接说明。"
                             f"只改测试文件，不改产品代码，改了也会被撤掉。{revise_bit}{run_hint}{_tester_conflict_note(cdir)}")
        else:
            tester_prompt = (f"{_rules('写测试')}按任务卡 {needs} 的验收标准编写自动化测试，放进项目的测试目录。"
                             f"只写测试，不改产品代码，改了也会被撤掉。{revise_bit}{plan_hint}{run_hint}"
                             f"{_tester_conflict_note(cdir)}")
        prep = _dispatch(ask_fn, cdir, "测试员", test_adapter, "写测试", tester_prompt, worktree, by_role.get("测试员", models), 1,
                         test_command=test_command)
        # 摘要变了或新出现的文件算测试文件；以前的测试文件还在的也保留
        root = Path(worktree)
        fresh = []
        clashing = set(conflict_names(cdir))
        for rel in _expand_to_files(worktree, [p for p in _changed_list(worktree)
                                               if not is_cache(p) and p not in clashing
                                               and not p.startswith(".collab")]):
            digest = _file_digest(root / rel)
            if rel not in before_map or before_map.get(rel) != digest:
                fresh.append(rel)
        # 写测试的只许动测试文件（68b 里它去改了 desk.py，被当成测试文件锁住，开发员白干，⑱）
        # 不按测试算的：改了原有的非测试文件，或在原有产品文件夹里新建文件（流程修理复查第 1 轮）；
        # 新建的顶层文件夹（e2e/ 这类各项目叫法不一）照旧按测试算
        stray = [rel for rel in fresh if not is_test_path(rel)
                 and (rel in before_map or _tracked(worktree, rel) or _in_product_folder(worktree, rel))]
        if stray:
            _undo_tester_changes(cdir, worktree, stray, before_map, revert_fn)
            fresh = [rel for rel in fresh if rel not in stray]
            _append(cdir / "返工记录.md",
                    f"- {datetime.now():%Y-%m-%d %H:%M} 写测试的改了测试以外的文件，已撤掉：{'、'.join(stray)}\n")
            record(cdir, "范围", src="程序", ok=True,
                   summary=f"写测试的改了测试以外的文件，已撤掉：{'、'.join(stray)}", text="\n".join(stray))
        previous = [p for p in _load_test_files(cdir) if (root / p).is_file()]
        added = []
        seen = set()
        for rel in previous + fresh:
            if rel in seen:
                continue
            seen.add(rel)
            added.append(rel)
        if added:
            _save_test_files(cdir, added)
            _snapshot_test_files(cdir, worktree, added)
        if not prep.ok:
            return _worker_failed(collab, cdir, card, "测试员", test_adapter, prep)
        _log_step(cdir, "写测试", test_adapter, worktree)
        if require_tests and not _load_test_files(cdir):
            _escalate(collab, cdir, card, f"测试员（{test_adapter}）没有写出任何测试：{reply_summary('写测试', readable(prep.output, test_adapter), True)}")
            return card, None

    if red_check and test_command:
        # 写测试的交活后先跑一遍本卡测试；一跑就全过退回重写，再全过停下（第一次跑和接着跑共用）
        outcome = _red_check_after_tests(
            collab, cdir, card, ask_fn=ask_fn, test_adapter=test_adapter,
            test_models=by_role.get("测试员", models), worktree=worktree, needs=needs,
            test_command=test_command, revert_fn=revert_fn, judge_fn=judge_fn,
            require_tests=require_tests,
        )
        if outcome is not None:
            return outcome

    if notes_job is not None:
        notes_job.join()
        plan_hint = _plan_hint(cdir)

    tester_files = _load_test_files(cdir)
    if require_tests and not tester_files:
        _escalate(collab, cdir, card, "本卡已经没有测试文件了，不能交给写代码的，需要把关的人看")
        return card, None
    effective_allowed = list(allowed) + tester_files if allowed is not None else None

    # 第二步：开发员照着测试写代码，直到测试通过
    dev_prompt = (f"{_rules('写代码')}按任务卡实现功能：{needs}。测试已经写好，请让它们全部通过。"
                  f"{revise_bit}{_DEV_TEST_NOTE}{plan_hint}{run_hint}{_REPORT_ASK}")
    last_output = None  # 上一轮测试的报错，用来发现“原地打转”
    past_fails = [e for e in past if e.get("kind") in ("判定", "页面检查") and e.get("result") == FAIL]
    if past_fails and card.rework > 0:
        last_output = text_of(cdir, past_fails[-1])
        last_output_file = _log_file(past_fails[-1].get("kind"))
        dev_prompt = (f"{_rules('写代码')}上一版测试没过，测试输出如下，请修复后重新提交。\n\n"
                      f"{_short_test_note(cdir, last_output, last_output_file)}\n\n"
                      f"任务卡：{needs}。{revise_bit}{_DEV_TEST_NOTE}{plan_hint}{run_hint}{_REPORT_ASK}")
    if force_after_review_reject:
        # 补测阶段被中断后再接着跑：先核对上次复查退回有没有走完上限判断，再核对补测有没有交成
        review_file = cdir / "复查结果.md"
        latest_conc, pending_data = _latest_conclusion_list(cdir)
        if (latest_conc is not None and latest_conc.get("result") == "退回" and not pending_data
                and latest_conc.get("text")):
            # C1.8：旧做法“整段原话退回”已取消，续跑碰到这种记录不再按原话派活，停下转人工
            _escalate(collab, cdir, card,
                      "上次复查是按整段原话退回的（旧做法，已取消），程序不再按原话派活；需要你看看")
            return card, None
        if latest_conc is not None and latest_conc.get("result") == "退回":
            conc_seq = int(latest_conc.get("seq") or 0)
            if not any(int(e.get("seq") or 0) > conc_seq and e.get("kind") in ("退回", "转人工") for e in past):
                reply_seq = int(latest_conc.get("reply") or 0)
                reply_ev = next((e for e in past if int(e.get("seq") or 0) == reply_seq), None)
                stop_data = pending_data if isinstance(pending_data, dict) else {}
                stop_back = _conclusion_back_problems(latest_conc)
                rev_text = (text_of(cdir, reply_ev) if reply_ev else "") or review_notes
                stopped, _ = _handle_review_reject(
                    collab, cdir, card, ask_fn=ask_fn, reviewer_adapter=reviewer_adapter,
                    by_role=by_role, models=models, worktree=worktree, needs=needs, changed_fn=changed_fn,
                    draft=draft, rnd=card.rework + 1, data=stop_data, back_problems=stop_back,
                    review_text=rev_text,
                )
                if stopped:
                    return card, None
        pending = _conclusion_back_problems(latest_conc) if latest_conc is not None else []
        same_files = (_same_place_new_list(
            cdir, pending_data or {}, int(latest_conc.get("reply") or 0),
            root=worktree, current_back=pending,
        ) if latest_conc is not None and pending else [])
        dev_prompt = _dispatch_review_return(
            cdir, ask_fn, test_adapter, by_role.get("测试员", models), worktree, needs,
            test_command, revert_fn, card.rework + 1,
            back_problems=pending, same_files=same_files,
            plan_hint=_plan_hint(cdir, False), run_hint=run_hint, revise_bit=revise_bit,
            review_file=review_file, legacy_notes=review_notes, check_done=True,
            judge_fn=judge_fn, red_check=red_check,
        )

    dev_prompt += _conflict_note(worktree, cdir)  # 续跑不论哪条路都带上冲突提醒（C1.6 重构时漏掉，Claude 补回）

    first_iter = True
    review_round = _review_rejects(cdir) if force_after_review_reject else 0  # 这次写代码是不是复查退回来的（㉝）
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
                    has_fail_after = any(e.get("kind") in ("判定", "页面检查")
                                        and e.get("result") not in (PASS, TIMEOUT)
                                        for e in after)
                    if not has_fail_after:
                        # 复查没给结论时：跳过开发员，只重新复查
                        dev_already_done = True

        # 第一次写代码之前收到你跑着时确认的修改：按“接着改”从补测试重新走。
        # 测试没过返工、复查退回时不在这里取（会丢掉报错和复查意见，复查中等 5）——等复查过了交验收前再取
        if rnd == 1 and not dev_already_done and review_round == 0 and "上一次复查不通过" not in dev_prompt \
                and _apply_queued(collab, cdir, card):
            # 回去写代码之前收到你跑着时确认的修改：按“接着改”从补测试重新走
            return _run_card(card, collab_dir, worktree, test_command, dev_adapter, test_adapter, ask_fn, judge_fn,
                             models, allowed, changed_fn, page_command, reviewer_adapter, revert_fn, require_tests,
                             role_models, None, full, draft, red_check)

        if not dev_already_done:
            # 复查打回重改：返工次数不增加，步骤记为“开发”
            if "上一次复查不通过" in dev_prompt or card.rework == 0:
                step_name = "开发"
            else:
                step_name = "返工修改"
            dev = _dispatch(ask_fn, cdir, "开发员", dev_adapter, step_name,
                            dev_prompt, worktree, by_role.get("开发员", models), rnd, test_command=test_command)
            if not dev.ok:
                # 被停下（超时、没动静）时先跑一次本卡测试：已经全过就直接交复查，不再派人重写（第三批 ㊾）
                # 只认“被停下”（超时、看门人停的）；额度用完、报错这类它根本没干成，照旧停下说原因
                # 没有复查员时不走这条：被停下的半成品没人兜底（复查第 2 条）
                stopped = bool(reviewer_adapter) and str(dev.output or "").startswith(("超时", "看门人"))
                quick = judge_fn(test_command, cwd=worktree, timeout=_TEST_TIMEOUT) if stopped else None
                if quick is None or quick.result != PASS:
                    return _worker_failed(collab, cdir, card, "开发员", dev_adapter, dev)
                why = (readable(dev.output, dev_adapter) or agy_error(dev.output) or "没交活").strip().splitlines()
                record(cdir, "接着走", src="程序", dst="复查员",
                       summary="写代码的被停下了，但本卡测试已经全过，直接交复查",
                       say=f"写代码的（{dev_adapter}）被停下了（{(why[-1] if why else '没交活')[:60]}），"
                           f"但本卡测试已经全过，直接交复查。")
            else:
                _save_report(cdir, reply_text(dev))
            _log_step(cdir, step_name, dev_adapter, worktree)

        if effective_allowed is not None:
            changed = [p for p in changed_fn(worktree) if not is_cache(p)]  # 缓存和给工具的放行设置不算改动
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

        resumed_reviews = list(pending_reviews)
        pending_reviews = []
        if resumed_reviews:
            card.transition("待测试")
            card.transition("测试中")
            _save(collab, card)
            verdict = Verdict(PASS, 0, "")
            verdict_file = _log_file("判定")
        else:
            card.transition("待测试")
            card.transition("测试中")
            _save(collab, card)
            left = unresolved_conflicts(worktree, cdir)
            verdict = (Verdict(FAIL, None, "这些文件还有没解决的冲突：" + "、".join(left)) if left
                       else judge_fn(test_command, cwd=worktree, timeout=_TEST_TIMEOUT))
            verdict_file = _log_file("判定")
            _append(cdir / "测试结果.md",
                    f"\n## 第 {card.rework + 1} 轮判定\n结论：{verdict.result}\n"
                    f"退出码：{verdict.returncode}\n\n{verdict.output}\n")
            record(cdir, "判定", src="程序", dst="" if verdict.result == PASS else "开发员",
                   result=verdict.result, round=card.rework + 1, returncode=verdict.returncode,
                   summary=(f"复查退回后第 {review_round} 次改完" if review_round else f"第 {card.rework + 1} 轮")
                           + f"：{_VERDICT_SUMMARY.get(verdict.result, verdict.result)}"
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
                verdict, verdict_file = _with_log(page, "页面检查")

            if verdict.result == PASS:
                # 相关老测试（第三批 ㉜）：本卡测试过了，再跑用到这次改动文件的老测试；不过就当测试没过退回写代码的
                # 有全部测试兜底（和复查同时跑、几个文件同时跑，几分钟）就不先跑相关老测试挡着复查开工（第一期 1-9）
                from collab import fullrun as _fullrun
                covered = full.runner is not None and _fullrun.applicable(worktree)
                olds = _related_old_tests(worktree, cdir, changed_fn)
                if covered:   # 只省掉全量真会跑到的；全量找不到的（例如没有 __init__.py 的子文件夹）照旧先跑（复查第 1 轮）
                    found = set(_fullrun._test_files(worktree))
                    olds = [o for o in olds if Path(o).as_posix() not in found]
                if olds:
                    names = "、".join(Path(o).stem for o in olds)
                    old_v = judge_fn(_old_tests_command(olds), cwd=worktree, timeout=_TEST_TIMEOUT)
                    none_ran = old_v.result == PASS and "Ran 0 tests" in (old_v.output or "")
                    # 超时、环境出错一律不放过（转人工看）；“没过”时逐个比改之前，只把这次改坏的算上（第三批复查第 2 轮）
                    culprits = [] if old_v.result == PASS else (olds if old_v.result != FAIL
                                                                 else _broken_by_change(worktree, olds, judge_fn))
                    broke = bool(culprits)
                    if broke:
                        names = "、".join(Path(o).stem for o in culprits)
                    said = ("都过了" if old_v.result == PASS and not none_ran else
                            "一项都没跑起来（不算过也不算没过，最后全量兜底）" if none_ran else
                            "没过" if broke else "没过，但改之前就不过，不算这次改坏的")
                    record(cdir, "老测试", src="程序", result=old_v.result, returncode=old_v.returncode,
                           summary=f"跑了和这次改动有关的老测试（{names}）：{said}", text=old_v.output)
                    if broke:  # 超时、环境出错也不能放过去（复查第 1 条）；改之前就不过的不算到这次头上（第 3 条）
                        _append(cdir / "测试结果.md",
                                f"\n## 相关老测试没过\n结论：{old_v.result}\n\n{old_v.output}\n")
                        record(cdir, "判定", src="程序", dst="开发员", result=old_v.result, round=card.rework + 1,
                               returncode=old_v.returncode,
                               summary=f"第 {card.rework + 1} 轮：本卡测试过了，但老测试没过（{names}），改之前它们是过的",
                               text=old_v.output)
                        verdict, verdict_file = _with_log(
                            Verdict(old_v.result, old_v.returncode,
                                    f"本卡测试过了，但这些老测试没过，改之前它们是过的（这次改动把原来的功能改坏了，"
                                    f"要改代码让它们也过，不要改老测试）：{names}\n\n{old_v.output}"), "判定")

        full_checked = False
        if verdict.result == PASS and full is not None:
            full.start()  # 复查的同时程序在副本里跑全量（第四轮 59）

        if verdict.result == PASS and reviewer_adapter:
            # 独立复查：由另一个 AI 只读检查改动是否符合需求，写“结论：通过/不通过”
            later_round = _prior_reviews(cdir) > 0   # 退回过以后的复查只看改动、强度降一档（第二批 A5）
            review_models = _lower_effort(by_role.get("复查员", models)) if later_round else by_role.get("复查员", models)
            if resumed_reviews:
                r1 = resumed_reviews[0]
                review_ok = bool(r1.get("ok"))
                review_text = text_of(cdir, r1)
                reply_seq = int(r1.get("seq") or 0)
                _record_review_history(cdir, f"复查（{reviewer_adapter}）", review_text)
                if not review_ok:
                    full.stop() if full is not None else None
                    _escalate(collab, cdir, card,
                              f"复查员（{reviewer_adapter}）没有给出结论，不能算通过")
                    return card, verdict
            else:
                review = _dispatch(ask_fn, cdir, "复查员", reviewer_adapter, "复查",
                                   _review_prompt(cdir, needs, plan_hint, run_hint, worktree, later_round),
                                   worktree, review_models, card.rework + 1, test_command=test_command)
                review_text = reply_text(review)
                _record_review_history(cdir, f"复查（{reviewer_adapter}）", review_text)
                if not review.ok:
                    problem = "" if review.ok else _credit_problem("复查员", reviewer_adapter, review.output)
                    error = "" if review.ok or problem else agy_error(review.output)
                    problem = problem or (f"复查员（{reviewer_adapter}）没有完成：{error}" if error else "")
                    full.stop() if full is not None else None
                    _escalate(collab, cdir, card,
                              problem or f"复查员（{reviewer_adapter}）没有给出结论，不能算通过")
                    return card, verdict
                reply_seq = max((e.get("seq", 0) for e in read_flow(cdir) if e.get("kind") == "回复"
                                 and e.get("src") == "复查员"), default=0)
            prev_reject = _last_reject_conclusion(cdir)
            prev_items = _conclusion_back_problems(prev_reject) if prev_reject is not None else []
            prev_count = len(prev_items)
            data, errors = read_review_list(review_text, prev_count=prev_count)
            verdict_kind = review_verdict(review_text)
            if not errors and _list_contradicts(review_text, data):
                errors = ["正文第一行写的是“结论：不通过”，清单却写“通过”，两处对不上"]
            fixed_text = ""
            if verdict_kind == "unclear":   # “结论：不通过（有一项回归）”这类首行：老认法认不出，按第一行算（11-18 复查 2-3）
                verdict_kind = first_line_verdict(review_text) or "unclear"
            if errors:
                if verdict_kind == "unclear":
                    full.stop() if full is not None else None
                    _escalate(collab, cdir, card,
                              f"复查员（{reviewer_adapter}）没有给出结论，不能算通过")
                    return card, verdict
                if len(resumed_reviews) >= 2:
                    r2 = resumed_reviews[1]
                    fixed_ok = bool(r2.get("ok"))
                    fixed_text = text_of(cdir, r2)
                    reply_seq = int(r2.get("seq") or reply_seq)
                    _record_review_history(cdir, f"复查格式重写（{reviewer_adapter}）", fixed_text)
                else:
                    rewrite = _dispatch(ask_fn, cdir, "复查员", reviewer_adapter, "复查",
                                        _reformat_prompt(cdir, review_text, errors, needs, worktree),
                                        worktree, review_models, card.rework + 1, test_command=test_command)
                    fixed_ok = bool(rewrite.ok)
                    fixed_text = reply_text(rewrite)
                    _record_review_history(cdir, f"复查格式重写（{reviewer_adapter}）", fixed_text)
                    reply_seq = max((e.get("seq", 0) for e in read_flow(cdir) if e.get("kind") == "回复"
                                     and e.get("src") == "复查员"), default=reply_seq)
                fixed_data, fixed_errors = read_review_list(fixed_text, prev_count=prev_count)
                if (verdict_kind == "reject" or first_line_verdict(review_text) == "reject"
                        or _list_contradicts(fixed_text, fixed_data)) \
                        and isinstance(fixed_data, dict) and fixed_data.get("结论") == "通过":
                    # 原话或重写的第一行说不通过，重写的清单却写通过：都不能靠重写放行（11-18 复查 1）
                    fixed_errors = list(fixed_errors) + ["第一行说不通过，重写的清单却写通过"]
                if fixed_ok and not fixed_errors and fixed_data:
                    data = fixed_data
                    verdict_kind = "reject" if data.get("结论") == "不通过" else "pass"
                elif verdict_kind == "reject":
                    # C1.8：重写后还看不懂就停下转人工，原话已存在流转记录里；不再从原话里猜问题、整段退回
                    full.stop() if full is not None else None
                    latest_conc = next((e for e in reversed(_after_revise(read_flow(cdir)))
                                        if e.get("kind") == "复查结论"), None)
                    still_open = prev_items if (latest_conc is not None and latest_conc.get("result") == "退回") else []
                    # 最近一条正式结论已是“通过”的，之前退回的那些算确认修好了，不再说成没确认（C1.8a 复查第 3 轮轻微）
                    _stop_unreadable(collab, cdir, card, reviewer_adapter, review_text, fixed_text, still_open)
                    return card, verdict
                else:
                    full.stop() if full is not None else None
                    _escalate(collab, cdir, card, "复查结论格式不合格，不能自动放行")
                    return card, verdict
            else:
                verdict_kind = "reject" if data.get("结论") == "不通过" else "pass"
            same_files = []
            back_problems, later_problems, verdict_kind = _resolve_review(cdir, data)
            back_texts = [_problem_text(p) for p in back_problems]
            if verdict_kind == "reject" and back_problems:
                same_files = _same_place_new_list(
                    cdir, data, reply_seq, root=worktree, current_back=back_problems,
                )
            prior_rejects = _review_rejects(cdir)
            only_non_severe = (
                bool(back_problems)
                and all(p.get("级别") != "严重" and p.get("类型") not in ("测试写错", "安全或丢数据")
                        for p in back_problems)   # 安全或丢数据退满也不自动放行（复查新规矩 Codex 复查第 1 条）
            )
            if verdict_kind == "reject" and prior_rejects >= MAX_REVIEW_RETURNS and only_non_severe:
                if full is not None:
                    verdict, stop_reason = full.finish(judge_round=card.rework + 1)
                    verdict, verdict_file = _with_log(verdict, "判定")
                    full_checked = True
                    actual_ok = (full.runner is None) or (
                        not full.skipped
                        and not stop_reason
                        and verdict.result == PASS
                        and full.last_actual is not None
                        and full.last_actual.result == PASS
                    )
                    if not actual_ok:
                        _record_deferred(
                            cdir, card, reply_seq, later_problems,
                            back_texts=back_texts, closed=False,
                        )
                        why = stop_reason or (
                            "全部测试被跳过（没有可跑的 Python 测试），退回次数已用完，不能自动放行，需要你看看"
                            if full.skipped else
                            "全部测试显示改坏了老功能，退回次数已用完，需要你看看"
                            if verdict.result != PASS else
                            "全部测试没有全部通过（或没跑成），退回次数已用完，需要你看看"
                        )
                        issues, hidden, severe_hidden, count = _plain_summary_items(back_problems)
                        more = _hidden_words(hidden, severe_hidden)
                        left = f"本轮还有 {count} 件事没改好：{_issues_words(issues)}{more}。" if issues else ""
                        say = f"{why}。{left}" if left else why
                        stop_data = _stop_manifest(data, back_problems)
                        _escalate(
                            collab, cdir, card, why, text=verdict.output or None, say=say,
                            清单=stop_data if stop_data else None,
                            退回问题=back_problems if back_problems else None,
                            issues=issues or None, hidden=hidden or None,
                        )
                        return card, verdict
                merged_deferred, seen_deferred = [], set()
                for p in later_problems + back_problems:
                    key = _problem_key(p)
                    if key not in seen_deferred:
                        seen_deferred.add(key)
                        merged_deferred.append(p)
                later_problems = merged_deferred
                back_problems, back_texts, verdict_kind = [], [], "pass"
            _, new_words, old_words = _record_deferred(
                cdir, card, reply_seq, later_problems,
                back_texts=back_texts if verdict_kind == "reject" else None,
                closed=(verdict_kind == "pass"),
            )
            extra = {"清单": data} if data else {}
            if verdict_kind == "reject":
                if back_problems:
                    extra["退回问题"] = back_problems
                record(cdir, "复查结论", src="程序", result="退回", reply=reply_seq, later=len(later_problems),
                       summary=f"复查退回 {len(back_problems) or len(back_texts) or 1} 件", **extra)
            else:
                pass_bits = []
                if new_words:
                    pass_bits.append(f"还剩 {len(new_words)} 件小问题已记以后再修")
                if old_words:
                    pass_bits.append(f"另有 {len(old_words)} 件开工前就有的问题，已记进台账")
                pass_summary = "复查通过" + (f"（{'，'.join(pass_bits)}）" if pass_bits else "")
                record(cdir, "复查结论", src="程序", result="通过", reply=reply_seq, later=len(later_problems),
                       summary=pass_summary, **extra)
            if verdict_kind == "reject":
                if full is not None:
                    full.stop()  # 复查不通过要改代码，这一轮全量不用跑完
                stopped, rejects = _handle_review_reject(
                    collab, cdir, card, ask_fn=ask_fn, reviewer_adapter=reviewer_adapter,
                    by_role=by_role, models=models, worktree=worktree, needs=needs, changed_fn=changed_fn,
                    draft=draft, rnd=rnd, data=data, back_problems=back_problems,
                    review_text=review_text,
                )
                if stopped:
                    return card, verdict
                dev_prompt = _dispatch_review_return(
                    cdir, ask_fn, test_adapter, by_role.get("测试员", models), worktree, needs,
                    test_command, revert_fn, rnd,
                    back_problems=back_problems, same_files=same_files,
                    plan_hint=_plan_hint(cdir, False), run_hint=run_hint, revise_bit=revise_bit,
                    review_file=cdir / "复查结果.md", check_done=False,
                    judge_fn=judge_fn, red_check=red_check,
                ) + _conflict_note(worktree, cdir)
                last_output = None
                review_round = rejects
                continue

        if verdict.result == PASS and full is not None and not full_checked:
            verdict, stop_reason = full.finish(judge_round=card.rework + 1)
            verdict, verdict_file = _with_log(verdict, "判定")   # 全量的完整报错也记在测试结果里（第四轮 54）
            if stop_reason:
                _escalate(collab, cdir, card, stop_reason)
                return card, verdict

        if verdict.result == PASS and _apply_queued(collab, cdir, card):
            # 复查都过了、要交你验收前收到修改：用户 11-16 定按新要求再走一轮
            return _run_card(card, collab_dir, worktree, test_command, dev_adapter, test_adapter, ask_fn, judge_fn,
                             models, allowed, changed_fn, page_command, reviewer_adapter, revert_fn, require_tests,
                             role_models, None, full, draft, red_check)

        if verdict.result == PASS:
            card.transition("待验收")
            _save(collab, card)
            record(cdir, "结束", src="程序", dst="你", state="待验收",
                   summary=f"做完了，等你确认（{rework_words(card.rework, cdir)}）")
            return card, verdict

        if verdict.result in (ENV_ERROR, TIMEOUT):
            # 环境或超时问题不是代码的错，不计返工轮数，直接叫 Claude
            _escalate(collab, cdir, card, f"{_VERDICT_SUMMARY[verdict.result]}，需要你看看")
            return card, verdict

        if last_output is not None and verdict.output.strip() == last_output.strip():
            # 同样的报错连续出现两次：说明在原地打转，再返工也是白费，立刻转人工
            _append(cdir / "返工记录.md",
                    f"- {datetime.now():%Y-%m-%d %H:%M} 同样的报错连续出现两次，判定为原地打转，转需Claude介入\n")
            _escalate(collab, cdir, card, "同样的报错连续出现两次，判定为原地打转")
            return card, verdict
        last_output = verdict.output

        review_round = 0
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
                   summary=f"返工 {card.rework} 次后测试仍没过，需要你看看")
            return card, verdict
        dev_prompt = (f"{_rules('写代码')}上一版测试没过，测试输出如下，请修复后重新提交。\n\n"
                      f"{_short_test_note(cdir, verdict.output, verdict_file)}\n\n"
                      f"任务卡：{needs}。{revise_bit}{_DEV_TEST_NOTE}{plan_hint}{run_hint}{_REPORT_ASK}")
