// 方案 1（纯单向图集快照 + 页面批次号）前端交互检查：
// 1. shell=1 模式下点击走查缩略图只发出单向 superagent://walk-image（含 index、page、items），不开页面内 #walkLightbox，且绝不触发 /api/view；
// 2. 同名步骤复用同一张设计图时，点击第 2 步设计图发出准确的 index=3、page 和完整 items 快照；
// 3. 回归守门：从空白主对话起步且有未发草稿时开卡再 startFresh（菜单开始新对话路径），旧草稿绝不带进新对话；并用“先 closeCard 再 startFresh”的错误旧顺序自证能抓住泄漏；
// 4. 非 shell 模式维持页面内 #walkLightbox，且彻底无双向通信残留，点击也不触发 /api/view。
"use strict";
const assert = require("assert");
const { page, settle, visible } = require("./ability_69b_dom.js");

const SHOTS_TWO = [
  { step: "右栏总览", shot: "走查截图/第1次/01-右栏总览.png", design: "docs/借鉴DSH/页面对齐/05-你验收.png" },
  { step: "点开交接", shot: "走查截图/第1次/02-点开交接.png", design: "" },
];
const SHOTS_SAME_NAME = [
  { step: "同名", shot: "走查截图/第1次/01.png", design: "docs/same.png" },
  { step: "同名", shot: "走查截图/第1次/02.png", design: "docs/same.png" },
];

function openChatPage({ shell = true, shellPage = "7", threads = [{ id: "T1", title: "主对话" }], viewFn }) {
  const tells = [];
  const stats = { viewCalls: 0 };
  let ctx;
  const search = "?project=%2Fp%2FA&token=secret-tok&clerk=1"
    + (shell ? "&shell=1&shell_page=" + encodeURIComponent(shellPage) : "");
  const p = page("chat.html", {
    search,
    before(document, context) {
      ctx = context;
      const origCreate = document.createElement.bind(document);
      document.createElement = tag => {
        const el = origCreate(tag);
        Object.defineProperty(el, "scrollHeight", { get: () => 20, configurable: true });
        return el;
      };
      let hrefVal = "";
      Object.defineProperty(context.location, "href", {
        get: () => hrefVal,
        set: v => { hrefVal = String(v); tells.push(String(v)); },
        configurable: true,
      });
    },
    respond(req) {
      const pathname = req.url.pathname;
      if (pathname === "/api/cards") return [];
      if (pathname === "/api/clerk/list") return { threads, current: null };
      if (pathname === "/api/clerk/history") return { events: [], open: true, busy: false };
      if (pathname === "/api/clerk/events") {
        const waitSec = Number(req.url.searchParams.get("wait") || 0);
        if (waitSec > 0 && ctx) {
          return new Promise(resolve => {
            ctx.setTimeout(() => resolve({ events: [], open: true, busy: false }), waitSec * 1000);
          });
        }
        return { events: [], open: true, busy: false };
      }
      if (pathname === "/api/view") {
        stats.viewCalls += 1;
        return viewFn(req);
      }
      return { ok: true };
    },
  });
  return { p, tells, stats };
}

