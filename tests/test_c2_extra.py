"""C2 补充验收。项目、提交和预览服务都用临时测试数据。"""
import json
import shlex
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import test_c2_acceptance as c2
from acceptance_helpers import ProjectCase, ServerCase, needs_node, run_node
from collab.archive import archive_card
from collab.flow import read_flow, text_of


class ArchiveDetails(c2.ArchiveCase):
    def test_second_archive_replaces_its_own_files_and_keeps_other_cards(self):
        card = self.card()
        target = Path(self.project) / "docs" / "改按钮"
        target.mkdir(parents=True)
        other_cards = ("卡片原文.md", "方案.md", "验收记录.md")
        for name in other_cards:
            (target / name).write_text("另一张卡已经存过的记录\n", encoding="utf-8")

        self.assertEqual(Path(archive_card(self.project, "CARD-001", "改按钮")), target)
        first_paths = {p.relative_to(target) for p in target.rglob("*") if p.is_file()}
        new_plan = "# 第二次归档更新过的方案\n"
        (card / "方案.md").write_text(new_plan, encoding="utf-8")
        archive_card(self.project, "CARD-001", "改按钮")

        self.assertEqual({p.relative_to(target) for p in target.rglob("*") if p.is_file()}, first_paths)
        for name in other_cards:
            self.assertEqual((target / name).read_text(encoding="utf-8"), "另一张卡已经存过的记录\n")
        new_file = target / "方案（CARD-001）.md"
        self.assertEqual(new_file.read_text(encoding="utf-8"), new_plan)
        manifest = json.loads((card / "归档文件.json").read_text(encoding="utf-8"))
        listed = json.dumps(manifest, ensure_ascii=False)
        for rel in first_paths.difference(map(Path, other_cards)):
            self.assertIn(json.dumps(f"docs/改按钮/{rel.as_posix()}", ensure_ascii=False), listed)
        for name in other_cards:
            self.assertNotIn(json.dumps(f"docs/改按钮/{name}", ensure_ascii=False), listed)

    def test_copy_uses_first_order_in_main_project_without_recording_a_merge(self):
        self.order()
        second_order = Path(self.project) / "docs" / "功能乙" / "施工单-乙.md"
        second_order.parent.mkdir()
        second_order.write_text("# 另一份施工单\n", encoding="utf-8")
        card = self.card(order=f"{c2.ORDER}、docs/功能乙/施工单-乙.md")
        copy = Path(self.tmp) / "副本"
        target = copy / "docs/功能甲/卡片记录/CARD-001"
        target.mkdir(parents=True)
        (target / "方案.md").write_text("这张卡上次归档的方案\n", encoding="utf-8")
        flow_before = read_flow(card)
        ledger = Path(self.project) / "docs/开发进度跟踪.md"
        ledger_before = ledger.read_bytes() if ledger.exists() else None

        result = archive_card(self.project, "CARD-001", "改按钮", root=copy, final_state="已放进项目")

        self.assertEqual(Path(result), target)
        self.assertEqual((target / "方案.md").read_bytes(), (card / "方案.md").read_bytes())
        self.assertEqual(list(target.glob("方案*.md")), [target / "方案.md"])
        self.assertIn("最终状态：已放进项目", (target / "验收记录.md").read_text(encoding="utf-8"))
        self.assertEqual(self.state(), "待验收")
        self.assertEqual(read_flow(card), flow_before)
        self.assertEqual(ledger.read_bytes() if ledger.exists() else None, ledger_before)

    def test_direct_archive_records_the_real_order_folder(self):
        self.order()
        card = self.card(order=c2.ORDER)
        archive_card(self.project, "CARD-001", "改按钮")

        rel = "docs/功能甲/卡片记录/CARD-001/"
        entries = [e for e in read_flow(card) if e["kind"] == "归档"]
        self.assertEqual(len(entries), 1)
        self.assertIn(rel, entries[0]["summary"])
        self.assertIn(rel, Path(self.project, "docs/开发进度跟踪.md").read_text(encoding="utf-8"))


