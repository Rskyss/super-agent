"""能力卡片的运行（67 号单）。

负责按卡片去调联网搜索、大模型 API 或命令行工具（AI 助手），
拿回结果由程序查合格后存入成果文件夹，每次运行追加一条记录。
出了问题分清类别：ok、format、model、block、error。
同一张卡片同时只运行一次；不自动换模型，有备用模型时在返回结果中给出 next_model。
"""
from __future__ import annotations

import functools
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from collab import abilities, blocks, blocks_check, blocks_env, blocks_web, providers
from collab.abilities import AbilityError
from collab.agents import QuietTimeout, watched_runner
from collab.blocks import BlockError
from desktop.clerk.client import ModelClient, ModelError

_running: set[str] = set()
_lock = threading.Lock()


class _OneAtATime:
    def __init__(self, ability_id: str):
        self.ability_id = ability_id

    def __enter__(self):
        with _lock:
            if self.ability_id in _running:
                raise AbilityError(f"能力卡片「{self.ability_id}」正在运行，稍等它做完")
            _running.add(self.ability_id)

    def __exit__(self, *exc):
        with _lock:
            _running.discard(self.ability_id)


def _calc_next_model(card: dict, current_model: str | None, status: str) -> str | None:
    if status != "model":
        return None
    backup_models = card.get("backup_models") or []
    if not backup_models:
        return None
    primary_model = card.get("model")
    if current_model == primary_model:
        return backup_models[0]
    if current_model in backup_models:
        idx = backup_models.index(current_model)
        if idx + 1 < len(backup_models):
            return backup_models[idx + 1]
        return None
    return backup_models[0]


def _collect_secrets(keys: dict | None) -> list[str]:
    secrets = []
    if keys and isinstance(keys, dict):
        for v in keys.values():
            if isinstance(v, str) and v.strip():
                secrets.append(v.strip())
    secrets.sort(key=len, reverse=True)
    return secrets


def _mask_text(text: str, secrets: list[str]) -> str:
    if not isinstance(text, str):
        return text
    res = text
    for s in secrets:
        if s:
            res = res.replace(s, "[已隐藏]")
    return blocks_check._mask(res)


def _mask_output(output, secrets: list[str]):
    if isinstance(output, str):
        return _mask_text(output, secrets)
    if isinstance(output, list):
        masked_list = []
        for item in output:
            if isinstance(item, dict):
                m_item = {}
                for k, v in item.items():
                    m_item[k] = _mask_text(v, secrets) if isinstance(v, str) else v
                masked_list.append(m_item)
            elif isinstance(item, str):
                masked_list.append(_mask_text(item, secrets))
            else:
                masked_list.append(item)
        return masked_list
    return output


def _is_safe_file(path: Path, base_dir: Path) -> bool:
    """检查文件是否安全（非符号链接，且解析后的真实物理路径必须在 base_dir 内）。"""
    try:
        if path.is_symlink():
            return False
        if not path.is_file():
            return False
        return path.resolve().is_relative_to(base_dir.resolve())
    except Exception:
        return False


def _save_deliverables(ability_id: str, run_id: str, root, output_type: str, result, output_file: str | None) -> list[str]:
    """把成果存进 成果/<编号>/<运行编号>/ 文件夹。返回成果文件名列表。"""
    res_dir = abilities.result_folder(ability_id, run_id, root)
    res_dir.mkdir(parents=True, exist_ok=True)
    if output_type == "text":
        filename = "结果.txt"
        (res_dir / filename).write_text(result, encoding="utf-8")
        return [filename]
    if output_type == "list":
        filename = "结果.json"
        (res_dir / filename).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return [filename]
    if output_type in ("image", "file"):
        if output_file and Path(result).is_file():
            dest = res_dir / output_file
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(result, dest)
            return [output_file]
    return []


def _save_deliverables_safe(ability_id: str, run_id: str, root, output_type: str, result, output_file: str | None) -> tuple[bool, list[str], str]:
    """安全保存成果文件，异常不向外抛，返回 (成功与否, 文件列表, 错误信息)。"""
    try:
        files = _save_deliverables(ability_id, run_id, root, output_type, result, output_file)
        return True, files, ""
    except Exception as exc:
        return False, [], f"保存成果文件失败：{exc}"


