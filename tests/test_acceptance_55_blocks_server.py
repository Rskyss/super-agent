"""55 号单：积木库的本机服务接口。

绑定行政（含会不会用工具）、体检、基础层列表和详情、检测、安装进度、移出基础层；
提议卡确认后真的去安装、检测、存进基础层。耗时的都走后台，页面隔一会儿来取进度。
密钥只在内存里用，不出现在任何返回内容、记录和文件里。找命令的路径、装到哪里只由程序定，页面传了也不认。
积木库的行政和项目窗口的行政各用各的模型连接，互不干扰。
"""
import json
import os
import time
from pathlib import Path

from acceptance_helpers import ScriptedClient, ServerCase, tool_call, wait_idle, wait_until, write_script
from collab import blocks, blocks_install
from collab.blocks_check import TOOL_NAME
from collab.providers import TENCENT_BASE_URL
from desktop.clerk.client import ModelError

KEY = "sk-blocks55secretkeyABCDEFGHIJ"


class FakeModel:
    """假的大模型服务：记下每次用的地址、模型、密钥。mode：ok / no_tools / bad_key。"""

    def __init__(self):
        self.mode = "ok"
        self.seen = []  # (base_url, model, key)
        self.chats = []

    def factory(self, base_url, model, api_key):
        self.seen.append((base_url, model, api_key))
        return _FakeClient(self)


class _FakeClient:
    def __init__(self, owner):
        self.owner = owner

    def chat(self, messages, on_text=None, tools=None):
        self.owner.chats.append(json.dumps(messages, ensure_ascii=False))
        if self.owner.mode == "bad_key":
            raise ModelError("模型密钥不对或已失效")
        if self.owner.mode == "bad_model":
            raise ModelError("这家服务商没有这个模型，或服务地址不对。请检查模型名和地址。")
        if tools and any(t["function"]["name"] == TOOL_NAME for t in tools):
            if self.owner.mode == "no_tools":
                return {"text": "好的", "tool_calls": [], "tokens": 1}
            return {"text": "", "tool_calls": [{"id": "t1", "name": TOOL_NAME, "arguments": '{"ok": true}'}],
                    "tokens": 1}
        if "用不超过 12 个字" in self.owner.chats[-1]:
            return {"text": "标题", "tool_calls": [], "tokens": 1}
        if on_text:
            on_text("收到")
        return {"text": "收到", "tool_calls": [], "tokens": 1}


class BlocksCase(ServerCase):
    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp) / "积木库"
        os.environ["SUPER_AGENT_BLOCKS_HOME"] = str(self.home)  # ProjectCase 收尾时会还原环境变量
        self.fake = FakeModel()
        self.server.blocks_client_factory = self.fake.factory
        self.server.terminal_path = lambda: ""  # 不去跑真的登录终端
        self.answers = []  # 所有返回内容，最后查有没有密钥

    def tearDown(self):
        text = json.dumps(self.answers, ensure_ascii=False)
        self.assertNotIn(KEY, text, "密钥出现在了返回内容里")
        if self.home.exists():
            for path in self.home.rglob("*"):
                if path.is_file():
                    self.assertNotIn(KEY, path.read_text(encoding="utf-8", errors="replace"), f"密钥写进了 {path}")
        super().tearDown()

    def bpost(self, url, body=None):
        status, data = self.post(url, body or {})
        self.answers.append(data)
        return status, data

    def bget(self, url, **query):
        status, data = self.get(url, token="口令", **query)
        self.answers.append(data)
        return status, data

    def job(self, job_id, timeout=20):
        def done():
            status, data = self.bget("/api/blocks/job", id=job_id)
            self.assertEqual(status, 200, data)
            return data if data["done"] else None
        return wait_until(done, timeout, "后台任务一直没做完")

    def bind(self, **over):
        body = {"provider": "tencent", "model": "deepseek/deepseek-flash", "api_key": KEY}
        body.update(over)
        return self.bpost("/api/blocks/bind", body)

    def tool(self, name="mytool", body=None):
        """假的 CLI：按交代在当前文件夹建出算干完活的文件。"""
        write_script(self.bin / name, body if body is not None else 'n=$(cat 题目.txt); printf "%s\\n%s\\n" $((n+1)) $(ls | wc -l) > "连通检测.txt"\n')

    def drafted(self, block_id="mytool", **over):
        card = {"id": block_id, "kind": "cli", "name": "我的工具", "command": "mytool", "run": ["{prompt}"],
                "source": "clerk"}
        card.update(over)
        return card

    def api_card(self, block_id="tongyi"):
        return {"id": block_id, "kind": "llm_api", "name": "通义", "provider": "bailian",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "key_account": "bailian", "source": "clerk"}  # 按整家接：不带模型（2026-11-10）

    # ---- 用假行政在积木库里出提议 ----
    def proposal(self, name, arguments):
        self.server.blocks_clerk = ScriptedClient([{"text": "", "tool_calls": [tool_call(name, arguments)]},
                                                  {"text": "请看确认框。"}])
        status, data = self.bpost("/api/clerk/send", {"project": str(self.home), "conversation": "",
                                                      "text": "帮我弄一下"})
        self.assertEqual(status, 200, data)
        cid = data["conversation"]
        events = wait_idle(self.server.desk, str(self.home), cid)
        found = [e for e in events if e.get("type") == "proposal"]
        self.assertTrue(found, f"没出提议：{events}")
        return cid, found[-1]["id"]

    def confirm(self, cid, pid, **extra):
        body = {"project": str(self.home), "conversation": cid, "id": pid, "decision": "confirm"}
        body.update(extra)
        return self.bpost("/api/clerk/proposal", body)


