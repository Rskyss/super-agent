"""写方案：只读调用 Codex，把结果落到卡片文件。"""
from __future__ import annotations

import json
import os
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


def _norm_paths(items):
    return [str(p).strip().replace("\\", "/") for p in (items or []) if str(p).strip()]


def _bad_scope_reason(path: str) -> Optional[str]:
    p = path.strip().replace("\\", "/")
    if not p:
        return "路径是空的"
    if os.path.isabs(p):
        return "不能写绝对路径"
    parts = [x for x in p.split("/") if x]
    if ".." in parts:
        return "不能写带 .. 的路径"
    for part in parts:
        low = part.lower()
        if low in (".git", ".env", ".collab"):
            return f"不能指向 {low}"
    return None


def _validate_answer(data: dict, need_profile: bool) -> Tuple[bool, str]:
    if not isinstance(data, dict):
        return False, "回答不是一份结构化的内容"
    for k in _PLAN_SCHEMA_KEYS:
        if k not in data:
            return False, f"缺少字段：{k}"
    summary = str(data.get("summary") or "").strip()
    if not summary:
        return False, "摘要不能为空"
    test_cmd = str(data.get("test_command") or "").strip()
    if not test_cmd:
        return False, "测试命令不能为空"
    allowed = _norm_paths(data.get("allowed"))
    if not allowed:
        return False, "允许改动的文件列表不能为空"
    for one in allowed:
        why = _bad_scope_reason(one)
        if why:
            return False, f"允许改动的范围不合法：{one}（{why}）"
    prof = str(data.get("profile") or "")
    if need_profile:
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
        "你只读项目，不要改文件、不要运行命令。",
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
    card = Card.load(state_path)
    if card.state != "待对齐":
        print(f"这张卡现在是「{card.state}」，只有「待对齐」才能写方案。", file=sys.stderr)
        return 1

    requirement = req_path.read_text(encoding="utf-8")
    prof_file = profile_path(project)
    need_profile = True
    if prof_file.is_file():
        txt = prof_file.read_text(encoding="utf-8")
        need_profile = has_blanks(txt)
    prompt = _build_prompt(card_dir, requirement, need_profile)
    models = load_models(project)
    model = models.get("codex") or "gpt-6.1-sol"
    effort = str(models.get("codex_effort") or "xhigh")

    with tempfile.NamedTemporaryFile("w", suffix=".schema.json", delete=False, encoding="utf-8") as schema_fh:
        schema_path = schema_fh.name
        json.dump({"type": "object", "required": list(_PLAN_SCHEMA_KEYS)}, schema_fh, ensure_ascii=False)
    out_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as out_fh:
            out_path = out_fh.name
        argv = [
            "codex", "exec", "--skip-git-repo-check", "-s", "read-only",
            "-m", str(model), "-c", f'model_reasoning_effort="{effort}"',
            "--output-schema", schema_path, "-o", out_path, prompt,
        ]
        proc = subprocess.run(
            argv,
            cwd=str(project),
            stdin=subprocess.DEVNULL,
            timeout=_PLAN_TIMEOUT,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            reason = (proc.stderr or proc.stdout or "").strip() or "工具没有返回可用的结果"
            flow_record(card_dir, "方案失败", src="程序", dst="你", summary=reason[:200])
            return 2
        raw = Path(out_path).read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except ValueError:
            flow_record(card_dir, "方案失败", src="程序", dst="你", summary="看不懂写方案的回答")
            return 2
        ok, why = _validate_answer(data, need_profile)
        if not ok:
            flow_record(card_dir, "方案失败", src="程序", dst="你", summary=why)
            return 2

        allowed = _norm_paths(data.get("allowed"))
        plan_md = _render_plan_md(data)
        if has_blanks(plan_md):
            flow_record(card_dir, "方案失败", src="程序", dst="你", summary="方案里还有没填完的地方")
            return 2

        (card_dir / "方案.md").write_text(plan_md, encoding="utf-8")
        req_path.write_text(_replace_allowed_section(requirement, allowed), encoding="utf-8")
        (card_dir / "开工.json").write_text(
            json.dumps({"test_command": str(data.get("test_command") or "").strip()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        extra = {}
        if need_profile and str(data.get("profile") or "").strip():
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
    except subprocess.TimeoutExpired:
        flow_record(card_dir, "方案失败", src="程序", dst="你", summary="写方案等太久，先停在这里")
        return 2
    except OSError as exc:
        flow_record(card_dir, "方案失败", src="程序", dst="你", summary=f"写方案时出问题：{exc}")
        return 2
    finally:
        for path in (schema_path, out_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
