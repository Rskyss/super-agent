/* 左边聊天视图。分两层：
   ChatCore（记账层）：把调度员的聊天事件整理成“对话现在该显示什么”，不碰页面，可以单独测试；
   页面层：按记账结果画出来，只重画变了的那几块。
   安全：调度员回复里的任何文字都当纯文字处理，只排版加粗、列表、代码这几样，不会被当成网页代码执行。 */
var ChatCore = (function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function inline(s) {
    // 先整体转义，再认几种简单写法；所有内容都已经是纯文字
    return esc(s)
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  }

  /** 简单排版：段落、标题、列表、代码块。 */
  function md(text) {
    var lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
    var out = [], para = [], list = null, code = null;
    function flushPara() { if (para.length) { out.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; } }
    function flushList() { if (list) { out.push("<" + list.tag + ">" + list.items.map(function (i) { return "<li>" + inline(i) + "</li>"; }).join("") + "</" + list.tag + ">"); list = null; } }
    lines.forEach(function (line) {
      if (code !== null) {
        if (/^```/.test(line)) { out.push("<pre><code>" + esc(code.join("\n")) + "</code></pre>"); code = null; }
        else code.push(line);
        return;
      }
      if (/^```/.test(line)) { flushPara(); flushList(); code = []; return; }
      var h = /^(#{1,4})\s+(.*)$/.exec(line);
      var ul = /^\s*[-*•]\s+(.*)$/.exec(line);
      var ol = /^\s*\d+[.、)]\s+(.*)$/.exec(line);
      if (h) { flushPara(); flushList(); out.push("<p class=\"h\">" + inline(h[2]) + "</p>"); }
      else if (ul || ol) {
        flushPara();
        var tag = ul ? "ul" : "ol";
        if (!list || list.tag !== tag) { flushList(); list = { tag: tag, items: [] }; }
        list.items.push((ul || ol)[1]);
      } else if (!line.trim()) { flushPara(); flushList(); }
      else { flushList(); para.push(line); }
    });
    if (code !== null) out.push("<pre><code>" + esc(code.join("\n")) + "</code></pre>");
    flushPara(); flushList();
    return out.join("");
  }

  function Model() {
    this.blocks = [];       // 一块一块：sys / user / ai / ask / note / divider
    this.busy = false;
    this.usage = null;
    this.status = "";
    this.seq = 0;
    this._sawHistory = false;
    this._divided = false;
  }

  Model.prototype._push = function (block) { block.v = 1; this.blocks.push(block); return block; };
  Model.prototype._touch = function (block) { block.v = (block.v || 0) + 1; };
  Model.prototype._ai = function () {
    var last = this.blocks[this.blocks.length - 1];
    return last && last.kind === "ai" ? last : this._push({ kind: "ai", parts: [] });
  };
  Model.prototype._textPart = function (ai, id) {
    for (var i = ai.parts.length - 1; i >= 0; i--) {
      if (ai.parts[i].type === "text" && ai.parts[i].id === id) return ai.parts[i];
    }
    var part = { type: "text", id: id, text: "", done: false };
    ai.parts.push(part);
    return part;
  };

  /** 收下一条事件。重复的（编号不大于已收的）直接忽略，断线重连不会重复显示。 */
  Model.prototype.apply = function (ev) {
    if (ev.seq != null) { if (ev.seq <= this.seq) return; this.seq = ev.seq; }
    if (ev.history) this._sawHistory = true;
    else if (this._sawHistory && !this._divided && ev.type !== "status" && ev.type !== "usage") {
      this._divided = true;
      this._push({ kind: "divider", text: "以上是之前的对话" });
    }
    var ai, part, i;
    switch (ev.type) {
      case "status": this.status = ev.text; break;
      case "opening": this._push({ kind: "sys", text: ev.text }); break;
      case "user": this._push({ kind: "user", text: ev.text }); break;
      case "delta":
        ai = this._ai(); part = this._textPart(ai, ev.item);
        part.text += ev.text; this._touch(ai); break;
      case "message":
        if (!ev.text) break;
        ai = this._ai(); part = this._textPart(ai, ev.item);
        part.text = ev.text; part.done = true; this._touch(ai); break;
      case "activity":
        ai = this._ai();
        var acts = ai.parts[ai.parts.length - 1];
        if (!acts || acts.type !== "acts") { acts = { type: "acts", list: [], open: false }; ai.parts.push(acts); }
        var hit = null;
        for (i = 0; i < acts.list.length; i++) if (acts.list[i].id === ev.item) hit = acts.list[i];
        if (hit) { hit.label = ev.label; hit.status = ev.status; hit.detail = ev.detail || hit.detail; hit.text = ev.text || hit.text; }
        else acts.list.push({ id: ev.item, label: ev.label, status: ev.status, detail: ev.detail || "", text: ev.text || "" });
        this._touch(ai); break;
      case "image":
        ai = this._ai();
        // 画图中的那一行改成“画了 1 张图”
        ai.parts.forEach(function (p) {
          if (p.type === "acts") p.list.forEach(function (a) { if (a.id === ev.item) { a.status = "done"; a.label = "画了 1 张图"; } });
        });
        ai.parts.push({ type: "image", path: ev.path, prompt: ev.prompt || "" });
        this._touch(ai); break;
      case "approval":
        this._push({ kind: "ask", id: ev.id, what: ev.kind, command: ev.command || "", reason: ev.reason || "", state: "open" });
        break;
      case "approval_done":
        for (i = this.blocks.length - 1; i >= 0; i--) {
          var b = this.blocks[i];
          if (b.kind === "ask" && b.id === ev.id) { b.state = ev.decision; this._touch(b); break; }
        }
        break;
      case "proposal":
        var pkind = ev.kind || "task";
        var pblock = {
          kind: "proposal", id: ev.id, proposalKind: pkind,
          title: ev.title || "", do: ev.do || "", not_do: ev.not_do || "", done_when: ev.done_when || "",
          role: ev.role || "", tool: ev.tool || "", before: ev.before != null ? ev.before : "",
          profile: ev.profile || "", text: ev.text || "",
          state: ev.state || "open", card: ev.card || "",
        };
        if (isBlockKind(pkind)) {
          pblock.info = {};
          ["install", "tool", "label", "command", "source", "needs", "minutes", "block", "name", "verified", "preview", "replaces", "provider", "model",
           "category", "secrets", "domain", "danger", "new", "facts", "found_by", "key_account", "official", "maker", "risk"].forEach(function (k) {
            if (ev[k] != null) pblock.info[k] = ev[k];
          });
        }
        if (pkind === "ability") {
          pblock.ability = ev.ability || null;
          pblock.name = ev.name || "";
          pblock.block_name = ev.block_name || "";
          pblock.warns = Array.isArray(ev.warns) ? ev.warns : [];
          pblock.replaces = ev.replaces || "";
          pblock.trial = ev.trial || null;
          pblock.saved = !!ev.saved;
          var same = null;
          for (i = 0; i < this.blocks.length; i++) {
            if (this.blocks[i].kind === "proposal" && this.blocks[i].id === ev.id) { same = this.blocks[i]; break; }
          }
          if (same) {   // 同一张草稿后来又送来一次：按编号更新，不再多出一个框
            Object.keys(pblock).forEach(function (k) { same[k] = pblock[k]; });
            this._touch(same);
            break;
          }
        }
        this._push(pblock);
        break;
      case "proposal_done":
        for (i = this.blocks.length - 1; i >= 0; i--) {
          var pb = this.blocks[i];
          if (pb.kind === "proposal" && pb.id === ev.id) {
            pb.state = ev.state; pb.card = ev.card || ""; this._touch(pb); break;
          }
        }
        break;
      case "turn_started": this.busy = true; break;
      case "turn_done":
        this.busy = false;
        this.blocks.forEach(function (bl) { if (bl.kind === "ai") bl.parts.forEach(function (p) { if (p.type === "text") p.done = true; }); });
        var last = this.blocks[this.blocks.length - 1];
        if (last) this._touch(last);
        if (ev.status === "interrupted") this._push({ kind: "note", tone: "", text: "已停止" });
        if (ev.status === "failed") this._push({ kind: "note", tone: "bad", text: "这一轮没完成：" + (ev.error || "原因不明") });
        break;
      case "usage": this.usage = ev.total; break;
      case "notice": this._push({ kind: "note", tone: "", text: ev.text }); break;
      case "error": this._push({ kind: "note", tone: "bad", text: ev.text }); break;
    }
  };

  // 每种动作怎么说（按标签开头认），顺序就是摘要里出现的顺序
  var KINDS = [
    // 积木库管家的动作（名字不带空格，和下面项目窗口的分开）
    [/^查电脑/, "查了电脑"], [/^查安装/, "查了 {n} 次安装"], [/^读帮助/, "读了帮助说明"], [/^看基础层/, "看了基础层"],
    [/^翻手册/, "翻了 {n} 次手册"], [/^查模型/, "查了模型名单"], [/^搜网上/, "搜了网上 {n} 次"], [/^读网页/, "读了 {n} 个网页"], [/^(?:出提议|出卡)/, "出了 {n} 个提议"], [/^(?:没出提议|没出卡)/, "{n} 次没出成提议"],  // 旧叫法：以前的记录里是“出卡”
    [/^读 /, "读了 {n} 个文件"], [/^看文件夹/, "看了 {n} 个文件夹"], [/^搜索 /, "在项目里搜索 {n} 次"],
    [/^搜索网页/, "搜索网页 {n} 次"], [/^改 /, "改了 {n} 处文件"], [/^运行 /, "运行了 {n} 条命令"],
    [/^用工具/, "用了 {n} 次工具"], [/^看图片/, "看了 {n} 张图片"], [/^画/, "画了 {n} 张图"]
  ];

  /** 展开后每一行：有大白话（积木库管家记下的）就显示大白话 */
  function actLine(a) {
    return (a && a.text) || (a && a.label) || "";
  }

  /** 一组动作的摘要：“读了 2 个文件 · 运行了 1 条命令”。 */
  function actsSummary(list) {
    var counts = KINDS.map(function () { return 0; }), other = 0, declined = 0, going = null;
    list.forEach(function (a) {
      if (a.status === "started") going = a.label;
      if (a.status === "declined") declined++;
      for (var i = 0; i < KINDS.length; i++) { if (KINDS[i][0].test(a.label)) { counts[i]++; return; } }
      other++;
    });
    if (going) return going.replace(/…$/, "") + "…";
    var bits = [];
    KINDS.forEach(function (k, i) { if (counts[i]) bits.push(k[1].replace("{n}", counts[i])); });
    if (other) bits.push("另有 " + other + " 个动作");
    if (declined) bits.push("其中 " + declined + " 个被你拒绝");
    return bits.join(" · ") || "干活中";
  }

  function usageText(total) {
    if (total == null) return "";
    return "这次对话用量 " + (total >= 10000 ? (total / 10000).toFixed(1) + " 万" : total);
  }

  // 一段历史对话的记录直接变成画面（切换时先显示，不等连接）
  // 在干活但还没有文字往外出时显示“正在处理”；等用户确认时不显示
  Model.prototype.showWorking = function () {
    if (!this.busy) return false;
    var last = this.blocks[this.blocks.length - 1];
    if (last && last.kind === "ask" && last.state === "open") return false;
    var part = last && last.kind === "ai" ? last.parts[last.parts.length - 1] : null;
    return !(part && part.type === "text" && !part.done);
  };

  Model.prototype.dropErrors = function () {
    this.blocks = this.blocks.filter(function (b) { return !(b.kind === "note" && b.tone === "bad"); });
  };

  // 点左栏一段对话时要不要去连：换了对话，或这段上次没连上
  function needsOpen(id, current, failed) { return id !== current || !!failed; }

  // 卡片标题下那一行：此刻在干什么 · 已干多久 · 多久前有动静（流程修理 ⑪）
  function cardNowLine(v) {
    var now = (v && v.now) || {};
    var text = String(now.text || "");
    if (now.detail) text += "：" + now.detail;
    if (!v || !v.live) return text;
    var spent = v.worker_elapsed != null ? v.worker_elapsed : (v.elapsed || 0);  // 当前这个人干了多久（复查第 1 轮）
    var min = Math.floor(spent / 60);
    text += " · 已干" + (min < 1 ? "不到 1 分钟" : " " + min + " 分钟");
    if (v.quiet != null) text += " · " + (v.quiet < 60 ? "刚刚有动静" : Math.floor(v.quiet / 60) + " 分钟前有动静");
    return text;
  }
  function cardInfoRows(info) {
    var rows = [];
    if (!info) return rows;
    if (info.do) rows.push({ label: "要做什么", text: info.do });
    if (info.not_do) rows.push({ label: "不做什么", text: info.not_do });
    if (info.done_when) rows.push({ label: "怎么算做完", text: info.done_when });
    return rows;
  }

  function modelFrom(events) {
    var m = new Model();
    events.forEach(function (ev) { m.apply(ev); });
    m.busy = false;
    return m;
  }

  // 对话删掉后聊天页怎么办：正在看卡片时不打断，回主对话时是空白
  function afterThreadDeleted(id, state) {
    if (state.clerk) {
      return { clearMain: state.main === id, open: !state.viewingCard && state.current === id ? "blank" : null };
    }
    return { clearMain: false, open: state.current === id ? "fresh" : null };
  }

  function isBlockKind(kind) {
    return ["install", "connect", "connect_api", "web_install", "connect_search"].indexOf(kind) !== -1;
  }

  /** 没通过的原因是不是出在密钥本身：只有这时才劝“换一把”（和 BlocksCore.keyProblem 同一个判断） */
  function keyProblem(reason) {
    return /密钥|额度/.test(String(reason || ""));
  }

  function blockCard(b) {
    var info = b.info || {};
    if (b.proposalKind === "install") {
      var rows = [
        { label: "来源", text: info.source },
        { label: "会执行的命令", text: info.command, code: true },
        { label: "大概要多久", text: "约 " + info.minutes + " 分钟" }
      ];
      if (info.needs) rows.push({ label: "要先有", text: info.needs === "npm" ? "npm（装 Node 时自带）" : (info.needs === "brew" ? "Homebrew" : info.needs) });
      return { title: info.label, rows: rows, needsKey: false, confirmText: "确认安装", warn: "" };
    }
    if (b.proposalKind === "web_install") {
      // 网上找来的：黄色提醒；“下载脚本直接运行”换红色（沙盒接入方案第一节第 3 步）
      var rows = [
        { label: "来源", text: info.source },
        { label: "网站", text: info.domain, strong: true },
        { label: "原文核对", text: "✓ 这条命令在这一页的原文里找到了" + (info.found_by === "tavily" ? "（由联网搜索读出页面）" : "") }
      ];
      var unofficial = info.official === false;  // 积木库管家之前出的旧卡没有这一项：不显示，也不当成非官方
      if (typeof info.official === "boolean") {
        var maker = info.maker ? " · " + (info.official ? "厂商：" : "作者：") + info.maker : "";
        rows.splice(2, 0, { label: "谁做的", text: (info.official ? "官方" : "非官方（别人做的）") + maker });
      }
      (info.facts || []).forEach(function (f) { rows.push({ label: "安装包底细", text: f }); });
      if (info.risk) rows.push({ label: "风险", text: info.risk, danger: true });
      if (info.new) rows.push({ label: "提醒", text: "刚发布不久，多留个心" });
      rows.push({ label: "会执行的命令", text: info.command, code: true });
      var warnText = info.danger
        ? (unofficial ? "这是别人做的安装脚本，会直接运行，脚本做什么程序管不住。看清下面写的风险再确认"
                      : "这种方式会运行厂商的安装脚本，脚本做什么程序管不住。只在你确定这是官网时确认")
        : (unofficial ? "不在核实名单里，是别人做的，不是厂商官方的。看清下面写的风险再确认"
                      : "不在核实名单里，来自网上。请看清下面的网站是不是这个工具的官网");
      return { title: info.label, rows: rows, needsKey: false, confirmText: "确认安装", warn: warnText,
               warnLevel: info.danger ? "danger" : "caution" };
    }
    if (b.proposalKind === "connect_search") {
      var srows = [{ label: "用途", text: "让管家能上网找官网和安装说明" }, { label: "服务", text: "Tavily（免费版每月 1000 次）" }];
      if (info.replaces) srows.push({ label: "会替换", text: "基础层里的「" + info.replaces + "」" });
      return { title: "接进基础层：" + info.name, rows: srows, needsKey: true, confirmText: "检测", warn: "",
               keyAccount: info.key_account || "tavily", keyHint: "在这里填入 Tavily 密钥（tvly- 开头）" };
    }
    if (b.proposalKind === "connect") {
      var warn = info.verified ? "" : "未核实，有风险：确认前看清要运行的完整命令";
      var rows = [];
      if (info.category) rows.push({ label: "类别", text: info.category });
      if (!info.verified) rows.push({ label: "要运行的完整命令", text: info.preview, code: true });
      if (info.replaces) rows.push({ label: "会替换", text: "基础层里的「" + info.replaces + "」" });
      return { title: "接进基础层：" + info.name, rows: rows, needsKey: false, confirmText: info.verified ? "确认并检测" : "看过了，确认检测", warn: warn,
               secrets: info.secrets || [] };
    }
    if (b.proposalKind === "connect_api") {
      // 按整家接：卡上没有“模型”一栏；只有平台不给名单、管家另给了一个试通用的才显示。
      // 不放“服务商”一行：标题已经是这家的名字，“其他”两个字只让人困惑；地址留着，密钥就发到这里（用户 2026-11-10）
      var rows = [{ label: "地址", text: info.block.base_url }];
      if (info.model) rows.push({ label: "试通用的模型", text: info.model });
      if (info.replaces) rows.push({ label: "会替换", text: "基础层里的「" + info.replaces + "」" });
      return { title: "接进基础层：" + info.name, rows: rows, needsKey: true, confirmText: "检测", warn: "",
               keyAccount: (info.block && info.block.key_account) || "" };
    }
    return { title: b.title, rows: ChatCore.cardInfoRows(b), needsKey: false, confirmText: "确认" };
  }

  /** 点过确认的卡的标题。有结果（view 是 jobView 的返回）就照结果说：没通过不能写“已确认接入” */
  function blockDoneTitle(b, view) {
    if (b.state === "confirmed") {
        var st = view && view.state;
        if (b.proposalKind === "install" || b.proposalKind === "web_install") {
          var head = { running: "正在安装：", fail: "没装成：", ok: "已装好：" }[st] || "已确认安装：";
          return head + b.info.label;
        }
        if (b.proposalKind === "connect" || b.proposalKind === "connect_api" || b.proposalKind === "connect_search") {
          var head2 = { running: "正在检测：", fail: "没接上：", ok: "已接入：", warn: "已接入：",
                        lost: "已点确认，结果没留下来：" }[st] || "已确认接入：";
          if (st === "ok" && view.canSave) head2 = "检测通过，还没放进基础层：";
          if (st === "warn" && view.canSave) head2 = view.head || "能连上但额度不足，还没放进基础层：";
          return head2 + b.info.name;
        }
    }
    return { cancelled: "好的，先不做。", replaced: "已换成新的提议" }[b.state] || b.title;
  }

  function jobView(job) {
    if (!job) return { state: "lost", text: "这次的结果没留下来（超级助手重开过）。可以在左边基础层里看，或者跟管家说重新提议", lines: [], canSave: false };
    if (!job.done) return { state: "running", text: job.kind === "install" ? "正在安装…" : "正在检测…", lines: job.kind === "install" ? (job.tail || []) : [], canSave: false };
    if (job.error) return { state: "fail", text: job.error, lines: job.tail || [], canSave: false };
    var res = job.result || {};
    if (job.kind !== "install" && res.status === "quota") {  // 只是额度不足：算接上了，可以放进基础层
      var short = "能连上，但额度不足：" + (res.reason || "") + (res.how ? "。" + res.how : "");
      return { state: "warn", text: job.saved ? short + " · 已放进基础层" : short + "。可以先放进基础层", lines: [], canSave: !job.saved };
    }
    if (job.kind !== "install" && res.status === "listed") {  // 拿到模型名单、试的模型没通：也算接上（用户 2026-11-11 选 A）
      var said = String(res.reason || "").replace(/^已接上：/, "");
      return { state: "warn", text: job.saved ? said + " · 已放进基础层" : said + "。可以先放进基础层", lines: [], canSave: !job.saved,
               head: "已接上（试的模型没通），还没放进基础层：" };
    }
    if (!res.ok) {
      var why = res.reason ? res.reason + "。" + (res.how || "") : (res.how || "没成功，原因没查出来，跟管家说一声");
      return { state: "fail", text: why, lines: job.tail || [], canSave: false };
    }
    if (job.kind === "install") return { state: "ok", text: res.how || (res.reason + "。跟管家说一声，它会接着帮你接进基础层"), lines: job.tail || [], canSave: false };
    return { state: "ok", text: (job.saved ? "检测通过 · " + res.seconds + " 秒 · 已放进基础层" : "检测通过 · " + res.seconds + " 秒"), lines: [], canSave: !job.saved };
  }

  return { esc: esc, md: md, Model: Model, actsSummary: actsSummary, actLine: actLine, usageText: usageText, modelFrom: modelFrom, needsOpen: needsOpen, cardInfoRows: cardInfoRows, cardNowLine: cardNowLine, afterThreadDeleted: afterThreadDeleted, isBlockKind: isBlockKind, keyProblem: keyProblem, blockCard: blockCard, blockDoneTitle: blockDoneTitle, jobView: jobView };
})();

