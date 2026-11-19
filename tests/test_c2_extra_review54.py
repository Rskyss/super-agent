"""第四轮复查退回的五项问题，都用临时项目或面板脚本复现。"""
import subprocess
import unittest
from pathlib import Path

from acceptance_helpers import ServerCase, needs_node, run_node
from collab.merge import merge_card
from test_c2_extra import PANEL


class PendingSaveRegressions(ServerCase):
    def save_pending(self):
        return self.post("/api/clerk/save_pending", {"project": self.project, "card": "CARD-001"})

    def git_output(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.project, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def test_local_env_is_left_out_of_the_save_list_and_git_history(self):
        self.make_card()
        project = Path(self.project)
        local_env = project / ".env.local"
        fixture = "只是测试数据\n"
        local_env.write_text(fixture, encoding="utf-8")
        (project / "src/app.py").write_text("x = 2\n", encoding="utf-8")

        pending = self.get("/api/clerk/uncommitted", project=self.project, card="CARD-001", token="口令")
        status, answer = self.save_pending()

        self.assertEqual(status, 200, answer)
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(self.git_output("show", "HEAD:src/app.py"), "x = 2")
        self.assertEqual(local_env.read_text(encoding="utf-8"), fixture)
        history = self.git_output("log", "--all", "--format=%H", "--", ".env.local")
        self.assertEqual(
            (pending, history), ((200, {"count": 1, "files": ["src/app.py"]}), ""),
            "本机配置应留在本地，开工清单和 Git 历史里都只收该存的文件",
        )

    def test_rejected_save_preserves_the_users_partially_staged_file(self):
        self.make_card()
        app = Path(self.project) / "src/app.py"
        app.write_text("x = 2\n", encoding="utf-8")
        self.git("add", "--", "src/app.py")
        staged = self.git_output("rev-parse", ":src/app.py")
        working = "x = 2\ny = 3\n"
        app.write_text(working, encoding="utf-8")
        head = self.git_output("rev-parse", "HEAD")
        hooks = Path(self.tmp) / "hooks"
        hooks.mkdir()
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\necho '测试用提交检查拒绝保存' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        self.git("config", "--local", "core.hooksPath", str(hooks))

        status, answer = self.save_pending()

        self.assertEqual(status, 409, answer)
        self.assertIn("测试用提交检查拒绝保存", answer["reason"])
        self.assertEqual(self.git_output("rev-parse", "HEAD"), head)
        self.assertEqual(app.read_text(encoding="utf-8"), working)
        self.assertEqual(self.git_output("rev-parse", ":src/app.py"), staged,
                         "保存失败后应保留用户原先选中的暂存版本")

    def test_saved_rename_leaves_only_the_new_name_in_the_worktree(self):
        self.make_card()
        old, new = "src/app.py", "src/新 名.py"
        self.git("mv", "--", old, new)

        status, answer = self.save_pending()
        self.assertEqual(status, 200, answer)
        self.assertTrue(answer["ok"], answer)
        copy = self.make_copy()

        self.assertEqual((copy / new).read_text(encoding="utf-8"), "x = 1\n")
        self.assertFalse((copy / old).exists(), "施工副本里不该还留着改名前的文件")

    def test_saving_and_trying_an_empty_merge_preserve_git_path_quoting(self):
        self.make_card(plan=True)
        (Path(self.project) / "src/app.py").write_text("x = 2\n", encoding="utf-8")
        self.git("config", "--local", "core.quotepath", "true")

        status, answer = self.save_pending()
        self.assertEqual(status, 200, answer)
        self.assertTrue(answer["ok"], answer)
        after_save = self.git_output("config", "--local", "--get", "core.quotepath")

        self.make_card(state="待验收", plan=True)
        self.make_copy()
        self.git("config", "--local", "core.quotepath", "true")
        answer = merge_card(self.project, "CARD-001")
        self.assertFalse(answer["ok"], answer)
        self.assertIn("没有改动", answer["reason"])
        after_merge = self.git_output("config", "--local", "--get", "core.quotepath")

        self.assertEqual(
            {"先存再开工": after_save, "放进项目": after_merge},
            {"先存再开工": "true", "放进项目": "true"},
            "两处操作都应保留用户原来的中文文件名显示设置",
        )


@needs_node()
class AutomaticCardChange(unittest.TestCase):
    def test_refresh_to_another_card_closes_the_old_start_dialog(self):
        # 沿用原有面板测试，只把数据源切到跟随最新卡片，并手动触发下一次轮询。
        setup, boot, helpers = PANEL.partition("\nvm.runInNewContext(")
        self.assertTrue(boot, "面板测试缺少脚本启动入口")
        result = run_node(setup + r'''
const params = new URLSearchParams(context.location.search);
params.delete('card');
context.location.search = '?' + params;
let latestCard = first;
let pollAgain;
context.setTimeout = callback => { pollAgain = callback; };
const fixtureFetch = context.fetch;
context.fetch = async (path, opts) => {
  const url = new URL(path, 'http://localhost');
  if (url.pathname === '/api/view') {
    assert.equal(url.searchParams.has('card'), false, '右栏应跟随最新卡片');
    url.searchParams.set('card', latestCard);
    return fixtureFetch(url, opts);
  }
  if (url.pathname === '/api/plan') return response({plan: url.searchParams.get('card')});
  return fixtureFetch(path, opts);
};
''' + boot + helpers + r'''
(async () => {
  await flush();
  await approve();
  assert.deepEqual(checks, [first]);
  assert.equal(startBox().hidden, false, '第一张卡应已弹出开工小框');

  latestCard = second;
  assert.equal(typeof pollAgain, 'function', '面板应安排下一次自动刷新');
  pollAgain();
  await flush();
  assert.equal($('planText').textContent, second, '右栏应已显示第二张卡的方案');

  const hidden = startBox().hidden;
  if (!hidden) await choose('startGo');
  assert.deepEqual({hidden, posts}, {hidden: true, posts: []},
    '自动切卡应关掉旧框，也不能让旧框启动第一张卡');

  await approve();
  await choose('startGo');
  assert.deepEqual(posts, [['approve', second]]);
})().catch(err => { console.error(err); process.exitCode = 1; });
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
