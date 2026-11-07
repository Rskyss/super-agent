/* 左栏和右栏“规则层”的检查：用 node 跑，失败就报错退出。由 test_desktop_nav.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var N = require(path.join(__dirname, "..", "desktop", "static", "nav.js"));
var P = require(path.join(__dirname, "..", "desktop", "static", "panel.js"));

// 1. 卡片圆点：只看 phase，没有 phase 时只显示状态原文
assert.deepStrictEqual(N.dotFor({ state: "待对齐", phase: { tone: "bad", label: "方案没写成" } }),
  { tone: "bad", label: "方案没写成" });
assert.deepStrictEqual(N.dotFor({ state: "没见过的状态" }), { tone: "done", label: "没见过的状态" });

// 2. 搜索：标题和编号，不分大小写
var cards = [{ id: "CARD-001", title: "三处显示问题修复" }, { id: "CARD-002", title: "Audio 听感" }];
assert.strictEqual(N.filter(cards, "").length, 2);
assert.strictEqual(N.filter(cards, "  ").length, 2);
assert.deepStrictEqual(N.filter(cards, "显示").map(function (c) { return c.id; }), ["CARD-001"]);
assert.deepStrictEqual(N.filter(cards, "card-002").map(function (c) { return c.id; }), ["CARD-002"]);
assert.deepStrictEqual(N.filter(cards, "audio").map(function (c) { return c.id; }), ["CARD-002"]);
assert.strictEqual(N.filter(cards, "没有这个").length, 0);

// 2b. 任务卡片一个列表：要你处理的排最前，同类顺序不变
var mixed = [{ id: "a", title: "a", phase: { tone: "done", label: "已完成" } }, { id: "b", title: "b", phase: { tone: "run", label: "进行中" } },
             { id: "c", title: "c", phase: { tone: "ok", label: "等你验收" } }, { id: "d", title: "d", phase: { tone: "bad", label: "卡住了" } }];
mixed[2].phase.needs_you = mixed[3].phase.needs_you = true;
var grouped = N.cardGroups(mixed);
assert.deepStrictEqual(grouped.map(function (g) { return g.title; }), ["等你处理", "进行中", "已完成"]);
assert.deepStrictEqual(grouped.map(function (g) { return g.cards.map(function (c) { return c.id; }); }), [["c", "d"], ["b"], ["a"]]);
assert.deepStrictEqual(N.cardGroups([]), [], "空的分组不显示");
assert.deepStrictEqual(N.cardGroups([mixed[1]]).map(function (g) { return g.title; }), ["进行中"]);

// 2b-1. 标签：打开时按当前在看什么
assert.strictEqual(N.startTab("card"), "cards");
assert.strictEqual(N.startTab("main"), "main");
assert.strictEqual(N.startTab("settings"), "main");

// 2b-2. 主对话标签的搜索只看标题
var titled = [{ id: "a", title: "项目进度及待办" }, { id: "b", title: "Audio 听感" }];
assert.deepStrictEqual(N.filterThreads(titled, "进度").map(function (t) { return t.id; }), ["a"]);
assert.deepStrictEqual(N.filterThreads(titled, "AUDIO").map(function (t) { return t.id; }), ["b"]);
assert.strictEqual(N.filterThreads(titled, "  ").length, 2);

// 2b-3. 没有历史对话（如只能用终端的调度员）时，看卡片或设置也要有路回到对话
assert.strictEqual(N.needsBackRow(0, "card", true), true);
assert.strictEqual(N.needsBackRow(0, "settings", true), true);
assert.strictEqual(N.needsBackRow(0, "main", true), false);
assert.strictEqual(N.needsBackRow(2, "card", true), false);
assert.strictEqual(N.needsBackRow(0, "settings", false), false, "没有行政也没有调度员：没有对话可回");

// 2c. 历史对话：去掉括号说明、时间、最多先显示几条
assert.strictEqual(N.threadTitle("新对话（刚接手项目）"), "新对话");
assert.strictEqual(N.threadTitle("那请帮我修复好。"), "那请帮我修复好。");
assert.strictEqual(N.threadTitle(""), "对话");
var now = new Date(2026, 10, 5, 16, 3);
assert.strictEqual(N.timeLabel(new Date(2026, 10, 5, 15, 43).getTime() / 1000, now), "15:43");
assert.strictEqual(N.timeLabel(new Date(2026, 10, 4, 9, 5).getTime() / 1000, now), "昨天 09:05");
assert.strictEqual(N.timeLabel(new Date(2026, 10, 3, 9, 5).getTime() / 1000, now), "11-03");
assert.strictEqual(N.timeLabel(new Date(2026, 9, 31, 23, 50).getTime() / 1000, new Date(2026, 10, 1, 8, 0)), "昨天 23:50", "跨月也算昨天");
assert.strictEqual(N.timeLabel(0, now), "");
var many = [1, 2, 3, 4, 5, 6, 7].map(function (n) { return { id: "t" + n }; });
assert.strictEqual(N.visibleThreads(many, false).length, 5);
assert.strictEqual(N.visibleThreads(many, true).length, 7);
assert.strictEqual(N.visibleThreads(many.slice(0, 2), false).length, 2);

// 2d. 点历史对话时马上标出选中，不等连接
var ts = [{ id: "a", current: true }, { id: "b", current: false }];
assert.deepStrictEqual(N.markCurrent(ts, "b").map(function (t) { return t.current; }), [false, true]);
assert.deepStrictEqual(N.markCurrent(ts, null).map(function (t) { return t.current; }), [false, false]);
assert.strictEqual(ts[0].current, true, "不改原列表");

// 3. 选中的卡不在了，回到主对话
assert.strictEqual(N.keepSelected("CARD-001", cards), "CARD-001");
assert.strictEqual(N.keepSelected("CARD-009", cards), null);
assert.strictEqual(N.keepSelected(null, cards), null);

// 4. 右栏流程：状态归类、文字去掉内部前缀
assert.strictEqual(P.stepClass("done"), "done");
assert.strictEqual(P.stepClass("active"), "active");
assert.strictEqual(P.stepClass("wait"), "wait");
assert.strictEqual(P.stepClass("fail"), "fail");
assert.strictEqual(P.stepClass("新状态"), "idle");
assert.strictEqual(P.cleanDetail("汇报：测试已交付"), "测试已交付");
assert.strictEqual(P.cleanDetail("结论：通过"), "通过");
assert.strictEqual(P.cleanDetail("第 1 轮：测试全部通过"), "第 1 轮：测试全部通过");
assert.strictEqual(P.cleanDetail(""), "");

console.log("OK");