class MergeDetails(c2.MergeCase):
    def setUp(self):
        # 直接调用合并流程，只需要临时 Git 项目。
        ProjectCase.setUp(self)

    def commit_order(self):
        order = Path(self.project) / c2.ORDER
        order.parent.mkdir(parents=True)
        self.commit(c2.ORDER, "# 施工单 甲\n")

    def test_successful_merge_records_the_order_folder_in_flow_and_ledger(self):
        self.commit_order()
        card = self.prepared()
        (card / "需求卡.md").write_text(c2._need(order=c2.ORDER), encoding="utf-8")

        answer = self.merge()

        self.assertTrue(answer["ok"], answer)
        rel = "docs/功能甲/卡片记录/CARD-001/"
        entries = [e for e in read_flow(card) if e["kind"] == "归档"]
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]["src"], entries[0]["dst"]), ("程序", "你"))
        self.assertIn(rel, entries[0]["summary"])
        self.assertIn(rel, Path(self.project, "docs/开发进度跟踪.md").read_text(encoding="utf-8"))

    def test_project_tests_see_code_and_records_before_both_are_rolled_back(self):
        self.commit_order()
        target = "docs/功能甲/卡片记录/CARD-001"
        marker = Path(self.tmp) / "测到的内容.txt"
        command = (
            'if [ "$(basename "$PWD")" = 项目 ]; then '
            f'cat src/app.py {shlex.quote(target + "/验收记录.md")} > {shlex.quote(str(marker))}; '
            'exit 1; fi'
        )
        card = self.prepared(command)
        (card / "需求卡.md").write_text(c2._need(order=c2.ORDER), encoding="utf-8")
        before = self.head()

        answer = self.merge()

        self.assert_stopped(answer, "已退回", before)
        seen = marker.read_text(encoding="utf-8")
        self.assertIn("x = 2", seen)
        self.assertIn("最终状态：已放进项目", seen, "退回前，主项目里的测试要能看到同一次提交的归档")
        self.assertEqual(self.main_file(), "x = 1\n")
        self.assertFalse((Path(self.project) / target).exists())
        self.assertNotIn("归档", self.flow_kinds())
        ledger = Path(self.project) / "docs/开发进度跟踪.md"
        if ledger.exists():
            self.assertNotIn(target, ledger.read_text(encoding="utf-8"))

    def test_archive_failure_explains_the_error_and_lets_the_code_through(self):
        card = self.prepared()
        with mock.patch("collab.merge.archive_card", side_effect=OSError("测试用：记录写不进去")):
            answer = self.merge()

        self.assertTrue(answer["ok"], answer)
        self.assertEqual(self.main_file(), "x = 2\n")
        notes = [e for e in read_flow(card) if "记录没能存入项目文档" in e.get("summary", "")]
        self.assertEqual(len(notes), 1)
        self.assertIn("记录写不进去", text_of(card, notes[0]))
        self.assertNotIn("归档", self.flow_kinds())

    def test_left_behind_notice_limits_summary_keeps_details_and_comes_before_merge(self):
        self.commit(".gitignore", "# 让 Git 看得到副本中这些测试文件\n")
        card = self.prepared()
        omitted = [f"extra/{i}.py" for i in range(7)]
        excluded = [".collab/内部.txt", "vendor/node_modules/a.js", "src/__pycache__/a.pyc", "private/.env.local"]
        for rel in omitted + excluded:
            path = self.worktree / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("测试数据\n", encoding="utf-8")

        answer = self.merge()

        self.assertTrue(answer["ok"], answer)
        flow = read_flow(card)
        notes = [e for e in flow if e["kind"] == "提醒"]
        self.assertEqual(len(notes), 1)
        note = notes[0]
        self.assertIn("副本里有 7 个文件没带进项目", note["summary"])
        self.assertIn("等", note["summary"])
        shown = sum(path in note["summary"] for path in omitted)
        self.assertIn(shown, range(1, 6))
        detail = text_of(card, note)
        for path in omitted:
            self.assertIn(path, detail)
            self.assertFalse((Path(self.project) / path).exists())
        for path in excluded + ["src/app.py"]:
            self.assertNotIn(path, note["summary"] + detail)
        merged = next(e for e in flow if e["kind"] == "合并" and e["summary"] == "已放进项目")
        self.assertLess(flow.index(note), flow.index(merged))


