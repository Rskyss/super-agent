/* 积木库页面（上）：绑定页、左栏（聊天列表、基础层列表）、中间的管家聊天。
   分两层：BlocksCore（规则层，不碰页面，tests/blocks_page_check.js 用 node 直接检查）；页面层。
   密钥规矩：只在密钥框和那一次请求里出现，不存网页、不放地址、不打印。 */
"use strict";

var BlocksCore = {
  startView: function (state) {
    if (!state || !state.setting) return "bind";
    if (!state.connected) return "reconnect";
    return "main";
  },
  /** 钥匙串账号名：和本机服务 account_for、Mac 窗口同一套；“其他”按地址的主机名分开，地址不对给空 */
  accountFor: function (provider, baseUrl) {
    var p = String(provider || "").trim();
    if (p === "tencent" || p === "bailian") return p;
    if (p !== "other") return "";
    try {
      var u = new URL(String(baseUrl || "").trim());
      if ((u.protocol !== "https:" && u.protocol !== "http:") || !u.hostname) return "";
      return "other:" + u.hostname.toLowerCase();
    } catch (_) {
      return "";
    }
  },
  /** 没通过的原因是不是出在密钥本身（不对、失效、额度用完）：只有这时才劝“换一把”，模型名错、网断了不怪密钥 */
  keyProblem: function (reason) {
    return /密钥|额度/.test(String(reason || ""));
  },
  /** saved：钥匙串里已有密钥的账号；这一家有、密钥又没填，就用存的那把（请求里不带密钥） */
  /** “设置”按钮要不要带小红点：体检发现有工具要处理，或体检本身没查成 */
  settingsAlert: function (view, scanError) {
    return !!scanError || !!(view && view.state === "problem");
  },
  bindRequest: function (form, providers, saved) {
    form = form || {};
    var prov = String(form.provider || "").trim();
    var model = String(form.model || "").trim();
    var key = String(form.api_key || "").trim();
    var base = String(form.base_url || "").trim();
    var account = BlocksCore.accountFor(prov, base);
    var useSaved = !key && Array.isArray(saved) && !!account && saved.indexOf(account) >= 0;
    var done = function (body) {
      if (!useSaved) body.api_key = key;
      return { ok: true, body: body, useSaved: useSaved };
    };
    if (!key && !useSaved) return { ok: false, error: "请先填写 API 密钥" };
    if (!model) return { ok: false, error: "请填写模型名称" };
    var list = Array.isArray(providers) ? providers : [];
    var item = null;
    for (var i = 0; i < list.length; i++) {
      if (list[i] && list[i].id === prov) { item = list[i]; break; }
    }
    if (!item) return { ok: false, error: "请选择服务商" };
    if (prov === "other") {
      if (!base) return { ok: false, error: "请填写服务地址" };
      if (base.indexOf("https://") !== 0) return { ok: false, error: "服务地址必须以 https:// 开头" };
      return done({ provider: prov, model: model, base_url: base });
    }
    return done({ provider: prov, model: model });
  },
  modelChoices: function (providers, id) {
    var list = Array.isArray(providers) ? providers : [];
    for (var i = 0; i < list.length; i++) {
      if (list[i] && list[i].id === id) return Array.isArray(list[i].models) ? list[i].models.slice() : [];
    }
    return [];
  },
  bindResultText: function (result) {
    if (result && result.ok) return "绑定好了";
    var reason = result && result.reason ? String(result.reason).trim() : "";
    var how = result && result.how ? String(result.how).trim() : "";
    if (reason && how) return reason.replace(/[。.]+$/, "") + "。" + how;  // 原因自带句号时不叠成两个
    if (reason) return reason;
    if (how) return how;
    return "绑定没通过，请检查服务商、模型和密钥后再试";
  },
  statusText: function (block) {
    var st = block && block.status;
    if (st === "ok") return "能用";
    if (st === "quota") return "额度不足";
    if (st === "listed") return "已接上";
    if (st === "fail") return "不能用";
    return "没检测过";
  },
  statusClass: function (block) {
    var st = block && block.status;
    if (st === "ok") return "ok";
    if (st === "quota" || st === "listed") return "warn";
    if (st === "fail") return "bad";
    return "idle";
  },
  kindText: function (kind) {
    return kind === "cli" ? "CLI" : "大模型 API";
  },
  // 沙盒接入：左栏和详情标类别（AI 助手 / 普通工具 / 大模型 API / 联网搜索）；没有类别时退回老说法
  categoryText: function (category, kind) {
    var labels = { agent: "AI 助手", tool: "普通工具", llm_api: "大模型 API", search: "联网搜索" };
    if (labels[category]) return labels[category];
    if (kind === "search") return "联网搜索";
    return BlocksCore.kindText(kind);
  },
  pendingView: function (p, job) {
    // 待接入：体检认出、还没接进来的预置工具。点一下检测，通过自动放进基础层（用户 2026-11-09 选 A）
    if (!job) return { cls: "idle", text: "待接入 · 点一下检测并接入", busy: false };
    if (!job.done) return { cls: "idle", text: "正在检测…", busy: true };
    if (job.error) return { cls: "bad", text: "没接上：" + job.error, busy: false };
    var res = job.result || {};
    if (job.saved) return { cls: res.ok ? "ok" : "warn", text: "已接入", busy: false };
    return { cls: "bad", text: "没接上：" + (res.reason || "") + (res.how ? "。" + res.how : ""), busy: false };
  },
  chatUrl: function (home, token) {
    var q = new URLSearchParams({ project: String(home || ""), token: String(token || ""), clerk: "1", butler: "1" });
    return "/static/chat.html?" + q.toString();
  },
  esc: function (s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  },
  threadTitle: function (title) {
    return String(title || "").replace(/（[^）]*）$/, "") || "对话";
  },
  healthView: function (scan, blocks) {
    if (!scan) return { state: "loading", expand: false, line: "环境体检：正在检查…", rows: [] };
    var items = scan.items || [];
    var blockMap = {};
    (blocks || []).forEach(function (b) { blockMap[b.id] = b; });
    // 工具报版本常自带名字（“Homebrew 7.0.12”“git version 2.54.0”），前面已经写了名字，这里去掉免得重复
    function shortVersion(v, label) {
      v = String(v);
      var name = String(label || "").toLowerCase() + " ";
      if (name === " " || v.toLowerCase().indexOf(name) !== 0) return v;
      return v.slice(name.length).replace(/^\s*version\s+/i, "").trim() || v;
    }
    var rows = items.map(function (it) {
      var ok = it.status === "有" && it.login !== "没登录" && !it.note;
      var text = it.status === "有" ? (it.version ? shortVersion(it.version, it.label) : "有") + (it.login === "已登录" ? " · 已登录" : (it.login === "没登录" ? " · 没登录" : "")) + (it.note ? " · " + it.note : "") : (it.status === "没有" ? "没有" : "装了，但超级助手找不到");
      var fix = it.fix || (it.note ? "跟管家说一声，它会帮你查" : "");
      return { name: it.name, label: it.label, ok: ok, text: text, fix: fix, why: it.why };
    });
    var toHandle = rows.filter(function (r) { return !r.ok && blockMap[r.name] && blockMap[r.name].kind === "cli"; });
    var state = toHandle.length ? "problem" : "ok";
    var expand = state === "problem";
    var line = "";
    if (state === "problem") {
      var names = toHandle.map(function (r) { return r.label; }).join("、");
      line = "环境体检：基础层里有 " + toHandle.length + " 个工具要处理：" + names;
    } else {
      var inList = rows.filter(function (r) { return blockMap[r.name] && blockMap[r.name].kind === "cli"; });
      if (inList.length === 0) line = "环境体检：基础层里还没有工具";
      else line = "环境体检：基础层里的 " + inList.length + " 个工具都正常";
      var missing = items.filter(function (it) { return it.status === "没有" && !(blockMap[it.name] && blockMap[it.name].kind === "cli"); });
      if (missing.length) line += " · 没装：" + missing.map(function (m) { return m.label; }).join("、");
    }
    return { state: state, expand: expand, line: line, rows: rows };
  },
  detailView: function (d, scanItem) {
    var card = d.card;
    var rows = [{ label: "类型", text: BlocksCore.categoryText(d.category, card.kind) }];
    if (card.kind === "search") {
      rows.push({ label: "服务", text: "Tavily" });
      rows.push({ label: "密钥账号", text: d.key_account });
    } else if (card.kind === "cli") {
      rows.push({ label: "命令", text: card.command, code: true });
      rows.push({ label: "版本", text: scanItem ? scanItem.version || "还没查到" : "还没查到" });
      rows.push({ label: "登录", text: scanItem ? (scanItem.login || "查不了") : "还没查到" });
      rows.push({ label: "来源", text: d.verified ? "预置接法，核实过" : "管家起草，没核实" });
      (d.secrets || []).forEach(function (sec) {
        rows.push({ label: "密钥", text: sec.label + (sec.saved ? " · 已保存" : " · 还没填") });
      });
    } else {
      var provs = { tencent: "腾讯云", bailian: "阿里百炼", other: "其他" };
      rows.push({ label: "服务商", text: provs[card.provider] || card.provider });
      rows.push({ label: "地址", text: card.base_url });
      rows.push({ label: "密钥账号", text: d.key_account });
      // 按整家接（用户 2026-11-10 定）：不显示单个模型，显示这家的模型名单；选哪个是能力层的事
      var names = (d.last_check && d.last_check.models) || [];
      if (names.length) {
        rows.push({ label: "这家的模型", text: (names.length >= 400 ? "已拿到 " + names.length + " 个，可能没拿全" : names.length + " 个") + "（名单来自平台；具体哪个能用，以后选模型时再验）" });
        rows.push({ label: "名单", text: names.join("、"), list: true });
      } else {
        rows.push({ label: "这家的模型", text: "点一次“检测”后显示名单" });
      }
      if (card.model) rows.push({ label: "试通用的模型", text: card.model });
    }
    var time = "没检测过";
    if (d.last_check) {
      time = d.last_check.time.replace("T", " ") + " · 用了 " + d.last_check.seconds + " 秒";
    }
    rows.push({ label: "上次检测", text: time });
    var status = { cls: "idle", text: "没检测过" };
    if (d.last_check) {
      if (d.last_check.ok) status = { cls: "ok", text: "能用" };
      else if (d.last_check.status === "listed") status = { cls: "warn", text: "已接上：" + String(d.last_check.reason || "").replace(/^已接上：/, "") + (d.last_check.how ? "。" + d.last_check.how : "") };
      else if (d.last_check.status === "quota") status = { cls: "warn", text: "额度不足：工具能连上，" + d.last_check.reason + (d.last_check.how ? "。" + d.last_check.how : "") };
      else status = { cls: "bad", text: "不能用：" + d.last_check.reason + (d.last_check.how ? "。" + d.last_check.how : "") };
    }
    var needsKey = card.kind === "llm_api" || card.kind === "search";
    var needsApprove = card.kind === "cli" && !d.verified;
    return {
      title: card.name,
      rows: rows,
      status: status,
      needsKey: needsKey,
      needsApprove: needsApprove,
      preview: needsApprove ? d.preview : "",
      warn: needsApprove ? "未核实，有风险：检测前看清要运行的完整命令" : "",
      checkText: needsApprove ? "看过了，确认检测" : "检测",
      cardText: JSON.stringify(card, null, 2),
      secrets: d.secrets || []
    };
  },
  checkView: function (job) {
    if (!job) return { state: "lost", text: "这次检测的结果没留下来（超级助手重开过），再点一次检测" };
    if (!job.done) return { state: "running", text: "正在检测…" };
    if (job.error) return { state: "fail", text: job.error };
    if (job.result && job.result.ok) return { state: "ok", text: "检测通过 · " + job.result.seconds + " 秒" };
    var res = job.result || {};
    if (res.status === "listed") return { state: "warn", text: "已接上：" + String(res.reason || "").replace(/^已接上：/, "") + (res.how ? "。" + res.how : "") };
    if (res.status === "quota") return { state: "warn", text: "能连上，但额度不足：" + (res.reason || "") + (res.how ? "。" + res.how : "") };
    var text = (res.reason || "") + (res.how ? (res.reason ? "。" : "") + res.how : "");
    if (!text) text = "没成功，原因没查出来，跟管家说一声";
    return { state: "fail", text: text };
  },
  removeConfirm: function (name) {
    return "把「" + name + "」移出基础层？不会卸载工具，也不删密钥。要用时跟管家说一声就能再接回来。";
  },
  deleteConfirm: function (thread) {
    var words = "删除对话“" + BlocksCore.threadTitle(thread && thread.title) + "”？删除后找不回来。";
    if (thread && thread.pending) words += "里面还有一个没确认的提议，会一起作废。";
    return words;
  },
};

