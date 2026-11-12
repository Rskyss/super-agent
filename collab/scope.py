"""范围控制：任务卡片里写明允许改哪些文件，施工后由程序核对，超出的一律打回。"""
from __future__ import annotations

import fnmatch

SCOPE_HEADING = "## 允许改动的范围"
# 系统自己的东西不算越界：协作区、依赖链接
_ALWAYS_IGNORED = (".collab/", ".collab", "node_modules", "node_modules/")


_CACHE_DIRS = ("__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")


TOOL_SETTINGS = (".cursor/cli.json",)  # 派活前给工具写的放行设置（只在施工副本里，不算谁的改动、不进项目）


def is_cache(path: str) -> bool:
    """运行时自动生成的东西，不是谁改的代码：测试缓存、派活前写给工具的放行设置。"""
    if path.replace("\\", "/").strip("/") in TOOL_SETTINGS:
        return True
    parts = [p for p in path.split("/") if p]
    return path.endswith(".pyc") or any(part in _CACHE_DIRS for part in parts)


def _is_secret(path: str) -> bool:
    """任何目录下的 .env、.env.*、.git/ 都算越界，* 也放行不了。"""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    for part in parts:
        if part == ".env" or part.startswith(".env."):
            return True
        if part == ".git":
            return True
    return False


def read_allowed(requirement_text: str):
    """读出任务卡片里“允许改动的范围”下面的每一条。没有这一节返回 None。"""
    if SCOPE_HEADING not in requirement_text:
        return None
    body = requirement_text.split(SCOPE_HEADING, 1)[1]
    allowed = []
    for line in body.splitlines():
        if line.startswith("## "):
            break
        line = line.strip()
        if line.startswith("- "):
            allowed.append(line[2:].strip())
    return allowed


def _matches(path: str, pattern: str) -> bool:
    """目录写法：`src/` 与 `src` 都表示该目录下全部内容；通配符走 fnmatch。"""
    path = path.replace("\\", "/")
    pattern = (pattern or "").replace("\\", "/").strip()
    if not pattern:
        return False
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatch(path, pattern)
    base = pattern.rstrip("/")
    if path == base:
        return True
    return path.startswith(base + "/")


def out_of_scope(changed, allowed) -> list:
    """返回越界的文件。先比完全相同的路径，再按通配符/目录匹配。"""
    exact = {str(p).replace("\\", "/") for p in (allowed or []) if str(p).strip()}
    bad = []
    for path in changed:
        path = str(path).replace("\\", "/")
        if path in _ALWAYS_IGNORED or path.startswith(".collab/") or "node_modules" in path.split("/"):
            continue
        if is_cache(path):  # 运行测试时自动生成的缓存，不是谁改的代码
            continue
        if _is_secret(path):
            bad.append(path)
            continue
        if path in exact:
            continue
        if any(_matches(path, pattern) for pattern in allowed):
            continue
        bad.append(path)
    return bad
