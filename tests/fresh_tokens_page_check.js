// 外部项目试跑修复第二批 ㉓ ⑨：本卡合计旁写“新消耗”，各步提示里说清小帮手算在合计。真的执行右栏脚本。
// 由 tests/test_external_trial_batch2.py 调用。
"use strict";
const assert = require("assert");
const { page, settle, text } = require("./ability_69b_dom.js");

function openPanel(v) {
  const p = page("panel.html", {
    search: "?project=%2Fp&clerk=1&token=t",
    respond(req) { return req.url.pathname === "/api/view" ? v : { ok: true }; },
  });
  return p;
}
const base = { card: { id: "CARD-001", title: "卡", state: "待验收", phase: null }, handoffs: [],
  now: { text: "", tone: "idle" }, installed: true, elapsed: 0, back: false, stale: false };

const failed = [];
async function part(name, fn) { try { await fn(); } catch (e) { failed.push(name + "：" + (e && e.message)); } }

(async () => {
  await part("1 合计旁写新消耗", async () => {
    const p = openPanel(Object.assign({}, base, { stations: [],
      tokens: { total: 94771012, other: 10319884 }, tokens_fresh: 13062212 }));
    await settle(); await settle();
    const flow = text(p.el("pane-flow"));
    assert.ok(flow.includes("新消耗约"), flow.slice(-160));
    assert.ok(p.el("tokenTotal").title.includes("缓存读取"), "悬停说明解释缓存：" + p.el("tokenTotal").title);
    p.check();
  });
  await part("2 老数据没有 fresh 时照旧", async () => {
    const p = openPanel(Object.assign({}, base, { stations: [], tokens: { total: 1000, other: 0 } }));
    await settle(); await settle();
    assert.ok(!text(p.el("pane-flow")).includes("新消耗约"));
    p.check();
  });
  if (failed.length) { console.error(failed.join("\n")); process.exit(1); }
})();