class PreviewResponses(c2.PreviewBase, ProjectCase):
    def setUp(self):
        super().setUp()
        self.hub = c2.PreviewHub(wait_seconds=8)
        self.addCleanup(self.hub.stop)

    def response_at_preview_path(self, status):
        self.prepared()
        path = self.collab / "cards/CARD-001/开工.json"
        plan = json.loads(path.read_text(encoding="utf-8"))
        plan["view_url"] = self.url + "panel"
        plan["view_command"] = f"{shlex.quote(sys.executable)} preview_fixture.py"
        path.write_text(json.dumps(plan), encoding="utf-8")
        (self.copy / "preview_fixture.py").write_text(
            "from http.server import HTTPServer, BaseHTTPRequestHandler\n"
            "class Handler(BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            f"        self.send_response({status} if self.path == '/panel' else 200)\n"
            "        self.end_headers()\n"
            f"HTTPServer(('127.0.0.1', {self.port}), Handler).serve_forever()\n",
            encoding="utf-8",
        )
        return self.hub.start(self.project, "CARD-001")

    def test_410_offers_the_folder_and_stops_the_preview(self):
        answer = self.response_at_preview_path(410)

        self.assertEqual((answer["ok"], answer["mode"]), (False, "folder"), answer)
        self.assertIn("找不到这个页面", answer["reason"])
        self.assertIn("打开文件夹", answer["reason"])
        self.assertEqual(Path(answer["path"]).resolve(), self.copy.resolve())
        self.assertFalse(self.hub.status(self.project, "CARD-001")["running"])
        with socket.socket() as client:
            client.settimeout(1)
            self.assertNotEqual(client.connect_ex(("127.0.0.1", self.port)), 0)

    def test_401_and_500_still_count_as_a_running_preview(self):
        for status in (401, 500):
            with self.subTest(status=status):
                answer = self.response_at_preview_path(status)
                self.assertEqual((answer["ok"], answer["mode"], answer["url"]), (True, "web", self.url + "panel"))
                self.assertTrue(self.hub.status(self.project, "CARD-001")["running"])
                self.hub.stop()


