/* 右栏：一张卡的详情。每 1.5 秒向本机数据服务要一次最新状态，并把计时、“几秒前有动静”在两次之间走起来。
   只读：这里没有任何会改动项目的操作。窗口用 window.SuperAgentPanel.show(卡片编号) 指定显示哪张卡。 */
"use strict";

/* 规则层：不碰页面，tests/nav_page_check.js 用 node 直接检查。 */
var PanelCore = {
  stepClass: function (state) { return ["done", "active", "wait", "fail"].indexOf(state) >= 0 ? state : "idle"; },
  // 去掉记录里的内部前缀，只留内容
  cleanDetail: function (text) { return String(text || "").replace(/^(汇报|结论)：/, ""); },
  // 只按 phase.actions 出按钮；行政没开时不显示
  actionButtons: function (phase, clerkOn) {
    if (!clerkOn) return [];
    var branch = (phase && phase.branch) || "";
    var labels = {
      approve: { key: "approve", label: "批准并开工" },
      discard: { key: "discard", label: "不做了", danger: true },
      replan: { key: "replan", label: "重写方案" },
      resume: { key: "resume", label: "接着跑" },
      stop: { key: "stop", label: "停下" },
      accept: { key: "accept", label: branch ? "验收通过，合并到 " + branch : "验收通过，合并" },
      revise: { key: "revise", label: "还要修改" },
    };
    var actions = (phase && phase.actions) || [];
    var out = [];
    for (var i = 0; i < actions.length; i++) {
      var spec = labels[actions[i]];
      if (spec) out.push(spec);
    }
    return out;
  },
};
if (typeof module !== "undefined") module.exports = PanelCore;

