"""测试用的“假 Codex”：按 codex app-server 的协议回话，不联网、不花额度。

环境变量：
  FAKE_CODEX_DB     存对话的文件（让“接着上次的对话”能测）
  FAKE_CODEX_IMAGE  “画图”时报出的图片路径
按用户消息里的字决定怎么回：含“画”→画图；含“运行”→先问允许吗；含“数”→一直数到被停止；含“读”→读文件；
含“崩”→直接退出（测连接断了）；其他→简单回一句。
"""
import atexit
import json
import os
import signal
import sys
import threading
import time

DB = os.environ.get("FAKE_CODEX_DB", "/tmp/fake_codex_db.json")
IMAGE = os.environ.get("FAKE_CODEX_IMAGE", "/tmp/fake.png")
lock = threading.Lock()
answers = {}
interrupted = threading.Event()
held_threads = set()


def _pid_alive(pid) -> bool:
    if not pid or pid == os.getpid():
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _release_held():
    if not held_threads:
        return
    try:
        db = load()
        changed = False
        for tid in list(held_threads):
            t = db.get("threads", {}).get(tid)
            if t and t.get("writer_pid") == os.getpid():
                t["writer_pid"] = None
                changed = True
        if changed:
            save(db)
    except OSError:
        pass


atexit.register(_release_held)
signal.signal(signal.SIGTERM, lambda *_: (_release_held(), sys.exit(0)))


def load():
    try:
        with open(DB, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"threads": {}}


def save(db):
    with open(DB, "w", encoding="utf-8") as fh:
        json.dump(db, fh, ensure_ascii=False)


def out(obj):
    with lock:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def note(method, params):
    out({"method": method, "params": params})


def ask(rid, method, params):
    out({"id": rid, "method": method, "params": params})
    while rid not in answers:
        time.sleep(0.01)
    return answers.pop(rid)


def run_turn(thread_id, turn_id, text):
    items = [{"type": "userMessage", "id": f"{turn_id}-u", "content": [{"type": "text", "text": text}]}]
    note("turn/started", {"threadId": thread_id, "turn": {"id": turn_id, "status": "inProgress", "items": []}})
    reply, status = "", "completed"
    if "崩" in text:
        os._exit(1)
    if text.startswith("【超级助手自动交代】"):
        reply = "收到交代，已读完交接：目前没有进行中的卡片。"
    elif "画" in text:
        note("item/started", {"threadId": thread_id, "turnId": turn_id, "startedAtMs": 0,
                              "item": {"type": "imageGeneration", "id": "img1", "status": "inProgress"}})
        image = {"type": "imageGeneration", "id": "img1", "status": "completed", "savedPath": IMAGE,
                 "revisedPrompt": "一只橘猫坐在弯月上"}
        note("item/completed", {"threadId": thread_id, "turnId": turn_id, "completedAtMs": 0, "item": image})
        items.append(image)
        reply = "画好了。"
    elif "运行" in text:
        cmd = {"type": "commandExecution", "id": "c1", "command": "/bin/zsh -lc 'touch a.txt'", "status": "inProgress",
               "commandActions": [{"type": "unknown", "command": "touch a.txt"}]}
        note("item/started", {"threadId": thread_id, "turnId": turn_id, "startedAtMs": 0, "item": cmd})
        decision = ask(900, "item/commandExecution/requestApproval",
                       {"threadId": thread_id, "turnId": turn_id, "itemId": "c1", "startedAtMs": 0,
                        "command": "/bin/zsh -lc 'touch a.txt'", "reason": "需要新建文件"})["decision"]
        done = dict(cmd, status="completed" if decision in ("accept", "acceptForSession") else "declined")
        note("item/completed", {"threadId": thread_id, "turnId": turn_id, "completedAtMs": 0, "item": done})
        items.append(done)
        reply = "建好了。" if done["status"] == "completed" else "被拒绝了，没有建。"
    elif "读" in text:
        cmd = {"type": "commandExecution", "id": "c2", "command": "/bin/zsh -lc 'cat README.md'", "status": "completed",
               "commandActions": [{"type": "read", "name": "README.md", "path": "README.md", "command": "cat README.md"}],
               "aggregatedOutput": "# 说明\n"}
        note("item/started", {"threadId": thread_id, "turnId": turn_id, "startedAtMs": 0, "item": dict(cmd, status="inProgress")})
        note("item/completed", {"threadId": thread_id, "turnId": turn_id, "completedAtMs": 0, "item": cmd})
        items.append(cmd)
        reply = "读完了。"
    elif "数" in text:
        for i in range(1, 1000):
            if interrupted.is_set():
                status = "interrupted"
                break
            piece = f"{i}\n"
            reply += piece
            note("item/agentMessage/delta", {"threadId": thread_id, "turnId": turn_id, "itemId": "m-count", "delta": piece})
            time.sleep(0.02)
        interrupted.clear()
    else:
        reply = "好的，收到：" + text[:20]
    if reply and "数" not in text:
        for piece in (reply[:2], reply[2:]):
            if piece:
                note("item/agentMessage/delta", {"threadId": thread_id, "turnId": turn_id, "itemId": f"{turn_id}-m", "delta": piece})
    message = {"type": "agentMessage", "id": f"{turn_id}-m", "text": reply}
    if status == "completed":
        note("item/completed", {"threadId": thread_id, "turnId": turn_id, "completedAtMs": 0, "item": message})
    items.append(message)
    db = load()
    db["threads"][thread_id]["turns"].append({"id": turn_id, "status": status, "items": items})
    save(db)
    note("thread/tokenUsage/updated", {"threadId": thread_id, "turnId": turn_id,
                                      "tokenUsage": {"total": {"totalTokens": 1000 * len(db["threads"][thread_id]["turns"])}}})
    note("turn/completed", {"threadId": thread_id, "turn": {"id": turn_id, "status": status, "items": []}})