(async () => {
  // 1. shell=1 模式：单向发 superagent://walk-image（含 index、page、items），不开页面内 #walkLightbox，且不发 /api/view
  {
    const { p, tells, stats } = openChatPage({
      shell: true,
      shellPage: "7",
      viewFn: () => ({ now: { text: "做完了，等你确认", tone: "ok" }, live: false, walk_shots: SHOTS_TWO }),
    });
    await settle(); await settle();
    const chat = p.context.SuperAgentChat;
    assert.strictEqual(chat.stepWalkLightbox, undefined, "应彻底删除双向接口 stepWalkLightbox");
    assert.strictEqual(chat.walkOverlayClosed, undefined, "应彻底删除双向接口 walkOverlayClosed");

    chat.openCard({ id: "CARD-001", conversation: "c-001", title: "带图卡片", phase: { label: "待验收" }, do: "看图" });
    await settle(); await settle();

    const thumbs = p.el("cardWalk").querySelectorAll("button.walk-thumb");
    assert.strictEqual(thumbs.length, 3);

    const expectedGallery = [
      { kind: "shot", path: SHOTS_TWO[0].shot, title: "第 1 步：右栏总览 · 本次截图" },
      { kind: "design", path: SHOTS_TWO[0].design, title: "第 1 步：右栏总览 · 设计图" },
      { kind: "shot", path: SHOTS_TWO[1].shot, title: "第 2 步：点开交接 · 本次截图" },
    ];
    for (let i = 0; i < expectedGallery.length; i++) {
      tells.length = 0;
      const beforeView = stats.viewCalls;
      thumbs[i].click();
      await settle();
      assert.strictEqual(stats.viewCalls, beforeView, "点击缩略图绝不能额外发起 /api/view 请求");
      const lb = p.el("walkLightbox");
      assert.ok(!lb || !visible(lb), "shell=1 下绝不打开页面内 #walkLightbox");
      const hits = tells.filter(u => u.startsWith("superagent://walk-image?"));
      assert.strictEqual(hits.length, 1, "每次点击应只发出 1 条 walk-image");
      const u = new URL(hits[0]);
      assert.strictEqual(u.searchParams.get("project"), "/p/A");
      assert.strictEqual(u.searchParams.get("card"), "CARD-001");
      assert.strictEqual(u.searchParams.get("kind"), expectedGallery[i].kind);
      assert.strictEqual(u.searchParams.get("path"), expectedGallery[i].path);
      assert.strictEqual(u.searchParams.get("title"), expectedGallery[i].title);
      assert.strictEqual(u.searchParams.get("index"), String(i));
      assert.strictEqual(u.searchParams.get("page"), "7");
      assert.deepStrictEqual(JSON.parse(u.searchParams.get("items")), expectedGallery);
    }

    p.check();
  }

  // 2. 同名步骤复用同一张设计图：点第 2 步设计图必须发出 index=3、page=7 和完整 4 项快照，且不发 /api/view
  {
    const { p, tells, stats } = openChatPage({
      shell: true,
      shellPage: "7",
      viewFn: () => ({ now: { text: "好了", tone: "ok" }, live: false, walk_shots: SHOTS_SAME_NAME }),
    });
    await settle(); await settle();
    const chat = p.context.SuperAgentChat;
    chat.openCard({ id: "CARD-001", conversation: "c-001", title: "卡", phase: { label: "待验收" } });
    await settle(); await settle();

    const thumbs = p.el("cardWalk").querySelectorAll("button.walk-thumb");
    assert.strictEqual(thumbs.length, 4);
    tells.length = 0;
    const beforeView = stats.viewCalls;
    thumbs[3].click();
    await settle();
    assert.strictEqual(stats.viewCalls, beforeView, "点击缩略图绝不能额外发起 /api/view 请求");
    const u = new URL(tells.find(x => x.startsWith("superagent://walk-image?")));
    assert.strictEqual(u.searchParams.get("index"), "3");
    assert.strictEqual(u.searchParams.get("page"), "7");
    assert.strictEqual(u.searchParams.get("kind"), "design");
    assert.strictEqual(u.searchParams.get("path"), "docs/same.png");
    assert.strictEqual(u.searchParams.get("title"), "第 2 步：同名 · 设计图");
    const items = JSON.parse(u.searchParams.get("items"));
    assert.strictEqual(items.length, 4);
    assert.strictEqual(items[1].title, "第 1 步：同名 · 设计图");
    assert.strictEqual(items[3].title, "第 2 步：同名 · 设计图");
    p.check();
  }

  // 3a. 回归守门：从真正空白主对话起步（threads: []）有未发草稿 → 开卡 → 直接调 startFresh，旧草稿绝不留进新对话
  {
    const { p } = openChatPage({
      shell: true,
      threads: [],
      viewFn: () => ({ now: { text: "", tone: "idle" }, live: false, walk_shots: [] }),
    });
    await settle(); await settle();
    const chat = p.context.SuperAgentChat;
    const input = p.el("input");
    input.value = "旧主对话未发送草稿";
    chat.openCard({ id: "CARD-001", conversation: "c-001", title: "卡", phase: { label: "待验收" } });
    await settle();
    chat.startFresh();
    await settle();
    assert.strictEqual(input.value, "", "开始新对话后输入框必须为空，不能带入旧主对话草稿");
    assert.strictEqual((chat.place() || {}).draft || "", "", "保存的草稿位置也必须为空");
    p.check();
  }

  // 3b. 对照自证：同一空白主对话起步，若走错误旧顺序（先 closeCard 再 startFresh），旧草稿确会泄漏
  {
    const { p } = openChatPage({
      shell: true,
      threads: [],
      viewFn: () => ({ now: { text: "", tone: "idle" }, live: false, walk_shots: [] }),
    });
    await settle(); await settle();
    const chat = p.context.SuperAgentChat;
    const input = p.el("input");
    input.value = "旧主对话未发送草稿";
    chat.openCard({ id: "CARD-001", conversation: "c-001", title: "卡", phase: { label: "待验收" } });
    await settle();
    chat.closeCard();
    chat.startFresh();
    await settle();
    assert.strictEqual(input.value, "旧主对话未发送草稿", "错误顺序 closeCard()+startFresh() 应复现草稿泄漏，证明夹具真能抓回归");
    p.check();
  }

  // 4. 对照组：非 shell 模式维持页面内 #walkLightbox，且点击不发 /api/view
  {
    const { p, tells, stats } = openChatPage({
      shell: false,
      viewFn: () => ({ now: { text: "好了", tone: "ok" }, live: false, walk_shots: SHOTS_TWO }),
    });
    await settle(); await settle();
    const chat = p.context.SuperAgentChat;
    chat.openCard({ id: "CARD-001", conversation: "c-001", title: "卡", phase: { label: "待验收" } });
    await settle(); await settle();
    tells.length = 0;
    const beforeView = stats.viewCalls;
    p.el("cardWalk").querySelectorAll("button.walk-thumb")[0].click();
    await settle();
    assert.strictEqual(stats.viewCalls, beforeView, "点击缩略图绝不能额外发起 /api/view 请求");
    const lb = p.el("walkLightbox");
    assert.ok(lb && visible(lb));
    assert.strictEqual(tells.filter(u => u.startsWith("superagent://walk-image")).length, 0);
    p.check();
  }

  console.log("OK");
})().catch(err => { console.error(err && err.stack || err); process.exit(1); });
