"""64 号单：行政上网找官网、出网上安装卡、接联网搜索、接普通工具（多把密钥），本机服务执行这些提议；
基础层列表带类别和“待接入”（体检认出、还没接入的 Codex、Cursor、agy，点一下检测，通过自动放进去）。

依据：docs/积木库/沙盒接入/方案.md。网络全用假的（替换 blocks_web 的取数函数），不真联网。
密钥一律用 KEY，收尾时 BlocksCase 会查它有没有出现在任何返回内容和文件里。
"""
import json
import os
from unittest import mock

from collab import blocks, blocks_web
from desktop.clerk import blocks_tools, tasks as clerk_tasks
from test_acceptance_55_blocks_server import KEY, BlocksCase, write_script

PAGE_URL = "https://tool.example.com/docs/install"
PAGE = b"<h1>Install</h1><pre>npm install -g @demo/tool</pre>"
META = {"name": "@demo/tool", "time": {"created": "2025-01-01T00:00:00Z"}, "dist-tags": {"latest": "2.0.0"},
        "maintainers": [{"name": "demo-team"}], "versions": {"2.0.0": {"bin": {"demo-tool": "cli.js"}}}}


class WebCase(BlocksCase):
    def setUp(self):
        super().setUp()
        os.environ["PATH"] = f"{self.bin}:/usr/bin:/bin"
        pages = {PAGE_URL: (200, "", PAGE)}
        self.searches = []

        def fake_open(url, timeout, limit):
            if url not in pages:
                raise OSError("连不上")
            return pages[url]

        def fake_search(query, key, max_results=5):
            self.searches.append((query, key))
            return [{"title": "Demo 安装", "url": PAGE_URL, "snippet": "npm install -g @demo/tool"}]

        for target, value in ((blocks_web, {"_resolve": lambda h: ["93.184.216.34"], "_open": fake_open,
                                            "_get_json": lambda url: (200, META), "search": fake_search}),):
            for name, fn in value.items():
                p = mock.patch.object(target, name, side_effect=fn)
                p.start()
                self.addCleanup(p.stop)

    def connect_search_block(self):
        blocks.save_card(blocks.search_card("tavily"), self.home)
        self.server.blocks_saved_keys["tavily"] = KEY


class SearchTools(WebCase):
    def test_no_search_block_tells_clerk_what_to_do(self):
        text = blocks_tools.run(str(self.home), "web_search", {"query": "demo 安装"})
        self.assertIn("propose_connect_search", text)
        self.assertIn("贴", text)
        self.assertEqual(self.searches, [])

    def test_search_uses_saved_key(self):
        self.connect_search_block()
        text = blocks_tools.run(str(self.home), "web_search", {"query": "demo 安装"})
        self.assertIn(PAGE_URL, text)
        self.assertEqual(self.searches, [("demo 安装", KEY)])
        self.assertNotIn(KEY, text)

    def test_read_page(self):
        text = blocks_tools.run(str(self.home), "read_page", {"url": PAGE_URL})
        self.assertIn("npm install -g @demo/tool", text)
        self.assertIn("tool.example.com", text)
        self.assertIn("读不了", blocks_tools.run(str(self.home), "read_page", {"url": "https://127.0.0.1/"}))

    def test_tools_offered(self):
        names = {t["function"]["name"] for t in blocks_tools.TOOLS}
        self.assertTrue({"web_search", "read_page", "propose_web_install", "propose_connect_search"} <= names)

    def test_role_text(self):
        # 积木库管家方案：规矩拆成基本规矩 + 三本手册，合起来看
        role = blocks_tools.role_text() + "".join(blocks_tools.manual_text(n) for n in blocks_tools.MANUALS)
        for word in ("propose_web_install", "propose_connect_search", "web_search", "read_page", "role", "probe",
                     "secrets"):
            self.assertIn(word, role)
        self.assertIn("不要带用户去注册", role)