if (typeof module !== "undefined") module.exports = BlocksCore;


if (typeof document !== "undefined") (function () {
  var THREADS_MS = 1500, BLOCKS_MS = 5000;
  var params = new URLSearchParams(location.search);
  var token = params.get("token") || "";
  var state = null;
  var currentView = "bind";
  var returnTo = "";  // 绑定卡上的“返回”回哪：main（聊天）、reconnect（重新连上）或不显示
  var checking = false, loopsOn = false, threadsLoaded = false, blocksLoaded = false;
  var frameLoadedHome = "";
  var threads = [], deleted = {}, locks = {}, pendingThread = null, threadKey = "", blocksKey = "";
  var threadTimer = null, blocksTimer = null;
  var lastScan = null, scanBlocks = [], scanBroken = [], scanRunning = false, manualExpand = null, scanError = "";
  var activeBlock = null, currentDetail = null, checkTimer = null, pendingKey = "", checkGen = 0;
  var scanPending = [], pendingJobs = {}, pendingSecrets = {};  // 待接入的预置工具和它们的检测；详情里新填的工具密钥
  // 60 号单：钥匙串里有这一家的密钥时显示“已保存的密钥 / 换一把”；点了“换一把”或存的没通过才露输入框
  var bindWantNew = false, detailWantNew = false, usedSaved = false;
  var settingsOpen = false;  // 打开的是“设置”页（管家 + 环境体检），不是第一次绑定或重新连上
  var SAVED_FAILED = "存的这把没通过，可以换一把";

  function withSavedFailed(text) { return String(text || "").replace(/[。.\s]+$/, "") + "。" + SAVED_FAILED; }

  function savedAccounts() { return (state && state.saved_accounts) || []; }

  function hasSaved(account) { return !!account && savedAccounts().indexOf(account) >= 0; }

  function bindAccount() {
    if (currentView === "reconnect") return (state && state.key_account) || "";
    return BlocksCore.accountFor($("bindProvider").value, $("bindAddress").value);
  }

  /** 输入框里已经填了字的不藏 */
  function syncBindKey() {
    var showSaved = hasSaved(bindAccount()) && !bindWantNew && !$("bindKey").value;
    $("bindSaved").hidden = !showSaved;
    $("bindKey").hidden = showSaved;
  }

  function syncDetailKey() {
    var card = currentDetail && currentDetail.card;
    var isApi = !!card && (card.kind === "llm_api" || card.kind === "search");
    var showSaved = isApi && hasSaved(currentDetail.key_account || card.key_account) && !detailWantNew && !$("detailKey").value;
    $("detailSaved").hidden = !showSaved;
    $("detailKey").hidden = showSaved;
    $("detailKeyTip").hidden = showSaved;
  }

  $("bindNewKey").addEventListener("click", function () {
    bindWantNew = true;
    syncBindKey();
    $("bindKey").focus();
  });

  $("detailNewKey").addEventListener("click", function () {
    detailWantNew = true;
    syncDetailKey();
    $("detailKey").focus();
  });

  $("bindAddress").addEventListener("input", function () {
    bindWantNew = false;  // 换了地址就是另一家：重新判断有没有存的
    syncBindKey();
  });

  /** Mac 窗口把钥匙串交给本机服务后调这里：重新取一次有哪些已保存，重画密钥行 */
  window.blocksKeysChanged = function () {
    getJson("/api/blocks/state").then(function (d) {
      if (!state || !d) return;
      state.saved_accounts = d.saved_accounts || [];
      if (!$("bindWrap").hidden) syncBindKey();
      if (currentDetail) syncDetailKey();
    }).catch(function () {});
    try {  // 聊天页嵌在里面、同源：它的提议也跟着变
      var chatWin = $("chatFrame").contentWindow;
      if (chatWin && chatWin.chatKeysChanged) chatWin.chatKeysChanged();
    } catch (_) {}
  };
  var TRASH = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"></path><path d="M8 6V4h8v2"></path><path d="M19 6l-1 14H6L5 6"></path><path d="M10 11v6M14 11v6"></path></svg>';

  function $(id) { return document.getElementById(id); }

  function showProblem(msg) {
    var el = $("pageProblem");
    if (!el) return;
    el.textContent = msg || "";
    el.hidden = !msg;
  }

  function getJson(path, queryObj) {
    var q = new URLSearchParams(Object.assign({ token: token }, queryObj || {})).toString();
    return fetch(path + "?" + q).catch(function () {
      throw new Error("没连上本机服务，请稍后再试");
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) {
          var err = new Error(d.error || "出错了，请稍后再试");
          err.status = r.status;
          throw err;
        }
        return d;
      });
    });
  }

  function postJson(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Chat-Token": encodeURIComponent(token) },
      body: JSON.stringify(body || {}),
    }).catch(function () {
      throw new Error("没连上本机服务，请稍后再试");
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || "出错了，请稍后再试");
        return d;
      });
    });
  }

  function saveKeyToMac(account, key) {
    if (account && key && window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.blocksKey) {
      try {
        window.webkit.messageHandlers.blocksKey.postMessage({ account: account, key: key });
      } catch (_) {}
    }
  }

  function populateProviders(providers, selectedId) {
    var sel = $("bindProvider");
    sel.innerHTML = "";
    (providers || []).forEach(function (p) {
      var opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.name;
      sel.appendChild(opt);
    });
    if (selectedId && (providers || []).some(function (p) { return p.id === selectedId; })) {
      sel.value = selectedId;
    } else if (providers && providers.length) {
      sel.value = providers[0].id;
    }
    syncProviderFields(false);
  }

  function syncProviderFields(keepModel) {
    var providers = (state && state.providers) || [];
    var prov = $("bindProvider").value;
    var addrRow = $("bindAddressRow");
    if (addrRow) addrRow.hidden = prov !== "other";
    var choices = BlocksCore.modelChoices(providers, prov);
    var dl = $("bindModels");
    dl.innerHTML = "";
    choices.forEach(function (m) {
      var opt = document.createElement("option");
      opt.value = m;
      dl.appendChild(opt);
    });
    if (!keepModel) {
      $("bindModel").value = choices.length ? choices[0] : "";
    }
  }

  function setBindFeedback(text, tone) {
    var res = $("bindResult");
    res.textContent = text || "";
    res.className = "bind-result" + (tone ? " " + tone : "");
  }

  function showBindCard(mode, back) {
    currentView = mode;
    returnTo = back || "";
    $("bindWrap").hidden = false;
    $("mainLayout").hidden = true;
    setBindFeedback("", "");
    $("bindKey").value = "";
    bindWantNew = false;
    $("bindBack").hidden = !returnTo;
    settingsOpen = mode === "bind" && back === "main" && !!(state && state.setting);
    $("settingsHealth").hidden = !settingsOpen;
    $("bindSub").hidden = !settingsOpen;
    $("bindCard").classList.toggle("wide", settingsOpen);
    if (settingsOpen) {
      manualExpand = true;  // 设置页里体检列表默认展开
      drawHealth();
    }

    var setting = state && state.setting;
    if (mode === "reconnect" && setting) {
      $("bindTitle").textContent = "重新连上积木库管家";
      populateProviders(state.providers, setting.provider);
      $("bindModel").value = setting.model || "";
      $("bindAddress").value = setting.base_url || "";
      $("bindAddressRow").hidden = setting.provider !== "other";
      $("bindProvider").disabled = true;
      $("bindModel").disabled = true;
      $("bindAddress").disabled = true;
      $("bindChange").hidden = false;
    } else {
      $("bindTitle").textContent = settingsOpen ? "设置" : (setting ? "换一个管家：换服务商、模型或密钥" : "先绑定一个大模型，它会当你的积木库管家");
      populateProviders(state ? state.providers : [], setting && setting.provider);
      if (setting && setting.model) $("bindModel").value = setting.model;
      if (setting && setting.base_url && setting.provider === "other") $("bindAddress").value = setting.base_url;
      $("bindProvider").disabled = false;
      $("bindModel").disabled = false;
      $("bindAddress").disabled = false;
      $("bindChange").hidden = true;
    }
    syncBindKey();
  }

  function showMainView() {
    currentView = "main";
    $("bindKey").value = "";
    $("bindWrap").hidden = true;
    $("mainLayout").hidden = false;
    var home = (state && state.home) || "";
    var frame = $("chatFrame");
    if (home && frameLoadedHome !== home) {
      frame.src = BlocksCore.chatUrl(home, token);
      frameLoadedHome = home;
    }
    startLoops();
    scanAll();
  }

  function chatApi() {
    var frame = $("chatFrame");
    try {
      return frame && frame.contentWindow && frame.contentWindow.SuperAgentChat;
    } catch (_) {
      return null;
    }
  }

  function drawThreads() {
    var shown = (threads || []).filter(function (t) { return !deleted[t.id]; });
    var key = JSON.stringify(shown.map(function (t) {
      return [t.id, t.title, !!t.current, !!t.busy, !!t.pending, t.deletable];
    }));
    if (key === threadKey) return;
    threadKey = key;
    if (!shown.length) {
      $("threads").innerHTML = '<div class="empty">还没有对话</div>';
      return;
    }
    $("threads").innerHTML = shown.map(function (t) {
      var on = !!t.current;
      var row = '<div class="row"><button class="thread' + (on ? " on" : "") + '" type="button" data-thread="' + BlocksCore.esc(t.id) + '">' +
        '<span class="tt">' + BlocksCore.esc(BlocksCore.threadTitle(t.title)) + "</span></button>";
      if (t.deletable !== false && !t.busy) {
        row += '<button class="rowact" type="button" data-delete="' + BlocksCore.esc(t.id) + '" aria-label="删除这段对话" title="删除这段对话">' + TRASH + "</button>";
      }
      return row + "</div>";
    }).join("");
  }

  function fetchThreads() {
    if (!state || !state.home || currentView !== "main") return Promise.resolve();
    return getJson("/api/clerk/list", { project: state.home }).then(function (d) {
      if (!Array.isArray(d.threads)) return;
      var list = d.threads.filter(function (t) { return !deleted[t.id]; });
      if (pendingThread && d.current === pendingThread) pendingThread = null;
      if (pendingThread) {
        list = list.map(function (t) { return Object.assign({}, t, { current: t.id === pendingThread }); });
      }
      threads = list;
      threadsLoaded = true;
      drawThreads();
    }).catch(function () {
      if (!threadsLoaded) $("threads").innerHTML = '<div class="empty">没连上本机服务，请稍后再试</div>';
    });
  }

  function drawBlocks(data) {
    var items = (data && data.blocks) || [];
    var broken = (data && data.broken) || [];
    var pending = (data && data.pending) || scanPending || [];
    scanBlocks = items;
    scanBroken = broken;
    scanPending = pending;
    drawHealth();
    var key = JSON.stringify([items, broken, pending, activeBlock, pendingJobs]);
    if (key === blocksKey) return;
    blocksKey = key;
    if (!items.length && !broken.length && !pending.length) {
      $("blocksList").innerHTML = '<div class="empty">还没有接进来的工具，跟管家说一声就行</div>';
      return;
    }
    var html = items.map(function (b) {
      var cls = BlocksCore.statusClass(b);
      var st = BlocksCore.statusText(b);
      return '<div class="block-item' + (activeBlock === b.id ? " on" : "") + '" data-block="' + BlocksCore.esc(b.id) + '">' +
        '<div class="block-top"><span class="block-name">' + BlocksCore.esc(b.name) + '</span>' +
        '<span class="block-kind">' + BlocksCore.esc(BlocksCore.categoryText(b.category, b.kind)) + "</span></div>" +
        '<div class="block-status"><i class="dot ' + cls + '"></i><span>' + BlocksCore.esc(st) + "</span></div>" +
      "</div>";
    }).join("");
    html += broken.map(function (b) {
      return '<div class="block-item broken' + (activeBlock === b.id ? " on" : "") + '" data-broken="' + BlocksCore.esc(b.id) + '" data-problem="' + BlocksCore.esc(b.problem) + '">' +
        '<div class="block-top"><span class="block-name">' + BlocksCore.esc(b.id) + '</span></div>' +
        '<div class="block-status"><i class="dot bad"></i><span>接法说明有问题</span></div>' +
      "</div>";
    }).join("");
    pending.forEach(function (p) {  // 页面重新加载时还在检测的：接着跟，不显示成“点一下”（复查发现）
      if (p.job && !(p.id in pendingJobs)) followPending(p.id, p.job);
    });
    html += pending.map(function (p) {
      var v = BlocksCore.pendingView(p, pendingJobs[p.id]);
      return '<div class="block-item pending' + (v.busy ? " busy" : "") + '" data-pending="' + BlocksCore.esc(p.id) + '">' +
        '<div class="block-top"><span class="block-name">' + BlocksCore.esc(p.name) + '</span>' +
        '<span class="block-kind">' + BlocksCore.esc(BlocksCore.categoryText(p.category, "cli")) + "</span></div>" +
        '<div class="block-status"><i class="dot ' + v.cls + '"></i><span>' + BlocksCore.esc(v.text) + "</span></div>" +
      "</div>";
    }).join("");
    $("blocksList").innerHTML = html;
  }

  function followPending(id, jobId) {
    pendingJobs[id] = pendingJobs[id] || { done: false };
    var poll = function () {
      getJson("/api/blocks/job", { id: jobId }).then(function (job) {
        pendingJobs[id] = job;
        drawBlocks({ blocks: scanBlocks, broken: scanBroken, pending: scanPending });
        if (job.done) fetchBlocks();
        else setTimeout(poll, 1500);
      }).catch(function (err) {
        pendingJobs[id] = err && err.status === 404 ? null : { done: true, error: err.message };
        drawBlocks({ blocks: scanBlocks, broken: scanBroken, pending: scanPending });
      });
    };
    poll();
  }

  function connectPending(id) {
    if (pendingJobs[id] && !pendingJobs[id].done) return;
    pendingJobs[id] = { done: false };
    drawBlocks({ blocks: scanBlocks, broken: scanBroken, pending: scanPending });
    postJson("/api/blocks/connect_builtin", { id: id }).then(function (d) {
      followPending(id, d.job);
    }).catch(function (err) {
      pendingJobs[id] = { done: true, error: err.message };
      drawBlocks({ blocks: scanBlocks, broken: scanBroken, pending: scanPending });
    });
  }

  $("blocksList").addEventListener("click", function (ev) {
    var item = ev.target.closest(".block-item");
    if (!item) return;
    var blockId = item.getAttribute("data-block");
    var brokenId = item.getAttribute("data-broken");
    var pendingId = item.getAttribute("data-pending");
    if (pendingId) {
      connectPending(pendingId);
    } else if (blockId) {
      activeBlock = blockId;
      drawBlocks({ blocks: scanBlocks, broken: scanBroken, pending: scanPending });
      openDetail(blockId);
    } else if (brokenId) {
      activeBlock = brokenId;
      drawBlocks({ blocks: scanBlocks, broken: scanBroken });
      openBrokenDetail(brokenId, item.getAttribute("data-problem"));
    }
  });

  function clearCheckState() {
    checkGen++;  // 换块、离开详情：路上还没回来的检测请求和查询一律作废
    if (checkTimer) { clearTimeout(checkTimer); checkTimer = null; }
    pendingKey = "";
    usedSaved = false;
    $("detailCheck").disabled = false;
    $("detailRemove").disabled = false;
  }

  function openDetail(id, keepResult) {
    window.AbilitiesPage && window.AbilitiesPage.close();
    if (!keepResult) {
      clearCheckState();
      currentDetail = null;  // 新详情回来前不留上一块的内容，免得点到上一块的按钮
      detailWantNew = false;
      $("detailResult").textContent = "";
      $("detailResult").className = "detail-result";
      $("detailTitle").textContent = "正在读取…";
      ["detailStatus", "detailWarn", "detailRows", "detailPreview", "detailKeyBox", "detailCheck", "detailRemove", "detailCard"].forEach(function (k) { $(k).hidden = true; });
    }
    $("chatFrame").hidden = true;
    $("blockDetail").hidden = false;
    $("detailKey").value = "";
    getJson("/api/blocks/detail", { id: id }).then(function (d) {
      if (activeBlock !== id) return;
      currentDetail = d;
      var scanItem = (lastScan && lastScan.items || []).find(function (it) { return it.name === id; }) || null;
      var v = BlocksCore.detailView(d, scanItem);
      $("detailTitle").textContent = v.title;
      $("detailStatus").textContent = v.status.text;
      $("detailStatus").className = "detail-status " + v.status.cls;
      $("detailStatus").hidden = false;
      $("detailWarn").textContent = v.warn;
      $("detailWarn").hidden = !v.warn;
      var rowsEl = $("detailRows");
      rowsEl.hidden = false;
      rowsEl.innerHTML = "";
      v.rows.forEach(function (r) {
        var rowDiv = document.createElement("div");
        rowDiv.className = "detail-row";
        var lbl = document.createElement("span");
        lbl.className = "detail-label";
        lbl.textContent = r.label;
        rowDiv.appendChild(lbl);
        var val = document.createElement(r.code ? "code" : "span");
        val.className = "detail-value" + (r.list ? " detail-list" : "");
        val.textContent = r.text;
        rowDiv.appendChild(val);
        rowsEl.appendChild(rowDiv);
      });
      $("detailPreview").textContent = v.preview;
      $("detailPreview").hidden = !v.preview;
      $("detailKeyBox").hidden = !v.needsKey;
      syncDetailKey();
      drawDetailSecrets(v.secrets);
      $("detailCheck").textContent = v.checkText;
      $("detailCheck").hidden = false;
      $("detailRemove").hidden = false;
      $("detailCard").hidden = false;
      $("detailCard").querySelector("pre").textContent = v.cardText;
    }).catch(function (err) {
      if (activeBlock !== id) return;
      currentDetail = null;
      $("detailTitle").textContent = err && err.status === 404 ? "基础层里没有这个积木块了" : "详情没取到：" + err.message;
      ["detailStatus", "detailWarn", "detailRows", "detailPreview", "detailKeyBox", "detailCheck", "detailRemove", "detailCard"].forEach(function (k) { $(k).hidden = true; });
    });
  }

  function drawDetailSecrets(secrets) {
    // 普通工具的几把密钥：一把一格；钥匙串里存过的可以空着（用存的）
    var box = $("detailSecrets");
    if (!box) {
      box = document.createElement("div");
      box.id = "detailSecrets";
      box.className = "detail-key";
      $("detailKeyBox").after(box);
    }
    box.innerHTML = "";
    box.hidden = !(secrets && secrets.length);
    (secrets || []).forEach(function (sec) {
      var lab = document.createElement("small");
      lab.textContent = sec.label + (sec.saved ? "（已保存，可以空着）" : "");
      var inp = document.createElement("input");
      inp.type = "password";
      inp.setAttribute("autocomplete", "off");
      inp.setAttribute("data-account", sec.account);
      if (sec.saved) inp.setAttribute("data-saved", "1");
      inp.placeholder = "在这里填入「" + sec.label + "」";
      box.appendChild(lab);
      box.appendChild(inp);
    });
  }

  function openBrokenDetail(id, problem) {
    window.AbilitiesPage && window.AbilitiesPage.close();
    clearCheckState();
    $("detailResult").textContent = "";
    $("detailResult").className = "detail-result";
    $("detailKey").value = "";
    $("chatFrame").hidden = true;
    $("blockDetail").hidden = false;
    $("detailTitle").textContent = id;
    $("detailStatus").textContent = "接法说明有问题：" + problem;
    $("detailStatus").className = "detail-status bad";
    $("detailStatus").hidden = false;
    ["detailWarn", "detailRows", "detailPreview", "detailKeyBox", "detailCheck", "detailCard"].forEach(function (k) { $(k).hidden = true; });
    $("detailRemove").hidden = false;
    currentDetail = { card: { id: id, name: id } };
  }

  $("detailBack").addEventListener("click", backToChat);

  function closeBlockStage() {
    clearCheckState();
    activeBlock = null;
    currentDetail = null;
    drawBlocks({ blocks: scanBlocks, broken: scanBroken });
    $("blockDetail").hidden = true;
    $("detailKey").value = "";
    $("detailResult").textContent = "";
    $("detailResult").className = "detail-result";
  }

  window.BlocksStage = {
    closeBlock: closeBlockStage,
  };

  function backToChat() {
    window.AbilitiesPage && window.AbilitiesPage.close();
    closeBlockStage();
    $("chatFrame").hidden = false;
  }

  $("detailCheck").addEventListener("click", function () {
    if (!currentDetail) return;
    var card = currentDetail.card;
    var payload = { id: card.id };
    usedSaved = false;
    pendingSecrets = {};
    var secretInps = document.querySelectorAll("#detailSecrets input[data-account]");
    if (secretInps.length) {
      var lacking = false;
      secretInps.forEach(function (si) {
        var v = si.value.trim();
        if (v) pendingSecrets[si.getAttribute("data-account")] = v;
        else if (!si.getAttribute("data-saved")) lacking = true;
      });
      if (lacking) {
        $("detailResult").textContent = "请先把每个密钥框都填好";
        $("detailResult").className = "detail-result fail";
        return;
      }
      payload.secret_keys = pendingSecrets;
    }
    if (card.kind === "llm_api" || card.kind === "search") {
      var k = $("detailKey").value.trim();
      if (!k && !$("detailSaved").hidden) {
        usedSaved = true;  // 用钥匙串里存的那把：请求里不带密钥
      } else if (!k) {
        $("detailResult").textContent = "请先在密钥框里填好密钥";
        $("detailResult").className = "detail-result fail";
        return;
      } else {
        payload.api_key = k;
        pendingKey = k;
      }
    }
    if (card.kind === "cli" && !currentDetail.verified) {
      var scanItem = (lastScan && lastScan.items || []).find(function (it) { return it.name === card.id; }) || null;
      payload.approved = BlocksCore.detailView(currentDetail, scanItem).preview;
    }
    $("detailCheck").disabled = true;
    $("detailRemove").disabled = true;
    $("detailResult").textContent = "正在检测…";
    $("detailResult").className = "detail-result running";
    var gen = checkGen;
    postJson("/api/blocks/check", payload).then(function (d) {
      if (gen !== checkGen) return;
      $("detailKey").value = "";
      followCheck(d.job, card.id);
    }).catch(function (err) {
      if (gen !== checkGen) return;
      pendingKey = "";
      $("detailResult").textContent = err.message;
      $("detailResult").className = "detail-result fail";
      $("detailCheck").disabled = false;
      $("detailRemove").disabled = false;
    });
  });

  function followCheck(jobId, cardId) {
    if (checkTimer) { clearTimeout(checkTimer); checkTimer = null; }
    var gen = checkGen;
    var poll = function () {
      if (gen !== checkGen || activeBlock !== cardId) return;
      getJson("/api/blocks/job", { id: jobId }).then(function (job) {
        if (gen !== checkGen || activeBlock !== cardId) return;
        var v = BlocksCore.checkView(job);
        $("detailResult").textContent = v.text;
        $("detailResult").className = "detail-result " + v.state;
        if (job.done && (v.state === "ok" || v.state === "warn")) detailWantNew = false;  // 新填的通过了：存进钥匙串后这里显示“已保存”
        if (job.done && v.state === "fail" && usedSaved && BlocksCore.keyProblem(job.result && job.result.reason)) {
          detailWantNew = true;  // 存的那把没通过：说原因，展开输入框
          $("detailResult").textContent = withSavedFailed(v.text);
        }
        if (job.done) usedSaved = false;
        if (job.done) {
          $("detailCheck").disabled = false;
          $("detailRemove").disabled = false;
          if ((v.state === "ok" || v.state === "warn") && currentDetail && currentDetail.card.kind !== "cli" && pendingKey) {
            saveKeyToMac(currentDetail.key_account, pendingKey);
          }
          if (v.state === "ok" || v.state === "warn") {
            Object.keys(pendingSecrets).forEach(function (acc) { saveKeyToMac(acc, pendingSecrets[acc]); });
          }
          pendingSecrets = {};
          pendingKey = "";
          openDetail(cardId, true);
          fetchBlocks();
        } else {
          checkTimer = setTimeout(poll, 1000);
        }
      }).catch(function (err) {
        if (gen !== checkGen || activeBlock !== cardId) return;
        if (err && err.status === 404) {
          var lost = BlocksCore.checkView(null);
          $("detailResult").textContent = lost.text;
          $("detailResult").className = "detail-result " + lost.state;
          $("detailCheck").disabled = false;
          $("detailRemove").disabled = false;
          pendingKey = "";
          return;
        }
        checkTimer = setTimeout(poll, 2000);
      });
    };
    poll();
  }

  $("detailRemove").addEventListener("click", function () {
    if (!currentDetail) return;
    if (!window.confirm(BlocksCore.removeConfirm(currentDetail.card.name))) return;
    postJson("/api/blocks/remove", { id: currentDetail.card.id }).then(function () {
      backToChat();
      fetchBlocks();
    }).catch(function (err) {
      $("detailResult").textContent = err.message;
      $("detailResult").className = "detail-result fail";
    });
  });

  function fetchBlocks() {
    if (!state || currentView !== "main") return Promise.resolve();
    return getJson("/api/blocks/list").then(function (d) {
      blocksLoaded = true;
      drawBlocks(d);
    }).catch(function () {
      if (!blocksLoaded) $("blocksList").innerHTML = '<div class="empty">没连上本机服务，请稍后再试</div>';
    });
  }

  function startLoops() {
    if (loopsOn) {
      fetchThreads();
      fetchBlocks();
      return;
    }
    loopsOn = true;
    if (!threadTimer) {
      var runThreads = function () {
        fetchThreads().then(function () {
          threadTimer = setTimeout(runThreads, THREADS_MS);
        });
      };
      runThreads();
    } else {
      fetchThreads();
    }
    if (!blocksTimer) {
      var runBlocks = function () {
        fetchBlocks().then(function () {
          blocksTimer = setTimeout(runBlocks, BLOCKS_MS);
        });
      };
      runBlocks();
    } else {
      fetchBlocks();
    }
  }

  $("bindProvider").addEventListener("change", function () {
    syncProviderFields(false);
    setBindFeedback("", "");
    bindWantNew = false;
    syncBindKey();
  });

  $("bindChange").addEventListener("click", function () {
    if (!checking) showBindCard("bind", "reconnect");
  });

  $("bindBack").addEventListener("click", function () {
    if (checking) return;
    if (returnTo === "main") showMainView();
    else if (returnTo === "reconnect") showBindCard("reconnect");
  });

  $("settingsBtn").addEventListener("click", function () {
    showBindCard("bind", "main");
  });

  $("healthToggle").addEventListener("click", function () {
    var rowsEl = $("healthRows");
    manualExpand = rowsEl.hidden;
    drawHealth();
  });

  $("healthRecheck").addEventListener("click", function () {
    if (scanRunning) return;
    scanAll();
  });

  function setChecking(on) {
    checking = on;
    $("bindCheck").disabled = on;
    $("bindCheck").textContent = on ? "检测中…" : "检测";
    $("bindBack").disabled = on;
    $("bindChange").disabled = on;
  }

  $("bindCheck").addEventListener("click", function () {
    if (checking) return;
    var providers = (state && state.providers) || [];
    var form = {
      provider: $("bindProvider").value,
      model: $("bindModel").value,
      base_url: $("bindAddress").value,
      api_key: $("bindKey").value,
    };
    var check = BlocksCore.bindRequest(form, providers, $("bindSaved").hidden ? [] : savedAccounts());
    if (!check.ok) {
      setBindFeedback(check.error, "bad");
      return;
    }
    var rawKey = check.body.api_key || "";
    var withSaved = check.useSaved;
    setChecking(true);
    setBindFeedback("", "");

    var isReconnect = currentView === "reconnect";
    var url = isReconnect ? "/api/blocks/connect" : "/api/blocks/bind";
    var payload = isReconnect ? (withSaved ? { check: true } : { api_key: rawKey, check: true }) : check.body;

    postJson(url, payload).then(function (res) {
      setChecking(false);
      if (res && res.ok) {
        var account = res.key_account || (state && state.key_account) || "";
        if (rawKey) saveKeyToMac(account, rawKey);
        $("bindKey").value = "";
        state.connected = true;
        if (!isReconnect) {
          state.setting = {
            provider: check.body.provider,
            model: check.body.model,
            base_url: check.body.base_url || "",
          };
          if (res.key_account) state.key_account = res.key_account;
        }
        setBindFeedback(BlocksCore.bindResultText(res), "ok");
        showMainView();
      } else if (withSaved && BlocksCore.keyProblem(res && res.reason)) {
        bindWantNew = true;  // 存的那把没通过：说原因，展开输入框
        syncBindKey();
        setBindFeedback(withSavedFailed((res && res.reason) || BlocksCore.bindResultText(res)), "bad");  // 只说原因，再说换一把
      } else {
        setBindFeedback(BlocksCore.bindResultText(res), "bad");
      }
    }).catch(function (err) {
      setChecking(false);
      setBindFeedback(err.message || "没连上本机服务，请稍后再试", "bad");
    });
  });

  function scanAll() {
    if (scanRunning) return;
    scanRunning = true;
    scanError = "";
    drawHealth();
    postJson("/api/blocks/scan").then(function (d) {
      pollScan(d.job);
    }).catch(function (err) {
      scanRunning = false;
      scanError = "环境体检没做成：" + (err.message || "出错了，请稍后再试");
      drawHealth();
    });
  }

  function pollScan(jobId) {
    var check = function () {
      getJson("/api/blocks/job", { id: jobId }).then(function (d) {
        if (d.done) {
          scanRunning = false;
          if (d.error) {
            scanError = "环境体检没做成：" + d.error;
          } else {
            lastScan = d.result;
            manualExpand = null;
          }
          drawHealth();
        } else {
          setTimeout(check, 1000);
        }
      }).catch(function (err) {
        scanRunning = false;
        if (err && err.status === 404) {
          scanError = "环境体检没做成：超级助手重开过，点“重新检查”";
        } else {
          scanError = "环境体检没做成：" + (err.message || "出错了，请稍后再试");
        }
        drawHealth();
      });
    };
    check();
  }

  function drawHealth() {
    $("healthRecheck").disabled = scanRunning;
    var rowsEl = $("healthRows");
    if (scanError) {
      $("settingsBtn").classList.toggle("alert", BlocksCore.settingsAlert(null, scanError));
      $("healthLine").textContent = scanError;
      $("healthToggle").hidden = true;
      rowsEl.hidden = true;
      return;
    }
    var v = BlocksCore.healthView(scanRunning ? null : lastScan, scanBlocks);
    $("settingsBtn").classList.toggle("alert", BlocksCore.settingsAlert(v, ""));
    $("healthLine").textContent = v.line;
    rowsEl.innerHTML = "";
    v.rows.forEach(function (r) {
      var div = document.createElement("div");
      div.className = "health-row";
      var mark = document.createElement("span");
      mark.className = "health-mark " + (r.ok ? "ok" : "bad");
      mark.textContent = r.ok ? "✓" : "✕";
      div.appendChild(mark);
      div.appendChild(document.createTextNode(r.label + "  " + r.text));
      var note = [r.why, r.fix].filter(Boolean).join("：");
      if (!r.ok && note) {
        var noteEl = document.createElement("div");
        noteEl.className = "health-note";
        noteEl.textContent = note;
        div.appendChild(noteEl);
      }
      rowsEl.appendChild(div);
    });
    var hasRows = v.rows.length > 0;
    var isExpanded = hasRows && (manualExpand !== null ? manualExpand : v.expand);
    $("healthToggle").hidden = !hasRows;
    $("healthToggle").textContent = isExpanded ? "收起" : "展开";
    rowsEl.hidden = !isExpanded;
  }

  $("newChat").addEventListener("click", function () {
    backToChat();
    pendingThread = null;
    threads = threads.map(function (t) { return Object.assign({}, t, { current: false }); });
    threadKey = "";
    drawThreads();
    var chat = chatApi();
    if (chat && typeof chat.startFresh === "function") chat.startFresh();
  });

  $("threads").addEventListener("click", function (ev) {
    var del = ev.target.closest("button[data-delete]");
    if (del) {
      var gone = del.getAttribute("data-delete");
      if (locks[gone]) return;
      var thread = null;
      for (var i = 0; i < threads.length; i++) {
        if (threads[i].id === gone) { thread = threads[i]; break; }
      }
      thread = thread || { id: gone, title: "这段对话" };
      if (!window.confirm(BlocksCore.deleteConfirm(thread))) return;
      locks[gone] = true;
      postJson("/api/clerk/delete", { project: state.home, conversation: gone }).then(function () {
        delete locks[gone];
        deleted[gone] = true;
        if (pendingThread === gone) pendingThread = null;
        threads = threads.filter(function (t) { return t.id !== gone; });
        threadKey = "";
        drawThreads();
        var chat = chatApi();
        if (chat && typeof chat.threadDeleted === "function") chat.threadDeleted(gone);
      }).catch(function (err) {
        delete locks[gone];
        window.alert(err.message || "出错了，请稍后再试");
      });
      return;
    }
    var b = ev.target.closest("button[data-thread]");
    if (!b) return;
    backToChat();
    var id = b.getAttribute("data-thread");
    pendingThread = id;
    threads = threads.map(function (t) { return Object.assign({}, t, { current: t.id === id }); });
    threadKey = "";
    drawThreads();
    var chat = chatApi();
    if (chat && typeof chat.openThread === "function") chat.openThread(id);
  });

  function init() {
    if (!token) {
      showProblem("请从超级助手窗口打开积木库");
      return;
    }
    getJson("/api/blocks/state").then(function (d) {
      state = d;
      var view = BlocksCore.startView(state);
      if (view === "main") {
        showMainView();
      } else {
        showBindCard(view);
      }
    }).catch(function (err) {
      showProblem(err.message || "没连上本机服务，请稍后再试");
    });
  }

  init();
})();
