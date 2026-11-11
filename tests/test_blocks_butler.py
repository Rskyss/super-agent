"""积木库管家（docs/积木库/积木库管家/方案.md）：

1. 规矩拆成基本规矩 + 3 本手册，按需翻开；翻过的记进对话，之后每句都带着；没翻就用工具时附在结果后面。
2. 网上安装卡：官方、作者、风险；代码托管站一律算非官方；非官方不写风险不出卡。
3. 搜完必须说结果：搜索结果末尾附提醒。
4. 每一步存进对话记录（打码、截短），聊天里显示大白话，重开后还在。
5. 改名：积木库里叫“积木库管家”，项目窗口的行政不变。
"""
import json
import os
import re
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from acceptance_helpers import ScriptedClient, tool_call, wait_idle
from collab import blocks, blocks_web
from desktop.clerk import blocks_tools, tasks as clerk_tasks
from desktop.clerk.desk import ClerkDesk
from test_acceptance_54_blocks_clerk import BlocksCase
from test_acceptance_64_clerk_web_and_server import META, PAGE, PAGE_URL

ROOT = Path(__file__).resolve().parent.parent
KEY = "tvly-abcdefghijklmnopqrstuvwxyz0123456789"
GH_URL = "https://github.com/someone/demo-tool"


class ButlerCase(BlocksCase):
    def setUp(self):
        super().setUp()
        pages = {PAGE_URL: (200, "", PAGE), GH_URL: (200, "", PAGE)}
        self.searches = []

        def fake_open(url, timeout, limit):
            if url not in pages:
                raise OSError("连不上")
            return pages[url]

        def fake_search(query, key, max_results=5):
            self.searches.append(query)
            return [{"title": "Demo 安装", "url": PAGE_URL, "snippet": "npm install -g @demo/tool"}]

        for name, fn in {"_resolve": lambda h: ["93.184.216.34"], "_open": fake_open,
                         "_get_json": lambda url: (200, META), "search": fake_search}.items():
            p = mock.patch.object(blocks_web, name, side_effect=fn)
            p.start()
            self.addCleanup(p.stop)
        old = clerk_tasks.key_source
        clerk_tasks.key_source = lambda account: KEY if account == "tavily" else ""
        self.addCleanup(setattr, clerk_tasks, "key_source", old)
        blocks.save_card(blocks.search_card("tavily"), self.home)

    def system(self, index=0):
        return self.client.calls[index][0][0]["content"]

    def lines(self, cid):
        path = self.home / ".collab" / "行政对话" / f"{cid}.jsonl"
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def run_steps(self, steps, text="帮我办", cid=""):
        self.client.steps = list(steps)
        return self.talk(text, cid)


# ---------- 1. 手册 ----------

class Manuals(ButlerCase):
    def test_files(self):
        self.assertTrue(blocks_tools.ROLE_FILE.is_file())
        self.assertEqual(blocks_tools.ROLE_FILE.name, "积木库管家.md")
        for name in ("cli", "api", "web"):
            self.assertTrue((blocks_tools.MANUAL_DIR / f"{name}.md").is_file(), name)

    def test_base_prompt_has_directory_not_full_manuals(self):
        self.talk()
        system = self.system()
        self.assertIn("积木库管家", system)
        for title in ("接命令行工具", "接大模型 API", "联网搜索和网上安装"):
            self.assertIn(title, system)
        self.assertIn("read_manual", system)
        web = blocks_tools.manual_text("web")
        self.assertNotIn(web.strip()[-60:], system, "没翻开时不带手册全文")
        self.assertIn("read_manual", {t["function"]["name"] for t in blocks_tools.TOOLS})

    def test_read_manual_then_carried_and_survives_reopen(self):
        cid, _ = self.run_steps([{"tool_calls": [tool_call("read_manual", {"name": "web"})]}, {"text": "好"}])
        result = self.tool_results(1)[0]
        web = blocks_tools.manual_text("web")
        self.assertIn(web.strip()[:40], result)
        self.assertIn({"role": "manual", "name": "web"}, [{k: v for k, v in x.items() if k in ("role", "name")}
                                                         for x in self.lines(cid) if x["role"] == "manual"])
        self.run_steps([{"text": "嗯"}], text="接着说", cid=cid)
        self.assertIn(web.strip()[-60:], self.system(len(self.client.calls) - 1), "翻过的手册后面每句都带着")
        fresh = ClerkDesk(lambda: self.client)  # 重开超级助手
        self.client.steps = [{"text": "嗯"}]
        fresh.send(str(self.home), cid, "再说")
        wait_idle(fresh, str(self.home), cid)
        self.assertIn(web.strip()[-60:], self.system(len(self.client.calls) - 1), "重开后还记得翻过哪本")

    def test_unknown_manual(self):
        self.run_steps([{"tool_calls": [tool_call("read_manual", {"name": "../x"})]}, {"text": "好"}])
        self.assertIn("没有这本手册", self.tool_results(1)[0])

    def test_tool_without_manual_gets_it_attached_once(self):
        cid, _ = self.run_steps([{"tool_calls": [tool_call("web_search", {"query": "demo 安装"})]},
                                 {"tool_calls": [tool_call("web_search", {"query": "demo 官网"})]},
                                 {"text": "好"}])
        web = blocks_tools.manual_text("web").strip()
        first, second = self.tool_results(1)[0], self.tool_results(2)[-1]
        self.assertIn(web[:40], first, "第一次用这类工具：附上手册")
        self.assertNotIn(web[:40], second, "同一本只附一次")
        self.assertTrue(any(x["role"] == "manual" and x["name"] == "web" for x in self.lines(cid)))

    def test_propose_without_manual_still_works_and_attaches(self):
        """出了卡这一轮就结束（2026-11-10）：手册来不及附在回话里，但记成翻过，下一句话起带着"""
        cid, _ = self.run_steps([{"tool_calls": [tool_call("propose_connect_search", {})]}, {"text": "请看卡"}])
        self.assertTrue(self.card_replies[0].startswith("已向用户出示确认框"), self.card_replies)
        self.assertTrue(any(x["role"] == "manual" and x["name"] == "web" for x in self.lines(cid)))
        self.run_steps([{"text": "好"}], text="然后呢", cid=cid)
        self.assertIn(blocks_tools.manual_text("web").strip()[:40], self.system(-1))

    def test_scan_needs_no_manual(self):
        cid, _ = self.run_steps([{"tool_calls": [tool_call("list_blocks", {})]}, {"text": "好"}])
        self.assertFalse([x for x in self.lines(cid) if x["role"] == "manual"])

    def test_old_rules_still_somewhere(self):
        """原来规矩里的要点拆进手册后一条不能丢（54、64 号单和走查补的几句）。"""
        allt = blocks_tools.role_text() + "".join(blocks_tools.manual_text(n) for n in ("cli", "api", "web"))
        for word in ("体检", "核实名单", "密钥框", "propose_web_install", "propose_connect_search", "web_search",
                     "read_page", "probe", "secrets", "不要带用户去注册", "不是云账号的 SecretId、SecretKey",
                     "已经登录的，接进基础层时不用再登录", "编号相同才会替换", "卡上会写", "查不了",
                     "没有调用 propose_ 开头的工具，就不能说", "额度不足", "连续 3 次"):
            self.assertIn(word, allt, word)
        self.assertNotIn("Claude Code", allt)

    def test_new_rules(self):
        web = blocks_tools.manual_text("web")
        for word in ("官方没有", "最多 3 个", "谁做的", "风险", "推荐", "先告诉用户搜到了什么", "official", "maker", "risk"):
            self.assertIn(word, web, word)
        self.assertIn("不编造", blocks_tools.role_text())
        self.assertIn("产品里没有的设置", blocks_tools.role_text())

    def test_project_window_untouched(self):
        self.talk(where=self.project)
        system = self.system()
        self.assertNotIn("积木库管家", system)
        self.assertNotIn("read_manual", {t["function"]["name"] for t in self.client.calls[0][1]})


