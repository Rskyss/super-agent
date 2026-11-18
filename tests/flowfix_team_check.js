/* 流程修理 ①②：团队页规则层。由 test_flowfix_11_team_page.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var T = require(path.join(__dirname, "..", "desktop", "static", "team.js"));
var ALL = ["tester", "developer", "reviewer"];   // 11-17 起没有调度员这一行
var team = { tester: "codex", developer: "cursor", reviewer: "codex" };

// ① 同一家工具，设置一样才一起测；各选各的就各测各的
var same = { developer: { tool: "codex", model: "m", effort: "high" }, tester: { tool: "codex", model: "m", effort: "high" },
             reviewer: { tool: "codex", model: "m", effort: "xhigh" } };
var teamTwo = { tester: "codex", developer: "codex", reviewer: "codex" };
assert.deepStrictEqual(T.rolesSharingTool(teamTwo, "tester", ALL, same), ["tester", "developer"]);
assert.deepStrictEqual(T.rolesSharingTool(teamTwo, "reviewer", ALL, same), ["reviewer"]);
assert.deepStrictEqual(T.rolesSharingTool(team, "reviewer", ALL), ["tester", "reviewer"], "不给 picks 照旧");

// ② 存不了时说清是谁
var ok = { ok: true }, bad = { ok: false };
assert.strictEqual(T.unsavedReason({ team: team, active: ALL, results: { reviewer: bad }, configured: true }), "复查没测通");
assert.strictEqual(T.unsavedReason({ team: team, active: ALL, results: { tester: ok }, configured: false }),
                   "写代码、复查还没检测");
console.log("OK");
