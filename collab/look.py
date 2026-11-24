"""第三批 ③：施工单要求“放进项目前看页面 / 对照原型图”的卡，复查通过后先停在“等看页面”。

外部项目试跑 ③：train 施工单 02～05 都写了“放进主项目前必须停下对照原型图走查”，开发助手不认这句话，
五张卡都没看页面就放进了本地项目。这里把那一行认出来：要看页面的卡，人点了“已看过”才放行。
“已看过”只认这一轮做完（最后一条“结束”）之后点的：又改了一轮就要重新看。
"""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

LOOK_WORDS = ("原型", "设计图", "对照", "看页面", "看一遍", "看图")
_IMAGE = re.compile(r"`([^`\n]+\.(?:png|jpe?g|webp))`|((?:[\w\-.一-龥]+/)*[\w\-一-龥][\w\-.一-龥]*\.(?:png|jpe?g|webp))",
                    re.I)


def needs_look_line(line: str) -> bool:
    """施工单“验收”一节的“走查”那一行有没有要求人看页面。只写“不涉及”的不算。"""
    text = str(line or "")
    return any(word in text for word in LOOK_WORDS)


def look_images(line: str, project, order_rel: str) -> list:
    """从走查那一行认出图片文件名，换成项目里的相对路径（施工单里多半写相对施工单所在文件夹的路径）。"""
    root = Path(project)
    base = PurePosixPath(order_rel).parent
    out = []

    def add(path):
        if path not in out:
            out.append(path)

    for m in _IMAGE.finditer(str(line or "")):
        name = (m.group(1) or m.group(2) or "").strip()
        if not name or ".." in PurePosixPath(name).parts or name.startswith("/"):
            continue
        near = (base / name).as_posix()
        if "*" in name:   # 施工单常写成“导师端原型对照/02*.png”：在施工单旁边按通配展开
            hits = sorted(p for p in (root / base).glob(name) if p.is_file()) if (root / base).is_dir() else []
            for hit in hits:
                add(hit.relative_to(root).as_posix())
            if hits:
                continue
        if (root / near).is_file():
            add(near)
            continue
        if (root / name).is_file():
            add(name)
            continue
        # 只写了文件名、没写文件夹（“…/04.png 与 05.png”）：在施工单所在文件夹里按文件名找，只认唯一一张
        found = sorted(p for p in (root / base).rglob(PurePosixPath(name).name) if p.is_file()) \
            if (root / base).is_dir() and "*" not in name else []
        if len(found) == 1:
            add(found[0].relative_to(root).as_posix())
            continue
        add(name if name.startswith("docs/") else near)
    return out


def _start(cdir) -> dict:
    try:
        data = json.loads((Path(cdir) / "开工.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def look_info(cdir) -> dict:
    """开工记录里存的看页面要求：{required, images, line}。
    升级前开工的卡开工记录里没有这几项：回头读任务卡提到的施工单再认一次（第三批复查第 2 轮 2）。"""
    data = _start(cdir)
    if data and "look_required" not in data:
        try:
            from collab.planner import _order_look, existing_orders
            project = Path(cdir).resolve().parents[2]
            req = (Path(cdir) / "需求卡.md").read_text(encoding="utf-8")
            for rel in existing_orders(project, req)[:1]:
                data = dict(data, **_order_look(project, rel))
        except (OSError, ValueError, IndexError):
            pass
    images = data.get("look_images")
    return {
        "required": bool(data.get("look_required")),
        "images": [str(x) for x in images] if isinstance(images, list) else [],
        "line": str(data.get("look_line") or ""),
    }


def last_look(events) -> dict:
    """最后一次“结束”之后点的“已看过”记录；没有返回空。"""
    finished = max((e.get("seq", 0) for e in events if e.get("kind") == "结束"), default=0)
    seen = [e for e in events if e.get("kind") == "看页面" and e.get("seq", 0) > finished]
    return seen[-1] if seen else {}


def look_pending(cdir, events) -> bool:
    return look_info(cdir)["required"] and not last_look(events)
