"""DSH 改版第 10 项：调度员真实检测、新版积木库接入与确认逻辑。

约束与安全边界：
- 只允许白名单订阅工具：codex、agy（不接受任意 shell 命令，不含其他不相关工具）。
- 强制要求显式传入新版积木库路径 explicit_blocks_home，禁止空路径、用户原有默认库及其所有子目录（包含 symlink）。
- 普通 status() 只检查配置与未过期缓存指纹，不调用真实 CLI；detect() 才调 checker(..., root=..., record=False)。
- 同一配置指纹的并发 detect() 走 single-flight 合并，仅执行一次真实检测。
- 接入前必须严格校验 tool 与 card_id 白名单；遇到 TaskBusy 保持等待标记并仅对目标未写方案卡做单卡重试调度。
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Optional

from collab import blocks, blocks_check
from collab.config import (
    DEFAULT_MODELS,
    ConfigError,
    load_model_options,
    legacy_dispatcher,
    load_models,
    save_legacy_dispatcher,
)
from collab.phase import card_phase
from desktop.clerk import tasks as clerk_tasks

ALLOWED_DISPATCHERS = ("codex", "agy")
TOOL_LABELS = {"codex": "Codex", "agy": "agy"}
_CARD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# 目标卡遇 TaskBusy 时的单卡重试登记：(resolved_project, card_id) -> token
_RETRY_LOCK = threading.Lock()
_ACTIVE_RETRIES: dict[tuple[str, str], str] = {}


class DispatcherError(RuntimeError):
    """调度员接入参数或执行失败。"""


class _InflightDetect:
    """同一个指纹的并发检测占位：首线程执行，其余等待同一个结果。"""

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Optional[dict] = None
        self.error: Optional[BaseException] = None


def require_isolated_blocks_home(explicit_blocks_home: str) -> Path:
    """校验显式新版积木库目录，拒绝空路径及旧版真实积木库本身或其任何子目录。"""
    raw = str(explicit_blocks_home or "").strip()
    if not raw:
        raise ValueError("必须提供显式的新版积木库目录，不能使用空路径")
    resolved = Path(raw).expanduser().resolve()
    default_resolved = Path(blocks.DEFAULT_ROOT).expanduser().resolve()
    try:
        resolved.relative_to(default_resolved)
    except ValueError:
        pass
    else:
        raise ValueError("安全限制：不能读写用户原有真实积木库或其子目录")
    return resolved


def normalize_dispatcher_tool(tool: str) -> str:
    """把工具名收敛到白名单（仅 codex / agy）；非法值直接拒绝。"""
    clean = str(tool or "").strip().lower()
    if clean not in ALLOWED_DISPATCHERS:
        raise DispatcherError(f"调度员只支持订阅工具 Codex 或 agy，不支持：{tool or '空'}")
    return clean


def validate_card_id(card_id: str) -> str:
    """校验卡片编号白名单：空字符串仅用于普通聊天；非空必须严格符合卡片编号格式。"""
    if card_id is None:
        return ""
    raw = str(card_id)
    if raw == "":
        return ""
    if not _CARD_ID_RE.fullmatch(raw):
        raise ValueError(f"卡片编号不安全或格式不对：{raw!r}")
    return raw


def build_dispatcher_proposal(tool: str = "codex", card_id: str = "") -> dict:
    """生成前端确认卡展示的四要素与元信息（大白话，先严查 tool 与 card_id）。"""
    clean_tool = normalize_dispatcher_tool(tool)
    clean_card = validate_card_id(card_id)
    label = TOOL_LABELS[clean_tool]
    return {
        "tool": clean_tool,
        "label": label,
        "card": clean_card,
        "title": f"接一个调度员：{label}",
        "what": f"把电脑里已装好并登录过的 {label} 接进来当调度员，用来写方案、拆步骤和安排执行与复查。",
        "how": f"在新版积木库里用 {label} 的预置接法，并做一次很短的连通检测，确认它真能回话（不改项目代码）。",
        "needYou": f"如果你还没装好或没登录 {label}，请先自己完成安装与登录，再点确认；我不会替你自动安装软件。",
        "scope": f"只保存进新版积木库和当前项目设置，把调度员设成 {label}；不会改动原有积木库，也不读取你的钥匙串密钥。",
    }


def _selected_model_for(project: str, tool: str) -> tuple[str, dict, dict]:
    """读取项目里给该工具/调度员配好的模型与附加选项，用于探测与指纹计算。"""
    models = load_models(project)
    model_opts = load_model_options(project)
    _tool, role_entry = legacy_dispatcher(project)   # 11-17 起团队里没有调度员岗，老配置里残留的这一份只在这里读
    tool_model = str(models.get(tool) or DEFAULT_MODELS.get(tool) or "").strip()
    if role_entry.get("tool") == tool and str(role_entry.get("model") or "").strip():
        chosen_model = str(role_entry.get("model") or "").strip()
    else:
        chosen_model = tool_model
    opts_for_fp = {
        "codex_effort": models.get("codex_effort"),
        "model_options": model_opts.get(tool) if isinstance(model_opts, dict) else None,
        "dispatcher_role_model": role_entry if role_entry.get("tool") == tool else None,
        "tool_model": tool_model,
    }
    return chosen_model, models, opts_for_fp


def _fingerprint(project: str, isolated_home: Path, tool: str, model: str, extra: dict) -> tuple:
    proj_resolved = str(Path(project).resolve())
    return (
        proj_resolved,
        str(isolated_home),
        tool,
        model,
        json.dumps(extra, ensure_ascii=False, sort_keys=True),
    )


def _format_failure_reason(raw_reason: str, tool: str) -> str:
    label = TOOL_LABELS.get(tool, tool)
    reason = str(raw_reason or "").strip() or "真实检测没通过"
    lowered = reason.lower()
    hints = ("请先安装", "请先登录", "先安装", "先登录")
    if any(h in reason for h in hints):
        return reason
    if any(k in lowered for k in ("not found", "command not found", "no such file", "未找到", "找不到", "没装", "安装")):
        return f"{reason}。请先安装 {label} 后再试"
    if any(k in lowered for k in ("login", "auth", "unauthorized", "没有登录", "未登录", "登录")):
        return f"{reason}。请先登录 {label} 后再试"
    return reason


def _source_path(project: str, card_id: str) -> Path:
    return Path(project) / ".collab" / "cards" / card_id / "来源.json"


def _clear_deferred_plan(project: str, card_id: str) -> None:
    """目标卡真正开始写方案后，清掉等待调度员标记。"""
    if not card_id:
        return
    src_path = _source_path(project, card_id)
    if not src_path.is_file():
        return
    try:
        data = json.loads(src_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("deferred_plan"):
            data.pop("deferred_plan", None)
            src_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except (OSError, ValueError):
        pass


def _is_deferred_card(project: str, card_id: str) -> bool:
    src_path = _source_path(project, card_id)
    if not src_path.is_file():
        return False
    try:
        data = json.loads(src_path.read_text(encoding="utf-8"))
        return isinstance(data, dict) and bool(data.get("deferred_plan"))
    except (OSError, ValueError):
        return False


def _schedule_target_plan_retry(project: str, card_id: str) -> None:
    """遇到另一张卡正在写方案时，只针对这张目标卡排队重试，绝不启动无关旧卡或全局 Watcher。"""
    key = (str(Path(project).resolve()), card_id)
    token = uuid.uuid4().hex
    with _RETRY_LOCK:
        if key in _ACTIVE_RETRIES:
            return
        _ACTIVE_RETRIES[key] = token

    def retry_loop() -> None:
        attempt = 0
        try:
            while True:
                # 前几次快速跟进（保证测试 3 秒内触发），之后约 1 秒温和重试，直到目标完成、变更或被取消
                delay = 0.05 if attempt < 4 else (0.25 if attempt < 12 else 1.0)
                time.sleep(delay)
                attempt += 1
                cdir = Path(project) / ".collab" / "cards" / card_id
                if not (cdir / "状态.json").is_file():
                    return
                if not _is_deferred_card(project, card_id):
                    return
                if card_phase(project, card_id).get("code") != "plan_waiting":
                    return
                busy = clerk_tasks.running(project, "plan")
                if busy is not None:
                    continue
                try:
                    clerk_tasks.launch(project, "plan", card_id, ["plan", str(project), card_id])
                except clerk_tasks.TaskBusy:
                    continue
                except (clerk_tasks.TaskError, OSError):
                    return
                _clear_deferred_plan(project, card_id)
                return
        finally:
            with _RETRY_LOCK:
                if _ACTIVE_RETRIES.get(key) == token:
                    _ACTIVE_RETRIES.pop(key, None)

    threading.Thread(target=retry_loop, daemon=True).start()


class DispatcherDetector:
    """调度员真实状态探测与缓存（支持 TTL 过期、配置指纹失效与并发 single-flight）。"""

    def __init__(
        self,
        checker: Optional[Callable[..., dict]] = None,
        clock: Optional[Callable[[], float]] = None,
        cache_ttl: float = 300.0,
    ) -> None:
        self._checker = checker if checker is not None else blocks_check.check_cli
        self._clock = clock if clock is not None else time.monotonic
        self._cache_ttl = float(cache_ttl)
        self._lock = threading.Lock()
        # key: (project_resolved, home_resolved, tool) -> (fingerprint, result_dict, cached_at)
        self._cache: dict[tuple[str, str, str], tuple[tuple, dict, float]] = {}
        self._inflight: dict[tuple, _InflightDetect] = {}

    def _is_expired(self, cached_at: float) -> bool:
        if self._cache_ttl <= 0:
            return True
        return (self._clock() - cached_at) >= self._cache_ttl

    def _current_dispatcher(self, project: str, isolated_home: Path) -> tuple[str, str, tuple]:
        tool = legacy_dispatcher(project)[0].strip().lower()
        if tool not in ALLOWED_DISPATCHERS:
            fp = _fingerprint(project, isolated_home, tool, "", {})
            return tool, "", fp
        model, _models, extra = _selected_model_for(project, tool)
        fp = _fingerprint(project, isolated_home, tool, model, extra)
        return tool, model, fp

    def status(self, project: str, explicit_blocks_home: str) -> dict:
        """只读配置与未过期缓存指纹判断是否已通过真实检测；不触发 CLI 调用。"""
        try:
            isolated_home = require_isolated_blocks_home(explicit_blocks_home)
        except ValueError as exc:
            return {"status": "missing", "tool": "", "model": "", "reason": str(exc), "verified": False}

        if not project or not Path(project).is_dir():
            return {"status": "missing", "tool": "", "model": "", "reason": "项目目录不存在", "verified": False}

        try:
            tool, model, fp = self._current_dispatcher(project, isolated_home)
        except ConfigError as exc:
            return {"status": "missing", "tool": "", "model": "", "reason": str(exc), "verified": False}

        if tool not in ALLOWED_DISPATCHERS:
            return {
                "status": "missing",
                "tool": tool,
                "model": "",
                "reason": "还没有接调度员（仅支持 Codex 或 agy）",
                "verified": False,
            }

        cache_key = (str(Path(project).resolve()), str(isolated_home), tool)
        with self._lock:
            entry = self._cache.get(cache_key)
            if entry is not None:
                cached_fp, cached_result, cached_at = entry
                if cached_fp == fp and not self._is_expired(cached_at):
                    if cached_result.get("status") == "ready":
                        return dict(cached_result)
                    if cached_result.get("reason"):
                        return {
                            "status": "missing",
                            "tool": tool,
                            "model": model,
                            "reason": str(cached_result.get("reason")),
                            "verified": False,
                        }

        return {
            "status": "missing",
            "tool": tool,
            "model": model,
            "reason": f"调度员 {TOOL_LABELS[tool]} 还没有通过真实检测",
            "verified": False,
        }

    def detect(self, project: str, explicit_blocks_home: str, force: bool = False) -> dict:
        """对当前项目配置的调度员做真实检测；先检查显式新版积木库目录。"""
        isolated_home = require_isolated_blocks_home(explicit_blocks_home)
        if not project or not Path(project).is_dir():
            raise ValueError("项目目录不存在")
        tool = legacy_dispatcher(project)[0].strip().lower()
        if tool not in ALLOWED_DISPATCHERS:
            return {
                "status": "missing",
                "tool": tool,
                "model": "",
                "reason": "还没有接调度员（仅支持 Codex 或 agy）",
                "verified": False,
            }
        return self.detect_tool(project, str(isolated_home), tool, force=force)

    def detect_tool(self, project: str, explicit_blocks_home: str, tool: str, force: bool = False) -> dict:
        """检测指定白名单工具（可用于接入确认前试通，不改动现有团队配置）。"""
        isolated_home = require_isolated_blocks_home(explicit_blocks_home)
        clean_tool = normalize_dispatcher_tool(tool)
        if not project or not Path(project).is_dir():
            raise ValueError("项目目录不存在")

        model, _models, extra = _selected_model_for(project, clean_tool)
        fp = _fingerprint(project, isolated_home, clean_tool, model, extra)
        cache_key = (str(Path(project).resolve()), str(isolated_home), clean_tool)

        inflight: Optional[_InflightDetect] = None
        leader = False

        with self._lock:
            if not force:
                entry = self._cache.get(cache_key)
                if entry is not None:
                    cached_fp, cached_result, cached_at = entry
                    if cached_fp == fp and cached_result.get("status") == "ready" and not self._is_expired(cached_at):
                        return dict(cached_result)
            existing = self._inflight.get(fp)
            if existing is not None:
                inflight = existing
            else:
                inflight = _InflightDetect()
                self._inflight[fp] = inflight
                leader = True

        if not leader:
            inflight.event.wait()
            if inflight.error is not None:
                raise inflight.error
            return dict(inflight.result or {})

        try:
            isolated_home.mkdir(parents=True, exist_ok=True)
            card = dict(blocks.builtin_card(clean_tool))
            if model:
                card["model"] = model
            raw = self._checker(card, root=str(isolated_home), record=False)
            raw_dict = raw if isinstance(raw, dict) else {}
            ok = bool(raw_dict.get("ok"))
            raw_reason = str(raw_dict.get("reason") or raw_dict.get("how") or "").strip()
            if ok:
                result = {
                    "status": "ready",
                    "tool": clean_tool,
                    "label": TOOL_LABELS[clean_tool],
                    "model": str(raw_dict.get("model") or model or ""),
                    "reason": raw_reason or f"{TOOL_LABELS[clean_tool]} 已接好并测通",
                    "verified": True,
                }
            else:
                result = {
                    "status": "missing",
                    "tool": clean_tool,
                    "label": TOOL_LABELS[clean_tool],
                    "model": model,
                    "reason": _format_failure_reason(raw_reason, clean_tool),
                    "how": str(raw_dict.get("how") or "").strip(),
                    "verified": False,
                }
            checked_at = self._clock()
            with self._lock:
                self._cache[cache_key] = (fp, dict(result), checked_at)
            inflight.result = result
            return dict(result)
        except BaseException as exc:
            inflight.error = exc
            raise
        finally:
            with self._lock:
                if self._inflight.get(fp) is inflight:
                    self._inflight.pop(fp, None)
            inflight.event.set()

    def mark_ready(self, project: str, explicit_blocks_home: str, tool: str, result: dict) -> None:
        """确认并保存角色/模型之后，把最新指纹同步记成 ready，避免紧接着轮询闪烁。"""
        isolated_home = require_isolated_blocks_home(explicit_blocks_home)
        clean_tool = normalize_dispatcher_tool(tool)
        model, _models, extra = _selected_model_for(project, clean_tool)
        fp = _fingerprint(project, isolated_home, clean_tool, model, extra)
        cache_key = (str(Path(project).resolve()), str(isolated_home), clean_tool)
        now = self._clock()
        with self._lock:
            self._cache[cache_key] = (fp, dict(result), now)


def _propose_in_isolated_blocks(isolated_home: Path, tool: str) -> dict:
    """复用既有 propose_connect_cli 生成预置块提议，并将派生文件完全限制在新版积木库目录。"""
    isolated_home.mkdir(parents=True, exist_ok=True)
    events: list[dict] = []
    fake_conv = SimpleNamespace(
        id=f"dsh-dispatcher-{tool}-{uuid.uuid4().hex[:8]}",
        project=str(isolated_home),
        turn=1,
        turn_expected=1,
        _conv=SimpleNamespace(turn=1),
        deleted=False,
        emit=events.append,
    )
    clerk_tasks.propose_connect_cli(fake_conv, {"template": tool})
    for ev in reversed(events):
        if isinstance(ev, dict) and isinstance(ev.get("block"), dict):
            return dict(ev["block"])
    return dict(blocks.builtin_card(tool))


def connect_dispatcher(
    project: str,
    explicit_blocks_home: str,
    tool: str,
    card_id: str = "",
    detector: Optional[DispatcherDetector] = None,
) -> dict:
    """用户明确确认后：先严格校验全部入参，再隔离生成接法 -> 真实检测 -> 成功才保存角色并启动目标卡。"""
    isolated_home = require_isolated_blocks_home(explicit_blocks_home)
    clean_tool = normalize_dispatcher_tool(tool)
    target_card = validate_card_id(card_id)
    if not project or not Path(project).is_dir():
        raise DispatcherError("项目文件夹不存在")
    if target_card and not (Path(project) / ".collab" / "cards" / target_card / "状态.json").is_file():
        raise DispatcherError(f"任务卡 {target_card} 不存在")

    active_detector = detector if detector is not None else DispatcherDetector()
    block_card = _propose_in_isolated_blocks(isolated_home, clean_tool)
    detect_res = active_detector.detect_tool(project, str(isolated_home), clean_tool, force=True)
    if detect_res.get("status") != "ready":
        return {
            "ok": False,
            "status": "missing",
            "tool": clean_tool,
            "label": TOOL_LABELS[clean_tool],
            "model": detect_res.get("model") or "",
            "reason": detect_res.get("reason") or f"{TOOL_LABELS[clean_tool]} 检测未通过，已保留原有设置",
            "started_plan": False,
            "plan_note": "",
            "card": target_card,
        }

    # 真实检测通过：保存到新版积木库，并把当前项目的调度员保存为该工具。
    model, _models, _extra = _selected_model_for(project, clean_tool)
    card_to_save = dict(block_card)
    if detect_res.get("model") or model:
        card_to_save["model"] = str(detect_res.get("model") or model)
    blocks.save_card(card_to_save, str(isolated_home))
    blocks.add_record(
        card_to_save["id"],
        {
            "type": "check",
            "ok": True,
            "status": "ok",
            "model": card_to_save.get("model", ""),
            "reason": detect_res.get("reason", ""),
        },
        str(isolated_home),
    )
    save_legacy_dispatcher(project, clean_tool)
    active_detector.mark_ready(project, str(isolated_home), clean_tool, detect_res)

    started_plan = False
    plan_note = ""
    if target_card:
        cdir = Path(project) / ".collab" / "cards" / target_card
        if (cdir / "状态.json").is_file() and card_phase(project, target_card).get("code") == "plan_waiting":
            # 只有真正开始写方案才清 deferred_plan 标记；遇 TaskBusy 保持标记并排队重试这一张卡。
            try:
                clerk_tasks.launch(project, "plan", target_card, ["plan", str(project), target_card])
                _clear_deferred_plan(project, target_card)
                started_plan = True
            except clerk_tasks.TaskBusy:
                _schedule_target_plan_retry(project, target_card)
                plan_note = "已有另一张卡正在写方案，已为这张卡安排排队，前一张写完后会自动开始"
            except (OSError, clerk_tasks.TaskError) as exc:
                plan_note = f"调度员已接好，但启动写方案失败：{exc}"

    return {
        "ok": True,
        "status": "ready",
        "tool": clean_tool,
        "label": TOOL_LABELS[clean_tool],
        "model": detect_res.get("model") or card_to_save.get("model", ""),
        "reason": detect_res.get("reason") or "",
        "started_plan": started_plan,
        "plan_note": plan_note,
        "card": target_card,
    }
