"""按功能名归档：卡片做完后，把记录整理进项目的 docs/<功能名>/，沿用项目原有的文档习惯。

- 需求卡 → 需求共识.md，方案 → 方案.md，另生成一份 验收记录.md（大白话总结）
- 执行记录、测试结果、返工记录、复查结果等过程文件 → 过程记录/
- 只新增，不覆盖：同名文件已存在时，新文件名后面加上卡片编号
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

from collab.ledger import ledger_add
from collab.state import Card

_MAIN_FILES = {"需求卡.md": "需求共识.md", "方案.md": "方案.md"}
_PROCESS_FILES = ("执行记录.md", "测试结果.md", "返工记录.md", "复查结果.md",
                  "页面检查结果.md", "汇报.md", "设计稿.html")


def _safe_feature(feature: str) -> str:
    name = (feature or "").strip()
    if not name or "/" in name or "\\" in name or name in (".", "..") or name.startswith("."):
        raise ValueError(f"功能名不合法：{feature!r}（不能为空，不能包含路径符号）")
    return name


def _free_name(folder: Path, filename: str, card_id: str) -> Path:
    """目标已存在就换一个带卡片编号的名字，绝不覆盖原有文件。"""
    target = folder / filename
    if not target.exists():
        return target
    stem, suffix = Path(filename).stem, Path(filename).suffix
    return folder / f"{stem}（{card_id}）{suffix}"


_CONCLUSION = re.compile(r"结论：\s*(不通过|通过|没过|过|环境错误|超时)")


def _last_conclusion(path: Path):
    """取文件里最后一个“结论：xx”的 xx。原始记录可能是工具的 JSON 包装，这里只认结论本身。"""
    if not path.exists():
        return None
    hits = _CONCLUSION.findall(path.read_text(encoding="utf-8"))
    return hits[-1] if hits else None


def _acceptance_text(feature: str, card: Card, cdir: Path) -> str:
    test = _last_conclusion(cdir / "测试结果.md")
    review = _last_conclusion(cdir / "复查结果.md")
    page = _last_conclusion(cdir / "页面检查结果.md")
    test_text = test or "没有测试记录"
    return "\n".join([
        f"# 验收记录：{feature}（{card.card_id}）",
        "",
        f"- 归档时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"- 最终状态：{card.state}，返工 {card.rework} 次",
        f"- 测试：{test_text}（最后一轮）",
        f"- 独立复查：{review or '未复查'}",
        f"- 页面检查：{page or '未做自动页面检查'}",
        "- 过程记录：见本目录下的 过程记录/（谁做了什么、每轮测试输出、返工原因）",
        "",
    ])


def archive_card(project_dir, card_id: str, feature: str) -> Path:
    project = Path(project_dir)
    name = _safe_feature(feature)
    cdir = project / ".collab" / "cards" / card_id
    if not (cdir / "状态.json").exists():
        raise FileNotFoundError(f"没有卡片 {card_id}")
    card = Card.load(cdir / "状态.json")

    target = project / "docs" / name
    process = target / "过程记录"
    process.mkdir(parents=True, exist_ok=True)

    for src_name, dst_name in _MAIN_FILES.items():
        src = cdir / src_name
        if src.exists():
            shutil.copy2(src, _free_name(target, dst_name, card_id))
    for src_name in _PROCESS_FILES:
        src = cdir / src_name
        if src.exists():
            shutil.copy2(src, _free_name(process, src_name, card_id))
    _free_name(target, "验收记录.md", card_id).write_text(
        _acceptance_text(name, card, cdir), encoding="utf-8")

    ledger_add(project, f"{card_id}：记录已归档到 docs/{name}/")
    return target