class AccessTest(BlocksCase):
    def test_needs_token(self):
        status, _ = self.get("/api/blocks/state")
        self.assertEqual(status, 403)
        status, _ = self.post("/api/blocks/scan", {}, token="不对")
        self.assertEqual(status, 403)

    def test_unknown_path(self):
        status, _ = self.bget("/api/blocks/nothing")
        self.assertEqual(status, 404)
        status, _ = self.bpost("/api/blocks/nothing")
        self.assertEqual(status, 404)


class BindTest(BlocksCase):
    def test_state_before_bind(self):
        status, data = self.bget("/api/blocks/state")
        self.assertEqual(status, 200)
        self.assertIsNone(data["setting"])
        self.assertFalse(data["connected"])
        self.assertFalse(data["installing"])

    def test_bind_ok(self):
        status, data = self.bind()
        self.assertEqual(status, 200, data)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["key_account"], "tencent", "窗口要按这个账号名把密钥存进钥匙串")
        self.assertEqual(self.fake.seen[0], (TENCENT_BASE_URL, "deepseek/deepseek-flash", KEY))
        self.assertTrue(any(TOOL_NAME in c for c in self.fake.chats), "绑定要测会不会用工具")
        _, state = self.bget("/api/blocks/state")
        self.assertTrue(state["connected"])
        self.assertEqual(state["setting"]["provider"], "tencent")
        self.assertEqual(state["setting"]["model"], "deepseek/deepseek-flash")
        self.assertEqual(state["key_account"], "tencent")
        self.assertIsNone(self.server.clerk, "项目窗口的管家不受影响")

    def test_bind_model_without_tools(self):
        self.fake.mode = "no_tools"
        status, data = self.bind()
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("当不了积木库管家", data["reason"])
        _, state = self.bget("/api/blocks/state")
        self.assertFalse(state["connected"])
        self.assertIsNone(state["setting"], "没通过就不保存")

    def test_bind_bad_key(self):
        self.fake.mode = "bad_key"
        _, data = self.bind()
        self.assertFalse(data["ok"])
        self.assertIn("密钥", data["reason"])
        self.assertFalse(self.bget("/api/blocks/state")[1]["connected"])

    def test_bind_bad_input(self):
        for body in ({"api_key": ""}, {"model": ""}, {"provider": "nobody"},
                     {"provider": "other", "base_url": "http://plain.example/v1"},
                     {"provider": "other", "base_url": ""}):
            with self.subTest(body=body):
                status, _ = self.bind(**body)
                self.assertEqual(status, 400)
        self.assertEqual(self.fake.seen, [], "参数不对时不连模型")

    def test_known_provider_address_fixed(self):
        self.bind(base_url="https://evil.example/v1")
        self.assertEqual(self.fake.seen[0][0], TENCENT_BASE_URL, "认得的服务商地址固定，密钥不能被发到别处")

    def test_other_provider(self):
        _, data = self.bind(provider="other", base_url="https://api.example.com/v1", model="m1")
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["key_account"], "other:api.example.com")

    def test_connect_after_reopen(self):
        status, _ = self.bpost("/api/blocks/connect", {"api_key": KEY})
        self.assertEqual(status, 409, "还没绑定过，不能直接连")
        self.bind()
        self.server.blocks_clerk = None  # 相当于重新打开窗口
        calls = len(self.fake.chats)
        status, data = self.bpost("/api/blocks/connect", {"api_key": KEY})
        self.assertEqual(status, 200, data)
        self.assertTrue(self.bget("/api/blocks/state")[1]["connected"])
        self.assertEqual(len(self.fake.chats), calls, "重新连上不用再花额度测一遍")
        self.assertEqual(self.bpost("/api/blocks/connect", {"api_key": ""})[0], 400)

    def test_separate_from_project_clerk(self):
        self.bind()
        blocks_client = self.server.blocks_clerk
        status, _ = self.bpost("/api/clerk/config", {"project": self.project, "provider": "tencent",
                                                     "model": "deepseek/deepseek-flash", "api_key": "sk-project"})
        self.assertEqual(status, 200)
        self.assertIs(self.server.blocks_clerk, blocks_client, "项目窗口配管家不能换掉积木库的")
        status, data = self.bpost("/api/clerk/send", {"project": str(self.home), "conversation": "",
                                                      "text": "你好，积木库"})
        self.assertEqual(status, 200, "积木库的聊天不能被“这句话属于另一个项目”拦住")
        wait_idle(self.server.desk, str(self.home), data["conversation"])
        self.assertTrue(any("你好，积木库" in c for c in self.fake.chats), "积木库的聊天用积木库的模型")
        self.bpost("/api/clerk/clear", {})
        self.assertIs(self.server.blocks_clerk, blocks_client, "项目窗口清掉管家不能清掉积木库的")


