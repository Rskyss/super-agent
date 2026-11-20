"use strict";

const assert = require("assert");
const { page, settle, text } = require("./ability_69b_dom.js");

const REASON = "卡住原因是复查退回了一条：中间说明会自己打架。";
const DRAFTING = REASON + "正在替你起草“继续改”的话，约 3–5 分钟";
const DRAFTED = REASON + "“继续改”的话已起草好，点“不满意，继续改”就能看到";

(async () => {
  const holder = { text: "卡停下了，等你处理：" + DRAFTING };
  const pane = page("chat.html", {
    search: "?project=%2Fp&token=t&clerk=1",
    respond(request) {
      const path = request.url.pathname;
      if (path === "/api/view") return { now: { text: holder.text, tone: "bad" }, live: false };
      if (path === "/api/cards") return [];
      if (path === "/api/clerk/list") return { threads: [], current: null };
      if (path === "/api/clerk/history" || path === "/api/clerk/events") {
        return { events: [], open: true, busy: false };
      }
      return { ok: true };
    },
  });

  pane.context.SuperAgentChat.openCard({
    id: "CARD-020",
    title: "右栏显示小修",
    phase: { label: "卡住了", detail: DRAFTING },
  });
  await settle();
  await settle();
  assert.ok(text(pane.el("cardNow")).includes("正在替你起草"), "起草中要先显示进行中的话");

  holder.text = "卡停下了，等你处理：" + DRAFTED;
  await pane.advance(3000);
  const shown = text(pane.el("cardNow"));
  assert.ok(shown.includes("已起草好"), "起草完成后要换成新话：" + shown);
  assert.ok(!shown.includes("正在替你起草"), "最新说明里不能同时出现起草中和已起草好：" + shown);
  // Claude 接手（复查第 3 轮第 3 条）：再刷新几次，同一份完成说明也不能把打开时的旧话接回来
  for (let i = 0; i < 3; i++) {
    await pane.advance(3000);
    const again = text(pane.el("cardNow"));
    assert.ok(again.includes("已起草好") && !again.includes("正在替你起草"), "第 " + (i + 2) + " 次刷新后：" + again);
  }
  pane.check();
  console.log("OK");
})().catch(error => {
  console.error(error && error.stack || error);
  process.exit(1);
});
