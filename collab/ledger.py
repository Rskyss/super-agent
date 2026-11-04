"""项目总台账（docs/开发进度跟踪.md）：只追加，不改旧记录。

关卡发生时（方案确认、运行结束）自动登记一行，让所有 AI 和用户都能查到完整过程。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

LEDGER_NAME = "开发进度跟踪.md"
_HEADER = "# 开发进度跟踪\n\n## 协作包记录（只追加）\n"


def ledger_add(project_dir, text: str) -> None:
    path = Path(project_dir) / "docs" / LEDGER_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(_HEADER, encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"- {datetime.now():%Y-%m-%d %H:%M} {text}\n")
