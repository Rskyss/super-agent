"""接入与迁移。

从 v0.2 起，协作包不再复制进项目，只在超级助手里放一份：
- 接入（install / init）：只在项目里建 .collab/，并加进不提交名单；
- 迁移（migrate）：旧装法的项目（项目里有一份协作包副本 collab/）——
  项目档案复制成 docs/项目说明.md、加进不提交名单、更新协作区说明；
  只有明确要求才删项目里的旧协作包；同名但不是协作包的文件夹绝不碰。
"""
from __future__ import annotations

import shutil
from pathlib import Path

from collab.workspace import (LEGACY_PROFILE, PROFILE_FILE, RECORDS_README, ensure_ignored,
                              init_collab)

PACKAGE_DIR = Path(__file__).resolve().parent
_MARKERS = ("规则.md", "cli.py")  # 同时有这两个文件，才认作“协作包”
_OLD_README_HINT = "规则书在项目里的 collab/规则.md"


class InstallError(Exception):
    pass


def install_into(project_dir, update: bool = False) -> Path:
    """旧命令保留：现在等于“接入”，不复制协作包。update 参数只为兼容旧用法，效果相同。"""
    project = Path(project_dir).resolve()
    if not project.is_dir():
        raise InstallError(f"项目目录不存在：{project}")
    return init_collab(project)


def find_old_package(project_dir):
    """项目里那份旧的协作包副本；没有（或那个文件夹不是协作包）返回 None。"""
    folder = Path(project_dir) / "collab"
    if folder.resolve() == PACKAGE_DIR:  # 超级助手自己的源目录，不是“旧副本”
        return None
    if folder.is_dir() and all((folder / m).exists() for m in _MARKERS):
        return folder
    return None


def migrate(project_dir, remove_old_package: bool = False) -> dict:
    project = Path(project_dir)
    report = {"old_package": False, "removed": False, "profile_moved": False, "ignored": False}

    old = find_old_package(project)
    report["old_package"] = old is not None

    records = project / ".collab"
    if records.is_dir():
        report["ignored"] = ensure_ignored(project)
        readme = records / "README.md"
        if readme.exists() and _OLD_README_HINT in readme.read_text(encoding="utf-8"):
            readme.write_text(RECORDS_README, encoding="utf-8")  # 旧说明指向项目里的副本，换成真正的位置

    legacy, new = project / LEGACY_PROFILE, project / PROFILE_FILE
    if legacy.exists() and not new.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, new)  # 复制，不删旧的
        report["profile_moved"] = True

    if old is not None and remove_old_package:
        shutil.rmtree(old)
        report["removed"] = True
    return report
