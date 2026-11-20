// 右栏显示小修三条（docs/右栏显示小修/方案.md）验收：真的执行右栏和聊天页脚本，看用户看到的样子。
// 由 tests/test_rightfix_acceptance.py 调用（TZ=Asia/Shanghai）。
"use strict";
const assert = require("assert");
const { page, settle, visible, text, Element } = require("./ability_69b_dom.js");

const LONG = "复查员（codex）复查不通过（已自动退回 2 次）。复查还剩 4 件事：① 停用行政后切回来，卡片仍然看不见；"
  + "② 切回上次停在任务卡的项目，再点回原来的主对话，已经存好的字没了（严重）。判断：自动退回用完了，还有严重问题。";

function st(key, role, state, extra) {
  return Object.assign({ key, role, who: key === "you" || key === "accept" ? "你" : "Codex", face: "C", state,
    detail: state === "idle" ? "" : role + "的说明" }, extra || {});
}
function seven(over) {
  const list = [st("you", "提需求", "done"), st("disp", "写方案", "done"), st("test", "写测试", "done"),
    st("dev", "写代码", "done"), st("check", "跑测试", "done"), st("rev", "复查", "done"), st("accept", "确认", "idle")];
  (over || []).forEach(o => { const i = list.findIndex(s => s.key === o.key); list[i] = Object.assign(list[i], o); });
  return list;
}
function view(extra) {
  return Object.assign({ card: { id: "CARD-019", title: "切换项目", state: "测试中", phase: null },
    stations: seven(), handoffs: [], tokens: { total: 0, other: 0 }, now: { text: "", tone: "idle" },
    installed: true, elapsed: 0, back: false, stale: false }, extra || {});
}
function openPanel(v) {
  const holder = { v };
  const p = page("panel.html", {
    search: "?project=%2Fp&clerk=1&token=t",
    respond(req) { return req.url.pathname === "/api/view" ? holder.v : { ok: true }; },
  });
  p.swap = async next => { holder.v = next; await p.advance(1600); };   // 右栏每 1.5 秒取一次数据
  return p;
}
function rows(p) { return p.el("steps").children.map(li => li.children[0]); }
function shownRows(p) { return p.el("steps").children.filter(li => visible(li) && visible(li.children[0])); }
function all(el) { return [el].concat(...el.children.map(all)); }

const failed = [];
async function part(name, fn) { try { await fn(); } catch (e) { failed.push(name + "：" + (e && e.message)); } }