class ListTest(BlocksCase):
    def test_list_and_detail(self):
        blocks.save_card(blocks.builtin_card("codex"))
        blocks.save_card(self.api_card())
        (blocks.home() / "基础层").mkdir(parents=True, exist_ok=True)
        (blocks.home() / "基础层" / "broken.json").write_text("{坏的", encoding="utf-8")
        blocks.add_record("codex", {"type": "check", "ok": True, "status": "ok", "reason": "通了", "seconds": 3.0})
        status, data = self.bget("/api/blocks/list")
        self.assertEqual(status, 200)
        by_id = {b["id"]: b for b in data["blocks"]}
        self.assertEqual(set(by_id), {"codex", "tongyi"})
        self.assertEqual(by_id["codex"]["status"], "ok")
        self.assertEqual(by_id["codex"]["kind"], "cli")
        self.assertTrue(by_id["codex"]["verified"])
        self.assertEqual(by_id["tongyi"]["status"], "never")
        self.assertEqual([b["id"] for b in data["broken"]], ["broken"])
        self.assertTrue(data["broken"][0]["problem"])

        status, detail = self.bget("/api/blocks/detail", id="codex")
        self.assertEqual(status, 200)
        self.assertEqual(detail["card"]["command"], "codex")
        self.assertTrue(detail["verified"])
        self.assertIn("codex", detail["preview"])
        self.assertTrue(detail["last_check"]["ok"])
        status, detail = self.bget("/api/blocks/detail", id="tongyi")
        self.assertEqual(detail["key_account"], "bailian")
        self.assertEqual(self.bget("/api/blocks/detail", id="nothing")[0], 404)
        self.assertEqual(self.bget("/api/blocks/detail", id="../x")[0], 400)

    def test_remove(self):
        blocks.save_card(blocks.builtin_card("codex"))
        status, data = self.bpost("/api/blocks/remove", {"id": "codex"})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.bget("/api/blocks/list")[1]["blocks"], [])
        self.assertEqual(self.bpost("/api/blocks/remove", {"id": "codex"})[0], 404)
        self.assertEqual(self.bpost("/api/blocks/remove", {"id": "../x"})[0], 400)


class ScanTest(BlocksCase):
    def test_scan_in_background(self):
        terminal = Path(self.tmp) / "终端"
        terminal.mkdir()
        write_script(terminal / "agy", "echo 1.0\n")
        (self.bin / "agy").unlink()
        os.environ["PATH"] = f"{self.bin}:/usr/bin:/bin"
        self.server.terminal_path = lambda: str(terminal)
        status, data = self.bpost("/api/blocks/scan")
        self.assertEqual(status, 200, data)
        result = self.job(data["job"])["result"]
        items = {i["label"]: i for i in result["items"]}
        self.assertIn("Node", items)
        self.assertIn("agy", items)
        # 验收后改（Claude，2026-11-08）：体检前把终端的路径补进超级助手，终端里装的工具直接就能找到，不用重开
        self.assertEqual(items["agy"]["status"], "有", "用上了终端的路径")
        self.assertEqual(items["agy"]["version"], "1.0")
        self.assertIn(str(terminal), os.environ["PATH"])

    def test_unknown_job(self):
        self.assertEqual(self.bget("/api/blocks/job", id="nothing")[0], 404)


