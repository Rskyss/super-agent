/* 积木库页面（上）：绑定页、左栏（聊天列表、基础层列表）、中间的行政聊天。
   分两层：BlocksCore（规则层，不碰页面，tests/blocks_page_check.js 用 node 直接检查）；页面层。
   密钥规矩：只在密钥框和那一次请求里出现，不存网页、不放地址、不打印。 */
"use strict";

var BlocksCore = {
  startView: function (state) {
    if (!state || !state.setting) return "bind";
    if (!state.connected) return "reconnect";
    return "main";
  },
  bindRequest: function (form, providers) {
    form = form || {};
    var prov = String(form.provider || "").trim();
    var model = String(form.model || "").trim();
    var key = String(form.api_key || "").trim();
    var base = String(form.base_url || "").trim();
    if (!key) return { ok: false, error: "请先填写 API 密钥" };
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
      return { ok: true, body: { provider: prov, model: model, api_key: key, base_url: base } };
    }
    return { ok: true, body: { provider: prov, model: model, api_key: key } };
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
    if (reason && how) return reason + "。" + how;
    if (reason) return reason;
    if (how) return how;
    return "绑定没通过，请检查服务商、模型和密钥后再试";
  },
  statusText: function (block) {
    var st = block && block.status;
    if (st === "ok") return "能用";
    if (st === "fail") return "不能用";
    return "没检测过";
  },
  statusClass: function (block) {
    var st = block && block.status;
    if (st === "ok") return "ok";
    if (st === "fail") return "bad";
    return "idle";
  },
  kindText: function (kind) {
    return kind === "cli" ? "CLI" : "大模型 API";
  },
  chatUrl: function (home, token) {
    var q = new URLSearchParams({ project: String(home || ""), token: String(token || ""), clerk: "1" });
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
      var fix = it.fix || (it.note ? "跟行政说一声，它会帮你查" : "");
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
    var rows = [{ label: "类型", text: BlocksCore.kindText(card.kind) }];
    if (card.kind === "cli") {
      rows.push({ label: "命令", text: card.command, code: true });
      rows.push({ label: "版本", text: scanItem ? scanItem.version || "还没查到" : "还没查到" });
      rows.push({ label: "登录", text: scanItem ? (scanItem.login || "查不了") : "还没查到" });
      rows.push({ label: "来源", text: d.verified ? "预置接法，核实过" : "行政起草，没核实" });
    } else {
      var provs = { tencent: "腾讯云", bailian: "阿里百炼", other: "其他" };
      rows.push({ label: "服务商", text: provs[card.provider] || card.provider });
      rows.push({ label: "模型", text: card.model });
      rows.push({ label: "地址", text: card.base_url });
      rows.push({ label: "密钥账号", text: d.key_account });
    }
    var time = "没检测过";
    if (d.last_check) {
      time = d.last_check.time.replace("T", " ") + " · 用了 " + d.last_check.seconds + " 秒";
    }
    rows.push({ label: "上次检测", text: time });
    var status = { cls: "idle", text: "没检测过" };
    if (d.last_check) {
      if (d.last_check.ok) status = { cls: "ok", text: "能用" };
      else status = { cls: "bad", text: "不能用：" + d.last_check.reason + (d.last_check.how ? "。" + d.last_check.how : "") };
    }
    var needsKey = card.kind === "llm_api";
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
      cardText: JSON.stringify(card, null, 2)
    };
  },
  checkView: function (job) {
    if (!job) return { state: "lost", text: "这次检测的结果没留下来（超级助手重开过），再点一次检测" };
    if (!job.done) return { state: "running", text: "正在检测…" };
    if (job.error) return { state: "fail", text: job.error };
    if (job.result && job.result.ok) return { state: "ok", text: "检测通过 · " + job.result.seconds + " 秒" };
    var res = job.result || {};
    var text = (res.reason || "") + (res.how ? (res.reason ? "。" : "") + res.how : "");
    if (!text) text = "没成功，原因没查出来，跟行政说一声";
    return { state: "fail", text: text };
  },
  removeConfirm: function (name) {
    return "把「" + name + "」移出基础层？不会卸载工具，也不删密钥。要用时跟行政说一声就能再接回来。";
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
    $("bindBack").hidden = !returnTo;

    var setting = state && state.setting;
    if (mode === "reconnect" && setting) {
      $("bindTitle").textContent = "重新连上行政";
      populateProviders(state.providers, setting.provider);
      $("bindModel").value = setting.model || "";
      $("bindAddress").value = setting.base_url || "";
      $("bindAddressRow").hidden = setting.provider !== "other";
      $("bindProvider").disabled = true;
      $("bindModel").disabled = true;
      $("bindAddress").disabled = true;
      $("bindChange").hidden = false;
    } else {
      $("bindTitle").textContent = setting ? "行政设置：换服务商、模型或密钥" : "先绑定一个大模型，它会当你的行政";
      populateProviders(state ? state.providers : [], setting && setting.provider);
      if (setting && setting.model) $("bindModel").value = setting.model;
      if (setting && setting.base_url && setting.provider === "other") $("bindAddress").value = setting.base_url;
      $("bindProvider").disabled = false;
      $("bindModel").disabled = false;
      $("bindAddress").disabled = false;
      $("bindChange").hidden = true;
    }
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
    scanBlocks = items;
    scanBroken = broken;
    drawHealth();
    var key = JSON.stringify([items, broken, activeBlock]);
    if (key === blocksKey) return;
    blocksKey = key;
    if (!items.length && !broken.length) {
      $("blocksList").innerHTML = '<div class="empty">还没有接进来的工具，跟行政说一声就行</div>';
      return;
    }
    var html = items.map(function (b) {
      var cls = BlocksCore.statusClass(b);
      var st = BlocksCore.statusText(b);
      return '<div class="block-item' + (activeBlock === b.id ? " on" : "") + '" data-block="' + BlocksCore.esc(b.id) + '">' +
        '<div class="block-top"><span class="block-name">' + BlocksCore.esc(b.name) + '</span>' +
        '<span class="block-kind">' + BlocksCore.esc(BlocksCore.kindText(b.kind)) + "</span></div>" +
        '<div class="block-status"><i class="dot ' + cls + '"></i><span>' + BlocksCore.esc(st) + "</span></div>" +
      "</div>";
    }).join("");
    html += broken.map(function (b) {
      return '<div class="block-item broken' + (activeBlock === b.id ? " on" : "") + '" data-broken="' + BlocksCore.esc(b.id) + '" data-problem="' + BlocksCore.esc(b.problem) + '">' +
        '<div class="block-top"><span class="block-name">' + BlocksCore.esc(b.id) + '</span></div>' +
        '<div class="block-status"><i class="dot bad"></i><span>这张接法卡坏了</span></div>' +
      "</div>";
    }).join("");
    $("blocksList").innerHTML = html;
  }

  $("blocksList").addEventListener("click", function (ev) {
    var item = ev.target.closest(".block-item");
    if (!item) return;
    var blockId = item.getAttribute("data-block");
    var brokenId = item.getAttribute("data-broken");
    if (blockId) {
      activeBlock = blockId;
      drawBlocks({ blocks: scanBlocks, broken: scanBroken });
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
    $("detailCheck").disabled = false;
    $("detailRemove").disabled = false;
  }

  function openDetail(id, keepResult) {
    if (!keepResult) {
      clearCheckState();
      currentDetail = null;  // 新详情回来前不留上一块的内容，免得点到上一块的按钮
      $("detailResult").textContent = "";
      $("detailResult").className = "detail-result";
      $("detailTitle").textContent = "正在读取…";
      ["detailStatus", "detailWarn", "detailRows", "detailPreview", "detailKeyBox", "detailCheck", "detailRemove", "detailCard"].forEach(function (k) { $(k).hidden = true; });
    }
    $("healthCard").hidden = true;
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
        val.className = "detail-value";
        val.textContent = r.text;
        rowDiv.appendChild(val);
        rowsEl.appendChild(rowDiv);
      });
      $("detailPreview").textContent = v.preview;
      $("detailPreview").hidden = !v.preview;
      $("detailKeyBox").hidden = !v.needsKey;
      $("detailCheck").textContent = v.checkText;
      $("detailCheck").hidden = false;
      $("detailRemove").hidden = false;
      $("detailCard").hidden = false;
      $("detailCard").querySelector("pre").textContent = v.cardText;
    }).catch(function (err) {
      if (activeBlock !== id) return;
      currentDetail = null;
      $("detailTitle").textContent = err && err.status === 404 ? "基础层里没有这一块了" : "详情没取到：" + err.message;
      ["detailStatus", "detailWarn", "detailRows", "detailPreview", "detailKeyBox", "detailCheck", "detailRemove", "detailCard"].forEach(function (k) { $(k).hidden = true; });
    });
  }

  function openBrokenDetail(id, problem) {
    clearCheckState();
    $("detailResult").textContent = "";
    $("detailResult").className = "detail-result";
    $("detailKey").value = "";
    $("healthCard").hidden = true;
    $("chatFrame").hidden = true;
    $("blockDetail").hidden = false;
    $("detailTitle").textContent = id;
    $("detailStatus").textContent = "这张接法卡坏了：" + problem;
    $("detailStatus").className = "detail-status bad";
    $("detailStatus").hidden = false;
    ["detailWarn", "detailRows", "detailPreview", "detailKeyBox", "detailCheck", "detailCard"].forEach(function (k) { $(k).hidden = true; });
    $("detailRemove").hidden = false;
    currentDetail = { card: { id: id, name: id } };
  }

  $("detailBack").addEventListener("click", backToChat);

  function backToChat() {
    clearCheckState();
    activeBlock = null;
    currentDetail = null;
    drawBlocks({ blocks: scanBlocks, broken: scanBroken });
    $("blockDetail").hidden = true;
    $("healthCard").hidden = false;
    $("chatFrame").hidden = false;
    $("detailKey").value = "";
    $("detailResult").textContent = "";
    $("detailResult").className = "detail-result";
  }

  $("detailCheck").addEventListener("click", function () {
    if (!currentDetail) return;
    var card = currentDetail.card;
    var payload = { id: card.id };
    if (card.kind === "llm_api") {
      var k = $("detailKey").value.trim();
      if (!k) {
        $("detailResult").textContent = "请先在密钥框里填好密钥";
        $("detailResult").className = "detail-result fail";
        return;
      }
      payload.api_key = k;
      pendingKey = k;
    } else if (!currentDetail.verified) {
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
        if (job.done) {
          $("detailCheck").disabled = false;
          $("detailRemove").disabled = false;
          if (v.state === "ok" && currentDetail && currentDetail.card.kind === "llm_api" && pendingKey) {
            saveKeyToMac(currentDetail.key_account, pendingKey);
          }
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
    var check = BlocksCore.bindRequest(form, providers);
    if (!check.ok) {
      setBindFeedback(check.error, "bad");
      return;
    }
    var rawKey = check.body.api_key;
    setChecking(true);
    setBindFeedback("", "");

    var isReconnect = currentView === "reconnect";
    var url = isReconnect ? "/api/blocks/connect" : "/api/blocks/bind";
    var payload = isReconnect ? { api_key: rawKey, check: true } : check.body;

    postJson(url, payload).then(function (res) {
      setChecking(false);
      if (res && res.ok) {
        var account = res.key_account || (state && state.key_account) || "";
        saveKeyToMac(account, rawKey);
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
    var card = $("healthCard");
    if (!activeBlock) card.hidden = false;
    $("healthRecheck").disabled = scanRunning;
    var rowsEl = $("healthRows");
    if (scanError) {
      $("healthLine").textContent = scanError;
      $("healthToggle").hidden = true;
      rowsEl.hidden = true;
      return;
    }
    var v = BlocksCore.healthView(scanRunning ? null : lastScan, scanBlocks);
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