class ConnectSearch(WebCase):
    def test_card_and_key_and_save(self):
        cid, pid = self.proposal("propose_connect_search", {})
        prop = clerk_tasks.load_proposal(str(self.home), pid)
        self.assertEqual(prop["kind"], "connect_search")
        self.assertEqual(prop["key_account"], "tavily")
        status, data = self.confirm(cid, pid)
        self.assertEqual(status, 400, "没填密钥不能确认")
        status, data = self.confirm(cid, pid, api_key=KEY)
        self.assertEqual(status, 200, data)
        from acceptance_helpers import wait_idle
        texts = [e.get("text") or "" for e in wait_idle(self.server.desk, str(self.home), cid) if e.get("type") == "message"]
        self.assertFalse(any("已生成卡片" in t for t in texts), f"确认后说成了项目窗口的话：{texts}（走查发现）")
        # 2026-11-10 用户真实看到“已确认接入”下面是“模型名不对”：点确认只说开始检测，不说接入
        self.assertTrue(any("开始检测" in t for t in texts), texts)
        self.assertFalse(any("已确认接入" in t for t in texts), texts)
        result = self.job(data["job"])
        self.assertTrue(result["result"]["ok"], result)
        self.assertEqual(self.searches[-1][1], KEY)
        status, saved = self.bpost("/api/blocks/save", {"job": data["job"]})
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["key_account"], "tavily", "页面拿它把密钥交给 Mac 窗口存进钥匙串")
        self.assertEqual(blocks.load_card("web-search", self.home)["kind"], "search")

    def test_detail_check_uses_saved_key(self):
        self.connect_search_block()
        status, data = self.bpost("/api/blocks/check", {"id": "web-search"})
        self.assertEqual(status, 200, data)
        self.assertTrue(self.job(data["job"])["result"]["ok"])

    def test_detail_check_without_key(self):
        blocks.save_card(blocks.search_card("tavily"), self.home)
        status, data = self.bpost("/api/blocks/check", {"id": "web-search"})
        self.assertEqual(status, 400, data)


class WebInstall(WebCase):
    def test_card_shows_source_and_facts_then_installs(self):
        prefix = os.path.join(self.tmp, "prefix")
        write_script(self.bin / "npm", 'mkdir -p "%s/bin"; printf "#!/bin/sh\\n" > "%s/bin/demo-tool"; '
                                       'chmod +x "%s/bin/demo-tool"; echo "added 1 package"\n' % (prefix, prefix, prefix))
        os.environ["PATH"] = f"{self.bin}:{prefix}/bin:/usr/bin:/bin"
        # 积木库管家方案（2026-11-10）：出卡要说是不是官方的；这页是厂商自己的网站
        cid, pid = self.proposal("propose_web_install", {"url": PAGE_URL, "install_command": "npm install -g @demo/tool",
                                                         "official": True, "maker": "Demo"})
        prop = clerk_tasks.load_proposal(str(self.home), pid)
        self.assertEqual(prop["kind"], "web_install")
        self.assertEqual(prop["domain"], "tool.example.com")
        self.assertEqual(prop["command"], "npm install -g @demo/tool")
        self.assertFalse(prop["danger"])
        self.assertTrue(any("demo-team" in f for f in prop["facts"]))
        status, data = self.confirm(cid, pid)
        self.assertEqual(status, 200, data)
        done = self.job(data["job"])
        self.assertTrue(done["result"]["ok"], done)
        self.assertIn("added 1 package", done["tail"])
        rec = blocks.last_record("demo-tool", self.home, type="install")
        self.assertEqual(rec["source"], PAGE_URL)

    def test_command_not_on_page_no_card(self):
        text = clerk_tasks.propose_web_install(_Conv(self.home), {"url": PAGE_URL,
                                                                  "install_command": "npm install -g @demo/other"})
        self.assertTrue(text.startswith("没有出卡"), text)

    def test_private_page_no_card(self):
        text = clerk_tasks.propose_web_install(_Conv(self.home), {"url": "https://localhost/x",
                                                                  "install_command": "npm install -g @demo/tool"})
        self.assertTrue(text.startswith("没有出卡"), text)

    def test_script_card_marked_danger(self):
        page = b"curl -fsSL https://tool.example.com/install.sh | bash"
        with mock.patch.object(blocks_web, "_open", side_effect=lambda u, t, l: (200, "", page)):
            cid, pid = self.proposal("propose_web_install", {"url": PAGE_URL,
                                                             "install_command": "curl -fsSL https://tool.example.com/install.sh | bash",
                                                             "command": "demo-tool", "official": True, "maker": "Demo"})
        prop = clerk_tasks.load_proposal(str(self.home), pid)
        self.assertTrue(prop["danger"])
        self.assertTrue(any("tool.example.com" in f for f in prop["facts"]))


