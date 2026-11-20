// CARD-021：真的执行右栏脚本，卡停下时顶部告诉用户原因在中间卡片标题下。
// 由 tests/test_stophint_extra_card021.py 调用。
"use strict";
const assert = require("assert");
const { page, settle, text } = require("./ability_69b_dom.js");

const blockedReason = "复查员复查不通过（已自动退回 2 次）。复查还剩 4 件事：……判断：自动退回用完了，还有严重问题。";

function openPanel(viewData) {
  const holder = { viewData };
  const panel = page("panel.html", {
    search: "?project=%2Fp&clerk=1&token=t",
    respond(request) {
      return request.url.pathname === "/api/view" ? holder.viewData : { ok: true };
    },
  });
  panel.swap = async next => {
    holder.viewData = next;
    await panel.advance(1600);
  };
  return panel;
}

function view(phase) {
  return {
    card: { id: "CARD-021", title: "右栏停下提示", state: "需Claude介入", phase },
    stations: [], handoffs: [], tokens: { total: 0, other: 0 },
    now: { text: "", tone: "idle" }, installed: true, elapsed: 0,
  };
}

(async () => {
  const blocked = {
    code: "blocked", label: "卡住了", tone: "bad", needs_you: true,
    detail: blockedReason, actions: ["resume", "revise", "accept_issues", "discard"],
  };
  const panel = openPanel(view(blocked));
  await settle();
  await settle();
  assert.strictEqual(
    text(panel.el("phaseHint")),
    "卡住了（原因在中间卡片标题下）",
    "卡停下时右栏顶部写清原因在哪看",
  );
  assert.deepStrictEqual(
    panel.el("actionBtns").children.map(button => text(button)),
    ["接着跑", "不满意，继续改", "接受剩下的问题，进入待确认", "放弃这个需求"],
    "按钮照旧",
  );

  await panel.swap(view(Object.assign({}, blocked, { detail: blockedReason + "又多了一句。" })));
  assert.strictEqual(
    text(panel.el("phaseHint")),
    "卡住了（原因在中间卡片标题下）",
    "自动刷新后提示也不铺长文字",
  );

  await panel.swap(view({
    code: "plan_ready", label: "方案写好了", tone: "wait",
    detail: "请看方案，批准后开工", actions: ["approve", "replan", "discard"],
  }));
  assert.strictEqual(
    text(panel.el("phaseHint")),
    "方案写好了 · 请看方案，批准后开工",
    "别的阶段照旧“标签 · 说明”",
  );
  panel.check();
  console.log("OK");
})().catch(error => {
  console.error(error && error.stack || error);
  process.exit(1);
});
