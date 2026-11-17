"""旧版改名“开发助手”并冻结（用户 2026-11-17 “改名冻结”）。

- 名字：应用名、窗口标题、菜单改“开发助手”；应用编号、积木库资料夹不改（设置和钥匙串照旧能用）。
- 冻结：默认只用应用自带的那份协作包和页面，不读项目源目录（开发助手要开发自己的源码，读源目录会一边干活一边改自己）；
  只有明确带 --source-root 才读源目录。
- 打开的项目就是超级玩家源码本身时，不能当“旧协作包”迁移或提议删掉它的 collab/。
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWIFT = ROOT / "desktop" / "mac" / "Sources" / "SuperAgent" / "main.swift"
BUILD = ROOT / "desktop" / "mac" / "build.sh"


def swift_func(src, name):
    m = re.search(r"func " + name + r"\(.*?\n    \}\n", src, re.S)
    return m.group(0) if m else None


class Name(unittest.TestCase):
    def setUp(self):
        self.build = BUILD.read_text(encoding="utf-8")
        self.src = SWIFT.read_text(encoding="utf-8")

    def test_bundle_names(self):
        self.assertIn("<key>CFBundleName</key><string>开发助手</string>", self.build)
        self.assertIn("<key>CFBundleDisplayName</key><string>开发助手</string>", self.build)
        self.assertIn('APP="$HERE/build/开发助手.app"', self.build)

    def test_identifier_unchanged(self):
        # 编号不变，设置、钥匙串、窗口记忆照旧
        self.assertIn("<key>CFBundleIdentifier</key><string>local.superagent.desktop</string>", self.build)

    def test_install_replaces_old_app(self):
        # 装开发助手时去掉旧的“超级助手.app”，免得搜索里出现两个
        install = self.build[self.build.index('if [ "${1:-}" = "--install" ]'):]
        self.assertIn('rm -rf "$HOME/Applications/超级助手.app"', install)
        self.assertIn('rm -rf "$HOME/Applications/开发助手.app"', install)
        self.assertIn("开发助手.app", install.split("cp -R")[1].split("\n")[0] + install)

    def test_window_title_and_menu(self):
        self.assertIn('window?.title = "开发助手 · "', self.src)
        for item in ("关于开发助手", "隐藏开发助手", "退出开发助手"):
            self.assertIn(item, self.src)
        self.assertNotIn('window?.title = "超级助手 · "', self.src)

    def test_blocks_folder_unchanged(self):
        blocks = (ROOT / "collab" / "blocks.py").read_text(encoding="utf-8")
        self.assertIn('"Application Support" / "超级助手" / "积木库"', blocks)


class Frozen(unittest.TestCase):
    def setUp(self):
        self.src = SWIFT.read_text(encoding="utf-8")
        self.body = swift_func(self.src, "superAgentRoot")

    def test_default_is_bundled(self):
        self.assertIsNotNone(self.body)
        # 读源目录只能在 --source-root 分支里
        self.assertIn('"--source-root"', self.body)
        src_branch = self.body[self.body.index('"--source-root"'):]
        self.assertIn("return source", src_branch)
        before = self.body[:self.body.index('"--source-root"')]
        self.assertNotIn("return source", before)
        # 最后兜底返回应用自带的那份
        last_return = self.body.strip().splitlines()[-2].strip()
        self.assertTrue(last_return.startswith("return Bundle.main.resourceURL"), last_return)

    def test_no_unconditional_source(self):
        # 不能再有“源目录存在就用源目录”的老写法
        self.assertNotRegex(self.body, r'if fm\.fileExists\(atPath: source\.appendingPathComponent\("collab/规则\.md"\)\.path\),\s*\n\s*fm\.fileExists\(atPath: source\.appendingPathComponent\("desktop/server\.py"\)\.path\) \{\s*\n\s*return source')


class SourceProjectNotOldPackage(unittest.TestCase):
    def test_guard(self):
        src = SWIFT.read_text(encoding="utf-8")
        body = swift_func(src, "prepareProject")
        self.assertIsNotNone(body)
        decl = body[body.index("let isOldPackage"):body.index("if isOldPackage")]
        # 项目里有 desktop/server.py（就是本产品源码）时，不算旧协作包
        self.assertIn('desktop/server.py', decl)


if __name__ == "__main__":
    unittest.main()
