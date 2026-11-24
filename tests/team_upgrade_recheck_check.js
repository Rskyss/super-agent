/* 团队页升级提示与一键重测“规则层”检查：用 node 跑，失败就报错退出。由 test_team_upgrade_recheck.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var T = require(path.join(__dirname, "..", "desktop", "static", "team.js"));

var ALL = ["tester", "developer", "reviewer"];
var ROLE = { tester: "写测试", developer: "写代码", reviewer: "复查" };

// 1. 工具到命令映射：Codex、千问、腾讯云共用 codex 壳
assert.strictEqual(T.commandOf("codex"), "codex");
assert.strictEqual(T.commandOf("qwen"), "codex");
assert.strictEqual(T.commandOf("tencent"), "codex");
assert.strictEqual(T.commandOf("cursor"), "agent");
assert.strictEqual(T.commandOf("agy"), "agy");
assert.strictEqual(T.commandOf("none"), "");

// 2. Codex 升级影响 codex / qwen / tencent，不影响 agy / cursor / none
var codexUpgrade = {
  command: "codex",
  name: "Codex（千问也用它的壳）",
  old: "codex-cli 0.167.0",
  now: "codex-cli 0.171.0",
  tools: ["codex", "qwen", "tencent"],
  message: "Codex（千问也用它的壳） 升级了（codex-cli 0.167.0 → codex-cli 0.171.0）"
};
var teamMixed = { tester: "agy", developer: "qwen", reviewer: "codex" };
assert.deepStrictEqual(T.upgradeAffectedRoles(codexUpgrade, teamMixed, ALL), ["developer", "reviewer"]);
assert.deepStrictEqual(
  T.upgradeAffectedRoles(codexUpgrade, { tester: "tencent", developer: "cursor", reviewer: "none" }, ALL),
  ["tester"]
);

// 3. 初始状态：显示受影响岗位，按钮可点
var items = T.normalizeUpgrades([codexUpgrade], [codexUpgrade.message]);
var st1 = T.upgradeState({ items: items, resolved: {}, team: teamMixed, active: ALL, results: {}, pending: {} });
assert.strictEqual(st1.visible.length, 1);
assert.deepStrictEqual(st1.toCheckRoles, ["developer", "reviewer"]);
assert.strictEqual(st1.checking, false);
var line1 = T.upgradeLineText(st1.visible[0], ROLE);
assert(line1.indexOf("受影响岗位：写代码、复查") !== -1, line1);

// 4. 点一键重测：受影响岗位都处于 pending -> checking=true，按钮禁用，待测列表为空
var st2 = T.upgradeState({
  items: items, resolved: st1.resolved, team: teamMixed, active: ALL,
  results: {}, pending: { developer: 1, reviewer: 1 }
});
assert.strictEqual(st2.visible.length, 1);
assert.strictEqual(st2.checking, true);
assert.deepStrictEqual(st2.toCheckRoles, []);

// 5. 部分通过（写代码通了，复查还没测完/没通过）：提示保留，文案只催“还差 复查”
var st3 = T.upgradeState({
  items: items, resolved: st2.resolved, team: teamMixed, active: ALL,
  results: { developer: { ok: true } }, pending: {}
});
assert.strictEqual(st3.visible.length, 1);
assert.strictEqual(st3.checking, false);
assert.deepStrictEqual(st3.toCheckRoles, ["reviewer"]);
var line3 = T.upgradeLineText(st3.visible[0], ROLE);
assert(line3.indexOf("还差 复查 未重新检测") !== -1, line3);

// 6. 复查失败时：只重测没通过的复查，不重复测已通的写代码
var stFail = T.upgradeState({
  items: items, resolved: st3.resolved, team: teamMixed, active: ALL,
  results: { developer: { ok: true }, reviewer: { ok: false } }, pending: {}
});
assert.strictEqual(stFail.visible.length, 1);
assert.deepStrictEqual(stFail.toCheckRoles, ["reviewer"]);

// 7. 全部受影响岗位测通：自动消掉红字，记入 resolved；之后改模型清掉结果也不回弹
var st4 = T.upgradeState({
  items: items, resolved: stFail.resolved, team: teamMixed, active: ALL,
  results: { developer: { ok: true }, reviewer: { ok: true } }, pending: {}
});
assert.strictEqual(st4.visible.length, 0);
assert.strictEqual(st4.resolved.codex, true);
var stAfterModelEdit = T.upgradeState({
  items: items, resolved: st4.resolved, team: teamMixed, active: ALL,
  results: { reviewer: { ok: true } }, pending: {}
});
assert.strictEqual(stAfterModelEdit.visible.length, 0, "本轮已全部测通消掉的升级提示，换模型不回弹");

// 8. 多个工具同时升级：测通其中一家先消掉那一家，另一家保留
var agyUpgrade = {
  command: "agy",
  name: "agy",
  old: "1.5.9",
  now: "1.5.14",
  tools: ["agy"],
  message: "agy 升级了（1.5.9 → 1.5.14）"
};
var stMulti = T.upgradeState({
  items: [codexUpgrade, agyUpgrade], resolved: {}, team: teamMixed, active: ALL,
  results: { tester: { ok: true } }, pending: {}
});
assert.strictEqual(stMulti.visible.length, 1);
assert.strictEqual(stMulti.visible[0].item.command, "codex");
assert.strictEqual(stMulti.resolved.agy, true);
assert(!stMulti.resolved.codex);

// 9. 把所有岗位暂时切走：提示先隐藏，但不标成 resolved；切回来后照旧提醒
var teamAway = { tester: "agy", developer: "cursor", reviewer: "none" };
var stAway = T.upgradeState({ items: items, resolved: {}, team: teamAway, active: ALL, results: {}, pending: {} });
assert.strictEqual(stAway.visible.length, 0);
assert(!stAway.resolved.codex, "切走不等于测通，不能标成 resolved");
var stBack = T.upgradeState({ items: items, resolved: stAway.resolved, team: teamMixed, active: ALL, results: {}, pending: {} });
assert.strictEqual(stBack.visible.length, 1);

// 10. 未接入的文件夹（active=[]）不显示提示
var stNoGit = T.upgradeState({ items: items, resolved: {}, team: teamMixed, active: [], results: {}, pending: {} });
assert.strictEqual(stNoGit.visible.length, 0);

// 11. 兼容旧格式：只有 upgraded 字符串数组时也能正常展示并全员测通后消掉
var legacyItems = T.normalizeUpgrades(null, ["Codex（千问也用它的壳） 升级了（0.167.0 → 0.171.0）"]);
var stLegacy1 = T.upgradeState({ items: legacyItems, resolved: {}, team: teamMixed, active: ALL, results: { tester: { ok: true } }, pending: {} });
assert.strictEqual(stLegacy1.visible.length, 1);
assert.deepStrictEqual(stLegacy1.toCheckRoles, ["developer", "reviewer"]);
var stLegacy2 = T.upgradeState({
  items: legacyItems, resolved: stLegacy1.resolved, team: teamMixed, active: ALL,
  results: { tester: { ok: true }, developer: { ok: true }, reviewer: { ok: true } }, pending: {}
});
assert.strictEqual(stLegacy2.visible.length, 0);

console.log("OK");
