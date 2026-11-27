"""写方案：只读调用 Codex，把结果落到卡片文件。"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from typing import Optional, Tuple

from collab.card_attachments import attachment_prompt
from collab.config import plan_models  # noqa: F401 — 写方案、备交接资料共用这一份（别的地方也从这里取）
from collab.flow import record as flow_record
from collab.rulebook import rules_for as _rules, say as _say
from collab.state import Card
from collab.token_usage import usage_from_text
from collab.workspace import PLACEHOLDER, PLAN_TEMPLATE, PROFILE_TEMPLATE, has_blanks, profile_path, render_board

_PLAN_TIMEOUT = 15 * 60
_VIEW_KEYS = ("view_how", "view_command", "view_url")
_PLAN_SCHEMA_KEYS = (
    "summary", "steps", "result", "not_do", "questions",
    "allowed", "test_command", "profile",
) + _VIEW_KEYS
_CORE_KEYS = tuple(k for k in _PLAN_SCHEMA_KEYS if k not in _VIEW_KEYS)
_TEXT_KEYS = ("summary", "result", "not_do", "test_command", "profile")
_LIST_KEYS = ("steps", "questions", "allowed")


def _plan_schema() -> dict:
    props = {}
    for key in _TEXT_KEYS + _VIEW_KEYS:
        props[key] = {"type": "string"}
    for key in _LIST_KEYS:
        props[key] = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": props,
        "required": list(_PLAN_SCHEMA_KEYS),
        "additionalProperties": False,
    }


def _norm_paths(items):
    return [str(p).strip().replace("\\", "/") for p in (items or []) if str(p).strip()]


def _bad_scope_reason(path: str) -> Optional[str]:
    p = path.strip().replace("\\", "/")
    if not p:
        return "路径是空的"
    if p in ("*", "**"):
        return "不能匹配全部文件"
    if os.path.isabs(p):
        return "不能写绝对路径"
    parts = [x for x in p.split("/") if x]
    if ".." in parts:
        return "不能写带 .. 的路径"
    first = parts[0] if parts else ""
    # 第一段带方括号：只有“动态路由”写法（[id]、[...slug]、[[...slug]]）、整条路径没有 * ?、结尾不带“(说明)”时
    # 才按文件名认（卡 C′）；[.]env 这类、和通配符混用的（[id]/*.tsx 会被范围检查当通配符）照旧挡掉（复查第 6 轮）
    from collab.scope import _NOTE
    route_only = re.fullmatch(r"(?:[^\[\]*?]|\[\[\.\.\.[\w-]+\]\]|\[(?:\.\.\.)?[\w-]+\])*", first) is not None
    literal_ok = route_only and not any(ch in p for ch in "*?") and not _NOTE.search(p)
    if any(ch in first for ch in "*?") or ("[" in first and not literal_ok):
        return "第一段不能用通配符"
    if any(ch in p for ch in "*?[") and (".env" in p or ".git" in p):
        return "不能用通配符匹配敏感文件"
    for part in parts:
        if part == ".collab" or part.startswith(".collab"):
            return "不能指向 .collab"
        if part == ".env" or part.startswith(".env.") or part.startswith(".env"):
            return f"不能指向 {part}"
        if part.startswith(".git"):
            return f"不能指向 {part}"
    return None

def _need_profile(project: Path) -> bool:
    """只有文件不存在，或内容与模板完全相同，才让 Codex 写项目说明。"""
    path = profile_path(project)
    if not path.is_file():
        return True
    try:
        return path.read_text(encoding="utf-8") == PROFILE_TEMPLATE
    except OSError:
        return True


def _validate_answer(data: dict, need_profile: bool) -> Tuple[bool, str]:
    if not isinstance(data, dict):
        return False, "回答不是一份结构化的内容"
    for k in _CORE_KEYS:
        if k not in data:
            return False, f"缺少字段：{k}"
    for k in _TEXT_KEYS:
        if not isinstance(data.get(k), str):
            return False, f"字段类型不对：{k}"
    for k in _LIST_KEYS:
        value = data.get(k)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return False, f"字段类型不对：{k}"
    for k in _VIEW_KEYS:
        if k in data and data[k] is not None and not isinstance(data[k], str):
            return False, f"字段类型不对：{k}"

    summary = data["summary"].strip()
    if not summary:
        return False, "摘要不能为空"
    test_cmd = data["test_command"].strip()
    if not test_cmd:
        return False, "测试命令不能为空"
    try:
        parts = shlex.split(test_cmd)
    except ValueError:
        return False, "测试命令写得不对，解析不了"
    if not parts:
        return False, "测试命令不能为空"

    allowed = _norm_paths(data.get("allowed"))
    if not allowed:
        return False, "允许改动的文件列表不能为空"
    for one in allowed:
        why = _bad_scope_reason(one)
        if why:
            return False, f"允许改动的范围不合法：{one}（{why}）"

    if need_profile:
        prof = data["profile"]
        if not prof.strip():
            return False, "这个项目还没有项目说明，需要先生成一份草稿"
        if "（待填）" in prof:
            return False, "项目说明草稿里还有未填写的地方"
    return True, ""


def is_local_url(url: str) -> bool:
    """预览只开本机的 http 地址。窗口那边用同一条规则，两边不一致会启动了却打不开。"""
    try:
        parsed = urlparse(str(url or "").strip())
        return parsed.scheme == "http" and (parsed.hostname or "").lower() in ("127.0.0.1", "localhost")
    except ValueError:
        return False


def _clean_view_fields(plan: dict) -> Tuple[str, str, str]:
    """命令解析不了或地址不是本机时，命令和地址都清空；怎么看保留。"""
    how = str(plan.get("view_how") or "").strip()
    cmd = str(plan.get("view_command") or "").strip()
    url = str(plan.get("view_url") or "").strip()
    drop = False
    if cmd:
        try:
            if not shlex.split(cmd):
                drop = True
        except ValueError:
            drop = True
    if url and not is_local_url(url):
        drop = True
    if drop:
        cmd, url = "", ""
    return how, cmd, url


def _render_plan_md(plan: dict) -> str:
    steps = plan.get("steps") or []
    if not isinstance(steps, (list, tuple)):
        steps = [str(steps)]
    steps_txt = "\n".join(f"{i + 1}. {str(s).strip()}" for i, s in enumerate(steps) if str(s).strip()) or "1. （无）"
    questions = plan.get("questions") or []
    if not isinstance(questions, (list, tuple)):
        questions = [str(questions)]
    q_lines = [str(q).strip() for q in questions if str(q).strip()]
    q_txt = "\n".join(f"- {q}" for q in q_lines) if q_lines else "无"
    how, cmd, url = _clean_view_fields(plan)
    view_lines = []
    if how:
        view_lines.append(how)
    if cmd:
        view_lines.append(f"启动：{cmd}")
    if url:
        view_lines.append(f"地址：{url}")
    view_txt = "\n".join(view_lines) if view_lines else "方案里没写"
    return (
        "# 拆解方案\n\n"
        f"## 要做什么（一句话）\n{plan['summary'].strip()}\n\n"
        f"## 拆成几步，每步做什么、产出什么\n{steps_txt}\n\n"
        f"## 你会看到什么结果\n{str(plan.get('result') or '').strip()}\n\n"
        f"## 明确不做什么\n{str(plan.get('not_do') or '').strip()}\n\n"
        f"## 需要你确认的问题\n{q_txt}\n\n"
        f"## 做完后怎么看结果\n{view_txt}\n"
    )


def _replace_allowed_section(requirement_text: str, allowed_paths: list) -> str:
    from collab.scope import SCOPE_HEADING

    new_scope = [SCOPE_HEADING, ""] + [f"- {p}" for p in allowed_paths]
    lines = requirement_text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() == SCOPE_HEADING:
            end = idx + 1
            while end < len(lines) and not lines[end].startswith("## "):
                end += 1
            return "\n".join(lines[:idx] + new_scope + lines[end:]) + "\n"
    return requirement_text.rstrip() + "\n\n" + "\n".join(new_scope) + "\n"


_ORDER_PATH = re.compile(r"[^\n，。；、：！？\"'“”‘’（）()《》【】]+?\.md")


def _order_candidates(chunk: str):
    """从一段含 .md 的文字里，按从长到短试可能的路径（兼容空格和项目内绝对路径）。"""
    starts = {0}
    for index, char in enumerate(chunk):
        if char.isspace() or char in "，。；、：（）()《》“”\"'`":   # 反引号括起来的路径也认（C2 复查第 3 轮）
            starts.add(index + 1)
        if char == "/":
            starts.add(index + 1)
    for index in sorted(starts):
        candidate = chunk[index:].strip().strip("\"'“”‘’`")
        if "施工单" in candidate and candidate.endswith(".md"):
            yield candidate


def _order_relative(root: Path, candidate: str):
    path = Path(candidate)
    if path.is_absolute():
        try:
            path = path.resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            return None
    else:
        if ".." in path.parts:
            return None
        path = Path(*[part for part in path.parts if part not in ("", ".")])
    rel = path.as_posix()
    if not rel or rel.startswith("../"):
        return None
    return rel if (root / rel).is_file() else None


def existing_orders(project, requirement_text: str) -> list:
    """任务卡片里提到的、项目里真实存在的施工单文件（相对项目根）。"""
    root = Path(project) if project else None
    if root is None:
        return []
    found = []
    for chunk in _ORDER_PATH.findall(requirement_text or ""):
        for candidate in _order_candidates(chunk):
            rel = _order_relative(root, candidate)
            if rel and rel not in found:
                found.append(rel)
                break
    return found


def _order_sections(text: str) -> list:
    """把施工单按二级标题切成 [(标题, 正文)]，正文不含标题行。"""
    sections = []
    title, body = None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if title is not None:
                sections.append((title, body))
            title, body = line[3:].strip(), []
        elif title is not None:
            body.append(line)
    if title is not None:
        sections.append((title, body))
    return sections


def _section_body(sections: list, keyword: str):
    """按标题里的关键词找；找不到就是没有（不按第几节去猜：C1、C1.5 施工单第四节是“验收”，Codex 接手复查）。"""
    for title, body in sections:
        if keyword in title:
            return body
    return None


def _first_paragraph(lines):
    """取第一段：跳过开头的空行，遇到空行或标题就停。"""
    paragraph = []
    for line in lines or []:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    return "\n".join(paragraph)


def _walk_command_from_sections(sections: list) -> str:
    """只在标题含“验收”的那一节里找“- 走查命令：”，取这一行第一对反引号里的命令。"""
    for line in _section_body(sections, "验收") or []:
        stripped = line.strip()
        if stripped.startswith("- 走查命令："):
            quoted = re.search(r"`([^`\n]+)`", stripped)
            return quoted.group(1).strip() if quoted else ""
    return ""


def _order_walk_command(project, rel: str) -> str:
    try:
        text = (Path(project) / rel).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""
    return _walk_command_from_sections(_order_sections(text))


def _order_look(project, rel: str) -> dict:
    """施工单“验收”一节的“走查”那一行要不要人看页面（第三批 ③）；照施工单生成和 AI 写方案两条路都用这一份。"""
    from collab.look import asks_look, look_images, needs_look_line
    try:
        text = (Path(project) / rel).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    lines = [re.sub(r"^-\s*\*\*(走查[^*]*?)\*\*", r"- \1", ln.strip())
             for ln in (_section_body(_order_sections(text), "验收") or [])]   # 只去掉“走查”两个字上的加粗，路径里的 ** 不动（确认复查）
    accept = [ln[2:].strip() for ln in lines if ln.startswith("- 走查") and not ln.startswith("- 走查命令")]
    walk_text = accept[0].split("：", 1)[-1].strip() if accept else ""
    if not needs_look_line(walk_text):
        walk_text = asks_look(accept, bool(_order_walk_command(project, rel)))
    if not walk_text:
        return {}
    return {"look_required": True, "look_images": look_images(walk_text, project, rel), "look_line": walk_text}


_ACCEPT_COMMAND_PREFIX = "- 验收命令："


def _order_test_command(sections: list, text: str) -> str:
    """施工单的验收命令：有“验收”一节只认这一节里以“- 验收命令：”开头那一行的反引号命令；
    老格式（没有“验收”一节）照旧找全文第一处。检查和照施工单生成共用这一份（卡 A）。"""
    body = _section_body(sections, "验收")
    if body is not None:
        for line in body:
            stripped = line.strip()
            if stripped.startswith(_ACCEPT_COMMAND_PREFIX):
                quoted = re.search(r"`([^`\n]*)`", stripped)
                return quoted.group(1).strip() if quoted else ""
        return ""
    command = re.search(r"验收命令：.*?`([^`\n]+)`", text)
    return command.group(1).strip() if command else ""


def _order_allowed(sections: list) -> list:
    allowed = []
    for line in _section_body(sections, "允许改") or []:
        stripped = line.strip()
        if "**不许改**" in stripped:
            break
        if not stripped.startswith("- "):
            continue
        quoted = re.search(r"`([^`]+)`", stripped)
        if quoted:
            path = quoted.group(1).strip()
            if path and path not in allowed:
                allowed.append(path)
    return allowed


def _order_plan_data(project, rel: str):
    """把施工单按固定格式摘成方案内容（缺项也照摘，交给 order_gaps 判断）；读不了返回 (None, 原因)。"""
    try:
        text = (Path(project) / rel).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return None, str(exc)
    sections = _order_sections(text)
    summary = _first_paragraph(_section_body(sections, "要做什么")) or ""
    allowed = _order_allowed(sections)
    test_command = _order_test_command(sections, text)
    walk_command = _walk_command_from_sections(sections)
    steps = []
    for line in _section_body(sections, "做法") or []:
        stripped = line.strip()
        if stripped.startswith("### ") and stripped[3:].strip():
            steps.append(stripped[3:].strip())
    not_do = _first_paragraph(_section_body(sections, "不做什么")) or "照施工单"
    # 外部项目观察 ②：“你会看到什么结果”原来照抄“要做什么”，“做完后怎么看结果”写“方案里没写”。
    # 施工单有“验收”一节就把它的验收命令、走查两行带进来；没有才退回老样子
    accept = [re.sub(r"^\*\*(走查[^*]*?)\*\*", r"\1", ln.strip()[2:].strip()) for ln in (_section_body(sections, "验收") or [])
              if ln.strip().startswith("- ") and ("验收命令" in ln or "走查" in ln)]   # “**走查：**”加粗也取正文（收尾确认复查 A2）
    walk_line = next((a for a in accept if a.startswith("走查") and not a.startswith("走查命令")), "")
    check_line = next((a for a in accept if a.startswith("验收命令")), "")
    result = summary
    if check_line:   # 写实际会跑的那条（和存进开工.json 的同一条，复查第 3 轮 B4）
        result = f"{summary}\n\n验收时程序会跑：`{test_command}`"
    look = _order_look(project, rel)   # 第三批 ③：要求放进项目前看页面的，复查通过后停在“等看页面”
    return {
        "look_required": bool(look),
        "look_images": look.get("look_images", []),
        "look_line": look.get("look_line", ""),
        "summary": summary,
        "steps": steps or ["照施工单第三节做"],
        "result": result,
        "view_how": (walk_line.split("：", 1)[-1].strip() if walk_line else "") or (
            f"见施工单「验收」一节（{rel}）" if accept else ""),
        "not_do": not_do,
        "questions": [],
        "allowed": allowed,
        "test_command": test_command,
        "walk_command": walk_command,
    }, ""


def _order_unfilled_fields(data: dict) -> list:
    """方案会带出去的几处里还有空白标记的项（用来写清“没填完”是哪一项）。"""
    fields = []

    def add(field: str, text) -> None:
        if PLACEHOLDER in str(text or "") and field not in fields:
            fields.append(field)

    add("要做什么", data.get("summary"))
    add("允许改哪些文件", "".join(data.get("allowed") or []))
    add("验收命令", data.get("test_command"))
    add("走查", (data.get("walk_command") or "") + (data.get("view_how") or ""))
    steps = data.get("steps") or []
    if not isinstance(steps, (list, tuple)):
        steps = [steps]
    add("具体做法", "".join(str(step) for step in steps))
    add("不做什么", data.get("not_do"))
    return fields


def _order_bad_scopes(data) -> list:
    """施工单允许范围里过不了 AI 方案那道检查的路径（复查第 7 轮：照施工单直出也要拦 [id]/*.tsx、带说明的名字）。"""
    return [(one, why) for one in _norm_paths(data.get("allowed")) if (why := _bad_scope_reason(one))]


def _order_gaps_of(data, why: str) -> list:
    if data is None:
        return ["读不了"]
    gaps = []
    if not data.get("summary"):
        gaps.append("要做什么")
    if not data.get("allowed"):
        gaps.append("允许改哪些文件")
    elif _order_bad_scopes(data):
        gaps.append("合法的允许范围")
    if not data.get("test_command"):
        gaps.append("验收命令")
    if _order_unfilled_fields(data):
        gaps.append("没填完")
    return gaps


def order_gaps(project, rel: str) -> list:
    """这份施工单缺的项（没缺返回空列表）：读不了、要做什么、允许改哪些文件、验收命令、没填完。"""
    data, why = _order_plan_data(project, rel)
    return _order_gaps_of(data, why)


_ORDER_GAP_HINT = {
    "要做什么": "要做什么：补一节 `## 一、要做什么（一句话）`，标题下面第一段写清要做什么。",
    "允许改哪些文件": "允许改哪些文件：补一节 `## 二、允许改哪些文件`，每个文件一行，写成 “- `路径`” 的样子。",
    "验收命令": "验收命令：在“验收”一节写一行 `- 验收命令：`python3 -m unittest discover -s tests -p \"test_xxx.py\"``，"
                "反引号里放一条完整命令。",
}


def _order_gap_text(rel: str, gaps: list, data, why: str) -> str:
    lines = [f"施工单：{rel}"]
    for gap in gaps:
        if gap == "读不了":
            lines.append(f"读不了：{why or '文件读不出来'}。请检查文件权限，或确认它是 UTF-8 编码。")
        elif gap == "没填完":
            for field in _order_unfilled_fields(data) or ["方案正文"]:
                lines.append(f"没填完：{field} 里还有 {PLACEHOLDER}，请写清。")
        elif gap == "合法的允许范围":
            for one, reason in _order_bad_scopes(data):
                lines.append(f"范围不合法：`{one}`（{reason}）。说明写在反引号外，方括号路径别和 * ? 混用。")
        else:
            lines.append(_ORDER_GAP_HINT[gap])
    return "\n".join(lines)


def _plan_from_order(project, rel: str):
    """按施工单的固定格式摘出方案内容；摘要、允许路径或验收命令缺一项就返回 None。"""
    data, _why = _order_plan_data(project, rel)
    if data is None or not data["summary"] or not data["allowed"] or not data["test_command"]:
        return None
    return data


def _build_prompt(card_dir: Path, requirement_text: str, need_profile: bool, project=None) -> str:
    """写方案的交代：规则在最前面（规则包），交代在 agents/交代/写方案/（第③步）。"""
    profile = (_say("写方案/项目说明·要写", 模板=PROFILE_TEMPLATE) if need_profile
               else _say("写方案/项目说明·已有"))
    orders = existing_orders(project, requirement_text)
    # 施工单已经写好了（68b 里调度员仍从头写方案 5 分 42 秒，⑦）：只摘，不重新设计
    order_part = _say("写方案/施工单那段", 施工单="、".join(orders)) if orders else ""
    return _rules("写方案") + _say(
        "写方案/拆方案", 字段=", ".join(_PLAN_SCHEMA_KEYS), 项目说明那行=profile, 施工单那段=order_part,
        卡片原文=requirement_text.strip()) + attachment_prompt(card_dir)


def _fail(card_dir: Path, summary: str, text: Optional[str] = None) -> int:
    flow_record(card_dir, "方案失败", src="程序", dst="你", summary=summary, text=text)
    return 2


def plan_card(project: str, card_id: str) -> int:
    """跑 Codex 写方案；成功写文件并记流转，失败记「方案失败」并返回 2。"""
    project = Path(project)
    collab = project / ".collab"
    card_dir = collab / "cards" / card_id
    req_path = card_dir / "需求卡.md"
    state_path = card_dir / "状态.json"
    if not req_path.is_file() or not state_path.is_file():
        print("找不到这张卡片的需求或状态。", file=sys.stderr)
        return 1
    try:
        card = Card.load(state_path)
    except Exception as exc:  # noqa: BLE001 — 任何意外都记失败，避免白烧额度空转
        return _fail(card_dir, "写方案时出问题，可以重写", text=str(exc))
    if card.state != "待对齐":
        print(f"这张卡现在是「{card.state}」，只有「待对齐」才能写方案。", file=sys.stderr)
        return 1

    schema_path = ""
    out_path = ""
    try:
        requirement = req_path.read_text(encoding="utf-8")
        need_profile = _need_profile(project)
        # 第一次写方案才照施工单直接生成；批准前点过“继续改”（有追加要求）就照旧叫 AI 重写，
        # 新要求、范围增减都按老流程处理（CARD-016 三轮复查：在这条路上补合并、补抄要求，越补越多，Claude 接手收回）
        revised = "## 追加要求" in requirement
        if not need_profile and not revised:
            for rel in existing_orders(project, requirement)[:1]:
                data, why = _order_plan_data(project, rel)
                gaps = _order_gaps_of(data, why)
                if gaps:
                    start_path = card_dir / "开工.json"
                    if start_path.exists():
                        start_path.unlink()
                    (card_dir / "方案.md").write_text(PLAN_TEMPLATE, encoding="utf-8")
                    summary = f"{Path(rel).stem} 缺：{'、'.join(gaps)}。补好后点“重写方案”。"
                    return _fail(card_dir, summary, text=_order_gap_text(rel, gaps, data, why))
                allowed = _norm_paths(data.get("allowed"))
                plan_md = _render_plan_md(data).replace(
                    "# 拆解方案\n\n",
                    f"# 拆解方案\n\n> 照施工单 {rel} 生成，没有让 AI 再写一遍。\n\n",
                    1,
                )
                (card_dir / "方案.md").write_text(plan_md, encoding="utf-8")
                req_path.write_text(_replace_allowed_section(requirement, allowed), encoding="utf-8")
                start = {"test_command": data["test_command"]}
                if data.get("walk_command"):
                    start["walk_command"] = data["walk_command"]
                if data.get("view_how"):
                    start["view_how"] = data["view_how"]   # 待验收时右栏“怎么看”读这里（复查第 3 轮 B5）
                if data.get("look_required"):
                    start["look_required"] = True
                    start["look_images"] = data.get("look_images") or []
                    start["look_line"] = data.get("look_line") or ""
                (card_dir / "开工.json").write_text(
                    json.dumps(start, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                flow_record(
                    card_dir, "方案", src="程序", dst="你",
                    summary=str(data.get("summary") or "").strip(),
                )
                render_board(collab)
                return 0
        prompt = _build_prompt(card_dir, requirement, need_profile, project=project)
        models = plan_models(project)
        model = models.get("codex") or "gpt-6.1-sol"
        effort = str(models.get("codex_effort") or "xhigh")

        with tempfile.NamedTemporaryFile("w", suffix=".schema.json", delete=False, encoding="utf-8") as schema_fh:
            schema_path = schema_fh.name
            json.dump(_plan_schema(), schema_fh, ensure_ascii=False)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as out_fh:
            out_path = out_fh.name
        argv = [
            "codex", "exec", "--skip-git-repo-check", "-s", "read-only",
            "-m", str(model), "-c", f'model_reasoning_effort="{effort}"',
            "--output-schema", schema_path, "-o", out_path, prompt,
        ]
        try:
            proc = subprocess.run(
                argv,
                cwd=str(project),
                stdin=subprocess.DEVNULL,
                timeout=_PLAN_TIMEOUT,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return _fail(card_dir, "这台电脑上没有找到 Codex，先安装或登录")
        except subprocess.TimeoutExpired:
            return _fail(card_dir, "写方案等太久，先停在这里")

        tool_text = ((proc.stderr or "") + "\n" + (proc.stdout or "")).strip()
        if proc.returncode != 0:
            return _fail(
                card_dir,
                "Codex 没有写成方案，详细原因记在这张卡的记录里",
                text=tool_text or "工具没有返回可用的结果",
            )
        raw = Path(out_path).read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except ValueError:
            return _fail(card_dir, "看不懂写方案的回答", text=raw)
        ok, why = _validate_answer(data, need_profile)
        if not ok:
            return _fail(card_dir, why, text=raw)

        allowed = _norm_paths(data.get("allowed"))
        plan_md = _render_plan_md(data)
        if has_blanks(plan_md):
            return _fail(card_dir, "方案里还有没填完的地方", text=raw)

        (card_dir / "方案.md").write_text(plan_md, encoding="utf-8")
        req_path.write_text(_replace_allowed_section(requirement, allowed), encoding="utf-8")
        start = {"test_command": str(data.get("test_command") or "").strip()}
        for rel in existing_orders(project, requirement)[:1]:
            walk_command = _order_walk_command(project, rel)
            if walk_command:
                start["walk_command"] = walk_command
            start.update(_order_look(project, rel))   # AI 写方案也守住施工单的“先看页面”（第三批复查第 1 轮 1）
        how, cmd, url = _clean_view_fields(data)
        if how:
            start["view_how"] = how
        if cmd:
            start["view_command"] = cmd
        if url:
            start["view_url"] = url
        (card_dir / "开工.json").write_text(
            json.dumps(start, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        extra = {}
        if need_profile and str(data.get("profile") or "").strip():
            prof_file = profile_path(project)
            prof_file.parent.mkdir(parents=True, exist_ok=True)
            prof_file.write_text(str(data["profile"]).strip() + "\n", encoding="utf-8")
            extra["new_profile"] = True

        try:
            extra["tokens"] = usage_from_text(tool_text)
        except Exception as exc:  # noqa: BLE001 — 算用量失败不能把写好的方案记成失败
            extra["tokens"] = {"ok": False, "reason": f"算用量时出问题（{exc}）"}

        flow_record(
            card_dir, "方案", src="Codex", dst="你",
            summary=str(data.get("summary") or "").strip(),
            **extra,
        )
        render_board(collab)
        return 0
    except Exception as exc:  # noqa: BLE001 — 崩溃也要留下失败记录，巡视才不会空转重跑
        return _fail(card_dir, "写方案时出问题，可以重写", text=str(exc))
    finally:
        for path in (schema_path, out_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
