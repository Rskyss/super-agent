"""小修卡“去掉‘超级助手’旧名”验收测试（Claude 写；施工方不许改，补充测试写进 tests/test_oldname_extra*.py）。

依据 docs/跑卡流程调整/施工单-去掉超级助手旧名.md：用户能看到的话（提示、报错、写进用户项目的说明、
AI 角色说明）不再出现“超级助手”，改成不带名字的说法（“这个应用”“程序”）。
内部标记、积木库资料夹名、临时文件夹前缀、合并提交的署名、注释和函数说明不算。
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 只能留在代码里、用户看不到（或改了会丢数据、认不出老记录）的写法：整串一字不差才放过
ALLOWED = {
    "user.name=超级助手",          # 合并提交的署名：老项目历史里一直是这个，不改
    "超级助手版本-", "超级助手检测-",   # 临时文件夹前缀，用完就删
    "超级助手 连通检测",            # 只发给搜索工具试连通，不给人看
    "超级助手",                    # 积木库资料夹名（blocks.py）、和 Codex 握手时报的名字：改了找不到老数据
    "【超级助手自动交代】",          # 认老对话用的内部标记
    "超级助手流转面板的本机数据服务",  # 只在命令行 --help 里
}


def _literals(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.lineno, node.value


def _py_files():
    files = list((ROOT / "collab").glob("*.py"))
    files += [p for p in (ROOT / "desktop").rglob("*.py") if "build" not in p.parts]
    return files


class NoOldNameInCode(unittest.TestCase):
    def test_user_facing_strings(self):
        bad = []
        for f in _py_files():
            for line, text in _literals(f):
                if "超级助手" in text and text not in ALLOWED:
                    bad.append(f"{f.relative_to(ROOT)}:{line}: {text[:60]}")
        self.assertEqual(bad, [], "用户能看到的话里还有“超级助手”")

    def test_allowed_ones_kept(self):
        """不该改的别顺手改了：资料夹名、内部标记、署名还在。"""
        text = {f.name: f.read_text(encoding="utf-8") for f in _py_files()}
        self.assertIn('"超级助手"', text["blocks.py"], "积木库资料夹名不能改，改了找不到老数据")
        self.assertIn("user.name=超级助手", text["merge.py"])
        self.assertIn("【超级助手自动交代】", text["codex.py"])


class NoOldNameInRoles(unittest.TestCase):
    def test_role_files(self):
        files = [ROOT / "agents" / "角色" / "积木库管家.md", *sorted((ROOT / "agents" / "手册" / "积木库").glob("*.md"))]
        self.assertGreaterEqual(len(files), 5, "规则包第③步搬到 agents/ 后要真的查到文件")
        bad = [f"{f.name}:{i}" for f in files
               for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1) if "超级助手" in line]
        self.assertEqual(bad, [], "AI 角色说明里不再自称超级助手")


class Behaviour(unittest.TestCase):
    def test_reopen_hint_still_says_reopen(self):
        from collab.blocks_env import __file__ as env_file
        text = Path(env_file).read_text(encoding="utf-8")
        self.assertIn("再重新打开", text, "装好了还没认到时，仍要告诉用户关掉应用再重新打开")

    def test_ignore_comment(self):
        import tempfile
        import subprocess
        from collab.workspace import ensure_ignored
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
            self.assertTrue(ensure_ignored(tmp))
            written = (Path(tmp) / ".gitignore").read_text(encoding="utf-8")
            self.assertNotIn("超级助手", written, "写进用户项目的说明不带旧名")
            self.assertIn(".collab/", written)
            self.assertFalse(ensure_ignored(tmp), "已经有了不重复加")


if __name__ == "__main__":
    unittest.main()
