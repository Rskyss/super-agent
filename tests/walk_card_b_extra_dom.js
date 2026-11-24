// 卡 B 补充前端交互实跑检查（测试员编写，对应施工单 docs/走查截图/施工单-卡B.md）：
// 使用真实 chat.html 与 chat.js（通过 ability_69b_dom.js），补齐验收测试未覆盖的交互与边界：
// 1. DOM 结构、步骤序号/并排双栏/单栏、alt 与大图标题、防 XSS（纯文本渲染）、#walkLightbox 挂载位置；
// 2. 大图浮层交互：点遮罩空白关闭、点面板内部不关闭、按非 Escape 键不关闭、关闭清空 img.src；
// 3. 数据变更时大图保留与关闭策略：当前大图仍在最新列表则保持打开，被替换或移除则立刻关闭并清空 src；
// 4. 切卡立刻清空（不等新卡接口返回）且重置折叠态；同卡重开防旧慢响应覆盖；开卡后立刻关闭再回旧响应不污染主对话；主对话草稿不丢；
// 5. 折叠同步 hidden 与 .fold 类、按钮文字不与纯“展开/收起”冲突、不额外起第二个 /api/view 定时器；
// 6. 极简模拟 DOM 兼容（无 document.addEventListener、无 document.body、头部容器无 classList；保留原有状态行等元素的 classList，并实测大图打开与关闭）。
"use strict";
const assert = require("assert");
const { page, settle, visible, text } = require("./ability_69b_dom.js");

const SHOTS_TWO = [
  { step: "右栏总览", shot: "走查截图/第1次/01-右栏总览.png", design: "docs/页面对齐/05-你验收.png" },
  { step: "点开交接", shot: "走查截图/第1次/02-点开交接.png", design: "" },
];

const SHOTS_XSS = [
  { step: "<img src=x onerror=alert(1)><b>危险步骤</b>", shot: "走查截图/第1次/01-危险.png", design: "docs/设计图.png" },
];

function makeView(cardId, walkShots, extra) {
  return Object.assign({
    installed: true,
    project: "A",
    card: { id: cardId, title: "测试卡", state: "待验收", rework: 0, round: 1, phase: { label: "待验收" } },
    cards: [],
    handoffs: [],
    back: false,
    stale: false,
    elapsed: 12,
    tokens: { total: 0, other: 0 },
    stations: [],
    now: { text: "做完了，等你确认", tone: "ok" },
    live: false,
    quiet: null,
    worker_elapsed: null,
    walk_shots: walkShots,
  }, extra || {});
}

function openChatPage(respond, extraBefore) {
  let ctx;
  return page("chat.html", {
    search: "?project=%2Fp%2FA&token=tok-extra&clerk=1",
    before(document, context) {
      ctx = context;
      const origCreate = document.createElement.bind(document);
      document.createElement = tag => {
        const el = origCreate(tag);
        Object.defineProperty(el, "scrollHeight", { get: () => 20, configurable: true });
        return el;
      };
      if (extraBefore) extraBefore(document, context);
    },
    respond(req) {
      const p = req.url.pathname;
      if (p === "/api/cards") return [];
      if (p === "/api/clerk/list") return { threads: [{ id: "main-thread" }], current: "main-thread" };
      if (p === "/api/clerk/history") return { events: [], open: true, busy: false };
      if (p === "/api/clerk/events") {
        // 照真实服务（desktop/clerk/desk.py）的长轮询行为：无新消息且 wait > 0 时先按 wait 秒等待再返回空
        const waitSec = Number(req.url.searchParams.get("wait") || 0);
        if (waitSec > 0 && ctx) {
          return new Promise(resolve => {
            ctx.setTimeout(() => resolve({ events: [], open: true, busy: false }), waitSec * 1000);
          });
        }
        return { events: [], open: true, busy: false };
      }
      if (p === "/api/clerk/focus") return { ok: true };
      return respond(req);
    },
  });
}