def run(ability_id: str, inputs: dict, root=None, *,
        model: str | None = None,
        kind: str = "run",
        keys: dict | None = None,
        search_fn=None,
        client_factory=None,
        runner=None,
        path: str | None = None,
        codex_images: Path | str | None = None) -> dict:
    """运行一次能力卡片。"""
    if kind not in ("run", "trial"):
        raise AbilityError("运行类型只能是 run 或 trial")

    card = abilities.load_ability(ability_id, root)
    backups = card.get("backup_models") or []
    if model is not None and model not in backups:
        raise AbilityError(f"模型 {model} 不在备用模型名单里")

    prepared = abilities.prepare(card, inputs)

    with _OneAtATime(ability_id):
        run_id = abilities.new_run_id()
        start_time = time.time()
        how_kind = card["how"]["kind"]

        if how_kind == "search":
            selected_model = None
        else:
            selected_model = model or card.get("model")

        secret_list = _collect_secrets(keys)

        def _finish(ok: bool, status: str, reason: str, how: str, output,
                    platform_said: str = "", files: list[str] | None = None) -> dict:
            elapsed = round(time.time() - start_time, 3)
            files = files or []
            next_model = _calc_next_model(card, selected_model, status)

            clean_reason = _mask_text(reason, secret_list)
            clean_how = _mask_text(how, secret_list)
            clean_platform_said = _mask_text(platform_said, secret_list)
            return_output = _mask_output(output, secret_list)

            # 构造 inputs 字段写入记录：values 文字打码后截到 500 字
            masked_inputs = {}
            for k, v in prepared["values"].items():
                if isinstance(v, str):
                    masked_v = _mask_text(v, secret_list)
                    if len(masked_v) > 500:
                        masked_v = blocks_check._mask_cut(masked_v, 500)
                    masked_inputs[k] = masked_v
                else:
                    masked_inputs[k] = v

            # 构造 output 字段写入记录：文字打码后截到 2000 字
            if isinstance(return_output, str):
                if len(return_output) > 2000:
                    rec_output = blocks_check._mask_cut(return_output, 2000)
                else:
                    rec_output = return_output
            else:
                rec_output = return_output

            rec = {
                "type": kind,
                "run_id": run_id,
                "ok": ok,
                "status": status,
                "reason": clean_reason,
                "model": selected_model,
                "seconds": elapsed,
                "inputs": masked_inputs,
                "output": rec_output,
                "platform_said": clean_platform_said,
            }
            abilities.add_run(ability_id, rec, root)

            return {
                "run_id": run_id,
                "ok": ok,
                "status": status,
                "reason": clean_reason,
                "how": clean_how,
                "model": selected_model,
                "seconds": elapsed,
                "output": return_output,
                "next_model": next_model,
                "platform_said": clean_platform_said,
                "files": files,
            }

        # 跑之前先查积木块
        block_id = card["block"]
        try:
            block_card = blocks.load_card(block_id, root)
        except BlockError:
            return _finish(
                ok=False,
                status="block",
                reason=f"这张能力卡片用的积木块「{block_id}」已经移出基础层",
                how="去基础层重新接上这个积木块",
                output=None
            )

        if how_kind == "cli" and selected_model is None:
            selected_model = block_card.get("model")

        last_check = blocks.last_record(block_id, root, type="check")
        if last_check and last_check.get("status") not in ("ok", "quota", "listed"):
            bname = block_card.get("name") or block_id
            last_reason = last_check.get("reason", "")
            return _finish(
                ok=False,
                status="block",
                reason=f"积木块「{bname}」上次检测没通过：{last_reason}",
                how="去基础层点它检测一次",
                output=None
            )

        try:
            abilities.fits_block(card, block_card)
        except AbilityError as exc:
            bname = block_card.get("name") or block_id
            return _finish(
                ok=False,
                status="block",
                reason=str(exc),
                how=f"去基础层检查积木块「{bname}」或重新配置",
                output=None
            )

        if how_kind == "llm_api":
            if last_check and "models" in last_check and isinstance(last_check["models"], list):
                model_list = last_check["models"]
                if len(model_list) < 400 and selected_model not in model_list:
                    bname = block_card.get("name") or block_id
                    return _finish(
                        ok=False,
                        status="model",
                        reason=f"{bname} 的模型名单里没有 {selected_model}",
                        how="更换模型再试",
                        output=None
                    )

        # 三种跑法
        if how_kind == "search":
            key_account = block_card.get("key_account")
            key = (keys or {}).get(key_account)
            if not key or not isinstance(key, str) or not key.strip():
                return _finish(
                    ok=False,
                    status="block",
                    reason="还没有联网搜索的密钥：请先在钥匙串中配置",
                    how="去基础层检测或配置密钥",
                    output=None
                )

            key = key.strip()
            if key not in secret_list:
                secret_list.append(key)
                secret_list.sort(key=len, reverse=True)

            query = prepared["query"]
            max_results = prepared["max_results"]
            time_range = prepared["time_range"]
            fn = search_fn or blocks_web.search

            try:
                raw_results = fn(query, key, max_results, time_range)
            except blocks_web.SearchError as exc:
                st = getattr(exc, "status", None)
                if st in (401, 403, 429, 432, 433):
                    return _finish(ok=False, status="block", reason=str(exc), how="去基础层检测或更换密钥", output=None)
                return _finish(ok=False, status="error", reason=str(exc), how="稍后再试", output=None)
            except BlockError as exc:
                return _finish(ok=False, status="error", reason=str(exc), how="稍后再试", output=None)
            except Exception as exc:  # noqa: BLE001
                return _finish(ok=False, status="error", reason=f"联网搜索出错：{exc}", how="稍后再试", output=None)

            if not raw_results:
                ok, check_reason = abilities.check_output(card, [], prepared["values"])
                return _finish(ok=False, status="format", reason=check_reason, how="调整搜索关键词或参数后再试", output=[], files=[])

            formatted = [
                {"title": item.get("title", ""), "url": item.get("url", ""), "summary": item.get("snippet", "")}
                for item in raw_results
            ]
            formatted = _mask_output(formatted, secret_list)

            ok, check_reason = abilities.check_output(card, formatted, prepared["values"])
            save_ok, files, err = _save_deliverables_safe(ability_id, run_id, root, "list", formatted, None)
            if not save_ok:
                return _finish(ok=False, status="error", reason=err, how="稍后再试", output=formatted, files=[])

            if ok:
                return _finish(ok=True, status="ok", reason="成功完成", how="", output=formatted, files=files)
            return _finish(ok=False, status="format", reason=check_reason, how="调整搜索关键词或参数后再试", output=formatted, files=files)

        if how_kind == "llm_api":
            key_account = block_card.get("key_account")
            key = (keys or {}).get(key_account)
            if not key or not isinstance(key, str) or not key.strip():
                bname = block_card.get("name") or block_id
                return _finish(
                    ok=False,
                    status="block",
                    reason="还没有大模型 API 的密钥：请先在钥匙串中配置",
                    how=f"去基础层检测积木块「{bname}」或配置密钥",
                    output=None
                )

            key = key.strip()
            if key not in secret_list:
                secret_list.append(key)
                secret_list.sort(key=len, reverse=True)

            messages = []
            if prepared.get("system"):
                messages.append({"role": "system", "content": prepared["system"]})
            messages.append({"role": "user", "content": prepared["prompt"]})

            try:
                if client_factory:
                    client = client_factory(block_card["base_url"], selected_model, key)
                else:
                    provider_name = block_card.get("provider", "")
                    known = providers.find(provider_name) if provider_name else None
                    thinking_off = bool(known and known.get("thinking_off"))
                    client = ModelClient(base_url=block_card["base_url"], model=selected_model, api_key=key,
                                         timeout=120, thinking_off=thinking_off)
            except Exception as exc:  # noqa: BLE001
                words = blocks_check._words(exc, key)
                return _finish(ok=False, status="error", reason=f"连接大模型服务出错：{exc}", how="稍后再试", output=None, platform_said=words)

            try:
                reply = client.chat(messages)
            except ModelError as exc:
                code = getattr(exc, "code", None)
                words = blocks_check._words(exc, key)
                if code in (400, 402, 403, 404, 429):
                    code_reasons = {
                        400: "模型请求参数错误或模型不可用",
                        402: "额度用完或欠费",
                        403: "模型未开通或无权访问",
                        404: "模型不存在",
                        429: "请求过于频繁或额度受限",
                    }
                    reason = code_reasons.get(code, "模型服务出错")
                    next_m = _calc_next_model(card, selected_model, "model")
                    how = f"可以改用备用模型「{next_m}」再试" if next_m else "更换其他可用模型再试"
                    return _finish(ok=False, status="model", reason=reason, how=how, output=None, platform_said=words)
                if code == 401:
                    bname = block_card.get("name") or block_id
                    return _finish(ok=False, status="block", reason="大模型 API 密钥不对或已失效",
                                   how=f"去基础层检测积木块「{bname}」或换密钥", output=None, platform_said=words)
                return _finish(ok=False, status="error", reason=str(exc) or "模型服务暂时出错",
                               how="稍后再试", output=None, platform_said=words)
            except Exception as exc:  # noqa: BLE001
                words = blocks_check._words(exc, key)
                return _finish(ok=False, status="error", reason=str(exc) or "模型调用失败",
                               how="稍后再试", output=None, platform_said=words)

            raw_text = reply.get("text", "")
            text_output = _mask_text(raw_text, secret_list)
            ok, check_reason = abilities.check_output(card, text_output, prepared["values"])
            save_ok, files, err = _save_deliverables_safe(ability_id, run_id, root, "text", text_output, None)
            if not save_ok:
                return _finish(ok=False, status="error", reason=err, how="稍后再试", output=text_output, files=[])

            if ok:
                return _finish(ok=True, status="ok", reason="成功完成", how="", output=text_output, files=files)
            return _finish(ok=False, status="format", reason=check_reason, how="调整要求或提示词后再试", output=text_output, files=files)

        if how_kind == "cli":
            cmd = block_card.get("command", "")
            search_path = path or os.environ.get("PATH", "")
            cmd_path = blocks_env._which(cmd, search_path)
            if not cmd_path:
                return _finish(
                    ok=False,
                    status="block",
                    reason=f"电脑上找不到 {cmd}",
                    how=f"检查电脑上是否安装了 {block_card.get('name') or cmd}",
                    output=None
                )

            args = blocks.build_args(block_card, prepared["prompt"], selected_model)
            args[0] = cmd_path

            timeout = prepared["timeout"]
            runner_fn = runner or functools.partial(watched_runner, poll=0.5, quiet_limit=timeout + 5)

            if codex_images is None:
                codex_home = os.environ.get("CODEX_HOME")
                if codex_home:
                    img_folder = Path(codex_home) / "generated_images"
                else:
                    img_folder = Path.home() / ".codex" / "generated_images"
            else:
                img_folder = Path(codex_images)

            output_file = prepared["output_file"]
            is_image = card["output"]["type"] == "image"

            existing_images: set[str] = set()
            if is_image and img_folder.exists():
                try:
                    for p in img_folder.rglob("*"):
                        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg"):
                            existing_images.add(str(p.resolve()))
                            existing_images.add(str(p))
                except Exception:
                    pass

            tmp_dir = tempfile.mkdtemp(prefix="能力运行-")
            cli_start_time = time.time()
            try:
                code, cli_output = runner_fn(args, tmp_dir, timeout)
                lines = [ln.strip() for ln in (cli_output or "").splitlines() if ln.strip()]
                last_line = lines[-1] if lines else ""
                platform_said = blocks_check._mask_cut(last_line, 160) if last_line else ""

                found_path = None
                retrieved_elsewhere = False
                target_in_tmp = Path(tmp_dir) / output_file
                if _is_safe_file(target_in_tmp, Path(tmp_dir)):
                    found_path = target_in_tmp
                elif is_image and img_folder.exists():
                    candidate = None
                    latest_mtime = -1.0
                    for p in img_folder.rglob("*"):
                        if not _is_safe_file(p, img_folder):
                            continue
                        if p.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                            continue
                        if str(p) in existing_images or str(p.resolve()) in existing_images:
                            continue
                        try:
                            mtime = p.stat().st_mtime
                            if mtime >= latest_mtime:
                                latest_mtime = mtime
                                candidate = p
                        except OSError:
                            pass
                    if candidate is not None:
                        found_path = candidate
                        retrieved_elsewhere = True

                if found_path is not None:
                    out_type = card["output"]["type"]
                    if out_type == "text":
                        try:
                            raw_text = found_path.read_text(encoding="utf-8")
                        except Exception as exc:
                            return _finish(ok=False, status="format", reason=f"读取成果文件出错：{exc}",
                                           how="检查工具生成的文件编码格式", output=None, platform_said=platform_said, files=[])

                        masked_text = _mask_text(raw_text, secret_list)
                        ok, check_reason = abilities.check_output(card, masked_text, prepared["values"])
                        save_ok, files, err = _save_deliverables_safe(ability_id, run_id, root, "text", masked_text, None)
                        if not save_ok:
                            return _finish(ok=False, status="error", reason=err, how="稍后再试", output=masked_text, platform_said=platform_said, files=[])

                        if ok:
                            return _finish(ok=True, status="ok", reason="成功完成", how="", output=masked_text,
                                           platform_said=platform_said, files=files)
                        return _finish(ok=False, status="format", reason=check_reason, how="调整画面描述或参数后再试",
                                       output=masked_text, platform_said=platform_said, files=files)

                    else:
                        has_secret = False
                        try:
                            data = found_path.read_bytes()
                            for sec in secret_list:
                                if sec and sec.encode("utf-8") in data:
                                    has_secret = True
                                    break
                        except Exception:
                            pass
                        if has_secret:
                            return _finish(ok=False, status="error", reason="成果里带了密钥，没有保存",
                                           how="稍后再试", output=None, platform_said=platform_said, files=[])

                        ok, check_reason = abilities.check_output(card, found_path, prepared["values"])
                        save_ok, files, err = _save_deliverables_safe(ability_id, run_id, root, out_type, found_path, output_file)
                        if not save_ok:
                            return _finish(ok=False, status="error", reason=err, how="稍后再试", output=output_file, platform_said=platform_said, files=[])

                        if ok:
                            reason = "Codex 把图存到了别处，已拿回来" if retrieved_elsewhere else "成功完成"
                            return _finish(ok=True, status="ok", reason=reason, how="", output=output_file,
                                           platform_said=platform_said, files=files)
                        how = "调整画面描述或参数后再试" if is_image else "检查工具生成的文件是否符合要求"
                        return _finish(ok=False, status="format", reason=check_reason, how=how,
                                       output=output_file, platform_said=platform_said, files=files)

                if code == 0:
                    return _finish(ok=False, status="format", reason=f"没有找到 {output_file}",
                                   how="检查工具是否按要求生成了文件", output=None, platform_said=platform_said)

                out_lower = (cli_output or "").lower()
                login_keywords = ("not logged", "please log in", "please login", "unauthorized",
                                  "not authenticated", "authentication failed", "please sign in", "codex login")
                if any(k in out_lower for k in login_keywords):
                    return _finish(ok=False, status="block", reason="工具未登录",
                                   how=f"在终端里运行一次 {cmd}，按提示登录后再检测一次",
                                   output=None, platform_said=platform_said)

                model_not_found = "model" in out_lower and any(k in out_lower for k in (
                    "not found", "invalid", "unknown", "does not exist", "not supported"
                ))
                if blocks_check.is_quota(cli_output) or model_not_found:
                    next_m = _calc_next_model(card, selected_model, "model")
                    how = f"可以改用备用模型「{next_m}」再试" if next_m else "更换其他可用模型再试"
                    reason = "额度用完或被限流" if blocks_check.is_quota(cli_output) else "模型名不对或不支持"
                    return _finish(ok=False, status="model", reason=reason, how=how,
                                   output=None, platform_said=platform_said)

                return _finish(ok=False, status="error", reason=f"命令行工具运行出错（退出码 {code}）",
                               how="稍后再试", output=None, platform_said=platform_said)

            except subprocess.TimeoutExpired:
                return _finish(ok=False, status="error", reason="运行超时：规定时间内未完成",
                               how="稍后再试或适当简化任务", output=None)
            except QuietTimeout:
                return _finish(ok=False, status="error", reason="长时间没有任何动静，被停下了",
                               how="稍后再试", output=None)
            except Exception as exc:  # noqa: BLE001
                return _finish(ok=False, status="error", reason=f"运行命令行工具出错：{exc}",
                               how="稍后再试", output=None)
            finally:
                blocks_env.remove_folder(tmp_dir)

        raise AbilityError(f"不支持的运行方式：{how_kind}")
