/* 去掉调度员 D5：团队页规则层只有写测试、写代码、复查三个岗位。由 test_nodispatcher_05_team_page.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var T = require(path.join(__dirname, "..", "desktop", "static", "team.js"));

var ALL = ["tester", "developer", "reviewer"];
var team = { tester: "codex", developer: "cursor", reviewer: "codex" };
var solo = { tester: "cursor", developer: "agy", reviewer: "none" };
var ok = { ok: true }, bad = { ok: false };

function save(o) {
  return T.canSave(Object.assign({ team: team, active: ALL, results: {}, configured: false, busy: false }, o));
}

// 1. 第一次配置：三位执行员全测通才能保存；不再要求选调度员
assert.strictEqual(save({}), false, "第一次没测不能保存");
assert.strictEqual(save({ results: { tester: ok } }), false, "只测了一部分不能保存");
assert.strictEqual(save({ results: { tester: ok, developer: ok, reviewer: ok } }), true, "没有调度员这一项也能保存");
assert.strictEqual(save({ results: { tester: ok, developer: ok, reviewer: bad } }), false);
assert.strictEqual(save({ team: solo, results: { tester: ok, developer: ok } }), true, "不复查不用测也算全员");

// 2. 已配置的项目：没测的不拦，只拦标红的和检测中的
assert.strictEqual(save({ configured: true }), true);
assert.strictEqual(save({ configured: true, results: { tester: bad } }), false);
assert.strictEqual(save({ configured: true, busy: true }), false);

// 3. 没接入的文件夹没有执行员可存：团队本身存不了（页面上只能存行政、回对话）
assert.strictEqual(save({ active: [], configured: true }), false);
assert.strictEqual(save({ active: [], configured: false }), false);

// 4. 老页面传来的 dispatcher 字段不起作用
assert.strictEqual(save({ team: Object.assign({ dispatcher: "" }, team), configured: true }), true, "空的调度员不拦保存");

// 5. 存不了时说清是谁：没有“还没选调度员”这种说法
assert.strictEqual(T.unsavedReason({ team: team, active: ALL, results: { reviewer: bad }, configured: true }), "复查没测通");
assert.strictEqual(T.unsavedReason({ team: team, active: ALL, results: { tester: ok }, configured: false }), "写代码、复查还没检测");
assert.strictEqual(T.unsavedReason({ team: team, active: ALL, results: {}, configured: true }), "");
assert.strictEqual(T.unsavedReason({ team: { dispatcher: "" }, active: [], results: {}, configured: true }), "");

// 6. 一起测的分组只在三位执行员里算
assert.deepStrictEqual(T.rolesSharingTool(team, "reviewer", ALL), ["tester", "reviewer"]);
assert.deepStrictEqual(T.rolesSharingTool(solo, "reviewer", ALL), []);

// 7. 恢复没保存的改动：草稿里残留的调度员改动不再恢复，三位执行员照旧
var base = { team: { dispatcher: "codex", tester: "codex", developer: "cursor", reviewer: "codex" },
             picks: { dispatcher: { tool: "codex", model: "m", effort: "xhigh" }, tester: { tool: "codex", model: "m", effort: "high" } } };
var mine = JSON.parse(JSON.stringify(base));
mine.picks.dispatcher.effort = "medium";
mine.picks.tester.effort = "medium";
var got = T.draftRows(base, { base: base, now: mine });
assert.deepStrictEqual(Object.keys(got), ["tester"], "只恢复写测试，调度员那份旧草稿丢掉");

// 8. 行政保存成功后下一步：没接入的文件夹没有执行员可存，直接回对话（原来会误报“团队有没通过检测的项”）
assert.strictEqual(T.afterClerkSaved({ canSaveTeam: true, installed: true, configured: false }), "save-team");
assert.strictEqual(T.afterClerkSaved({ canSaveTeam: false, installed: false, configured: true }), "leave", "没接入的文件夹直接回对话");
assert.strictEqual(T.afterClerkSaved({ canSaveTeam: false, installed: false, configured: false }), "leave");
assert.strictEqual(T.afterClerkSaved({ canSaveTeam: false, installed: true, configured: false }), "leave", "团队没配过：只用行政也能回对话");
assert.strictEqual(T.afterClerkSaved({ canSaveTeam: false, installed: true, configured: true }), "team-unsaved", "团队配过又改了、没测通：照实说");

// 9. 没配行政不能离开设置页：行政是必配，不再有退回去和调度员聊的路
assert.strictEqual(T.leaveBlockedReason({ clerkReady: true }), "");
assert.ok(/行政/.test(T.leaveBlockedReason({ clerkReady: false })), "要说清还差行政");
assert.ok(!/调度/.test(T.leaveBlockedReason({ clerkReady: false })));

console.log("OK");
