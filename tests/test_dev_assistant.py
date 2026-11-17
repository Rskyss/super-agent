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

    def test_window_title_and_menu(self):
        self.assertIn('window?.title = "开发助手 · "', self.src)
        for item in ("关于开发助手", "隐藏开发助手", "退出开发助手"):
            self.assertIn(item, self.src)
        self.assertNotIn('window?.title = "超级助手 · "', self.src)

    def test_blocks_folder_unchanged(self):
        blocks = (ROOT / "collab" / "blocks.py").read_text(encoding="utf-8")
        self.assertIn('"Application Support" / "超级助手" / "积木库"', blocks)


def top_func(src, name):
    """顶层函数全文（到第一个回到行首的右括号）。"""
    m = re.search(r"^func " + re.escape(name) + r"\b.*?^}", src, re.S | re.M)
    return m.group(0) if m else None


def run_swift(code):
    import shutil, subprocess, tempfile
    if not shutil.which("swift"):
        raise unittest.SkipTest("本机没有 swift")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "t.swift"
        f.write_text("import Foundation\n" + code, encoding="utf-8")
        out = subprocess.run(["swift", str(f)], capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        raise AssertionError(out.stderr)
    return dict(line.split(" ", 1) for line in out.stdout.splitlines() if line)


class Frozen(unittest.TestCase):
    """实际跑选根规则：默认只用自带的那份；--source-root 且源目录完整才用源目录。"""

    def setUp(self):
        self.src = SWIFT.read_text(encoding="utf-8")

    def test_pick_rule(self):
        body = top_func(self.src, "pickSuperAgentRoot")
        self.assertIsNotNone(body, "要有一个可单独验证的选根规则 pickSuperAgentRoot")
        cases = {
            "none": ('[]', "true"), "bundledflag": ('["--bundled-root"]', "true"),
            "src_ok": ('["--source-root"]', "true"), "src_broken": ('["--source-root"]', "false"),
            "walk": ('["--walk"]', "true"),
        }
        lines = [body, 'let b = URL(fileURLWithPath: "/B")', 'let s = URL(fileURLWithPath: "/S")']
        for k, (args, ok) in cases.items():
            lines.append(f'print("{k}", pickSuperAgentRoot(["/app"] + {args}, bundled: b, source: s, sourceComplete: {ok}).path)')
        got = run_swift("\n".join(lines))
        self.assertEqual(got, {"none": "/B", "bundledflag": "/B", "src_ok": "/S", "src_broken": "/B", "walk": "/B"})

    def test_root_uses_rule_without_source_fallback(self):
        body = swift_func(self.src, "superAgentRoot")
        self.assertIsNotNone(body)
        self.assertIn("pickSuperAgentRoot(", body)
        self.assertNotIn("?? source", body, "应用资源拿不到时也不能退回源目录")
        self.assertNotIn("return source", body)


class SourceProjectNotOldPackage(unittest.TestCase):
    """打开的项目就是本产品源码时不当旧协作包；只凭一个通用文件名不算。"""

    def test_rule(self):
        src = SWIFT.read_text(encoding="utf-8")
        body = top_func(src, "isProductSource")
        self.assertIsNotNone(body, "要有一个可单独验证的判断 isProductSource")
        code = body + """
func check(_ files: [String]) -> Bool { isProductSource("/p", exists: { files.contains($0) }) }
print("both", check(["/p/desktop/server.py", "/p/desktop/mac/Sources/SuperAgent/main.swift"]))
print("server_only", check(["/p/desktop/server.py"]))
print("swift_only", check(["/p/desktop/mac/Sources/SuperAgent/main.swift"]))
print("none", check([]))
"""
        self.assertEqual(run_swift(code), {"both": "true", "server_only": "false", "swift_only": "false", "none": "false"})

    def test_prepare_uses_rule(self):
        src = SWIFT.read_text(encoding="utf-8")
        body = swift_func(src, "prepareProject")
        decl = body[body.index("let isOldPackage"):body.index("if isOldPackage")]
        self.assertRegex(decl, r"&&\s*!isProductSource\(p,")


class InstallOnlyRemovesOurApp(unittest.TestCase):
    """装开发助手时只删编号是 local.superagent.desktop 的应用；同名的别的东西不删、停下。"""

    def make_app(self, path, ident):
        import plistlib
        (path / "Contents").mkdir(parents=True)
        with open(path / "Contents" / "Info.plist", "wb") as f:
            plistlib.dump({"CFBundleIdentifier": ident}, f)

    def run_remove(self, target):
        import subprocess
        script = ROOT / "desktop" / "mac" / "remove_app.sh"
        self.assertTrue(script.is_file(), "删旧应用的规则单独放在 desktop/mac/remove_app.sh")
        return subprocess.run(["bash", str(script), str(target)], capture_output=True, text=True, timeout=30)

    def test_cases(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            ours = d / "ours.app"; self.make_app(ours, "local.superagent.desktop")
            other = d / "other.app"; self.make_app(other, "com.example.other")
            plain = d / "plain.app"; plain.mkdir(); (plain / "x.txt").write_text("keep")
            missing = d / "missing.app"
            self.assertEqual(self.run_remove(ours).returncode, 0); self.assertFalse(ours.exists())
            self.assertNotEqual(self.run_remove(other).returncode, 0); self.assertTrue(other.exists())
            self.assertNotEqual(self.run_remove(plain).returncode, 0); self.assertTrue((plain / "x.txt").exists())
            self.assertEqual(self.run_remove(missing).returncode, 0)
            # --check 只核对不删
            again = d / "again.app"; self.make_app(again, "local.superagent.desktop")
            import subprocess
            script = ROOT / "desktop" / "mac" / "remove_app.sh"
            self.assertEqual(subprocess.run(["bash", str(script), "--check", str(again)]).returncode, 0)
            self.assertTrue(again.exists())
            self.assertNotEqual(subprocess.run(["bash", str(script), "--check", str(other)], capture_output=True).returncode, 0)

    def test_build_uses_it(self):
        build = BUILD.read_text(encoding="utf-8")
        install = build[build.index('if [ "${1:-}" = "--install" ]'):]
        self.assertIn('remove_app.sh" "$HOME/Applications/超级助手.app"', install)
        self.assertIn('remove_app.sh" "$HOME/Applications/开发助手.app"', install)
        self.assertNotIn("rm -rf", install)
        # 两个都先核对，再开始删
        first_delete = install.index('remove_app.sh" "$HOME')
        self.assertLess(install.index('--check "$HOME/Applications/超级助手.app"'), first_delete)
        self.assertLess(install.index('--check "$HOME/Applications/开发助手.app"'), first_delete)
        self.assertIn('cp -R "$APP" "$HOME/Applications/"', install)



class NoOldNameShown(unittest.TestCase):
    """页面和后台报给用户看的话里不再出现“超级助手”（这套后台新旧两个应用共用，改成不带名字的说法）。
    内部标记【超级助手自动交代】、积木库资料夹名、注释不算。"""

    def test_no_old_name(self):
        files = list((ROOT / "desktop" / "static").glob("*.js")) + list((ROOT / "desktop" / "static").glob("*.html")) + [
            ROOT / "desktop" / n for n in ("chat.py", "view.py", "server.py")]
        bad = []
        for f in files:
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("//")[0] if f.suffix == ".js" else line.split("#")[0]
                code = code.replace("【超级助手自动交代】", "")
                if "超级助手" in code and not code.lstrip().startswith(('"""', "'''")) and i > 3:
                    bad.append(f"{f.name}:{i}: {line.strip()[:80]}")
        allowed = ['"from": "超级助手"', 'description="超级助手']
        bad = [b for b in bad if not any(a in b for a in allowed)]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
