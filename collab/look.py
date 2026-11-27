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


_SEP = "\\s，。；、,.;（）()！!"
# 开头明说不用。“不涉及 / 不适用”后面写什么都算；“不需要 / 无”这几个词后面要么结束，要么接走查、页面这类字，
# 免得把“无痕窗口打开…”“不用登录，打开大屏…”当成不用看
_SAYS_NO = re.compile(r"(?:不涉及|不适用|(?:不需要|不用|无需|不必|没有|无)(?=$|[" + _SEP + r"]|手动|人工|真人|页面|界面|走查|看|对照|原型))"
                      r"(?:(?!但|不过|然而)[^" + _SEP + r"])*")
_NO_WORD = re.compile(r"(?:不需要|不用|无需|不必)(?:再|手动|人工|真人)?(?:" + "|".join(LOOK_WORDS) + ")")


def needs_look_line(line: str, default: bool = False) -> bool:
    """施工单“走查：”后面那句话要不要人看页面。
    开头明说不用的，后面又写了要看（“不涉及（…必须停下，打开页面看一遍）”）仍算要看。
    default：没用上那几个词、但写了要做的事，算不算要看。"""
    text = str(line or "").strip()
    said_no = _SAYS_NO.match(text)
    rest = _NO_WORD.sub("", text[said_no.end():] if said_no else text)
    if any(word in rest for word in LOOK_WORDS):
        return True
    # “不需要对照原型，但放进项目前在浏览器实测”：转折后面还有要做的事（复查第 2 轮）
    return bool(default and text and (not said_no or re.search("但|不过|然而", rest)))


def asks_look(accept_lines, has_walk_command: bool) -> str:
    """没用上那几个词、但“走查：”后面写了要做的事的，也算要看页面；返回那句话，不算就返回空。
    写了走查命令的由程序自动走查，不算；“走查步骤”那种表头不算。
    来历：11-25 train 三张施工单写“按走查步骤表检查 / 实测”，一张都没认出来，会直接进待验收。"""
    if has_walk_command:
        return ""
    for line in accept_lines:
        m = re.match(r"走查\s*[：:]\s*(.+)$", str(line or "").strip())
        if m:
            text = m.group(1).strip()
            return text if needs_look_line(text, default=True) else ""
    return ""


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
