"""第三批 D1 复查退回的红测：每条问题一个测试，只改这个新文件。"""
import unittest
from pathlib import Path

import test_redfirst_acceptance as rf
from acceptance_helpers import ProjectCase, needs_node, run_node
from collab.agents import AgentResult
from collab.dispatch import run_card
from collab.flow import read_flow
from collab.judge import FAIL
from collab.state import Card


class R1NewCardNeedsInitFirst(ProjectCase):
    def test_uninstalled_project_is_not_connected_before_requirement_is_read(self):
        gitignore = Path(self.project) / ".gitignore"
        gitignore.write_text(".env\n", encoding="utf-8")
        requirement = Path(self.tmp) / "not-exist.md"

        code, out = self.cli("new-card", self.project, "CARD-009",
                             "--requirement", str(requirement))

        self.assertEqual(code, 1)
        self.assertIn("请先执行 init", out)
        self.assertFalse(self.collab.exists())
        self.assertEqual(gitignore.read_text(encoding="utf-8"), ".env\n")


class R2RateLimitDetail(rf.Base):
    def test_right_panel_detail_says_rate_limit_even_when_error_has_more_output(self):
        def ask(adapter, prompt, cwd, **kw):
            if adapter == "cursor":
                return AgentResult(
                    ok=False, attempts=1, final="",
                    output="ERROR: 429 Too Many Requests\n后来又输出了别的内容",
                )
            return self.ask_fn(adapter, prompt, cwd, **kw)

        self.verdicts = [FAIL, FAIL]
        card = run_card(Card("CARD-001", state="待派发"), self.collab, self.project, rf.CMD,
                        "cursor", "codex", ask_fn=rf.listed_ask(ask), judge_fn=self.judge_fn,
                        reviewer_adapter="agy", require_tests=True)[0]
        self.assertEqual(card.state, "需Claude介入")
        self.assertTrue([e for e in read_flow(self.cdir) if e.get("kind") == "转人工"])

        from desktop.view import card_view
        detail = card_view(self.project, "CARD-001")["card"]["phase"]["detail"]
        self.assertIn("开发员（cursor）被服务限流（请求太多）停下了", detail)
        self.assertIn("过一会儿再点“接着跑”", detail)
        self.assertNotIn("后来又输出了别的内容", detail)


@needs_node()
class R3UsageTooltipOnlyOnUsage(unittest.TestCase):
    def test_station_without_usage_has_no_usage_update_note(self):
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
const station = {key:'you', role:'提需求', who:'你', face:'你', state:'done',
                 detail:'已提需求'};
const payload = {installed:true, card:{id:'CARD-001'}, stations:[station], handoffs:[],
                 tokens:{total:0, other:0}};
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
  assert.ok(button.title.includes('已提需求'));
  assert.ok(!button.title.includes('每答完一轮才更新一次'));
})().catch(e => {console.error(e); process.exitCode = 1;});
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
