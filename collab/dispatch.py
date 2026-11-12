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
import subprocess
import time
from datetime import datetime
from pathlib import Path

from collab.agents import QUIET_LIMIT, ask as default_ask, readable
from collab.flow import read_flow, record, reply_summary, text_of
from collab.judge import Verdict, ENV_ERROR, FAIL, PASS, TIMEOUT, judge as default_judge
from collab.scope import is_cache, out_of_scope
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
    """干活的人最后那条回复；旧的假实现没有 final 时从完整输出里取。"""
    return (getattr(result, "final", "") or readable(result.output)).strip()


def _command_text(test_command) -> str:
    if isinstance(test_command, (list, tuple)):
        return str(test_command[-1]) if test_command else ""
    return str(test_command or "")


def _run_hint(test_command) -> str:
    cmd = _command_text(test_command).strip()
    if not cmd:
        return ""
    return f"本卡的测试命令：{cmd}。要确认就只跑这一条，不要跑全部测试（全部测试留到最后验收）。"


_REPORT_ASK = "完成后，在回复的最后一行写“汇报：”加不超过100字的说明（改了什么、测试情况）。不要另建汇报文件。"
_TEST_FILES = "测试文件.json"
_BEFORE_TESTER = "测试前改动.json"
_TEST_SNAPSHOT = "测试快照"
_DEV_TEST_NOTE = "测试文件由测试员负责，不要修改；改了也会在判定前被恢复。"


def _plan_hint(cdir: Path) -> str:
    plan = cdir / "方案.md"
    if plan.is_file():
        return f"请严格按方案文件 {plan} 执行，尤其遵守其中「明确不做什么」。方案里如果写着只输出方案、不修改文件，那只约束写方案这一步，不约束你。"
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
    parent = str(Path(rel).parent)
    if parent in ("", "."):
        return False
    out = subprocess.run(["git", "ls-files", "--", parent], cwd=str(worktree), capture_output=True, text=True).stdout
    return bool(out.strip())


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
    "开发": "按测试写代码，让测试全部通过",
    "返工修改": "测试没过，带着测试输出退回修改",
    "复查": "只读复查：对照需求检查改动",
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


