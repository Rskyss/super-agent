"""跑卡流程调整 · 第一批（右栏显示）验收测试（Claude 写；**施工方不许改**，补充测试请另写 test_flowfix_b_extra*.py）。

依据 docs/跑卡流程调整/方案-第一批.md、docs/跑卡流程调整/施工单-B.md。
数字取自 CARD-001 的真实记录：写代码 4 轮 326/57/151/112 秒，用量 2063310/181429/548485/497731。
"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from acceptance_helpers import ProjectCase, needs_node, run_node
from collab.flow import record

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def T(n):
    return {"ok": True, "total": n, "input": n, "cached": 0, "output": 0, "reasoning": 0, "children": 0}


class Rounds(ProjectCase):
    """右栏取数：每个环节给出轮数、累计用时、每轮明细；说明里不再拼“用了几分几秒”。"""

    def setUp(self):
        super().setUp()
        self.cdir = self.make_card(state="测试中")

    def station(self, key):
        from desktop.view import card_view
        view = card_view(self.project, "CARD-001")
        return next(s for s in view["stations"] if s["key"] == key)

    def round(self, role, seconds, tokens=None, summary="交了", tool="qwen"):
        sent = record(self.cdir, "派活", src="调度员", dst=role, tool=tool, step="开发", summary="干活", live=True)
        extra = {"tokens": tokens} if tokens is not None else {}
        if seconds is not None:
            extra["seconds"] = seconds
        record(self.cdir, "回复", src=role, dst="调度员", tool=tool, step="开发", ok=True,
               reply_to=sent["seq"], summary=summary, **extra)

    def test_four_rounds_add_up(self):
        for sec, n in ((326, 2063310), (57, 181429), (151, 548485), (112, 497731)):
            self.round("开发员", sec, T(n), summary="修好了坏数字导致右栏刷新失败的问题")
        dev = self.station("dev")
        self.assertEqual(dev["rounds"], 4)
        self.assertEqual(dev["spent"], 646)
        self.assertEqual([h["seconds"] for h in dev["history"]], [326, 57, 151, 112])
        self.assertEqual([h["tokens"] for h in dev["history"]], [2063310, 181429, 548485, 497731])
        self.assertEqual(dev["detail"], "修好了坏数字导致右栏刷新失败的问题", "说明里不再拼“用了”")

    def test_single_round(self):
        self.round("测试员", 419, T(1131271), summary="补了 41 项测试", tool="codex")
        test = self.station("test")
        self.assertEqual(test["rounds"], 1)
        self.assertEqual(test["spent"], 419)
        self.assertNotIn("用了", test["detail"])

    def test_running_second_round(self):
        self.round("开发员", 326, T(2063310))
        record(self.cdir, "派活", src="调度员", dst="开发员", tool="qwen", step="开发", summary="改一处",
               live=True)
        dev = self.station("dev")
        self.assertEqual(dev["state"], "active")
        self.assertEqual(dev["rounds"], 2, "正在跑的那轮也算")
        self.assertEqual(len(dev["history"]), 1, "明细只列交了活的轮")
        self.assertIsNotNone(dev.get("elapsed"))

    def test_old_reply_without_seconds_or_tokens(self):
        self.round("开发员", None, None)
        dev = self.station("dev")
        self.assertEqual(dev["rounds"], 1)
        self.assertIsNone(dev.get("spent"), "没有用时记录就不编一个 0")
        self.assertIsNone(dev["history"][0]["tokens"])

    def test_idle_station_has_no_rounds(self):
        self.assertFalse(self.station("rev").get("rounds"))


class ReportAsk(unittest.TestCase):
    def test_report_line_asks_for_plain_words(self):
        from collab.dispatch import _REPORT_ASK
        self.assertIn("汇报：", _REPORT_ASK)
        self.assertIn("不写文件名", _REPORT_ASK)


TEST_A = "import unittest\nclass A(unittest.TestCase):\n    def test_1(self): pass\n    def test_2(self): pass\n"
TEST_ACC = ("import unittest\nclass B(unittest.TestCase):\n    def test_1(self): pass\n"
            "    def test_2(self): pass\n    def test_3(self): self.fail('还没做')\n")


class ProgressCountsTheCardCommand(unittest.TestCase):
    """“通过 x/y”按本卡测试命令算，把事先写好的验收测试也算上（CARD-001 少算了 23 项）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.wt = self.tmp / "wt"
        (self.wt / "tests").mkdir(parents=True)
        (self.wt / "tests" / "test_card_extra.py").write_text(TEST_A, encoding="utf-8")   # 写测试的人交的
        (self.wt / "tests" / "test_card_acceptance.py").write_text(TEST_ACC, encoding="utf-8")  # 事先写好的
        self.cdir = self.tmp / "card"
        self.cdir.mkdir()

    def test_unittest_command_counts_everything_it_runs(self):
        from collab.progress import ProgressJob
        job = ProgressJob(self.cdir, self.wt, "开发", 1, ["tests/test_card_extra.py"],
                          test_command='python3 -m unittest discover -s tests -p "test_card*.py"')
        got = job.compute()
        self.assertEqual((got.get("passed"), got.get("total")), (4, 5), got)

    def test_chained_unittest_commands_are_added_up(self):
        from collab.progress import ProgressJob
        cmd = ('python3 -m unittest discover -s tests -p "test_card_extra.py" && '
               'python3 -m unittest discover -s tests -p "test_card_acceptance.py"')
        got = ProgressJob(self.cdir, self.wt, "开发", 1, ["tests/test_card_extra.py"], test_command=cmd).compute()
        self.assertEqual((got.get("passed"), got.get("total")), (4, 5), got)

    def test_other_commands_fall_back_to_tester_files(self):
        from collab.progress import ProgressJob
        job = ProgressJob(self.cdir, self.wt, "开发", 1, ["tests/test_card_extra.py"], test_command="npm test")
        got = job.compute()
        self.assertEqual((got.get("passed"), got.get("total")), (2, 2), got)

    def test_no_command_keeps_old_behaviour(self):
        from collab.progress import ProgressJob
        got = ProgressJob(self.cdir, self.wt, "开发", 1, ["tests/test_card_extra.py"]).compute()
        self.assertEqual((got.get("passed"), got.get("total")), (2, 2), got)


