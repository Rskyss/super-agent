// 复查退回第 1 条的自动化页面实跑检查：
// 行政对话（主对话、普通卡片、积木库管家）每轮查完消息后应保持零等待继续下一轮轮询，
// 不得额外多等 50ms。通过 ability_69b_dom.js 真实执行 chat.html 与 chat.js。
"use strict";
const assert = require("assert");
const { page, settle, text } = require("./ability_69b_dom.js");

function makePlainCardView(cardId) {
  return {
    installed: true,
    project: "A",
    card: { id: cardId, title: "普通卡片", state: "施工中", rework: 0, round: 1, phase: { label: "施工中" } },
    cards: [],
    handoffs: [],
    back: false,
    stale: false,
    elapsed: 10,
    tokens: { total: 0, other: 0 },
    stations: [],
    now: { text: "开发员正在施工", tone: "busy" },
    live: true,
    quiet: 2,
    worker_elapsed: 10,
    walk_shots: [],
  };
}

function createScenarioPage({ search, listResponse, convId, firstEvents }) {
  let ctx;
  const eventsBySeq = firstEvents.slice();
  return page("chat.html", {
    search,
    before(document, context) {
      ctx = context;
      const origCreate = document.createElement.bind(document);
      document.createElement = tag => {
        const el = origCreate(tag);
        Object.defineProperty(el, "scrollHeight", { get: () => 20, configurable: true });
        return el;
      };
    },
    respond(req) {
      const p = req.url.pathname;
      if (p === "/api/cards") return [];
      if (p === "/api/view") {
        return makePlainCardView(req.url.searchParams.get("card") || "CARD-001");
      }
      if (p === "/api/clerk/list") return listResponse;
      if (p === "/api/clerk/focus") return { ok: true };
      if (p === "/api/clerk/events") {
        // 照真实后台 desktop/clerk/desk.py 的 events() 行为：
        // 有新事件（seq > since）立刻返回；无新事件且 wait > 0 时挂起 wait 秒再返回空。
        const cid = req.url.searchParams.get("conversation") || "";
        const since = Number(req.url.searchParams.get("since") || 0);
        const waitSec = Number(req.url.searchParams.get("wait") || 0);
        if (cid !== convId) {
          return { events: [], open: false, busy: false };
        }
        const fresh = eventsBySeq.filter(ev => ev.seq > since);
        if (fresh.length > 0) {
          return { events: fresh, open: true, busy: true };
        }
        if (waitSec > 0 && ctx) {
          return new Promise(resolve => {
            ctx.setTimeout(() => resolve({ events: [], open: true, busy: false }), waitSec * 1000);
          });
        }
        return { events: [], open: true, busy: false };
      }
      return { ok: true };
    },
  });
}

function clerkEventRequests(p, convId) {
  return p.requests.filter(
    req => req.url.pathname === "/api/clerk/events" && req.url.searchParams.get("conversation") === convId
  );
}

function pendingTimerDelays(p) {
  return [...p.timers.values()].filter(t => !t.repeat).map(t => t.delay);
}

async function verifyZeroDelayPolling(label, p, convId, expectedMessage) {
  const afterFirst = clerkEventRequests(p, convId);
  assert.strictEqual(afterFirst.length, 1, `${label}：进入对话后应发起第 1 轮 /api/clerk/events 查询`);
  assert.strictEqual(afterFirst[0].url.searchParams.get("since"), "0", `${label}：第 1 轮应从 since=0 开始`);
  assert.ok(text(p.el("msgs")).includes(expectedMessage), `${label}：第 1 轮返回的消息应显示在页面上`);

  // 在 ability_69b_dom.js 里，setTimeout(fn, 0) 的到期时间是 now + 1；
  // 若被额外加上 50ms 延迟，推进 1ms 时不会发出第 2 轮查询。
  const delaysBeforeSecond = pendingTimerDelays(p);
  await p.advance(1);
  const afterSecond = clerkEventRequests(p, convId);
  if (afterSecond.length < 2) {
    return `${label}：收到消息后未按零等待继续查下一轮（推进 1ms 后仅发出 ${afterSecond.length} 次，当前一次性定时器等待毫秒数：${JSON.stringify(delaysBeforeSecond)}）`;
  }
  assert.strictEqual(afterSecond[1].url.searchParams.get("since"), "1", `${label}：第 2 轮应接续 since=1`);

  // 第 2 轮按真实后台长轮询等待 20 秒返回空后，也应零等待继续发起第 3 轮查询。
  const waitSec = Number(afterSecond[1].url.searchParams.get("wait") || 20);
  await p.advance(waitSec * 1000);
  const delaysBeforeThird = pendingTimerDelays(p);
  await p.advance(1);
  const afterThird = clerkEventRequests(p, convId);
  if (afterThird.length < 3) {
    return `${label}：长轮询超时返回空后未按零等待继续查下一轮（推进 1ms 后仅发出 ${afterThird.length} 次，当前一次性定时器等待毫秒数：${JSON.stringify(delaysBeforeThird)}）`;
  }
  p.check();
  return null;
}

(async () => {
  const problems = [];

  // 1. 行政主对话
  {
    const convId = "aaaa1111bbbb2222cccc3333dddd4444";
    const p = createScenarioPage({
      search: "?project=%2Fp%2FA&token=tok-poll&clerk=1",
      listResponse: {
        threads: [{ id: convId, title: "主对话", updated: 100, current: true, busy: true, pending: false }],
        current: convId,
        cached: false,
      },
      convId,
      firstEvents: [{ seq: 1, type: "user", text: "主对话查进度" }],
    });
    await settle();
    await settle();
    assert.strictEqual(text(p.el("who")).trim(), "行政", "主对话页头应显示行政");
    const err = await verifyZeroDelayPolling("行政主对话", p, convId, "主对话查进度");
    if (err) problems.push(err);
  }

  // 2. 普通卡片对话（无走查截图的普通卡片）
  {
    const convId = "bbbb1111cccc2222dddd3333eeee4444";
    const p = createScenarioPage({
      search: "?project=%2Fp%2FA&token=tok-poll&clerk=1",
      listResponse: { threads: [], current: null, cached: false },
      convId,
      firstEvents: [{ seq: 1, type: "user", text: "普通卡片消息" }],
    });
    await settle();
    await settle();
    p.context.SuperAgentChat.openCard({
      id: "CARD-001",
      conversation: convId,
      title: "普通卡片",
      phase: { label: "施工中" },
      do: "常规任务",
      not_do: "",
      done_when: "",
    });
    await settle();
    await settle();
    const err = await verifyZeroDelayPolling("普通卡片对话", p, convId, "普通卡片消息");
    if (err) problems.push(err);
  }

  // 3. 积木库管家对话
  {
    const convId = "cccc1111dddd2222eeee3333ffff4444";
    const p = createScenarioPage({
      search: "?project=%2Fp%2Fblocks&token=tok-poll&clerk=1&butler=1",
      listResponse: {
        threads: [{ id: convId, title: "管家对话", updated: 200, current: true, busy: true, pending: false }],
        current: convId,
        cached: false,
      },
      convId,
      firstEvents: [{ seq: 1, type: "user", text: "管家查积木" }],
    });
    await settle();
    await settle();
    assert.strictEqual(text(p.el("who")).trim(), "积木库管家", "积木库模式页头应显示积木库管家");
    const err = await verifyZeroDelayPolling("积木库管家对话", p, convId, "管家查积木");
    if (err) problems.push(err);
  }

  assert.deepStrictEqual(problems, [], "行政对话轮询出现额外等待：\n" + problems.join("\n"));
  console.log("OK");
})().catch(err => {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
