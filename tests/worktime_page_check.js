// 外部项目试跑修复 T5（⑪，用户点名）：右栏“本卡合计”下面加一行干活总耗时。真的执行右栏脚本，看用户看到的样子。
// 由 tests/test_external_trial_fixes.py 调用。
"use strict";
const assert = require("assert");
const { page, settle, text } = require("./ability_69b_dom.js");

function view(extra) {
  return Object.assign({ card: { id: "CARD-001", title: "竞赛组过程工作表", state: "需Claude介入", phase: null },
    stations: [], handoffs: [], tokens: { total: 12667000, other: 1985000 }, now: { text: "", tone: "idle" },
    installed: true, elapsed: 0, back: false, stale: false }, extra || {});
}
function openPanel(v) {
  const holder = { v };
  const p = page("panel.html", {
    search: "?project=%2Fp&clerk=1&token=t",
    respond(req) { return req.url.pathname === "/api/view" ? holder.v : { ok: true }; },
  });
  p.swap = async next => { holder.v = next; await p.advance(1600); };
  return p;
}

const failed = [];
async function part(name, fn) { try { await fn(); } catch (e) { failed.push(name + "：" + (e && e.message)); } }

(async () => {
  await part("1 合计下面写干活总耗时", async () => {
    const p = openPanel(view({ work_seconds: 6780 }));
    await settle(); await settle();
    const flow = text(p.el("pane-flow"));
    assert.ok(flow.includes("干活共 1 小时 53 分"), "train 那张卡约 1 小时 53 分：" + flow.slice(-120));
    assert.ok(flow.indexOf("干活共") > flow.indexOf("本卡合计"), "写在合计下面");
    p.check();
  });
  await part("2 短的写分钟", async () => {
    const p = openPanel(view({ work_seconds: 7 * 60 + 20 }));
    await settle(); await settle();
    assert.ok(text(p.el("pane-flow")).includes("干活共 7 分钟"), text(p.el("pane-flow")).slice(-80));
    const p2 = openPanel(view({ work_seconds: 40 }));
    await settle(); await settle();
    assert.ok(text(p2.el("pane-flow")).includes("干活共不到 1 分钟"), text(p2.el("pane-flow")).slice(-80));
    p.check(); p2.check();
  });
  await part("3 没开跑的卡不写，换卡不残留", async () => {
    const p = openPanel(view({ work_seconds: 6780 }));
    await settle(); await settle();
    await p.swap(view({ work_seconds: null }));
    assert.ok(!text(p.el("pane-flow")).includes("干活共"), "换到没开跑的卡，耗时不残留");
    const p2 = openPanel(view({ tokens: { total: 0, other: 0 } }));
    await settle(); await settle();
    assert.ok(!text(p2.el("pane-flow")).includes("干活共"), "没有耗时数据不写");
    p.check(); p2.check();
  });
  await part("4 换卡读不到时不留上一张卡的耗时（复查第 12 条）", async () => {
    const p = openPanel(view({ work_seconds: 6780 }));
    await settle(); await settle();
    await p.swap({ error: "读不到这张卡" });
    assert.ok(!text(p.el("pane-flow")).includes("干活共"), "读不到新卡时不显示上一张卡的耗时");
    p.check();
  });
  await part("5 换卡时请求断了也不留上一张卡的耗时（复查第 2 轮第 12 条）", async () => {
    const holder = { v: view({ work_seconds: 6780 }), broken: false };
    const p = page("panel.html", {
      search: "?project=%2Fp&clerk=1&token=t",
      respond(req) {
        if (req.url.pathname === "/api/view") {
          // 响应读不了（连接中途断开）：json() 失败，走右栏的 catch
          if (holder.broken) return { status: 200, ok: true, json: async () => { throw new Error("响应读不了"); } };
          return holder.v;
        }
        return { ok: true };
      },
    });
    await settle(); await settle();
    assert.ok(text(p.el("pane-flow")).includes("干活共"), "先显示");
    holder.broken = true;
    await p.advance(1600);
    assert.ok(!text(p.el("pane-flow")).includes("干活共"), "读不到时不留旧耗时");
    p.check();
  });
  if (failed.length) { console.error(failed.join("\n")); process.exit(1); }
  console.log("ok");
})();