# ---------- 2. 非官方 ----------

class Unofficial(ButlerCase):
    def propose(self, **args):
        base = {"url": PAGE_URL, "install_command": "npm install -g @demo/tool"}
        base.update(args)
        return clerk_tasks.propose_web_install(self.conv(), base)

    def conv(self):
        cid = self.desk.send(str(self.home), "", "你好")
        wait_idle(self.desk, str(self.home), cid)
        return self.desk._conversation(str(self.home), cid)

    def card(self):
        folder = self.home / ".collab" / "行政提议"
        props = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")]
        return [p for p in props if p.get("kind") == "web_install"][-1]

    def test_official_site(self):
        text = self.propose(official=True, maker="Demo 公司")
        self.assertTrue(text.startswith("已向用户出示确认框"), text)
        card = self.card()
        self.assertTrue(card["official"])
        self.assertEqual(card["maker"], "Demo 公司")
        event = clerk_tasks.event_from_proposal(card)
        for k in ("official", "maker", "risk"):
            self.assertIn(k, event)

    def test_unofficial_needs_risk(self):
        text = self.propose(official=False, maker="someone")
        self.assertTrue(text.startswith("没有出卡"), text)
        self.assertIn("风险", text)
        text = self.propose(official=False, maker="someone", risk="通过你的浏览器登录来用，可能封号")
        self.assertTrue(text.startswith("已向用户出示确认框"), text)
        card = self.card()
        self.assertFalse(card["official"])
        self.assertEqual(card["risk"], "通过你的浏览器登录来用，可能封号")

    def test_missing_official_counts_as_unofficial(self):
        self.assertTrue(self.propose().startswith("没有出卡"))
        self.assertTrue(self.propose(risk="个人维护").startswith("已向用户出示确认框"))
        self.assertFalse(self.card()["official"])

    def test_code_hosting_is_never_official(self):
        for url in ("https://github.com/x/y", "https://someone.github.io/tool", "https://gitee.com/a/b",
                    "https://www.npmjs.com/package/x", "https://gitlab.com/a/b"):
            self.assertTrue(clerk_tasks.code_hosting(url), url)
        for url in ("https://tool.example.com/docs", "https://xhs-cli.com", "https://notgithub.com/x"):
            self.assertFalse(clerk_tasks.code_hosting(url), url)
        text = clerk_tasks.propose_web_install(self.conv(), {"url": GH_URL, "install_command": "npm install -g @demo/tool",
                                                             "official": True, "maker": "someone"})
        self.assertTrue(text.startswith("没有出卡"), "GitHub 上的说是官方也算非官方，就得写风险：" + text)

    def test_tool_schema(self):
        tool = next(t for t in blocks_tools.TOOLS if t["function"]["name"] == "propose_web_install")
        props = tool["function"]["parameters"]["properties"]
        for k in ("official", "maker", "risk"):
            self.assertIn(k, props)


# ---------- 3. 搜完说结果 ----------

class SearchReminder(ButlerCase):
    def test_reminder(self):
        text = blocks_tools.run(str(self.home), "web_search", {"query": "demo"})
        self.assertIn(PAGE_URL, text)
        self.assertIn("先告诉用户搜到了什么", text)

    def test_no_key_no_reminder(self):
        clerk_tasks.key_source = lambda account: ""
        text = blocks_tools.run(str(self.home), "web_search", {"query": "demo"})
        self.assertNotIn("先告诉用户搜到了什么", text)


# ---------- 4. 动作 ----------

