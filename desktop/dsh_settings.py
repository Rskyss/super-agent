"""新版设置整页（页面对齐改版第六批）：能力层、积木库的只读列表。

只读新版自己的积木库资料夹（网关传进来的 SUPER_AGENT_BLOCKS_HOME，已校验不是旧版位置）。
用户 11-16 晚定：资料由用户自己添加，所以这里**不补预置能力卡**（不调 abilities.ensure_builtin）。
"""
from __future__ import annotations

from pathlib import Path

from collab import abilities, blocks
from collab.blocks import BlockError

_OUTPUT_WORDS = {"text": "一段文字", "list": "一组结果", "image": "一张图片", "file": "一个文件"}


def _num(value) -> str:
    """2.0 → 2，0.5 → 0.5。"""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def check_text(output: dict) -> str:
    """把能力卡“怎么算做对”翻成一句大白话；没有规则写“—”。"""
    output = output if isinstance(output, dict) else {}
    check = output.get("check") if isinstance(output.get("check"), dict) else {}
    kind = output.get("type")
    if kind == "text":
        lo, hi = check.get("min_chars"), check.get("max_chars")
        if lo is not None and hi is not None:
            return f"字数 {lo}–{hi}"
        if hi is not None:
            return f"字数不超过 {hi}"
        if lo is not None:
            return f"字数至少 {lo}"
        rlo, rhi = check.get("min_ratio"), check.get("max_ratio")
        if rlo is not None and rhi is not None:
            return f"字数在要求的 {_num(rlo)}–{_num(rhi)} 倍之间"
        if rhi is not None:
            return f"字数不超过要求的 {_num(rhi)} 倍"
        if rlo is not None:
            return f"字数至少是要求的 {_num(rlo)} 倍"
        return "—"
    if kind == "list":
        n = check.get("min_items")
        return f"至少 {n} 条" if n else "—"
    if kind == "image":
        side = check.get("min_side")
        return f"图片边长至少 {side}" if side else "—"
    return "—"


def _inputs(card: dict) -> str:
    props = ((card.get("inputSchema") or {}).get("properties") or {})
    names = [str((spec or {}).get("title") or key) for key, spec in props.items()]
    return " + ".join(names) if names else "—"


def _who(card: dict, home: Path) -> str:
    block_id = str(card.get("block") or "")
    try:
        name = str(blocks.load_card(block_id, home).get("name") or block_id)
    except BlockError:
        name = block_id or "—"
    model = str(card.get("model") or "")
    return f"{name} · {model}" if model else name


def _inside_home(home: Path, *names: str) -> None:
    """新版库里读的几个文件夹，里面任何一层都不许有软链接（单张卡、记录目录都算），
    防止经软链读到旧版库（复查第 1、2 轮轻微 19 / 5）。"""
    import os
    root = Path(home).resolve()
    for name in names:
        sub = Path(home) / name
        if not sub.exists() and not sub.is_symlink():
            continue
        if sub.is_symlink() or not str(sub.resolve()).startswith(str(root) + "/"):
            raise ValueError(f"安全限制：新版积木库里的“{name}”指到了库外面，不读")
        for dirpath, dirnames, filenames in os.walk(sub, followlinks=False):
            for entry in dirnames + filenames:
                if os.path.islink(os.path.join(dirpath, entry)):
                    raise ValueError(f"安全限制：新版积木库里的“{name}/{entry}”是软链接，不读")


def ability_rows(home) -> list:
    """能力层表格的每一行；坏卡单独一行写原因，不藏。"""
    home = Path(home)
    _inside_home(home, abilities.ABILITY_DIR, blocks.CARD_DIR, blocks.RECORD_DIR)
    rows = []
    for card in abilities.list_abilities(home):
        if "problem" in card:
            rows.append({"id": card["id"], "title": card["id"], "problem": str(card["problem"])})
            continue
        try:
            runs = len(abilities.runs(card["id"], home))
        except Exception:  # noqa: BLE001 — 记录读不了就当没跑过，不挡整页
            runs = 0
        rows.append({
            "id": card["id"],
            "title": str(card.get("title") or card["id"]),
            "inputs": _inputs(card),
            "output": _OUTPUT_WORDS.get((card.get("output") or {}).get("type"), "—"),
            "who": _who(card, home),
            "check": check_text(card.get("output") or {}),
            "runs": runs,
            "draft": runs == 0,
            "problem": "",
        })
    return rows


