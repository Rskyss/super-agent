"""施工单 B 补充：缺失字段、零值、鼠标提示、首次绘制与定时刷新。

用本地 Node 执行真实 panel.js，不打开浏览器；页面替身只提供 DOM 和时钟。
"""
import unittest
from pathlib import Path

from acceptance_helpers import needs_node, run_node


@needs_node()
class PanelBoundaryText(unittest.TestCase):
    def check_js(self, body):
        result = run_node("const assert = require('assert');\n"
                          "const P = require('./desktop/static/panel.js');\n" + body)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_zero_time_usage_and_default_since_are_not_dropped(self):
        self.check_js(r'''
assert.strictEqual(P.metaText({state:'active', rounds:1, elapsed:0, quiet:0,
  tokens:{ok:true,total:0}}), '已干 0:00 · 0 秒前有动静 · 0 token');
assert.strictEqual(P.metaText({state:'done', rounds:1, spent:0,
  tokens:{ok:true,total:0}}), '0:00 · 0 token');
assert.strictEqual(P.metaText({state:'active', elapsed:59, quiet:59}, 1),
  '已干 1:00 · 1 分钟前有动静');
''')

    def test_every_missing_segment_is_omitted_without_stray_separators(self):
        self.check_js(r'''
const cases = [
 [{state:'active'}, ''],
 [{state:'active', rounds:2}, '第 2 轮'],
 [{state:'active', elapsed:7}, '已干 0:07'],
 [{state:'active', quiet:7}, '7 秒前有动静'],
 [{state:'active', tokens:{ok:false}}, '用量 —（读不到）'],
 [{state:'done'}, ''],
 [{state:'done', spent:5}, '0:05'],
 [{state:'fail', rounds:2}, '共 2 轮'],
 [{state:'wait', rounds:3, spent:99, elapsed:8, quiet:5,
   tokens:{ok:false,reason:'会话丢失'}}, '用量 —（会话丢失）'],
 [{state:'wait', rounds:3, spent:99}, ''],
 [{state:'idle', rounds:3, spent:99, tokens:{ok:false}}, '']
];
for (const [station, expected] of cases) {
  assert.strictEqual(P.metaText(station), expected, JSON.stringify(station));
}
''')

    def test_partial_history_tooltip_keeps_zero_and_omits_missing_values(self):
        self.check_js(r'''
assert.strictEqual(P.stepTitle({detail:'汇报：处理完了\n全文还在', rounds:4, history:[
  {seconds:null,tokens:1234}, {seconds:0,tokens:0},
  {seconds:61,tokens:null}, {seconds:null,tokens:null}],
  tokens:{ok:false,reason:'没有读到'}}),
  '处理完了\n全文还在\n第 1 轮 1,234 token\n第 2 轮 0:00 · 0 token\n第 3 轮 1:01\n第 4 轮');
assert.strictEqual(P.stepTitle({detail:'结论：检查通过',rounds:2}), '检查通过');
assert.strictEqual(P.stepTitle({rounds:2,history:[{seconds:null,tokens:0}]}), '第 1 轮 0 token');
assert.strictEqual(P.totalTitle({total:12}), '');
assert.strictEqual(P.totalTitle({total:12,other:0}), '');
''')

    def test_old_formatters_are_removed_and_clock_helpers_have_one_definition(self):
        # 删除旧函数是施工单明确的验收项，因此这里额外检查导出和定义。
        self.check_js(r'''
assert.strictEqual(P.tokenText, undefined, '旧用量函数应删除');
const fs = require('fs');
const source = fs.readFileSync('./desktop/static/panel.js', 'utf8');
assert.ok(!/\bfunction\s+clockText\s*\(/.test(source), '旧计时函数应删除');
for (const name of ['mmss','ago']) {
  const definitions = source.match(new RegExp('\\bfunction\\s+' + name + '\\s*\\(', 'g')) || [];
  assert.strictEqual(definitions.length, 1, name + '只能保留一份');
}
''')


DOM_SETUP = r'''
const assert = require('assert'), fs = require('fs'), vm = require('vm');
class Element {
  constructor() {
    this.textContent=''; this.innerHTML=''; this.hidden=true; this.title='';
    this.style={}; this.children=[]; this.selectors=new Map(); this.attrs={};
    this.classList={add(){},remove(){},toggle(){}};
  }
  querySelector(s) {
    if (!this.selectors.has(s)) this.selectors.set(s,new Element());
    return this.selectors.get(s);
  }
  addEventListener() {}
  appendChild(child) {this.children.push(child);}
  setAttribute(k,v) {this.attrs[k]=v;}
  getAttribute(k) {return this.attrs[k];}
}
const html=fs.readFileSync('./desktop/static/panel.html','utf8');
const ids=new Map([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
const $=id=>ids.get(id)||null;
let now=100000, poll, tick;
class Clock extends Date {static now(){return now;}}
let station={key:'dev',role:'写代码',who:'Codex',state:'active',rounds:2,
  detail:'汇报：正在检查刷新',elapsed:59,quiet:59,tokens:{ok:true,total:1200}};
let payload={installed:true,card:{id:'CARD-001'},stations:[station],handoffs:[],
  tokens:{total:1234,other:34}};
const context={console,URLSearchParams,Date:Clock,module:{exports:{}},
  location:{search:'?project=fake'},window:{},
  document:{getElementById:$,createElement:()=>new Element()},
  fetch:async()=>({json:async()=>payload}),
  setTimeout:fn=>{poll=fn;},setInterval:fn=>{tick=fn;}};
vm.runInNewContext(fs.readFileSync('./desktop/static/panel.js','utf8'),context);
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
const button=()=>$('steps').children[0].querySelector('button');
const update=async(s,total)=>{
  payload={...payload,stations:[s],tokens:total||payload.tokens};
  poll(); await flush();
};
'''