@needs_node()
class PanelText(unittest.TestCase):
    def test_rules(self):
        result = subprocess_node(HERE / "flowfix_b_panel_check.js")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_first_draw_and_refresh_agree(self):
        """首次显示和每半秒刷新只能是一套拼法：没有计时时不能多出“·”（CARD-001 复查两轮都点名）。"""
        result = run_node(r'''
const assert = require('assert'), fs = require('fs'), vm = require('vm');
class Element {
  constructor() {
    this.textContent = ''; this.innerHTML = ''; this.hidden = true; this.title = '';
    this.style = {}; this.children = []; this.selectors = new Map(); this.attrs = {};
    this.classList = {add(){}, remove(){}, toggle(){}};
  }
  querySelector(s) { if (!this.selectors.has(s)) this.selectors.set(s, new Element()); return this.selectors.get(s); }
  addEventListener() {}
  appendChild(child) { this.children.push(child); }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
}
const html = fs.readFileSync('./desktop/static/panel.html', 'utf8');
const ids = new Map([...html.matchAll(/id="([^"]+)"/g)].map(m => [m[1], new Element()]));
const $ = id => ids.get(id) || null;
const station = {key:'disp', role:'写方案', who:'Codex', state:'active', elapsed:null, quiet:null,
                 detail:'方案已批准，准备开工', tokens:{ok:true, total:8512, input:8512, cached:0, output:0}};
let payload = {installed:true, card:{id:'CARD-001'}, stations:[station], handoffs:[], tokens:{total:8512, other:0}};
let nextPoll, tick;
const context = {
  console, URLSearchParams, Date, module:{exports:{}}, location:{search:'?project=fake'}, window:{},
  document:{getElementById:$, createElement:() => new Element()},
  fetch:async () => ({json:async () => payload}),
  setTimeout:fn => {nextPoll = fn;}, setInterval:fn => {tick = fn;}
};
vm.runInNewContext(fs.readFileSync('./desktop/static/panel.js', 'utf8'), context);
const flush = async () => {for(let i=0;i<16;i++) await Promise.resolve();};
(async () => {
  await flush();
  const b = $('steps').children[0].querySelector('button');
// 右栏显示小修第 1 条（用户 11-19）：用量另起一行（.u），状态行（.k）只留轮次、用时、动静
  assert.strictEqual(b.querySelector('.k').textContent, '', '首次显示：没有计时就空着');
  assert.strictEqual(b.querySelector('.u').textContent, '8,512 token', '首次显示：用量单独一行');
  tick();
  assert.strictEqual(b.querySelector('.k').textContent, '', '刷新后不能多出“·”');
  assert.strictEqual(b.querySelector('.u').textContent, '8,512 token', '刷新后用量还在');
  assert.ok(!b.querySelector('.d').textContent.includes('已干'), '计时不进说明那行');
  // 第 2 轮正在跑：首次和刷新一致
  payload = {...payload, stations:[{...station, key:'dev', role:'写代码', rounds:2, elapsed:83, quiet:5}]};
  nextPoll(); await flush();
  const first = b.querySelector('.k').textContent;
  assert.ok(first.startsWith('第 2 轮 · 已干 1:2'), first);
  assert.ok(!first.includes('token'), first);
  assert.strictEqual(b.querySelector('.u').textContent, '8,512 token');
  tick();
  const again = b.querySelector('.k').textContent;
  assert.strictEqual(again.split(' · ').length, first.split(' · ').length, again);
  // 合计：一行，细项进提示
  payload = {...payload, tokens:{total:7364538, other:78696}};
  nextPoll(); await flush();
  assert.strictEqual($('tokenTotal').textContent, '本卡合计 736.5 万 token');
  assert.strictEqual($('tokenTotal').title, '其中交接资料等其他 7.9 万 token\n每答完一轮才更新一次');   // 第三批 C10
})().catch(e => {console.error(e); process.exitCode = 1;});
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_report_text_is_two_lines_at_most(self):
        css = (ROOT / "desktop" / "static" / "panel.css").read_text(encoding="utf-8")
        rule = css.split(".step .d {")[1].split("}")[0]
        self.assertIn("-webkit-line-clamp: 2", rule)


def subprocess_node(path):
    import subprocess
    return subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=30, cwd=str(ROOT))


if __name__ == "__main__":
    unittest.main()