def block_rows(home) -> list:
    """积木库每一块：名字、分组、上次检测结果、被几张能力卡用到。"""
    home = Path(home)
    _inside_home(home, abilities.ABILITY_DIR, blocks.CARD_DIR, blocks.RECORD_DIR)
    used: dict[str, int] = {}
    for card in abilities.list_abilities(home):
        if "problem" not in card:
            used[str(card.get("block") or "")] = used.get(str(card.get("block") or ""), 0) + 1
    rows = []
    for card in blocks.list_cards(home):
        if "problem" in card:
            rows.append({"id": card["id"], "name": card["id"], "group": "tool", "status": "fail",
                         "reason": str(card["problem"]), "used_by": 0})
            continue
        last = blocks.last_record(card["id"], home, type="check")
        if last is None:
            status = "never"
        elif last.get("ok") or last.get("status") in ("quota", "listed"):
            status = "ok"
        else:
            status = "fail"
        category = blocks.category(card)
        group = "brain" if card.get("kind") == "llm_api" else "worker" if category == "agent" else "tool"
        rows.append({"id": card["id"], "name": str(card.get("name") or card["id"]), "group": group,
                     "kind": card.get("kind", ""), "status": status,
                     "reason": str((last or {}).get("reason") or ""), "used_by": used.get(card["id"], 0)})
    return rows


_TOOL_NAMES = {"codex": "Codex", "cursor": "Cursor", "agy": "agy", "qwen": "千问"}
_PICK_ROLES = ("tester", "developer", "reviewer")


def team_rows(project) -> dict:
    """业务流程页：现在开发流程每个岗位谁来跑（读真实分工，不写死），以及能选哪些（用户 11-16 晚：过渡期照旧版可选）。"""
    from collab.config import (CODEX_EFFORTS, WORKERS, ConfigError, _EFFORT_LABELS, load_model_options,
                               load_role_models, load_team, model_label)
    try:
        team = load_team(project)
        role_models = load_role_models(project, team)
    except ConfigError as exc:
        return {"roles": {}, "picks": {}, "dispatcher": "", "problem": str(exc)}
    roles, picks = {}, {}
    for role in _PICK_ROLES:
        entry = role_models.get(role)
        if not entry:
            none = team.get(role) == "none"
            roles[role] = "不复查" if none else ""
            picks[role] = {"tool": "none" if none else str(team.get(role) or ""), "model": "", "effort": ""}
            continue
        tool = entry["tool"]
        models = {tool: entry.get("model", "")}
        if tool == "codex":
            models["codex_effort"] = entry.get("effort", "")
        label = model_label(tool, models)
        name = _TOOL_NAMES.get(tool, tool)
        roles[role] = f"{name} · {label}" if label else name
        picks[role] = {"tool": tool, "model": entry.get("model", ""), "effort": entry.get("effort", "") if tool == "codex" else ""}
    worker = [{"value": t, "label": _TOOL_NAMES.get(t, t)} for t in WORKERS]
    options = load_model_options()
    models = {t: [{"value": o["value"], "label": o.get("label") or o["value"],
                   **({"efforts": list(o["efforts"])} if o.get("efforts") else {})}
                  for o in options.get(t, [])] for t in WORKERS}
    efforts = [{"value": e, "label": _EFFORT_LABELS.get(e, e)} for e in CODEX_EFFORTS]
    dispatcher = str(team.get("dispatcher") or "")
    return {"roles": roles, "picks": picks, "dispatcher": _TOOL_NAMES.get(dispatcher, dispatcher), "problem": "",
            "options": {"tester": worker, "developer": worker, "reviewer": worker + [{"value": "none", "label": "不复查"}]},
            "models": models, "efforts": efforts}


def save_role(project, role: str, tool: str, model: str, effort: str) -> None:
    """只改一个岗位（工具 + 模型 + Codex 强度），别的岗位、调度员原样不动；不对就报错，什么都不存。"""
    from collab.config import ConfigError, WORKERS, clean_role_models, save_role_models, save_team_member
    if role not in _PICK_ROLES:
        raise ValueError("只能改写测试、写代码、复查这三步")
    if tool == "none":
        if role != "reviewer":
            raise ValueError("只有复查可以选“不复查”")
    elif tool not in WORKERS:
        raise ValueError(f"不认识的工具：{tool!r}")
    import fcntl
    lock_path = Path(project) / ".collab" / ".配置.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if tool != "none":
            clean_role_models({role: {"tool": tool, "model": model, "effort": effort}})  # 先查，有错什么都不存
        # 同一页同时保存两步时排队写，免得后写的盖掉先写的（复查第 1 轮中等 8）
        with open(lock_path, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                save_team_member(project, role, tool)
                if tool != "none":
                    save_role_models(project, {role: {"tool": tool, "model": model, "effort": effort}})
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except ConfigError as exc:
        raise ValueError(str(exc)) from exc