@needs_node()
class PanelRenderingEdges(unittest.TestCase):
    def check_dom(self, body):
        result = run_node(DOM_SETUP + "\n(async()=>{await flush();\n" + body
                          + "\n})().catch(e=>{console.error(e);process.exitCode=1;});")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_timer_crosses_minute_and_refresh_uses_same_formatter(self):
        self.check_dom(r'''
const b=button();
assert.strictEqual(b.querySelector('.k').textContent,
  '第 2 轮 · 已干 0:59 · 59 秒前有动静 · 1,200 token');
const original=context.module.exports.metaText;
assert.strictEqual(typeof original,'function');
// 给同一个入口挂探针：首次重绘和定时刷新都必须通过它。
context.module.exports.metaText=(s,since)=>'统一入口 '+original(s,since);
await update(station);
assert.strictEqual(b.querySelector('.k').textContent,
  '统一入口 第 2 轮 · 已干 0:59 · 59 秒前有动静 · 1,200 token');
now+=2000; tick();
assert.strictEqual(b.querySelector('.k').textContent,
  '统一入口 第 2 轮 · 已干 1:01 · 1 分钟前有动静 · 1,200 token');
assert.strictEqual(b.querySelector('.d').textContent,'正在检查刷新');
''')

    def test_active_to_done_updates_history_and_clears_old_total_tooltip(self):
        self.check_dom(r'''
assert.strictEqual($('tokenTotal').title,'其中交接资料等其他 34 token\n每答完一轮才更新一次');   // 第三批 C10
await update({...station,state:'done',elapsed:null,quiet:null,spent:61,
  detail:'汇报：全部处理好了\n这里是完整说明',history:[{seconds:60,tokens:1000},{seconds:1,tokens:200}]},
  {total:1200,other:0});
const b=button();
assert.strictEqual(b.querySelector('.k').textContent,'共 2 轮 · 1:01 · 1,200 token');
assert.ok(b.title.includes('全部处理好了\n这里是完整说明'));
assert.ok(b.title.includes('第 1 轮 1:00 · 1,000 token'));
assert.ok(b.title.includes('第 2 轮 0:01 · 200 token'));
assert.strictEqual($('tokenTotal').textContent,'本卡合计 1,200 token');
assert.ok(!$('tokenTotal').title.includes('其中交接资料'),'取数后不能留着上次其他用量提示');   // 第三批 C10：只剩“每答完一轮才更新一次”
assert.strictEqual($('tokenTotal').title,'每答完一轮才更新一次');
const done=b.querySelector('.k').textContent;
now+=60000; tick();
assert.strictEqual(b.querySelector('.k').textContent,done,'已完成的用时不能继续涨');
await update({...station,state:'idle'}, {total:0,other:0});
assert.strictEqual(b.querySelector('.k').textContent,'');
assert.strictEqual(b.querySelector('.d').textContent,'');
assert.strictEqual($('tokenTotal').hidden,true);
assert.strictEqual($('tokenTotal').title,'');
''')

    def test_no_clock_and_failed_usage_stay_identical_after_tick(self):
        self.check_dom(r'''
for (const [tokens,expected] of [
  [null,''], [{ok:false,reason:'没有会话'},'用量 —（没有会话）'],
  [{ok:true,total:0},'0 token'],
  [{ok:true,total:1200,missing:2},'1,200 token（有 2 轮没读到）']]) {
  await update({...station,rounds:1,elapsed:null,quiet:null,tokens});
  assert.strictEqual(button().querySelector('.k').textContent,expected,'首次绘制');
  now+=500; tick();
  assert.strictEqual(button().querySelector('.k').textContent,expected,'定时刷新');
}
''')


class TotalLayout(unittest.TestCase):
    def test_total_has_separator_in_existing_color_and_spacing(self):
        # 无浏览器任务：检查明确要求的 CSS 声明；页面文字效果由上面的 Node 测试覆盖。
        import re
        css = (Path(__file__).resolve().parents[1] / "desktop/static/panel.css").read_text(encoding="utf-8")
        rule = re.search(r"\.token-total\s*\{([^}]+)\}", css)
        self.assertIsNotNone(rule)
        block = rule.group(1)
        self.assertRegex(block, r"border-top\s*:\s*1px\s+solid\s+var\(--hair(?:-2)?\)")
        for prop in ("margin", "padding"):
            # 简写第一项也是顶部距离；不把左侧留白误当成分隔线上下的留白。
            values = re.findall(r"(?:^|;)\s*" + prop + r"(?:-top)?\s*:\s*([^;]+)", block)
            self.assertTrue(values, prop + "需要顶部留白")
            self.assertRegex(values[-1].strip(), r"^(?:[1-9]\d*(?:\.\d+)?|0?\.\d*[1-9]\d*)(?:px|rem|em)\b|^var\(--")


if __name__ == "__main__":
    unittest.main()
