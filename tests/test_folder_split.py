"""工具分家（2026-11-17）：超级玩家的代码、DSH 底座都在 ~/Desktop/project/超级玩家/ 下，互为同级目录。
代码里不再写死旧位置 ~/Desktop/project/super-agent、~/Desktop/project/dsh-base；底座默认位置一律按“代码仓库的同级 dsh-base”找。"""
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_DIRS = ("collab", "desktop", "dsh-plugin", "skill")
OLD = [re.compile(p) for p in (r"Desktop/project/super-agent", r"Desktop/project/dsh-base",
                               r"'Desktop',\s*'project',\s*'super-agent'", r'"Desktop",\s*"project",\s*"super-agent"')]


class NoOldLocation(unittest.TestCase):
    def test_code_has_no_old_paths(self):
        bad = []
        for d in CODE_DIRS:
            for f in (ROOT / d).rglob("*"):
                if not f.is_file() or any(p in f.parts for p in ("node_modules", "lib", "build", ".build", "__pycache__")):
                    continue
                if f.suffix not in (".py", ".sh", ".ts", ".tsx", ".mts", ".mjs", ".js", ".md", ".swift", ".json"):
                    continue
                text = f.read_text(encoding="utf-8", errors="ignore")
                for i, line in enumerate(text.splitlines(), 1):
                    if any(p.search(line) for p in OLD):
                        bad.append(f"{f.relative_to(ROOT)}:{i}")
        self.assertEqual(bad, [])


class DshIsSibling(unittest.TestCase):
    def test_sync_default(self):
        # 不带参数时 sync.sh 认的底座 = 仓库同级的 dsh-base（只取出默认值那一行实跑，不真同步）
        line = next(l for l in (ROOT / "dsh-plugin" / "sync.sh").read_text().splitlines() if l.startswith('DSH="${1:-'))
        out = subprocess.run(["bash", "-c", f'HERE="{ROOT}/dsh-plugin"; set --; {line}; echo "$DSH"'],
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(Path(out).resolve(), (ROOT.parent / "dsh-base").resolve())

    def test_patch_scripts_default(self):
        for name in ("apply_root_config.py", "apply_desktop_brand.py"):
            src = (ROOT / "dsh-plugin" / name).read_text()
            line = next(l for l in src.splitlines() if l.startswith("DSH = "))
            code = (f"import sys\nfrom pathlib import Path\n__file__ = {str(ROOT / 'dsh-plugin' / name)!r}\n"
                    f"sys.argv = ['x']\n{line}\nprint(DSH)")
            out = subprocess.run(["python3", "-c", code], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(Path(out.stdout.strip()).resolve(), (ROOT.parent / "dsh-base").resolve(), name)

    def test_plugin_default_repo(self):
        src = (ROOT / "dsh-plugin" / "core" / "src" / "index.ts").read_text()
        self.assertRegex(src, r"const DEFAULT_REPO = join\(homedir\(\), 'Desktop', 'project', '超级玩家', 'super-agent'\)")

    def test_alignment_config_relative(self):
        src = (ROOT / "dsh-plugin" / "core" / "tests" / "vitest.alignment.config.mts").read_text()
        self.assertNotIn("/Users/", src)
        self.assertIn("../../../../dsh-base/", src)


if __name__ == "__main__":
    unittest.main()