class PendingFiles(ServerCase):
    def write(self, name, text="测试数据\n"):
        path = Path(self.project) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def order_card(self):
        card = self.make_card()
        self.write(c2.ORDER, "# 本卡施工单\n")
        (card / "需求卡.md").write_text(c2._need(order=c2.ORDER), encoding="utf-8")

    def read_pending(self, **query):
        return self.get("/api/clerk/uncommitted", **{"project": self.project, "token": "口令", **query})

    def save_pending(self, **extra):
        return self.post("/api/clerk/save_pending", {"project": self.project, "card": "CARD-001", **extra})

    def git_output(self, *args):
        return subprocess.run(["git", *args], cwd=self.project, check=True, capture_output=True, text=True).stdout.strip()

    def test_docs_include_non_markdown_but_only_the_current_cards_order_counts(self):
        self.order_card()
        self.make_card("CARD-002")
        self.write("docs/资料.csv")
        self.write("docs/施工单-无关.md")
        self.write("src/开发说明.md")

        self.assertEqual(self.read_pending(card="CARD-001"), (200, {"count": 1, "files": [c2.ORDER]}))
        for query in ({}, {"card": "CARD-002"}):
            self.assertEqual(self.read_pending(**query), (200, {"count": 0, "files": []}))

    def test_rename_lists_the_new_path_with_spaces_and_other_paths_in_order(self):
        self.make_card()
        self.git("mv", "src/app.py", "src/新 名.py")
        self.write("a.py")
        self.write(".collab/check.txt")
        self.git("add", "-f", "--", ".collab/check.txt")

        self.assertEqual(self.read_pending(card="CARD-001"),
                         (200, {"count": 2, "files": ["a.py", "src/新 名.py"]}))

    def test_bad_card_number_is_rejected_and_non_git_project_returns_empty(self):
        self.assertEqual(self.read_pending(card="../CARD-001")[0], 400)
        self.assertEqual(self.read_pending(project=self.tmp), (200, {"count": 0, "files": []}))

    def test_save_rechecks_the_list_and_leaves_already_staged_document_out(self):
        self.order_card()
        self.write("src/app.py", "x = 2\n")
        self.write("README.md")
        self.git("add", "--", "README.md")

        self.assertEqual(self.save_pending(files=["README.md"]), (200, {"ok": True, "saved": 2}))

        names = self.git_output("-c", "core.quotepath=off", "show", "--name-only", "--format=", "HEAD").splitlines()
        self.assertCountEqual(names, [c2.ORDER, "src/app.py"])
        self.assertEqual(self.git_output("diff", "--cached", "--name-only"), "README.md")
        self.assertEqual(self.git_output("log", "-1", "--format=%s"), "开工前先存：CARD-001 改按钮")
        self.assertEqual(self.state(), "待对齐")

    def test_only_ordinary_documents_means_save_does_not_create_a_commit(self):
        self.make_card()
        self.write("docs/资料.csv")
        before = self.git_output("rev-parse", "HEAD")

        self.assertEqual(self.save_pending(), (200, {"ok": True, "saved": 0}))
        self.assertEqual(self.git_output("rev-parse", "HEAD"), before)
        self.assertEqual(self.state(), "待对齐")

    def test_failed_commit_unstages_its_changes_and_returns_a_short_reason(self):
        self.order_card()
        self.write("src/app.py", "x = 2\n")
        hooks = Path(self.tmp) / "hooks"
        hooks.mkdir()
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\necho '检查没过：" + "字" * 240 + "' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        self.git("config", "core.hooksPath", str(hooks))
        before = self.git_output("rev-parse", "HEAD")

        status, answer = self.save_pending()

        self.assertEqual(status, 409, answer)
        self.assertIs(answer["ok"], False)
        self.assertTrue(answer["reason"].startswith("没存成：检查没过"), answer)
        self.assertLessEqual(len(answer["reason"].removeprefix("没存成：")), 200)
        self.assertEqual(self.git_output("rev-parse", "HEAD"), before)
        self.assertEqual(self.git_output("diff", "--cached", "--name-only"), "")
        self.assertEqual(self.read_pending(card="CARD-001"), (200, {"count": 2, "files": [c2.ORDER, "src/app.py"]}))
        self.assertEqual(self.state(), "待对齐")


class PendingGitTimeout(ProjectCase):
    def test_git_gets_at_most_five_seconds_and_timeout_yields_an_empty_list(self):
        from collab import dispatch

        self.assertTrue(hasattr(dispatch, "files_to_save"), "还没有开工前要保存的文件清单")

        def time_out(args, **options):
            self.assertEqual(args[0], "git")
            self.assertIn("status", args)
            self.assertGreater(options["timeout"], 0)
            self.assertLessEqual(options["timeout"], 5)
            raise subprocess.TimeoutExpired(args, options["timeout"])

        with mock.patch("collab.dispatch.subprocess.run", side_effect=time_out) as git:
            self.assertEqual(dispatch.files_to_save(self.project), [])
        git.assert_called_once()


class PlannerPreviewInstructions(ProjectCase):
    def test_planner_is_told_to_check_entry_point_and_routes_for_both_fields(self):
        from collab.planner import _build_prompt

        card = self.make_card()
        need = (card / "需求卡.md").read_text(encoding="utf-8")
        prompt = _build_prompt(card, need, False, self.project)

        self.assertRegex(prompt, r"view_command[^\n]*view_url[^\n]*启动入口[^\n]*路由")