class CheckTest(BlocksCase):
    def test_check_api_card(self):
        blocks.save_card(self.api_card())
        status, data = self.bpost("/api/blocks/check", {"id": "tongyi", "api_key": KEY})
        self.assertEqual(status, 200, data)
        result = self.job(data["job"])["result"]
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.fake.seen[-1][2], KEY)
        self.assertTrue(blocks.last_record("tongyi", type="check")["ok"])
        self.assertEqual(self.bpost("/api/blocks/check", {"id": "tongyi"})[0], 400, "大模型 API 要带密钥")

    def test_check_unverified_cli_needs_approval(self):
        self.tool()
        blocks.save_card(self.drafted())
        status, _ = self.bpost("/api/blocks/check", {"id": "mytool"})
        self.assertEqual(status, 400, "没核实的卡，要先把完整命令给用户看")
        preview = self.bget("/api/blocks/detail", id="mytool")[1]["preview"]
        self.assertIn(str(self.bin / "mytool"), preview)
        _, data = self.bpost("/api/blocks/check", {"id": "mytool", "approved": preview})
        result = self.job(data["job"])["result"]
        self.assertTrue(result["ok"], result)
        _, data = self.bpost("/api/blocks/check", {"id": "mytool", "approved": preview + " --改过"})
        result = self.job(data["job"])["result"]
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "changed")

    def test_check_missing_card(self):
        self.assertEqual(self.bpost("/api/blocks/check", {"id": "nothing"})[0], 404)

    def test_client_path_ignored(self):
        evil = Path(self.tmp) / "冒名"
        evil.mkdir()
        write_script(evil / "mytool", f'touch "{Path(self.tmp) / "冒名跑了"}"; n=$(cat 题目.txt); printf "%s\\n%s\\n" $((n+1)) $(ls | wc -l) > "连通检测.txt"\n')
        self.tool()
        blocks.save_card(self.drafted())
        preview = self.bget("/api/blocks/detail", id="mytool")[1]["preview"]
        _, data = self.bpost("/api/blocks/check", {"id": "mytool", "approved": preview, "path": str(evil),
                                                   "root": str(evil)})
        self.job(data["job"])
        self.assertFalse((Path(self.tmp) / "冒名跑了").exists(), "找命令的路径只由程序定")


class InstallTest(BlocksCase):
    def setUp(self):
        super().setUp()
        (self.bin / "codex").unlink()
        os.environ["PATH"] = f"{self.bin}:/usr/bin:/bin"
        self.runs = Path(self.tmp) / "npm.log"
        write_script(self.bin / "npm", f'echo "$@ prefix=$npm_config_prefix" >> "{self.runs}"\n'
                                       'echo "added 1 package"\n')

    def test_confirm_runs_install(self):
        cid, pid = self.proposal("propose_install", {"install_id": "codex-npm"})
        status, data = self.confirm(cid, pid, path="/tmp/冒名", prefix="/tmp/冒名")
        self.assertEqual(status, 200, data)
        self.assertTrue(data["job"])
        done = self.job(data["job"])
        self.assertEqual(done["kind"], "install")
        self.assertTrue(done["result"]["ok"], done)
        self.assertIn("added 1 package", done["tail"])
        self.assertEqual(self.runs.read_text().split(), ["install", "-g", "@openai/codex", "prefix="],
                         "页面传的路径、装到哪里都不认")
        self.assertIsNotNone(blocks.last_record("codex", type="install"))

        status, again = self.confirm(cid, pid)
        self.assertEqual(status, 200)
        time.sleep(0.5)
        self.assertEqual(len(self.runs.read_text().splitlines()), 1, "同一个提议点两次只装一次")

    def test_one_install_at_a_time(self):
        write_script(self.bin / "npm", f'echo run >> "{self.runs}"; /bin/sleep 2\n')
        cid, pid = self.proposal("propose_install", {"install_id": "codex-npm"})
        _, data = self.confirm(cid, pid)
        wait_until(lambda: self.bget("/api/blocks/state")[1]["installing"], 5, "安装没开始")
        cid2, pid2 = self.proposal("propose_install", {"install_id": "codex-npm"})
        status, answer = self.confirm(cid2, pid2)
        self.assertEqual(status, 409, answer)
        self.assertIn("正在安装", answer["error"])
        self.job(data["job"])
        self.assertEqual(len(self.runs.read_text().splitlines()), 1)


