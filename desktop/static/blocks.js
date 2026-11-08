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
  deleteConfirm: function (thread) {
    var title = (thread && thread.title) ? String(thread.title) : "这段对话";
    return "删除对话“" + title + "”？删除后找不回来。";
  },
};

if (typeof module !== "undefined") module.exports = BlocksCore;

if (typeof document !== "undefined") (function () {
  var THREADS_MS = 1500, BLOCKS_MS = 5000;
  var params = new URLSearchParams(location.search);
  var token = params.get("token") || "";
  var state = null;
  var currentView = "bind";
  var canReturnToMain = false;
  var frameLoadedHome = "";
  var threads = [], deleted = {}, locks = {}, pendingThread = null, threadKey = "", blocksKey = "";
  var threadTimer = null, blocksTimer = null;
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
        if (!r.ok) throw new Error(d.error || "出错了，请稍后再试");
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

  function showBindCard(mode) {
    currentView = mode;
    $("bindWrap").hidden = false;
    $("mainLayout").hidden = true;
    setBindFeedback("", "");
    $("bindKey").value = "";
    $("bindBack").hidden = !canReturnToMain;

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
      $("bindTitle").textContent = "先绑定一个大模型，它会当你的行政";
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
    canReturnToMain = true;
    $("bindWrap").hidden = true;
    $("mainLayout").hidden = false;
    var home = (state && state.home) || "";
    var frame = $("chatFrame");
    if (home && frameLoadedHome !== home) {
      frame.src = BlocksCore.chatUrl(home, token);
      frameLoadedHome = home;
    }
    startLoops();
  }

  function chatApi() {
    var frame = $("chatFrame");
    try {
      return frame && frame.contentWindow && frame.contentWindow.SuperAgentChat;
    } catch (_) {
      return null;
    }
  }

  function threadTitle(title) {
    return String(title || "").replace(/（[^）]*）$/, "") || "对话";
  }

  function drawThreads() {
    var shown = (threads || []).filter(function (t) { return !deleted[t.id]; });
    var key = JSON.stringify(shown.map(function (t) {
      return [t.id, t.title, !!t.current, !!t.busy, t.deletable];
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
        '<span class="tt">' + BlocksCore.esc(threadTitle(t.title)) + "</span></button>";
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
      drawThreads();
    }).catch(function () {});
  }

  function drawBlocks(data) {
    var items = (data && data.blocks) || [];
    var broken = (data && data.broken) || [];
    var key = JSON.stringify([items, broken]);
    if (key === blocksKey) return;
    blocksKey = key;
    if (!items.length && !broken.length) {
      $("blocksList").innerHTML = '<div class="empty">还没有接进来的工具，跟行政说一声就行</div>';
      return;
    }
    var html = items.map(function (b) {
      var cls = BlocksCore.statusClass(b);
      var st = BlocksCore.statusText(b);
      return '<div class="block-item" data-block="' + BlocksCore.esc(b.id) + '">' +
        '<div class="block-top"><span class="block-name">' + BlocksCore.esc(b.name) + '</span>' +
        '<span class="block-kind">' + BlocksCore.esc(BlocksCore.kindText(b.kind)) + "</span></div>" +
        '<div class="block-status"><i class="dot ' + cls + '"></i><span>' + BlocksCore.esc(st) + "</span></div>" +
      "</div>";
    }).join("");
    html += broken.map(function (b) {
      return '<div class="block-item broken" data-broken="' + BlocksCore.esc(b.id) + '">' +
        '<div class="block-top"><span class="block-name">' + BlocksCore.esc(b.id) + '</span></div>' +
        '<div class="block-status"><i class="dot bad"></i><span>这张接法卡坏了</span></div>' +
      "</div>";
    }).join("");
    $("blocksList").innerHTML = html;
  }

  function fetchBlocks() {
    if (!state || currentView !== "main") return Promise.resolve();
    return getJson("/api/blocks/list").then(function (d) {
      drawBlocks(d);
    }).catch(function () {});
  }

  function startLoops() {
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
    showBindCard("bind");
  });

  $("bindBack").addEventListener("click", function () {
    if (canReturnToMain) showMainView();
  });

  $("settingsBtn").addEventListener("click", function () {
    showBindCard("bind");
  });

  $("bindCheck").addEventListener("click", function () {
    var btn = $("bindCheck");
    if (btn.disabled) return;
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
    btn.disabled = true;
    btn.textContent = "检测中…";
    setBindFeedback("", "");

    var isReconnect = currentView === "reconnect";
    var url = isReconnect ? "/api/blocks/connect" : "/api/blocks/bind";
    var payload = isReconnect ? { api_key: rawKey } : check.body;

    postJson(url, payload).then(function (res) {
      btn.disabled = false;
      btn.textContent = "检测";
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
      btn.disabled = false;
      btn.textContent = "检测";
      setBindFeedback(err.message || "没连上本机服务，请稍后再试", "bad");
    });
  });

  $("newChat").addEventListener("click", function () {
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
