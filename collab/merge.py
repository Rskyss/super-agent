"""验收通过后合并：只收允许范围内的改动，失败时主项目退回原样。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from collab.archive import archive_card
from collab.flow import card_title
from collab.dispatch import CONFLICTS, _changed_list, has_conflict_marks, unresolved_conflicts
from collab.flow import read_flow, record
from collab.judge import PASS, judge
from collab.ledger import ledger_add
from collab.scope import _is_secret, _matches, is_cache, read_allowed
from collab.state import Card
from collab.workspace import render_board


_GIT_TIMEOUT = 300
PROGRESS = "合并进行中.json"  # 已经快进、测试还没跑完时存在。中途断了靠它接上或退回
SYNCED_FROM = "同步分支.txt"  # 副本最后同步的是哪个分支
_BUSY_MARKS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")


class _Stop(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _git(cwd, *args, check=True):
    try:
        proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=_GIT_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise _Stop("git 一直没有结束，这次先停下", str(exc)) from exc
    if check and proc.returncode != 0:
        raise _Stop("没能放进项目", (proc.stderr or proc.stdout or "").strip())
    return proc


def _card_dir(project: Path, card_id: str) -> Path:
    return project / ".collab" / "cards" / card_id


_title = card_title


def _fail(project: Path, card_id: str, reason: str, detail: str = "") -> None:
    cdir = _card_dir(project, card_id)
    if not cdir.is_dir():
        return
    record(cdir, "合并失败", src="程序", dst="你", summary=reason, text=detail or "")


def _keep(path: str, allowed, tester: set) -> bool:
    path = path.replace("\\", "/").strip()
    if not path or path in (".", ".."):
        return False
    parts = [p for p in path.split("/") if p]
    if "node_modules" in parts or path.startswith(".collab/") or path == ".collab":
        return False
    if is_cache(path) or _is_secret(path):
        return False
    if path in tester:
        return True
    if not allowed:
        return False
    return any(_matches(path, pattern) for pattern in allowed)


def _scope(cdir: Path) -> tuple:
    need = ""
    req = cdir / "需求卡.md"
    if req.is_file():
        need = req.read_text(encoding="utf-8")
    allowed = read_allowed(need) or []
    tester = set()
    listed = cdir / "测试文件.json"
    if listed.is_file():
        try:
            data = json.loads(listed.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = []
        if isinstance(data, list):
            tester = {str(p).replace("\\", "/") for p in data}
    return allowed, tester


def _selected(worktree: Path, cdir: Path) -> list:
    allowed, tester = _scope(cdir)
    out = []
    for path in _changed_list(worktree):
        if _keep(path, allowed, tester):
            out.append(path.replace("\\", "/"))
    return out


def _stage(worktree: Path, files: list) -> None:
    """把这些文件放进提交。已经删掉的文件走另一条路，上次放过一次也不会报错。"""
    here = [name for name in files if (worktree / name).exists() or (worktree / name).is_symlink()]
    gone = [name for name in files if name not in here]
    if here:
        _git(worktree, "add", "--", *here)
    if gone:
        _git(worktree, "rm", "-q", "--cached", "--ignore-unmatch", "--", *gone)


def _identity_args(project: Path) -> list:
    name = _git(project, "config", "user.name", check=False).stdout.strip()
    email = _git(project, "config", "user.email", check=False).stdout.strip()
    if name and email:
        return []
    return ["-c", "user.name=超级助手", "-c", "user.email=super-agent@localhost"]


def _ahead(worktree: Path, base: str) -> int:
    proc = _git(worktree, "rev-list", "--count", f"{base}..HEAD", check=False)
    try:
        return int((proc.stdout or "0").strip() or "0")
    except ValueError:
        return 0


def _run_tests(command: str, cwd: Path):
    return judge(["/bin/bash", "-o", "pipefail", "-c", command], cwd=str(cwd), timeout=900)


def _note(cdir: Path, summary: str, detail: str = "") -> None:
    record(cdir, "合并", src="程序", dst="你", summary=summary, text=detail or "")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _current_branch(project: Path) -> str:
    return _git(project, "symbolic-ref", "--quiet", "--short", "HEAD", check=False).stdout.strip()


def _ready_branch(project: Path) -> str:
    """项目必须停在一个分支上，并且没有做到一半的合并或变基。"""
    for mark in _BUSY_MARKS:
        found = _git(project, "rev-parse", "--git-path", mark, check=False).stdout.strip()
        if found and (project / found).exists():
            raise _Stop("项目里有一个没做完的 git 操作，先处理完再放进项目")
    branch = _current_branch(project)
    if not branch:
        raise _Stop("项目现在不在任何分支上，先切回一个分支再放进项目")
    return branch


def _load_progress(cdir: Path):
    path = cdir / PROGRESS
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("base") and data.get("merged") else None


def _save_progress(cdir: Path, base: str, branch: str, merged: str) -> None:
    path = cdir / PROGRESS
    spare = path.with_suffix(".tmp")
    spare.write_text(json.dumps({"base": base, "branch": branch, "merged": merged}, ensure_ascii=False),
                     encoding="utf-8")
    spare.replace(path)


def _clear_progress(cdir: Path) -> None:
    try:
        (cdir / PROGRESS).unlink()
    except OSError:
        pass


def _resume_point(project: Path, cdir: Path, saved: dict) -> tuple:
    """上次中途断了：还停在合并后就接着验证；还没合并就从头来；项目有新变动就不动它。"""
    head = _git(project, "rev-parse", "HEAD").stdout.strip()
    if _current_branch(project) == saved.get("branch"):
        if head == saved["merged"]:
            return saved["base"], saved["branch"]
        if head == saved["base"]:
            _clear_progress(cdir)
            return "", ""
    raise _Stop("上次放进项目停在半路，之后项目又有了新变动，这次没有动你的项目。"
                "这张卡的改动可能已经在项目里，没有验证过。可以点“放弃这个需求”结束这张卡。")


def _roll_back(project: Path, cdir: Path, detail: str) -> None:
    """测试没过才退回。退回前核对：用户在测试期间提交或切了分支，就不能动。"""
    saved = _load_progress(cdir) or {}
    head = _git(project, "rev-parse", "HEAD").stdout.strip()
    if _current_branch(project) != saved.get("branch") or head != saved.get("merged"):
        raise _Stop("放进项目后测试没过。测试期间项目有了新提交或换了分支，所以没有自动退回。"
                    f"这次放进去的提交是 {str(saved.get('merged'))[:8]}。", detail)
    reset = _git(project, "reset", "--keep", saved["base"], check=False)
    if reset.returncode != 0 or _git(project, "rev-parse", "HEAD").stdout.strip() != saved["base"]:
        raise _Stop("放进项目后测试没过，而且没能退回：测试改动了项目里的文件。撤销这些改动后，点“放弃这个需求”退回。",
                    detail + "\n" + (reset.stderr or ""))
    _clear_progress(cdir)
    raise _Stop("放进项目后测试没过，已退回放进去之前的提交", detail)


def undo_interrupted(project, card_id: str) -> str:
    """放弃一张合并到一半的卡：还停在合并后就退回；用户有新变动就不动。返回要告诉用户的话。"""
    project = Path(project)
    cdir = _card_dir(project, card_id)
    saved = _load_progress(cdir)
    if not saved:
        return ""
    head = _git(project, "rev-parse", "HEAD", check=False).stdout.strip()
    if head == saved["base"]:  # 还没快进就断了，项目没变
        _clear_progress(cdir)
        return ""
    same_place = _current_branch(project) == saved.get("branch") and head == saved["merged"]
    if not same_place:
        _clear_progress(cdir)
        return f"项目有了新变动，没有自动撤回。这张卡的提交是 {saved['merged'][:8]}，可能还在项目里。"
    reset = _git(project, "reset", "--keep", saved["base"], check=False)
    if reset.returncode != 0:
        raise RuntimeError("没能退回：项目里有文件和这次改动冲突，先撤销这些改动再试。")
    _clear_progress(cdir)
    return "已退回放进去之前的提交。"


def _commit_work(project: Path, worktree: Path, cdir: Path, card_id: str, base: str) -> None:
    left = unresolved_conflicts(worktree, cdir)
    if left:
        raise _Stop("这些文件还有没解决的冲突：" + "、".join(left) + "。点“不满意，继续改”让开发员处理。")
    files = _selected(worktree, cdir)
    if files:
        _stage(worktree, files)
        done = _git(worktree, *_identity_args(project), "commit", "-qm", f"{card_id}：{_title(cdir, card_id)}",
                    check=False)
        if done.returncode != 0:
            raise _Stop("提交没成功：项目的提交检查没过，详情在这张卡的记录里",
                        (done.stderr or done.stdout or "").strip())
    if not files and _ahead(worktree, base) == 0:
        raise _Stop("这张卡没有改动可以放进项目")
    changed = _names(worktree, "diff", "--name-only", f"{base}...HEAD")
    # 卡片版本和项目现在一模一样的文件不算这张卡带来的改动（中途补的测试先进了项目、再搬进副本，68b ㉔）
    differs = set(_names(worktree, "diff", "--name-only", base, "HEAD"))
    changed = [name for name in changed if name in differs]
    allowed, tester = _scope(cdir)
    stray = [name for name in changed if not _keep(name, allowed, tester)]
    if stray:
        raise _Stop("这张卡的提交里有不该进项目的文件：" + "、".join(stray) + "。这次没有放进项目。")
    marked = [name for name in changed if has_conflict_marks(worktree / name)]
    if marked:
        raise _Stop("这些文件还有没解决的冲突：" + "、".join(marked) + "。点“不满意，继续改”让开发员处理。")


def _check_overlap(project: Path, card_id: str) -> None:
    names = _git(project, "diff", "--name-only", f"HEAD...card/{card_id}", check=False).stdout.splitlines()
    dirty = set(_changed_list(project))
    overlap = [name for name in names if name in dirty]
    if overlap:
        shown = "、".join(overlap)
        raise _Stop(f"你的项目里这些文件也有没提交的改动：{shown}。先提交或撤销后再放进项目。")


def _fast_forward(project: Path, worktree: Path, cdir: Path, card_id: str, base: str, branch: str,
                  command: str) -> None:
    card_ref = f"card/{card_id}"
    behind = _git(project, "merge-base", "--is-ancestor", base, card_ref, check=False).returncode != 0
    if behind:
        replay = _git(worktree, *_identity_args(project), "rebase", "--autostash", base, check=False)
        if replay.returncode != 0:
            clash = _git(worktree, "diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
            _git(worktree, "rebase", "--abort", check=False)
            text = (replay.stderr or replay.stdout or "").strip()
            if clash:
                raise _Stop("和项目里新的改动有冲突：" + "、".join(clash) + "。点“不满意，继续改”让开发员处理。", text)
            raise _Stop("改好的版本没法同步项目里新的改动，详情在这张卡的记录里", text)
        verdict = _run_tests(command, worktree)
        if verdict.result != PASS:
            raise _Stop("同步项目新内容后测试没过，先不放进项目", verdict.output or "")
    tip = _git(project, "rev-parse", card_ref).stdout.strip()
    _save_progress(cdir, base, branch, tip)
    fast = _git(project, "merge", "--ff-only", card_ref, check=False)
    if fast.returncode != 0:
        _clear_progress(cdir)
        raise _Stop("放进项目时有文件挡住了，这次没有动你的项目", (fast.stderr or fast.stdout or "").strip())


def _names(cwd: Path, *args) -> list:
    """git 输出的文件名：中文和空格都原样保留。"""
    out = _git(cwd, *args, "-z", check=False).stdout
    return [name for name in out.split("\0") if name]


def _clash_kinds(worktree: Path) -> tuple:
    """有冲突的文件分两种：留下了冲突标记的（开发员能解决），一边删了或不是文本的（开发员看不出来）。"""
    clash = sorted(set(_names(worktree, "diff", "--name-only", "--diff-filter=U")))
    marked = [name for name in clash if has_conflict_marks(worktree / name)]
    return marked, [name for name in clash if name not in marked]


def sync_copy(project, card_id: str) -> list:
    """修改前把项目里新的内容同步进副本。有冲突的文件留下冲突标记交给开发员，返回这些文件。

    做法是把这张卡的改动重新放到项目最新内容上，所以开发员看到的还是“只有这张卡的改动”。
    原来的进度留在 card-backup 分支。任何一步不成，都退回同步前，并抛 RuntimeError 说明原因。"""
    project = Path(project)
    cdir = _card_dir(project, card_id)
    worktree = project.parent / f"{project.name}-worktrees" / card_id
    if not worktree.is_dir():
        return []
    try:
        branch = _ready_branch(project)
        latest = _git(project, "rev-parse", "HEAD").stdout.strip()
        if _git(worktree, "merge-base", "--is-ancestor", latest, "HEAD", check=False).returncode == 0:
            return []
        still_open = unresolved_conflicts(worktree, cdir)  # 上次的冲突还没解决，不能当成解决了
        files = _selected(worktree, cdir)
        if files:
            _stage(worktree, files)
            _git(worktree, *_identity_args(project), "commit", "-qm", f"{card_id}：同步前的进度", "--no-verify")
        tip = _git(worktree, "rev-parse", "HEAD").stdout.strip()
        has_work = _git(worktree, "rev-list", "--count", f"{latest}..{tip}").stdout.strip() != "0"
    except _Stop as exc:
        raise RuntimeError(exc.reason) from exc
    if has_work:
        _git(worktree, "branch", "-f", f"card-backup/{card_id}", tip, check=False)
    try:
        _git(worktree, "reset", "-q", "--hard", latest)
        merged = _git(worktree, "-c", "merge.ff=true", "merge", "--squash", tip, check=False)
        marked, stuck = _clash_kinds(worktree)
        if stuck:
            raise _Stop("这些文件没法自动同步（一边删了，或者不是文本）：" + "、".join(stuck)
                        + "。这张卡没有改动。可以点“放弃这个需求”，或者先在项目里处理这些文件。")
        if merged.returncode != 0 and not marked:
            raise _Stop("改好的版本没法同步项目里新的内容，这张卡没有改动。", merged.stderr or merged.stdout or "")
        _git(worktree, "reset", "-q")
    except _Stop as exc:
        _git(worktree, "reset", "-q", "--hard", tip, check=False)
        _git(worktree, "merge", "--abort", check=False)
        raise RuntimeError(exc.reason) from exc
    clash = sorted(set(marked) | {name for name in still_open if has_conflict_marks(worktree / name)})
    (cdir / SYNCED_FROM).write_text(branch, encoding="utf-8")
    path = cdir / CONFLICTS
    if clash:
        path.write_text(json.dumps(clash, ensure_ascii=False), encoding="utf-8")
    elif path.exists():
        path.unlink()
    for name in clash:  # 测试文件冲突：解决之前不能再被快照写回去
        try:
            (cdir / "测试快照" / name).unlink()
        except OSError:
            pass
    summary = "已同步项目里新的内容" + (f"，{len(clash)} 个文件有冲突，交给开发员处理" if clash else "")
    record(cdir, "同步", src="程序", dst="开发员", summary=summary, text="\n".join(clash))
    return clash


def merge_unfinished(project) -> str:
    """有张卡的合并停在半路时，项目里可能有没验证的代码，别的卡不能从这里开工。返回卡片编号。"""
    project = Path(project)
    root = project / ".collab" / "cards"
    if not root.is_dir():
        return ""
    for cdir in sorted(root.iterdir(), key=lambda p: p.name):
        saved = _load_progress(cdir) if cdir.is_dir() else None
        if not saved:
            continue
        try:
            if Card.load(cdir / "状态.json").state != "待验收":
                continue
        except (OSError, ValueError, KeyError):
            continue
        if _git(project, "merge-base", "--is-ancestor", saved["merged"], "HEAD", check=False).returncode == 0:
            return cdir.name
    return ""


def merge_card(project, card_id: str) -> dict:
    project = Path(project)
    try:
        _merge(project, card_id)
        return {"ok": True, "reason": ""}
    except _Stop as exc:
        _fail(project, card_id, exc.reason, exc.detail)
        return {"ok": False, "reason": exc.reason}
    except Exception as exc:  # 任何意外都记下来，不能把合并弄崩
        reason = "没能放进项目"
        _fail(project, card_id, reason, str(exc))
        return {"ok": False, "reason": reason}


def _merge(project: Path, card_id: str) -> None:
    cdir = _card_dir(project, card_id)
    state_path = cdir / "状态.json"
    if not state_path.is_file():
        raise _Stop("找不到这张卡片")
    card = Card.load(state_path)
    if card.state != "待验收":
        raise _Stop("这张卡现在还不能放进项目")
    worktree = project.parent / f"{project.name}-worktrees" / card_id
    if not worktree.is_dir() and not (cdir / PROGRESS).is_file():  # 放进一半断了：不用副本也能接着验证或退回
        raise _Stop("改好的版本不在了，没法放进项目")
    start = cdir / "开工.json"
    command = ""
    if start.is_file():
        try:
            command = str(json.loads(start.read_text(encoding="utf-8")).get("test_command") or "").strip()
        except (OSError, ValueError, TypeError):
            command = ""
    if not command:
        raise _Stop("还没有测试命令，没法放进项目")

    saved = _load_progress(cdir)
    if saved:
        base, branch = _resume_point(project, cdir, saved)
    else:
        base, branch = "", ""
    if not base:
        branch = _ready_branch(project)
        synced = _read_text(cdir / SYNCED_FROM)
        if synced and synced != branch:
            raise _Stop(f"这张卡同步的是 {synced} 分支的内容，现在项目在 {branch} 分支。先切回 {synced} 再放进项目，"
                        "不然那个分支的提交会一起进来。")
        base = _git(project, "rev-parse", "HEAD").stdout.strip()
        _commit_work(project, worktree, cdir, card_id, base)
        _check_overlap(project, card_id)
        _fast_forward(project, worktree, cdir, card_id, base, branch, command)

    verdict = _run_tests(command, project)
    if verdict.result != PASS:
        _roll_back(project, cdir, verdict.output or "")

    card.transition("已合并")
    card.save(state_path)
    _clear_progress(cdir)
    render_board(project / ".collab")
    summary = f"已放进 {branch}"
    record(cdir, "合并", src="程序", dst="你", summary=summary)
    ledger_add(project, f"{card_id}：{summary}")
    try:
        archive_card(project, card_id, _title(cdir, card_id))
    except Exception as exc:
        _note(cdir, "记录没能存入项目文档", str(exc))
    remove_copy(project, card_id)


def remove_copy(project, card_id: str) -> None:
    """删掉改好的版本。没有就不做；删失败只记一条。分支不删。"""
    project = Path(project)
    cdir = _card_dir(project, card_id)
    worktree = project.parent / f"{project.name}-worktrees" / card_id
    if not worktree.exists():
        return
    try:
        removed = _git(project, "worktree", "remove", "--force", str(worktree), check=False)
    except (_Stop, OSError) as exc:  # git 太慢或运行不了：卡片已经结束，只记一条
        if cdir.is_dir():
            _note(cdir, "改好的版本没能删掉", str(exc))
        return
    if removed.returncode != 0 and cdir.is_dir():
        _note(cdir, "改好的版本没能删掉", (removed.stderr or removed.stdout or "").strip())
