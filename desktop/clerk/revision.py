"""旧版各改要求入口共用的逐条验收标准拆分准备服务（只读准备，绝不直接改卡）。"""
from __future__ import annotations

import inspect
import json
import math
import re
import threading
import time
from typing import Any
from urllib.parse import quote

from collab.phase import card_phase
from collab.rulebook import say
from collab.tasks import (
    TaskError,
    _load_card_for_revise,
    _locate_criteria_section,
    _split_criteria_body,
    validate_criteria_revision,
)

REVISION_TIMEOUT_SECONDS = 60
MAX_REVISION_OUTPUT_CHARS = 16_384
MAX_CRITERIA_CHANGES = 50
MAX_CRITERION_LEN = 500

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*\r?\n([\s\S]*?)\r?\n```\s*$", re.IGNORECASE)
_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{4,}", re.IGNORECASE)
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9._-]{4,}")


def _extract_secret(client: Any) -> str:
    if client is None:
        return ""
    raw = getattr(client, "api_key", "")
    return raw.strip() if isinstance(raw, str) else ""


def _redact_secret(text: str, client: Any = None) -> str:
    cleaned = str(text or "")
    secret = _extract_secret(client)
    if secret:
        candidates = {secret, quote(secret, safe="")}
        for candidate in candidates:
            if len(candidate) >= 2:
                cleaned = cleaned.replace(candidate, "***")
    cleaned = _BEARER_RE.sub("Bearer ***", cleaned)
    cleaned = _SK_RE.sub("sk-***", cleaned)
    return " ".join(cleaned.splitlines()).strip()


def _contains_secret(text: str, client: Any = None) -> bool:
    secret = _extract_secret(client)
    if not secret or len(secret) < 4:
        return False
    return secret in text or quote(secret, safe="") in text


def read_card_criteria(project: str, card_id: str) -> list[str]:
    """从任务卡片读取当前验收标准列表（只读）。"""
    cdir, _, _ = _load_card_for_revise(project, card_id)
    req = cdir / "需求卡.md"
    body = req.read_text(encoding="utf-8") if req.is_file() else ""
    if not body:
        return []
    try:
        _, _, section_body = _locate_criteria_section(body)
    except TaskError:
        return []
    return _split_criteria_body(section_body)


def build_criteria_summary(criteria_base: list[str], criteria_changes: list[dict[str, Any]]) -> str:
    """根据实际旧条目与合法修改生成多行可读确认摘要。"""
    lines: list[str] = []
    next_index = len(criteria_base) + 1
    for change in criteria_changes:
        if "replaceNth" in change:
            nth = change["replaceNth"]
            old_text = criteria_base[nth - 1] if 1 <= nth <= len(criteria_base) else ""
            lines.append(f"第{nth}条：{old_text} → {change['text']}")
        else:
            lines.append(f"新增第{next_index}条：{change['add']}")
            next_index += 1
    return "\n".join(lines)


# 改一处同步另一处：与 dsh-plugin/core/src/revision.ts 的 buildRevisionPrompt 保持逐字一致。
def build_revision_prompt(criteria: list[str] | tuple[str, ...], user_text: str, attachment_count: int = 0) -> dict[str, str]:
    """生成与新版插件完全一致的逐条拆分提示词（含截图分支规则）。"""
    if isinstance(attachment_count, (int, float)) and not isinstance(attachment_count, bool) and math.isfinite(attachment_count) and attachment_count > 0:
        count = math.floor(attachment_count)
    else:
        count = 0

    # 话在 agents/交代/行政/改要求拆分·*.md（规则包第③步；插件 dsh-plugin 里另有一份同样的话）
    system = say("行政/改要求拆分·系统", 截图那条=say("行政/改要求拆分·截图") if count > 0 else "")
    criteria_list = list(criteria or [])
    criteria_block = ("\n".join(f"{idx}. {item}" for idx, item in enumerate(criteria_list, 1)) if criteria_list
                      else say("行政/改要求拆分·没有标准"))
    user = say("行政/改要求拆分·用户", 标准=criteria_block, 原话=str(user_text or "").strip(),
               截图张数=say("行政/改要求拆分·几张截图", 张数=count) if count > 0 else "")
    return {"system": system, "user": user}


def _unwrap_single_json_fence(raw: str) -> str | None:
    trimmed = str(raw or "").strip()
    if trimmed.startswith("{") and trimmed.endswith("}"):
        return trimmed
    match = _JSON_FENCE_RE.match(trimmed)
    if not match:
        return None
    inner = (match.group(1) or "").strip()
    return inner if inner.startswith("{") and inner.endswith("}") else None


def parse_revision_model_output(
    project: str,
    card_id: str,
    raw_text: str,
    criteria_base: list[str],
    client: Any = None,
) -> list[dict[str, Any]]:
    """严格解析并复用 collab.tasks.validate_criteria_revision 校验模型输出。"""
    if len(raw_text) > MAX_REVISION_OUTPUT_CHARS:
        raise RuntimeError("模型返回内容过长，请把修改意见写更具体后再试")
    if _contains_secret(raw_text, client):
        raise RuntimeError("拆分修改要求失败：模型返回内容包含敏感信息，已拒绝使用")

    json_text = _unwrap_single_json_fence(raw_text)
    if not json_text:
        raise RuntimeError("拆分修改要求失败：模型没有返回有效格式，请重试")

    def _reject_constant(val: str) -> None:
        raise ValueError(f"unsupported JSON constant: {val}")

    try:
        parsed = json.loads(json_text, parse_constant=_reject_constant)
    except ValueError as exc:
        raise RuntimeError("拆分修改要求失败：模型返回的 JSON 格式有误，请重试") from exc

    if not isinstance(parsed, dict) or set(parsed.keys()) != {"changes"} or not isinstance(parsed["changes"], list):
        raise RuntimeError("拆分修改要求失败：缺少或包含不支持的修改字段，请重试")

    raw_changes = parsed["changes"]
    if len(raw_changes) > MAX_CRITERIA_CHANGES:
        raise RuntimeError(f"单次最多修改 {MAX_CRITERIA_CHANGES} 条验收标准")

    changes: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_changes, 1):
        if not isinstance(item, dict):
            raise RuntimeError(f"第 {idx} 项修改格式不对")
        keys = set(item.keys())
        if keys == {"replaceNth", "text"}:
            nth = item["replaceNth"]
            if not isinstance(nth, int) or isinstance(nth, bool):
                raise RuntimeError("修改的验收标准序号必须是整数")
            if not isinstance(item["text"], str):
                raise RuntimeError(f"第 {nth} 条验收标准必须是文字")
            if _contains_secret(item["text"], client):
                raise RuntimeError("拆分修改要求失败：模型返回内容包含敏感信息，已拒绝使用")
            changes.append({"replaceNth": nth, "text": item["text"]})
        elif keys == {"add"}:
            if not isinstance(item["add"], str):
                raise RuntimeError("新增验收标准必须是文字")
            if _contains_secret(item["add"], client):
                raise RuntimeError("拆分修改要求失败：模型返回内容包含敏感信息，已拒绝使用")
            changes.append({"add": item["add"]})
        else:
            raise RuntimeError("模型返回了不支持的验收标准修改操作")

    try:
        validate_criteria_revision(project, card_id, criteria_base=criteria_base, criteria_changes=changes)
    except TaskError as exc:
        raise RuntimeError(f"拆分修改要求失败：{_redact_secret(str(exc), client)}") from exc

    normalized: list[dict[str, Any]] = []
    for item in changes:
        if "replaceNth" in item:
            normalized.append({"replaceNth": item["replaceNth"], "text": item["text"].strip()})
        else:
            normalized.append({"add": item["add"].strip()})
    return normalized


def _assert_card_revisable(project: str, card_id: str) -> None:
    try:
        _, _, card = _load_card_for_revise(project, card_id)
    except TaskError as exc:
        raise RuntimeError(str(exc)) from exc
    if card.state in ("已合并", "已放弃", "已完成"):
        raise RuntimeError("这张卡已结束，要改请新建一张")
    phase = card_phase(project, card_id)
    if phase.get("code") in ("done", "dropped"):
        raise RuntimeError("这张卡已结束，要改请新建一张")
    if "revise" not in (phase.get("actions") or []):
        raise RuntimeError("当前阶段暂不支持逐条拆分修改")


def _chat_deadline_kwargs(client: Any, deadline: float, cancel_event: threading.Event) -> dict[str, Any]:
    try:
        sig = inspect.signature(client.chat)
    except (TypeError, ValueError):
        return {}
    params = sig.parameters
    accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    extra: dict[str, Any] = {}
    if accepts_var_kw or "deadline" in params:
        extra["deadline"] = deadline
    if accepts_var_kw or "cancel_event" in params:
        extra["cancel_event"] = cancel_event
    return extra


def _call_model_with_deadline(client: Any, messages: list[dict[str, str]], timeout_seconds: float) -> str:
    done = threading.Event()
    cancel_event = threading.Event()
    result_box: dict[str, Any] = {}
    stream_len = 0
    overflowed = False
    deadline = time.monotonic() + timeout_seconds
    extra_kwargs = _chat_deadline_kwargs(client, deadline, cancel_event)

    def on_text(piece: str) -> None:
        nonlocal stream_len, overflowed
        stream_len += len(piece or "")
        if stream_len > MAX_REVISION_OUTPUT_CHARS:
            overflowed = True
            cancel_event.set()

    def worker() -> None:
        try:
            answer = client.chat(messages, on_text=on_text, tools=None, **extra_kwargs)
            result_box["answer"] = answer
        except BaseException as exc:  # noqa: BLE001
            result_box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=worker, daemon=True).start()
    if not done.wait(timeout_seconds):
        cancel_event.set()
        raise RuntimeError("拆分修改要求超时，请重试")

    if "error" in result_box:
        exc = result_box["error"]
        safe_msg = _redact_secret(str(exc), client) or "模型调用失败，请重试"
        raise RuntimeError(f"拆分修改要求失败：{safe_msg}")

    if overflowed:
        raise RuntimeError("模型返回内容过长，请把修改意见写更具体后再试")

    answer = result_box.get("answer")
    if not isinstance(answer, dict):
        raise RuntimeError("拆分修改要求失败：模型返回结构无效，请重试")
    return str(answer.get("text") or "")


def prepare_revision(
    project: str,
    card_id: str,
    text: str,
    client: Any,
    attachment_count: int = 0,
) -> dict[str, Any]:
    """用旧版行政模型将用户意见拆成逐条修改方案；只读准备，绝不修改任何卡片文件。"""
    words = str(text or "").strip()
    if not words:
        raise RuntimeError("请写上要改什么")

    _assert_card_revisable(project, card_id)

    if client is None or not callable(getattr(client, "chat", None)):
        raise RuntimeError("旧版行政还没配置模型，请先到设置里填写模型密钥")

    try:
        criteria_base = read_card_criteria(project, card_id)
    except TaskError as exc:
        raise RuntimeError(str(exc)) from exc

    prompt = build_revision_prompt(criteria_base, words, attachment_count)
    messages = [
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": prompt["user"]},
    ]

    timeout = REVISION_TIMEOUT_SECONDS
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        timeout = 60

    raw_output = _call_model_with_deadline(client, messages, float(timeout))
    changes = parse_revision_model_output(project, card_id, raw_output, criteria_base, client=client)

    # 二次核验：模型运行期间卡片状态或验收标准均未发生变化。
    _assert_card_revisable(project, card_id)
    try:
        latest_criteria = read_card_criteria(project, card_id)
    except TaskError as exc:
        raise RuntimeError(str(exc)) from exc
    if latest_criteria != criteria_base:
        raise RuntimeError("验收标准刚刚发生变化，请再点一次重试")

    summary = build_criteria_summary(criteria_base, changes)
    return {
        "ok": True,
        "card": card_id,
        "criteria_base": criteria_base,
        "criteria_changes": changes,
        "summary": summary,
    }
