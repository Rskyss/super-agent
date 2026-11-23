"""agy 的 ACP 翻译（docs/agy改走ACP/，11-22 用户定）。

为什么有它：Google 说个人订阅下把 `agy -p` 包进第三方程序自动调用“不支持”、可封号；官方给第三方程序开的门是
`agy acp`（Agent Client Protocol，T3 Code 走的就是这条）。派活改成拉起本文件，本文件拉起 `agy acp`，用 ACP 跟它说话，
再把它的回话翻成和 `agy -p --output-format stream-json` 一样的格式印出来——后面读回复（readable）、读报错（agy_error）、
看门人认命令（_last_command）、读用量（token_usage）全都照旧，不用改。

口径：权限一律自动批（1B，和原来包装壳一样）；出错不退回 `agy -p`（2A），直接印失败结果。
只保证后面真在读的字段一致（event/step_update/result 的那几个键）；步骤编号等细节和原来不完全一样。

用法：python3 agy_acp.py --model <模型编号> <交代>   （在副本目录里运行）
"""
import argparse
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time

AGY = "agy"
PROTOCOL_VERSION = 1


def emit(obj) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class AgyGone(Exception):
    """agy 自己退出了（崩了、被停了）。"""


class Bridge:
    def __init__(self, model: str, cwd: str):
        self.model = model
        self.cwd = cwd
        self.sid = ""
        self.next_id = 0
        self.inbox = queue.Queue()
        self.stderr_tail = []
        self.steps = {}          # toolCallId → 步骤编号
        self.step_no = 0
        self.text = []           # agy 说的话（agent_message_chunk），按先后
        self.started = time.time()
        self.proc = subprocess.Popen([AGY, "acp", "--dangerously-skip-permissions"], cwd=cwd,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1)
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    # ---- 收发 ----
    def _read_out(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self._note_err(line)  # 不是协议的话（日志之类）：留着出错时当原因
                continue
            if isinstance(msg, dict):
                self.inbox.put(msg)
        self.inbox.put(None)

    def _read_err(self):
        for line in self.proc.stderr:
            self._note_err(line.strip())

    def _note_err(self, line):
        if line:
            self.stderr_tail = (self.stderr_tail + [line])[-20:]

    def _send(self, obj):
        try:
            self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            raise AgyGone()

    def call(self, method, params):
        """发一个请求，等它的回答；等的时候 agy 吐的过程、来问的事一并处理。返回 (result, error)。"""
        self.next_id += 1
        mid = self.next_id
        self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        while True:
            try:
                msg = self.inbox.get(timeout=0.5)
            except queue.Empty:
                # agy 退出了、但它拉起的子进程还拿着输出管道时读不到结尾：看它自己退没退（复查第 1 轮）
                if self.proc.poll() is not None and self.inbox.empty():
                    time.sleep(0.2)   # 退出前印的最后几行还在路上
                    if self.inbox.empty():
                        raise AgyGone()
                continue
            if msg is None:
                raise AgyGone()
            if msg.get("id") == mid and "method" not in msg:
                return msg.get("result"), msg.get("error")
            self._handle(msg)

    def _handle(self, msg):
        method = msg.get("method")
        if method == "session/update":
            self._on_update((msg.get("params") or {}).get("update") or {})
        elif method == "session/request_permission" and "id" in msg:
            options = (msg.get("params") or {}).get("options") or []
            pick = (next((o for o in options if o.get("kind") == "allow_always"), None)
                    or next((o for o in options if str(o.get("kind", "")).startswith("allow")), None))
            if pick:   # 1B：一律批
                outcome = {"outcome": "selected", "optionId": pick.get("optionId")}
            else:
                outcome = {"outcome": "cancelled"}
            self._send({"jsonrpc": "2.0", "id": msg["id"], "result": {"outcome": outcome}})
        elif method and "id" in msg:   # 我们没开的本事（读写文件、开终端）：明说不支持，免得它干等
            self._send({"jsonrpc": "2.0", "id": msg["id"],
                        "error": {"code": -32601, "message": f"client does not support {method}"}})

    # ---- 翻成 agy -p 的格式 ----
    def _step(self, cid, state, upd):
        if cid not in self.steps:
            self.step_no += 1
            self.steps[cid] = self.step_no
        name = upd.get("name") or upd.get("kind") or "tool"
        params = upd.get("args") or upd.get("rawInput") or {}
        if name != "run_command" and upd.get("kind") == "execute":
            name = "run_command"
        if name == "run_command" and "CommandLine" not in params:
            params = dict(params, CommandLine=params.get("command") or upd.get("title") or "")
        emit({"event": "step_update", "step_update": {
            "conversation_id": self.sid, "step_index": self.steps[cid], "state": state, "step_type": "tool",
            "tool_name": name, "tool_info": {"name": name, "parameters": params}}})

    def _on_update(self, upd):
        kind = upd.get("sessionUpdate")
        if kind == "tool_call":
            self._step(upd.get("toolCallId"), "ACTIVE", upd)
        elif kind == "tool_call_update":
            status = upd.get("status")
            if status in ("completed", "failed"):
                self._step(upd.get("toolCallId"), "DONE", upd)
        elif kind in ("agent_message_chunk", "agent_thought_chunk"):
            text = ((upd.get("content") or {}).get("text")) or ""
            if kind == "agent_message_chunk":
                self.text.append(text)
            emit({"event": "step_update", "step_update": {
                "conversation_id": self.sid, "step_index": self.step_no + 1, "state": "ACTIVE",
                "step_type": "agent_response" if kind == "agent_message_chunk" else "thought",
                "text_delta": text}})

    def reply(self):
        """回复原文 = agy 这一轮说的全部话，原样连起来。和原来 `agy -p` 的 response 一模一样：11-22 对了 CARD-022、023
        的 5 份真实记录，response 都等于全部 text_delta 连起来，不挑段。复查第 1、2 轮试过“只取最后一段 / 从结论那段取”，
        都会在结论被跑命令切开或引用里出现“结论”时取错，所以不挑。"""
        return "".join(self.text).strip()

    # ---- 一次活 ----
    def result(self, status, response="", error="", usage=None):
        out = {"conversation_id": self.sid, "status": status, "response": response,
               "duration_seconds": round(time.time() - self.started, 3), "num_turns": 1}
        if isinstance(usage, dict):   # 没拿到用量就不写：读的人算“读不到”，不能记成 0（复查第 1 轮）
            keys = {"input_tokens": "inputTokens", "output_tokens": "outputTokens", "thinking_tokens": "thoughtTokens",
                    "cache_read_tokens": "cachedReadTokens", "total_tokens": "totalTokens"}
            got = {mine: usage[theirs] for mine, theirs in keys.items()
                   if isinstance(usage.get(theirs), (int, float)) and not isinstance(usage.get(theirs), bool)}
            if got:
                out["usage"] = got
        if error:
            out["error"] = error
        emit({"event": "result", "result": out})

    def run(self, prompt) -> int:
        _res, err = self.call("initialize", {"protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {
            "fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False}})
        if err:
            return self.fail(f"agy 不接受连接：{_msg(err)}")
        res, err = self.call("session/new", {"cwd": self.cwd, "mcpServers": []})
        if err or not isinstance(res, dict) or not res.get("sessionId"):
            return self.fail(f"agy 开不了会话：{_msg(err) or '没给会话编号'}")
        self.sid = res["sessionId"]
        emit({"event": "init", "conversation_id": self.sid,
              "init": {"model": self.model, "cwd": self.cwd, "permission_mode": "always-proceed", "via": "acp"}})
        if self.model:
            _res, err = self.call("session/set_model", {"sessionId": self.sid, "modelId": self.model})
            if err:   # 设不上就不干活，不悄悄用默认模型
                return self.fail(f"invalid model selection (--model \"{self.model}\"): {_msg(err)}")
        res, err = self.call("session/prompt", {"sessionId": self.sid,
                                                "prompt": [{"type": "text", "text": prompt}]})
        if err:
            return self.fail(f"agy 干活时报错：{_msg(err)}")
        res = res if isinstance(res, dict) else {}
        stop = res.get("stopReason")
        if stop != "end_turn":   # 被取消、到了上限、拒答：宁可算没完成
            self.result("ERROR", error=f"agy 没正常做完（stopReason={stop}）", usage=res.get("usage"))
            return 1
        self.result("SUCCESS", response=self.reply(), usage=res.get("usage"))
        return 0

    def fail(self, error) -> int:
        self.result("ERROR", error=error)
        return 1

    def gone(self) -> int:
        code = self.proc.poll()
        tail = " / ".join(self.stderr_tail[-3:])
        why = f"agy 意外退出（退出码 {code}）" if code is not None else "agy 断开了连接"
        return self.fail(why + (f"：{tail}" if tail else ""))

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _msg(err):
    """JSON-RPC 报错的原文：message 加上 data（agy 常把真原因放 data 里，只留 message 会只剩 Internal error，复查第 1 轮）。"""
    if not isinstance(err, dict):
        return str(err or "")
    text = str(err.get("message") or "")
    data = err.get("data")
    if data not in (None, "", {}, []):
        extra = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        text = f"{text}：{extra}" if text else extra
    return text or json.dumps(err, ensure_ascii=False)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="用 ACP 叫 agy 干一次活，印出 agy -p stream-json 同格式")
    ap.add_argument("--model", default="")
    ap.add_argument("prompt")
    args = ap.parse_args(argv)
    try:
        bridge = Bridge(args.model, os.getcwd())
    except OSError as exc:
        emit({"event": "result", "result": {"conversation_id": "", "status": "ERROR", "response": "",
                                            "error": f"拉不起 agy acp：{exc}", "usage": {}}})
        return 1

    def on_term(_sig, _frame):   # 被停工：把 agy 一起带走（整组停时它也会收到，这里是兜底）
        # 不碰管道（正写着时再关会出重入错，复查第 1 轮）：直接杀掉 agy、等它收尸，然后立刻退出
        try:
            bridge.proc.kill()
            bridge.proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass
        os._exit(143)

    signal.signal(signal.SIGTERM, on_term)
    try:
        return bridge.run(args.prompt)
    except AgyGone:
        return bridge.gone()
    finally:
        bridge.close()


if __name__ == "__main__":
    sys.exit(main())
