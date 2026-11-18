/* 团队配置页“规则层”的检查：用 node 跑，失败就报错退出。由 test_desktop_team.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var T = require(path.join(__dirname, "..", "desktop", "static", "team.js"));

var ALL = ["tester", "developer", "reviewer"];   // 11-17 起没有调度员这一行
var team = { tester: "codex", developer: "agy", reviewer: "agy" };
var ok = { ok: true }, bad = { ok: false };

// 1. 点一行：同一家工具的几行一起测，别家不动
assert.deepStrictEqual(T.rolesSharingTool(team, "reviewer", ALL), ["developer", "reviewer"]);
assert.deepStrictEqual(T.rolesSharingTool(team, "tester", ALL), ["tester"]);
var solo = { tester: "cursor", developer: "agy", reviewer: "none" };
assert.deepStrictEqual(T.rolesSharingTool(solo, "tester", ALL), ["tester"], "只有这一家时只测这一行");

// 2. 选“不复查”的那行不用测，也不算进“全员”
assert.strictEqual(T.needsCheck(solo, "reviewer"), false);
assert.strictEqual(T.needsCheck(solo, "developer"), true);
assert.deepStrictEqual(T.rolesSharingTool({ reviewer: "none" }, "reviewer", ALL), [], "不复查的行没有可测的");

// 3. 第一次配置：全员测通才能保存
function save(o) {
  return T.canSave(Object.assign({ team: team, active: ALL, results: {}, configured: false, busy: false }, o));
}
assert.strictEqual(save({}), false, "第一次没测不能保存");
assert.strictEqual(save({ results: { tester: ok } }), false, "第一次只测了一部分不能保存");
assert.strictEqual(save({ results: { tester: ok, developer: ok, reviewer: ok } }), true);
assert.strictEqual(save({ results: { tester: ok, developer: ok, reviewer: bad } }), false);
assert.strictEqual(save({ team: solo, results: { tester: ok, developer: ok } }), true, "不复查不用测也算全员");

// 4. 已配置的项目：没测的不拦，只拦标红的
assert.strictEqual(save({ configured: true }), true, "已配置不测也能保存");
assert.strictEqual(save({ configured: true, results: { tester: ok } }), true, "只测了一个也能保存");
assert.strictEqual(save({ configured: true, results: { tester: bad } }), false, "标红的要拦");

// 5. 任何时候：检测进行中都不能保存
assert.strictEqual(save({ configured: true, busy: true }), false);

// 6. 没接入的文件夹没有执行员可存（11-17 起没有调度员那一行，团队本身存不了）
assert.strictEqual(save({ active: [], results: {} }), false);

console.log("OK");
