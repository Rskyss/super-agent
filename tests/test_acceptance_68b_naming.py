"""68b 号单：统一叫法（能力层需求共识第二节，用户 2026-11-10 定 1A、2A）。

只有“能力卡片”“任务卡片”带“卡”字：提议卡/安装卡/接入卡 → 提议，接法卡 → 接法说明，体检卡 → 体检结果，
出卡 → 出提议，那一块 → 积木块，项目窗口的需求卡 → 任务卡片。程序内部的名字（文件名、元素编号、函数名）不改。
旧记录兼容只许在 chat.js 动作摘要、desk.py 识别词表两处留旧字，那一行要带注释“旧叫法”。
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD = re.compile(r"接法卡|提议卡|安装卡|接入卡|体检卡|需求卡|出卡|那一块|哪一块|这一块|一家平台一块|旧的那块")
COMPAT = "旧叫法"
COMPAT_FILES = {"desktop/static/chat.js", "desktop/clerk/desk.py"}
SUFFIXES = {".py", ".js", ".html", ".css", ".md", ".json"}


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def scanned_files():
    files = []
    for top in ("desktop", "collab"):
        for path in (ROOT / top).rglob("*"):
            rel = path.relative_to(ROOT).as_posix()
            if (path.is_file() and path.suffix in SUFFIXES and not rel.startswith("desktop/mac/")
                    and "__pycache__" not in rel):
                files.append(rel)
    return sorted(files + ["README.md", "docs/界面改版/设计规范.md"])


class OldWordsGone(unittest.TestCase):
    def test_no_old_words(self):
        left = []
        for rel in scanned_files():
            for no, line in enumerate(read(rel).splitlines(), 1):
                if COMPAT in line and rel in COMPAT_FILES:
                    continue
                for found in OLD.finditer(line.replace("需求卡.md", "")):
                    left.append(f"{rel}:{no} {found.group(0)}：{line.strip()[:80]}")
        self.assertEqual(left, [], "还有旧叫法：\n" + "\n".join(left))

    def test_compat_marker_only_two_files(self):
        where = [rel for rel in scanned_files() if COMPAT in read(rel)]
        self.assertTrue(set(where) <= COMPAT_FILES, f"“旧叫法”只许出现在 chat.js、desk.py：{where}")

    def test_compat_lines_only_for_old_words(self):
        """带“旧叫法”的行必须真的是在认旧字，不能拿这三个字给普通的话开后门"""
        for rel in COMPAT_FILES:
            for line in read(rel).splitlines():
                if COMPAT in line:
                    self.assertRegex(line, r"出卡|提议卡|接法卡|安装卡", f"{rel}：{line.strip()}")


class NewWords(unittest.TestCase):
    def test_blocks_page(self):
        self.assertRegex(read("desktop/static/blocks.html"), r"<summary>接法说明全文</summary>")
        js = read("desktop/static/blocks.js")
        self.assertEqual(js.count("接法说明有问题"), 2, "基础层列表和详情两处")
        self.assertIn("基础层里没有这个积木块了", js)

    def test_chat_page(self):
        js = read("desktop/static/chat.js")
        self.assertIn("出了 {n} 个提议", js)
        self.assertIn("{n} 次没出成提议", js)
        self.assertIn("在新的提议上点“换一把”", js)
        self.assertIn('"已生成任务卡片 "', js)

    def test_server_messages(self):
        server = read("desktop/server.py")
        self.assertIn("基础层里没有这个积木块", server)
        self.assertIn("正在检测这个积木块", server)
        self.assertIn("基础层里已经有这个积木块", server)

    def test_butler_tool_labels_and_replies(self):
        from desktop.clerk import blocks_tools
        tools = read("desktop/clerk/blocks_tools.py")
        self.assertIn('"propose_install": "出提议"', tools)
        self.assertIn('"出提议："', tools)
        self.assertIn('"没出提议："', tools)
        self.assertIn('"没出提议"', read("desktop/clerk/desk.py"))
        self.assertIn('"没有出提议：', read("desktop/clerk/tasks.py"))
        self.assertIn("旧的那个积木块就没了", read("desktop/clerk/tasks.py"))
        role = blocks_tools.role_text() + "".join(blocks_tools.manual_text(n) for n in blocks_tools.MANUALS)
        for words in ("出提议前", "出提议后", "提议上会写", "积木块"):
            self.assertIn(words, role)
        self.assertIn("一家平台一个积木块", read("desktop/clerk/roles/积木库手册/api.md"))

    def test_blocks_errors(self):
        from collab import blocks
        with self.assertRaises(blocks.BlockError) as ctx:
            blocks.validate_card(["x"])
        self.assertIn("接法说明", str(ctx.exception))

    def test_project_window_words(self):
        opening = json.loads(read("desktop/opening.json"))
        self.assertIn("写任务卡片和方案", json.dumps(opening, ensure_ascii=False))
        manual = read("collab/README.md")
        self.assertIn("### 2. 对齐需求 → 任务卡片", manual)
        self.assertIn("新建任务卡片", manual)
        self.assertIn("需求卡.md", manual, "调度员要知道任务卡片的文件名还是 需求卡.md")
        self.assertIn("任务卡片", read("collab/规则.md"))
        self.assertIn("任务卡片", read("collab/planner.py"))
        self.assertIn("新建任务卡片", read("collab/cli.py"))

    def test_docs(self):
        self.assertIn("体检结果", read("docs/界面改版/设计规范.md"))
        self.assertIn("行政管需求和任务卡片", read("README.md"))


class BareCardWordGone(unittest.TestCase):
    """复查第 3 轮：单说一个“卡”指提议、接法说明的，用户和管家看得见的地方也要改（项目窗口的“这张卡”指任务卡片，不改）"""

    def test_visible_leftovers(self):
        for rel, words in (("desktop/clerk/tasks.py", ("起草的卡要换", 'or "一张卡")')),
                           ("desktop/clerk/desk.py", ("（出了一张卡）",)),
                           ("collab/blocks_check.py", ("这张卡没写模型",)),
                           ("README.md", ("卡上不选模型", "在卡上一把一格填"))):
            text = read(rel)
            for word in words:
                self.assertNotIn(word, text, f"{rel} 还有“{word}”")


class InternalNamesKept(unittest.TestCase):
    def test_file_name_and_ids(self):
        for rel in ("collab/cli.py", "collab/tasks.py", "collab/flow.py", "collab/dispatch.py", "collab/merge.py",
                    "collab/phase.py", "collab/planner.py", "desktop/clerk/tools.py", "desktop/clerk/desk.py",
                    "desktop/server.py"):
            self.assertIn("需求卡.md", read(rel), f"{rel} 读写的文件名不能改")
        self.assertIn('"需求卡.md": "需求共识.md"', read("collab/archive.py"))
        html = read("desktop/static/blocks.html")
        self.assertIn('id="healthCard"', html)
        self.assertIn('id="detailCard"', html)
        self.assertIn('"new-card"', read("collab/cli.py"))


class ClaimWords(unittest.TestCase):
    """改名后管家会说“提议已经弹出来了”：新说法要认出来，旧说法照样认，“提议”当动词不能误认"""

    def setUp(self):
        from desktop.clerk import desk
        self.claims, self.points = desk.claims_card, desk.points_at_card

    def test_new_words_claimed(self):
        for text in ("安装提议已经弹出来了", "接入提议出好了，点确认就行", "我已经给你出了一个提议", "我已经出好提议了。",
                     "新的提议出来了：腾讯云", "提议已弹出，请点确认", "提议已经弹出来了，你可以点确认。"):
            self.assertTrue(self.claims(text), text)

    def test_new_words_point(self):
        for text in ("提议就在上面", "把密钥填进提议里", "提议在你屏幕上", "提议给你：平台选腾讯云", "提议给你了，点确认就行",
                     "提议给你，请填密钥。"):
            self.assertTrue(self.points(text), text)

    def test_old_words_still_claimed(self):
        for text in ("提议卡已经弹出来了", "接法卡已经出好了", "安装卡已经出来了，你看一下。", "我给你出了接法卡，点确认就行"):
            self.assertTrue(self.claims(text), text)
        self.assertTrue(self.points("接法卡给你：平台选腾讯云"))
        # 复查第 3 轮：上一版把“给你”后面限死成“了/冒号”，旧说法“接法卡给你，请填密钥”就漏了
        for text in ("接法卡给你，请填密钥。", "安装卡给你，点确认就行"):
            self.assertTrue(self.points(text), text)

    def test_not_claims(self):
        for text in ("我提议你先装 Node。", "我提议先查一下电脑。", "我提议给你换个模型。", "我只是提议给你换个模型。", "其实我是提议给你换个模型。",
                     # 复查第 2 轮：前面换个词就又误认了，要按“提议后面跟着要做的事”来认动词，不是一个个排除前面的词
                     "我想提议给你换个模型。", "我刚才提议给你换个模型。", "我也提议给你换个模型。", "我个人提议给你换一个模型。",
                     "我倒是提议你先装 Node。",
                     # 复查第 4 轮：“提议把……”也是动词；前面是“我/我们/想/也/还/就……”的“提议”一律是动词，两头一起看
                     "我提议把名单发给你。", "我提议把已经生成的模型名单给你。", "我们提议先装 Node。",
                     "咱们提议换个模型。", "我还是提议用 Codex。", "我就提议一句：先装 Node。",
                     "要我出一个提议吗？",
                     "等提议出来了再填密钥", "提议里的命令已经出错了", "如果提议出来了，点确认就行"):
            self.assertFalse(self.claims(text), text)
            self.assertFalse(self.points(text), text)

    def test_confirm_reply_says_proposal(self):
        """复查第 1 轮：点确认接入后回给管家的话还写着“结果看上面那张卡”"""
        from desktop.clerk import tasks as clerk_tasks
        reply = clerk_tasks._block_result({"kind": "connect_api", "name": "腾讯云混元", "block": {}}, False)["reply"]
        self.assertNotIn("卡", reply)
        self.assertIn("提议", reply)

    def test_reminder_words(self):
        from desktop.clerk import desk
        self.assertIn("提议", desk._CLAIM_REMINDER)
        self.assertNotIn("提议卡", desk._CLAIM_REMINDER)
        self.assertNotIn("出卡", desk._CLAIM_REMINDER)
        self.assertNotIn("提议卡", desk._claim_reminder([{"id": "x", "kind": "install", "label": "装 Codex"}]))


@unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
class PageCheck(unittest.TestCase):
    def test_node_checks(self):
        proc = subprocess.run(["node", str(Path(__file__).parent / "naming_check.js")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