class ConnectTest(BlocksCase):
    def test_cli_confirm_check_save(self):
        self.tool()
        cid, pid = self.proposal("propose_connect_cli", {"card": self.drafted()})
        status, data = self.confirm(cid, pid)
        self.assertEqual(status, 200, data)
        done = self.job(data["job"])
        self.assertEqual(done["kind"], "check")
        self.assertTrue(done["result"]["ok"], done)
        self.assertEqual(self.bget("/api/blocks/list")[1]["blocks"], [], "检测通过后还要用户点“放进基础层”")
        status, saved = self.bpost("/api/blocks/save", {"job": data["job"]})
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["id"], "mytool")
        self.assertEqual(blocks.load_card("mytool")["command"], "mytool")
        self.assertEqual(self.bpost("/api/blocks/save", {"job": data["job"]})[0], 200, "点两次不出错")

    def test_failed_check_cannot_save(self):
        self.tool(body="exit 1\n")
        cid, pid = self.proposal("propose_connect_cli", {"card": self.drafted()})
        _, data = self.confirm(cid, pid)
        self.assertFalse(self.job(data["job"])["result"]["ok"])
        self.assertEqual(self.bpost("/api/blocks/save", {"job": data["job"]})[0], 409)
        self.assertEqual(self.bpost("/api/blocks/save", {"job": "nothing"})[0], 404)

    def test_check_recorded_after_confirm(self):
        self.tool(body="exit 1\n")
        cid, pid = self.proposal("propose_connect_cli", {"card": self.drafted()})
        _, data = self.confirm(cid, pid)
        self.job(data["job"])
        record = blocks.last_record("mytool", type="check")
        self.assertIsNotNone(record, "检测要留记录：“连续 3 次就停”靠它算")
        self.assertFalse(record["ok"])

    def test_same_id_saved_in_between(self):
        self.tool()
        cid, pid = self.proposal("propose_connect_cli", {"card": self.drafted()})
        _, data = self.confirm(cid, pid)
        self.job(data["job"])
        blocks.save_card(self.drafted(name="别处存的"))
        status, answer = self.bpost("/api/blocks/save", {"job": data["job"]})
        self.assertEqual(status, 409, "提议之后别处存了同编号的卡，要重新提议，不能悄悄盖掉")
        self.assertEqual(blocks.load_card("mytool")["name"], "别处存的")

    def test_replaces_overwrites(self):
        self.tool()
        blocks.save_card(self.drafted(name="旧的"))
        cid, pid = self.proposal("propose_connect_cli", {"card": self.drafted(name="新的")})
        _, data = self.confirm(cid, pid)
        self.assertTrue(self.job(data["job"])["result"]["ok"])
        status, _ = self.bpost("/api/blocks/save", {"job": data["job"]})
        self.assertEqual(status, 200)
        self.assertEqual(blocks.load_card("mytool")["name"], "新的")

    def test_api_confirm_needs_key(self):
        cid, pid = self.proposal("propose_connect_api", {"provider": "bailian", "model": "qwen-plus", "name": "通义"})
        status, _ = self.confirm(cid, pid)
        self.assertEqual(status, 400, "接大模型 API 要先在密钥框里填密钥")
        status, data = self.confirm(cid, pid, api_key=KEY)
        self.assertEqual(status, 200, data)
        done = self.job(data["job"])
        self.assertTrue(done["result"]["ok"], done)
        self.assertEqual(self.fake.seen[-1][2], KEY)
        status, saved = self.bpost("/api/blocks/save", {"job": data["job"]})
        self.assertEqual(status, 200, saved)
        self.assertEqual(saved["key_account"], "bailian")
        self.assertEqual(blocks.load_card(saved["id"])["kind"], "llm_api")

    def test_project_window_proposals_unchanged(self):
        status, data = self.bpost("/api/clerk/proposal", {"project": self.project, "conversation": "x", "id": "nothing",
                                                          "decision": "confirm"})
        self.assertEqual(status, 404)
        self.assertNotIn("job", data)


if __name__ == "__main__":
    import unittest
    unittest.main()
