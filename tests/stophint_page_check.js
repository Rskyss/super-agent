// 右栏停下提示（docs/右栏停下提示/施工单-右栏停下提示.md）验收：真的执行右栏脚本，看卡停下时顶部那行字。
// 由 tests/test_stophint_acceptance.py 调用。
"use strict";
const assert = require("assert");
const { page, settle, text } = require("./ability_69b_dom.js");

const LONG = "复查员复查不通过（已自动退回 2 次）。复查还剩 4 件事：……判断：自动退回用完了，还有严重问题。";

function openPanel(v) {
  const holder = { v };
  const p = page("panel.html", {
    search: "?project=%2Fp&clerk=1&token=t",
    respond(req) { return req.url.pathname === "/api/view" ? holder.v : { ok: true }; },
  });
  p.swap = async next => { holder.v = next; await p.advance(1600); };
  return p;
}
function view(phase) {
  return { card: { id: "CARD-021", title: "x", state: "需Claude介入", phase }, stations: [], handoffs: [],
           tokens: { total: 0, other: 0 }, now: { text: "", tone: "idle" }, installed: true, elapsed: 0 };
}

(async () => {
  const blocked = { code: "blocked", label: "卡住了", tone: "bad", needs_you: true, detail: LONG,
                    actions: ["resume", "revise", "accept_issues", "discard"] };
  const p = openPanel(view(blocked));
  await settle(); await settle();
  assert.strictEqual(text(p.el("phaseHint")), "卡住了（原因在中间卡片标题下）", "停下时顶部告诉用户原因在哪看");
  await p.swap(view(Object.assign({}, blocked, { detail: LONG + "又多了一句" })));
  assert.strictEqual(text(p.el("phaseHint")), "卡住了（原因在中间卡片标题下）", "刷新后也不铺长文字");
  // 原有行为：按钮照旧，别的阶段照旧“标签 · 说明”
  assert.deepStrictEqual(p.el("actionBtns").children.map(b => text(b)),
                         ["接着跑", "不满意，继续改", "接受剩下的问题，进入待确认", "放弃这个需求"]);
  await p.swap(view({ code: "plan_ready", label: "方案写好了", tone: "wait", detail: "请看方案，批准后开工",
                      actions: ["approve", "replan", "discard"] }));
  assert.strictEqual(text(p.el("phaseHint")), "方案写好了 · 请看方案，批准后开工", "别的阶段照旧");
  p.check();
  console.log("OK");
})().catch(err => { console.error(err && err.stack || err); process.exit(1); });
