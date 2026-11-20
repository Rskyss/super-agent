"""规则包第③步（docs/规则包/方案-第③步.md）：派活时的具体交代也放进 agents/交代/，程序只往空位里填东西。

交代文件的写法（写在 agents/README.md“交代”一节）：`---` 以上是给人看的说明，以下是发给模型的话；
连着的几行直接接起来（中文不用空格），一个空行＝换一行；`{空位}` 由程序每次填。
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from collab import rulebook

AGENTS = Path(__file__).resolve().parents[1] / "agents"


def make(root: Path, name: str, rel: str, slots: str, text: str):
    index = root / "README.md"
    body = index.read_text(encoding="utf-8")
    row = f"| {name} | {rel} | {slots} |\n"
    head = "| 交代 | 文件 | 空位 |\n|---|---|---|\n"
    if head not in body:
        body += "\n## 交代\n\n" + head
    body = body.replace(head, head + row, 1)
    index.write_text(body, encoding="utf-8")
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


class Say(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "agents"
        shutil.copytree(AGENTS, self.root)

    def test_fills_slots_and_joins_lines(self):
        make(self.root, "试/甲", "交代/试/甲.md", "卡片、命令",
             "# 甲\n\n给人看的说明。\n\n---\n按任务卡 {卡片} 写测试，\n只跑 {命令}。\n\n第二段。\n")
        self.assertEqual(rulebook.say("试/甲", root=self.root, 卡片="a.md", 命令="t"),
                         "按任务卡 a.md 写测试，只跑 t。\n第二段。")

    def test_two_blank_lines_make_two_newlines(self):
        make(self.root, "试/乙", "交代/试/乙.md", "—", "---\n甲\n\n\n乙\n")
        self.assertEqual(rulebook.say("试/乙", root=self.root), "甲\n\n乙")

    def test_json_like_braces_are_left_alone(self):
        make(self.root, "试/丙", "交代/试/丙.md", "—", '---\n每项写 {"编号": 数字}。\n')
        self.assertEqual(rulebook.say("试/丙", root=self.root), '每项写 {"编号": 数字}。')

    def test_missing_file_is_written_into_the_text(self):
        make(self.root, "试/丁", "交代/试/丁.md", "—", "---\n甲\n")
        (self.root / "交代/试/丁.md").unlink()
        text = rulebook.say("试/丁", root=self.root)
        self.assertIn("交代文件缺失", text)
        self.assertIn("交代/试/丁.md", rulebook.missing_files(root=self.root))

    def test_unregistered_name_is_reported(self):
        self.assertIn("交代文件缺失", rulebook.say("没有这份", root=self.root))

    def test_unfilled_slot_is_reported(self):
        make(self.root, "试/戊", "交代/试/戊.md", "卡片", "---\n按 {卡片} 做。\n")
        self.assertIn("交代文件有问题", rulebook.say("试/戊", root=self.root))


class SayChecksRegisteredSlots(unittest.TestCase):
    """Codex 复查 T1 第 1、3 条：按登记表核对空位；格式坏了不把给人看的说明发给模型。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "agents"
        shutil.copytree(AGENTS, self.root)

    def test_registered_slot_not_given_is_reported(self):
        make(self.root, "试/甲", "交代/试/甲.md", "卡片、命令", "---\n按 {卡片} 和 {命令} 做。\n")
        self.assertIn("交代文件有问题", rulebook.say("试/甲", root=self.root, 卡片="a"))

    def test_unregistered_slot_given_is_reported(self):
        make(self.root, "试/乙", "交代/试/乙.md", "—", "---\n按 {别的} 做。\n")
        self.assertIn("交代文件有问题", rulebook.say("试/乙", root=self.root, 别的="x"))

    def test_missing_separator_does_not_leak_notes(self):
        make(self.root, "试/丙", "交代/试/丙.md", "—", "# 给人看的标题\n\n给人看的说明\n  ---\n正文\n")
        text = rulebook.say("试/丙", root=self.root)
        self.assertNotIn("给人看的", text)
        self.assertIn("交代文件有问题", text)
        self.assertTrue(any("丙.md" in p for p in rulebook.health(root=self.root)))

    def test_empty_body_is_caught(self):
        make(self.root, "试/丁", "交代/试/丁.md", "—", "# 标题\n\n说明\n---\n\n")
        self.assertTrue(any("丁.md" in p for p in rulebook.health(root=self.root)))


