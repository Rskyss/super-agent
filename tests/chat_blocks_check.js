/* 57 号单：聊天页里积木库三种提议卡（安装、接入 CLI、接入大模型 API）的规则层检查。
   用 node 跑，失败就报错退出。由 test_acceptance_57_block_proposals.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var C = require(path.join(__dirname, "..", "desktop", "static", "chat.js"));

// 1. 哪些是积木库的提议
assert.strictEqual(C.isBlockKind("install"), true);
assert.strictEqual(C.isBlockKind("connect"), true);
assert.strictEqual(C.isBlockKind("connect_api"), true);
["task", "team", "profile", "revise", "", undefined].forEach(function (k) {
  assert.strictEqual(C.isBlockKind(k), false, "项目窗口的提议不算：" + k);
});

// 2. 记账层留下提议卡要显示的内容
var m = new C.Model();
m.apply({ type: "proposal", id: "p1", kind: "install", state: "open", card: "", install: "codex-npm", tool: "codex",
  label: "用 npm 装 Codex", command: "npm install -g @openai/codex", source: "https://www.npmjs.com/package/@openai/codex",
  needs: "npm", minutes: 2 });
var ins = m.blocks[m.blocks.length - 1];
assert.strictEqual(ins.kind, "proposal");
assert.strictEqual(ins.proposalKind, "install");
assert.strictEqual(ins.info.command, "npm install -g @openai/codex");
assert.strictEqual(ins.info.label, "用 npm 装 Codex");
assert.strictEqual(ins.info.minutes, 2);

var drafted = { id: "mytool", kind: "cli", name: "我的工具", command: "mytool", run: ["{prompt}"], source: "clerk" };
m.apply({ type: "proposal", id: "p2", kind: "connect", state: "open", card: "", block: drafted, name: "我的工具",
  verified: false, preview: "/usr/local/bin/mytool '新建 连通检测.txt，内容写 OK'", replaces: "旧的工具" });
var cli = m.blocks[m.blocks.length - 1];
assert.strictEqual(cli.info.verified, false);
assert.strictEqual(cli.info.block.command, "mytool");
assert.strictEqual(cli.info.replaces, "旧的工具");

var apiBlock = { id: "bailian-qwen-plus", kind: "llm_api", name: "通义", provider: "bailian",
  base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus", key_account: "bailian" };
m.apply({ type: "proposal", id: "p3", kind: "connect_api", state: "open", card: "", block: apiBlock, name: "通义",
  provider: "bailian", model: "qwen-plus" });
var api = m.blocks[m.blocks.length - 1];
assert.strictEqual(api.info.model, "qwen-plus");
assert.strictEqual(api.info.block.base_url, "https://dashscope.aliyuncs.com/compatible-mode/v1");

// 项目窗口的提议照旧
m.apply({ type: "proposal", id: "p4", kind: "task", state: "open", card: "", title: "加按钮", do: "做", not_do: "不做",
  done_when: "能点" });
var task = m.blocks[m.blocks.length - 1];
assert.strictEqual(task.title, "加按钮");
assert.strictEqual(task.do, "做");
assert.ok(!task.info || Object.keys(task.info).length === 0, "项目窗口的提议不多带东西");

// 确认后状态照旧更新，内容不丢
m.apply({ type: "proposal_done", id: "p1", state: "confirmed", card: "" });
assert.strictEqual(ins.state, "confirmed");
assert.strictEqual(ins.info.label, "用 npm 装 Codex");

// 3. 卡上显示什么
function rowText(card, label) {
  var row = card.rows.filter(function (r) { return r.label === label; })[0];
  return row ? row : null;
}
var c1 = C.blockCard(ins);
assert.ok(c1.title.indexOf("用 npm 装 Codex") >= 0, c1.title);
assert.strictEqual(rowText(c1, "会执行的命令").text, "npm install -g @openai/codex", "命令要原样显示");
assert.strictEqual(rowText(c1, "会执行的命令").code, true, "命令用等宽字显示");
assert.strictEqual(rowText(c1, "来源").text, "https://www.npmjs.com/package/@openai/codex");
assert.strictEqual(rowText(c1, "大概要多久").text, "约 2 分钟");
assert.ok(rowText(c1, "要先有").text.indexOf("npm") >= 0);
assert.strictEqual(c1.needsKey, false);
assert.strictEqual(c1.confirmText, "确认安装");
assert.strictEqual(c1.warn, "");
var noNeeds = C.blockCard({ proposalKind: "install", info: { label: "装 X", command: "x", source: "https://x.example", minutes: 1 } });
assert.strictEqual(rowText(noNeeds, "要先有"), null, "没有前提就不显示这一行");
var brew = C.blockCard({ proposalKind: "install", info: { label: "装 Node", command: "brew install node", source: "https://x.example", minutes: 5, needs: "brew" } });
assert.ok(rowText(brew, "要先有").text.indexOf("Homebrew") >= 0);

var c2 = C.blockCard(cli);
assert.ok(c2.title.indexOf("我的工具") >= 0, c2.title);
assert.ok(c2.warn.indexOf("未核实") >= 0 && c2.warn.indexOf("风险") >= 0, "没核实的要标出风险：" + c2.warn);
assert.strictEqual(rowText(c2, "要运行的完整命令").text, cli.info.preview, "完整命令原样显示");
assert.strictEqual(rowText(c2, "要运行的完整命令").code, true);
assert.ok(rowText(c2, "会替换").text.indexOf("旧的工具") >= 0);
assert.strictEqual(c2.needsKey, false);
assert.ok(c2.confirmText.indexOf("检测") >= 0, c2.confirmText);

var verifiedCli = C.blockCard({ proposalKind: "connect", info: { name: "Codex", verified: true, block: { id: "codex" } } });
assert.strictEqual(verifiedCli.warn, "", "核实过的不标风险");
assert.strictEqual(rowText(verifiedCli, "要运行的完整命令"), null, "核实过的不用再亮完整命令");
assert.strictEqual(rowText(verifiedCli, "会替换"), null);

var c3 = C.blockCard(api);
assert.ok(c3.title.indexOf("通义") >= 0, c3.title);
assert.strictEqual(rowText(c3, "服务商").text, "阿里百炼");
assert.strictEqual(rowText(c3, "模型").text, "qwen-plus");
assert.strictEqual(rowText(c3, "地址").text, "https://dashscope.aliyuncs.com/compatible-mode/v1");
assert.strictEqual(c3.needsKey, true, "接大模型 API 要密钥框");
assert.strictEqual(C.blockCard({ proposalKind: "connect_api", info: { provider: "tencent", block: {} } }).rows[0].text, "腾讯云");
assert.strictEqual(rowText(C.blockCard({ proposalKind: "connect_api", info: { provider: "other", block: {} } }), "服务商").text, "其他");

// 处理过的卡的标题
assert.ok(C.blockDoneTitle(ins).indexOf("用 npm 装 Codex") >= 0);
assert.ok(C.blockDoneTitle(ins).indexOf("已确认") === 0);
assert.ok(C.blockDoneTitle(Object.assign({}, api, { state: "confirmed" })).indexOf("通义") >= 0);
assert.strictEqual(C.blockDoneTitle(Object.assign({}, api, { state: "cancelled" })), "好的，先不做。");
assert.strictEqual(C.blockDoneTitle(Object.assign({}, api, { state: "replaced" })), "已换成新的提议");

// 4. 后台任务的进度变成卡片下面的显示
var run = C.jobView({ kind: "install", done: false, result: null, error: "", tail: ["added 1 package", "第二行"] });
assert.strictEqual(run.state, "running");
assert.ok(run.text.indexOf("正在安装") >= 0);
assert.deepStrictEqual(run.lines, ["added 1 package", "第二行"]);
assert.strictEqual(run.canSave, false);

var insOk = C.jobView({ kind: "install", done: true, error: "", tail: ["done"],
  result: { ok: true, status: "ok", reason: "codex 装好了", how: "" } });
assert.strictEqual(insOk.state, "ok");
assert.ok(insOk.text.indexOf("codex 装好了") >= 0);
assert.ok(insOk.text.indexOf("行政") >= 0, "装好后告诉用户跟行政说一声，接着接入：" + insOk.text);
assert.strictEqual(insOk.canSave, false, "装好不等于接进基础层");
assert.deepStrictEqual(insOk.lines, ["done"], "装完后还能看到最后几行");

var insBad = C.jobView({ kind: "install", done: true, error: "", tail: [],
  result: { ok: false, status: "needs_admin", reason: "要管理员权限才能装，超级助手不会自动提权", how: "换一种安装方式" } });
assert.strictEqual(insBad.state, "fail");
assert.ok(insBad.text.indexOf("管理员权限") >= 0 && insBad.text.indexOf("换一种安装方式") >= 0, insBad.text);

var checking = C.jobView({ kind: "check", done: false, result: null, error: "" });
assert.strictEqual(checking.state, "running");
assert.ok(checking.text.indexOf("正在检测") >= 0);
assert.deepStrictEqual(checking.lines, []);

var passed = C.jobView({ kind: "check", done: true, error: "", result: { ok: true, reason: "通了 · 4.2 秒", how: "", seconds: 4.2 } });
assert.strictEqual(passed.state, "ok");
assert.ok(passed.text.indexOf("检测通过") >= 0 && passed.text.indexOf("4.2") >= 0, passed.text);
assert.strictEqual(passed.canSave, true, "检测通过才出“放进基础层”");

var savedJob = C.jobView({ kind: "check", done: true, error: "", saved: true, result: { ok: true, seconds: 3 } });
assert.strictEqual(savedJob.canSave, false, "已经放进去的不再出按钮");
assert.ok(savedJob.text.indexOf("已放进基础层") >= 0, savedJob.text);

var failed = C.jobView({ kind: "check", done: true, error: "", result: { ok: false, reason: "没登录", how: "在终端里登录一次" } });
assert.strictEqual(failed.state, "fail");
assert.ok(failed.text.indexOf("没登录") >= 0 && failed.text.indexOf("在终端里登录一次") >= 0, failed.text);
assert.strictEqual(failed.canSave, false);

var vague = C.jobView({ kind: "check", done: true, error: "", result: { ok: false } });
assert.ok(vague.text.length > 0, "什么都没说也要给一句");

var broke = C.jobView({ kind: "check", done: true, error: "后台任务出错了，请再试一次", result: null });
assert.strictEqual(broke.state, "fail");
assert.ok(broke.text.indexOf("后台任务出错了") >= 0);

var lost = C.jobView(null);
assert.strictEqual(lost.state, "lost");
assert.ok(lost.text.indexOf("基础层") >= 0 || lost.text.indexOf("重新提议") >= 0, "找不到结果时说怎么办：" + lost.text);
assert.strictEqual(lost.canSave, false);

// 5. 原有的东西还在
["esc", "md", "Model", "actsSummary", "usageText", "modelFrom", "needsOpen", "cardInfoRows", "afterThreadDeleted"].forEach(function (k) {
  assert.ok(C[k], "原有的 " + k + " 不能丢");
});

console.log("OK");
