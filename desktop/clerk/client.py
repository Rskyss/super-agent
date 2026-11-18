"""和模型说话：兼容 OpenAI 的接口，回复逐字返回；出错时给用户看得懂的话，绝不带上密钥。"""
from __future__ import annotations

import http.client
import json
import re
import socket
import threading
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
_DEFAULT_OPENER = _OPENER
_KEY_SHAPE = re.compile(r"[\x21-\x7e]+")
_ADDRESS_SHAPE = re.compile(r"https?://[^\s/]+\S*")
_PROVIDER_ERROR = "模型服务商返回了错误，请稍后再试。"
_TIMED_OUT = "模型回复超时了，已收到的内容保留着。"


def _should_abort(deadline: float | None, cancel_event: threading.Event | None) -> bool:
    if cancel_event is not None and cancel_event.is_set():
        return True
    if deadline is not None and time.monotonic() >= deadline:
        return True
    return False


def _extract_response_socket(response):
    raw_fp = getattr(response, "fp", None)
    raw_io = getattr(raw_fp, "raw", None)
    return getattr(raw_io, "_sock", None)


def _track_create_connection(conn, guard) -> None:
    """在原始 TCP 连接建立完成的第一时间把 socket 登记到取消守护，覆盖后续代理 CONNECT 隧道与 TLS 握手。"""
    orig_create_connection = conn._create_connection

    def _tracked_create_connection(*cargs, **ckwargs):
        sock = orig_create_connection(*cargs, **ckwargs)
        guard.attach_connection(conn, sock)
        return sock

    conn._create_connection = _tracked_create_connection


