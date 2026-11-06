"""写方案：只读调用 Codex，把结果落到卡片文件。"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from collab.config import load_models
from collab.flow import record as flow_record
from collab.state import Card
from collab.workspace import PROFILE_TEMPLATE, has_blanks, profile_path, render_board

_PLAN_TIMEOUT = 15 * 60
_PLAN_SCHEMA_KEYS = (
    "summary", "steps", "result", "not_do", "questions",
    "allowed", "test_command", "profile",
)
_TEXT_KEYS = ("summary", "result", "not_do", "test_command", "profile")
_LIST_KEYS = ("steps", "questions", "allowed")


def _plan_schema() -> dict:
    props = {}
    for key in _TEXT_KEYS:
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
    if any(ch in first for ch in "*?["):
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
    for k in _PLAN_SCHEMA_KEYS:
        if k not in data:
            return False, f"缺少字段：{k}"
    for k in _TEXT_KEYS:
        if not isinstance(data.get(k), str):
            return False, f"字段类型不对：{k}"
    for k in _LIST_KEYS:
        value = data.get(k)
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
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
    return (
        "# 拆解方案\n\n"
        f"## 要做什么（一句话）\n{plan['summary'].strip()}\n\n"
        f"## 拆成几步，每步做什么、产出什么\n{steps_txt}\n\n"
        f"## 你会看到什么结果（验收标准）\n{str(plan.get('result') or '').strip()}\n\n"
        f"## 明确不做什么\n{str(plan.get('not_do') or '').strip()}\n\n"
        f"## 需要你确认的问题\n{q_txt}\n"
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


def _build_prompt(card_dir: Path, requirement_text: str, need_profile: bool) -> str:
    lines = [
        "你可以用只读命令查看项目，但不要修改任何文件。",
        "根据下面的需求卡，用大白话写一份拆解方案。",
        "输出必须是 JSON，一个对象，键必须齐全："
        + ", ".join(_PLAN_SCHEMA_KEYS)
        + "。",
        "- summary：一句话说明要做什么。",
        "- steps：字符串数组，每一步一句话。",
        "- result：用户会看到什么验收结果。",
        "- not_do：明确不做什么。",
        "- questions：字符串数组；没有问题就写空数组。问题里要带推荐答案。",
        "- allowed：字符串数组，尽量窄，列出允许改动的相对路径（相对项目根）。",
        "- test_command：一条测试命令字符串。",
    ]
    if need_profile:
        lines.append(
            f"- profile：没有现成项目说明时，按下面模板各段填真实内容（不要用「（待填）」）：\n{PROFILE_TEMPLATE}"
        )
    else:
        lines.append("- profile：项目里已有项目说明时，填空字符串。")
    lines.append("")
    lines.append("需求卡原文：")
    lines.append(requirement_text.strip())
    return "\n".join(lines)


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
        prompt = _build_prompt(card_dir, requirement, need_profile)
        models = load_models(project)
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
        (card_dir / "开工.json").write_text(
            json.dumps({"test_command": str(data.get("test_command") or "").strip()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        extra = {}
        if need_profile and str(data.get("profile") or "").strip():
            prof_file = profile_path(project)
            prof_file.parent.mkdir(parents=True, exist_ok=True)
            prof_file.write_text(str(data["profile"]).strip() + "\n", encoding="utf-8")
            extra["new_profile"] = True

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