if (typeof module !== "undefined") module.exports = ChatCore;

/* ---------------- 页面层（只在窗口里运行） ---------------- */
if (typeof document !== "undefined") (function () {
  "use strict";
  var params = new URLSearchParams(location.search);
  var project = params.get("project") || "", dispatcher = params.get("dispatcher") || "", token = params.get("token") || "";
  var names = { codex: "Codex", claude: "Claude" }, faces = { codex: "Cx", claude: "Cl" };
  var clerkMode = params.get("clerk") === "1";   // 聊天找行政人员：走 API，不连 Codex
  var butlerMode = clerkMode && params.get("butler") === "1";  // 积木库窗口里：同一套聊天，名字叫积木库管家
  var model = new ChatCore.Model(), drawn = [];
  var $ = function (id) { return document.getElementById(id); };
  var list = $("msgs");

  $("who").textContent = names[dispatcher] || dispatcher;
  $("whoFace").textContent = faces[dispatcher] || "调";

  function api(path, body) {
    return fetch(path, {
      method: "POST", headers: { "Content-Type": "application/json", "X-Chat-Token": encodeURIComponent(token) },
      body: JSON.stringify(Object.assign({ project: project }, body || {}))
    }).then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || "出错了"); return d; }); });
  }

  function imageUrl(path) {
    return "/api/chat/image?" + new URLSearchParams({ project: project, path: path, token: token }).toString();
  }

  var proposalJobs = {};
  var proposalKeys = {};
  var proposalSecrets = {};  // 提议编号 -> {钥匙串账号: 密钥}：放进基础层后逐把交给 Mac 窗口，只在内存里
  var confirming = {};  // 确认请求还没回话的提议：这时服务器还没登记任务，不按提议去查
  // 60 号单：钥匙串里有这一家的密钥时，提议显示“已保存的密钥 / 换一把”
  var savedAccounts = null, savedLoading = false, keyWanted = {}, usedSavedKey = {};
  var SAVED_FAILED = "存的这把没通过，可以换一把：跟管家说重新提议，在新的提议上点“换一把”填新的";

  function loadSavedAccounts() {
    if (savedLoading) return;
    savedLoading = true;
    fetch("/api/blocks/state?" + new URLSearchParams({ token: token }).toString())
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        savedLoading = false;
        savedAccounts = (d && d.saved_accounts) || [];
        model.blocks.forEach(function (b) {
          if (b.kind === "proposal" && ["connect_api", "connect_search", "connect"].indexOf(b.proposalKind) >= 0 && b.state === "open") model._touch(b);
        });
        render();
      })
      .catch(function () { savedLoading = false; savedAccounts = savedAccounts || []; });
  }

  /** 积木库页面在 Mac 窗口交完钥匙串后调这里 */
  window.chatKeysChanged = loadSavedAccounts;

  function findProposal(pid) {
    for (var i = 0; i < model.blocks.length; i++) {
      if (model.blocks[i].kind === "proposal" && model.blocks[i].id === pid) return model.blocks[i];
    }
    return null;
  }

  function touchProposal(pid) {
    for (var i = 0; i < model.blocks.length; i++) {
      if (model.blocks[i].kind === "proposal" && model.blocks[i].id === pid) {
        model._touch(model.blocks[i]);
      }
    }
    render();
  }

  function startJobPoll(pid, jobId) {
    var rec = proposalJobs[pid] || (proposalJobs[pid] = { job: jobId, view: null, saved: false, polling: false });
    rec.job = jobId;
    if (rec.polling) return;
    rec.polling = true;  // 请求发出前就占住：同一个任务只跑一套
    function tick() {
      fetch("/api/blocks/job?" + new URLSearchParams({ id: jobId, token: token }).toString())
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) {
          if (!d) {
            rec.view = ChatCore.jobView(null);
            rec.polling = false;
            delete proposalKeys[pid];
            touchProposal(pid);
            return;
          }
          rec.saved = !!d.saved;
          rec.view = ChatCore.jobView(d);
          // 额度不足（warn）时手填的密钥要留着：放进基础层时交给 Mac 窗口存进钥匙串（第四轮复查）
          if (d.done && rec.view.state !== "ok" && rec.view.state !== "warn") delete proposalKeys[pid];
          if (d.done && rec.view.state === "fail" && usedSavedKey[pid] && ChatCore.keyProblem(d.result && d.result.reason) &&
              rec.view.text.indexOf(SAVED_FAILED) === -1) {
            rec.view.text = String(rec.view.text || "").replace(/[。.\s]+$/, "") + "。" + SAVED_FAILED;
          }
          touchProposal(pid);
          if (!d.done) {
            setTimeout(tick, 1000);
          } else {
            rec.polling = false;
          }
        })
        .catch(function () {
          setTimeout(tick, 2000);  // 网络断一下：过一会儿再问，不停在“正在安装…”
        });
    }
    tick();
  }

  function ensureProposalJob(pid) {
    if (proposalJobs[pid] || confirming[pid]) return;
    var rec = proposalJobs[pid] = { job: "", view: null, saved: false, polling: false };
    fetch("/api/blocks/job?" + new URLSearchParams({ proposal: pid, token: token }).toString())
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (rec.polling) return;  // 确认的回话已经开始跟进了
        if (!d) {
          rec.view = ChatCore.jobView(null);
          touchProposal(pid);
          return;
        }
        rec.job = d.job || "";
        rec.saved = !!d.saved;
        rec.view = ChatCore.jobView(d);
        touchProposal(pid);
        if (!d.done && rec.job) {
          startJobPoll(pid, rec.job);
        }
      })
      .catch(function () {
        if (rec.polling) return;
        rec.view = { state: "lost", text: "没连上本机服务，过一会儿再试…", lines: [], canSave: false };
        touchProposal(pid);
        setTimeout(function () {  // 连不上不等于结果没了：过一会儿重新问一次
          if (proposalJobs[pid] === rec && !rec.polling) {
            delete proposalJobs[pid];
            touchProposal(pid);
          }
        }, 3000);
      });
  }

  /* ---------------- 能力卡片（草稿）：试做、放进能力层（69b） ---------------- */
  var ABILITY_JOB_MS = 1500;
  var ABILITY_RETRY_MS = 3000;
  var ABILITY_OFFLINE = "没连上本机服务，过一会儿再试…";
  var ABILITY_LOST = "这次试做的结果没留下来（超级助手重开过）。跟管家说再出一次草稿。";
  var abilityJobs = {};   // 提议编号 -> 这次试做的跟进情况；同一个草稿同时只有一个查询在路上

  function abilityRec(pid) {
    return abilityJobs[pid] || (abilityJobs[pid] = {
      job: "", result: null, saved: false, asking: false, timer: null, file: false,
      confirming: false, cancelling: false, saving: false, inputs: null,
      error: "", saveError: "", started: 0, lost: false, restoreError: "",
    });
  }

  function abilityStage(draft, rec) {
    var state = draft.state || "open";
    if (state === "cancelled" || state === "voided" || state === "replaced" || state === "confirmed") return state;
    if (rec && (rec.confirming || rec.started || rec.result || rec.lost || rec.restoreError)) return "confirmed";
    return "open";
  }

  function abilityFields(ability) {
    var fields = AbilitiesCore.formFields(ability && ability.inputSchema);
    var props = (ability && ability.inputSchema && ability.inputSchema.properties) || {};
    fields.forEach(function (field) {
      var examples = (props[field.name] || {}).examples;
      if (Array.isArray(examples) && examples.length) field.value = examples[0];   // 例子优先于默认值
    });
    return fields;
  }

  function abilityJobUrl(pid) {
    return "/api/blocks/job?" + new URLSearchParams({ proposal: pid, token: token }).toString();
  }

  function abilityRestorable(trial) {
    return !!(trial && trial.ok && trial.run_id);
  }

  function abilityTrialWhole(trial) {
    // 事件里的 trial 只是摘要：成了的那次得有正文或成果文件名，才画得出完整结果
    return !!trial && (!trial.ok || (trial.output !== undefined && trial.output !== null));
  }

  function abilityBusy(rec) {
    return !!(rec.asking || rec.timer);   // 查询在路上、或正在等下一次问：都不许再发
  }

  function abilityQueryWanted(pid, rec) {
    // 拿到完整成果、或服务明确回过“没有”，就不用再问；连不上不算没了，接着问
    if (rec.confirming || rec.result || rec.lost || rec.restoreError) return false;
    var draft = findProposal(pid);
    if (!draft || abilityStage(draft, rec) !== "confirmed") return false;
    return !(draft.trial && !draft.trial.ok);   // 没成的试做：摘要里的原因已经够用
  }

  function abilityFileName(ability) {
    var outType = (ability && ability.output && ability.output.type) || "";
    return outType === "text" ? "结果.txt" : outType === "list" ? "结果.json" :
      String((ability && ability.how && ability.how.output_file) || "");
  }

  function abilityFileUrl(ability, trial, name) {
    return "/api/abilities/file?" + new URLSearchParams({
      token: token, id: (ability && ability.id) || "", run: (trial && trial.run_id) || "", name: name,
    }).toString();
  }

  // 服务明确说任务没了：摘要里成了的那次转去按地址取成果文件，取不回来的才算结果没留下来
  function abilityJobGone(rec, draft) {
    var ability = draft.ability || {}, trial = draft.trial || {};
    var name = abilityFileName(ability);
    if (!abilityRestorable(trial) || !name || !ability.id) {
      rec.lost = true;
      return false;
    }
    rec.file = true;   // 文字、列表、图片、文件都经统一查询取回，不再凭摘要直接当成功
    return true;
  }

  // 草稿重开后的查进度、取成果文件、接着轮询都走这一条：同一个草稿同时只有一个查询在路上
  function abilityQuery(pid, wait) {
    var rec = abilityRec(pid);
    if (abilityBusy(rec)) return;   // 查询在路上、或已排了重试：都不许再发
    if (wait) {
      rec.next = Date.now() + wait;   // 记住最早能再问的时刻：切到别的对话也留着，切回来不会立刻重查
      rec.timer = setTimeout(function () { rec.timer = null; abilityQuery(pid, 0); }, wait);
      return;
    }
    if (rec.next && Date.now() < rec.next) {   // 还在等重试：重画只能排剩余时间，不能立刻问
      abilityQuery(pid, rec.next - Date.now());
      return;
    }
    if (!abilityQueryWanted(pid, rec)) return;
    var draft = findProposal(pid);   // 发出前就记下这张草稿：回话晚到、人已切走，也按它恢复
    var ability = draft.ability || {}, trial = draft.trial || {};
    var name = abilityFileName(ability);
    var outType = (ability.output && ability.output.type) || "";
    var asList = outType === "list";
    var binary = outType === "image" || outType === "file";
    rec.asking = true;   // 请求发出前就占住：重画插不进来
    if (!rec.started) rec.started = Date.now();
    fetch(rec.file ? abilityFileUrl(ability, trial, name) : abilityJobUrl(pid))
      .then(function (r) {
        if (r.status === 404) return null;   // 只有服务明确回“没有”，才算任务或文件没了
        if (!r.ok) throw new Error("查询暂时出错：" + r.status);
        if (rec.file && binary) return name;   // 图片、文件只确认还在，正文仍按地址显示
        return rec.file && !asList ? r.text() : r.json();
      })
      .then(function (data) {
        rec.asking = false;
        rec.error = "";
        if (rec.file) {
          if (data === null) rec.restoreError = ABILITY_LOST;
          else rec.result = Object.assign({}, trial, { output: data, files: [name] });
          touchProposal(pid);
          return;
        }
        if (data) {
          rec.job = data.job || rec.job;
          rec.saved = !!data.saved;
          if (!data.done) {
            abilityQuery(pid, ABILITY_JOB_MS);   // 先占住下一次，再重画，免得画一次又多问一遍
            touchProposal(pid);
            return;
          }
          rec.result = data.error ? { ok: false, status: "error", reason: data.error } : (data.result || null);
        }
        if (!rec.result && abilityJobGone(rec, draft)) abilityQuery(pid, 0);   // 任务没了：接着取上次的成果文件
        touchProposal(pid);
      })
      .catch(function () {
        rec.asking = false;
        rec.error = ABILITY_OFFLINE;   // 连不上、服务出错都只算暂时连不上，不当成结果没了
        abilityQuery(pid, ABILITY_RETRY_MS);   // 先排好 3 秒后重试，再重画
        touchProposal(pid);
      });
  }

  function submitAbilityTrial(pid, box) {
    var draft = findProposal(pid);
    if (!draft) return;
    var rec = abilityRec(pid);
    if (rec.confirming || abilityBusy(rec)) return;
    var raw = {};
    box.querySelectorAll("[data-name]").forEach(function (control) {
      raw[control.getAttribute("data-name")] = control.value;
    });
    var got = AbilitiesCore.collectInputs(abilityFields(draft.ability), raw);
    rec.inputs = raw;
    if (!got.ok) {
      rec.error = got.error;
      touchProposal(pid);
      return;
    }
    rec.error = "";
    rec.confirming = true;
    touchProposal(pid);
    api("/api/clerk/proposal", { conversation: currentThread || "", id: pid, decision: "confirm", inputs: got.inputs })
      .then(function (data) {
        rec.confirming = false;
        rec.job = (data && data.job) || "";
        rec.started = Date.now();
        abilityQuery(pid, 0);
        touchProposal(pid);
      })
      .catch(function (err) {
        rec.confirming = false;
        rec.error = err.message;   // 例子不对、正在用：原因留在框里，改了还能再点
        touchProposal(pid);
      });
  }

  function cancelAbilityProposal(pid) {
    var rec = abilityRec(pid);
    if (rec.cancelling) return;
    rec.cancelling = true;
    api("/api/clerk/proposal", { conversation: currentThread || "", id: pid, decision: "cancel" })
      .then(function (data) {
        rec.cancelling = false;
        var draft = findProposal(pid);
        if (draft) {
          draft.state = (data && data.state) || "cancelled";
          model._touch(draft);
        }
        render();
      })
      .catch(function (err) {
        rec.cancelling = false;
        rec.error = err.message;
        touchProposal(pid);
      });
  }

  function refreshAbilityList() {
    try {
      var page = window.parent && window.parent.AbilitiesPage;
      if (page && typeof page.refresh === "function") page.refresh();
    } catch (_) {}
  }

  function saveAbilityDraft(pid) {
    var rec = abilityRec(pid);
    if (rec.saving || rec.saved) return;
    rec.saving = true;
    rec.saveError = "";
    touchProposal(pid);
    api("/api/abilities/save", { proposal: pid })
      .then(function () {
        rec.saving = false;
        rec.saved = true;
        touchProposal(pid);
        refreshAbilityList();
      })
      .catch(function (err) {
        rec.saving = false;
        rec.saveError = err.message;
        touchProposal(pid);
      });
  }

  function renderAbilityResult(box, result, ability) {
    var view = AbilitiesCore.resultView(result, ability);
    if (result && result.status === "model") {
      view.tip = "草稿上不能换模型：跟管家说换个模型，它会重新出草稿。";   // 模型下拉框只在能力层页面上
    }
    var head = document.createElement("p");
    head.className = "prop-result " + view.tone;
    head.textContent = view.head;
    box.appendChild(head);
    if (view.said || view.tip) {
      var note = document.createElement("p");
      note.className = "ability-note";
      note.textContent = view.said || view.tip;
      box.appendChild(note);
    }
    if (!view.body) return;
    var body = document.createElement("div");
    body.className = "ability-output";
    if (view.body.type === "text") {
      var pre = document.createElement("pre");
      pre.className = "ability-text";
      pre.textContent = view.body.text;
      body.appendChild(pre);
    } else if (view.body.type === "list") {
      var listEl = document.createElement("ul");
      listEl.className = "ability-list";
      (view.body.items || []).forEach(function (item) {
        var li = document.createElement("li");
        var link = document.createElement("a");
        link.href = item.url || "#";
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = item.title || item.url || "无标题";
        li.appendChild(link);
        if (item.summary || item.snippet) {
          var summary = document.createElement("p");
          summary.textContent = item.summary || item.snippet;
          li.appendChild(summary);
        }
        listEl.appendChild(li);
      });
      body.appendChild(listEl);
    } else {
      var query = new URLSearchParams({
        token: token, id: (ability && ability.id) || "", run: result.run_id || "", name: view.body.name,
      });
      var url = "/api/abilities/file?" + query.toString();
      if (view.body.type === "image") {
        var img = document.createElement("img");
        img.src = url;
        img.alt = view.body.name;
        img.className = "ability-image";
        body.appendChild(img);
      } else {
        var fileLink = document.createElement("a");
        fileLink.href = url;
        fileLink.target = "_blank";
        fileLink.rel = "noopener";
        fileLink.textContent = "成果文件：" + view.body.name;
        body.appendChild(fileLink);
      }
    }
    box.appendChild(body);
    if (result.files && result.files.length && result.run_id) {
      var actions = document.createElement("div");
      actions.className = "btns";
      var reveal = document.createElement("button");
      reveal.type = "button";
      reveal.className = "btn";
      reveal.textContent = "在访达里打开";
      reveal.addEventListener("click", function () {
        api("/api/abilities/reveal", { id: (ability && ability.id) || "", run: result.run_id })
          .catch(function (err) { showError(err.message); });
      });
      actions.appendChild(reveal);
      box.appendChild(actions);
    }
  }

  function renderAbilityDraft(el, draft) {
    var ability = draft.ability || {};
    var rec = abilityJobs[draft.id] || null;
    var stage = abilityStage(draft, rec);
    var isSearch = !!(ability.how && ability.how.kind === "search");
    el.className = "ask proposal ability-draft" + (stage === "open" ? "" : (stage === "cancelled" ? " no" : " done"));
    el.innerHTML = "<h3></h3><p class=\"ability-desc\"></p><div class=\"rows\"></div>";
    el.querySelector("h3").textContent = "能力卡片（草稿）「" + (draft.name || ability.title || "") + "」";
    el.querySelector(".ability-desc").textContent = ability.description || "";
    var rows = el.querySelector(".rows");
    function row(label, value) {
      var wrap = document.createElement("div");
      var name = document.createElement("span");
      name.textContent = label;
      var text = document.createElement("p");
      text.textContent = value;
      wrap.appendChild(name);
      wrap.appendChild(text);
      rows.appendChild(wrap);
    }
    row("用哪个积木块", draft.block_name || ability.block || "");
    var models = ability.model || "";
    if (isSearch) models = "不涉及";
    else if (ability.backup_models && ability.backup_models.length) models += "（备用：" + ability.backup_models.join("、") + "）";
    row("用哪个模型", models);
    row("交回什么", AbilitiesCore.outputText(ability.output && ability.output.type));
    if (draft.replaces) {
      var replace = document.createElement("p");
      replace.className = "ability-replace";
      replace.textContent = "放进能力层后会替换原来的「" + draft.replaces + "」";
      rows.after(replace);
    }
    if (draft.warns && draft.warns.length) {
      var warns = document.createElement("ul");
      warns.className = "ability-warns";
      draft.warns.forEach(function (warning) {
        var li = document.createElement("li");
        li.textContent = warning;
        warns.appendChild(li);
      });
      el.appendChild(warns);
    }
    var prompt = isSearch ? ((ability.how && ability.how.query) || "") : AbilitiesCore.promptText(ability);
    if (prompt) {
      var details = document.createElement("details");
      details.className = "ability-prompt";
      details.open = true;
      var summary = document.createElement("summary");
      summary.textContent = isSearch ? "搜什么" : "交代原文";
      var pre = document.createElement("pre");
      pre.textContent = prompt;
      details.appendChild(summary);
      details.appendChild(pre);
      el.appendChild(details);
    }
    if (stage === "open") {
      var form = document.createElement("div");
      form.className = "ability-form";
      abilityFields(ability).forEach(function (field) {
        var wrap = document.createElement("div");
        wrap.className = "field";
        var label = document.createElement("label");
        var inputId = "abilityInput_" + draft.id + "_" + field.name;
        label.setAttribute("for", inputId);
        label.textContent = field.title + (field.required ? " *" : "");
        var value = rec && rec.inputs && rec.inputs[field.name] !== undefined ? rec.inputs[field.name] : field.value;
        var control;
        if (field.kind === "select") {
          control = document.createElement("select");
          (field.options || []).forEach(function (option) {
            var opt = document.createElement("option");
            opt.value = option;
            opt.textContent = option;
            if (String(option) === String(value)) opt.selected = true;
            control.appendChild(opt);
          });
        } else if (field.kind === "textarea") {
          control = document.createElement("textarea");
          control.value = value === undefined || value === null ? "" : String(value);
          control.placeholder = field.placeholder || "";
        } else {
          control = document.createElement("input");
          control.type = field.kind === "number" ? "number" : "text";
          control.value = value === undefined || value === null ? "" : String(value);
          control.placeholder = field.placeholder || "";
        }
        control.id = inputId;
        control.setAttribute("data-name", field.name);
        wrap.appendChild(label);
        wrap.appendChild(control);
        form.appendChild(wrap);
      });
      el.appendChild(form);
      var error = document.createElement("p");
      error.className = "prop-result fail";
      error.textContent = (rec && rec.error) || "";
      error.hidden = !(rec && rec.error);
      el.appendChild(error);
      var btns = document.createElement("div");
      btns.className = "btns";
      var trialBtn = document.createElement("button");
      trialBtn.type = "button";
      trialBtn.className = "btn primary";
      trialBtn.setAttribute("data-ability-act", "trial");
      trialBtn.textContent = "试做";
      var cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "btn";
      cancelBtn.setAttribute("data-ability-act", "cancel");
      cancelBtn.textContent = "先不做";
      btns.appendChild(trialBtn);
      btns.appendChild(cancelBtn);
      el.appendChild(btns);
      return;
    }
    if (stage === "cancelled" || stage === "voided" || stage === "replaced") {
      var ended = document.createElement("p");
      ended.className = "prop-result";
      ended.textContent = stage === "cancelled" ? "先不做了" : stage === "replaced" ? "已换成新的草稿" : "已失效";
      el.appendChild(ended);
      return;
    }
    abilityQuery(draft.id, 0);
    rec = abilityJobs[draft.id];
    var result = rec.result || (!rec.lost && !rec.restoreError && abilityTrialWhole(draft.trial) ? draft.trial : null);
    if (!result) {
      var running = document.createElement("p");
      if (rec.restoreError) {
        running.className = "prop-result lost";
        running.textContent = rec.restoreError;
      } else if ((rec.file && rec.asking) || abilityRestorable(draft.trial)) {
        running.className = "prop-result running";
        running.textContent = "正在取回上次的试做结果…";
      } else if (rec.lost) {
        running.className = "prop-result lost";
        running.textContent = ABILITY_LOST;
      } else {
        var spent = Math.round((Date.now() - (rec.started || Date.now())) / 1000);
        running.className = "prop-result running";
        running.textContent = "正在试做… " + (spent < 1 ? "用时不到 1 秒" : "已用 " + spent + " 秒");
      }
      el.appendChild(running);
      if (rec.error) {
        var offline = document.createElement("p");
        offline.className = "prop-result fail";
        offline.textContent = rec.error;
        el.appendChild(offline);
      }
      return;
    }
    renderAbilityResult(el, result, ability);
    if (rec.restoreError) {
      var restoreNote = document.createElement("p");
      restoreNote.className = "prop-result fail";
      restoreNote.textContent = rec.restoreError;
      el.appendChild(restoreNote);
    }
    if (!result.ok) {
      var guide = document.createElement("p");
      guide.className = "ability-note";
      guide.textContent = "跟管家说哪里不对，它会改了重新出草稿";
      el.appendChild(guide);
      return;
    }
    if (draft.saved || rec.saved) {
      var saved = document.createElement("p");
      saved.className = "prop-result ok";
      saved.textContent = "已放进能力层";
      el.appendChild(saved);
    } else {
      var saveBtns = document.createElement("div");
      saveBtns.className = "btns";
      var saveBtn = document.createElement("button");
      saveBtn.type = "button";
      saveBtn.className = "btn primary";
      saveBtn.setAttribute("data-ability-save", "1");
      saveBtn.textContent = rec.saving ? "放进中…" : "放进能力层";
      saveBtn.disabled = !!rec.saving;
      saveBtns.appendChild(saveBtn);
      el.appendChild(saveBtns);
    }
    if (rec.saveError) {
      var saveError = document.createElement("p");
      saveError.className = "prop-result fail";
      saveError.textContent = rec.saveError;
      el.appendChild(saveError);
    }
  }

  function passKeyToWindow(account, key) {
    if (!account || !key) return;
    var handler = null;
    try {
      if (window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.blocksKey) {
        handler = window.webkit.messageHandlers.blocksKey;
      } else if (window.parent && window.parent !== window && window.parent.webkit && window.parent.webkit.messageHandlers && window.parent.webkit.messageHandlers.blocksKey) {
        handler = window.parent.webkit.messageHandlers.blocksKey;
      }
      if (handler) handler.postMessage({ account: account, key: key });
    } catch (_) {}
  }

  function renderBlock(b, idx) {
    var el = document.createElement("div");
    var face = butlerMode ? "管" : (clerkMode ? "行" : (faces[dispatcher] || "调"));
    if (b.kind === "sys") {
      el.className = "sys";
      el.innerHTML = "超级助手自动交代<button type=\"button\" data-sys>看全文</button><pre hidden></pre>";
      el.querySelector("pre").textContent = b.text;
    } else if (b.kind === "user") {
      el.className = "row me";
      el.innerHTML = "<span class=\"av\">你</span><div class=\"bubble\"></div>";
      el.querySelector(".bubble").textContent = b.text;
    } else if (b.kind === "ai") {
      el.className = "row ai";
      var html = "<span class=\"av\">" + ChatCore.esc(face) + "</span><div class=\"stack\">";
      b.parts.forEach(function (p, pi) {
        if (p.type === "text") {
          html += "<div class=\"bubble\">" + ChatCore.md(p.text) + (p.done || !model.busy ? "" : "<span class=\"caret\"></span>") + "</div>";
        } else if (p.type === "acts") {
          html += "<div class=\"acts\"><button type=\"button\" data-acts=\"" + idx + ":" + pi + "\" aria-expanded=\"" + p.open + "\">" +
            ChatCore.esc(ChatCore.actsSummary(p.list)) + "</button>" + (p.open ? "<ul>" + p.list.map(function (a) {
              return "<li class=\"" + ChatCore.esc(a.status) + "\" title=\"" + ChatCore.esc(a.detail) + "\">" + ChatCore.esc(ChatCore.actLine(a)) + "</li>";
            }).join("") + "</ul>" : "") + "</div>";
        } else if (p.type === "image") {
          html += "<figure class=\"img\"><a href=\"" + ChatCore.esc(imageUrl(p.path)) + "\" target=\"_blank\"><img alt=\"" +
            ChatCore.esc(p.prompt || "调度员画的图") + "\" src=\"" + ChatCore.esc(imageUrl(p.path)) + "\"></a><figcaption>" +
            ChatCore.esc(names[dispatcher] || "调度员") + " 画的图 · 已存在你电脑上</figcaption></figure>";
        }
      });
      el.innerHTML = html + "</div>";
    } else if (b.kind === "ask") {
      var titles = { command: "调度员想运行一条命令，允许吗？", file: "调度员想改文件，允许吗？", permissions: "调度员想要更多权限，允许吗？" };
      var done = { accept: "已允许", acceptForSession: "已允许（这次对话里同类不再问）", decline: "你拒绝了" }[b.state];
      el.className = "ask" + (b.state === "open" ? "" : (b.state === "decline" ? " no" : " done"));
      el.innerHTML = "<h3></h3>" + (b.command ? "<code></code>" : "") + (b.reason ? "<p></p>" : "") +
        (b.state === "open" ? "<div class=\"btns\"><button class=\"btn primary\" data-ans=\"accept\">允许</button>" +
          "<button class=\"btn\" data-ans=\"acceptForSession\">这次对话都允许</button><button class=\"btn\" data-ans=\"decline\">拒绝</button></div>" : "");
      el.querySelector("h3").textContent = done || titles[b.what] || "调度员想做一件事，允许吗？";
      if (b.command) el.querySelector("code").textContent = b.command;
      if (b.reason) el.querySelector("p").textContent = "理由：" + b.reason;
      el.setAttribute("data-ask", b.id);
    } else if (b.kind === "proposal") {
      function propRoleLabel(role) {
        return ({ dispatcher: "调度员", tester: "写测试", developer: "写代码", reviewer: "复查" })[role] || role;
      }
      function propToolLabel(tool) {
        if (!tool && tool !== "") return "";
        if (String(tool).indexOf("custom:") === 0) return "自定义命令";
        return ({ codex: "Codex", cursor: "Cursor", agy: "agy", none: "不复查" })[tool] || tool;
      }
      el.className = "ask proposal" + (b.state === "open" ? "" : (b.state === "cancelled" ? " no" : " done"));
      if (butlerMode && b.proposalKind === "ability") {
        renderAbilityDraft(el, b);
      } else if (ChatCore.isBlockKind(b.proposalKind)) {
        if (b.state === "open") {
          var bc = ChatCore.blockCard(b);
          el.innerHTML = "<h3></h3><div class=\"rows\"></div>" +
            "<div class=\"btns\"><button class=\"btn primary\" data-prop=\"confirm\"></button>" +
            "<button class=\"btn\" data-prop=\"cancel\">先不做</button></div>";
          el.querySelector("h3").textContent = bc.title || "";
          el.querySelector("[data-prop=\"confirm\"]").textContent = bc.confirmText || "确认";
          if (bc.warn) {
            var warnP = document.createElement("p");
            warnP.className = "prop-warn" + (bc.warnLevel === "caution" ? " caution" : "");
            warnP.textContent = bc.warn;
            el.querySelector("h3").after(warnP);
          }
          var rowsEl = el.querySelector(".rows");
          bc.rows.forEach(function (r) {
            var d = document.createElement("div");
            var sp = document.createElement("span");
            sp.textContent = r.label || "";
            var p = document.createElement("p");
            if (r.strong) {
              var st = document.createElement("strong");
              st.textContent = r.text || "";
              p.appendChild(st);
            } else if (r.danger) {
              p.className = "risk";
              p.textContent = "🔴 " + (r.text || "");
            } else if (r.code) {
              var cd = document.createElement("code");
              cd.textContent = r.text || "";
              p.appendChild(cd);
            } else if (r.label === "来源" && /^https:\/\//.test(String(r.text || ""))) {
              var a = document.createElement("a");
              a.href = String(r.text);
              a.target = "_blank";
              a.rel = "noopener";
              a.textContent = r.text;
              p.appendChild(a);
            } else {
              p.textContent = r.text || "";
            }
            d.appendChild(sp);
            d.appendChild(p);
            rowsEl.appendChild(d);
          });
          if (bc.needsKey && savedAccounts === null) loadSavedAccounts();
          var useSaved = bc.needsKey && !keyWanted[b.id] && !!bc.keyAccount && (savedAccounts || []).indexOf(bc.keyAccount) >= 0;
          if (useSaved) {
            var savedBox = document.createElement("div");
            savedBox.className = "prop-key";
            var savedRow = document.createElement("div");
            savedRow.className = "saved-key";
            var savedText = document.createElement("span");
            savedText.textContent = "已保存的密钥";
            var newBtn = document.createElement("button");
            newBtn.type = "button";
            newBtn.setAttribute("data-new-key", b.id);
            newBtn.textContent = "换一把";
            savedRow.appendChild(savedText);
            savedRow.appendChild(newBtn);
            savedBox.appendChild(savedRow);
            rowsEl.after(savedBox);
          } else if (bc.needsKey) {
            var keyBox = document.createElement("div");
            keyBox.className = "prop-key";
            var inp = document.createElement("input");
            inp.type = "password";
            inp.setAttribute("autocomplete", "off");
            inp.placeholder = bc.keyHint || "在这里填入 API 密钥";
            var tip = document.createElement("small");
            tip.textContent = "密钥只填在这里，别贴在聊天里";
            keyBox.appendChild(inp);
            keyBox.appendChild(tip);
            rowsEl.after(keyBox);
          }
          if (bc.secrets && bc.secrets.length) {  // 普通工具要的几把密钥：一把一格；钥匙串里存过的可以空着
            if (savedAccounts === null) loadSavedAccounts();
            var secBox = document.createElement("div");
            secBox.className = "prop-key prop-secrets";
            bc.secrets.forEach(function (sec) {
              var lab = document.createElement("small");
              var saved = (savedAccounts || []).indexOf(sec.account) >= 0;
              lab.textContent = sec.label + (saved ? "（已保存，可以空着）" : "");
              var si = document.createElement("input");
              si.type = "password";
              si.setAttribute("autocomplete", "off");
              si.setAttribute("data-account", sec.account);
              if (saved) si.setAttribute("data-saved", "1");
              si.placeholder = "在这里填入「" + sec.label + "」";
              secBox.appendChild(lab);
              secBox.appendChild(si);
            });
            var stip = document.createElement("small");
            stip.textContent = "密钥只填在这里，别贴在聊天里";
            secBox.appendChild(stip);
            (el.querySelector(".prop-key") || rowsEl).after(secBox);
          }
          var errP = document.createElement("p");
          errP.className = "prop-result fail";
          errP.hidden = true;
          el.querySelector(".btns").before(errP);
        } else if (b.state === "confirmed") {
          el.innerHTML = "<h3></h3>";
          ensureProposalJob(b.id);
          var rec = proposalJobs[b.id];
          var vw = rec && rec.view;
          el.querySelector("h3").textContent = ChatCore.blockDoneTitle(b, vw);
          if (vw && vw.state === "fail") el.classList.add("failed");
          if (vw && vw.state === "lost") el.classList.add("unknown");
          if (vw) {
            if (vw.lines && vw.lines.length) {
              var preOut = document.createElement("pre");
              preOut.className = "prop-output";
              preOut.textContent = vw.lines.slice(-50).join("\n");
              el.appendChild(preOut);
              // 用户往上翻着看的时候，新输出来了也不拉回最底下
              var oldPre = drawn[idx] && drawn[idx].querySelector ? drawn[idx].querySelector(".prop-output") : null;
              var nearEnd = !oldPre || oldPre.scrollHeight - oldPre.scrollTop - oldPre.clientHeight < 24;
              var keepTop = oldPre ? oldPre.scrollTop : 0;
              setTimeout(function () { preOut.scrollTop = nearEnd ? preOut.scrollHeight : keepTop; }, 0);
            }
            var resP = document.createElement("p");
            resP.className = "prop-result " + (vw.state || "");
            resP.textContent = vw.text || "";
            el.appendChild(resP);
            if (rec.keyMissing) {
              var missP = document.createElement("p");
              missP.className = "prop-warn";
              missP.textContent = "密钥没带上（页面刷新过）：这块暂时用不了。跟管家说重新提议，再填一次密钥";
              el.appendChild(missP);
            }
            if (rec.saveError) {
              var saveErrP = document.createElement("p");
              saveErrP.className = "prop-result fail";
              saveErrP.textContent = rec.saveError;
              el.appendChild(saveErrP);
            }
            if (vw.canSave) {
              var btns = document.createElement("div");
              btns.className = "btns";
              var saveBtn = document.createElement("button");
              saveBtn.className = "btn primary";
              saveBtn.setAttribute("data-save-job", rec.job || "");
              saveBtn.textContent = "放进基础层";
              btns.appendChild(saveBtn);
              el.appendChild(btns);
            }
          }
        } else {
          delete proposalKeys[b.id];
          el.innerHTML = "<h3></h3>";
          el.querySelector("h3").textContent = ChatCore.blockDoneTitle(b);
        }
      } else {
        var isTeam = b.proposalKind === "team", isProfile = b.proposalKind === "profile";
        var isRevise = b.proposalKind === "revise";
        var result = isRevise ? {
          confirmed: "已确认，开始修改。",
          cancelled: "好的，先不改。",
          replaced: "已换成新的说法",
        } : isProfile ? {
          confirmed: "项目说明已保存。",
          cancelled: "好的，先不保存。",
          replaced: "已换成新的提议",
        } : isTeam ? {
          confirmed: "团队设置已更新。",
          cancelled: "好的，先不改团队设置。",
          replaced: "已换成新的提议",
        } : {
          confirmed: b.card ? ("已生成任务卡片 " + b.card) : "已确认",
          cancelled: "好的，先不做。",
          replaced: "已换成新的提议",
        };
        result = result[b.state];
        if (b.state === "open") {
          el.innerHTML = "<h3></h3><div class=\"rows\"></div>" +
            "<div class=\"btns\"><button class=\"btn primary\" data-prop=\"confirm\">确认</button>" +
            "<button class=\"btn\" data-prop=\"cancel\">先不做</button></div>";
          if (isRevise) {
            el.querySelector("h3").textContent = "继续改？";
            var note = document.createElement("p");
            note.textContent = b.text || "";
            el.querySelector(".rows").appendChild(note);
          } else if (isProfile) {
            el.querySelector("h3").textContent = "保存项目说明？";
            var draft = document.createElement("div");
            draft.innerHTML = "<span>内容</span><p></p>";
            draft.querySelector("p").textContent = b.profile;
            draft.querySelector("p").style.whiteSpace = "pre-wrap";
            el.querySelector(".rows").appendChild(draft);
          } else if (isTeam) {
            el.querySelector("h3").textContent = "改团队设置？";
            var row = document.createElement("div");
            var beforeT = b.before === "" || b.before == null ? "还没选" : propToolLabel(b.before);
            row.innerHTML = "<span></span><p></p>";
            row.querySelector("span").textContent = propRoleLabel(b.role);
            row.querySelector("p").textContent = beforeT + " → " + propToolLabel(b.tool);
            el.querySelector(".rows").appendChild(row);
          } else {
            el.querySelector("h3").textContent = "变成任务？";
            el.querySelector(".rows").innerHTML =
              "<div><span>要做什么</span><p></p></div>" +
              "<div><span>不做什么</span><p></p></div>" +
              "<div><span>怎么算做完</span><p></p></div>";
            var ps = el.querySelectorAll(".rows p");
            ps[0].textContent = b.do; ps[1].textContent = b.not_do; ps[2].textContent = b.done_when;
            if (b.title) {
              var title = document.createElement("p");
              title.className = "prop-title";
              title.textContent = b.title;
              el.querySelector("h3").after(title);
            }
          }
        } else {
          el.innerHTML = "<h3></h3>";
          el.querySelector("h3").textContent = result || b.state;
        }
      }
      el.setAttribute("data-proposal", b.id);
    } else if (b.kind === "note") {
      el.className = "note " + (b.tone || "");
      el.textContent = b.text;
    } else if (b.kind === "divider") {
      el.className = "divider";
      el.textContent = b.text;
    }
    el.v = b.v;
    return el;
  }

  function render() {
    var nearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    model.blocks.forEach(function (b, i) {
      if (drawn[i] && drawn[i].v === b.v && !(b.kind === "ai" && i === model.blocks.length - 1)) return;
      var el = renderBlock(b, i);
      if (drawn[i]) {
        if (drawn[i].outerHTML === el.outerHTML) { drawn[i].v = b.v; return; }
        list.replaceChild(el, drawn[i]);
      } else {
        list.appendChild(el);
      }
      drawn[i] = el;
    });
    while (drawn.length > model.blocks.length) {
      list.removeChild(drawn.pop());
    }
    $("usage").textContent = ChatCore.usageText(model.usage).replace(/^这次对话用量\s*/, "");
    var st = model.status || "";
    var dot = $("connDot");
    if (dot) {
      dot.className = "conn-dot " + (st.indexOf("已连上") === 0 ? "ok" : (st.indexOf("未连上") === 0 ? "bad" : (st ? "wait" : "")));
    }
    $("status").textContent = st.indexOf("已连上") === 0 ? "" : st;
    $("working").hidden = !model.showWorking();
    $("send").textContent = model.busy ? "停止" : "↑";
    $("send").className = "btn send-btn " + (model.busy ? "stop" : "primary");
    if (nearBottom) list.scrollTop = list.scrollHeight;
  }

  list.addEventListener("click", function (e) {
    var t = e.target;
    var sys = t.closest("[data-sys]");
    if (sys) { var pre = sys.nextElementSibling; pre.hidden = !pre.hidden; sys.textContent = pre.hidden ? "看全文" : "收起"; return; }
    var acts = t.closest("[data-acts]");
    if (acts) {
      var ij = acts.getAttribute("data-acts").split(":"), part = model.blocks[+ij[0]].parts[+ij[1]];
      part.open = !part.open; model.blocks[+ij[0]].v++; render(); return;
    }
    var ans = t.closest("[data-ans]");
    if (ans) {
      var card = ans.closest("[data-ask]");
      card.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
      api("/api/chat/answer", { id: card.getAttribute("data-ask"), decision: ans.getAttribute("data-ans") })
        .catch(function (err) { showError(err.message); card.querySelectorAll("button").forEach(function (b) { b.disabled = false; }); });
      return;
    }
    var newKey = t.closest("[data-new-key]");
    if (newKey) {
      var nkPid = newKey.getAttribute("data-new-key");
      keyWanted[nkPid] = true;
      touchProposal(nkPid);
      var nkBox = document.querySelector('[data-proposal="' + nkPid.replace(/["\\]/g, "") + '"] .prop-key input');
      if (nkBox) nkBox.focus();
      return;
    }
    var abilityAct = t.closest("[data-ability-act]");
    if (abilityAct) {
      var abilityBox = abilityAct.closest("[data-proposal]");
      var abilityPid = abilityBox.getAttribute("data-proposal");
      abilityBox.querySelectorAll("button").forEach(function (btn) { btn.disabled = true; });
      if (abilityAct.getAttribute("data-ability-act") === "trial") submitAbilityTrial(abilityPid, abilityBox);
      else cancelAbilityProposal(abilityPid);
      return;
    }
    var abilitySave = t.closest("[data-ability-save]");
    if (abilitySave) {
      var saveBox = abilitySave.closest("[data-proposal]");
      saveAbilityDraft(saveBox.getAttribute("data-proposal"));
      return;
    }
    var prop = t.closest("[data-prop]");
    if (prop) {
      var box = prop.closest("[data-proposal]");
      var pid = box.getAttribute("data-proposal");
      var decision = prop.getAttribute("data-prop");
      box.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
      var body = {
        conversation: currentThread || "", id: pid,
        decision: decision,
      };
      var keyInp = box.querySelector(".prop-key:not(.prop-secrets) input");
      var secretInps = box.querySelectorAll(".prop-secrets input[data-account]");
      var secretVals = {};
      if (decision === "confirm" && secretInps.length) {
        var lacking = false;
        secretInps.forEach(function (si) {
          var v = si.value.trim();
          if (v) secretVals[si.getAttribute("data-account")] = v;
          else if (!si.getAttribute("data-saved")) lacking = true;
        });
        if (lacking) {
          var serr = box.querySelector(".prop-result.fail");
          serr.textContent = "请先把每个密钥框都填好";
          serr.hidden = false;
          box.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
          return;
        }
        body.secret_keys = secretVals;
      }
      var keyVal = "";
      usedSavedKey[pid] = decision === "confirm" && !keyInp && !!box.querySelector(".saved-key");
      if (decision === "confirm" && keyInp) {
        keyVal = keyInp.value.trim();
        if (!keyVal) {
          var err = box.querySelector(".prop-result.fail");
          err.textContent = "请先在密钥框里填好密钥";
          err.hidden = false;
          box.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
          return;
        }
        body.api_key = keyVal;
        var hint = box.querySelector(".prop-result.fail");
        if (hint) hint.hidden = true;
      } else if (decision !== "confirm") {
        delete proposalKeys[pid];
      }
      var blockKind = findProposal(pid) && ChatCore.isBlockKind(findProposal(pid).proposalKind);
      if (decision === "confirm" && blockKind) confirming[pid] = true;
      api("/api/clerk/proposal", body).then(function (d) {
        delete confirming[pid];
        if (keyInp) keyInp.value = "";
        secretInps.forEach(function (si) { si.value = ""; });
        if (keyVal) proposalKeys[pid] = keyVal;
        if (Object.keys(secretVals).length) proposalSecrets[pid] = secretVals;
        if (d && d.job) startJobPoll(pid, d.job);
        else if (blockKind && decision === "confirm") touchProposal(pid);  // 没带任务编号（比如点过一次）：按提议查一次
      }).catch(function (err) {
        delete confirming[pid];
        if (blockKind && decision === "confirm") touchProposal(pid);  // 服务器可能已经确认了：按提议查一次，免得卡片一直没结果
        showError(err.message);
        box.querySelectorAll("button").forEach(function (b) { b.disabled = false; });
      });
      return;
    }
    var save = t.closest("[data-save-job]");
    if (save) {
      save.disabled = true;
      save.textContent = "放进中…";
      var box = save.closest("[data-proposal]");
      var pid = box ? box.getAttribute("data-proposal") : "";
      api("/api/blocks/save", { job: save.getAttribute("data-save-job") }).then(function (d) {
        var key = proposalKeys[pid];
        if (d && d.key_account && key) passKeyToWindow(d.key_account, key);
        delete proposalKeys[pid];
        var secs = proposalSecrets[pid] || {};
        Object.keys(secs).forEach(function (acc) { passKeyToWindow(acc, secs[acc]); });  // 普通工具的几把密钥各存一条
        delete proposalSecrets[pid];
        var rec = proposalJobs[pid];
        if (rec) {
          rec.saved = true;
          rec.saveError = "";
          // 用存的那把、或钥匙串里本来就有这一家：不算没带上（页面重新加载后也认）
          rec.keyMissing = !!(d && d.key_account && !key && !usedSavedKey[pid] &&
                              (savedAccounts || []).indexOf(d.key_account) < 0);
          if (rec.view) {
            rec.view.canSave = false;
            rec.view.text = rec.view.text.replace("。可以先放进基础层", "");  // 额度不足的放进去后不再劝“可以先放进”
            if (rec.view.text.indexOf("已放进基础层") === -1) rec.view.text += " · 已放进基础层";
          }
          touchProposal(pid);
        }
      }).catch(function (err) {
        var rec = proposalJobs[pid];
        if (rec) {
          rec.saveError = err.message;  // 记下来，下次重画时还在；按钮跟着重画恢复
          touchProposal(pid);
        } else {
          showError(err.message);
          save.textContent = "放进基础层";
          save.disabled = false;
        }
      });
    }
  });

  function showError(text) { model.apply({ type: "error", text: text }); render(); }

  $("composer").addEventListener("submit", function (e) {
    e.preventDefault();
    if (model.busy) {
      api(clerkMode ? "/api/clerk/stop" : "/api/chat/stop", { conversation: currentThread }).catch(function (err) { showError(err.message); });
      return;
    }
    var box = $("input"), text = box.value.trim();
    if (!text) return;
    if (pendingModel) { $("status").textContent = "正在同步，稍等一下再发送"; return; }  // 话留在输入框里
    box.value = ""; resize();
    if (clerkMode) { clerkSend(text); return; }
    api("/api/chat/send", { text: text }).catch(function (err) { box.value = text; showError(err.message); });
  });
  $("input").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("composer").requestSubmit(); }
  });
  function resize() { var box = $("input"); box.style.height = "auto"; box.style.height = Math.min(box.scrollHeight, 160) + "px"; }
  $("input").addEventListener("input", resize);

  var failures = 0, stopped = false, pendingModel = null, pollCtrl = null, pollTimer = null, gen = 0;
  var threadCache = {}, currentThread = null;   // 读好的历史对话：点的时候直接显示
  var connectFailed = false;                    // 当前这段没连上，再点一次要重连
  var blankChat = false;                        // 新对话还没说话：Codex 还没存档，接不回来
  var viewingCard = false, mainThread = null;   // 正在看任务对话；回去时接着这段主对话
  var clerkPlaceholder = butlerMode ? "跟管家说…" : "跟行政说…";

  function pause() {
    stopped = true;
    gen++;
    if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
    if (pollCtrl) { try { pollCtrl.abort(); } catch (_) {} pollCtrl = null; }
    $("offline").hidden = true;
  }

  // 行政对话：内容都在本机，切换马上显示，不用连接
  function clerkOpen(id) {
    pause();
    stopped = false;
    failures = 0;
    if ((id || null) !== currentThread) $("input").value = "";   // 草稿不带到别的对话
    currentThread = id || null;
    if (!viewingCard) mainThread = currentThread;
    pendingModel = null;
    model = new ChatCore.Model();
    drawn = [];
    list.innerHTML = "";
    render();
    api("/api/clerk/focus", { conversation: id || "" }).catch(function () { /* 只影响左栏选中 */ });
    if (id) { var myGen = ++gen; poll(myGen); }
  }

  function clerkSend(text) {
    var fromBlank = !currentThread, mine = gen;   // 回应到之前用户可能已经切走了
    api("/api/clerk/send", { conversation: currentThread || "", text: text }).then(function (d) {
      if (!fromBlank || gen !== mine) return;
      currentThread = d.conversation;
      if (!viewingCard) mainThread = currentThread;
      api("/api/clerk/focus", { conversation: currentThread }).catch(function () {});
      var myGen = ++gen;
      poll(myGen);
    }).catch(function (err) {
      if (gen === mine) $("input").value = text;   // 已经切走就不放回去，免得发到别的对话
      showError(err.message);
    });
  }

  // 从终端或设置回来：和切换对话走同一条路，接着当前这段
  function resume() {
    if (clerkMode) { clerkOpen(currentThread); return; }
    if (blankChat) switchSession("fresh", false, null);
    else switchSession("continue", false, currentThread);
  }

  function switchSession(mode, withOpening, threadId) {
    pause();
    stopped = false;
    failures = 0;
    if (connectFailed) model.dropErrors();
    connectFailed = false;
    blankChat = false;
    if (currentThread && !pendingModel && model.blocks.length) threadCache[currentThread] = model;
    var cached = threadId ? threadCache[threadId] : null;
    currentThread = threadId || null;
    drawn = [];
    list.innerHTML = "";
    if (cached) {
      // 先显示读好的内容，连接在后台进行，连上后静默换成最新的
      model = cached;
      model.busy = false;
      model.status = "已连上 " + (names[dispatcher] || dispatcher);
      pendingModel = new ChatCore.Model();
    } else {
      model = new ChatCore.Model();
      model.status = "正在连接 " + (names[dispatcher] || dispatcher) + "…";
      pendingModel = null;
    }
    render();
    list.scrollTop = list.scrollHeight;
    var myGen = ++gen;
    api("/api/chat/open", { dispatcher: dispatcher, mode: mode, opening: withOpening, thread_id: threadId || "" })
      .catch(function (err) { if (myGen === gen) showError(err.message); })
      .then(function () { if (myGen === gen && !stopped) poll(myGen); });
  }

  var nowCard = "", nowBusy = false, nowOpen = 0;   // 卡片标题下那一行：正在看哪张卡、有没有请求还在路上
  function hideCard() {
    viewingCard = false;
    nowCard = "";
    var head = document.getElementById("cardHead");
    var box = document.getElementById("cardInfo");
    if (head) { head.hidden = true; head.textContent = ""; }
    if (box) { box.hidden = true; box.textContent = ""; }
    var nowLine = document.getElementById("cardNow");
    if (nowLine) { nowLine.hidden = true; nowLine.textContent = ""; }
    if (window.__cardPhaseTimer) clearInterval(window.__cardPhaseTimer);
  }

  window.SuperAgentChat = {
    pause: pause, resume: resume,
    isWorking: function () { return (butlerMode || !clerkMode) && !stopped && !!(model.busy || (!clerkMode && pendingModel && pendingModel.busy)); },
    startFresh: function () {
      if (clerkMode) { hideCard(); clerkOpen(null); } else switchSession("fresh", true, null);
    },
    startFromBlock: function (blockId, blockName) {
      if (!butlerMode) return;   // 只有积木库管家能提取能力
      hideCard();
      clerkOpen(null);           // 和“新对话”一样：先清空当前对话
      var mine = gen;
      api("/api/clerk/send", { conversation: "", text: "我想从「" + blockName + "」提取能力卡片", block: blockId })
        .then(function (d) {
          if (gen !== mine) return;
          currentThread = d.conversation;
          if (!viewingCard) mainThread = currentThread;
          api("/api/clerk/focus", { conversation: currentThread }).catch(function () {});
          var myGen = ++gen;
          poll(myGen);
        })
        .catch(function (err) {
          if (gen === mine) showError(err.message);   // 积木块不存在：报错，不留空对话
        });
    },
    threadDeleted: function (id) {
      var next = ChatCore.afterThreadDeleted(id, { clerk: clerkMode, current: currentThread, main: mainThread, viewingCard: viewingCard });
      if (next.clearMain) mainThread = null;
      if (next.open === "blank") clerkOpen(null);
      else if (next.open === "fresh") switchSession("fresh", false, null);   // 空白对话：不带开场白，不自动发话
    },
    openThread: function (id) {
      if (clerkMode) { if (id !== currentThread) clerkOpen(id); return; }
      if (ChatCore.needsOpen(id, currentThread, connectFailed)) switchSession("continue", false, id);
    },
    openCard: function (info) {
      info = info || {};
      var head = document.getElementById("cardHead");
      var box = document.getElementById("cardInfo");
      if (head) {
        head.hidden = false;
        head.textContent = (info.title || "") + (info.phase && info.phase.label ? " · " + info.phase.label : "");
      }
      if (box) {
        box.hidden = false;
        box.textContent = "";
        ChatCore.cardInfoRows(info).forEach(function (row) {
          var line = document.createElement("p");
          line.textContent = row.label + "：" + row.text;
          box.appendChild(line);
        });
      }
      viewingCard = true;
      if (info.conversation) clerkOpen(info.conversation);
      var nowLine = document.getElementById("cardNow");
      if (!nowLine && head && head.parentNode) {
        nowLine = document.createElement("div");
        nowLine.id = "cardNow";
        head.parentNode.insertBefore(nowLine, head.nextSibling);
      }
      // 切卡时先清掉上一张卡的那一行；上一次还没回来就不再发；回来的不是当前这张卡的就丢掉（复查第 1 轮）
      nowCard = info.id || "";
      nowOpen += 1;   // 每打开一次换一个号：同一张卡重新打开，旧那次的结果也丢掉（复查第 2 轮）
      var myOpen = nowOpen;
      nowBusy = false;
      if (nowLine) { nowLine.hidden = true; nowLine.textContent = ""; }
      function showNow() {
        if (!nowLine || !info.id || nowBusy || myOpen !== nowOpen) return;
        var forCard = info.id;
        nowBusy = true;
        fetch("/api/view?project=" + encodeURIComponent(project) + "&card=" + encodeURIComponent(forCard))
          .then(function (r) { return r.json(); }).then(function (v) {
            if (forCard !== nowCard || myOpen !== nowOpen || !v || v.error || !viewingCard) return;
            var line = ChatCore.cardNowLine(v);
            nowLine.hidden = !line;
            nowLine.textContent = line;
            nowLine.className = "now-" + ((v.now && v.now.tone) || "idle");
          }).catch(function () {}).then(function () { if (myOpen === nowOpen) nowBusy = false; });
      }
      showNow();
      if (window.__cardPhaseTimer) clearInterval(window.__cardPhaseTimer);
      window.__cardPhaseTimer = setInterval(function () {
        showNow();
        if (!info.conversation) return;
        fetch("/api/cards?project=" + encodeURIComponent(project)).then(function (r) { return r.json(); }).then(function (cards) {
          if (!Array.isArray(cards) || !head) return;
          for (var i = 0; i < cards.length; i++) {
            if (cards[i].id === info.id) {
              var label = cards[i].phase && cards[i].phase.label;
              if (label) head.textContent = (info.title || "") + " · " + label;
            }
          }
        }).catch(function () {});
      }, 3000);
    },
    closeCard: function () {
      if (!viewingCard) return;
      hideCard();
      clerkOpen(mainThread);
    },
  };
  window.addEventListener("pagehide", pause);
  window.addEventListener("beforeunload", pause);

  // 连上之后，把最近几段对话的内容先读好
  function prefetchThreads() {
    var query = function (extra) { return new URLSearchParams(Object.assign({ project: project, token: token }, extra)).toString(); };
    fetch("/api/chat/threads?" + query({})).then(function (r) { return r.json(); }).then(function (d) {
      var wanted = (d.threads || []).filter(function (t) { return !t.current && !threadCache[t.id]; }).slice(0, 10);
      return wanted.reduce(function (done, t) {
        return done.then(function () {
          return fetch("/api/chat/thread?" + query({ id: t.id })).then(function (r) { return r.json(); }).then(function (x) {
            if (x.events && x.events.length && !threadCache[t.id]) threadCache[t.id] = ChatCore.modelFrom(x.events);
          });
        });
      }, Promise.resolve());
    }).catch(function () { /* 读不到就等点的时候再连 */ });
  }

  function poll(myGen) {
    if (stopped || myGen !== gen) return;
    var active = pendingModel || model;
    pollCtrl = typeof AbortController !== "undefined" ? new AbortController() : null;
    var feed = clerkMode ? "/api/clerk/events?" : "/api/chat/events?";
    fetch(feed + new URLSearchParams({ project: project, conversation: currentThread || "", since: active.seq, wait: 20, token: token }).toString(),
          pollCtrl ? { signal: pollCtrl.signal } : {})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (stopped || myGen !== gen) return;
        failures = 0; $("offline").hidden = true;
        if (d.error) throw new Error(d.error);
        d.events.forEach(function (ev) {
          if (ev.type === "status" && ev.thread_id) {
            currentThread = ev.thread_id;
            blankChat = !(pendingModel || model).blocks.length;
            setTimeout(prefetchThreads, 300);
          }
          if (ev.type === "user" || ev.type === "opening") blankChat = false;
          if (ev.type === "status" && ev.text && ev.text.indexOf("未连上") === 0) connectFailed = true;
          if (!pendingModel) { model.apply(ev); return; }
          pendingModel.apply(ev);
          if (ev.type === "status" && ev.text && ev.text.indexOf("正在等") === 0) {
            model.status = ev.text; $("status").textContent = model.status;
          } else if (ev.type === "status" && ev.text && ev.text.indexOf("已连上") === 0) {
            if (pendingModel.blocks.length > 0) {
              model = pendingModel;
              drawn.forEach(function (el) { if (el) el.v = 0; });
            } else {
              model.status = pendingModel.status;
              model.seq = pendingModel.seq;   // 接着新连接的编号取，不然旧事件会再来一遍
            }
            pendingModel = null;
          } else if (ev.type === "error") {
            model.status = pendingModel.status || ("未连上 " + (names[dispatcher] || dispatcher));
            model.apply({ type: "error", text: ev.text });
            model.seq = pendingModel.seq;
            pendingModel = null;
          }
        });
        if (!pendingModel) {
          if (typeof d.busy === "boolean") model.busy = d.busy;
          render();
        }
        if (!d.open) { stopped = true; return; }
      })
      .catch(function (err) {
        if (stopped || myGen !== gen || (err && err.name === "AbortError")) return;
        failures++; $("offline").hidden = false;
      })
      .then(function () {
        if (!stopped && myGen === gen) {
          pollTimer = setTimeout(function () { poll(myGen); }, failures ? Math.min(5000, 500 * failures) : 0);
        }
      });
  }

  if (!project || !token) {
    $("status").textContent = "请从超级助手窗口打开聊天";
  } else if (clerkMode) {
    $("input").placeholder = clerkPlaceholder;
    if (butlerMode) {
      $("whoFace").textContent = "管";
      $("who").textContent = "积木库管家";
      $("input").setAttribute("aria-label", "给积木库管家的消息");
      if ($("chatMain")) $("chatMain").setAttribute("aria-label", "和积木库管家的对话");
    } else {
      $("whoFace").textContent = "行";
      $("who").textContent = "行政";
      $("input").setAttribute("aria-label", "给行政的消息");  // 读屏软件念的名字跟着变
      if ($("chatMain")) $("chatMain").setAttribute("aria-label", "和行政的对话");
    }
    fetch("/api/clerk/list?" + new URLSearchParams({ project: project, token: token }).toString())
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var latest = d.threads && d.threads.length ? d.threads[0].id : null;
        if (viewingCard) mainThread = latest;   // 列表回来前已经开了卡，不能把卡片换掉
        else clerkOpen(latest);
      })
      .catch(function () { if (!viewingCard) clerkOpen(null); });
  } else {
    var initGen = ++gen;
    api("/api/chat/open", { dispatcher: dispatcher, mode: params.get("mode") || "continue",
                            opening: params.get("opening") !== "0" })
      .catch(function (err) { showError(err.message); })
      .then(function () { poll(initGen); });
  }
})();
