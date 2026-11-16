"""DSH 改版第 16 项：新版桌面不弹 DeepSeek Harness 欢迎页、“关于”面板和标题写超级助手。
欢迎页只能改 DSH 本体，按 apply_root_config.py 的同一做法：按原文锚点插入、可重复执行、锚点找不到就报错停下。"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "dsh-plugin" / "apply_desktop_brand.py"
SYNC = ROOT / "dsh-plugin" / "sync.sh"
LAUNCHER = ROOT / "dsh-plugin" / "core" / "scripts" / "local-desktop.mjs"

# DSH 0.2.2-rc.3 apps/desktop/src/main.ts 里要改的原文（逐字摘出）
FIXTURE = """\
    if (!enteredWorkspace && needsWelcome({ loggedIn: state.loggedIn, hasApiKey: state.hasApiKey })) {
      await showWelcome()
    }
              if (needsWelcome(value) && !quitting) {
                await showWelcome()
              }
            if (!needsWelcome(value) || quitting) return
  app.setAboutPanelOptions({
    applicationName: 'DeepSeek Harness',
    applicationVersion: app.getVersion(),
"""


def run(dsh: Path):
    return subprocess.run([sys.executable, str(SCRIPT), str(dsh)], capture_output=True, text=True)


def make_dsh(text: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix="dsh-brand-"))
    (d / "apps" / "desktop" / "src").mkdir(parents=True)
    (d / "apps" / "desktop" / "src" / "main.ts").write_text(text, "utf-8")
    return d


class DesktopBrandPatch(unittest.TestCase):
    def test_skips_welcome_only_for_superagent_build(self):
        dsh = make_dsh(FIXTURE)
        r = run(dsh)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (dsh / "apps/desktop/src/main.ts").read_text("utf-8")
        flag = "process.env.SUPERAGENT_DESKTOP"
        # 三处会弹欢迎页的地方都要被超级助手标记挡住；不是超级助手版本照旧
        self.assertIn(f"if (!enteredWorkspace && {flag} !== '1' && needsWelcome(", text)
        self.assertIn(f"if ({flag} !== '1' && needsWelcome(value) && !quitting) {{", text)
        self.assertIn(f"if ({flag} === '1' || !needsWelcome(value) || quitting) return", text)
        self.assertIn(f"applicationName: {flag} === '1' ? app.getName() : 'DeepSeek Harness',", text)

    def test_repeatable(self):
        dsh = make_dsh(FIXTURE)
        run(dsh)
        once = (dsh / "apps/desktop/src/main.ts").read_text("utf-8")
        r = run(dsh)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((dsh / "apps/desktop/src/main.ts").read_text("utf-8"), once)

    def test_stops_when_dsh_changed(self):
        dsh = make_dsh(FIXTURE.replace("needsWelcome({ loggedIn", "needsWelcomeV2({ loggedIn"))
        r = run(dsh)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("找不到", r.stderr + r.stdout)
        # 报错时一个字都不改
        self.assertIn("needsWelcomeV2", (dsh / "apps/desktop/src/main.ts").read_text("utf-8"))
        self.assertNotIn("SUPERAGENT_DESKTOP", (dsh / "apps/desktop/src/main.ts").read_text("utf-8"))

    def test_sync_applies_patch_and_sets_title(self):
        sync = SYNC.read_text("utf-8")
        self.assertIn('apply_desktop_brand.py" "$DSH"', sync)
        # 网页和窗口标题用 DSH 公开的编译设置，不改本体
        self.assertRegex(sync, r"DSH_CLIENT_TITLE=['\"]?超级助手")

    def test_launcher_marks_superagent_build(self):
        self.assertIn("env.SUPERAGENT_DESKTOP = '1'", LAUNCHER.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
