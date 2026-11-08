/* 积木库页面“规则层”的检查：用 node 跑，失败就报错退出。由 test_acceptance_56_blocks_page.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var B = require(path.join(__dirname, "..", "desktop", "static", "blocks.js"));

var providers = [
  { id: "tencent", name: "腾讯云", base_url: "https://tokenhub.tencentmaas.com/v1", models: ["deepseek/deepseek-flash"] },
  { id: "bailian", name: "阿里百炼", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    models: ["qwen-flash", "qwen-plus"] },
  { id: "other", name: "其他", base_url: "", models: [] },
];

// 1. 打开窗口先看哪一页：没绑过 → 绑定页；绑过但这次还没连上（密钥没交过来）→ 重新连上；连上了 → 聊天
assert.strictEqual(B.startView({ setting: null, connected: false }), "bind");
assert.strictEqual(B.startView({ setting: { provider: "tencent", model: "m" }, connected: false }), "reconnect");
assert.strictEqual(B.startView({ setting: { provider: "tencent", model: "m" }, connected: true }), "main");
assert.strictEqual(B.startView(null), "bind", "取不到状态时按没绑过处理");

// 2. 绑定表单：去掉首尾空格；认得的服务商不带地址（地址由程序定，密钥不会被发到别处）
var ok = B.bindRequest({ provider: "tencent", model: " deepseek/deepseek-flash ", base_url: "https://evil.example",
                         api_key: " sk-abc " }, providers);
assert.strictEqual(ok.ok, true);
assert.deepStrictEqual(ok.body, { provider: "tencent", model: "deepseek/deepseek-flash", api_key: "sk-abc" });
var other = B.bindRequest({ provider: "other", model: "m1", base_url: " https://api.example.com/v1 ", api_key: "k" },
                          providers);
assert.deepStrictEqual(other.body, { provider: "other", model: "m1", api_key: "k", base_url: "https://api.example.com/v1" });

// 3. 表单不对，在页面上就说清楚，不发请求
function err(form) {
  var r = B.bindRequest(form, providers);
  assert.strictEqual(r.ok, false, JSON.stringify(form));
  assert.ok(/[一-鿿]/.test(r.error), "错误要是中文：" + r.error);
  return r.error;
}
assert.ok(err({ provider: "tencent", model: "m", api_key: "" }).indexOf("密钥") >= 0);
assert.ok(err({ provider: "tencent", model: " ", api_key: "k" }).indexOf("模型") >= 0);
assert.ok(err({ provider: "nobody", model: "m", api_key: "k" }).indexOf("服务商") >= 0);
assert.ok(err({ provider: "other", model: "m", api_key: "k", base_url: "http://plain.example" }).indexOf("https://") >= 0);
assert.ok(err({ provider: "other", model: "m", api_key: "k", base_url: "" }).indexOf("地址") >= 0);

// 4. 模型名给建议：跟着服务商走
assert.deepStrictEqual(B.modelChoices(providers, "bailian"), ["qwen-flash", "qwen-plus"]);
assert.deepStrictEqual(B.modelChoices(providers, "other"), []);
assert.deepStrictEqual(B.modelChoices(providers, "nobody"), []);

// 5. 绑定结果用大白话说
assert.strictEqual(B.bindResultText({ ok: true }), "绑定好了");
var no = B.bindResultText({ ok: false, reason: "这个模型不会用工具，当不了行政", how: "换一个支持工具调用的模型" });
assert.ok(no.indexOf("当不了行政") >= 0 && no.indexOf("换一个") >= 0);
assert.ok(B.bindResultText({ ok: false, reason: "", how: "" }).length > 0, "没有原因也要说点什么");

// 6. 基础层列表：状态和类型
assert.strictEqual(B.statusText({ status: "ok" }), "能用");
assert.strictEqual(B.statusText({ status: "fail" }), "不能用");
assert.strictEqual(B.statusText({ status: "never" }), "没检测过");
assert.strictEqual(B.statusClass({ status: "ok" }), "ok");
assert.strictEqual(B.statusClass({ status: "fail" }), "bad");
assert.strictEqual(B.statusClass({ status: "never" }), "idle");
assert.strictEqual(B.kindText("cli"), "CLI");
assert.strictEqual(B.kindText("llm_api"), "大模型 API");

// 7. 中间的聊天：复用现有聊天页的行政模式，资料夹和口令放在地址里
var url = B.chatUrl("/Users/x/Library/Application Support/超级助手/积木库", "口令 1");
assert.ok(url.indexOf("/static/chat.html?") === 0, url);
var q = new URLSearchParams(url.split("?")[1]);
assert.strictEqual(q.get("clerk"), "1");
assert.strictEqual(q.get("project"), "/Users/x/Library/Application Support/超级助手/积木库");
assert.strictEqual(q.get("token"), "口令 1");

// 8. 网页代码只当文字显示（对话标题、积木名字来自模型或用户）
assert.strictEqual(B.esc("<img src=x onerror=alert(1)>&\"'"), "&lt;img src=x onerror=alert(1)&gt;&amp;&quot;&#39;");

// 9. 删除对话前问一句，说清楚删的是哪一段
var words = B.deleteConfirm({ id: "a", title: "装 Codex" });
assert.ok(words.indexOf("装 Codex") >= 0 && words.indexOf("删除") >= 0);

console.log("OK");
