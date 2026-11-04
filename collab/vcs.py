"""版本与副本：开工前打标记、为每张卡建独立 worktree。非 git 项目直接拒绝。"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class NotGitRepo(Exception):
    pass


def _git(cwd, *args) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def is_git_repo(path) -> bool:
    try:
        return _git(path, "rev-parse", "--is-inside-work-tree") == "true"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def snapshot(repo, label: str) -> str:
    """在当前 HEAD 打标记，返回标记名。用于出事时一键回到开工前。"""
    if not is_git_repo(repo):
        raise NotGitRepo(f"{repo} 不是 git 仓库，请先 git init")
    _git(repo, "tag", label)
    return label


def add_worktree(repo, card_id: str, dest) -> str:
    """为卡片建独立副本（分支 card/<id>），与主工作区互不影响。"""
    if not is_git_repo(repo):
        raise NotGitRepo(f"{repo} 不是 git 仓库，请先 git init")
    branch = f"card/{card_id}"
    _git(repo, "worktree", "add", "-b", branch, str(dest), "HEAD")
    return branch


def link_dependencies(project, worktree) -> None:
    """把主项目已装好的依赖接到副本里（用链接，不复制，不重新安装）。没有依赖就跳过。"""
    src = Path(project) / "node_modules"
    dst = Path(worktree) / "node_modules"
    if src.exists() and not dst.exists():
        os.symlink(src, dst)


# 只带这两个本地配置：它们装着运行所需的设置，但被 git 忽略，副本里不会自动有
LOCAL_SETTINGS = (".env", ".env.local")


def copy_local_settings(project, worktree) -> None:
    for name in LOCAL_SETTINGS:
        src = Path(project) / name
        if src.is_file() and not (Path(worktree) / name).exists():
            shutil.copy2(src, Path(worktree) / name)


def revert_paths(worktree, paths) -> None:
    """把副本里指定的改动撤销：原有文件恢复成提交时的样子，新建的文件或目录直接删除。
    用于越界改动——开发员在自动模式下不能执行命令，删不掉文件，只能由程序来做。"""
    root = Path(worktree)
    for raw in paths:
        rel = raw.rstrip("/")
        if not rel:
            continue
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", rel], cwd=root,
                                 capture_output=True).returncode == 0
        target = root / rel
        if tracked:
            subprocess.run(["git", "checkout", "--", rel], cwd=root, capture_output=True)
        elif target.is_dir() and not target.is_symlink():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists() or target.is_symlink():
            target.unlink()