# 运行真实面板脚本。只替换页面元素和本地接口，按钮仍走面板自己的点击处理。
PANEL = r'''
const assert = require('assert').strict;
const fs = require('fs');
const vm = require('vm');

class Element {
  constructor(tag = '', hidden = false) {
    this.tagName = tag.toUpperCase();
    this.hidden = hidden;
    this.dataset = {};
    this.children = [];
    this.listeners = {};
    this.selectors = new Map();
    this.classList = {add() {}, remove() {}, toggle() {}};
    this.style = {};
    this.markup = '';
    this.words = '';
  }
  set innerHTML(value) { this.textContent = ''; this.markup = value; }
  get innerHTML() { return this.markup; }
  set textContent(value) { this.words = String(value); this.markup = ''; this.children = []; }
  get textContent() { return [this.ownText(), ...this.children.map(e => e.textContent)].join('\n'); }
  get innerText() {
    return this.hidden ? '' : [this.ownText(), ...this.children.map(e => e.innerText)].join('\n');
  }
  set innerText(value) { this.textContent = value; }
  ownText() { return this.words || this.markup.replace(/<[^>]*>/g, '\n'); }
  appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
  replaceChildren(...children) { this.textContent = ''; children.forEach(child => this.appendChild(child)); }
  setAttribute(name) { if (name === 'hidden') this.hidden = true; }
  removeAttribute(name) { if (name === 'hidden') this.hidden = false; }
  focus() {}
  querySelector(selector) {
    if (!this.selectors.has(selector)) this.selectors.set(selector, new Element());
    return this.selectors.get(selector);
  }
  closest(selector) {
    if (selector === this.tagName.toLowerCase() || (this.id && selector === '#' + this.id)) return this;
    return this.parentElement ? this.parentElement.closest(selector) : null;
  }
  addEventListener(type, listener) {
    if (!this.listeners[type]) this.listeners[type] = [];
    this.listeners[type].push(listener);
  }
  click(target = this) {
    if (this.disabled) return;
    let stopped = false;
    const event = {target, preventDefault() {}, stopPropagation() { stopped = true; }};
    for (let node = this; node && !stopped; node = node.parentElement) {
      event.currentTarget = node;
      for (const listener of node.listeners.click || []) listener.call(node, event);
      if (node.onclick) node.onclick(event);
    }
  }
}

const ids = new Map();
const stack = [new Element()];
const html = fs.readFileSync('desktop/static/panel.html', 'utf8');
for (const [, close, tag, attrs] of html.matchAll(/<(\/?)([a-z][\w-]*)(\s[^<>]*)?>/gi)) {
  if (close) { stack.pop(); continue; }
  const node = new Element(tag, /\shidden(?:\s|=|$)/.test(attrs || ''));
  const id = (attrs || '').match(/\bid=["']([^"']+)["']/);
  if (id) { node.id = id[1]; ids.set(node.id, node); }
  stack[stack.length - 1].appendChild(node);
  if (!['meta', 'link', 'input', 'img', 'br', 'hr'].includes(tag)) stack.push(node);
}
const $ = id => ids.get(id) || null;
const first = 'CARD-001';
const second = 'CARD-002';
let pending = {count: 1, files: ['src/app.py']};
let saveReply;
const checks = [], posts = [], confirms = [];
const response = (data, ok = true, status = 200) => ({ok, status, json: async () => data});
const window = {confirm: words => { confirms.push(words); return false; }};
const context = {
  window, URL, URLSearchParams, Headers: globalThis.Headers,
  location: {search: '?project=fake&clerk=1&token=test&card=CARD-001'},
  document: {getElementById: $, createElement: tag => new Element(tag),
    createTextNode: text => { const node = new Element(); node.textContent = text; return node; }},
  setTimeout() {}, setInterval() {},
  async fetch(path, opts) {
    const url = new URL(path, 'http://localhost');
    switch (url.pathname) {
      case '/api/view':
        return response({installed: true, stations: [], handoffs: [],
          card: {id: url.searchParams.get('card') || first, phase: {code: 'plan_ready', actions: ['approve']}}});
      case '/api/plan': return response({});
      case '/api/clerk/uncommitted':
        checks.push(url.searchParams.get('card'));
        if (pending === null) throw new Error('清单请求断开');
        return response(pending);
      case '/api/clerk/save_pending':
        assert.equal(opts.method, 'POST');
        assert.equal(typeof opts.headers.get === 'function' ? opts.headers.get('X-Chat-Token') :
          Object.entries(opts.headers).find(([key]) => key.toLowerCase() === 'x-chat-token')[1], 'test');
        posts.push(['save', JSON.parse(opts.body).card]);
        return await saveReply;
      case '/api/clerk/card': {
        const body = JSON.parse(opts.body);
        posts.push([body.action, body.card]);
        return response({ok: true});
      }
      default: throw new Error(url.pathname);
    }
  },
};
vm.runInNewContext(fs.readFileSync('desktop/static/panel.js', 'utf8'), context);

const flush = () => new Promise(resolve => setImmediate(resolve));
async function approve() {
  assert.match($('actionBtns').innerHTML, /data-act="approve"/);
  const target = {closest: () => target, getAttribute: () => 'approve', dataset: {act: 'approve'}};
  $('actions').click(target);
  await flush();
  assert.deepEqual(confirms, [], '批准开工不应弹出浏览器确认框');
}
function startBox() {
  assert.ok($('startBox'), '页面缺少开工确认框');
  return $('startBox');
}
async function choose(button) {
  assert.equal(startBox().hidden, false, '应先显示开工确认框');
  $(button).click();
  await flush();
}
'''