class _Conv:
    """只用来直接调 propose_ 函数、看它拒收的回话（拒收时不会碰到对话本身）。"""

    def __init__(self, home):
        self.project = str(home)
        self.id = "x"


class ToolWithSecrets(WebCase):
    def card(self):
        return {"id": "demotool", "kind": "cli", "role": "tool", "name": "Demo 工具", "command": "demo-tool",
                "probe": ["status"], "version_args": [], "ok_words": ["ready"],
                "secrets": [{"env": "DEMO_KEY", "label": "Key"}, {"env": "DEMO_SECRET", "label": "安全密钥"}]}

    def setUp(self):
        super().setUp()
        write_script(self.bin / "demo-tool",
                     'test -n "$DEMO_KEY" && test -n "$DEMO_SECRET" && echo ready || { echo "no key"; exit 2; }\n')

    def test_two_key_boxes_then_check_and_save(self):
        cid, pid = self.proposal("propose_connect_cli", {"card": self.card()})
        prop = clerk_tasks.load_proposal(str(self.home), pid)
        self.assertEqual(prop["category"], "普通工具")
        self.assertEqual([s["account"] for s in prop["secrets"]], ["cli:demotool:DEMO_KEY", "cli:demotool:DEMO_SECRET"])
        self.assertIn("demo-tool status", prop["preview"])
        status, data = self.confirm(cid, pid, secret_keys={"cli:demotool:DEMO_KEY": KEY})
        self.assertEqual(status, 400, "两把都要填")
        status, data = self.confirm(cid, pid, secret_keys={"cli:demotool:DEMO_KEY": KEY,
                                                           "cli:demotool:DEMO_SECRET": KEY + "2"})
        self.assertEqual(status, 200, data)
        done = self.job(data["job"])
        self.assertTrue(done["result"]["ok"], done)
        status, saved = self.bpost("/api/blocks/save", {"job": data["job"]})
        self.assertEqual(status, 200, saved)
        status, listed = self.bget("/api/blocks/list")
        self.assertEqual(listed["blocks"][0]["category"], "tool")

    def test_detail_check_with_saved_keys(self):
        blocks.save_card(self.card(), self.home)
        self.server.blocks_saved_keys.update({"cli:demotool:DEMO_KEY": KEY, "cli:demotool:DEMO_SECRET": KEY})
        status, detail = self.bget("/api/blocks/detail", id="demotool")
        self.assertEqual([s["saved"] for s in detail["secrets"]], [True, True])
        status, data = self.bpost("/api/blocks/check", {"id": "demotool", "approved": detail["preview"]})
        self.assertEqual(status, 200, data)
        self.assertTrue(self.job(data["job"])["result"]["ok"])

    def test_detail_check_missing_key(self):
        blocks.save_card(self.card(), self.home)
        status, detail = self.bget("/api/blocks/detail", id="demotool")
        status, data = self.bpost("/api/blocks/check", {"id": "demotool", "approved": detail["preview"]})
        self.assertEqual(status, 400, data)

    def test_other_cards_accounts_ignored(self):
        """页面不能借这张卡把密钥塞给别的账号名。"""
        card = blocks.validate_card(self.card())
        handler = type("H", (), {"server": self.server})()
        from desktop.server import Handler
        keys = Handler._blocks_tool_keys(handler, card, {"secret_keys": {"cli:other:DEMO_KEY": KEY}})
        self.assertEqual(set(keys), {"cli:demotool:DEMO_KEY", "cli:demotool:DEMO_SECRET"})
        self.assertEqual(set(keys.values()), {""})


