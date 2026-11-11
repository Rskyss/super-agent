"""和模型说话：兼容 OpenAI 的接口，回复逐字返回；出错时给用户看得懂的话，绝不带上密钥。"""
from __future__ import annotations

import http.client
import json
import re
import socket
import time
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://tokenhub.tencentmaas.com/v1"
DEFAULT_MODEL = "deepseek/deepseek-flash"


class ModelError(Exception):
    """用户能看懂的失败原因；partial 是出错前已经收到的文字。"""

    def __init__(self, message: str, partial: str = ""):
        super().__init__(message)
        self.partial = partial


def _http_failure(code: int) -> ModelError:
    if code == 404:
        return ModelError("这家服务商没有这个模型，或服务地址不对。请检查模型名和地址。")
    if code in (401, 403):
        return ModelError("模型密钥不对或已失效，请到设置里重新填写。")
    if code in (402, 429):
        return ModelError("模型额度用完了，或请求太频繁。请稍后再试，或到服务商那里检查余额。")
    return ModelError(f"模型服务商暂时出错了（代码 {code}），请稍后再试。")


def _platform_words(exc) -> str:
    """平台拒绝时回的那句话（常见写法是 {"error": {"message": ...}}），压成一行。读不出就空着，出什么错都不往外抛。
    这里不截短：要先打码再截短，不然会把半截密钥留下（第二轮复查严重第 1 条），截短交给调用方。"""
    try:
        data_bytes = exc.read(4001)
        raw = data_bytes[:4000].decode("utf-8", "replace")
        if len(data_bytes) > 4000:  # 没读全：最后那串字可能是半截密钥、半截邮箱，打码认不出来，先去掉（第三轮复查严重）
            raw = re.sub(r"\S*$", "", raw)
        try:
            data = json.loads(raw, strict=False)
        except ValueError:
            data = None
        if isinstance(data, dict):
            err = data.get("error")
            text = (err.get("message") or err.get("code")) if isinstance(err, dict) else err
            if not (isinstance(text, str) and text.strip()):
                text = data.get("message")
            if isinstance(text, str) and text.strip():
                raw = text
        return " ".join(str(raw).split())
    except Exception:  # noqa: BLE001
        return ""


def _timed_out(reason) -> bool:
    return isinstance(reason, (socket.timeout, TimeoutError))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随跳转：否则密钥会被带到另一个地址。"""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)
_KEY_SHAPE = re.compile(r"[\x21-\x7e]+")
_ADDRESS_SHAPE = re.compile(r"https?://[^\s/]+\S*")
_PROVIDER_ERROR = "模型服务商返回了错误，请稍后再试。"
_TIMED_OUT = "模型回复超时了，已收到的内容保留着。"


class ModelClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL, api_key: str = "",
                 timeout: float = 60, thinking_off: bool = True):
        self.base_url = base_url.strip().rstrip("/")
        self.model = model
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self.thinking_off = thinking_off

    def chat(self, messages: list, on_text=None, tools: list = None) -> dict:
        """发一轮对话。on_text 每收到一段文字调用一次。返回 text、tool_calls、tokens。"""
        if not self.api_key:
            raise ModelError("还没有填模型密钥，请先到设置里填写。")
        if not _KEY_SHAPE.fullmatch(self.api_key):
            raise ModelError("模型密钥格式不对，请重新复制后填写。")
        if not _ADDRESS_SHAPE.fullmatch(self.base_url):
            raise ModelError("模型服务地址不对，要以 https:// 开头。")
        body = {"model": self.model, "messages": messages, "stream": True,
                "stream_options": {"include_usage": True}}
        if self.thinking_off:
            body["thinking"] = {"type": "disabled"}
        if tools:
            body["tools"] = tools
        try:
            request = urllib.request.Request(
                self.base_url + "/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}, method="POST")
            response = _OPENER.open(request, timeout=self.timeout)
        except ValueError:
            raise ModelError("模型服务地址不对，要以 https:// 开头。") from None
        except urllib.error.HTTPError as exc:
            failure = _http_failure(exc.code)
            failure.code = exc.code
            failure.detail = _platform_words(exc)  # 积木库检测要写出平台的原话（2026-11-11 火山引擎）；用前由调用方打码
            raise failure from None
        except urllib.error.URLError as exc:
            if _timed_out(exc.reason):
                raise ModelError("模型回复超时了，请再试一次。") from None
            raise ModelError("连不上模型服务，请检查网络。") from None
        except http.client.HTTPException:
            raise ModelError("模型服务返回了看不懂的内容，请检查网络或代理。") from None
        except OSError as exc:
            if _timed_out(exc):
                raise ModelError("模型回复超时了，请再试一次。") from None
            raise ModelError("连不上模型服务，请检查网络。") from None
        with response:
            return self._read(response, on_text)

    def _read(self, response, on_text) -> dict:
        text, calls, tokens, finished = [], {}, None, False
        progress = time.monotonic()  # 只有收到文字或工具调用才算进展，心跳和空片段不算
        try:
            for raw in response:
                if time.monotonic() - progress > self.timeout:
                    if finished:
                        break  # 说完了，只是等不到结束标记
                    raise ModelError(_TIMED_OUT, "".join(text))
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    if line.startswith("{") and "error" in line:  # 状态 200，内容却是错误
                        raise ModelError(_PROVIDER_ERROR, "".join(text))
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    finished = True
                    break
                try:
                    data = json.loads(payload)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("error"):
                    raise ModelError(_PROVIDER_ERROR, "".join(text))
                usage = data.get("usage")
                if isinstance(usage, dict):
                    tokens = usage.get("total_tokens", tokens)
                choices = data.get("choices")
                for choice in choices if isinstance(choices, list) else []:
                    if not isinstance(choice, dict):
                        continue
                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                    piece = delta.get("content")
                    if isinstance(piece, str) and piece:
                        text.append(piece)
                        progress = time.monotonic()
                        if on_text:
                            on_text(piece)
                    reasoning = delta.get("reasoning_content")
                    if isinstance(reasoning, str) and reasoning:
                        progress = time.monotonic()  # 思考中也算有进展，不显示、不进回复
                    if self._collect_calls(delta.get("tool_calls"), calls):
                        progress = time.monotonic()
                    if choice.get("finish_reason"):
                        finished = True
        except (OSError, http.client.HTTPException) as exc:
            if not finished:  # 模型说完后才断开不算失败
                words = _TIMED_OUT if _timed_out(exc) else "回复中断了，已收到的内容保留着。"
                raise ModelError(words, "".join(text)) from None
        if not finished:
            raise ModelError("回复中断了，已收到的内容保留着。", "".join(text))
        return {"text": "".join(text), "tool_calls": [calls[i] for i in sorted(calls)], "tokens": tokens}

    @staticmethod
    def _collect_calls(pieces, calls: dict) -> bool:
        """把分段的工具调用拼起来，格式不对的字段跳过。返回这次有没有收到内容。"""
        got = False
        for call in pieces if isinstance(pieces, list) else []:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict):
                continue
            index = call.get("index")
            slot = calls.setdefault(index if isinstance(index, int) else 0, {"id": "", "name": "", "arguments": ""})
            if isinstance(call.get("id"), str) and call["id"]:
                slot["id"] = call["id"]
            if isinstance(function.get("name"), str) and function["name"]:
                slot["name"] = function["name"]
            if isinstance(function.get("arguments"), str):
                slot["arguments"] += function["arguments"]
            got = True
        return got
