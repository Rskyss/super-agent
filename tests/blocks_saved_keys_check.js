/* 60 号单：存过的密钥免填，页面规则层检查。用 node 跑，失败就报错退出。由 test_acceptance_60_saved_keys.py 调用。 */
"use strict";
var assert = require("assert");
var path = require("path");
var B = require(path.join(__dirname, "..", "desktop", "static", "blocks.js"));
var C = require(path.join(__dirname, "..", "desktop", "static", "chat.js"));

var providers = [
  { id: "tencent", name: "腾讯云", base_url: "https://tokenhub.tencentmaas.com/v1", models: [] },
  { id: "bailian", name: "阿里百炼", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", models: [] },
  { id: "other", name: "其他", base_url: "", models: [] },
];

// 1. 账号名：和本机服务、钥匙串同一套
assert.strictEqual(B.accountFor("tencent", ""), "tencent");
assert.strictEqual(B.accountFor("bailian", "https://evil.example"), "bailian");
assert.strictEqual(B.accountFor("other", " https://API.Example.com/v1 "), "other:api.example.com");
assert.strictEqual(B.accountFor("other", "not a url"), "");
assert.strictEqual(B.accountFor("other", ""), "");
assert.strictEqual(B.accountFor("nobody", "https://x.example"), "");

// 2. 这一家存过：密钥空也能检测，请求里不带密钥
var r = B.bindRequest({ provider: "tencent", model: "m", api_key: "" }, providers, ["tencent"]);
assert.strictEqual(r.ok, true, JSON.stringify(r));
assert.strictEqual(r.useSaved, true);
assert.ok(!("api_key" in r.body), "用存的那把时请求里不带密钥");
assert.deepStrictEqual(r.body, { provider: "tencent", model: "m" });

// 3. 存的是别家：照旧要填
r = B.bindRequest({ provider: "tencent", model: "m", api_key: "" }, providers, ["bailian"]);
assert.strictEqual(r.ok, false);
assert.ok(r.error.indexOf("密钥") >= 0);

// 4. 填了新的：用新的
r = B.bindRequest({ provider: "tencent", model: "m", api_key: " sk-new " }, providers, ["tencent"]);
assert.strictEqual(r.ok, true);
assert.strictEqual(r.body.api_key, "sk-new");
assert.ok(!r.useSaved);

// 5. “其他”按地址分开
r = B.bindRequest({ provider: "other", model: "m", base_url: "https://api.example.com/v1", api_key: "" }, providers,
                  ["other:api.example.com"]);
assert.strictEqual(r.ok, true, JSON.stringify(r));
assert.deepStrictEqual(r.body, { provider: "other", model: "m", base_url: "https://api.example.com/v1" });
r = B.bindRequest({ provider: "other", model: "m", base_url: "https://other.example.com/v1", api_key: "" }, providers,
                  ["other:api.example.com"]);
assert.strictEqual(r.ok, false, "换了地址就是另一家");

// 6. 不传已保存列表：和原来完全一样
r = B.bindRequest({ provider: "tencent", model: "m", api_key: "" }, providers);
assert.strictEqual(r.ok, false);

// 7. 提议卡知道是哪个账号
var card = C.blockCard({ proposalKind: "connect_api",
  info: { provider: "bailian", model: "qwen-plus", name: "通义", block: { base_url: "https://x", key_account: "bailian" } } });
assert.strictEqual(card.needsKey, true);
assert.strictEqual(card.keyAccount, "bailian");

// 8. 原因自带句号时，和“怎么办”之间不叠成两个句号
assert.strictEqual(B.bindResultText({ ok: false, reason: "密钥不对。", how: "换一把再试" }), "密钥不对。换一把再试");

// 9. 只有密钥本身的问题才劝“换一把”
[B.keyProblem, C.keyProblem].forEach(function (f) {
  assert.strictEqual(f("模型密钥不对或已失效，请到设置里重新填写。"), true);
  assert.strictEqual(f("模型额度用完了，或请求太频繁。"), true);
  assert.strictEqual(f("这个模型不会用工具，当不了行政"), false);
  assert.strictEqual(f("这家服务商没有这个模型，或服务地址不对。"), false);
  assert.strictEqual(f("连不上模型服务，请检查网络。"), false);
  assert.strictEqual(f(""), false);
  assert.strictEqual(f(undefined), false);
});

console.log("ok");
