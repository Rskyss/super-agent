"""第三批 D1 补充验收：只测用户能看见的口径，不改产品代码。"""
import json
import shutil
import unittest
from pathlib import Path

import test_redfirst_acceptance as rf
from acceptance_helpers import ROOT, ProjectCase, needs_node, run_node
from collab.agents import AgentResult
from collab.flow import read_flow, record, text_of
from collab.judge import FAIL, PASS, TIMEOUT
from collab.state import Card
from test_acceptance_20_merge import MergeCase


def view_of(project, card_id="CARD-001"):
    from desktop.view import card_view
    return card_view(project, card_id)


def station(view, key):
    return next(s for s in view["stations"] if s["key"] == key)


def handoff(view, kind):
    return next(e for e in view["handoffs"] if e["kind"] == kind)


class C1OldRecordWords(ProjectCase):
    def test_visible_flow_words_are_rewritten(self):
        cdir = self.make_card(state="需Claude介入", plan=True)
        record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工")
        record(cdir, "交接资料", src="调度员", dst="开发员", ok=True,
               summary="调度员备好了交接资料（300 字）")
        record(cdir, "转人工", src="程序", dst="调度员",
               summary="全部测试没过，需要调度员看看")
        view = view_of(self.project)

        self.assertNotIn("调度员", json.dumps(view, ensure_ascii=False))
        prepared = handoff(view, "交接资料")
        self.assertEqual((prepared["from"], prepared["to"]), ("程序", "开发员"))
        self.assertEqual(prepared["summary"], "程序备好了交接资料（300 字）")
        self.assertEqual(handoff(view, "转人工")["summary"], "全部测试没过，需要你看看")


class C2TimeoutKeepsWrittenCode(rf.Base):
    def test_resume_after_timeout_does_not_call_developer_again(self):
        self.verdicts = [FAIL, TIMEOUT]
        self.assertEqual(self.go().state, "需Claude介入")
        self.calls.clear()
        card = self.go(card=Card.load(self.cdir / "状态.json"))
        self.assertEqual(card.state, "待验收")
        self.assertNotIn("代码", self.who())


class C3ReviewStations(ProjectCase):
    def stopped_by_review(self, cause=None, summary="复查不通过"):
        cdir = self.make_card(state="需Claude介入", plan=True)
        record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工")
        record(cdir, "派活", src="调度员", dst="复查员", tool="agy", step="复查", round=1,
               summary="复查")
        record(cdir, "回复", src="复查员", dst="调度员", tool="agy", step="复查", round=1,
               ok=True, summary="结论：不通过")
        record(cdir, "转人工", src="程序", dst="调度员", cause=cause, summary=summary)
        return cdir

    def test_review_fail_and_unreadable_stops_mark_review(self):
        for cause, summary in (
            ("复查不通过", "复查不通过（已自动退回 2 次）"),
            ("复查看不懂", "复查清单看不懂，需要你看看"),
            (None, "复查不通过，需要调度员看原因"),
        ):
            with self.subTest(cause=cause):
                self.stopped_by_review(cause, summary)
                view = view_of(self.project)
                self.assertEqual(station(view, "rev")["state"], "fail")
                self.assertEqual(station(view, "disp")["state"], "done")


class C8OldCardPlanStation(ProjectCase):
    def test_cards_without_flow_still_say_plan_is_set(self):
        for state in ("待验收", "已合并"):
            with self.subTest(state=state):
                cdir = self.make_card(state=state, plan=True)
                (cdir / "流转.jsonl").unlink(missing_ok=True)
                shutil.rmtree(cdir / "流转", ignore_errors=True)
                detail = station(view_of(self.project), "disp")["detail"]
                self.assertIn("方案已定", detail)
                self.assertNotIn("已向你汇报", detail)
                self.assertNotIn("已放进项目", detail)


class C9MergeFullText(MergeCase):
    def test_branch_name_is_only_in_full_text_and_ledger(self):
        self.prepared()
        branch = self.out("rev-parse", "--abbrev-ref", "HEAD")
        self.assertTrue(self.merge()["ok"])
        merged = self.last_flow("合并")
        self.assertEqual(merged["summary"], "已放进项目")
        self.assertEqual(text_of(self.collab / "cards" / "CARD-001", merged),
                         f"已放进 {branch} 分支")
        ledger = (Path(self.project) / "docs" / "开发进度跟踪.md").read_text(encoding="utf-8")
        self.assertIn(f"CARD-001：已放进 {branch}", ledger)


