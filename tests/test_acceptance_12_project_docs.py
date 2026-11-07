"""行政自己读项目自带的说明，回答“这个项目是干什么的”，并起草《项目说明》。"""
import json
from pathlib import Path

from acceptance_helpers import ProjectCase, ScriptedClient, ServerCase, STATIC, needs_node, run_node, tool_call, wait_idle
from collab.workspace import PROFILE_TEMPLATE
from desktop.clerk.desk import ROLE
from desktop.clerk.tools import TOOLS, run_tool

GOOD = ("# 项目说明\n\n- 这个项目是做什么的：清理 Mac\n- 怎么启动：npm start\n- 怎么测试（测试命令）：npm test\n"
        "- 禁区（哪些不能动）：无\n- 技术栈：Electron\n")


class ReadDocs(ProjectCase):
    def write(self, name, text):
        path = Path(self.project) / name
        path.write_text(text, encoding="utf-8")
        return path

    def read(self):
        return run_tool(self.project, "read_project_docs", {})

    def test_reads_the_usual_files_and_the_top_level_names(self):
        self.write("README.md", "这是清理工具")
        self.write("CLAUDE.md", "规矩：不动主分支")
        self.write("AGENTS.md", "分工说明")
        self.write("package.json", json.dumps({"name": "cleanmac", "description": "Mac 清理", "scripts": {"x": "y"}}))
        got = self.read()
        for words in ("这是清理工具", "不动主分支", "分工说明", "Mac 清理", "src"):
            self.assertIn(words, got)

    def test_no_documents_still_shows_the_folder(self):
        self.assertIn("src", self.read())

    def test_secrets_never_leave(self):
        self.write(".env", "SECRET=环境里的密钥")
        self.write("README.md", "正文\napi_key = abc123456\nPASSWORD: 不能看\n令牌 sk-abcdefghijklmnopqrstuvwx 结束\n")
        got = self.read()
        self.assertIn("正文", got)
        for bad in ("环境里的密钥", "abc123456", "不能看", "sk-abcdefghijklmnopqrstuvwx", ".env"):
            self.assertNotIn(bad, got)

    def test_links_are_not_followed(self):
        outside = Path(self.tmp) / "外面.md"
        outside.write_text("外面的内容", encoding="utf-8")
        (Path(self.project) / "README.md").symlink_to(outside)
        self.assertNotIn("外面的内容", self.read())

    def test_long_and_broken_files_are_safe(self):
        self.write("README.md", "长" * 20000)
        (Path(self.project) / "CLAUDE.md").write_bytes(b"\xff\xfe abc")
        got = self.read()
        self.assertLessEqual(len(got), 6000)
        self.assertIn("src", got)

    def test_tool_takes_no_path(self):
        tool = next(t for t in TOOLS if t["function"]["name"] == "read_project_docs")
        self.assertEqual(tool["function"]["parameters"]["properties"], {})

    def test_role_tells_it_to_read_first(self):
        self.assertIn("read_project_docs", ROLE)
        self.assertIn("propose_profile", ROLE)


class SaveProfile(ServerCase):
    def setUp(self):
        super().setUp()
        self.path = Path(self.project) / "docs" / "项目说明.md"

    def propose(self, text=GOOD):
        self.server.clerk = ScriptedClient([{"text": "", "tool_calls": [tool_call("propose_profile", {"profile": text})]},
                                            {"text": "看一下下面的草稿。"}])
        desk = self.server.desk
        cid = desk.send(self.project, "", "帮我写项目说明")
        return cid, wait_idle(desk, self.project, cid)

    def shown(self, events):
        return [e for e in events if e["type"] == "proposal"]

    def decide(self, cid, pid, decision):
        return self.post("/api/clerk/proposal", {"project": self.project, "conversation": cid, "id": pid, "decision": decision})

    def test_confirm_saves_it(self):
        cid, events = self.propose()
        box = self.shown(events)[0]
        self.assertEqual((box["kind"], box["profile"]), ("profile", GOOD))
        self.assertFalse(self.path.exists(), "用户确认前不写文件")
        self.assertEqual(self.decide(cid, box["id"], "confirm")[0], 200)
        self.assertEqual(self.path.read_text(encoding="utf-8"), GOOD)
        words = [e.get("text") for e in self.server.desk.events(self.project, cid, 0, wait=0)["events"]]
        self.assertIn("项目说明已保存。", words)

    def test_cancel_writes_nothing(self):
        cid, events = self.propose()
        self.decide(cid, self.shown(events)[0]["id"], "cancel")
        self.assertFalse(self.path.exists())

    def test_blank_template_may_be_replaced(self):
        self.path.parent.mkdir()
        self.path.write_text(PROFILE_TEMPLATE, encoding="utf-8")
        cid, events = self.propose()
        self.decide(cid, self.shown(events)[0]["id"], "confirm")
        self.assertEqual(self.path.read_text(encoding="utf-8"), GOOD)

    def test_user_content_is_never_overwritten(self):
        self.path.parent.mkdir()
        self.path.write_text("我自己写的\n", encoding="utf-8")
        _, events = self.propose()
        self.assertEqual(self.shown(events), [])
        self.assertEqual(self.path.read_text(encoding="utf-8"), "我自己写的\n")

    def test_user_content_written_after_the_proposal_is_kept(self):
        cid, events = self.propose()
        self.path.parent.mkdir()
        self.path.write_text("后来写的\n", encoding="utf-8")
        status, _ = self.decide(cid, self.shown(events)[0]["id"], "confirm")
        self.assertEqual(status, 409)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "后来写的\n")

    def test_incomplete_draft_is_refused(self):
        for bad in ("", "只有一句话", GOOD.replace("npm test", "（待填）")):
            _, events = self.propose(bad)
            self.assertEqual(self.shown(events), [])

    def test_position_survives_reopening(self):
        from desktop.clerk.desk import ClerkDesk
        cid, _ = self.propose()
        again = ClerkDesk(lambda: self.server.clerk).events(self.project, cid, 0, wait=0)["events"]
        self.assertEqual(self.shown(again)[0]["profile"], GOOD)


@needs_node()
class Page(ProjectCase):
    def test_box_is_shown_with_plain_words(self):
        source = (STATIC / "chat.js").read_text(encoding="utf-8")
        self.assertIn("保存项目说明？", source)
        done = run_node("const C = require(" + json.dumps(str(STATIC / "chat.js")) + ");" + """
          const m = C.modelFrom([{ type: 'proposal', id: 'a', kind: 'profile', profile: '内容', state: 'open', card: '' }]);
          const b = m.blocks[m.blocks.length - 1];
          if (b.profile !== '内容' || b.proposalKind !== 'profile') throw new Error('没有带上内容');
        """)
        self.assertEqual(done.returncode, 0, done.stderr)