def thread_obj(tid, data, with_turns):
    preview = ""
    for turn in reversed(data.get("turns") or []):
        for it in reversed(turn.get("items") or []):
            if it.get("type") == "userMessage":
                txt = "".join(c.get("text", "") for c in it.get("content") or [])
                if txt and not txt.startswith("【超级助手自动交代】"):
                    preview = txt
                    break
        if preview:
            break
    return {"id": tid, "cwd": data["cwd"], "turns": data["turns"] if with_turns else [],
            "preview": preview, "updatedAt": int(data.get("updated") or time.time()), "status": "idle"}


def main():
    turn_no = 0
    for line in sys.stdin:
        msg = json.loads(line)
        if "method" not in msg:  # 我们问出去的问题（允许吗）得到了回答
            answers[msg["id"]] = msg.get("result") or {}
            continue
        method, params, rid = msg["method"], msg.get("params") or {}, msg.get("id")
        if rid is None:
            continue  # initialized 之类的通知
        db = load()
        if method == "initialize":
            out({"id": rid, "result": {"userAgent": "fake-codex"}})
        elif method == "thread/list":
            hits = [(tid, d) for tid, d in db["threads"].items() if d["cwd"] == params.get("cwd")]
            hits.sort(key=lambda x: x[1]["updated"], reverse=True)
            out({"id": rid, "result": {"data": [thread_obj(t, d, False) for t, d in hits[:params.get("limit") or 50]]}})
        elif method == "thread/start":
            tid = f"t{len(db['threads']) + 1}"
            db["threads"][tid] = {"cwd": params.get("cwd"), "turns": [], "updated": time.time(),
                                  "instructions": params.get("developerInstructions"),
                                  "reviewer": params.get("approvalsReviewer"),
                                  "writer_pid": os.getpid()}
            held_threads.add(tid)
            save(db)
            out({"id": rid, "result": {"thread": thread_obj(tid, db["threads"][tid], True)}})
        elif method == "thread/resume":
            tid = params["threadId"]
            t = db["threads"][tid]
            if _pid_alive(t.get("writer_pid")):
                out({"id": rid, "error": {"code": -32000, "message": f"thread {tid} already has an active writer"}})
            else:
                t["writer_pid"] = os.getpid()
                held_threads.add(tid)
                save(db)
                out({"id": rid, "result": {"thread": thread_obj(tid, t, True)}})
        elif method == "turn/start":
            turn_no += 1
            tid = params["threadId"]
            text = "".join(i.get("text", "") for i in params.get("input") or [])
            turn_id = f"{tid}-turn{turn_no}-{int(time.time() * 1000)}"
            db["threads"][tid]["updated"] = time.time()
            save(db)
            out({"id": rid, "result": {"turn": {"id": turn_id, "status": "inProgress", "items": []}}})
            threading.Thread(target=run_turn, args=(tid, turn_id, text), daemon=True).start()
        elif method == "turn/interrupt":
            interrupted.set()
            out({"id": rid, "result": {}})
        else:
            out({"id": rid, "error": {"code": -32601, "message": f"假 Codex 不支持 {method}"}})


if __name__ == "__main__":
    main()
