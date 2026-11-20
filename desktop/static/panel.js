/* 右栏：一张卡的详情。每 1.5 秒向本机数据服务要一次最新状态，并把计时、“几秒前有动静”在两次之间走起来。
   只读：这里没有任何会改动项目的操作。窗口用 window.SuperAgentPanel.show(卡片编号) 指定显示哪张卡。 */
"use strict";

/* 规则层：不碰页面，tests/nav_page_check.js 用 node 直接检查。 */
function tokenThousands(n) {
  return String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}
// 不到 1 万写原数（千分位），1 万到 1 亿写“N.N 万”，1 亿以上写“N.N 亿”
function tokenAmount(n) {
  n = Number(n) || 0;
  if (n >= 100000000) return (n / 100000000).toFixed(1) + " 亿";
  if (n >= 10000) return (n / 10000).toFixed(1) + " 万";
  return tokenThousands(n);
}
function mmss(sec) { sec = Math.max(0, Math.round(sec)); return Math.floor(sec / 60) + ":" + (sec % 60 < 10 ? "0" : "") + (sec % 60); }
function ago(sec) { sec = Math.max(0, Math.round(sec)); return sec < 60 ? sec + " 秒" : Math.floor(sec / 60) + " 分钟"; }
var PanelCore = {
  stepClass: function (state) { return ["done", "active", "wait", "fail"].indexOf(state) >= 0 ? state : "idle"; },
  // 去掉记录里的内部前缀，只留内容
  cleanDetail: function (text) { return String(text || "").replace(/^(汇报|结论)：/, ""); },
  // 只按 phase.actions 出按钮；行政没开时不显示
  actionButtons: function (phase, clerkOn) {
    if (!clerkOn) return [];
    var labels = {
      approve: { key: "approve", label: "批准并开工" },
      discard: { key: "discard", label: "放弃这个需求", danger: true },
      replan: { key: "replan", label: "重写方案" },
      resume: { key: "resume", label: "接着跑" },
      stop: { key: "stop", label: "停下" },
      accept: { key: "accept", label: "确认没问题，放进项目" },
      revise: { key: "revise", label: "不满意，继续改" },
      accept_issues: { key: "accept_issues", label: "接受剩下的问题，进入待确认" },
    };
    var actions = (phase && phase.actions) || [];
    var out = [];
    for (var i = 0; i < actions.length; i++) {
      var spec = labels[actions[i]];
      if (spec) out.push(spec);
    }
    return out;
  },
  // 写测试、写代码那步的进度（第四轮 53）：{passed,total} → 条 + “通过 x/y”；{written} → “已写 N 项”
  progressText: function (p) {
    if (!p) return null;
    if (p.written != null) return { text: "已写 " + p.written + " 项", pct: null };
    if (p.error) return { text: "进度算不出来（" + p.error + "）", pct: null };
    if (p.total != null && p.total > 0) {
      var words = p.own ? "本卡要做的 " + p.total + " 项，已过 " + p.passed + " 项"
        : "通过 " + p.passed + "/" + p.total;
      return { text: words, pct: Math.floor(100 * p.passed / p.total) };   // 只舍不入：没全过不显示满格（D2 复查第 3 轮）
    }
    if (p.computing || p.total === 0) return { text: "正在算通过了几项…", pct: 0 };
    return null;
  },
  // 继续改框：用户还没写字时，填上调度员起草好的话（第四轮 56 甲）；写了就不动
  reviseDraft: function (current, draft) {
    return String(current || "").trim() ? current : String(draft || "");
  },
  // 状态那一段：轮数、计时、动静，缺哪段就不写哪段；首次绘制和定时刷新都用这一份
  statusText: function (s, since) {
    if (!s || s.state === "idle") return "";
    var parts = [], gap = since || 0;
    if (s.state === "active") {
      if (s.rounds > 1) parts.push("第 " + s.rounds + " 轮");
      if (s.elapsed != null) parts.push("已干 " + mmss(s.elapsed + gap));
      if (s.quiet != null) parts.push(ago(s.quiet + gap) + "前有动静");
    } else if (s.state === "done" || s.state === "fail") {
      if (s.rounds > 1) parts.push("共 " + s.rounds + " 轮");
      if (s.spent != null) parts.push(mmss(s.spent));
    }
    return parts.join(" · ");
  },
  // 用量那一段的字样（零用量、读不到、等着出数都照原规则）
  usagePart: function (s, amount) {
    var t = s && s.tokens;
    if (t == null) return "";
    if (t.pending) {
      var pendingText = t.reason || "这一轮结束后出数";
      if (t.missing > 0) pendingText += "（有 " + t.missing + " 轮没读到）";
      return pendingText;
    }
    var usage = t.ok ? amount(t.total) + " token"
      : "用量 —（" + (t.reason || "读不到") + "）";
    if (t.ok && t.missing > 0) usage += "（有 " + t.missing + " 轮没读到）";
    return usage;
  },
  // 用量单独一行：字样和原来一样（万、亿），只是换到单独一行；零用量等规则不变
  usageText: function (s) {
    if (!s || s.state === "idle") return "";
    return PanelCore.usagePart(s, tokenAmount);
  },
  // 合成一行（沿用原写法给无障碍说明）：轮数、计时、动静、用量；输出和以前一字不差，老检查不动
  metaText: function (s, since) {
    if (!s || s.state === "idle") return "";
    return [PanelCore.statusText(s, since), PanelCore.usagePart(s, tokenAmount)]
      .filter(Boolean).join(" · ");
  },
  // 鼠标提示：说明全文、每轮明细（多轮才列）、用量明细
  stepTitle: function (s) {
    if (!s) return "";
    var lines = [], detail = PanelCore.cleanDetail(s.detail);
    if (detail) lines.push(detail);
    if (s.rounds > 1 && s.history != null && s.history.length) {
      for (var i = 0; i < s.history.length; i++) {
        var h = s.history[i] || {}, segs = [];
        if (h.who) segs.push(h.who);
        if (h.seconds != null) segs.push(mmss(h.seconds));
        if (h.tokens != null) segs.push(tokenAmount(h.tokens) + " token");
        else if (h.missing) segs.push("用量 —（" + (h.reason || "读不到") + "）");
        lines.push("第 " + (i + 1) + " 轮" + (segs.length ? " " + segs.join(" · ") : ""));
      }
    }
    var tip = PanelCore.tokenTitle(s.tokens);
    if (tip) lines.push(tip);
    return lines.join("\n");
  },
  // 鼠标提示里的明细：输入、缓存读取、输出、新消耗（不含缓存读取）
  tokenTitle: function (t) {
    if (!t || !t.ok) return "";
    var input = Number(t.input) || 0, cached = Number(t.cached) || 0, output = Number(t.output) || 0;
    return "输入 " + tokenAmount(input) + "（其中缓存读取 " + tokenAmount(cached) + "）· 输出 " +
      tokenAmount(output) + " · 新消耗 " + tokenAmount(input - cached + output) + "（不含缓存读取）";
  },
  // 环节列表下面的本卡合计；细项进鼠标提示
  totalText: function (v) {
    if (!v || (!v.total && !(v.missing > 0))) return "";
    var text = "本卡合计 " + tokenAmount(v.total || 0) + " token";
    if (v.missing > 0) text += "（不含没读到的 " + v.missing + " 轮）";
    return text;
  },
  totalTitle: function (v) {
    if (!v || !(v.other > 0)) return "";
    return "其中交接资料等其他 " + tokenAmount(v.other) + " token";
  },
  // 本卡合计下面那行完成时间：按电脑本地时间写成“完成于 月-日 时:分”
  doneText: function (ts) {
    if (ts == null || ts === "" || !isFinite(Number(ts))) return "";
    var when = new Date(Number(ts) * 1000);
    if (isNaN(when.getTime())) return "";
    function pad(n) { return (n < 10 ? "0" : "") + n; }
    return "完成于 " + pad(when.getMonth() + 1) + "-" + pad(when.getDate()) + " "
      + pad(when.getHours()) + ":" + pad(when.getMinutes());
  },
  // 点“接受剩下的问题”前给用户看的话：剩下什么、接受后还要再点什么
  acceptIssuesConfirm: function (detail) {
    return "复查员还剩这些问题：\n" + String(detail || "（没有写明）") +
      "\n\n接受后卡片进入待确认，你还要再点“确认没问题，放进项目”才会放进项目。要接受吗？";
  },
  startDialogText: function (count) {
    return count > 0 ? "有 " + count + " 处改动还没存进项目，施工会看不到它们。" : null;
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
  var startRequest = 0, startPending = null;
  var previewBusy = false;
  var previewFor = "";   // 正在打开预览的是哪张卡：切卡后回来的结果不能算到新卡头上
  var previewClosing = false;
  var previewRunning = false;

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function since() { return (Date.now() - fetchedAt) / 1000; }

  function cardHeaders() {
    var h = { "Content-Type": "application/json" };
    if (token) h["X-Chat-Token"] = token;
    return h;
  }

  function postCard(action, text, cardId, files, extraFields) {
    var card = cardId || view.card.id;
    var body = { project: project, card: card, action: action };
    if (text) body.text = text;
    if (files && files.length) body.files = files;   // “继续改”加的文件（第三批 ㉟）
    if (extraFields && typeof extraFields === "object") {
      Object.keys(extraFields).forEach(function (k) {
        if (extraFields[k] !== undefined) body[k] = extraFields[k];
      });
    }
    return fetch("/api/clerk/card", { method: "POST", headers: cardHeaders(),
      body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, status: r.status, d: d }; }); });
  }

  function phaseHint(ph) {
    if (!ph) return "";
    // 停下时右栏顶部标明原因在哪看，原因全文还在中间卡片标题下（用户 11-20）
    if (ph.code === "blocked") return "卡住了（原因在中间卡片标题下）";
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
    var rawText = $("reviseText").value || "";
    var rawFiles = $("reviseFiles").value || "";
    var words = rawText.trim();
    if (!words) return;
    var cardId = view.card.id;
    var files = rawFiles.split(/[,，、\n]/).map(function (x) { return x.trim(); })
      .filter(Boolean);
    actionBusy = true;
    renderActions();
    postCard("prepare_revise", words, cardId).then(function (res) {
      if (!res.ok || !res.d || !res.d.ok) {
        alert((res.d && res.d.error) || "拆分修改要求失败，请重试");
        actionBusy = false;
        renderActions();
        return;
      }
      var criteriaBase = Array.isArray(res.d.criteria_base) ? res.d.criteria_base : [];
      var criteriaChanges = Array.isArray(res.d.criteria_changes) ? res.d.criteria_changes : [];
      var summary = String(res.d.summary || "").trim();
      if (criteriaChanges.length > 0 && !window.confirm(summary || "确认按以上改动继续吗？")) {
        actionBusy = false;
        renderActions();
        return;
      }
      return postCard("revise", words, cardId, files, {
        criteria_base: criteriaBase,
        criteria_changes: criteriaChanges,
      }).then(function (res2) {
        if (!res2.ok || (res2.d && res2.d.error)) {
          alert((res2.d && res2.d.error) || "提交修改失败，请重试");
        } else {
          if (($("reviseText").value || "") === rawText) $("reviseText").value = "";
          if (($("reviseFiles").value || "") === rawFiles) $("reviseFiles").value = "";
          if (!($("reviseText").value || "").trim() && !($("reviseFiles").value || "").trim()) {
            $("reviseBox").hidden = true;
          }
        }
        actionBusy = false;
        poll();
      });
    }).catch(function () {
      alert("操作失败，请检查服务后重试");
      actionBusy = false;
      renderActions();
    });
  });

  function runCardAction(act, cardId) {
    actionBusy = true;
    renderActions();
    postCard(act, "", cardId).then(function (res) {
      if (!res.ok && res.d && res.d.error) alert(res.d.error);
      actionBusy = false;
      poll();
    }).catch(function () {
      actionBusy = false;
      renderActions();
    });
  }

  function startButtons(disabled) {
    ["startSave", "startGo", "startCancel"].forEach(function (id) { $(id).disabled = disabled; });
  }

  function closeStartDialog() {
    startRequest += 1;  // 切卡或取消后，旧的清单和失败回包不能再弹框
    startPending = null;
    $("startBox").hidden = true;
    startButtons(false);
    actionBusy = false;
    renderActions();
  }

  $("startCancel").addEventListener("click", closeStartDialog);
  $("startGo").addEventListener("click", function () {
    if (!startPending) return;
    var cardId = startPending.card;
    closeStartDialog();
    runCardAction("approve", cardId);
  });
  $("startSave").addEventListener("click", function () {
    if (!startPending) return;
    var pending = startPending, request = startRequest;
    startButtons(true);
    function failed(reason) {
      if (request !== startRequest) return;
      $("startText").textContent = PanelCore.startDialogText(pending.count) + "\n" + reason;
      startButtons(false);
    }
    fetch("/api/clerk/save_pending", { method: "POST", headers: cardHeaders(),
      body: JSON.stringify({ project: project, card: pending.card }) })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok || !res.d || !res.d.ok) {
          failed((res.d && res.d.reason) || "没存成：请重试");
          return;
        }
        if (request === startRequest) closeStartDialog();
        runCardAction("approve", pending.card);
      }).catch(function () { failed("没存成：请检查服务后重试"); });
  });

  $("actions").addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-act]");
    if (!b || !view || !view.card || actionBusy) return;
    var act = b.getAttribute("data-act");
    if (act === "revise") {
      var box = $("reviseBox");
      if (box) box.hidden = false;
      var field = $("reviseText");
      if (field) {
        field.value = PanelCore.reviseDraft(field.value, view.draft);
        field.focus();
      }
      return;
    }
    if (act === "discard" && !window.confirm("这次的改动不会进入项目，你的项目不会变。改好的版本也会删除。")) return;
    if (act === "accept_issues" && !window.confirm(PanelCore.acceptIssuesConfirm(view.card.phase && (view.card.phase.issues || view.card.phase.detail)))) return;
    var cardId = view.card.id;   // 问的时候可能切了卡：批准的必须是点的那张
    if (act === "approve") {
      actionBusy = true;
      renderActions();
      var request = ++startRequest;
      fetch("/api/clerk/uncommitted?project=" + encodeURIComponent(project) + "&card=" + encodeURIComponent(cardId) + "&token=" + encodeURIComponent(token))
        .then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; })
        .then(function (d) {
          if (request !== startRequest) return;
          var files = d && Array.isArray(d.files) ? d.files : [];
          if (files.length) {
            startPending = { card: cardId, count: files.length };
            $("startText").textContent = PanelCore.startDialogText(files.length);
            var shown = files.slice(0, 10);
            if (files.length > 10) shown.push("还有 " + (files.length - 10) + " 个");
            $("startFiles").textContent = shown.join("\n");
            startButtons(false);
            $("startBox").hidden = false;
            return;
          }
          runCardAction(act, cardId);
        });
      return;
    }
    runCardAction(act, cardId);
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

  function previewIsBusyHere() {
    return previewBusy && !!view && !!view.card && view.card.id === previewFor;
  }

  function previewLabel() {
    if (!view || !view.accept) return "打开文件夹";
    if (previewClosing) return "正在关闭…";
    if (previewIsBusyHere()) return "正在打开…";
    if (previewRunning) return "关闭预览";
    return view.accept.can_preview ? "打开改好的版本" : "打开文件夹";
  }

  function renderAccept() {
    var box = $("acceptBox");
    if (!view || !view.accept || !clerkOn) { box.hidden = true; return; }   // 没开行政时没有确认按钮
    box.hidden = false;
    $("acceptResult").textContent = view.accept.result || "";
    $("acceptHow").textContent = view.accept.how || "";
    var btn = $("previewBtn");
    btn.hidden = view.accept.copy_there === false;   // 副本不在了，点了也打不开
    btn.textContent = previewLabel();
    btn.disabled = previewIsBusyHere() || previewClosing;
  }

  function showPreviewError(reason, path) {
    var err = $("previewErr");
    err.hidden = false;
    err.textContent = "";
    err.appendChild(document.createTextNode(reason || "没打开"));
    if (path) {
      var a = document.createElement("a");
      a.href = "superagent://clerk-open?project=" + encodeURIComponent(project) + "&path=" + encodeURIComponent(path);
      a.textContent = "打开文件夹";
      err.appendChild(a);
    }
  }

  function refreshPreviewStatus() {
    if (!view || !view.card || !view.accept || !token) return;
    var q = new URLSearchParams({ project: project, card: view.card.id, token: token });
    fetch("/api/clerk/preview?" + q.toString()).then(function (r) { return r.json(); }).then(function (d) {
      previewRunning = !!(d && d.running);
      renderAccept();
    }).catch(function () {});
  }

  $("previewBtn").addEventListener("click", function () {
    if (!view || !view.card || !view.accept || previewIsBusyHere() || previewClosing) return;
    $("previewErr").hidden = true;
    if (previewRunning) {
      previewClosing = true;
      renderAccept();
      fetch("/api/clerk/preview", { method: "POST", headers: cardHeaders(),
        body: JSON.stringify({ project: project, card: view.card.id, action: "stop" }) })
        .then(function () { previewRunning = false; previewClosing = false; renderAccept(); })
        .catch(function () { previewClosing = false; renderAccept(); });
      return;
    }
    previewBusy = true;
    previewFor = view.card.id;
    var startedFor = previewFor;
    renderAccept();
    fetch("/api/clerk/preview", { method: "POST", headers: cardHeaders(),
      body: JSON.stringify({ project: project, card: startedFor, action: "start" }) })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (startedFor !== previewFor) return;   // 之后又开了别的卡：这个结果已经没用了
        previewBusy = false;
        var d = res.d || {};
        if (!view || !view.card || view.card.id !== startedFor) { renderAccept(); return; }
        if (d.ok && d.mode === "web" && d.url) {
          previewRunning = true;
          location.href = "superagent://clerk-open?project=" + encodeURIComponent(project)
            + "&url=" + encodeURIComponent(d.url);
        } else if (d.ok && d.mode === "folder" && d.path) {
          previewRunning = false;
          location.href = "superagent://clerk-open?project=" + encodeURIComponent(project)
            + "&path=" + encodeURIComponent(d.path);
        } else {
          previewRunning = false;
          showPreviewError(d.reason || "没打开", d.path || "");
        }
        renderAccept();
      })
      .catch(function () {
        if (startedFor !== previewFor) return;
        previewBusy = false;
        showPreviewError("没打开", "");
        renderAccept();
      });
  });

  // ---------- 流程：8 个位置先搭好（有接手的卡多一格），之后只改内容 ----------
  var GLYPH = {
    done: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"></path></svg>',
    fail: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"></path></svg>',
    active: '<i class="spin"></i>', wait: '<i class="pip"></i>', idle: '<i class="pip"></i>',
  };
  var steps = [], slots = [];
  for (var i = 0; i < 8; i++) {
    var li = document.createElement("li");
    li.hidden = true;   // 拿到数据前先藏着，免得空行闪一下
    li.innerHTML = '<button class="step idle" type="button"><span class="g"></span><span class="t"></span>' +
      '<span class="w"></span><span class="p" hidden><i class="bar"><i class="fill"></i></i><span class="pt"></span></span>' +
      '<span class="d"></span><span class="k"></span><span class="u"></span></button>';
    (function (idx) {
      li.querySelector("button").addEventListener("click", function () {
        var s = view && view.stations[idx];
        if (s) showLog(s.key === "check" || s.key === "you" || s.key === "accept" ? null : s.who);
      });
    })(i);
    $("steps").appendChild(li);
    slots.push(li);
    steps.push(li.querySelector("button"));
  }

  function stepText(s) {
    return PanelCore.cleanDetail(s.detail);
  }

  // 没这张卡的这一格：藏起来，并把上一张卡留下的字清干净
  function clearStep(b, slot) {
    if (slot) slot.hidden = true;
    b.className = "step idle";
    b.querySelector(".g").innerHTML = GLYPH.idle;
    b.querySelector(".t").textContent = "";
    b.querySelector(".w").textContent = "";
    b.querySelector(".d").textContent = "";
    b.querySelector(".k").textContent = "";
    b.querySelector(".u").textContent = "";
    b.querySelector(".p").hidden = true;
    b.title = "";
    b.setAttribute("aria-label", "");
  }

  function renderFlow(v) {
    steps.forEach(function (b, idx) {
      var s = v.stations[idx];
      if (!s) { clearStep(b, slots[idx]); return; }
      if (slots[idx]) slots[idx].hidden = false;
      var kind = PanelCore.stepClass(s.state);
      var cls = "step " + kind + (s.state === "active" && v.stale ? " stale" : "");
      if (b.className !== cls) { b.className = cls; b.querySelector(".g").innerHTML = GLYPH[kind]; }
      b.querySelector(".t").textContent = s.role;
      // 工具 · 模型（强度）（第二轮 ㉗ ㊳）；点步骤看交接仍按工具名 s.who 找
      b.querySelector(".w").textContent = s.who + (s.model ? " · " + s.model : "") + (v.back && s.key === "dev" ? " · 退回返工" : "");
      var prog = kind === "active" ? PanelCore.progressText(s.progress) : null;
      var pbox = b.querySelector(".p");
      pbox.hidden = !prog;
      if (prog) {
        pbox.querySelector(".bar").hidden = prog.pct == null;
        pbox.querySelector(".fill").style.width = (prog.pct || 0) + "%";
        pbox.querySelector(".pt").textContent = prog.text;
      }
      b.querySelector(".d").textContent = kind === "idle" ? "" : stepText(s);
      b.querySelector(".k").textContent = kind === "idle" ? "" : PanelCore.statusText(s, since());
      b.querySelector(".u").textContent = kind === "idle" ? "" : PanelCore.usageText(s);
      var note = s.tokens != null ? "每答完一轮才更新一次" : "";
      b.title = [PanelCore.stepTitle(s), note].filter(Boolean).join("\n");
      var spoken = PanelCore.metaText(s, since());
      b.setAttribute("aria-label", s.role + " " + s.who + (spoken ? "，" + spoken : "") + "，看交接");
    });
    var total = PanelCore.totalText(v.tokens);
    var totalBox = $("tokenTotal");
    if (totalBox) {
      totalBox.textContent = total;
      totalBox.title = total
        ? [PanelCore.totalTitle(v.tokens), "每答完一轮才更新一次"].filter(Boolean).join("\n")
        : PanelCore.totalTitle(v.tokens);
      totalBox.hidden = !total;
    }
    var doneBox = $("doneAt");
    if (doneBox) {
      var done = PanelCore.doneText(v.finished_at);
      doneBox.textContent = done;
      doneBox.hidden = !done;
    }
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
      if (s.state === "active") {
        // 和首次绘制走同一个拼法，不能在这里另拼一套
        steps[idx].querySelector(".k").textContent = PanelCore.statusText(s, since());
      }
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
  var shownCard = null;
  function poll() {
    var asked = selected;
    var url = "/api/view?project=" + encodeURIComponent(project) + "&dispatcher=" + encodeURIComponent(dispatcher) +
      (asked ? "&card=" + encodeURIComponent(asked) : "");
    return fetch(url).then(function (r) { return r.json(); }).then(function (v) {
      if (asked !== selected) return;   // 取的时候又换了卡，这一份作废
      $("offline").hidden = true;
      if (v.error) { $("blank").hidden = false; $("blank").textContent = v.error; return; }
      view = v; fetchedAt = Date.now();
      var cardNow = v.card ? v.card.id : null;
      if (cardNow !== shownCard) {   // 自动跟到另一张卡时，上一张卡的开工小框不能留着
        closeStartDialog();
        shownCard = cardNow;
      }
      renderShell(v); renderFlow(v); tick();
      renderActions();
      renderAccept();
      refreshPreviewStatus();
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
      closeStartDialog();
      $("reviseBox").hidden = true;   // 没提交的修改不能发到另一张卡
      $("reviseText").value = "";
      $("reviseFiles").value = "";
      $("previewErr").hidden = true;
      previewRunning = false;
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
