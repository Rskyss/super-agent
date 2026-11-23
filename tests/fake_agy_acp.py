#!/usr/bin/env python3
"""假 agy：只会 `agy acp`，按环境变量 FAKE_ACP 选剧本，用 ACP（一行一个 JSON-RPC）跟翻译说话。
FAKE_ACP_LOG：把收到的每条消息记下来（测试看翻译发了什么）；FAKE_ACP_PIDFILE：记下自己的进程号；
FAKE_ACP_GATE：剧本 slow 里等这个文件出现才交活（测试“边干边吐”）。

另一种用法（老测试的假 agy，见 tests/fake_agy.py）：`fake_agy_acp.py --inner <脚本> acp ...`——收到活时照老办法跑那个
脚本（参数和原来 `agy -p <交代> --model <模型> --output-format stream-json` 一样），它印的话当回复；它退出码不是 0
就按 agy 报错回给翻译（报错原文 = 它印的话）。这样老测试的假 agy 照旧表演，只是外面多了一层 ACP。"""
import json
import os
import subprocess
import sys
import time

INNER = None

SCENE = os.environ.get("FAKE_ACP", "ok")
LOG = os.environ.get("FAKE_ACP_LOG")
MODELS = ("gemini-4-neon-high", "gemini-3.8-flash-low")
USAGE = {"inputTokens": 1000, "outputTokens": 50, "cachedReadTokens": 3000, "cachedWriteTokens": 0,
         "thoughtTokens": 20, "totalTokens": 4050}


def log(obj):
    if LOG:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    msg = json.loads(line)
    log(msg)
    return msg


def update(sid, upd):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": upd}})


def tool(sid, cid, name, args, kind):
    update(sid, {"sessionUpdate": "tool_call", "toolCallId": cid, "name": name, "args": args,
                 "status": "in_progress", "kind": kind, "title": name, "rawInput": args})
    update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": cid, "name": name, "args": args,
                 "status": "completed", "kind": kind, "title": name, "rawInput": args})


def say(sid, text):
    update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})


def main():
    global INNER
    if sys.argv[1:2] == ["--inner"]:
        INNER = sys.argv[2]
        del sys.argv[1:3]
    if sys.argv[1:2] != ["acp"]:
        print("假 agy 只会 acp", file=sys.stderr)
        sys.exit(2)
    if os.environ.get("FAKE_ACP_PIDFILE"):
        with open(os.environ["FAKE_ACP_PIDFILE"], "w") as f:
            f.write(str(os.getpid()))
    sid = "fake-session"
    state = {"model": "", "cwd": os.getcwd()}
    while True:
        msg = read()
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": 1, "agentCapabilities": {}}})
        elif method == "session/new":
            state["cwd"] = params.get("cwd") or state["cwd"]
            send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": sid, "models": {
                "availableModels": [{"modelId": m, "name": m} for m in MODELS], "currentModelId": MODELS[0]}}})
        elif method == "session/set_model":
            state["model"] = params.get("modelId") or ""
            if INNER:
                send({"jsonrpc": "2.0", "id": mid, "result": {}})
            elif params.get("modelId") not in MODELS:
                send({"jsonrpc": "2.0", "id": mid,
                      "error": {"code": -32602, "message": f"Unknown model ID: {params.get('modelId')}"}})
            else:
                send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "session/prompt":
            if INNER:
                inner(mid, sid, params, state)
            else:
                prompt(mid, sid, params)
        elif mid is not None and method is None:
            pass  # 翻译对我们请求的回答，已记进日志
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"no {method}"}})


def inner(mid, sid, params, state):
    text = "".join(p.get("text", "") for p in params.get("prompt", []))
    args = [INNER, "-p", text, "--model", state["model"], "--output-format", "stream-json"]
    p = subprocess.run(args, cwd=state["cwd"], capture_output=True, text=True)
    said = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": said.strip()}})
        return
    reply = said.strip()
    for line in reversed(said.splitlines()):   # 老假 agy 有的自己印 agy -p 的结果行：取里面的回复
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("event") == "result":
            reply = ((ev.get("result") or {}).get("response")) or ""
            break
    if reply:
        say(sid, reply)
    send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn", "usage": USAGE}})


def prompt(mid, sid, params):
    text = "".join(p.get("text", "") for p in params.get("prompt", []))
    update(sid, {"sessionUpdate": "usage_update", "used": 0, "size": 1048576})
    update(sid, {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "想一想"}})
    if SCENE == "split":   # 复查第 1 轮：结论和清单之间夹了一次跑命令
        say(sid, "我先看看。")
        tool(sid, "s0", "read_file", {"Path": "a.py"}, "read")
        say(sid, "结论：不通过\n")
        tool(sid, "s1", "run_command", {"CommandLine": "python3 -m unittest"}, "execute")
        say(sid, "1. 【中等】保存后数据丢了")
        send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn", "usage": USAGE}})
        return
    if SCENE == "quota":   # 复查第 1 轮：原因放在 error.data 里
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": "Internal error",
                                                      "data": {"details": "Your AI credits balance is too low"}}})
        return
    if SCENE == "orphan":   # 复查第 1 轮：agy 退出了，它拉起的子进程还拿着输出管道
        subprocess.Popen(["sleep", "30"])
        sys.stdout.flush()
        os._exit(7)
    if SCENE == "crash":
        tool(sid, "c1", "read_file", {"Path": "a.py"}, "read")
        sys.stdout.flush()
        os._exit(3)
    if SCENE == "permission":
        send({"jsonrpc": "2.0", "id": 900, "method": "session/request_permission", "params": {
            "sessionId": sid, "toolCall": {"toolCallId": "w1", "name": "write_file"},
            "options": [{"optionId": "no", "name": "Reject", "kind": "reject_once"},
                        {"optionId": "once", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "always", "name": "Always", "kind": "allow_always"}]}})
        answer = read()
        picked = ((answer.get("result") or {}).get("outcome") or {}).get("optionId")
        if answer.get("id") != 900 or picked != "always":
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": f"permission answer {picked}"}})
            return
    if SCENE == "fs_request":
        send({"jsonrpc": "2.0", "id": 901, "method": "fs/read_text_file", "params": {"sessionId": sid, "path": "x"}})
        answer = read()
        if answer.get("id") != 901 or "error" not in answer:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": "fs not refused"}})
            return
    if SCENE == "slow":
        tool(sid, "r1", "read_file", {"Path": "big.py"}, "read")
        gate = os.environ["FAKE_ACP_GATE"]
        deadline = time.time() + 20
        while not os.path.exists(gate) and time.time() < deadline:
            time.sleep(0.05)
    if SCENE == "hang":
        tool(sid, "h1", "run_command", {"CommandLine": "sleep 999"}, "execute")
        time.sleep(999)
    say(sid, "我先跑一下测试。")
    tool(sid, "t1", "run_command", {"CommandLine": "python3 -m unittest tests.test_x"}, "execute")
    say(sid, "测试")
    say(sid, "全过了：" + text[:10])
    stop = "cancelled" if SCENE == "cancelled" else "end_turn"
    send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": stop, "usage": USAGE}})


if __name__ == "__main__":
    main()
