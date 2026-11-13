"""55 号单验收和复查后的修改：本机服务接口。

找命令的路径前后一致（出提议、检测都用同一份）；绑定出错不露密钥、没连上不保存；连模型带上服务商；
接口出错要回话不能断开；项目窗口改不了积木库的行政设置；两个安装同时确认只装一个；
每个安装任务只显示自己的输出；“命令变了”不靠找字眼；任务表有上限；畸形路径不断开连接。
"""
import json
import os
import threading
from pathlib import Path

from acceptance_helpers import write_script
from collab import blocks, blocks_check
from test_acceptance_55_blocks_server import KEY, BlocksCase


class PathTest(BlocksCase):
    def test_tool_only_in_terminal_path(self):
        terminal = Path(self.tmp) / "终端"
        terminal.mkdir()
        write_script(terminal / "mytool", 'n=$(cat 题目.txt); printf "%s\\n%s\\n" $((n+1)) $(ls | wc -l) > "连通检测.txt"\n')
        os.environ["PATH"] = f"{self.bin}:/usr/bin:/bin"
        self.server.terminal_path = lambda: str(terminal)
        cid, pid = self.proposal("propose_connect_cli", {"card": self.drafted()})
        _, data = self.confirm(cid, pid)
        result = self.job(data["job"])["result"]
        self.assertTrue(result["ok"], f"出提议和检测要用同一份路径，不能每次都说“命令变了”：{result}")
        preview = self.bget("/api/blocks/detail", id="mytool")
        self.assertEqual(preview[0], 404)

    def test_detail_preview_matches_check(self):
        terminal = Path(self.tmp) / "终端"
        terminal.mkdir()
        write_script(terminal / "mytool", 'n=$(cat 题目.txt); printf "%s\\n%s\\n" $((n+1)) $(ls | wc -l) > "连通检测.txt"\n')
        os.environ["PATH"] = f"{self.bin}:/usr/bin:/bin"
        self.server.terminal_path = lambda: str(terminal)
        blocks.save_card(self.drafted())
        preview = self.bget("/api/blocks/detail", id="mytool")[1]["preview"]
        self.assertIn(str(terminal / "mytool"), preview)
        _, data = self.bpost("/api/blocks/check", {"id": "mytool", "approved": preview})
        self.assertTrue(self.job(data["job"])["result"]["ok"])

    def test_terminal_path_not_read_every_time(self):
        calls = []
        self.server.terminal_path = lambda: calls.append(1) or ""
        blocks.save_card(blocks.builtin_card("codex"))
        for _ in range(5):
            self.bget("/api/blocks/detail", id="codex")
        self.assertLessEqual(len(calls), 1, "读终端路径要几秒，短时间内不重复读")


class BindTest(BlocksCase):
    def test_error_masked_and_not_saved(self):
        calls = []

        def factory(base_url, model, api_key):
            calls.append(1)
            if len(calls) > 1:
                raise RuntimeError(f"Invalid header value b'Bearer {api_key}'")
            return self.fake.factory(base_url, model, api_key)
        self.server.blocks_client_factory = factory
        status, data = self.bind()
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        _, state = self.bget("/api/blocks/state")
        self.assertIsNone(state["setting"], "没连上就不保存")
        self.assertFalse(state["connected"])

    def test_default_client_knows_provider(self):
        seen = []
        original = blocks_check._default_client

        def recording(base_url, model, api_key, provider=""):
            seen.append(provider)
            return self.fake.factory(base_url, model, api_key)
        self.server.blocks_client_factory = None
        blocks_check._default_client = recording
        try:
            self.bind()
            self.server.blocks_clerk = None
            self.bpost("/api/blocks/connect", {"api_key": KEY})
        finally:
            blocks_check._default_client = original
        self.assertTrue(seen)
        self.assertEqual(set(seen), {"tencent"}, "腾讯云、百炼要关“思考”，连接要知道是哪家")

    def test_check_does_not_block_a_card_named_clerk(self):
        blocks.save_card(self.api_card("clerk"))
        status, data = self.bind()
        self.assertTrue(data["ok"], data)


