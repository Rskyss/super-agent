"""调度员的开场交代：读 desktop/opening.json，把路径填进去。终端视图和聊天视图用同一份文字。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OPENING_FILE = Path(__file__).resolve().parent / "opening.json"


def has_profile(project) -> bool:
    project = Path(project)
    return (project / "docs" / "项目说明.md").exists() or (project / ".collab" / "项目档案.md").exists()


def opening(project, root=ROOT) -> dict:
    """返回 {marker, background, first, every, first_time}；文字里的路径已换成实际位置。"""
    project = Path(project)
    data = json.loads(OPENING_FILE.read_text(encoding="utf-8"))
    profile = "docs/项目说明.md" if (project / "docs" / "项目说明.md").exists() or not (
        project / ".collab" / "项目档案.md").exists() else ".collab/项目档案.md"
    values = {"manual": str(Path(root) / "collab" / "README.md"), "cli": str(Path(root) / "collab"),
              "project": str(project), "profile": profile}
    out = {key: data[key].format(**values) for key in ("background", "first", "every")}
    out["marker"] = data["marker"]
    out["first_time"] = not has_profile(project)
    return out