class Actions(ButlerCase):
    def test_saved_and_shown_and_reloaded(self):
        cid, events = self.run_steps([
            {"tool_calls": [tool_call("web_search", {"query": "小红书 CLI 官方 安装"})]},
            {"tool_calls": [tool_call("read_page", {"url": PAGE_URL})]},
            {"text": "官方没有。"}])
        acts = [e for e in events if e.get("type") == "activity"]
        self.assertEqual([a["label"] for a in acts], ["搜网上", "读网页"])
        self.assertIn("小红书 CLI 官方 安装", acts[0]["text"])
        self.assertIn("1 条", acts[0]["text"])
        self.assertIn("tool.example.com", acts[1]["text"])
        saved = [x for x in self.lines(cid) if x["role"] == "action"]
        self.assertEqual([x["name"] for x in saved], ["web_search", "read_page"])
        self.assertTrue(all(len(x["text"]) <= 200 for x in saved))
        fresh = ClerkDesk(lambda: self.client)
        again = fresh.events(str(self.home), cid)["events"]
        old = [e for e in again if e.get("type") == "activity"]
        self.assertEqual([a["label"] for a in old], ["搜网上", "读网页"], "重开后灰字一行还在")
        self.assertTrue(all(a.get("history") for a in old))
        order = [e["type"] for e in again if e["type"] in ("user", "activity", "message")]
        self.assertEqual(order, ["user", "activity", "activity", "message"], "顺序和当时一样")
        self.assertFalse([m for m in fresh._conversation(str(self.home), cid).messages if m["role"] == "action"],
                         "动作不进模型的对话")

    def test_secret_masked(self):
        cid, events = self.run_steps([{"tool_calls": [tool_call("web_search", {"query": "查 " + KEY})]},
                                      {"text": "好"}])
        raw = (self.home / ".collab" / "行政对话" / f"{cid}.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(KEY, raw)
        self.assertNotIn(KEY, json.dumps(events, ensure_ascii=False))

    def test_labels_new_style(self):
        for name, label in {"scan_environment": "查电脑", "check_installed": "查安装", "install_options": "查安装",
                            "read_help": "读帮助", "list_blocks": "看基础层", "web_search": "搜网上",
                            "read_page": "读网页", "read_manual": "翻手册", "propose_web_install": "出卡",
                            "propose_connect_cli": "出卡"}.items():
            self.assertEqual(blocks_tools.LABELS[name], label, name)

    def test_project_window_labels_unchanged(self):
        from desktop.clerk.tools import LABELS
        self.assertEqual(LABELS["read_card"], "读 卡片")
        self.assertEqual(LABELS["propose_task"], "读 提议")


# ---------- 5. 改名 ----------

class Rename(unittest.TestCase):
    BLOCK_ONLY = ["desktop/static/blocks.html", "desktop/static/blocks.js", "collab/blocks_check.py",
                  "collab/blocks_env.py", "collab/blocks_install.py"]

    def visible_strings(self, text):
        """去掉注释，只看会显示给用户的字。"""
        text = re.sub(r"(?m)^\s*#.*$", "", text)
        text = re.sub(r'"""[\s\S]*?"""', "", text)
        text = re.sub(r"/\*[\s\S]*?\*/", "", text)
        text = re.sub(r"(?m)//.*$", "", text)
        text = re.sub(r"(?m)#[^\"'\n]*$", "", text)
        return text

    def test_block_only_files_say_butler(self):
        for rel in self.BLOCK_ONLY:
            text = self.visible_strings((ROOT / rel).read_text(encoding="utf-8"))
            self.assertNotIn("行政", text, rel)

    def test_chat_page(self):
        js = (ROOT / "desktop/static/chat.js").read_text(encoding="utf-8")
        self.assertIn("积木库管家", js)
        self.assertIn('"跟行政说…"', js, "项目窗口仍是行政")
        self.assertIn("butler", (ROOT / "desktop/static/blocks.js").read_text(encoding="utf-8"))

    def test_server_block_names(self):
        text = (ROOT / "desktop/server.py").read_text(encoding="utf-8")
        self.assertNotIn('"name": "行政"', text)
        self.assertIn('"name": "积木库管家"', text)

    def test_node_checks(self):
        proc = subprocess.run(["node", str(Path(__file__).parent / "butler_page_check.js")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)



# ---------- Codex 第一轮复查（2026-11-10）补的 ----------

class ReviewFixes(ButlerCase):
    def conv(self):
        cid = self.desk.send(str(self.home), "", "你好")
        wait_idle(self.desk, str(self.home), cid)
        return self.desk._conversation(str(self.home), cid)

    def test_redirect_to_code_host_is_unofficial(self):
        real = blocks_web.verify_install

        def moved(*a, **k):
            return dict(real(*a, **k), source=GH_URL, domain="github.com")
        with mock.patch.object(blocks_web, "verify_install", side_effect=moved):
            text = clerk_tasks.propose_web_install(self.conv(), {"url": PAGE_URL, "official": True, "maker": "x",
                                                                 "install_command": "npm install -g @demo/tool"})
        self.assertTrue(text.startswith("没有出卡"), "跳转到代码托管站也算非官方：" + text)

    def test_idna_host(self):
        self.assertTrue(clerk_tasks.code_hosting("https://github。com/a/b"))
        self.assertTrue(clerk_tasks.code_hosting("https://GitHub.com:443/a"))
        self.assertTrue(clerk_tasks.code_hosting("https://user@github.com/a"))
        self.assertFalse(clerk_tasks.code_hosting("https://github.com.evil.com/a"))

    def test_model_key_not_in_actions(self):
        secret = "opaqueModelCredential1234567890"
        self.client.api_key = secret
        cid, events = self.run_steps([{"tool_calls": [tool_call("web_search", {"query": "查 " + secret})]},
                                      {"text": "好"}])
        raw = (self.home / ".collab" / "行政对话" / f"{cid}.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        self.assertNotIn(secret, json.dumps(events, ensure_ascii=False))

    def test_maker_risk_masked(self):
        text = clerk_tasks.propose_web_install(self.conv(), {
            "url": PAGE_URL, "install_command": "npm install -g @demo/tool", "official": False,
            "maker": "作者 " + KEY, "risk": "个人维护 " + KEY})
        self.assertTrue(text.startswith("已向用户出示确认框"), text)
        folder = self.home / ".collab" / "行政提议"
        self.assertNotIn(KEY, "".join(p.read_text(encoding="utf-8") for p in folder.glob("*.json")))

    def test_stopped_turn_leaves_no_trace(self):
        import threading
        gate, started = threading.Event(), threading.Event()
        real = blocks_web.search

        def slow(query, key, max_results=5):
            started.set()
            gate.wait(5)
            return real(query, key, max_results)
        with mock.patch.object(blocks_web, "search", side_effect=slow):
            self.client.steps = [{"tool_calls": [tool_call("web_search", {"query": "demo"})]}, {"text": "好"}]
            cid = self.desk.send(str(self.home), "", "帮我搜")
            self.assertTrue(started.wait(5))
            self.desk.stop(str(self.home), cid)
            gate.set()
            import time
            time.sleep(0.5)
        self.assertFalse([x for x in self.lines(cid) if x["role"] in ("manual", "action")], "停止后晚到的结果不记")
        self.assertNotIn("web", self.desk._conversation(str(self.home), cid).manuals)
        self.assertFalse([e for e in self.desk.events(str(self.home), cid)["events"] if e["type"] == "activity"])

    def test_refused_card_label(self):
        cid, events = self.run_steps([{"tool_calls": [tool_call("propose_web_install", {
            "url": PAGE_URL, "install_command": "npm install -g @demo/tool", "official": False})]}, {"text": "好"}])
        acts = [e for e in events if e.get("type") == "activity"]
        self.assertEqual(acts[0]["label"], "没出卡")


class RenameMore(unittest.TestCase):
    def test_blocks_web(self):
        text = Rename().visible_strings((ROOT / "collab/blocks_web.py").read_text(encoding="utf-8"))
        self.assertNotIn("行政", text)


# ---------- Codex 第二轮复查（2026-11-10）补的 ----------

class ReviewFixes2(ButlerCase):
    MODEL_KEY = "opaqueModelCredential1234567890"

    def test_model_key_not_in_card_fields(self):
        self.client.api_key = self.MODEL_KEY
        self.run_steps([{"tool_calls": [tool_call("propose_web_install", {
            "url": PAGE_URL, "install_command": "npm install -g @demo/tool", "official": False,
            "maker": "作者 " + self.MODEL_KEY, "risk": "风险 " + self.MODEL_KEY})]}, {"text": "好"}])
        folder = self.home / ".collab" / "行政提议"
        saved = "".join(p.read_text(encoding="utf-8") for p in folder.glob("*.json"))
        self.assertIn("web_install", saved)
        self.assertNotIn(self.MODEL_KEY, saved)

    def stop_during(self, target, attr, steps, second):
        """target.attr 卡住时点停止，再说一句新话；放行后看旧的那一步留下了什么。"""
        import threading
        import time
        gate, started = threading.Event(), threading.Event()
        real = getattr(target, attr)

        def slow(*a, **k):
            started.set()
            gate.wait(5)
            return real(*a, **k)
        before = set(threading.enumerate())
        with mock.patch.object(target, attr, side_effect=slow):
            self.client.steps = list(steps)
            cid = self.desk.send(str(self.home), "", "第一句")
            self.assertTrue(started.wait(5))
            self.desk.stop(str(self.home), cid)
            self.client.steps = list(second)
            self.desk.send(str(self.home), cid, "第二句")
            gate.set()
            # 等旧的那一轮真正结束（不能只看新一轮忙不忙：复查第三轮小 1）
            end = time.time() + 5
            while time.time() < end and any(t.is_alive() for t in set(threading.enumerate()) - before):
                time.sleep(0.02)
            self.assertFalse([t for t in set(threading.enumerate()) - before if t.is_alive()], "旧的那一轮没结束")
            wait_idle(self.desk, str(self.home), cid)
        return cid

    def test_escaped_key_in_arguments(self):
        self.client.api_key = self.MODEL_KEY
        escaped = "\\u006f" + self.MODEL_KEY[1:]  # JSON 里写成转义，解析后才变回密钥
        raw = ('{"url": "%s", "install_command": "npm install -g @demo/tool", "official": false, '
               '"maker": "作者 %s", "risk": "风险 %s"}' % (PAGE_URL, escaped, escaped))
        cid, events = self.run_steps([{"tool_calls": [tool_call("propose_web_install", raw)]}, {"text": "好"}])
        folder = self.home / ".collab" / "行政提议"
        saved = "".join(p.read_text(encoding="utf-8") for p in folder.glob("*.json"))
        self.assertIn("web_install", saved)
        self.assertNotIn(self.MODEL_KEY, saved)
        self.assertNotIn(self.MODEL_KEY, json.dumps(events, ensure_ascii=False))

    def test_stop_during_web_install_check(self):
        cid = self.stop_during(blocks_web, "verify_install", [
            {"tool_calls": [tool_call("propose_web_install", {"url": PAGE_URL, "install_command": "npm install -g @demo/tool",
                                                              "official": True, "maker": "Demo"})]}, {"text": "好"}],
            [{"text": "嗯"}])
        folder = self.home / ".collab" / "行政提议"
        props = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")] if folder.is_dir() else []
        self.assertFalse([p for p in props if p.get("conversation") == cid], "停止后核对完的旧卡不能出")
        self.assertFalse([e for e in self.desk.events(str(self.home), cid)["events"] if e["type"] == "proposal"])

    def test_stop_then_new_turn_no_cross_state(self):
        cid = self.stop_during(blocks_web, "search", [
            {"tool_calls": [tool_call("web_search", {"query": "demo"})]}, {"text": "好"}], [{"text": "嗯"}])
        conv = self.desk._conversation(str(self.home), cid)
        self.assertNotIn("web", conv.manuals)
        self.assertFalse([x for x in self.lines(cid) if x["role"] in ("manual", "action")])



# ---------- 用户真实接高德时暴露的（2026-11-10） ----------

DSML = ('<｜｜DSML｜｜ calls>\n<｜｜DSML｜｜ invoke name="propose_connect_cli">\n'
        '<｜｜DSML｜｜ parameter name="card" string="false">{"id": "x"}</｜｜DSML｜｜ parameter>\n'
        '</｜｜DSML｜｜ invoke>\n</｜｜DSML｜｜ calls>')


class AmapFindings(ButlerCase):
    def test_blocks_gets_more_rounds_and_final_round_is_told(self):
        from desktop.clerk import desk as desk_module
        steps = [{"tool_calls": [tool_call("list_blocks", {})]} for _ in range(desk_module.BLOCKS_TOOL_ROUNDS)]
        cid, events = self.run_steps(steps + [{"text": "快好了。\n" + DSML}])
        self.assertEqual(desk_module.BLOCKS_TOOL_ROUNDS, 8)
        self.assertEqual(len(self.client.calls), 9)
        self.assertFalse(self.client.calls[-1][1], "最后一次不带工具")
        last_msgs = json.dumps(self.client.calls[-1][0], ensure_ascii=False)
        self.assertIn("步骤用完了", last_msgs, "最后一次要明说不能再用工具")
        shown = [e["text"] for e in events if e["type"] == "message"]
        self.assertTrue(shown)
        self.assertFalse(any("DSML" in t or "<｜" in t for t in shown), shown)
        self.assertTrue(any("继续" in t for t in shown), shown)
        saved = (self.home / ".collab" / "行政对话" / f"{cid}.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("DSML", saved)

    def test_tool_markup_stripped_any_round(self):
        cid, events = self.run_steps([{"text": "好的\n<tool_call>{\"name\": \"x\"}</tool_call>"}])
        shown = " ".join(e["text"] for e in events if e["type"] == "message")
        self.assertNotIn("tool_call", shown)

    def test_drafted_card_gets_id_and_kind(self):
        card = {"name": "高德地图", "command": "amap-gui", "role": "tool", "probe": ["status"],
                "version_args": [], "secrets": [{"env": "AMAP_KEY", "label": "高德 Key"}]}
        self.run_steps([{"tool_calls": [tool_call("propose_connect_cli", {"card": card})]}, {"text": "好"}])
        result = self.card_replies[0]  # 出了卡这一轮就结束（2026-11-10），看出卡工具的回话
        self.assertTrue(result.startswith("已向用户出示确认框"), result)

    def test_bad_card_error_shows_example(self):
        card = {"name": "高德地图", "command": "amap-gui", "role": "tool", "probe": ["status"], "type": "cli"}
        self.run_steps([{"tool_calls": [tool_call("propose_connect_cli", {"card": card})]}, {"text": "好"}])
        result = self.tool_results(1)[0]
        self.assertTrue(result.startswith("接法卡里有不认识的项"), result)
        self.assertIn('"probe"', result)
        self.assertIn("照这个样子", result)

    def test_cli_manual_has_full_example(self):
        cli = blocks_tools.manual_text("cli")
        for word in ('"id"', '"kind": "cli"', '"command"', '"role": "tool"', '"probe"', '"secrets"'):
            self.assertIn(word, cli, word)

    def test_package_name_not_masked_as_email(self):
        from collab import blocks_check
        line = "npm warn deprecated node-domexception@1.0.0: Use your platform's native DOMException instead"
        self.assertIn("node-domexception@1.0.0", blocks_check._mask(line))
        self.assertNotIn("someone@example.com", blocks_check._mask("联系 someone@example.com"))

    def test_hit_count(self):
        result = ("1. A｜https://a.com｜第一步\n2. 安装\n3. 配置\n2. B｜https://b.com｜x\n"
                  "（回话里先告诉用户搜到了什么）")
        self.assertIn("找到 2 条", blocks_tools.describe("web_search", {"query": "q"}, result))



class AmapReview(ButlerCase):
    """Codex 复查高德那组修改（2026-11-10）补的。"""

    def test_streamed_markup_never_shown(self):
        class Streaming(ScriptedClient):
            def chat(self, messages, on_text=None, tools=None):
                if on_text:
                    for piece in ["快好了。", "\n<｜｜DS", "ML｜｜ calls>", '{"id": 1}', "</｜｜DSML｜｜ calls>"]:
                        on_text(piece)
                return {"text": "快好了。\n" + DSML, "tool_calls": [], "tokens": 1}
        self.client = Streaming()
        self.desk = ClerkDesk(lambda: self.client, blocks_client=lambda: self.client)
        cid, events = self.talk("你好")
        streamed = "".join(e["text"] for e in events if e["type"] == "delta")
        self.assertNotIn("DSML", streamed)
        self.assertNotIn("<｜", streamed)
        self.assertIn("快好了", streamed)

    def test_stop_saves_clean_partial(self):
        import threading
        gate = threading.Event()

        class Hang(ScriptedClient):
            def chat(self, messages, on_text=None, tools=None):
                on_text("说到一半。\n<｜｜DSML｜｜ calls>")
                gate.wait(5)
                return {"text": "", "tool_calls": [], "tokens": 1}
        self.client = Hang()
        self.desk = ClerkDesk(lambda: self.client, blocks_client=lambda: self.client)
        cid = self.desk.send(str(self.home), "", "你好")
        import time
        time.sleep(0.3)
        self.desk.stop(str(self.home), cid)
        gate.set()
        raw = (self.home / ".collab" / "行政对话" / f"{cid}.jsonl").read_text(encoding="utf-8")
        self.assertIn("说到一半", raw)
        self.assertNotIn("DSML", raw)

    def test_error_partial_clean(self):
        from desktop.clerk.client import ModelError

        class Broken(ScriptedClient):
            def chat(self, messages, on_text=None, tools=None):
                raise ModelError("连不上", partial="写了一半\n<｜｜DSML｜｜ calls>")
        self.client = Broken()
        self.desk = ClerkDesk(lambda: self.client, blocks_client=lambda: self.client)
        cid, events = self.talk("你好")
        raw = (self.home / ".collab" / "行政对话" / f"{cid}.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("DSML", raw + json.dumps(events, ensure_ascii=False))

    def test_tables_and_code_kept(self):
        for text in ("| 符号 | 意思 |\n| < | 小于 |\n| > | 大于 |", "例子：`<invoke name=\"x\"/>` 是一个标签",
                     "a <| b 这种写法", "if a < b and c | d: pass"):
            cid, events = self.run_steps([{"text": text}])
            shown = [e["text"] for e in events if e["type"] == "message"][-1]
            self.assertEqual(shown, text)

    def test_email_masks_kept(self):
        from collab import blocks_check
        for text in ("登录 a@b.co已成功", "用户@例子.公司", "A.B@Example.COM", "x@mail2.163.com"):
            self.assertIn("[已隐藏]", blocks_check._mask(text), text)
        self.assertIn("left-pad@1.3.0", blocks_check._mask("npm warn deprecated left-pad@1.3.0: x"))

    def test_last_round_note_not_saved_and_project_window_still_5(self):
        from desktop.clerk import desk as desk_module
        steps = [{"tool_calls": [tool_call("list_blocks", {})]} for _ in range(desk_module.BLOCKS_TOOL_ROUNDS)]
        cid, _ = self.run_steps(steps + [{"text": "到这里"}])
        raw = (self.home / ".collab" / "行政对话" / f"{cid}.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("步骤用完了", raw)
        self.assertEqual(desk_module.MAX_TOOL_ROUNDS, 5)

    def test_auto_id_keeps_preset_guard(self):
        card = {"name": "假 Codex", "command": "codex", "run": ["{prompt}"]}
        self.run_steps([{"tool_calls": [tool_call("propose_connect_cli", {"card": card})]}, {"text": "好"}])
        self.assertIn("预置接法的编号", self.tool_results(1)[0])

    def test_auto_id_avoids_other_block(self):
        blocks.save_card({"id": "foo-bar", "kind": "cli", "name": "旧的", "command": "foo-bar", "run": ["{prompt}"]},
                         self.home)
        card = {"name": "新的", "command": "foo_bar", "role": "tool", "probe": ["status"]}
        self.run_steps([{"tool_calls": [tool_call("propose_connect_cli", {"card": card})]}, {"text": "好"}])
        result = self.card_replies[0]  # 出了卡这一轮就结束（2026-11-10），看出卡工具的回话
        self.assertTrue(result.startswith("已向用户出示确认框"), result)
        self.assertNotIn("会替换", result, "自动起的编号不能顶掉别的工具")
        folder = self.home / ".collab" / "行政提议"
        props = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*.json")]
        card_ids = [p["block"]["id"] for p in props if p.get("kind") == "connect"]
        self.assertEqual(card_ids, ["foo-bar-2"])



class AmapReview2(ButlerCase):
    """Codex 复查高德那组修改第 2 轮补的。"""

    def test_markup_in_one_round_does_not_hide_next_round(self):
        cid, events = self.run_steps([
            {"text": "第一轮。<tool_call>{}", "tool_calls": [tool_call("list_blocks", {})]},
            {"text": "第二轮干净的话"}])
        streamed = "".join(e["text"] for e in events if e["type"] == "delta")
        self.assertIn("第二轮干净的话", streamed)
        self.assertNotIn("tool_call", streamed)

    def test_stale_reply_after_stop_not_sealed(self):
        import threading
        import time
        gate, entered = threading.Event(), threading.Event()
        owner = self

        class Slow(ScriptedClient):
            def chat(self, messages, on_text=None, tools=None):
                if not entered.is_set():
                    entered.set()
                    gate.wait(5)
                    return {"text": "旧的话", "tool_calls": [tool_call("propose_connect_search", {})], "tokens": 1}
                return super().chat(messages, on_text, tools)
        self.client = Slow([{"text": "新的话"}])
        self.desk = ClerkDesk(lambda: self.client, blocks_client=lambda: self.client)
        cid = self.desk.send(str(self.home), "", "第一句")
        self.assertTrue(entered.wait(5))
        self.desk.stop(str(self.home), cid)
        self.desk.send(str(self.home), cid, "第二句")
        wait_idle(self.desk, str(self.home), cid)
        gate.set()
        time.sleep(0.5)
        raw = (self.home / ".collab" / "行政对话" / f"{cid}.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("旧的话", raw)
        self.assertIn("新的话", raw)

    def test_email_more(self):
        from collab import blocks_check
        self.assertIn("[已隐藏]", blocks_check._mask("test@example.xn--p1ai"))
        kept = blocks_check._mask("登录 a@b.co已成功")
        self.assertTrue(kept.startswith("登录 [已隐藏]") and "a@b" not in kept, kept)  # 后面的中文一起遮掉：原来就这样，宁多不漏
        self.assertIn("[已隐藏]", blocks_check._mask("用户@例子.公司"))
        self.assertIn("pkg@1.0.0", blocks_check._mask("npm warn pkg@1.0.0"))

    def test_all_suffixes_taken(self):
        for i, cid in enumerate(["foo"] + [f"foo-{n}" for n in range(2, 10)]):
            blocks.save_card({"id": cid, "kind": "cli", "name": f"旧{i}", "command": f"old{i}", "run": ["{prompt}"]},
                             self.home)
        card = {"name": "新的", "command": "foo", "role": "tool", "probe": ["status"]}
        self.run_steps([{"tool_calls": [tool_call("propose_connect_cli", {"card": card})]}, {"text": "好"}])
        result = self.tool_results(1)[0].split("（你还没翻")[0]
        self.assertFalse(result.startswith("已向用户出示确认框"), result)
        self.assertIn("编号", result)

    def test_long_protocol_name_held(self):
        class Pieces(ScriptedClient):
            def chat(self, messages, on_text=None, tools=None):
                for piece in ["好的", "<|", "a" * 40, "|>", "x"]:
                    if on_text:  # 起标题那次不带 on_text
                        on_text(piece)
                return {"text": "好的<|" + "a" * 40 + "|>x", "tool_calls": [], "tokens": 1}
        self.client = Pieces()
        self.desk = ClerkDesk(lambda: self.client, blocks_client=lambda: self.client)
        cid, events = self.talk("你好")
        streamed = "".join(e["text"] for e in events if e["type"] == "delta")
        self.assertNotIn("aaaa", streamed)



class AmapReview3(unittest.TestCase):
    """Codex 复查高德那组修改第 3 轮补的：邮箱整段遮住，包名@版本号留着。"""

    def test_emails_fully_masked(self):
        from collab import blocks_check
        for text in ("test@example.XN--P1AI", "test@example.xN--p1ai", "alice@mx.mail.example.com",
                     "alice@dept.co.uk", "test@example.みんな", "x@163.com", "A.B@Example.COM"):
            self.assertEqual(blocks_check._mask(text), "[已隐藏]", text)

    def test_package_versions_kept(self):
        from collab import blocks_check
        for text in ("npm warn deprecated node-domexception@1.0.0: x", "npm warn pkg@1.0.0-alpha.beta",
                     "added @scope/pkg@2.3.4"):
            self.assertEqual(blocks_check._mask(text), text, text)



# ---------- 用户真实接腾讯云混元时暴露的（2026-11-10） ----------

DOTTED = "sk-ws-A.BCDEFGH.IjK3.LMNOPQrstUVwxyz0123456789abcdefgHIJ_klmnoPQR"  # 百炼新格式（中间带点）的假密钥


class HunyuanFindings(ButlerCase):
    def test_dotted_key_hidden_before_model_and_file(self):
        cid, events = self.run_steps([{"text": "好"}], text="把阿里百炼的api接进来" + DOTTED)
        raw = (self.home / ".collab" / "行政对话" / f"{cid}.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(DOTTED[6:30], raw)
        self.assertNotIn(DOTTED[6:30], json.dumps(self.client.calls, ensure_ascii=False), "不能发给模型")
        self.assertTrue(any(e["type"] == "notice" for e in events), "要提醒用户作废")

    def test_other_key_shapes(self):
        for key in ("sk-proj-abcDEF123.456ghiJKL789mnopQRS", "sk-ant-api03-abcdefghijklmnopqrstuv",
                    "sk-0123456789abcdef0123456789abcdef"):
            self.assertTrue(blocks._SECRET_TEXT.search(key), key)
        for text in ("sk-learn 这个库", "请看 sk-xxx 开头的那串"):
            self.assertFalse(blocks._SECRET_TEXT.search(text), text)

    def test_claims_from_screenshot_get_reminded(self):
        from desktop.clerk.desk import points_at_card
        for text in ("接法卡给你：平台选腾讯云，模型先用 hunyuan-turbo。", "卡片已经送到你屏幕上了。",
                     "你屏幕上的这张卡还在，请填好后点确认即可。", "那就直接填进卡上：两个框都要填，然后点确认。"):
            self.assertTrue(points_at_card(text), text)
        for text in ("要我给你出一张接法卡吗？", "装好之后，我再把它接进基础层，那时候会让你把两把密钥填进卡里。",
                     "等卡出来了再填密钥。", "接法卡的检测结果出来了：通过。", "百炼已经接好了，没问题。"):
            self.assertFalse(points_at_card(text), text)

    def test_reminded_when_no_card_on_screen(self):
        cid, events = self.run_steps([{"text": "卡片已经送到你屏幕上了，填上去点确认。"},
                                      {"tool_calls": [tool_call("propose_connect_api", {
                                          "provider": "tencent", "model": "hunyuan-turbo", "name": "腾讯混元"})]},
                                      {"text": "好了，卡在上面。"}], text="腾讯云的混元模型也接进去")
        sent = json.dumps(self.client.calls[1][0], ensure_ascii=False)
        self.assertIn("系统提示", sent, "说了卡在屏幕上却没出：要提醒它")
        self.assertTrue([e for e in events if e["type"] == "proposal"])

    def test_not_reminded_when_card_is_open(self):
        self.run_steps([{"tool_calls": [tool_call("propose_connect_search", {})]}, {"text": "好"}])
        cid = self.desk.list(str(self.home))["threads"][0]["id"] if "threads" in self.desk.list(str(self.home)) else None
        calls_before = len(self.client.calls)
        self.client.steps = [{"text": "你屏幕上的这张卡还在，填好点确认。"}]
        self.desk.send(str(self.home), cid, "然后呢")
        wait_idle(self.desk, str(self.home), cid)
        self.assertEqual(len(self.client.calls) - calls_before, 1, "卡真在上面：不用提醒")

    def test_api_tool_says_one_key(self):
        tool = next(t for t in blocks_tools.TOOLS if t["function"]["name"] == "propose_connect_api")
        self.assertIn("一把 API 密钥", tool["function"]["description"])
        self.assertIn("SecretId", tool["function"]["description"])



# ---------- 用户第二次接腾讯云混元（管家猜的模型名不对）和 Codex 复查补的（2026-11-10） ----------

MODELS = ["hy4", "hy4-preview", "hy3", "kimi-k3.1-pro", "deepseek/deepseek-flash", "hunyuan-role-latest"]


class ModelNames(ButlerCase):
    def setUp(self):
        super().setUp()
        from collab import blocks_check
        self.fetched = []

        def fake_fetch(base_url, key):
            self.fetched.append((base_url, key))
            return list(MODELS)
        p = mock.patch.object(blocks_check, "fetch_models", side_effect=fake_fetch)
        p.start()
        self.addCleanup(p.stop)
        clerk_tasks.key_source = lambda account: {"tavily": KEY, "tencent": "tc-key-0123456789abcdef"}.get(account, "")

    def test_tool_lists_real_names(self):
        self.assertIn("list_models", {t["function"]["name"] for t in blocks_tools.TOOLS})
        text = blocks_tools.run(str(self.home), "list_models", {"provider": "tencent"})
        for name in MODELS:
            self.assertIn(name, text)
        self.assertEqual(self.fetched[0][0], "https://tokenhub.tencentmaas.com/v1")
        self.assertNotIn("tc-key", text)
        text = blocks_tools.run(str(self.home), "list_models", {"provider": "tencent", "keyword": "hy"})
        self.assertIn("hy4", text)
        self.assertNotIn("kimi", text)

    def test_tool_without_saved_key(self):
        text = blocks_tools.run(str(self.home), "list_models", {"provider": "bailian"})
        self.assertIn("还没存", text)
        self.assertEqual(self.fetched, [])

    def test_tool_error_is_plain(self):
        from collab import blocks_check
        from collab.blocks import BlockError
        with mock.patch.object(blocks_check, "fetch_models", side_effect=BlockError("这家没给模型名单")):
            text = blocks_tools.run(str(self.home), "list_models", {"provider": "tencent"})
        self.assertIn("这家没给模型名单", text)

    def test_manual_and_labels(self):
        api = blocks_tools.manual_text("api")
        self.assertIn("list_models", api)
        self.assertIn("不要凭记忆", api)
        self.assertEqual(blocks_tools.MANUAL_OF["list_models"], "api")
        self.assertEqual(blocks_tools.LABELS["list_models"], "查模型")

    def test_wrong_model_shows_real_names(self):
        from collab import blocks_check
        from desktop.clerk.client import ModelError

        class Bad:
            def chat(self, *a, **k):
                raise ModelError("模型服务商暂时出错了（代码 400），请稍后再试。")
        card = {"id": "tencent-hy", "kind": "llm_api", "name": "腾讯云混元", "provider": "tencent",
                "base_url": "https://tokenhub.tencentmaas.com/v1", "model": "hunyuan-turbos-latest",
                "key_account": "tencent"}
        with mock.patch.object(blocks_check, "_default_client", return_value=Bad()):
            result = blocks_check.check_api(card, "tc-key-0123456789abcdef", root=self.home, record=False)
        self.assertFalse(result["ok"])
        said = result["reason"] + result["how"]
        self.assertIn("hunyuan-turbos-latest", said, "说清是哪个名字不对")
        self.assertIn("hy4", said)
        self.assertNotIn("tc-key", said)

    def test_fetch_models_parses(self):
        import io
        from collab import blocks_check
        mock.patch.stopall()
        body = json.dumps({"data": [{"id": "hy4"}, {"id": "bad name!"}, {"id": 3}, {"id": "hy3"}]}).encode()
        seen = {}

        def fake_open(request, timeout=None):
            seen["url"], seen["auth"] = request.full_url, request.headers.get("Authorization")
            return io.BytesIO(body)
        with mock.patch.object(blocks_check, "_open_models", side_effect=fake_open):
            self.assertEqual(blocks_check.fetch_models("https://x.example.com/v1/", "k123"), ["hy4", "hy3"])
        self.assertEqual(seen["url"], "https://x.example.com/v1/models")
        self.assertEqual(seen["auth"], "Bearer k123")
        from collab.blocks import BlockError
        with self.assertRaises(BlockError):
            blocks_check.fetch_models("http://x.example.com/v1", "k")


class ScreenAndWording(ButlerCase):
    def test_prompt_says_what_is_on_screen(self):
        self.run_steps([{"text": "好"}])
        self.assertIn("屏幕上现在没有待处理的卡", self.system(len(self.client.calls) - 1))
        cid, _ = self.run_steps([{"tool_calls": [tool_call("propose_connect_search", {})]}, {"text": "好"}])
        self.run_steps([{"text": "嗯"}], text="然后呢", cid=cid)
        system = self.system(len(self.client.calls) - 1)
        self.assertIn("屏幕上现在有一张待处理的卡", system)
        self.assertIn("联网搜索", system)

    def test_confirm_reply_does_not_say_connected(self):
        result = clerk_tasks._block_result({"kind": "connect_api", "name": "腾讯云混元", "block": {}}, False)
        self.assertNotIn("已确认接入", result["reply"])
        self.assertIn("开始检测", result["reply"])
        self.assertIn("才能放进基础层", result["reply"])  # 2026-11-11：拿到名单就算接上，不再说“通过才”

    def test_claims_card_knows_kapian(self):
        from desktop.clerk.desk import claims_card
        self.assertTrue(claims_card("卡片已经弹出来了。"))
        self.assertTrue(claims_card("安装卡已经出来了，你看一下。"))


class SecretRuleReview(unittest.TestCase):
    def test_file_names_and_urls_not_masked(self):
        for text in ("task-configuration.html", "https://x.com/docs/task-configuration-guide-v2-final",
                     "risk-assessment-report-2026.pdf", "desk-lamp.model.v2.final"):
            self.assertIsNone(blocks._SECRET_TEXT.search(text), text)

    def test_keys_still_masked_whole(self):
        for key in (DOTTED, "sk-0123456789abcdef0123456789abcdef"):
            for text in (key, "密钥" + key, "KEY=" + key + "。", "(" + key + ")"):
                left = blocks._SECRET_TEXT.sub("", text)
                self.assertNotIn(key[5:25], left, text)

    def test_no_slow_input(self):
        import time
        started = time.time()
        blocks._SECRET_TEXT.sub("x", "sk-." * 60000)
        self.assertLess(time.time() - started, 1.0)



class ModelListReview(ButlerCase):
    """Codex 复查“查模型名单”补的（2026-11-10）。"""
    TC = "tcKey0123456789abcdefXYZ"

    def setUp(self):
        super().setUp()
        clerk_tasks.key_source = lambda account: {"tencent": self.TC, "other:api.example.com": self.TC}.get(account, "")
        self.addCleanup(mock.patch.stopall)

    def serve(self, payload, chunk_delay=0):
        import io
        import time
        from collab import blocks_check
        body = json.dumps(payload).encode()

        class Slow(io.BytesIO):
            def read(self, n=-1):
                if chunk_delay:
                    time.sleep(chunk_delay)
                    return super().read(min(n, 64) if n and n > 0 else 64)
                return super().read(n)
        p = mock.patch.object(blocks_check, "_open_models", side_effect=lambda request, timeout=None: Slow(body))
        p.start()
        self.addCleanup(p.stop)

    def test_key_echoed_as_model_name_is_dropped(self):
        from collab import blocks_check
        self.serve({"data": [{"id": "hy4"}, {"id": self.TC}, {"id": "x-" + self.TC}, {"id": "sk-0123456789abcdef0123"}]})
        self.assertEqual(blocks_check.fetch_models("https://tokenhub.tencentmaas.com/v1", self.TC), ["hy4"])
        text = blocks_tools.run(str(self.home), "list_models", {"provider": "tencent"})
        self.assertNotIn(self.TC, text)
        self.assertIn("只是名字", text, "告诉管家这是平台给的数据，不是指令")

    def test_other_platforms_never_listed_by_butler(self):
        """收窄（复查三轮后）：管家只查腾讯云、百炼这两家固定地址；别的平台不拿存着的密钥去问。"""
        from collab import blocks_check
        blocks.save_card({"id": "ex", "kind": "llm_api", "name": "例子", "provider": "other",
                          "base_url": "https://api.example.com/v1", "model": "m1",
                          "key_account": "other:api.example.com"}, self.home)
        with mock.patch.object(blocks_check, "fetch_models") as fetch:
            text = blocks_tools.run(str(self.home), "list_models",
                                    {"provider": "other", "base_url": "https://api.example.com/v1"})
        fetch.assert_not_called()
        self.assertIn("只能查腾讯云、阿里百炼", text)
        tool = next(t for t in blocks_tools.TOOLS if t["function"]["name"] == "list_models")
        self.assertNotIn("base_url", tool["function"]["parameters"]["properties"])

    def test_odd_http_error_never_carries_key(self):
        import http.client
        from collab import blocks_check
        from collab.blocks import BlockError
        boom = http.client.BadStatusLine("HTTP/1.1 " + self.TC)
        with mock.patch.object(blocks_check, "_open_models", side_effect=boom):
            with self.assertRaises(BlockError) as caught:
                blocks_check.fetch_models("https://tokenhub.tencentmaas.com/v1", self.TC)
            self.assertNotIn(self.TC, str(caught.exception))
            text = blocks_tools.run(str(self.home), "list_models", {"provider": "tencent"})
        self.assertNotIn(self.TC, text)
        with mock.patch.object(blocks_check, "fetch_models", side_effect=RuntimeError("x " + self.TC)):
            self.assertNotIn(self.TC, blocks_tools.run(str(self.home), "list_models", {"provider": "tencent"}))

    def test_whole_query_has_one_deadline(self):
        import time
        from collab import blocks_check
        from collab.blocks import BlockError

        def hang(request, timeout=None):
            time.sleep(3)
            raise OSError("late")
        started = time.time()
        with mock.patch.object(blocks_check, "_open_models", side_effect=hang), \
                mock.patch.object(blocks_check, "MODELS_TIMEOUT", 0.3):
            with self.assertRaises(BlockError):
                blocks_check.fetch_models("https://tokenhub.tencentmaas.com/v1", self.TC)
        self.assertLess(time.time() - started, 1.5)

    def test_cut_list_does_not_claim_missing(self):
        from collab import blocks_check
        self.serve({"data": [{"id": f"m{i}"} for i in range(blocks_check.MODELS_LIMIT + 50)]})
        hint = blocks_check._real_models_hint({"base_url": "https://tokenhub.tencentmaas.com/v1", "model": "zz"}, self.TC)
        self.assertNotIn("这家没有叫", hint)
        self.assertIn("拿到的", hint)
        text = blocks_tools.run(str(self.home), "list_models", {"provider": "tencent", "keyword": "zz"})
        self.assertIn("拿到的", text)

    def test_long_names_not_cut_in_half(self):
        self.serve({"data": [{"id": "n" * 99 + f"{i}"} for i in range(10)] + [{"id": "x" * 95 + f"{i:02d}"} for i in range(80)]})
        text = blocks_tools.run(str(self.home), "list_models", {"provider": "tencent"})
        self.assertIn("只是名字", text)
        for line in text.splitlines()[1:-1]:
            self.assertIn(len(line), (97, 100), line[:20])

    def test_existing_model_not_called_missing(self):
        from collab import blocks_check
        self.serve({"data": [{"id": "hy4"}, {"id": "hy3"}]})
        hint = blocks_check._real_models_hint({"base_url": "https://tokenhub.tencentmaas.com/v1", "model": "hy4"}, self.TC)
        self.assertNotIn("没有叫", hint)
        self.assertIn("这家有", hint)
        self.assertIn("参数", hint)

    def test_huge_list_is_fast_and_capped(self):
        import time
        from collab import blocks_check
        self.serve({"data": [{"id": f"m{i}"} for i in range(100000)]})
        started = time.time()
        names = blocks_check.fetch_models("https://tokenhub.tencentmaas.com/v1", self.TC)
        self.assertLess(time.time() - started, 2.0)
        self.assertEqual(len(names), blocks_check.MODELS_LIMIT)

    def test_slow_drip_gives_up(self):
        import time
        from collab import blocks_check
        from collab.blocks import BlockError
        self.serve({"data": [{"id": f"model-{i}"} for i in range(400)]}, chunk_delay=0.05)
        started = time.time()
        with mock.patch.object(blocks_check, "MODELS_TIMEOUT", 0.5):
            with self.assertRaises(BlockError):
                blocks_check.fetch_models("https://tokenhub.tencentmaas.com/v1", self.TC)
        self.assertLess(time.time() - started, 2.0)

    def test_prompt_says_open_card_is_only_for_itself(self):
        cid, _ = self.run_steps([{"tool_calls": [tool_call("propose_connect_search", {})]}, {"text": "好"}])
        self.run_steps([{"text": "嗯"}], text="再接个腾讯云", cid=cid)
        self.assertIn("要另出一张", self.system(len(self.client.calls) - 1))


if __name__ == "__main__":
    unittest.main()
