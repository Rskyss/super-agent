// CARD-019 Claude 接手补的实跑检查：真的执行聊天页脚本，看“主对话没发的字”在看卡、回来、切走时守不守得住。
// 复查第 3 轮第 1 条：切回项目先恢复到卡片，再回主对话，存好的字被清空。
"use strict";
const assert = require("assert");
const { page, settle } = require("./ability_69b_dom.js");

const THREADS = [{ id: "T-new", title: "新的" }, { id: "T-old", title: "旧的" }];

function open(search) {
  const told = [];
  const p = page("chat.html", {
    search,
    before(document, context) {
      context.location = {
        search,
        get href() { return ""; },
        set href(v) { told.push(String(v)); },
      };
    },
    respond(req) {
      const path = req.url.pathname;
      if (path === "/api/clerk/list") return { threads: THREADS, current: null };
      if (path === "/api/clerk/history" || path === "/api/clerk/events") return { events: [], open: true, busy: false };
      return { ok: true };
    },
  });
  return { p, told, chat: () => p.context.SuperAgentChat, input: () => p.el("input") };
}

function lastPlace(told) {
  const hit = told.filter(u => u.startsWith("superagent://place?")).pop();
  if (!hit) return null;
  const q = new URLSearchParams(hit.slice("superagent://place?".length));
  return { thread: q.get("thread"), draft: q.get("draft") };
}

(async () => {
  const base = "?project=%2Fp%2FA&token=t&clerk=1&thread=T-old&draft=" + encodeURIComponent("还没发的话");

  // 1. 列表先回来：恢复主对话和字；再开卡、回主对话，字一直在
  {
    const s = open(base);
    await settle(); await settle();
    assert.strictEqual(s.input().value, "还没发的话", "恢复时把字放回输入框");
    assert.deepStrictEqual(JSON.parse(JSON.stringify(s.chat().place())), { thread: "T-old", draft: "还没发的话" });

    s.chat().openCard({ id: "CARD-1", title: "卡", conversation: "card-conv" });
    await settle();
    assert.strictEqual(s.input().value, "", "看卡时输入框是卡片对话的，不放主对话的字");
    assert.deepStrictEqual(JSON.parse(JSON.stringify(s.chat().place())), { thread: "T-old", draft: "还没发的话" },
      "看卡时报回去的仍是主对话和它的字");
    assert.deepStrictEqual(lastPlace(s.told), { thread: "T-old", draft: "还没发的话" }, "开卡后上报的不能是空草稿");

    s.chat().closeCard();
    await settle();
    assert.strictEqual(s.input().value, "还没发的话", "回到主对话，字还在");
    assert.deepStrictEqual(lastPlace(s.told), { thread: "T-old", draft: "还没发的话" }, "回主对话后上报的不能是空草稿");
  }

  // 2. 卡先打开、列表后回来（切回上次停在卡上的项目）：回主对话时接回记录的那段和字
  {
    const s = open(base);
    s.chat().openCard({ id: "CARD-1", title: "卡", conversation: "card-conv" });
    await settle(); await settle();
    assert.deepStrictEqual(JSON.parse(JSON.stringify(s.chat().place())), { thread: "T-old", draft: "还没发的话" },
      "卡先开时也要记住回去接哪段、放回哪些字");
    s.chat().closeCard();
    await settle();
    assert.strictEqual(s.input().value, "还没发的话", "从卡回到主对话，存好的字没丢（复查第 3 轮第 1 条）");
    assert.deepStrictEqual(lastPlace(s.told), { thread: "T-old", draft: "还没发的话" });
  }

  // 3. 主对话里新打的字：开卡再回来也在
  {
    const s = open("?project=%2Fp%2FA&token=t&clerk=1");
    await settle(); await settle();
    s.input().value = "新打的";
    s.chat().openCard({ id: "CARD-1", title: "卡", conversation: "card-conv" });
    await settle();
    s.chat().closeCard();
    await settle();
    assert.strictEqual(s.input().value, "新打的");
  }

  // 4. 聊天列表回来前用户已经打了字：恢复旧对话时不能把这些字清掉、也不拿旧草稿盖掉
  {
    const s = open(base);
    s.input().value = "刚打的";
    s.input().listeners.input.forEach(fn => fn({}));
    await settle(); await settle();
    assert.strictEqual(s.input().value, "刚打的", "列表回来前打的字要留着");
  }

  // 5. 列表回来前打了新字、又马上开卡（复查第 4 轮第 1 条）：回主对话是新字，不是旧草稿
  {
    const s = open(base);
    s.input().value = "刚打的新字";
    s.input().listeners.input.forEach(fn => fn({}));
    s.chat().openCard({ id: "CARD-1", title: "卡", conversation: "card-conv" });
    await settle(); await settle();
    s.chat().closeCard();
    await settle();
    assert.strictEqual(s.input().value, "刚打的新字");
  }

  // 6. 还没恢复好就要位置：没动过时不报（不盖掉记录），打了字就报刚打的字
  {
    const s = open(base);
    assert.strictEqual(s.chat().place(), null, "没动过时不报");
    s.input().value = "抢先打的";
    s.input().listeners.input.forEach(fn => fn({}));
    assert.deepStrictEqual(JSON.parse(JSON.stringify(s.chat().place())), { thread: "T-old", draft: "抢先打的" });
  }

  // 7. 卡先开、在卡片对话里打字、列表后回来：主对话存好的字不受影响
  {
    const s = open(base);
    s.chat().openCard({ id: "CARD-1", title: "卡", conversation: "card-conv" });
    s.input().value = "给卡片说的话";
    s.input().listeners.input.forEach(fn => fn({}));
    await settle(); await settle();
    assert.deepStrictEqual(JSON.parse(JSON.stringify(s.chat().place())), { thread: "T-old", draft: "还没发的话" });
  }

  // 8. 看卡时把主对话删了：回来是空白新对话，不带被删对话的字（复查第 4 轮第 4 条）
  {
    const s = open(base);
    await settle(); await settle();
    s.chat().openCard({ id: "CARD-1", title: "卡", conversation: "card-conv" });
    await settle();
    s.chat().threadDeleted("T-old");
    s.chat().closeCard();
    await settle();
    assert.strictEqual(s.input().value, "", "被删对话的字不能带进新对话");
  }

  console.log("OK");
})().catch(err => { console.error(err && err.stack || err); process.exit(1); });
