"""一键安装：把协作包复制进任意项目的 collab/，并初始化协作区 .collab/。

- 已安装过的，必须明确要求“更新”才会替换包文件
- 项目里已有一个无关的 collab/ 文件夹时拒绝，绝不覆盖别人的东西
- 更新只替换包本身，项目的协作记录（.collab/）一概不动
"""
from __future__ import annotations

import shutil
from pathlib import Path

from collab.workspace import init_collab

PACKAGE_DIR = Path(__file__).resolve().parent
_MARKER = "规则.md"  # 用来认出“这是协作包”的文件


class InstallError(Exception):
    pass


def install_into(project_dir, update: bool = False) -> Path:
    project = Path(project_dir).resolve()
    if not project.is_dir():
        raise InstallError(f"项目目录不存在：{project}")
    target = project / "collab"
    if target.resolve() == PACKAGE_DIR:
        raise InstallError("不能把协作包装进它自己所在的项目")
    if target.exists():
        if not (target / _MARKER).exists():
            raise InstallError(f"{target} 已存在，但不是协作包，为避免覆盖已停止")
        if not update:
            raise InstallError(f"{target} 已经装过协作包；如需更新请加 --update")
        shutil.rmtree(target)
    shutil.copytree(PACKAGE_DIR, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    init_collab(project)
    return target
