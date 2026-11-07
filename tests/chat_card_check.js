// 用假页面跑真实的 chat.js：主对话和任务对话之间切换，话要发到对的地方。
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(path.join(__dirname, "..", "desktop", "static", "chat.js"), "utf8");
const elements = {};
const posts = [];

function makeElement(id) {
  return {
    id, hidden: false, value: "", textContent: "", innerHTML: "", placeholder: "", className: "", style: {},
    children: [], classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener(type, fn) { (this.listeners = this.listeners || {})[type] = fn; },
    appendChild(child) { this.children.push(child); return child; },
    replaceChild(next) { return next; }, removeChild() {}, insertBefore(child) { return child; },
    querySelector() { return makeElement("q"); }, querySelectorAll() { return []; },
    setAttribute() {}, getAttribute() { return null; }, focus() {}, remove() {}, after() {}, closest() { return null; },
    scrollTo() {},
  };
}

const sandbox = {
  console, URLSearchParams, encodeURIComponent,
  setTimeout: () => 0, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
  location: { search: "?project=/p&dispatcher=&token=t&clerk=1" },
  document: {
    getElementById(id) { return elements[id] || (elements[id] = makeElement(id)); },
    createElement(tag) { return makeElement(tag); },
    createTextNode(text) { return { text }; },
  },
  fetch(url, options) {
    if (options && options.method === "POST") {
      const body = JSON.parse(options.body);
      posts.push([url, body]);
      const answer = { ok: true, conversation: body.conversation || "a".repeat(32) };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(answer) });
    }
    return new Promise(() => {});
  },
  addEventListener() {},
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(source, sandbox);

const wait = () => new Promise((done) => setImmediate(done));
const main = "a".repeat(32);
const card = { id: "CARD-001", conversation: "c".repeat(32), title: "改按钮", phase: { label: "进行中" }, do: "改颜色", not_do: "", done_when: "变蓝" };
const lastSend = () => posts.filter((p) => p[0] === "/api/clerk/send").slice(-1)[0][1].conversation;
async function say(words) {
  elements.input.value = words;
  elements.composer.listeners.submit({ preventDefault() {} });
  await wait(); await wait();
}

(async () => {
  await wait(); await wait();
  const chat = sandbox.SuperAgentChat;

  await say("主对话一句");
  await wait();

  chat.openCard(card);
  await wait();
  assert.strictEqual(elements.cardHead.hidden, false, "打开卡片要显示任务信息");
  await say("任务里的话");
  assert.strictEqual(lastSend(), card.conversation, "任务里的话要发进任务对话");

  elements.input.value = "写了一半的话";
  chat.closeCard();
  await wait();
  assert.strictEqual(elements.cardHead.hidden, true, "回主对话要收起任务信息");
  assert.strictEqual(elements.input.value, "", "草稿不能带到别的对话");
  await say("回到主对话的话");
  assert.strictEqual(lastSend(), main, "点主对话后，话要发回主对话");

  chat.openCard(card);
  await wait();
  chat.openCard(Object.assign({}, card, { id: "CARD-002", conversation: "d".repeat(32) }));
  await wait();
  chat.closeCard();
  await wait();
  await say("连开两张卡后的话");
  assert.strictEqual(lastSend(), main, "连开两张卡后，还要回到原来的主对话");

  chat.closeCard();
  await wait();
  await say("没开卡时再收一次");
  assert.strictEqual(lastSend(), main, "没开卡时收起不能换对话");

  // 左栏只能点历史对话回去：窗口先收起卡片再打开这一段，之后的话和再开卡都要跟着这一段
  const other = "e".repeat(32);
  chat.openCard(card);
  await wait();
  chat.closeCard();
  chat.openThread(other);
  await wait();
  await say("点历史对话回去后的话");
  assert.strictEqual(lastSend(), other, "话要进点的那一段");
  chat.openCard(card);
  await wait();
  chat.closeCard();
  await wait();
  await say("再开卡又收起");
  assert.strictEqual(lastSend(), other, "收起卡片回到点过的那一段，不是更早的主对话");

  // 菜单里开新对话：任务信息要收起
  chat.openCard(card);
  await wait();
  chat.startFresh();
  await wait();
  assert.strictEqual(elements.cardHead.hidden, true, "开新对话要收起任务信息");
  console.log("OK");
  process.exit(0);
})().catch((err) => { console.error(err.message); process.exit(1); });