if (typeof document !== "undefined") (function () {
  var POLL_MS = 1500;
  var params = new URLSearchParams(location.search);
  var project = params.get("project") || "";
  var dispatcher = params.get("dispatcher") || "";  // 桌面窗口告诉面板：用户选的调度员是谁
  var selected = params.get("card") || null;   // 窗口指定的卡片；null 表示跟随最新的一张
  var token = params.get("token") || "";
  var clerkOn = params.get("clerk") === "1";
  var view = null, fetchedAt = 0, filterWho = null;
  var opened = {}, texts = {};
  var planCache = null;
  var actionBusy = false;

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function mmss(sec) { sec = Math.max(0, Math.round(sec)); return Math.floor(sec / 60) + ":" + (sec % 60 < 10 ? "0" : "") + (sec % 60); }
  function ago(sec) { sec = Math.max(0, Math.round(sec)); return sec < 60 ? sec + " 秒" : Math.floor(sec / 60) + " 分钟"; }
  function since() { return (Date.now() - fetchedAt) / 1000; }

  function cardHeaders() {
    var h = { "Content-Type": "application/json" };
    if (token) h["X-Chat-Token"] = token;
    return h;
  }

  function postCard(action, text) {
    var body = { project: project, card: view.card.id, action: action };
    if (text) body = { project: project, card: view.card.id, action: action, text: text };
    return fetch("/api/clerk/card", { method: "POST", headers: cardHeaders(),
      body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, status: r.status, d: d }; }); });
  }

  function phaseHint(ph) {
    if (!ph) return "";
    var parts = [ph.label || ""];
    if (ph.detail) parts.push(ph.detail);
    return parts.join(" · ");
  }

  function renderActions() {
    var bar = $("actions"), btns = $("actionBtns"), hint = $("phaseHint");
    if (!view || !view.card || !view.card.phase) { bar.hidden = true; return; }
    var ph = view.card.phase;
    var specs = PanelCore.actionButtons(ph, clerkOn);
    if (!specs.length) { bar.hidden = true; return; }
    bar.hidden = false;
    hint.textContent = phaseHint(ph);
    btns.innerHTML = specs.map(function (s) {
      return '<button type="button" data-act="' + esc(s.key) + '" class="' + (s.danger ? "danger" : "") + '"'
        + (actionBusy ? " disabled" : "") + ">" + esc(s.label) + "</button>";
    }).join("");
  }

  $("reviseSend").addEventListener("click", function () {
    if (!view || !view.card || actionBusy) return;
    var words = ($("reviseText").value || "").trim();
    if (!words) return;
    actionBusy = true;
    renderActions();
    postCard("revise", words).then(function (res) {
      if (!res.ok && res.d && res.d.error) alert(res.d.error);
      else { $("reviseText").value = ""; $("reviseBox").hidden = true; }
      actionBusy = false;
      poll();
    }).catch(function () {
      actionBusy = false;
      renderActions();
    });
  });

  $("actions").addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-act]");
    if (!b || !view || !view.card || actionBusy) return;
    var act = b.getAttribute("data-act");
    if (act === "revise") {
      var box = $("reviseBox");
      if (box) box.hidden = false;
      var field = $("reviseText");
      if (field) field.focus();
      return;
    }
    if (act === "discard" && !window.confirm("确定标记为「不做了」？这张卡会停下来。")) return;
    actionBusy = true;
    renderActions();
    postCard(act).then(function (res) {
      if (!res.ok && res.d && res.d.error) alert(res.d.error);
      actionBusy = false;
      poll();
    }).catch(function () {
      actionBusy = false;
      renderActions();
    });
  });

  function renderPlan() {
    var sec = $("planSection"), show = view && view.card && view.card.phase && view.card.phase.code === "plan_ready";
    if (!show) { sec.hidden = true; return; }
    sec.hidden = false;
    if (!planCache) return;
    $("planText").textContent = planCache.plan || "";
    $("planAllowed").textContent = (planCache.allowed || []).join("\n");
    $("planTest").textContent = planCache.test_command || "";
    var blk = $("profileBlock"), prev = $("planProfile");
    if (planCache.new_profile && planCache.profile_text) {
      blk.hidden = false;
      prev.textContent = planCache.profile_text;
    } else {
      blk.hidden = true;
      prev.textContent = "";
    }
  }

  // ---------- 流程：7 步先搭好，之后只改内容 ----------
  var GLYPH = {
    done: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"></path></svg>',
    fail: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"></path></svg>',
    active: '<i class="spin"></i>', wait: '<i class="pip"></i>', idle: '<i class="pip"></i>',
  };
  var steps = [];
  for (var i = 0; i < 7; i++) {
    var li = document.createElement("li");
    li.innerHTML = '<button class="step idle" type="button"><span class="g"></span><span class="t"></span>' +
      '<span class="w"></span><span class="d"></span></button>';
    (function (idx) {
      li.querySelector("button").addEventListener("click", function () {
        var s = view && view.stations[idx];
        if (s) showLog(s.key === "check" || s.key === "you" || s.key === "accept" ? null : s.who);
      });
    })(i);
    $("steps").appendChild(li);
    steps.push(li.querySelector("button"));
  }

  function stepText(s) {
    var parts = [PanelCore.cleanDetail(s.detail)];
    if (s.state === "active" && s.elapsed != null) parts.push("已干 " + mmss(s.elapsed + since()));
    if (s.state === "active" && s.quiet != null) parts.push(ago(s.quiet + since()) + "前有动静");
    return parts.filter(Boolean).join(" · ");
  }

  function renderFlow(v) {
    v.stations.forEach(function (s, idx) {
      var b = steps[idx], kind = PanelCore.stepClass(s.state);
      var cls = "step " + kind + (s.state === "active" && v.stale ? " stale" : "");
      if (b.className !== cls) { b.className = cls; b.querySelector(".g").innerHTML = GLYPH[kind]; }
      b.querySelector(".t").textContent = s.role;
      b.querySelector(".w").textContent = s.who + (v.back && s.key === "dev" ? " · 退回返工" : "");
      b.querySelector(".d").textContent = kind === "idle" ? "" : stepText(s);
      b.title = PanelCore.cleanDetail(s.detail);
      b.setAttribute("aria-label", s.role + " " + s.who + "，看交接");
    });
  }

  // ---------- 没有卡片时 ----------
  function renderShell(v) {
    $("side").classList.toggle("nocard", !v.card);
    $("blank").hidden = !!v.card;
    $("blank").textContent = v.installed ? "还没有卡片" : "这个文件夹还没接入";
    $("count").textContent = v.handoffs.length;
  }

  function tick() {
    if (!view) return;
    view.stations.forEach(function (s, idx) {
      if (s.state === "active") steps[idx].querySelector(".d").textContent = stepText(s);
    });
  }

  // ---------- 交接 ----------
  function drawList() {
    if (!view) return;
    var list = view.handoffs.filter(function (h) {
      return !filterWho || h.from.indexOf(filterWho) >= 0 || h.to.indexOf(filterWho) >= 0;
    });
    $("filter").innerHTML = filterWho
      ? "只看 " + esc(filterWho) + ' <button class="btn" type="button" id="clearFilter">看全部</button>'
      : "全部交接，最新的在最下面；点一条看全文";
    var clear = $("clearFilter");
    if (clear) clear.addEventListener("click", function () { showLog(null); });
    if (!list.length) { $("list").innerHTML = '<li class="empty">还没有交接记录</li>'; return; }
    var key = view.card ? view.card.id : "";
    $("list").innerHTML = list.map(function (h) {
      var id = key + "#" + h.seq, open = opened[id];
      var body = open ? "<pre>" + esc(texts[id] == null ? "读取中…" : texts[id]) + "</pre>" : "";
      return '<li><button type="button" data-seq="' + h.seq + '" aria-expanded="' + (!!open) + '">' +
        '<div class="m"><span>' + esc(h.time) + "</span>" + esc(h.from) + (h.to ? " → " + esc(h.to) : "") + "</div>" +
        '<div class="s ' + esc(h.tone) + '">' + esc(h.summary) + "</div>" + body + "</button></li>";
    }).join("");
  }
  $("list").addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-seq]");
    if (!b || !view || !view.card) return;
    var seq = b.getAttribute("data-seq"), id = view.card.id + "#" + seq;
    opened[id] = !opened[id];
    if (opened[id] && texts[id] == null) {
      fetch("/api/text?project=" + encodeURIComponent(project) + "&card=" + encodeURIComponent(view.card.id) + "&seq=" + seq)
        .then(function (r) { return r.json(); })
        .then(function (d) { texts[id] = d.text != null ? d.text : (d.error || "读不到全文"); drawList(); })
        .catch(function () { texts[id] = "读不到全文"; drawList(); });
    }
    drawList();
    var again = $("list").querySelector('button[data-seq="' + seq + '"]');
    if (again) again.focus();
  });

  // ---------- 标签页 ----------
  var tabs = ["flow", "log"];
  function select(name) {
    tabs.forEach(function (t) {
      $("tab-" + t).setAttribute("aria-selected", String(t === name));
      $("pane-" + t).hidden = t !== name;
    });
    if (name === "log") drawList();
  }
  function showLog(who) { filterWho = who; select("log"); }
  tabs.forEach(function (t) {
    $("tab-" + t).addEventListener("click", function () { if (t === "log") showLog(null); else select(t); });
  });

  // ---------- 取数据 ----------
  var lastLogKey = "";
  function poll() {
    var asked = selected;
    var url = "/api/view?project=" + encodeURIComponent(project) + "&dispatcher=" + encodeURIComponent(dispatcher) +
      (asked ? "&card=" + encodeURIComponent(asked) : "");
    return fetch(url).then(function (r) { return r.json(); }).then(function (v) {
      if (asked !== selected) return;   // 取的时候又换了卡，这一份作废
      $("offline").hidden = true;
      if (v.error) { $("blank").hidden = false; $("blank").textContent = v.error; return; }
      view = v; fetchedAt = Date.now();
      renderShell(v); renderFlow(v); tick();
      renderActions();
      var needPlan = v.card && v.card.phase && v.card.phase.code === "plan_ready";
      var cid = v.card && v.card.id;
      if (needPlan && cid && token) {
        fetch("/api/plan?" + new URLSearchParams({ project: project, card: cid, token: token }))
          .then(function (r) { return r.json(); })
          .then(function (d) {
            if (!d || d.error) return;
            planCache = d;
            renderPlan();
          })
          .catch(function () {});
      } else {
        planCache = null;
        renderPlan();
      }
      var logKey = (v.card ? v.card.id : "") + ":" + v.handoffs.length;
      if (!$("pane-log").hidden && logKey !== lastLogKey) drawList();
      lastLogKey = logKey;
    }).catch(function () { $("offline").hidden = false; });
  }
  function loop() { poll().then(function () { setTimeout(loop, POLL_MS); }); }

  window.SuperAgentPanel = {
    show: function (id) {
      if ((id || null) === selected) return;
      selected = id || null;
      $("reviseBox").hidden = true;   // 没提交的修改不能发到另一张卡
      $("reviseText").value = "";
      filterWho = null;
      select("flow"); poll();
    },
  };

  if (!project) {
    $("side").classList.add("nocard");
    $("blank").hidden = false;
    $("blank").textContent = "没有指定项目";
  } else {
    loop();
    setInterval(tick, 500);
  }
})();
