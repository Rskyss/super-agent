"""DSH 改版第 16 项：新版桌面不弹 DeepSeek Harness 欢迎页、“关于”面板和标题写自己的名字（第 22 项改名“超级玩家”）。
第 24 项：空对话页标题换成“说出你想做的事”、去掉“预览版”（DSH 没有公开改法，同一份补丁按编译设置生效）。
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


# DSH 0.2.2-rc.3 packages/client/ui-conversation/src/client/skeleton/EmptyHero.tsx 里要改的原文（逐字摘出）
HERO = "packages/client/ui-conversation/src/client/skeleton/EmptyHero.tsx"
HERO_FIXTURE = """\
          <span className={css.titleGroup}>
            {/* Own element: keeps the headline text addressable apart from the badge. */}
            <span>{t('hero.headline')}</span>
            <span className={css.previewBadge}>{t('hero.preview')}</span>
          </span>
"""
HEADLINE = "process.env.DSH_CLIENT_HERO_HEADLINE"


def run(dsh: Path):
    return subprocess.run([sys.executable, str(SCRIPT), str(dsh)], capture_output=True, text=True)


def make_dsh(text: str, hero: str = HERO_FIXTURE) -> Path:
    d = Path(tempfile.mkdtemp(prefix="dsh-brand-"))
    (d / "apps" / "desktop" / "src").mkdir(parents=True)
    (d / "apps" / "desktop" / "src" / "main.ts").write_text(text, "utf-8")
    (d / HERO).parent.mkdir(parents=True)
    (d / HERO).write_text(hero, "utf-8")
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

    def test_hero_headline_only_for_superagent_build(self):
        dsh = make_dsh(FIXTURE)
        r = run(dsh)
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (dsh / HERO).read_text("utf-8")
        # 编了我们的标题才换字、去掉“预览版”；没编（DSH 自己的版本）照旧显示原标题和徽标
        self.assertIn(f"<span>{{{HEADLINE} ?? t('hero.headline')}}</span>", text)
        self.assertIn(f"{{{HEADLINE} === undefined && <span className={{css.previewBadge}}>{{t('hero.preview')}}</span>}}", text)
        self.assertNotIn("            <span className={css.previewBadge}>{t('hero.preview')}</span>\n", text)

    def test_repeatable(self):
        dsh = make_dsh(FIXTURE)
        run(dsh)
        once = (dsh / "apps/desktop/src/main.ts").read_text("utf-8")
        hero_once = (dsh / HERO).read_text("utf-8")
        r = run(dsh)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((dsh / "apps/desktop/src/main.ts").read_text("utf-8"), once)
        self.assertEqual((dsh / HERO).read_text("utf-8"), hero_once)

    def test_stops_when_hero_changed_without_touching_anything(self):
        dsh = make_dsh(FIXTURE, HERO_FIXTURE.replace("t('hero.headline')", "t('hero.title')"))
        r = run(dsh)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("找不到", r.stderr + r.stdout)
        # 任何一处对不上，两份文件都一个字不改
        self.assertNotIn("SUPERAGENT_DESKTOP", (dsh / "apps/desktop/src/main.ts").read_text("utf-8"))
        self.assertNotIn(HEADLINE, (dsh / HERO).read_text("utf-8"))

    def test_stops_when_dsh_changed(self):
        dsh = make_dsh(FIXTURE.replace("needsWelcome({ loggedIn", "needsWelcomeV2({ loggedIn"))
        r = run(dsh)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("找不到", r.stderr + r.stdout)
        # 报错时一个字都不改
        self.assertIn("needsWelcomeV2", (dsh / "apps/desktop/src/main.ts").read_text("utf-8"))
        self.assertNotIn("SUPERAGENT_DESKTOP", (dsh / "apps/desktop/src/main.ts").read_text("utf-8"))

    def test_stops_when_patched_and_unpatched_both_present(self):
        # 复查第 1 轮中等：新旧写法同时在（打了一半或 DSH 自己多了一处）不能当“已打过”放行
        hero = HERO_FIXTURE + f"<span>{{{HEADLINE} ?? t('hero.headline')}}</span>\n"
        dsh = make_dsh(FIXTURE, hero)
        r = run(dsh)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("找不到", r.stderr + r.stdout)
        self.assertNotIn("SUPERAGENT_DESKTOP", (dsh / "apps/desktop/src/main.ts").read_text("utf-8"))
        self.assertEqual((dsh / HERO).read_text("utf-8"), hero)

    def test_write_failure_leaves_both_files_untouched(self):
        # 复查第 1 轮建议：第二份文件写不进去时，第一份也不能已经改了
        import os, stat
        dsh = make_dsh(FIXTURE)
        hero_dir = (dsh / HERO).parent
        os.chmod(hero_dir, stat.S_IRUSR | stat.S_IXUSR)
        try:
            r = run(dsh)
        finally:
            os.chmod(hero_dir, stat.S_IRWXU)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((dsh / "apps/desktop/src/main.ts").read_text("utf-8"), FIXTURE)
        self.assertEqual((dsh / HERO).read_text("utf-8"), HERO_FIXTURE)
        self.assertEqual(list(dsh.rglob("*.brand-tmp")), [])

    def test_sync_applies_patch_and_sets_title(self):
        sync = SYNC.read_text("utf-8")
        self.assertIn('apply_desktop_brand.py" "$DSH"', sync)
        # 网页和窗口标题用 DSH 公开的编译设置，不改本体（第 22 项改名）
        self.assertRegex(sync, r"DSH_CLIENT_TITLE=['\"]?超级玩家")
        self.assertNotRegex(sync, r"DSH_CLIENT_TITLE=['\"]?超级助手")
        # 第 24 项：空对话页标题随编译设置
        self.assertRegex(sync, r"DSH_CLIENT_HERO_HEADLINE=['\"]?今天想交给我什么事？")

    def test_launcher_marks_superagent_build(self):
        self.assertIn("env.SUPERAGENT_DESKTOP = '1'", LAUNCHER.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
