"""测试用的“假模型”：按 OpenAI 兼容接口回话，不联网、不花额度。

按用户最后一句话决定怎么回：含“额度”→429；含“崩了”→服务器错误；含“半截”→回一半就断；
含“工具”→调用 get_cards；含“慢”→迟迟不回；含“跳转”→让客户端跳到别的地址；含“流错”→回半句后报错；
含“假成功”→状态 200 但内容是错误；含“乱码”→夹杂异常内容；含“尾断”→说完但不发结束标记；
含“心跳”→只发心跳不发内容；其他→逐字回“好的，收到：<原话>”。
密钥不是 good-key 时回 401。
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _fail(self, code, message):
        body = json.dumps({"error": {"message": message}}, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _chunk(self, delta, finish=None, usage=None):
        data = {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage:
            data["usage"] = usage
        self.wfile.write(("data: " + json.dumps(data, ensure_ascii=False) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        if self.path != "/v1/chat/completions":
            self.server.leaked.append(self.headers.get("Authorization"))
            return self._fail(404, "not found")
        if self.headers.get("Authorization") != "Bearer good-key":
            return self._fail(401, "invalid api key")
        text = body["messages"][-1]["content"]
        if "额度" in text:
            return self._fail(429, "quota exceeded")
        if "崩了" in text:
            return self._fail(500, "boom")
        if "慢" in text:
            time.sleep(self.server.slow_seconds)
        if "跳转" in text:
            self.send_response(302)
            self.send_header("Location", "/other")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        if "假成功" in text:
            body = json.dumps({"error": {"message": "bad"}}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if "流错" in text:
            self._chunk({"content": "半句"})
            self.wfile.write(b'data: {"error": {"message": "overloaded"}}\n\ndata: [DONE]\n\n')
            self.wfile.flush()
            return None
        if "乱码" in text:
            self.wfile.write(b'data: 1\n\ndata: null\n\ndata: {"choices": "x"}\n\ndata: {"choices": [3]}\n\n')
            self._chunk({"content": "正常"})
            self._chunk({}, finish="stop")
            self.wfile.write(b"data: [DONE]\n\n")
            return None
        if "尾心跳" in text or "空心跳" in text:
            self._chunk({"content": "开头"})
            if "尾心跳" in text:
                self._chunk({}, finish="stop")
            end = time.time() + self.server.slow_seconds
            while time.time() < end:
                self._chunk({}) if "空心跳" in text else self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                time.sleep(0.05)
            return None
        if "工具乱" in text:
            self._chunk({"content": "前半"})
            self._chunk({"tool_calls": [{"index": None, "function": {"name": "a", "arguments": {"x": 1}}},
                                        {"index": 1, "function": {"name": "b", "arguments": "{}"}},
                                        {"index": {}, "function": {"name": "c"}}]})
            self._chunk({}, finish="stop")
            self.wfile.write(b"data: [DONE]\n\n")
            return None
        if "心跳" in text:
            self._chunk({"content": "开头"})
            end = time.time() + self.server.slow_seconds
            while time.time() < end:
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                time.sleep(0.05)
            return None
        if "尾断" in text:
            self._chunk({"tool_calls": [{"index": 0, "id": "c9", "type": "function",
                                         "function": {"name": "get_cards", "arguments": "{}"}}]})
            self._chunk({"content": "说完了"})
            self._chunk({}, finish="stop")
            return None
        if "工具" in text:
            self._chunk({"tool_calls": [{"index": 0, "id": "c1", "type": "function",
                                         "function": {"name": "get_cards", "arguments": ""}}]})
            self._chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"a"'}}]})
            self._chunk({"tool_calls": [{"index": 0, "function": {"arguments": ":1}"}}]})
            self._chunk({}, finish="tool_calls", usage={"total_tokens": 30})
        else:
            words = "好的，收到：" + text
            cut = len(words) // 2 if "半截" in text else len(words)
            for char in words[:cut]:
                self._chunk({"content": char})
            if "半截" in text:
                return
            self._chunk({}, finish="stop", usage={"total_tokens": 20})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class FakeModel:
    def __init__(self, slow_seconds=3):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.requests = []
        self.server.leaked = []
        self.server.slow_seconds = slow_seconds
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def requests(self):
        return self.server.requests

    @property
    def leaked(self):
        return self.server.leaked

    def close(self):
        self.server.shutdown()
        self.server.server_close()