class HealthChecksSlots(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "agents"
        shutil.copytree(AGENTS, self.root)

    def test_slot_removed_from_file_is_caught(self):
        make(self.root, "试/甲", "交代/试/甲.md", "卡片、命令", "---\n按 {卡片} 做。\n")
        self.assertTrue(any("命令" in p for p in rulebook.health(root=self.root)))

    def test_unknown_slot_in_file_is_caught(self):
        make(self.root, "试/甲", "交代/试/甲.md", "卡片", "---\n按 {卡片} 和 {别的} 做。\n")
        self.assertTrue(any("别的" in p for p in rulebook.health(root=self.root)))

    def test_message_files_count_as_registered(self):
        make(self.root, "试/甲", "交代/试/甲.md", "—", "---\n甲\n")
        self.assertFalse(any("没登记" in p for p in rulebook.health(root=self.root)))


if __name__ == "__main__":
    unittest.main()


class EveryToolDescribed(unittest.TestCase):
    """第③步：工具说明搬进 agents/交代/行政/工具说明.md；表里删了一行，工具就没说明了，要报出来。"""

    def test_clerk_tools(self):
        from desktop.clerk import tools
        for spec in tools.TOOLS + [tools.PROPOSE_REVISE]:
            fn = spec["function"]
            self.assertTrue(fn["description"], fn["name"])
            self.assertNotIn("工具说明缺失", fn["description"], fn["name"])
            for pname, pspec in fn["parameters"].get("properties", {}).items():
                self.assertTrue(pspec.get("description"), f"{fn['name']}.{pname}")
                self.assertNotIn("工具说明缺失", pspec["description"], f"{fn['name']}.{pname}")

    def test_deleted_row_is_visible_not_silent(self):
        # Codex 复查 T2～T6 第 2 条：表里删了一行，不能悄悄变成空说明
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "agents"
        shutil.copytree(AGENTS, root)
        path = root / "交代" / "行政" / "工具说明.md"
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines()
                 if not ln.startswith("| read_card |")]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        specs = [{"type": "function", "function": {"name": "read_card", "parameters": {
            "type": "object", "properties": {"card_id": {"type": "string"}}}}}]
        rulebook.describe_tools(specs, "交代/行政/工具说明.md", root=root)
        fn = specs[0]["function"]
        self.assertIn("工具说明缺失", fn["description"])
        self.assertIn("工具说明缺失", fn["parameters"]["properties"]["card_id"]["description"])

    def test_health_reports_missing_or_blank_rows(self):
        # Codex 复查 T2～T6 第 2 轮：缺行、说明格清空，体检都要报
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "agents"
        shutil.copytree(AGENTS, root)
        path = root / "交代" / "行政" / "工具说明.md"
        text = path.read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if not ln.startswith("| read_card | — |")]
        text = "\n".join(lines).replace("| read_card | card_id | 卡片编号，例如 CARD-001 |", "| read_card | card_id |  |")
        path.write_text(text + "\n", encoding="utf-8")
        problems = rulebook.health(root=root)
        self.assertTrue(any("read_card" in p and "工具说明" in p for p in problems), problems)
        self.assertTrue(any("card_id" in p for p in problems), problems)

    def test_table_has_no_unknown_tools(self):
        from desktop.clerk import tools
        known = {spec["function"]["name"] for spec in tools.TOOLS + [tools.PROPOSE_REVISE]}
        for name, _param in rulebook.tool_notes("交代/行政/工具说明.md"):
            self.assertIn(name, known, "表里写了程序没有的工具")


class EveryButlerToolDescribed(unittest.TestCase):
    """第③步第 6 批：积木库管家的工具说明也在 agents/ 里，表里删一行要报出来。"""

    def test_butler_tools(self):
        from desktop.clerk import blocks_tools
        for spec in blocks_tools.TOOLS:
            fn = spec["function"]
            self.assertTrue(fn["description"], fn["name"])
            self.assertNotIn("工具说明缺失", fn["description"], fn["name"])
            for pname, pspec in fn["parameters"].get("properties", {}).items():
                self.assertTrue(pspec.get("description"), f"{fn['name']}.{pname}")
                self.assertNotIn("工具说明缺失", pspec["description"], f"{fn['name']}.{pname}")