def _dispatch(ask_fn, cdir: Path, role: str, adapter: str, step: str, prompt: str,
              worktree, models, round_no: int, test_command=None):
    """派一次活：开始时记“派活”（含原文、预留实时输出），干完记“回复”（含全文、用时）。"""
    restore = cursor_permissions(worktree, test_command) if adapter == "cursor" else None
    sent = record(cdir, "派活", src="调度员", dst=role, tool=adapter, step=step, round=round_no,
                  summary=_STEP_SUMMARY[step], text=prompt, live=True)
    started = time.time()
    extra = {}
    if _accepts(ask_fn, "on_quiet"):
        def on_quiet(seconds, role=role, adapter=adapter):
            minutes = max(1, seconds // 60)
            left = max(1, (QUIET_LIMIT - seconds) // 60)
            record(cdir, "提醒", src="程序", dst="你", role=role, tool=adapter,
                   summary=f"{role}（{adapter}）已经 {minutes} 分钟没有动静（没改文件，也没有新输出）。"
                           f"可以再等等，再过约 {left} 分钟还没动静程序会自动停下；也可以点“停下”再点“接着跑”。")
        extra["on_quiet"] = on_quiet
    try:
        result = ask_fn(adapter, prompt, worktree, expect_file=None, models=models,
                        live_path=str(cdir / sent["live"]), **extra)
    finally:
        if restore:
            restore()
    text = reply_text(result)
    record(cdir, "回复", src=role, dst="调度员", tool=adapter, step=step, round=round_no,
           ok=bool(result.ok), seconds=round(time.time() - started), reply_to=sent["seq"],
           summary=reply_summary(step, text, result.ok), text=text)
    return result


def _escalate(collab: Path, cdir: Path, card: Card, reason: str, cause: str = "", text=None) -> None:
    """转需Claude介入，并在流转记录里写明原因。cause="复查不通过" 时用户可以接受剩下的问题（只认这个标记，不认文字）。"""
    record(cdir, "转人工", src="程序", dst="调度员", summary=reason, cause=cause or None, text=text)
    card.transition("需Claude介入")
    _save(collab, card)


def _worker_failed(collab: Path, cdir: Path, card: Card, role: str, adapter: str, result):
    """干活的人没完成（报错、超时、被看门人停掉）：把原因写进返工记录，再转人工。"""
    reason = (readable(result.output) or "没有返回任何内容").strip().splitlines()
    tail = reason[-1][:300] if reason else "没有返回任何内容"
    _append(cdir / "返工记录.md",
            f"- {datetime.now():%Y-%m-%d %H:%M} {role}（{adapter}）没有完成：{tail}\n")
    _escalate(collab, cdir, card, _credit_problem(role, adapter, result.output) or f"{role}（{adapter}）没有完成：{tail}")
    return card, None


MAX_REVIEW_RETURNS = 2  # 复查不通过自动退回写代码的最多几次（用户 2026-11-12 定）
_NOTES_LIMIT = 8000  # 交给写代码的复查意见最长多少字


def _review_rejects(cdir: Path) -> int:
    """这次开工（最后一条追加要求之后）复查不通过了几次，含刚才这次。"""
    count = 0
    for e in _after_revise(read_flow(cdir)):
        if e.get("kind") == "回复" and e.get("src") == "复查员" and e.get("ok") is True:
            body = text_of(cdir, e) or str(e.get("summary") or "")
            if review_verdict(body) == "reject":
                count += 1
    return count


def _first_issues(text: str, limit: int = 200) -> str:
    """转卡住时原因里带上复查意见的头几行，用户一眼能看到剩下什么问题。"""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip() and not _REVIEW_VERDICT.match(ln)]
    joined = "；".join(lines)
    return (joined[:limit] + "……") if len(joined) > limit else (joined or "复查员没有写下详细意见")


def _review_return_prompt(notes: str, needs, revise_bit: str, plan_hint: str) -> str:
    notes = (notes or "").strip() or "（复查员没有写下详细意见）"
    if len(notes) > _NOTES_LIMIT:
        notes = notes[:_NOTES_LIMIT] + "\n……（后面省略）"
    return (f"{_READ_RULES}上一次复查不通过。请按复查意见修改后再提交，不算一次返工。\n\n"
            f"{notes}\n\n任务卡：{needs}。{revise_bit}{_DEV_TEST_NOTE}{plan_hint}{_REPORT_ASK}")


_VERDICT_SUMMARY = {PASS: "测试全部通过", FAIL: "测试没过", ENV_ERROR: "测试环境出错（不是代码的问题）",
                    TIMEOUT: "测试超时"}


def run_card(card: Card, collab_dir, worktree, test_command, dev_adapter, test_adapter,
             ask_fn=default_ask, judge_fn=default_judge, models=None,
             allowed=None, changed_fn=None, page_command=None, reviewer_adapter=None,
             revert_fn=None, require_tests=False, role_models=None):
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

    # 复查不通过后接着跑：只看最后一条追加要求之后的记录
    force_after_review_reject, review_notes = _review_reject_pending(cdir, past)

    # 第一步：测试员按验收标准先写测试（测试先行；如果断点续跑前已经写完交活，直接跳过）
    run_hint = _run_hint(test_command)
    if not test_done:
        before_map = _remember_before_tester(cdir, worktree)
        old_tests = [p for p in _load_test_files(cdir) if (Path(worktree) / p).is_file()]
        if revise_note and old_tests:
            # 继续改：只为追加要求补测试，不整个重写（68b 里重写一遍 15 分钟，⑭）
            tester_prompt = (f"{_READ_RULES}任务卡 {needs} 的测试之前已经写好：{'、'.join(old_tests)}。"
                             f"现在有追加要求，只为追加要求补测试（可以在原测试文件里追加，或新建测试文件），"
                             f"已有的测试不要重写；追加要求不需要新测试就直接说明。"
                             f"只改测试文件，不改产品代码，改了也会被撤掉。{revise_bit}{run_hint}{_tester_conflict_note(cdir)}")
        else:
            tester_prompt = (f"{_READ_RULES}按任务卡 {needs} 的验收标准编写自动化测试，放进项目的测试目录。"
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
            _escalate(collab, cdir, card, f"测试员（{test_adapter}）没有写出任何测试：{reply_summary('写测试', readable(prep.output), True)}")
            return card, None

    tester_files = _load_test_files(cdir)
    effective_allowed = list(allowed) + tester_files if allowed is not None else None

    # 第二步：开发员照着测试写代码，直到测试通过
    dev_prompt = (f"{_READ_RULES}按任务卡实现功能：{needs}。测试已经写好，请让它们全部通过。"
                  f"{revise_bit}{_DEV_TEST_NOTE}{plan_hint}{run_hint}{_REPORT_ASK}")
    last_output = None  # 上一轮测试的报错，用来发现“原地打转”
    past_fails = [e for e in past if e.get("kind") in ("判定", "页面检查") and e.get("result") == FAIL]
    if past_fails and card.rework > 0:
        last_output = text_of(cdir, past_fails[-1])
        dev_prompt = (f"{_READ_RULES}上一版测试没过，测试输出如下，请修复后重新提交。\n\n{last_output}\n\n"
                      f"任务卡：{needs}。{revise_bit}{_DEV_TEST_NOTE}{plan_hint}{run_hint}{_REPORT_ASK}")
    if force_after_review_reject:
        dev_prompt = _review_return_prompt(review_notes, needs, revise_bit, plan_hint + run_hint)

    dev_prompt += _conflict_note(worktree, cdir)

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
                            dev_prompt, worktree, by_role.get("开发员", models), rnd, test_command=test_command)
            if not dev.ok:
                return _worker_failed(collab, cdir, card, "开发员", dev_adapter, dev)
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

        card.transition("待测试")
        card.transition("测试中")
        _save(collab, card)
        left = unresolved_conflicts(worktree, cdir)
        verdict = (Verdict(FAIL, None, "这些文件还有没解决的冲突：" + "、".join(left)) if left
                   else judge_fn(test_command, cwd=worktree, timeout=_TEST_TIMEOUT))
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
                               f"{plan_hint}{run_hint}"
                               f"{_tester_files_hint(cdir)}"
                               f"台账、归档这些流程记录由程序在施工结束后补写，现在没有不算问题。"
                               f"第一行写“结论：通过”或“结论：不通过”，之后列出问题。不要修改任何文件。",
                               worktree, by_role.get("复查员", models), card.rework + 1, test_command=test_command)
            review_text = reply_text(review)
            _append(cdir / "复查结果.md", f"\n## 复查（{reviewer_adapter}）\n{review_text}\n")
            verdict_kind = review_verdict(review_text)
            if not review.ok or verdict_kind == "unclear":
                problem = "" if review.ok else _credit_problem("复查员", reviewer_adapter, review.output)
                _escalate(collab, cdir, card,
                          problem or f"复查员（{reviewer_adapter}）没有给出结论，不能算通过")
                return card, verdict
            if verdict_kind == "reject":
                rejects = _review_rejects(cdir)
                if rejects > MAX_REVIEW_RETURNS:
                    _escalate(collab, cdir, card,
                              f"复查员（{reviewer_adapter}）复查不通过（已自动退回 {MAX_REVIEW_RETURNS} 次）："
                              f"{_first_issues(review_text)}", cause="复查不通过", text=review_text)
                    return card, verdict
                # 复查不通过自动退回写代码的（用户 11-12 定最多 2 次），只给复查员最后写的结论，不算返工
                record(cdir, "退回", src="程序", dst="开发员",
                       summary=f"复查不通过，自动退回写代码的（第 {rejects} 次，最多 {MAX_REVIEW_RETURNS} 次）")
                card.transition("开发中")
                _save(collab, card)
                dev_prompt = _review_return_prompt(review_text, needs, revise_bit, plan_hint + run_hint) \
                    + _conflict_note(worktree, cdir)
                last_output = None
                continue

        if verdict.result == PASS:
            card.transition("待验收")
            _save(collab, card)
            record(cdir, "结束", src="程序", dst="你", state="待验收",
                   summary=f"做完了，等你确认（返工 {card.rework} 次）")
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
                      f"任务卡：{needs}。{revise_bit}{_DEV_TEST_NOTE}{plan_hint}{run_hint}{_REPORT_ASK}")