class ErrorTest(BlocksCase):
    def test_broken_setting_file_answers(self):
        folder = self.home / ".collab"
        folder.mkdir(parents=True)
        (folder / "配置.json").write_text("{坏的", encoding="utf-8")
        status, data = self.bget("/api/blocks/state")
        self.assertIn(status, (409, 500))
        self.assertIn("error", data)
        status, data = self.bpost("/api/blocks/connect", {"api_key": KEY})
        self.assertIn(status, (409, 500))
        self.assertIn("error", data)

    def test_malformed_project_answers(self):
        status, data = self.bpost("/api/clerk/send", {"project": "a\0b", "conversation": "", "text": "x"})
        self.assertEqual(status, 400, data)


class ProjectWindowTest(BlocksCase):
    def test_setting_cannot_change_blocks_clerk(self):
        self.bind()
        status, _ = self.bpost("/api/clerk/setting", {"project": str(self.home), "provider": "bailian",
                                                      "model": "qwen-plus"})
        self.assertEqual(status, 400, "积木库的行政只能在积木库里绑定（要测会不会用工具）")
        self.assertEqual(self.bget("/api/blocks/state")[1]["setting"]["provider"], "tencent")


def _wait_install_idle(case, server=None, limit=20.0):
    """“正在安装”是整个程序共用的一个标记：前面测试起的安装在后台还没收尾，这里就会被当成“正在装别的”
    （流程修理第二轮 ㊸：单跑都过、连着跑偶尔两次确认都回 409）。等这份服务的后台任务都做完、标记放下；等不到就报错。"""
    import time
    from collab import blocks_install
    deadline = time.time() + limit
    def busy():
        jobs = list((getattr(server, "blocks_jobs", None) or {}).values()) if server is not None else []
        return blocks_install.installing() or any(not j.get("done") for j in jobs)
    while busy() and time.time() < deadline:
        time.sleep(0.1)
    case.assertFalse(busy(), "上一次安装 20 秒还没收尾")