(async () => {
  // 1. 正在干活那一步：状态行不带 token，用量另起一行（小修第 1 条）
  await part("1 token 另起一行", async () => {
    const tokens = { ok: true, total: 2552000, input: 2500000, cached: 2000000, output: 52000 };
    const p = openPanel(view({ stations: seven([{ key: "test", state: "active", rounds: 4, elapsed: 119, quiet: 3, tokens }]) }));
    await settle(); await settle();
    const btn = rows(p)[2];
    const k = btn.querySelector(".k");
    assert.ok(/第 4 轮/.test(text(k)) && /已干/.test(text(k)), "状态行还在：" + text(k));
    assert.ok(!/token/.test(text(k)), "状态行里不再带 token：" + text(k));
    const usage = all(btn).filter(e => e !== btn && e.children.length === 0 && /token/.test(text(e)));
    assert.strictEqual(usage.length, 1, "用量单独一行：" + JSON.stringify(usage.map(text)));
    assert.ok(!/已干|动静/.test(text(usage[0])), "用量那行只写用量");
    await p.advance(1500);   // 计时刷新后仍然分开
    assert.ok(!/token/.test(text(btn.querySelector(".k"))), "刷新后状态行仍不带 token");
    // 干完的步骤一样分两行
    const p2 = openPanel(view({ stations: seven([{ key: "dev", state: "done", rounds: 2, spent: 300, tokens }]) }));
    await settle(); await settle();
    const k2 = rows(p2)[3].querySelector(".k");
    assert.ok(/共 2 轮/.test(text(k2)) && !/token/.test(text(k2)), "干完的步骤状态行也不带 token：" + text(k2));
    p.check(); p2.check();
  });

  // 2. 卡停下：右栏顶部只留“卡住了”和按钮，不铺长文字（小修第 2 条）
  await part("2 停下时右栏只留按钮", async () => {
    const phase = { code: "blocked", label: "卡住了", tone: "bad", needs_you: true, detail: LONG,
      actions: ["resume", "revise", "accept_issues", "discard"] };
    const p = openPanel(view({ card: { id: "CARD-019", title: "切换项目", state: "需Claude介入", phase } }));
    await settle(); await settle();
    const hint = text(p.el("phaseHint"));
    assert.ok(/卡住了/.test(hint), "顶部还写卡住了：" + hint);
    assert.ok(!hint.includes("复查还剩") && !hint.includes("自动退回") && hint.length < 40, "顶部不铺长文字：" + hint);
    const labels = p.el("actionBtns").children.map(b => text(b));
    assert.deepStrictEqual(labels, ["接着跑", "不满意，继续改", "接受剩下的问题，进入待确认", "放弃这个需求"]);
    // 别的阶段的提示不受影响（原有行为）
    const other = { code: "plan_ready", label: "方案写好了", tone: "wait", detail: "请看方案，批准后开工", actions: ["approve", "replan", "discard"] };
    const p2 = openPanel(view({ card: { id: "CARD-020", title: "x", state: "待批准", phase: other } }));
    await settle(); await settle();
    assert.strictEqual(text(p2.el("phaseHint")), "方案写好了 · 请看方案，批准后开工", "别的阶段照旧写说明");
    p.check(); p2.check();
  });

  // 3. Claude 接手一格：有就显示在复查和确认之间，没有就不占位（小修第 3 条②）
  await part("3 Claude 接手一格", async () => {
    const eight = seven([{ key: "rev", state: "done", detail: "复查退回 4 件，已由 Claude 接手修好" }]);
    eight.splice(6, 0, { key: "takeover", role: "Claude 接手", who: "Claude", face: "C", state: "done",
      detail: "改了 4 处 · Codex 复查 2 轮 · 2 条记进以后再修" });
    eight[7].state = "done";
    const p = openPanel(view({ stations: eight, card: { id: "CARD-019", title: "切换项目", state: "已合并", phase: null } }));
    await settle(); await settle();
    const shown = shownRows(p);
    assert.strictEqual(shown.length, 8, "接手过的卡显示 8 格");
    assert.ok(/Claude 接手/.test(text(shown[6])) && /改了 4 处/.test(text(shown[6])), "第 7 格是 Claude 接手：" + text(shown[6]));
    assert.ok(/确认/.test(text(shown[7])), "最后一格还是确认");
    assert.ok(/已由 Claude 接手修好/.test(text(shown[5])), "复查格写明已接手修好");
    const p2 = openPanel(view());
    await settle(); await settle();
    assert.strictEqual(shownRows(p2).length, 7, "没接手过的卡还是 7 格，不留空格");
    await p.swap(view());   // 同一个右栏从接手过的卡换到没接手的卡
    assert.strictEqual(shownRows(p).length, 7, "换到没接手的卡，第 8 格藏回去");
    assert.ok(!/Claude 接手/.test(text(p.el("steps"))), "不留上一张卡的接手格");
    p.check(); p2.check();
  });

  // 4. 本卡合计下面写完成时间（小修第 3 条③）
  await part("4 完成时间", async () => {
    const p = openPanel(view({ tokens: { total: 19632638, other: 937588 }, finished_at: 1795150840.8,
      card: { id: "CARD-019", title: "切换项目", state: "已合并", phase: null } }));
    await settle(); await settle();
    const flow = text(p.el("pane-flow"));
    assert.ok(flow.includes("本卡合计"), "合计还在");
    assert.ok(flow.includes("完成于 11-20 13:00"), "合计下面有完成时间：" + flow.slice(-80));
    assert.ok(flow.indexOf("完成于") > flow.indexOf("本卡合计"), "完成时间在合计下面");
    const p2 = openPanel(view({ tokens: { total: 1000, other: 0 } }));
    await settle(); await settle();
    assert.ok(!text(p2.el("pane-flow")).includes("完成于"), "没完成的卡不写完成时间");
    await p.swap(view({ tokens: { total: 1000, other: 0 } }));   // 同一个右栏换到没完成的卡
    assert.ok(!text(p.el("pane-flow")).includes("完成于"), "换到没完成的卡，完成时间不残留");
    const p3 = openPanel(view({ tokens: { total: 0, other: 0 }, finished_at: 1795150840.8,
      card: { id: "CARD-022", title: "x", state: "已合并", phase: null } }));
    await settle(); await settle();
    assert.ok(text(p3.el("pane-flow")).includes("完成于 11-20 13:00"), "没有合计 token 也写完成时间");
    p.check(); p2.check(); p3.check();
  });

  // 5. 中间卡片标题下那一行：收起时一行、点“展开”看全文，3 秒刷新不收回去，换卡回到收起（小修第 2 条）
  await part("5 中间那行收起展开", async () => {
    let nowText = LONG;
    const p = page("chat.html", {
      search: "?project=%2Fp&token=t&clerk=1",
      before(document) {
        // 页面里没有排版：按字数模拟“放不下一行”（长于 40 字就算放不下）
        document.createElement = tag => {
          const e = new Element(tag);
          Object.defineProperty(e, "scrollHeight", { get: () => (e.textContent.length > 40 ? 300 : 100), configurable: true });
          return e;
        };
      },
      respond(req) {
        const path = req.url.pathname;
        if (path === "/api/view") return { now: { text: nowText, tone: "bad" }, live: false };
        if (path === "/api/cards") return [];
        if (path === "/api/clerk/list") return { threads: [], current: null };
        if (path === "/api/clerk/history" || path === "/api/clerk/events") return { events: [], open: true, busy: false };
        return { ok: true };
      },
    });
    await settle(); await settle();
    const chat = p.context.SuperAgentChat;
    chat.openCard({ id: "CARD-019", title: "切换项目", phase: { label: "卡住了" } });
    await settle(); await settle();
    const line = p.el("cardNow");
    assert.ok(line && visible(line), "卡片标题下那一行在");
    assert.ok(text(line).includes("复查还剩 4 件事"), "全文放在这里");
    assert.ok(line.classList.contains("fold"), "默认收起成一行");
    const btn = () => p.document.querySelectorAll("button").find(b => /^(展开|收起)$/.test(text(b)) && b.id !== "cardFold");
    assert.ok(btn() && visible(btn()) && text(btn()) === "展开", "放不下时有“展开”");
    btn().click();   // 真点一下：不管按钮用哪种方式接点击都算
    await settle();
    assert.ok(!line.classList.contains("fold"), "点展开后看全文");
    assert.strictEqual(text(btn()), "收起");
    await p.advance(3100);   // 每 3 秒刷新一次那一行
    assert.ok(!p.el("cardNow").classList.contains("fold"), "刷新后不自己收回去");
    assert.ok(text(p.el("cardNow")).includes("复查还剩 4 件事"));
    nowText = "已放进项目";   // 展开着，刷新后文字变短、一行放得下
    await p.advance(3100);
    assert.ok(!btn() || !visible(btn()), "展开着变短了：按钮藏起来");
    assert.strictEqual(text(p.el("cardNow")), "已放进项目");
    nowText = LONG;
    await p.advance(3100);
    chat.openCard({ id: "CARD-020", title: "另一张", phase: { label: "卡住了" } });
    await settle(); await settle();
    assert.ok(p.el("cardNow").classList.contains("fold"), "换一张卡回到收起");
    nowText = "已放进项目";
    chat.openCard({ id: "CARD-021", title: "短的", phase: { label: "已完成" } });
    await settle(); await settle();
    assert.ok(!btn() || !visible(btn()), "一行放得下就不出“展开”");
    p.check();
  });

  if (failed.length) { console.error(failed.join("\n")); process.exit(1); }
  console.log("OK");
})().catch(err => { console.error(err && err.stack || err); process.exit(1); });
