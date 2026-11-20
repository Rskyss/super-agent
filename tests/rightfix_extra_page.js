"use strict";
const assert = require("assert");
const { page, settle, visible, text } = require("./ability_69b_dom.js");

const REASON = "卡住原因是复查退回了四件事：第一件是行政恢复卡看不见，第二件是主对话草稿丢失，"
  + "第三件是展开状态没有保留，第四件是换卡后旧内容还在。";

function station(key, role, state, extra) {
  return Object.assign({
    key, role, state,
    who: key === "you" || key === "accept" ? "你" : "Codex",
    face: "C",
    detail: state === "idle" ? "" : role + "的说明",
  }, extra || {});
}

function seven(overrides) {
  const list = [
    station("you", "提需求", "done"),
    station("disp", "写方案", "done"),
    station("test", "写测试", "done"),
    station("dev", "写代码", "done"),
    station("check", "跑测试", "done"),
    station("rev", "复查", "done"),
    station("accept", "确认", "idle"),
  ];
  (overrides || []).forEach(override => {
    const index = list.findIndex(item => item.key === override.key);
    list[index] = Object.assign(list[index], override);
  });
  return list;
}

function view(extra) {
  return Object.assign({
    card: { id: "CARD-019", title: "切换项目", state: "测试中", phase: null },
    stations: seven(),
    handoffs: [],
    tokens: { total: 0, other: 0 },
    now: { text: "", tone: "idle" },
    installed: true,
    elapsed: 0,
    back: false,
    stale: false,
  }, extra || {});
}

function openPanel(data) {
  const holder = { data };
  const pane = page("panel.html", {
    search: "?project=%2Fp&clerk=1&token=t",
    respond(request) {
      return request.url.pathname === "/api/view" ? holder.data : { ok: true };
    },
  });
  pane.swap = async next => {
    holder.data = next;
    await pane.advance(1600);
  };
  return pane;
}

function rows(pane) {
  return pane.el("steps").children.map(item => item.children[0]);
}

function shownRows(pane) {
  return pane.el("steps").children.filter(item => visible(item) && visible(item.children[0]));
}

function descendants(element) {
  return [element].concat(...element.children.map(descendants));
}

const failures = [];

async function part(name, check) {
  try {
    await check();
  } catch (error) {
    failures.push(name + "：" + (error && error.message));
  }
}