class InstallTest(BlocksCase):
    def tearDown(self):
        _wait_install_idle(self, self.server)
        super().tearDown()

    def setUp(self):
        super().setUp()
        _wait_install_idle(self)
        (self.bin / "codex").unlink()
        os.environ["PATH"] = f"{self.bin}:/usr/bin:/bin"
        self.runs = Path(self.tmp) / "npm.log"

    def test_two_confirms_at_once(self):
        write_script(self.bin / "npm", f'echo run >> "{self.runs}"; /bin/sleep 2\n')
        first = self.proposal("propose_install", {"install_id": "codex-npm"})
        second = self.proposal("propose_install", {"install_id": "codex-npm"})
        answers = []
        threads = [threading.Thread(target=lambda c=c: answers.append(self.confirm(*c))) for c in (first, second)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        statuses = sorted(status for status, _ in answers)
        self.assertEqual(statuses, [200, 409], answers)
        job = next(data["job"] for status, data in answers if status == 200)
        self.job(job)
        self.assertEqual(len(self.runs.read_text().splitlines()), 1)

    def test_each_job_keeps_its_own_output(self):
        write_script(self.bin / "npm", 'echo "第一次的输出"\n')
        cid, pid = self.proposal("propose_install", {"install_id": "codex-npm"})
        first = self.confirm(cid, pid)[1]["job"]
        self.job(first)
        write_script(self.bin / "npm", 'echo "第二次的输出"\n')
        cid, pid = self.proposal("propose_install", {"install_id": "codex-npm"})
        second = self.confirm(cid, pid)[1]["job"]
        self.job(second)
        tail = self.bget("/api/blocks/job", id=first)[1]["tail"]
        self.assertIn("第一次的输出", tail)
        self.assertNotIn("第二次的输出", tail)

    def test_repeat_confirm_returns_same_job(self):
        write_script(self.bin / "npm", f'echo run >> "{self.runs}"\n')
        cid, pid = self.proposal("propose_install", {"install_id": "codex-npm"})
        answers = []
        threads = [threading.Thread(target=lambda: answers.append(self.confirm(cid, pid))) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        jobs = {data.get("job") for _, data in answers}
        self.assertEqual(len(jobs), 1, f"同一个提议点两次，拿到的是同一个任务：{answers}")
        self.assertTrue(jobs.pop())


class ChangedTest(BlocksCase):
    def test_changed_not_by_wording(self):
        self.tool()
        blocks.save_card(self.drafted(name="确认对不上的工具"))
        preview = self.bget("/api/blocks/detail", id="mytool")[1]["preview"]
        _, data = self.bpost("/api/blocks/check", {"id": "mytool", "approved": preview})
        result = self.job(data["job"])["result"]
        self.assertTrue(result["ok"], result)


class JobsTest(BlocksCase):
    def test_finished_jobs_capped(self):
        blocks.save_card(self.api_card())
        for _ in range(130):
            _, data = self.bpost("/api/blocks/check", {"id": "tongyi", "api_key": KEY})
            self.job(data["job"])
        self.assertLessEqual(len(self.server.blocks_jobs), 100, "做完的任务不能无限攒着")


class HomeTest(BlocksCase):
    def test_empty_project_is_not_blocks_home(self):
        from desktop.clerk.blocks_tools import is_blocks_home
        os.environ["SUPER_AGENT_BLOCKS_HOME"] = "."
        self.assertFalse(is_blocks_home(""))
        self.assertFalse(is_blocks_home("a\0b"))


class GrokFindingsTest(BlocksCase):
    """Grok 复查查出的：聊天出错时露密钥；提议编号越界；同编号同时保存互相覆盖。"""

    def test_chat_error_and_partial_masked(self):
        from desktop.clerk.client import ModelError

        class Leaky:
            api_key = KEY

            def chat(self, messages, on_text=None, tools=None):
                if on_text:
                    on_text(f"半截回复 {KEY}")
                raise ModelError(f"Invalid header value b'Bearer {KEY}'", partial=f"半截回复 {KEY}")
        self.server.blocks_clerk = Leaky()
        _, data = self.bpost("/api/clerk/send", {"project": str(self.home), "conversation": "", "text": "你好"})
        from acceptance_helpers import wait_until
        events = wait_until(lambda: (lambda e: e if any(x.get("type") == "turn_done" for x in e) else None)(
            self.server.desk.events(str(self.home), data["conversation"], 0, wait=0)["events"]), 8)
        self.answers.append(events)  # 收尾时查返回内容和资料夹里的文件有没有密钥

    def test_proposal_id_cannot_leave_folder(self):
        outside = Path(self.tmp) / "外面"
        outside.mkdir()
        cid, real = self.proposal("propose_install", {"install_id": "codex-npm"})
        planted = json.loads((self.home / ".collab" / "行政提议" / f"{real}.json").read_text(encoding="utf-8"))
        pid = "../../../外面/p"
        planted["id"] = pid
        (outside / "p.json").write_text(json.dumps(planted, ensure_ascii=False), encoding="utf-8")
        for project in (str(self.home), self.project):
            status, _ = self.bpost("/api/clerk/proposal", {"project": project, "conversation": cid, "id": pid,
                                                           "decision": "confirm"})
            self.assertEqual(status, 404, "提议编号只能是程序生成的那种，不能带路径")
        self.assertEqual(json.loads((outside / "p.json").read_text(encoding="utf-8"))["state"], "open")

    def test_same_id_saved_at_once(self):
        self.tool()
        first = self.proposal("propose_connect_cli", {"card": self.drafted(name="甲")})
        job1 = self.confirm(*first)[1]["job"]
        self.job(job1)
        second = self.proposal("propose_connect_cli", {"card": self.drafted(name="乙")})
        job2 = self.confirm(*second)[1]["job"]
        self.job(job2)
        answers = []
        threads = [threading.Thread(target=lambda j=j: answers.append(self.bpost("/api/blocks/save", {"job": j})))
                   for j in (job1, job2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(status for status, _ in answers), [200, 409], answers)


class ScrubTest(BlocksCase):
    """第三轮复查：行政自己的正常回话不能被当成密钥打码。"""

    def test_normal_reply_kept(self):
        words = ("在终端里运行 export OPENAI_API_KEY=<你的密钥> 再用 risk-assessment-toolkit；"
                 "设置 api_key: 在钥匙串里保存；编号 123e4567-e89b-12d3-a456-426614174000")
        from acceptance_helpers import ScriptedClient, wait_idle
        self.server.blocks_clerk = ScriptedClient([{"text": words}])
        _, data = self.bpost("/api/clerk/send", {"project": str(self.home), "conversation": "", "text": "怎么配"})
        events = wait_idle(self.server.desk, str(self.home), data["conversation"])
        said = [e["text"] for e in events if e.get("type") == "message"]
        self.assertIn(words, said)