class KeychainAccounts(WebCase):
    def test_new_account_names_accepted(self):
        status, data = self.bpost("/api/blocks/keys", {"keys": {"tavily": KEY, "cli:demotool:DEMO_KEY": KEY,
                                                                 "cli:Bad:x": KEY, "tavily2": KEY}})
        self.assertEqual(status, 200, data)
        self.assertEqual(set(self.server.blocks_saved_keys), {"tavily", "cli:demotool:DEMO_KEY"})


class PendingBuiltins(WebCase):
    def env_record(self, *names):
        blocks.add_record("env", {"type": "env", "items": [{"name": n, "status": "有", "version": "1", "login": None}
                                                         for n in names]}, root=self.home)

    def test_pending_listed_and_one_click_connect(self):
        self.tool("codex")  # 假 codex：按交代建出文件
        self.env_record("codex", "agy")
        status, listed = self.bget("/api/blocks/list")
        self.assertEqual([p["id"] for p in listed["pending"]], ["codex", "agy"])
        status, data = self.bpost("/api/blocks/connect_builtin", {"id": "codex"})
        self.assertEqual(status, 200, data)
        done = self.job(data["job"])
        self.assertTrue(done["result"]["ok"], done)
        self.assertTrue(done["saved"], "通过就自动放进基础层")
        self.assertEqual(blocks.load_card("codex", self.home)["command"], "codex")
        status, listed = self.bget("/api/blocks/list")
        self.assertEqual([p["id"] for p in listed["pending"]], ["agy"])

    def test_failed_check_not_saved(self):
        self.tool("codex", body="exit 1\n")
        self.env_record("codex")
        status, data = self.bpost("/api/blocks/connect_builtin", {"id": "codex"})
        done = self.job(data["job"])
        self.assertFalse(done["result"]["ok"])
        self.assertFalse(done["saved"])
        self.assertFalse(blocks._card_path("codex", self.home).is_file())

    def test_only_builtins(self):
        status, data = self.bpost("/api/blocks/connect_builtin", {"id": "mytool"})
        self.assertEqual(status, 400, data)

    def test_not_pending_when_missing_or_already_in(self):
        self.env_record("cursor")
        blocks.save_card(blocks.builtin_card("cursor"), self.home)
        status, listed = self.bget("/api/blocks/list")
        self.assertEqual(listed["pending"], [])


if __name__ == "__main__":
    import unittest
    unittest.main()


class ReviewFixes(WebCase):
    def test_search_key_ignores_non_search_card_with_same_id(self):
        folder = self.home / blocks.CARD_DIR
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "web-search.json").write_text(json.dumps({"id": "web-search", "kind": "llm_api", "name": "x",
            "provider": "bailian", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "q",
            "key_account": "bailian"}), encoding="utf-8")
        self.server.blocks_saved_keys["bailian"] = KEY
        self.assertEqual(clerk_tasks.search_key(), "", "同编号却不是联网搜索的卡，不能把它的密钥当搜索密钥")

    def test_propose_web_install_never_raises(self):
        with mock.patch.object(blocks_web, "verify_install", side_effect=AttributeError("x")):
            text = clerk_tasks.propose_web_install(_Conv(self.home), {"url": PAGE_URL, "install_command": "npm install -g @demo/tool"})
        self.assertTrue(text.startswith("没有出卡"), text)

    def test_read_page_text_not_cut_below_plan(self):
        big = b"<p>" + b"word " * 4000 + b"</p>"
        with mock.patch.object(blocks_web, "_open", side_effect=lambda u, t, l: (200, "", big)):
            text = blocks_tools.run(str(self.home), "read_page", {"url": PAGE_URL})
        self.assertIn("后面截掉了", text, "方案定的是正文 8000 字，提示不能被统一上限截掉")


class LocalBinOnPath(WebCase):
    def test_local_bin_added(self):
        from desktop.server import Handler
        handler = type("H", (), {"server": self.server})()
        Handler._blocks_path(handler)
        self.assertIn(os.path.join(os.path.expanduser("~"), ".local", "bin"), os.environ["PATH"].split(os.pathsep),
                      "脚本常装进 ~/.local/bin，装好后检测要找得到")