(async () => {
  await part("token 用量在首次、数据刷新和计时刷新都独占一行", async () => {
    const tokens = { ok: true, total: 2552000, input: 2500000, cached: 2000000, output: 52000 };
    const active = view({
      stations: seven([{ key: "test", state: "active", rounds: 4, elapsed: 119, quiet: 3, tokens }]),
    });
    const pane = openPanel(active);
    await settle(); await settle();
    const button = rows(pane)[2];
    const status = button.querySelector(".k");
    assert.ok(/第 4 轮/.test(text(status)) && /已干/.test(text(status)));
    assert.ok(!/token/.test(text(status)), "状态行不能混着 token：" + text(status));
    const usage = descendants(button).filter(item => item !== button && item.children.length === 0 && /token/.test(text(item)));
    assert.deepStrictEqual(usage.map(text), ["255.2 万 token"]);

    await pane.swap(view({
      stations: seven([{ key: "test", state: "active", rounds: 5, elapsed: 120, quiet: 1,
        tokens: { ok: true, total: 1200, input: 1000, output: 200 } }]),
    }));
    assert.ok(!/token/.test(text(button.querySelector(".k"))), "数据刷新后状态行仍不能混 token");
    const nextUsage = descendants(button).filter(item => item !== button && item.children.length === 0 && /token/.test(text(item)));
    assert.deepStrictEqual(nextUsage.map(text), ["1,200 token"], "数据刷新要换成新的用量");

    await pane.advance(500);
    assert.ok(/已干/.test(text(button.querySelector(".k"))), "计时刷新仍显示用时");
    const timerUsage = descendants(button).filter(item => item !== button && item.children.length === 0 && /token/.test(text(item)));
    assert.deepStrictEqual(timerUsage.map(text), ["1,200 token"], "计时刷新不能重画回旧用量");

    const finished = view({
      stations: seven([{ key: "dev", state: "done", rounds: 2, spent: 300, tokens }]),
    });
    const finishedPane = openPanel(finished);
    await settle(); await settle();
    const finishedButton = rows(finishedPane)[3];
    assert.ok(/共 2 轮/.test(text(finishedButton.querySelector(".k"))));
    assert.ok(!/token/.test(text(finishedButton.querySelector(".k"))));
    const finishedUsage = descendants(finishedButton).filter(item => item !== finishedButton && item.children.length === 0 && /token/.test(text(item)));
    assert.deepStrictEqual(finishedUsage.map(text), ["255.2 万 token"]);
    pane.check(); finishedPane.check();
  });

  await part("卡住时右栏顶部只剩卡住了和按钮", async () => {
    const phase = {
      code: "blocked", label: "卡住了", tone: "bad", needs_you: true, detail: REASON,
      actions: ["resume", "revise", "accept_issues", "discard"],
    };
    const pane = openPanel(view({
      card: { id: "CARD-019", title: "切换项目", state: "需Claude介入", phase },
    }));
    await settle(); await settle();
    assert.strictEqual(text(pane.el("phaseHint")), "卡住了");
    assert.deepStrictEqual(pane.el("actionBtns").children.map(item => text(item)), [
      "接着跑", "不满意，继续改", "接受剩下的问题，进入待确认", "放弃这个需求",
    ]);
    await pane.swap(view({
      card: { id: "CARD-019", title: "切换项目", state: "需Claude介入", phase },
    }));
    assert.strictEqual(text(pane.el("phaseHint")), "卡住了", "刷新后长原因不能回到顶部");
    pane.check();
  });

  await part("接手格、复查格和完成时间在换卡时都干净", async () => {
    const eight = seven([{ key: "rev", state: "done", detail: "复查退回 4 件，已由 Claude 接手修好" }]);
    eight.splice(6, 0, station("takeover", "Claude 接手", "done", {
      who: "Claude", detail: "改了 4 处 · Codex 复查 2 轮 · 2 条记进以后再修",
    }));
    eight[7].state = "done";
    const completed = view({
      stations: eight,
      tokens: { total: 19632638, other: 937588 },
      finished_at: 1795150840.8,
      card: { id: "CARD-019", title: "切换项目", state: "已合并", phase: null },
    });
    const pane = openPanel(completed);
    await settle(); await settle();
    const shown = shownRows(pane);
    assert.strictEqual(shown.length, 8);
    assert.ok(/Claude 接手/.test(text(shown[6])) && /改了 4 处/.test(text(shown[6])));
    assert.ok(/复查/.test(text(shown[5])) && /已由 Claude 接手修好/.test(text(shown[5])));
    assert.ok(/确认/.test(text(shown[7])));
    const flowText = text(pane.el("pane-flow"));
    assert.ok(flowText.includes("本卡合计"));
    assert.ok(flowText.includes("完成于 11-20 13:00"));
    assert.ok(flowText.indexOf("完成于") > flowText.indexOf("本卡合计"));

    const noTotal = view({
      stations: seven(), tokens: { total: 0, other: 0 },
      finished_at: 1795150840.8,
      card: { id: "CARD-020", title: "另一张卡", state: "已合并", phase: null },
    });
    const noTotalPane = openPanel(noTotal);
    await settle(); await settle();
    assert.ok(text(noTotalPane.el("pane-flow")).includes("完成于 11-20 13:00"), "没有合计 token 也要写完成时间");

    const unfinished = view({ tokens: { total: 1000, other: 0 } });
    await pane.swap(unfinished);
    assert.strictEqual(shownRows(pane).length, 7, "换到没接手的卡要藏起第 8 格");
    assert.ok(!/Claude 接手/.test(text(pane.el("steps"))));
    assert.ok(!text(pane.el("pane-flow")).includes("完成于"), "换到未完成卡不能留下完成时间");
    pane.check(); noTotalPane.check();
  });

  await part("中间卡片标题下的原因默认收起且状态不乱跑", async () => {
    // Claude 接手：后台给中间栏的就是完整原因（desktop/view.py 停下时用阶段说明全文），假数据照真的给；
    // 原来这里给截短的话、要页面拿打开时的旧说明补全，逼出了“新旧两条进度同时显示”的写法（复查第 2、3 轮）
    let nowText = "卡停下了，等你处理：" + REASON;
    const pane = page("chat.html", {
      search: "?project=%2Fp&token=t&clerk=1",
      before(document) {
        document.createElement = tag => {
          const element = require("./ability_69b_dom.js").Element;
          const created = new element(tag);
          Object.defineProperty(created, "scrollHeight", {
            get: () => (created.textContent.length > 40 ? 300 : 100),
            configurable: true,
          });
          Object.defineProperty(created, "clientHeight", {
            get: () => (created.classList.contains("open") ? 300 : 100),
            configurable: true,
          });
          return created;
        };
      },
      respond(request) {
        const path = request.url.pathname;
        if (path === "/api/view") return { now: { text: nowText, tone: "bad" }, live: false };
        if (path === "/api/cards") return [];
        if (path === "/api/clerk/list") return { threads: [], current: null };
        if (path === "/api/clerk/history" || path === "/api/clerk/events") {
          return { events: [], open: true, busy: false };
        }
        return { ok: true };
      },
    });
    const chat = pane.context.SuperAgentChat;
    chat.openCard({ id: "CARD-019", title: "切换项目", phase: { label: "卡住了", detail: REASON } });
    await settle(); await settle();
    const line = pane.el("cardNow");
    assert.ok(line && visible(line));
    assert.ok(text(line).includes(REASON), "长原因要完整，不能只剩前半段：" + text(line));
    assert.ok(line.classList.contains("fold"), "长原因默认收起");
    const foldButton = () => pane.document.querySelectorAll("button").find(
      item => item.id === "cardFold" && /^(展开|收起)$/.test(text(item)),
    );
    assert.ok(foldButton() && visible(foldButton()) && text(foldButton()) === "展开");
    foldButton().click();
    await settle();
    assert.ok(!line.classList.contains("fold"));
    assert.ok(line.classList.contains("open"));
    assert.ok(foldButton() && visible(foldButton()) && text(foldButton()) === "收起",
              "展开后长原因还要能再点收起");

    foldButton().click();
    await settle();
    assert.ok(line.classList.contains("fold"), "点收起后要回到一行");
    assert.ok(!line.classList.contains("open"));
    assert.strictEqual(text(foldButton()), "展开");

    foldButton().click();
    await settle();
    assert.ok(!line.classList.contains("fold"));

    await pane.advance(3000);
    assert.ok(!pane.el("cardNow").classList.contains("fold"), "刷新后不能自己收起");
    assert.ok(text(pane.el("cardNow")).includes(REASON), "刷新后长原因仍要完整");

    nowText = "已接手修好";
    await pane.advance(3000);
    assert.strictEqual(text(pane.el("cardNow")), "已接手修好");
    assert.ok(!foldButton() || !visible(foldButton()), "文字放得下时藏起按钮");

    nowText = "卡停下了，等你处理：" + REASON;   // 换卡后后台给的是这张卡的完整原因（Claude 接手：假数据照真的给）
    chat.openCard({ id: "CARD-020", title: "另一张卡", phase: { label: "卡住了", detail: REASON } });
    await settle(); await settle();
    assert.ok(pane.el("cardNow").classList.contains("fold"), "换卡要回到收起");
    assert.ok(text(pane.el("cardNow")).includes(REASON), "换卡后要显示这张卡的原因");

    nowText = "一行就放得下";
    chat.openCard({ id: "CARD-021", title: "短原因卡", phase: { label: "已完成" } });
    await settle(); await settle();
    assert.strictEqual(text(pane.el("cardNow")), "一行就放得下");
    assert.ok(!foldButton() || !visible(foldButton()), "短原因一开始就不该有展开按钮");
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
