// P1 卡 B 前端交互实跑检查（写施工单的角色写，施工方不许改）：
// 真实加载 chat.html 与 chat.js，检查：走查截图并排展示、点击查看大图（点关闭与按 Escape 关闭）、
// 收起/展开截图且 3 秒刷新不丢失折叠状态也不关闭大图、切卡/回主对话/慢响应防窜卡、无走查卡不显示。
"use strict";
const assert = require("assert");
const { page, settle, visible, text, Element } = require("./ability_69b_dom.js");

const SHOTS_TWO = [
  { step: "右栏总览", shot: "走查截图/第1次/01-右栏总览.png", design: "docs/借鉴DSH/页面对齐/05-你验收.png" },
  { step: "点开交接", shot: "走查截图/第1次/02-点开交接.png", design: "" },
];

function openChatPage(respond) {
  let ctx;
  return page("chat.html", {
    search: "?project=%2Fp%2FA&token=secret-tok&clerk=1",
    before(document, context) {
      ctx = context;
      const origCreate = document.createElement.bind(document);
      document.createElement = tag => {
        const el = origCreate(tag);
        // 避免 cardNow/cardInfo 折叠测量缺 scrollHeight 报错
        Object.defineProperty(el, "scrollHeight", { get: () => 20, configurable: true });
        return el;
      };
    },
    respond(req) {
      const p = req.url.pathname;
      if (p === "/api/cards") return [];
      if (p === "/api/clerk/list") return { threads: [], current: null };
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
      return respond(req);
    },
  });
}