class _TrackedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, guard):
        super().__init__()
        self._guard = guard

    def http_open(self, req):
        guard = self._guard

        class _TrackedHTTPConnection(http.client.HTTPConnection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                _track_create_connection(self, guard)

        return self.do_open(_TrackedHTTPConnection, req)


class _TrackedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, guard):
        super().__init__()
        self._guard = guard

    def https_open(self, req):
        guard = self._guard

        class _TrackedHTTPSConnection(http.client.HTTPSConnection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                _track_create_connection(self, guard)

            def connect(self):
                http.client.HTTPConnection.connect(self)
                if guard.is_aborted():
                    raise TimeoutError("timed out")
                server_hostname = self._tunnel_host if self._tunnel_host else self.host
                # 先用非阻塞握手完成 wrap_socket（其内部会 detach 原 sock），更新登记后再执行握手，避免 detach 后旧引用失效。
                self.sock = self._context.wrap_socket(
                    self.sock,
                    server_hostname=server_hostname,
                    do_handshake_on_connect=False,
                )
                guard.attach_connection(self, self.sock)
                if guard.is_aborted():
                    raise TimeoutError("timed out")
                self.sock.do_handshake()

        # Python 3.12 起去掉了 HTTPSHandler._check_hostname 和 HTTPSConnection 的 check_hostname 参数；
        # 是否校验主机名由 context 决定（默认开启，3.9 和 3.15 都实测过）
        return self.do_open(_TrackedHTTPSConnection, req, context=self._context)


class _RequestCancelGuard:
    """在单次 deadline 到期或 cancel_event 触发时仅通过 shutdown 唤醒底层阻塞读；
    HTTPResponse / HTTPError / HTTPConnection 的 close() 永远由工作线程单一持有并关闭。"""

    def __init__(self, deadline: float | None, cancel_event: threading.Event | None):
        self.deadline = deadline
        self.cancel_event = cancel_event
        self.finished_event = threading.Event()
        self.triggered_event = threading.Event()
        self.close_lock = threading.Lock()
        self.closed = False
        self.conn = None
        self.sock = None

    def is_aborted(self) -> bool:
        return self.triggered_event.is_set() or _should_abort(self.deadline, self.cancel_event)

    def start(self) -> None:
        if self.deadline is None and self.cancel_event is None:
            return
        threading.Thread(target=self._watch, daemon=True).start()

    def attach_connection(self, conn, sock) -> None:
        with self.close_lock:
            if self.closed:
                return
            self.conn = conn
            if sock is not None:
                self.sock = sock
        if self.is_aborted():
            self.triggered_event.set()
            self._shutdown_socket()

    def attach_response(self, response) -> None:
        sock = _extract_response_socket(response)
        if sock is None:
            return
        with self.close_lock:
            if self.closed:
                return
            if self.sock is None:
                self.sock = sock
        if self.is_aborted():
            self.triggered_event.set()
            self._shutdown_socket()

    def finish(self, resource=None) -> None:
        with self.close_lock:
            self.closed = True
            conn = self.conn
            self.conn = None
            self.sock = None
        self.finished_event.set()
        if resource is not None:
            try:
                resource.close()
            except Exception:  # noqa: BLE001
                pass
        elif conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _shutdown_socket(self) -> None:
        with self.close_lock:
            if self.closed or self.sock is None:
                return
            sock = self.sock
            try:
                if isinstance(sock, socket.socket):
                    socket.socket.shutdown(sock, socket.SHUT_RDWR)
                else:
                    sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def _watch(self) -> None:
        while not self.finished_event.is_set():
            if _should_abort(self.deadline, self.cancel_event):
                self.triggered_event.set()
                self._shutdown_socket()
                return
            if self.deadline is not None:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    self.triggered_event.set()
                    self._shutdown_socket()
                    return
                wait_for = min(remaining, 0.05) if self.cancel_event is not None else remaining
            else:
                wait_for = 0.05
            if self.cancel_event is not None:
                if self.cancel_event.wait(wait_for):
                    if not self.finished_event.is_set():
                        self.triggered_event.set()
                        self._shutdown_socket()
                    return
            else:
                if self.finished_event.wait(wait_for):
                    return


class ModelClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, model: str = DEFAULT_MODEL, api_key: str = "",
                 timeout: float = 60, thinking_off: bool = True):
        self.base_url = base_url.strip().rstrip("/")
        self.model = model
        self.api_key = (api_key or "").strip()
        self.timeout = timeout
        self.thinking_off = thinking_off

    def chat(
        self,
        messages: list,
        on_text=None,
        tools: list = None,
        *,
        deadline: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        """发一轮对话。on_text 每收到一段文字调用一次。返回 text、tool_calls、tokens。"""
        if not self.api_key:
            raise ModelError("还没有填模型密钥，请先到设置里填写。")
        if not _KEY_SHAPE.fullmatch(self.api_key):
            raise ModelError("模型密钥格式不对，请重新复制后填写。")
        if not _ADDRESS_SHAPE.fullmatch(self.base_url):
            raise ModelError("模型服务地址不对，要以 https:// 开头。")
        if _should_abort(deadline, cancel_event):
            raise ModelError("模型回复超时了，请再试一次。")
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
        except ValueError:
            raise ModelError("模型服务地址不对，要以 https:// 开头。") from None

        if deadline is None and cancel_event is None:
            try:
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

        open_timeout = self.timeout
        if deadline is not None:
            open_timeout = min(self.timeout, max(0.001, deadline - time.monotonic()))
        guard = _RequestCancelGuard(deadline, cancel_event)
        guard.start()
        opener = (
            urllib.request.build_opener(
                _TrackedHTTPHandler(guard),
                _TrackedHTTPSHandler(guard),
                _NoRedirect,
            )
            if _OPENER is _DEFAULT_OPENER
            else _OPENER
        )
        try:
            response = opener.open(request, timeout=open_timeout)
        except ValueError:
            guard.finish()
            raise ModelError("模型服务地址不对，要以 https:// 开头。") from None
        except urllib.error.HTTPError as exc:
            try:
                guard.attach_response(exc)
                if guard.is_aborted():
                    raise ModelError("模型回复超时了，请再试一次。") from None
                failure = _http_failure(exc.code)
                failure.code = exc.code
                failure.detail = _platform_words(exc)
                if guard.is_aborted():
                    raise ModelError("模型回复超时了，请再试一次。") from None
                raise failure from None
            finally:
                guard.finish(exc)
        except urllib.error.URLError as exc:
            timed_out = _timed_out(exc.reason) or guard.is_aborted()
            guard.finish()
            if timed_out:
                raise ModelError("模型回复超时了，请再试一次。") from None
            raise ModelError("连不上模型服务，请检查网络。") from None
        except http.client.HTTPException:
            timed_out = guard.is_aborted()
            guard.finish()
            if timed_out:
                raise ModelError("模型回复超时了，请再试一次。") from None
            raise ModelError("模型服务返回了看不懂的内容，请检查网络或代理。") from None
        except OSError as exc:
            timed_out = _timed_out(exc) or guard.is_aborted()
            guard.finish()
            if timed_out:
                raise ModelError("模型回复超时了，请再试一次。") from None
            raise ModelError("连不上模型服务，请检查网络。") from None

        guard.attach_response(response)
        try:
            return self._read(response, on_text, deadline=deadline, cancel_event=cancel_event)
        except (OSError, ValueError, http.client.HTTPException):
            if guard.is_aborted():
                raise ModelError(_TIMED_OUT) from None
            raise
        finally:
            guard.finish(response)

    def _read(
        self,
        response,
        on_text,
        *,
        deadline: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        text, calls, tokens, finished = [], {}, None, False
        progress = time.monotonic()  # 只有收到文字或工具调用才算进展，心跳和空片段不算
        try:
            for raw in response:
                if _should_abort(deadline, cancel_event):
                    if finished and not (cancel_event is not None and cancel_event.is_set()):
                        break
                    raise ModelError(_TIMED_OUT, "".join(text))
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
                    if _should_abort(deadline, cancel_event):
                        raise ModelError(_TIMED_OUT, "".join(text))
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
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if not finished:  # 模型说完后才断开不算失败
                words = _TIMED_OUT if (_timed_out(exc) or _should_abort(deadline, cancel_event)) else "回复中断了，已收到的内容保留着。"
                raise ModelError(words, "".join(text)) from None
        if not finished:
            words = _TIMED_OUT if _should_abort(deadline, cancel_event) else "回复中断了，已收到的内容保留着。"
            raise ModelError(words, "".join(text))
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