@needs_node()
class C10UsageTooltips(unittest.TestCase):
    def test_tooltips_add_update_note_without_changing_usage_details(self):
        result = run_node(r'''
const assert = require('assert');
const fs = require('fs'), vm = require('vm');
class Element {
  constructor() {
    this.textContent = ''; this.innerHTML = ''; this.hidden = true;
    this.style = {}; this.children = []; this.title = '';
    this.classList = {add(){}, remove(){}, toggle(){}};
    this._query = new Map();
  }
  querySelector(selector) {
    if (!this._query.has(selector)) this._query.set(selector, new Element());
    return this._query.get(selector);
  }
  addEventListener() {}
  appendChild(child) { this.children.push(child); }
  setAttribute() {}
}
const html = fs.readFileSync('./desktop/static/panel.html', 'utf8');
const ids = new Map([...html.matchAll(/id="([^"]+)"/g)].map(m => [m[1], new Element()]));
const $ = id => ids.get(id) || null;
const usage = {ok:true, total:60, input:30, cached:24, output:30};
const station = {key:'dev', role:'写代码', who:'Codex', state:'active', elapsed:12, quiet:1,
                 detail:'正在改颜色', rounds:2, history:[
  {who:'Codex', seconds:10, tokens:{ok:true, total:20, input:10, cached:8, output:10}},
  {who:'Codex', seconds:20, tokens:{ok:true, total:40, input:20, cached:16, output:20}}
], tokens:usage};
const payload = {installed:true, card:{id:'CARD-001'}, stations:[station], handoffs:[],
                 tokens:{total:80, other:20}};
let nextPoll;
const context = {
  console, URLSearchParams, Date, module:{exports:{}}, location:{search:'?project=fake'},
  window:{}, document:{getElementById:$, createElement:() => new Element()},
  fetch:async () => ({json:async () => payload}),
  setTimeout:fn => {nextPoll = fn;}, setInterval:() => {}
};
vm.runInNewContext(fs.readFileSync('./desktop/static/panel.js', 'utf8'), context);
const flush = async () => {for(let i=0;i<16;i++) await Promise.resolve();};
(async () => {
  await flush();
  const button = $('steps').children[0].querySelector('button');
  assert.ok(button.title.includes('正在改颜色'));
  assert.ok(button.title.includes('第 1 轮'));
  assert.ok(button.title.includes('输入 30（其中缓存读取 24）'));
  assert.ok(button.title.includes('每答完一轮才更新一次'));
  assert.strictEqual($('tokenTotal').hidden, false);
  assert.strictEqual($('tokenTotal').textContent, '本卡合计 80 token');
  assert.ok($('tokenTotal').title.includes('其中交接资料等其他 20 token'));
  assert.ok($('tokenTotal').title.includes('每答完一轮才更新一次'));
  const P = context.module.exports;
  assert.ok(!P.tokenTitle(usage).includes('每答完一轮才更新一次'));
})().catch(e => {console.error(e); process.exitCode = 1;});
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class C11ClaudeCard(ProjectCase):
    def new_card(self, card_id):
        requirement = Path(self.tmp) / f"{card_id}.md"
        requirement.write_text("# 卡\n\n## 要做什么\n改一处\n", encoding="utf-8")
        code, out = self.cli("init", self.project)
        self.assertEqual(code, 0, out)
        code, out = self.cli("new-card", self.project, card_id, "--requirement", str(requirement),
                             "--by-claude")
        self.assertEqual(code, 0, out)
        return self.collab / "cards" / card_id / "Claude亲自做.json"

    def test_cli_writes_marker_and_normal_card_has_no_marker(self):
        marker = self.new_card("CARD-005")
        self.assertEqual(json.loads(marker.read_text(encoding="utf-8")), {"by": "Claude"})

        requirement = Path(self.tmp) / "normal.md"
        requirement.write_text("# 普通卡\n\n## 要做什么\n改一处\n", encoding="utf-8")
        code, out = self.cli("new-card", self.project, "CARD-006", "--requirement", str(requirement))
        self.assertEqual(code, 0, out)
        self.assertFalse((self.collab / "cards" / "CARD-006" / "Claude亲自做.json").exists())

    def test_phase_and_view_hide_actions_and_honor_terminal_states(self):
        from collab.phase import card_phase
        self.make_card(card_id="CARD-001", state="开发中", plan=True)
        (self.collab / "cards" / "CARD-001" / "Claude亲自做.json").write_text(
            json.dumps({"by": "Claude"}), encoding="utf-8")
        phase = card_phase(self.project, "CARD-001")
        self.assertEqual(
            phase,
            {"code": "by_claude", "label": "Claude 亲自做", "tone": "run", "needs_you": False,
             "detail": "进度看下面的流转记录", "actions": []},
        )
        view = view_of(self.project)
        self.assertEqual(view["card"]["phase"], phase)
        self.assertEqual(view["now"], {"text": "Claude 亲自做", "tone": "run"})

        for state in ("已合并", "已放弃"):
            with self.subTest(state=state):
                cdir = self.make_card(card_id="CARD-002", state=state, plan=True)
                (cdir / "Claude亲自做.json").write_text(
                    json.dumps({"by": "Claude"}), encoding="utf-8")
                self.assertNotEqual(card_phase(self.project, "CARD-002")["code"], "by_claude")


class C12RateLimit(rf.Base):
    def test_rate_limit_stop_has_resume_advice(self):
        def ask(adapter, prompt, cwd, **kw):
            if adapter == "cursor":
                return AgentResult(
                    ok=False, attempts=1, final="",
                    output="ERROR: exceeded retry limit, last status: 429 Too Many Requests",
                )
            return self.ask_fn(adapter, prompt, cwd, **kw)

        self.verdicts = [FAIL, FAIL]
        from collab.dispatch import run_card
        card = run_card(Card("CARD-001", state="待派发"), self.collab, self.project, rf.CMD,
                        "cursor", "codex", ask_fn=rf.listed_ask(ask), judge_fn=self.judge_fn,
                        reviewer_adapter="agy", require_tests=True)[0]
        self.assertEqual(card.state, "需Claude介入")
        stop = [e for e in read_flow(self.cdir) if e.get("kind") == "转人工"][-1]
        self.assertIn("开发员（cursor）被服务限流（请求太多）停下了", stop["summary"])
        self.assertIn("建议在团队设置", stop["summary"])
        self.assertIn("或过一会儿再点“接着跑”", stop["summary"])
        self.assertIn("429 Too Many Requests", stop["say"])
        self.assertNotIn("模型没回话", stop["summary"] + stop["say"])


if __name__ == "__main__":
    unittest.main()
