"use strict";

const assert = require("assert");
const { page, settle, visible, text, Element } = require("./ability_69b_dom.js");

const LONG = "卡住原因是复查退回了四件事：第一件是行政恢复卡看不见，第二件是主对话草稿丢失，"
  + "第三件是展开状态没有保留，第四件是换卡后旧内容还在。";

function station(key, role, state, extra) {
  return Object.assign({
    key, role, state, who: key === "you" || key === "accept" ? "你" : "Codex",
    face: "C", detail: state === "idle" ? "" : role + "的说明",
  }, extra || {});
}

function stations() {
  return [
    station("you", "提需求", "done"), station("disp", "写方案", "done"),
    station("test", "写测试", "done"), station("dev", "写代码", "done"),
    station("check", "跑测试", "done"), station("rev", "复查", "done"),
    station("accept", "确认", "idle"),
  ];
}

function view(extra) {
  return Object.assign({
    card: { id: "CARD-019", title: "切换项目", state: "测试中", phase: null },
    stations: stations(), handoffs: [], tokens: { total: 0, other: 0 },
    now: { text: "", tone: "idle" }, installed: true, elapsed: 0,
    back: false, stale: false,
  }, extra || {});
}

const failures = [];
async function part(name, check) {
  try { await check(); } catch (error) { failures.push(name + "：" + (error && error.message)); }
}

(async () => {
  if (process.env.CARD020_REWORK_VIEW) {
    await part("中间说明要完整", async () => {
      const data = JSON.parse(process.env.CARD020_REWORK_VIEW);
      const pane = page("chat.html", {
        search: "?project=%2Fp&token=t&clerk=1",
        respond(request) {
          const path = request.url.pathname;
          if (path === "/api/view") return data;
          if (path === "/api/cards") return [];
          if (path === "/api/clerk/list") return { threads: [], current: null };
          if (path === "/api/clerk/history" || path === "/api/clerk/events") {
            return { events: [], open: true, busy: false };
          }
          return { ok: true };
        },
      });
      pane.context.SuperAgentChat.openCard({
        id: "CARD-019", title: "切换项目", phase: { label: "卡住了" },
      });
      await settle(); await settle();
      const wholeReason = String(data.card.phase.detail || "");
      assert.ok(wholeReason.includes("④ 切回项目时卡片还没出现就再切走"), "数据里要有第四个问题");
      assert.ok(wholeReason.includes("建议：点“不满意，继续改”"), "数据里要有处理建议");
      assert.ok(text(pane.el("cardNow")).includes(wholeReason),
                "中间说明要看得到全文：" + text(pane.el("cardNow")));
      pane.check();
    });
  }

  await part("展开后收起按钮还在", async () => {
    const pane = page("chat.html", {
      search: "?project=%2Fp&token=t&clerk=1",
      before(document) {
        document.createElement = tag => {
          const element = new Element(tag);
          Object.defineProperty(element, "scrollHeight", { get: () => 300, configurable: true });
          Object.defineProperty(element, "clientHeight", {
            get: () => (element.classList.contains("fold") ? 20 : 300),
            configurable: true,
          });
          return element;
        };
      },
      respond(request) {
        const path = request.url.pathname;
        if (path === "/api/view") return { now: { text: LONG, tone: "bad" }, live: false };
        if (path === "/api/cards") return [];
        if (path === "/api/clerk/list") return { threads: [], current: null };
        if (path === "/api/clerk/history" || path === "/api/clerk/events") {
          return { events: [], open: true, busy: false };
        }
        return { ok: true };
      },
    });
    pane.context.SuperAgentChat.openCard({
      id: "CARD-019", title: "切换项目", phase: { label: "卡住了" },
    });
    await settle(); await settle();
    const button = pane.document.getElementById("cardFold");
    assert.ok(button && visible(button) && text(button) === "展开");
    button.click();
    await settle();
    assert.ok(visible(button), "展开后按钮不能消失");
    assert.strictEqual(text(button), "收起");
    pane.check();
  });

  await part("大额用量沿用万字", async () => {
    const pane = page("panel.html", {
      search: "?project=%2Fp&clerk=1&token=t",
      respond(request) {
        if (request.url.pathname === "/api/view") {
          return view({
            stations: stations().map(item => item.key === "test" ? Object.assign(item, {
              state: "active", rounds: 4, elapsed: 119, quiet: 3,
              tokens: { ok: true, total: 2552000, input: 2500000, cached: 2000000, output: 52000 },
            }) : item),
          });
        }
        return { ok: true };
      },
    });
    await settle(); await settle();
    const row = pane.el("steps").children[2].children[0];
    assert.strictEqual(text(row.querySelector(".u")), "255.2 万 token");
    assert.ok(!text(row.querySelector(".k")).includes("token"));
    pane.check();
  });

  if (failures.length) {
    console.error(failures.join("\n"));
    process.exit(1);
  }
  console.log("OK");
})().catch(error => {
  console.error(error && error.stack || error);
  process.exit(1);
});
