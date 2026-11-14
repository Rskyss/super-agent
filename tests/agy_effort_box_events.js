/* 团队页 agy 强度框的点选交互（Codex 复查 11-14 两条）：用假页面跑 renderRoleModelBox，失败就报错退出。
   第一个参数是内置 agy 名单的 JSON。由 test_agy_effort_box.py 调用。 */
"use strict";
var assert = require("assert");
var fs = require("fs");
var path = require("path");
var vm = require("vm");

var file = path.join(__dirname, "..", "desktop", "static", "team.js");
var src = fs.readFileSync(file, "utf8");
var T = require(file);

function el(tag) {
  var e = {
    tagName: tag, children: [], hidden: false, value: "", textContent: "", innerHTML: "", handlers: {},
    appendChild: function (c) { e.children.push(c); c.parentNode = e; if (tag === "select" && e.children.length === 1) e.value = c.value; return c; },
    removeChild: function (c) { e.children.splice(e.children.indexOf(c), 1); c.parentNode = null; return c; },
    setAttribute: function () {}, focus: function () {},
    addEventListener: function (n, f) { e.handlers[n] = f; },
    fire: function (n, v) { e.value = v; e.handlers[n](); },
  };
  return e;
}
var box = el("div");
Object.defineProperty(box, "innerHTML", { set: function () { box.children = []; }, get: function () { return ""; } });
var toolSel = el("select");
var ctx = {
  TeamCore: T, document: { createElement: el },
  modelOptions: { agy: JSON.parse(process.argv[2]), codex: [], codex_effort: [] },
  ROLE: { tester: "写测试" }, picks: {}, currentModels: { agy: "gemini-4-argon-high" },
  rows: { tester: { querySelector: function () { return box; } } },
  toolSelect: function () { return toolSel; }, onModelModified: function () {},
};
vm.createContext(ctx);
vm.runInContext(src.slice(src.indexOf("  function renderRoleModelBox("), src.indexOf("  function rolePicks(")), ctx);

function start(model) { toolSel.value = "agy"; ctx.picks.tester = { tool: "agy", model: model }; ctx.renderRoleModelBox("tester"); }
function controls() { return { model: box.children[0].children[1], custom: box.children[1], effortRow: box.children[2] }; }

// 1. 改了强度再换模型：按改过的档位找最接近的
start("gemini-4-hydrogen-high");
controls().effortRow.children[1].fire("change", "gemini-4-hydrogen-low");
assert.strictEqual(ctx.picks.tester.model, "gemini-4-hydrogen-low");
controls().model.fire("change", "gemini-4-argon");
assert.strictEqual(ctx.picks.tester.model, "gemini-4-argon-low", "Hydrogen 改成低后换 Argon，应是 Argon 低");

// 2. 选“其他自定义模型名”后强度框收起，自定义名不会被盖掉
start("gemini-4-hydrogen-high");
var oldEffort = controls().effortRow.children[1];
controls().model.fire("change", "__custom__");
assert.strictEqual(box.children.length, 2, "填自定义名时强度框要拿掉（页面样式会让 hidden 不起作用）");
controls().custom.fire("input", "my-custom-model");
oldEffort.fire("change", "gemini-4-hydrogen-low");
assert.strictEqual(ctx.picks.tester.model, "my-custom-model", "旧强度框的事件也不能盖掉自定义名");

// 3. 名单外的旧值：单框显示、没有强度框，存的原样
start("my-custom-model");
assert.strictEqual(controls().model.value, "my-custom-model");
assert.ok(!box.children[2] || box.children[2].className !== "model-row effort", "名单外不出强度框");
assert.strictEqual(ctx.picks.tester.model, "my-custom-model");

console.log("OK");