@needs_node()
class ApprovalDialog(unittest.TestCase):
    def check_panel(self, script):
        result = run_node(PANEL + "\n(async () => { await flush();\n" + script
                          + "\n})().catch(err => { console.error(err); process.exitCode = 1; });")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_empty_list_and_failed_check_each_approve_without_a_dialog(self):
        self.check_panel(r'''
assert.equal(startBox().hidden, true, '开工确认框默认隐藏');
for (const reply of [{count: 0, files: []}, null]) {
  pending = reply;
  await approve();
  assert.equal(startBox().hidden, true);
  assert.deepEqual(posts.splice(0), [['approve', first]]);
  assert.deepEqual(checks.splice(0), [first]);
}
''')

    def test_cancel_and_start_without_saving_and_limit_the_visible_files(self):
        self.check_panel(r'''
const files = Array.from({length: 12}, (_, i) => `src/view-${i}.py`);
pending = {count: files.length, files};
await approve();
assert.deepEqual(posts, [], '显示小框前不能先开工');
assert.match($('startText').innerText, /有 12 处改动还没存进项目.*施工会看不到它们/);
const shown = $('startFiles').innerText.trim().split(/\n+/).map(line => line.trim());
for (const path of files.slice(0, 10)) assert.ok(shown.includes(path), path + ' 应独占一行');
for (const path of files.slice(10)) assert.ok(!shown.includes(path), path);
assert.match(startBox().textContent, /还有\s*2\s*个/);
await choose('startCancel');
assert.equal(startBox().hidden, true);
assert.deepEqual(posts, []);

await approve();
await choose('startGo');
assert.equal(startBox().hidden, true);
assert.deepEqual(posts, [['approve', first]]);
''')

    def test_failed_save_shows_reason_without_starting(self):
        self.check_panel(r'''
saveReply = response({ok: false, reason: '没存成：提交检查没通过'}, false, 409);
await approve();
await choose('startSave');
assert.deepEqual(posts, [['save', first]]);
assert.equal(startBox().hidden, false);
assert.match(startBox().innerText, /没存成：提交检查没通过/);
''')

    def test_switching_cards_closes_the_box_and_next_click_uses_the_new_card(self):
        self.check_panel(r'''
await approve();
assert.equal(startBox().hidden, false);
window.SuperAgentPanel.show(second);
await flush();
assert.equal(startBox().hidden, true);
assert.deepEqual(posts, []);

await approve();
await choose('startGo');
assert.deepEqual(checks, [first, second]);
assert.deepEqual(posts, [['approve', second]]);
''')

    def test_successful_save_approves_the_clicked_card_even_if_the_view_changed(self):
        self.check_panel(r'''
let finishSave;
saveReply = new Promise(resolve => { finishSave = resolve; });
await approve();
await choose('startSave');
assert.deepEqual(posts, [['save', first]], '收到保存成功之前不能开工');
window.SuperAgentPanel.show(second);
await flush();
assert.equal(startBox().hidden, true);
finishSave(response({ok: true, saved: 1}));
await flush();
assert.deepEqual(posts, [['save', first], ['approve', first]]);
''')