(async () => {
  // 1. 有走查截图的卡：并排显示、URL 带口令与安全参数、点击查看大图、关闭与 Escape 关闭、收起与 3 秒刷新保留状态
  {
    let currentShots = SHOTS_TWO;
    const p = openChatPage(req => {
      if (req.url.pathname === "/api/view") {
        return { now: { text: "做完了，等你确认", tone: "ok" }, live: false, walk_shots: currentShots };
      }
      return { ok: true };
    });
    await settle(); await settle();
    const chat = p.context.SuperAgentChat;

    const chatHead = p.el("cardHead").parentNode;

    // 先开一张没有走查截图的卡：#cardWalk 必须不可见，且不能挂 .has-walk
    currentShots = [];
    chat.openCard({ id: "CARD-002", conversation: "c-002", title: "普通卡片", phase: { label: "已完成" }, do: "只改文案", not_do: "", done_when: "通过" });
    await settle(); await settle();
    const walkHidden = p.el("cardWalk");
    assert.ok(!walkHidden || !visible(walkHidden), "没走查的卡不能显示走查截图区");
    assert.ok(!chatHead.classList.contains("has-walk"), "没走查的卡不能给头部挂 has-walk 限高类");

    // 切到有 2 步走查的卡
    currentShots = SHOTS_TWO;
    chat.openCard({ id: "CARD-001", conversation: "c-001", title: "带图卡片", phase: { label: "待验收" }, do: "看图", not_do: "", done_when: "看到图" });
    await settle(); await settle();

    const walkBox = p.el("cardWalk");
    assert.ok(walkBox && visible(walkBox), "有走查截图的卡必须显示 #cardWalk");
    assert.ok(chatHead.classList.contains("has-walk"), "显示 #cardWalk 时应给头部挂 has-walk 类");
    const boxText = text(walkBox);
    assert.ok(boxText.includes("走查截图（2 张）"), "应显示走查截图总张数：" + boxText);
    assert.ok(boxText.includes("右栏总览") && boxText.includes("点开交接"), "应显示各步步骤名：" + boxText);

    const imgs = walkBox.querySelectorAll("img");
    assert.strictEqual(imgs.length, 3, "第1步(截图+设计图) + 第2步(仅截图) 应共 3 张图，实际：" + imgs.length);
    const parsed = imgs.map(i => new URL(i.src, "http://fixture.invalid"));
    assert.ok(parsed.every(u => u.pathname === "/api/clerk/card/image"), "图片必须走只读看图接口");
    assert.strictEqual(parsed[0].searchParams.get("token"), "secret-tok");
    assert.strictEqual(parsed[0].searchParams.get("project"), "/p/A");
    assert.strictEqual(parsed[0].searchParams.get("card"), "CARD-001");
    assert.strictEqual(parsed[0].searchParams.get("kind"), "shot");
    assert.strictEqual(parsed[0].searchParams.get("path"), SHOTS_TWO[0].shot);
    assert.strictEqual(parsed[1].searchParams.get("kind"), "design");
    assert.strictEqual(parsed[1].searchParams.get("path"), SHOTS_TWO[0].design);
    assert.strictEqual(parsed[2].searchParams.get("kind"), "shot");
    assert.strictEqual(parsed[2].searchParams.get("path"), SHOTS_TWO[1].shot);

    // 记住第一个 img 节点引用：后续 3 秒轮询相同数据时不能被整块重建
    imgs[0].__marker = "keep-me";

    // 点击第 1 步本次截图缩略图按钮 → 弹出大图
    const thumbs = walkBox.querySelectorAll("button.walk-thumb");
    assert.strictEqual(thumbs.length, 3, "每张图都应有可点击的 .walk-thumb 按钮");
    thumbs[0].click();
    await settle();
    const lb = p.el("walkLightbox");
    assert.ok(lb && visible(lb), "点击缩略图应打开大图浮层 #walkLightbox");
    assert.ok(text(lb).includes("右栏总览") && text(lb).includes("本次截图"), "大图标题应含步骤名和类型：" + text(lb));
    const lbImg = lb.querySelector("img");
    assert.ok(lbImg && lbImg.src === imgs[0].src, "大图地址应与点击的缩略图一致");

    // 3 秒轮询相同数据时，大图不能被关掉
    await p.advance(3100);
    assert.ok(visible(lb), "3 秒刷新相同数据时不能关掉正在看的大图");
    assert.strictEqual(walkBox.querySelectorAll("img")[0].__marker, "keep-me", "相同数据刷新不应重建截图区 DOM");

    // 点关闭按钮关闭大图
    const closeBtn = p.el("walkLightboxClose");
    assert.ok(closeBtn && text(closeBtn) === "关闭", "大图浮层应有文字为“关闭”的按钮 #walkLightboxClose");
    closeBtn.click();
    await settle();
    assert.ok(!visible(lb), "点关闭后大图应隐藏");

    // 点击设计图按钮弹出大图，再按 Escape 关闭
    thumbs[1].click();
    await settle();
    assert.ok(visible(lb) && text(lb).includes("设计图"), "点设计图应打开对应大图");
    assert.strictEqual(lb.querySelector("img").src, imgs[1].src);
    p.document.dispatchEvent({ type: "keydown", key: "Escape" });
    await settle();
    assert.ok(!visible(lb), "按 Escape 应关闭大图浮层");

    // 点击收起截图 → 列表隐藏、按钮文字变“展开截图”，3 秒刷新仍保持收起
    const foldBtn = p.el("cardWalkFold");
    assert.ok(foldBtn && text(foldBtn) === "收起截图", "初始展开态按钮应写“收起截图”");
    assert.ok(visible(imgs[0]), "展开态图片应可见");
    foldBtn.click();
    await settle();
    assert.strictEqual(text(foldBtn), "展开截图", "收起后按钮应写“展开截图”");
    assert.ok(!visible(imgs[0]), "收起后图片应不可见");
    await p.advance(3100);
    assert.strictEqual(text(foldBtn), "展开截图", "3 秒刷新相同数据时不能重置收起状态");
    assert.ok(!visible(imgs[0]), "3 秒刷新相同数据时列表仍应保持收起");

    // 再点展开截图恢复
    foldBtn.click();
    await settle();
    assert.strictEqual(text(foldBtn), "收起截图");
    assert.ok(visible(imgs[0]));

    // 后端更新成无走查截图（如用户点继续改新增了追加要求）：截图区自动隐藏、正在看的大图自动关闭、移除 .has-walk
    thumbs[0].click();
    await settle();
    assert.ok(visible(lb));
    currentShots = [];
    await p.advance(3100);
    assert.ok(!visible(walkBox), "截图变空后应自动隐藏 #cardWalk");
    assert.ok(!visible(lb), "截图变空后应关闭大图浮层");
    assert.ok(!chatHead.classList.contains("has-walk"), "截图变空后应移除 has-walk");

    // 恢复有图时重新查询当前按钮（避免点到已移除的旧节点）；部分设计图消失时若正看该大图应同步关闭浮层
    currentShots = SHOTS_TWO;
    await p.advance(3100);
    assert.ok(visible(walkBox));
    assert.ok(chatHead.classList.contains("has-walk"), "恢复显示 #cardWalk 后应重新挂回 has-walk");
    const thumbs2 = walkBox.querySelectorAll("button.walk-thumb");
    assert.strictEqual(thumbs2.length, 3, "恢复后应重新渲染 3 个缩略图按钮");
    thumbs2[1].click();
    await settle();
    assert.ok(visible(lb) && text(lb).includes("设计图"), "点恢复后的设计图应打开大图");

    currentShots = [
      { step: SHOTS_TWO[0].step, shot: SHOTS_TWO[0].shot, design: "" },
      SHOTS_TWO[1],
    ];
    await p.advance(3100);
    assert.ok(visible(walkBox), "仍有其余截图时 #cardWalk 应继续显示");
    assert.ok(!visible(lb), "正在看的设计图从新列表消失后，大图浮层应自动关闭");

    currentShots = SHOTS_TWO;
    await p.advance(3100);
    assert.ok(visible(walkBox));
    const thumbs3 = walkBox.querySelectorAll("button.walk-thumb");
    assert.strictEqual(thumbs3.length, 3, "再次恢复后应重新渲染 3 个缩略图按钮");
    thumbs3[0].click();
    await settle();
    assert.ok(visible(lb));
    chat.closeCard();
    await settle();
    assert.ok(!visible(walkBox), "closeCard 后应隐藏 #cardWalk");
    assert.ok(!visible(lb), "closeCard 后应关闭大图");
    assert.ok(!chatHead.classList.contains("has-walk"), "closeCard 后应移除 has-walk");

    chat.openCard({ id: "CARD-001", conversation: "c-001", title: "带图卡片", phase: { label: "待验收" } });
    await settle(); await settle();
    assert.ok(visible(walkBox));
    chat.startFresh();
    await settle();
    assert.ok(!visible(walkBox), "startFresh 后应隐藏 #cardWalk");
    assert.ok(!chatHead.classList.contains("has-walk"), "startFresh 后应移除 has-walk");

    p.check();
  }

  // 2. 慢响应防窜卡：卡 1 的 /api/view 比卡 2 更晚回来，绝不能把卡 1 的走查图画到卡 2 上
  {
    let releaseCard1;
    const p = openChatPage(req => {
      if (req.url.pathname === "/api/view") {
        const c = req.url.searchParams.get("card");
        if (c === "CARD-001") {
          return new Promise(resolve => {
            releaseCard1 = () => resolve({
              status: 200, ok: true,
              json: async () => ({ now: { text: "卡1", tone: "ok" }, walk_shots: SHOTS_TWO }),
            });
          });
        }
        return { now: { text: "卡2", tone: "ok" }, walk_shots: [] };
      }
      return { ok: true };
    });
    await settle(); await settle();
    const chat = p.context.SuperAgentChat;
    chat.openCard({ id: "CARD-001", conversation: "c-001", title: "卡1", phase: { label: "待验收" } });
    await settle();
    chat.openCard({ id: "CARD-002", conversation: "c-002", title: "卡2", phase: { label: "待验收" } });
    await settle(); await settle();
    releaseCard1();
    await settle(); await settle();
    const walkBox = p.el("cardWalk");
    assert.ok(!walkBox || !visible(walkBox), "晚到的卡 1 响应不能把截图画到卡 2 上");
    p.check();
  }

  console.log("OK");
})().catch(err => { console.error(err && err.stack || err); process.exit(1); });
