"""按功能名或施工单归档：卡片做完后，把记录整理进项目文档。

- 任务卡片（文件 需求卡.md）→ 卡片原文.md，方案 → 方案.md，另生成一份 验收记录.md（大白话总结）
- 执行记录、测试结果、返工记录、复查结果等过程文件 → 过程记录/
- 卡片引用了施工单时，存到施工单旁边的「卡片记录/卡号/」；没有施工单才按功能名放 docs/<功能名>/
- 只新增，不覆盖别人的文件：同名文件已存在时，新文件名后面加上卡片编号
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from collab.flow import read_flow, record, rework_words
from collab.ledger import ledger_add
from collab.planner import existing_orders
from collab.state import Card

_MAIN_FILES = {"需求卡.md": "卡片原文.md", "方案.md": "方案.md"}
# 过程摘要随归档进项目文档；交接全文（流转/）和实时输出又多又大，只留在本机 .collab/，不进项目
_PROCESS_FILES = ("执行记录.md", "测试结果.md", "返工记录.md", "复查结果.md",
                  "页面检查结果.md", "汇报.md", "设计稿.html", "以后再修.md")
_MANIFEST = "归档文件.json"


def _safe_feature(feature: str) -> str:
    name = (feature or "").strip()
    if not name or "/" in name or "\\" in name or name in (".", "..") or name.startswith("."):
        raise ValueError(f"功能名不合法：{feature!r}（不能为空，不能包含路径符号）")
    return name


def _read_manifest(cdir: Path) -> set:
    try:
        data = json.loads((cdir / _MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    if not isinstance(data, list):
        return set()
    return {str(item).replace("\\", "/") for item in data if isinstance(item, str)}


def _pick(folder: Path, filename: str, root: Path, card_id: str, ours: set, force: bool) -> Path:
    """挑一个不会覆盖别人文件的名字；本次归档自己写过的文件直接覆盖。"""
    plain = folder / filename
    if force:
        return plain
    if plain.relative_to(root).as_posix() in ours:
        return plain
    if not plain.exists():
        return plain
    stem, suffix = Path(filename).stem, Path(filename).suffix
    number = 1
    while True:
        name = f"{stem}（{card_id}）{suffix}" if number == 1 else f"{stem}（{card_id}-{number}）{suffix}"
        candidate = folder / name
        if candidate.exists() and candidate.relative_to(root).as_posix() not in ours:
            number += 1
            continue
        return candidate


_CONCLUSION = re.compile(r"结论：\s*(不通过|通过|没过|过|环境错误|超时)")


def _last_conclusion(path: Path):
    """取文件里最后一个“结论：xx”的 xx。原始记录可能是工具的 JSON 包装，这里只认结论本身。"""
    if not path.exists():
        return None
    hits = _CONCLUSION.findall(path.read_text(encoding="utf-8"))
    return hits[-1] if hits else None


def _review_text(cdir: Path):
    """独立复查优先看流转里最后一条“复查结论”；老卡没有这条记录才读复查结果文件。"""
    for event in reversed(read_flow(cdir)):
        if event.get("kind") != "复查结论":
            continue
        result = str(event.get("result") or "").strip()
        if result == "退回":
            return "不通过"
        return result or None
    return _last_conclusion(cdir / "复查结果.md")


def _acceptance_text(feature: str, card: Card, cdir: Path, final_state: str) -> str:
    test = _last_conclusion(cdir / "测试结果.md")
    review = _review_text(cdir)
    page = _last_conclusion(cdir / "页面检查结果.md")
    test_text = test or "没有测试记录"
    return "\n".join([
        f"# 验收记录：{feature}（{card.card_id}）",
        "",
        f"- 存入时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"- 最终状态：{final_state}，{rework_words(card.rework, cdir)}",
        f"- 测试：{test_text}（最后一轮）",
        f"- 独立复查：{review or '未复查'}",
        f"- 页面检查：{page or '未做自动页面检查'}",
        "- 过程记录：见本目录下的 过程记录/（谁做了什么、每轮测试输出、返工原因）",
        "",
    ])


def archive_card(project_dir, card_id: str, feature: str, root=None, final_state=None) -> Path:
    project = Path(project_dir)
    where = Path(root) if root is not None else project
    name = _safe_feature(feature)
    cdir = project / ".collab" / "cards" / card_id
    if not (cdir / "状态.json").exists():
        raise FileNotFoundError(f"没有卡片 {card_id}")
    card = Card.load(cdir / "状态.json")

    requirement = ""
    req_path = cdir / "需求卡.md"
    if req_path.is_file():
        requirement = req_path.read_text(encoding="utf-8")
    orders = existing_orders(project, requirement)
    if orders:
        target = where / Path(orders[0]).parent / "卡片记录" / card_id
        dedicated = True
    else:
        target = where / "docs" / name
        dedicated = False
    process = target / "过程记录"
    process.mkdir(parents=True, exist_ok=True)

    ours = _read_manifest(cdir)
    written = []

    def put(src: Path, folder: Path, dst_name: str) -> None:
        if not src.exists():
            return
        out = _pick(folder, dst_name, where, card_id, ours, dedicated)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, out)
        written.append(out.relative_to(where).as_posix())

    for src_name, dst_name in _MAIN_FILES.items():
        put(cdir / src_name, target, dst_name)
    for src_name in _PROCESS_FILES:
        put(cdir / src_name, process, src_name)

    state = str(final_state or card.state)
    out = _pick(target, "验收记录.md", where, card_id, ours, dedicated)
    out.write_text(_acceptance_text(name, card, cdir, state), encoding="utf-8")
    written.append(out.relative_to(where).as_posix())

    (cdir / _MANIFEST).write_text(
        json.dumps(sorted(set(written)), ensure_ascii=False, indent=2), encoding="utf-8")

    if where == project:
        rel = target.relative_to(project).as_posix() + "/"
        record(cdir, "归档", src="调度员", summary=f"记录已存入项目文档 {rel}")
        ledger_add(project, f"{card_id}：记录已存入项目文档 {rel}")
    return target