(async () => {
  // 场景 1：DOM 结构、步骤序号/标签、纯文本防 XSS、大图挂载在 body 而非 .chat-head、浮层关闭与防误关
  {
    let currentShots = SHOTS_TWO;
    const p = openChatPage(req => {
      if (req.url.pathname === "/api/view") {
        return makeView(req.url.searchParams.get("card") || "CARD-001", currentShots);
      }
      return { ok: true };
    });
    await settle(); await settle();
    const chat = p.context.SuperAgentChat;
    const chatHead = p.el("cardHead").parentNode;

    // 先在主对话输入未发送草稿，后面验证看卡再关闭时草稿不丢
    p.el("input").value = "主对话未发草稿";
    p.el("input").dispatchEvent({ type: "input" });

    chat.openCard({
      id: "CARD-001",
      conversation: "c-001",
      title: "带图卡",
      phase: { label: "待验收" },
      do: "展示走查截图",
      not_do: "不改右栏",
      done_when: "截图正常显示",
    });
    await settle(); await settle();

    const walkBox = p.el("cardWalk");
    assert.ok(walkBox && visible(walkBox), "有走查截图时应显示 #cardWalk");

    // #cardWalk 应在 #cardInfo（及其展开按钮）之后
    const siblings = chatHead.children;
    const infoIdx = siblings.indexOf(p.el("cardInfo"));
    const walkIdx = siblings.indexOf(walkBox);
    assert.ok(infoIdx >= 0 && walkIdx > infoIdx, "#cardWalk 必须插在卡片原文 #cardInfo 下方");
    const infoFold = p.el("cardInfoFold");
    if (infoFold) {
      assert.ok(walkIdx > siblings.indexOf(infoFold), "#cardWalk 必须插在 #cardInfoFold 按钮下方");
    }

    // 校验头部标题、折叠按钮文字与步骤双栏/单栏结构
    const titleEl = p.el("cardWalkTitle");
    assert.ok(titleEl && text(titleEl).includes("走查截图（2 张）"), "标题 #cardWalkTitle 应显示总张数");
    const foldBtn = p.el("cardWalkFold");
    assert.strictEqual(text(foldBtn), "收起截图", "展开态按钮文字必须是“收起截图”");
    assert.notStrictEqual(text(foldBtn), "收起", "不能使用单纯的“收起”文字");

    const rows = walkBox.querySelectorAll(".walk-row");
    assert.strictEqual(rows.length, 2, "2 步走查应渲染 2 个 .walk-row");
    assert.strictEqual(text(rows[0].querySelector(".walk-step")).trim(), "第 1 步：右栏总览");
    assert.strictEqual(text(rows[1].querySelector(".walk-step")).trim(), "第 2 步：点开交接");

    const cols0 = rows[0].querySelectorAll(".walk-col");
    assert.strictEqual(cols0.length, 2, "有设计图的步骤应并排 2 栏 .walk-col");
    assert.strictEqual(text(cols0[0].querySelector(".walk-label")).trim(), "本次截图");
    assert.strictEqual(text(cols0[1].querySelector(".walk-label")).trim(), "设计图");
    assert.strictEqual(cols0[0].querySelector("img.walk-img").getAttribute("alt"), "右栏总览 本次截图");

    const cols1 = rows[1].querySelectorAll(".walk-col");
    assert.strictEqual(cols1.length, 1, "无设计图的步骤只画 1 栏 .walk-col");
    assert.strictEqual(text(cols1[0].querySelector(".walk-label")).trim(), "本次截图");
    assert.ok(!text(rows[1]).includes("设计图"), "无设计图的步骤不应出现“设计图”标签");

    // 折叠切换必须同步设置 #cardWalkList 的 hidden 与 .fold 类
    const walkList = p.el("cardWalkList");
    assert.ok(walkList && !walkList.hidden, "默认展开时 #cardWalkList 不应 hidden");
    foldBtn.click();
    await settle();
    assert.strictEqual(text(foldBtn), "展开截图", "收起后按钮文字必须是“展开截图”");
    assert.strictEqual(walkList.hidden, true, "收起后 #cardWalkList.hidden 必须为 true");
    assert.ok(walkList.classList.contains("fold"), "收起后 #cardWalkList 应同步加上 .fold 类");
    foldBtn.click();
    await settle();
    assert.strictEqual(text(foldBtn), "收起截图");
    assert.strictEqual(walkList.hidden, false, "展开后 #cardWalkList.hidden 必须为 false");
    assert.ok(!walkList.classList.contains("fold"), "展开后 #cardWalkList 应移除 .fold 类");

    // 打开大图浮层：必须挂在 body 下（不能在 .chat-head 内部）
    const thumbs = walkBox.querySelectorAll("button.walk-thumb");
    thumbs[0].click();
    await settle();
    const lb = p.el("walkLightbox");
    const lbTitle = p.el("walkLightboxTitle");
    const lbImg = p.el("walkLightboxImg");
    assert.ok(lb && visible(lb), "点缩略图应打开 #walkLightbox");
    assert.strictEqual(lb.parentNode, p.document.body, "#walkLightbox 必须挂载在 document.body 下");
    assert.ok(!chatHead.contains(lb), "#walkLightbox 绝不能挂载在 .chat-head 内部");
    assert.strictEqual(text(lbTitle).trim(), "第 1 步：右栏总览 · 本次截图");
    assert.ok(lbImg && lbImg.src.includes("/api/clerk/card/image"), "大图应加载正确的只读看图地址");

    // 点击面板内部或大图本身不能关闭浮层
    const lbCard = lb.querySelector(".walk-lightbox-card");
    assert.ok(lbCard, "浮层内部应有 .walk-lightbox-card");
    lbCard.click();
    await settle();
    assert.ok(visible(lb), "点击 .walk-lightbox-card 内部不能关闭大图浮层");
    lbImg.click();
    await settle();
    assert.ok(visible(lb), "点击大图本身不能关闭大图浮层");

    // 按非 Escape 键不能关闭浮层
    p.document.dispatchEvent({ type: "keydown", key: "Enter" });
    await settle();
    assert.ok(visible(lb), "按 Enter 键不应关闭大图浮层");

    // 点击遮罩空白处应关闭大图并清空 img.src
    lb.click();
    await settle();
    assert.ok(!visible(lb), "点击浮层遮罩背景空白处应关闭大图");
    assert.strictEqual(lbImg.src, "", "关闭大图后应清空 #walkLightboxImg.src");

    // 点击设计图大图校验标题，并按 Escape 关闭后同样清空 src
    thumbs[1].click();
    await settle();
    assert.ok(visible(lb));
    assert.strictEqual(text(lbTitle).trim(), "第 1 步：右栏总览 · 设计图");
    p.document.dispatchEvent({ type: "keydown", key: "Escape" });
    await settle();
    assert.ok(!visible(lb));
    assert.strictEqual(lbImg.src, "", "按 Escape 关闭大图后应清空 #walkLightboxImg.src");

    // 数据变更但正在看的大图仍在最新列表中：浮层必须保持打开
    thumbs[0].click();
    await settle();
    const keptUrl = lbImg.src;
    assert.ok(visible(lb));
    currentShots = [
      SHOTS_TWO[0],
      { step: "新增第三步", shot: "走查截图/第1次/03-新增.png", design: "" },
    ];
    await p.advance(3100);
    assert.ok(visible(lb), "重绘后当前正在看的大图仍在最新列表中，浮层应保持打开");
    assert.strictEqual(lbImg.src, keptUrl, "保持打开时大图地址不应被清空");
    assert.ok(text(titleEl).includes("走查截图（2 张）"));

    // 数据变更且重跑换了截图路径（旧大图 URL 不再存在于新列表）：浮层必须自动关闭并清空 src
    currentShots = [
      { step: "右栏总览", shot: "走查截图/第2次/01-右栏总览.png", design: "docs/页面对齐/05-你验收.png" },
      SHOTS_TWO[1],
    ];
    await p.advance(3100);
    assert.ok(visible(walkBox), "仍有新截图时 #cardWalk 继续显示");
    assert.ok(!visible(lb), "重跑走查换了截图后，正在看的旧大图浮层应立刻关闭");
    assert.strictEqual(lbImg.src, "", "自动关闭旧大图后应清空 #walkLightboxImg.src");

    // 不额外起第二个 /api/view 定时器：一个 3 秒周期里只多发 1 次 /api/view
    const beforeCount = p.requests.filter(r => r.url.pathname === "/api/view").length;
    await p.advance(3000);
    const afterCount = p.requests.filter(r => r.url.pathname === "/api/view").length;
    assert.strictEqual(afterCount - beforeCount, 1, "复用现有 3 秒轮询，每个周期只应发 1 次 /api/view");

    // 防 XSS：步骤名包含 HTML 标签时必须按纯文本显示，不能注入恶意元素
    currentShots = SHOTS_XSS;
    await p.advance(3100);
    assert.strictEqual(walkBox.querySelectorAll("b").length, 0, "步骤名中的 <b> 不能被解析为 HTML 标签");
    assert.strictEqual(walkBox.querySelectorAll("img").length, 2, "步骤名中的 <img> 不能被注入为额外图片");
    assert.ok(text(walkBox).includes("<img src=x onerror=alert(1)><b>危险步骤</b>"), "步骤名原文应以纯文本保留");
    walkBox.querySelector("button.walk-thumb").click();
    await settle();
    assert.strictEqual(lb.querySelectorAll("b").length, 0, "大图标题中的 HTML 标签也不能被解析");
    assert.ok(text(lbTitle).includes("<img src=x onerror=alert(1)><b>危险步骤</b>"));

    // 关闭卡片回到主对话：走查截图隐藏、大图关闭、主对话未发草稿保留
    chat.closeCard();
    await settle();
    assert.ok(!visible(walkBox));
    assert.ok(!visible(lb));
    assert.strictEqual(lbImg.src, "");
    assert.strictEqual(p.el("input").value, "主对话未发草稿", "看带图卡再关闭后主对话草稿不能丢");

    p.check();
  }

  // 场景 2：切卡立刻清空隐藏上一张卡的截图与大图（不等新卡接口回来），且新卡默认展开（不继承旧卡收起态）
  {
    let releaseCard2;
    const p = openChatPage(req => {
      if (req.url.pathname === "/api/view") {
        const c = req.url.searchParams.get("card");
        if (c === "CARD-001") return makeView("CARD-001", SHOTS_TWO);
        return new Promise(resolve => {
          releaseCard2 = () => resolve({
            status: 200, ok: true,
            json: async () => makeView("CARD-002", [
              { step: "卡2步骤", shot: "走查截图/第1次/01-卡2.png", design: "" },
            ]),
          });
        });
      }
      return { ok: true };
    });
    await settle(); await settle();
    const chat = p.context.SuperAgentChat;
    const chatHead = p.el("cardHead").parentNode;

    chat.openCard({ id: "CARD-001", conversation: "c-001", title: "卡1", phase: { label: "待验收" } });
    await settle(); await settle();
    const walkBox = p.el("cardWalk");
    const foldBtn = p.el("cardWalkFold");
    // 在卡 1 上先打开大图，再把截图区点成收起
    walkBox.querySelector("button.walk-thumb").click();
    await settle();
    const lb = p.el("walkLightbox");
    assert.ok(visible(lb));
    foldBtn.click();
    await settle();
    assert.strictEqual(text(foldBtn), "展开截图");

    // 切到卡 2（卡 2 的 /api/view 尚未返回）
    chat.openCard({ id: "CARD-002", conversation: "c-002", title: "卡2", phase: { label: "待验收" } });
    await settle();
    assert.ok(!visible(walkBox), "切入新卡后不等接口返回就必须立刻隐藏旧卡截图区");
    assert.strictEqual(walkBox.querySelectorAll("img").length, 0, "切入新卡后应立刻清空旧卡缩略图");
    assert.ok(!chatHead.classList.contains("has-walk"), "切入新卡等待返回期间不应残留 .has-walk");
    assert.ok(!visible(lb), "切入新卡后应立刻关闭旧卡大图浮层");

    // 卡 2 返回数据后：必须恢复默认展开态，不继承卡 1 的收起状态
    releaseCard2();
    await settle(); await settle();
    assert.ok(visible(walkBox), "卡 2 返回数据后应显示 #cardWalk");
    assert.strictEqual(text(p.el("cardWalkFold")), "收起截图", "切到新卡后应重置为默认展开态");
    assert.strictEqual(p.el("cardWalkList").hidden, false, "新卡截图列表应默认展开可见");
    assert.ok(text(walkBox).includes("卡2步骤") && !text(walkBox).includes("右栏总览"));
    p.check();
  }

  // 场景 3：同卡重开防旧慢响应覆盖 + 开卡后立刻 closeCard 回主对话防晚到响应污染
  {
    let callIdx = 0;
    let releaseFirst, releaseThird;
    const p = openChatPage(req => {
      if (req.url.pathname === "/api/view") {
        callIdx += 1;
        if (callIdx === 1) {
          return new Promise(resolve => {
            releaseFirst = () => resolve({
              status: 200, ok: true,
              json: async () => makeView("CARD-001", SHOTS_TWO),
            });
          });
        }
        if (callIdx === 2) {
          return makeView("CARD-001", []);
        }
        return new Promise(resolve => {
          releaseThird = () => resolve({
            status: 200, ok: true,
            json: async () => makeView("CARD-001", SHOTS_TWO),
          });
        });
      }
      return { ok: true };
    });
    await settle(); await settle();
    const chat = p.context.SuperAgentChat;
    const chatHead = p.el("cardHead").parentNode;

    // 同一张卡连续打开两次：第 2 次先返回空列表，随后第 1 次晚到带图响应必须被丢弃
    chat.openCard({ id: "CARD-001", conversation: "c-001", title: "卡1", phase: { label: "待验收" } });
    await settle();
    chat.openCard({ id: "CARD-001", conversation: "c-001", title: "卡1", phase: { label: "待验收" } });
    await settle(); await settle();
    releaseFirst();
    await settle(); await settle();
    const walkBox = p.el("cardWalk");
    assert.ok(!walkBox || !visible(walkBox), "同卡重开时旧一次打开的慢响应不能覆盖新结果");
    assert.ok(!chatHead.classList.contains("has-walk"));

    // 第 3 次打开卡，还没回来用户就 closeCard 回主对话；晚到响应绝不能在主对话画出截图
    chat.openCard({ id: "CARD-001", conversation: "c-001", title: "卡1", phase: { label: "待验收" } });
    await settle();
    chat.closeCard();
    await settle();
    releaseThird();
    await settle(); await settle();
    assert.ok(!p.el("cardWalk") || !visible(p.el("cardWalk")), "回主对话后晚到的 /api/view 响应不能画出 #cardWalk");
    assert.ok(!chatHead.classList.contains("has-walk"), "回主对话后不能被晚到响应挂上 .has-walk");
    p.check();
  }

  // 场景 4：极简模拟 DOM 兼容（无 document.addEventListener、无 document.body、头部容器无 classList）
  // 真实加载 chat.html 初始节点（不对未知 ID 凭空创建游离元素，且保留卡片状态行等原有元素的 classList，与 chat_card_check.js 一致），
  // 验证在无 document.body / 无 document.addEventListener / 头部无 classList 的受限宿主里，原有状态行正常显示且截图与大图可正常打开和关闭。
  {
    const p = openChatPage(
      req => {
        if (req.url.pathname === "/api/view") {
          return makeView(req.url.searchParams.get("card") || "CARD-001", SHOTS_TWO);
        }
        return { ok: true };
      },
      document => {
        document.body = null;
        document.addEventListener = undefined;
        const chatHead = document.getElementById("cardHead").parentNode;
        delete chatHead.classList;
      },
    );
    await settle(); await settle();
    const chat = p.context.SuperAgentChat;
    const chatHead = p.el("cardHead").parentNode;

    chat.openCard({
      id: "CARD-001",
      conversation: "c1",
      title: "极简卡",
      phase: { label: "待验收" },
      do: "验证受限宿主兼容",
    });
    await settle(); await settle();

    // 原有卡片状态行应正常显示，不能因模拟页缺少基础 classList 报错
    const nowLine = p.el("cardNow");
    assert.ok(nowLine && visible(nowLine), "极简宿主里原有卡片状态行 #cardNow 应正常显示");
    assert.ok(text(nowLine).includes("做完了，等你确认"), "卡片状态行文字应完整显示：" + text(nowLine));

    // 截图区必须由 chat.js 真正创建并挂到卡片头部，而非凭空返回游离元素
    const walkBox = p.el("cardWalk");
    assert.ok(walkBox && visible(walkBox), "极简宿主里应动态创建并显示 #cardWalk");
    assert.strictEqual(walkBox.parentNode, chatHead, "#cardWalk 应真实挂载在卡片头部容器下");
    assert.ok(text(p.el("cardWalkTitle")).includes("走查截图（2 张）"), "应渲染走查截图标题");
    const thumbs = walkBox.querySelectorAll("button.walk-thumb");
    assert.strictEqual(thumbs.length, 3, "极简宿主里也应真实渲染 3 个缩略图按钮");

    // 点击缩略图打开大图浮层：无 document.body 时回退挂载到卡片头部容器，且不因缺少 document.addEventListener 报错
    thumbs[0].click();
    await settle();
    const lb = p.el("walkLightbox");
    const lbTitle = p.el("walkLightboxTitle");
    const lbImg = p.el("walkLightboxImg");
    const closeBtn = p.el("walkLightboxClose");
    assert.ok(lb && visible(lb), "无 document.body 与 document.addEventListener 时点缩略图仍应打开大图浮层");
    assert.strictEqual(lb.parentNode, chatHead, "无 document.body 时浮层应回退挂载到卡片头部容器");
    assert.strictEqual(text(lbTitle).trim(), "第 1 步：右栏总览 · 本次截图");
    assert.strictEqual(lbImg.src, thumbs[0].querySelector("img").src, "大图地址应与点击的缩略图一致");

    // 点关闭按钮关闭大图并清空 src
    assert.ok(closeBtn && text(closeBtn) === "关闭");
    closeBtn.click();
    await settle();
    assert.ok(!visible(lb), "点关闭按钮后大图浮层应隐藏");
    assert.strictEqual(lbImg.src, "", "关闭后应清空大图地址");

    // 点设计图再次打开，再点遮罩空白关闭
    thumbs[1].click();
    await settle();
    assert.ok(visible(lb) && text(lbTitle).trim() === "第 1 步：右栏总览 · 设计图");
    lb.click();
    await settle();
    assert.ok(!visible(lb), "点遮罩空白处应关闭大图浮层");
    assert.strictEqual(lbImg.src, "");

    // 再次打开大图后切回主对话：截图区和大图浮层均应关闭
    thumbs[0].click();
    await settle();
    assert.ok(visible(lb));
    chat.closeCard();
    await settle();
    assert.ok(!visible(walkBox), "closeCard 后应隐藏 #cardWalk");
    assert.ok(!visible(lb), "closeCard 后应关闭大图浮层");
    assert.strictEqual(lbImg.src, "");

    // 重新开卡并打开大图，再 startFresh：截图区和大图浮层均应关闭
    chat.openCard({ id: "CARD-001", conversation: "c1", title: "极简卡", phase: { label: "待验收" } });
    await settle(); await settle();
    p.el("cardWalk").querySelector("button.walk-thumb").click();
    await settle();
    assert.ok(visible(lb));
    chat.startFresh();
    await settle();
    assert.ok(!visible(walkBox), "startFresh 后应隐藏 #cardWalk");
    assert.ok(!visible(lb), "startFresh 后应关闭大图浮层");
    assert.strictEqual(lbImg.src, "");

    p.check();
  }

  console.log("OK");
})().catch(err => { console.error(err && err.stack || err); process.exit(1); });
