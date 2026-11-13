/* 团队与模型配置页：选调度员和三位执行员 + 各家工具具体使用的模型 → 每行各自“检测生效”或直接保存 → 通知窗口切到对话。
   调度员没有默认值：第一次必须用户亲手选。 */
"use strict";

/* 规则层：不碰页面，tests/team_page_check.js 用 node 直接检查。 */
var TeamCore = {
  // 选“不复查”的那行不用测
  needsCheck: function (team, role) { return !(role === "reviewer" && team.reviewer === "none"); },
  // 这一行和哪些行一起测：同一家工具、同样的模型和强度（每个岗位各选各的，流程修理 ①）；不给 picks 时按同一家算
  rolesSharingTool: function (team, role, active, picks) {
    if (!TeamCore.needsCheck(team, role)) return [];
    var key = TeamCore.pickKey(team, role, picks);
    return active.filter(function (r) {
      return team[r] === team[role] && TeamCore.needsCheck(team, r) && TeamCore.pickKey(team, r, picks) === key;
    });
  },
  pickKey: function (team, role, picks) {
    var p = picks && picks[role];
    if (!p || p.tool !== team[role]) return String(team[role]);
    return [p.tool, p.model || "", p.effort || ""].join("|");
  },
  // 没保存的改动该恢复哪几行：用户真改过（now 和 base 不同），并且之后没人动过这一行（现在存着的还等于 base）。
  // 不能整份恢复：期间行政帮忙改过、保存过的行会被盖回去（复查第 1 轮）
  draftRows: function (saved, draft) {
    var out = {};
    if (!draft || !draft.base || !draft.now || !draft.now.team) return out;
    var b = draft.base, n = draft.now, s = saved || {};
    ["dispatcher", "tester", "developer", "reviewer"].forEach(function (r) {
      function row(x) { return JSON.stringify([x && x.team && x.team[r], (x && x.picks && x.picks[r]) || null]); }
      if (!(r in n.team)) return;
      if (row(n) !== row(b) && row(s) === row(b)) out[r] = { tool: n.team[r], pick: (n.picks && n.picks[r]) || null };
    });
    return out;
  },
  // 团队有改动却存不了时，说清是谁还没测通（68b 前用户点保存，设置悄悄丢了，②）
  unsavedReason: function (s) {
    var names = { dispatcher: "调度员", tester: "写测试", developer: "写代码", reviewer: "复查" };
    if (!s.team.dispatcher) return "还没选调度员";
    var need = s.active.filter(function (r) { return TeamCore.needsCheck(s.team, r); });
    var bad = need.filter(function (r) { return s.results[r] && !s.results[r].ok; });
    if (bad.length) return bad.map(function (r) { return names[r]; }).join("、") + "没测通";
    var unchecked = need.filter(function (r) { return !(s.results[r] && s.results[r].ok); });
    if (!s.configured && unchecked.length) return unchecked.map(function (r) { return names[r]; }).join("、") + "还没检测";
    return "";
  },
  // 第一次配置要全员测通；已配置的项目没测的不拦，只拦标红的
  canSave: function (s) {
    if (s.busy || !s.team.dispatcher) return false;
    var need = s.active.filter(function (r) { return TeamCore.needsCheck(s.team, r); });
    if (need.some(function (r) { return s.results[r] && !s.results[r].ok; })) return false;
    return s.configured || need.every(function (r) { return s.results[r] && s.results[r].ok; });
  },
};
if (typeof module !== "undefined") module.exports = TeamCore;

if (typeof document !== "undefined") (function () {
  var params = new URLSearchParams(location.search);
  var project = params.get("project") || "", token = params.get("token") || "";
  var savedDispatcher = params.get("dispatcher") || "";   // 没接入的文件夹，调度员存在窗口自己的设置里
  var clerkReady = params.get("clerk") === "1";
  var ROLE = { dispatcher: "调度员", tester: "写测试", developer: "写代码", reviewer: "复查" };
  var ALL_ROLES = ["dispatcher", "tester", "developer", "reviewer"];
  var rows = {}, info = null, results = {}, installed = true;
  var pending = {}, runSeq = 0;   // pending：岗位 -> 正在等第几次检测的结果；改了这一行就不再等
  var currentModels = { codex: "gpt-6.1-sol", codex_effort: "xhigh", cursor: "auto", agy: "gemini-4-argon-high", qwen: "deepseek-v4.1-flash" };  // 每家工具的整体设置（岗位没单独选时用它）
  var picks = {};          // 岗位 -> {tool, model, effort}：每个岗位各选各的（流程修理 ①）
  var savedSnapshot = "";  // 打开页面时存着的团队和岗位模型：用来判断有没有没保存的改动
  var checkTimeout = 120;
  var modelOptions = { codex: [], codex_effort: [], cursor: [], agy: [], qwen: [] };
  var providers = [];
  var clerkBaseline = "";
  var clerkWaiting = false;
  var clerkStopped = false;
  var clerkSent = "";       // 正在保存的那份选择：保存成功后以它为准，不以输入框当前内容为准
  var clerkCheckFor = "";   // 正在检测的那份选择：改了就不再要这次的结果
  document.querySelectorAll(".member").forEach(function (m) {
    var role = m.getAttribute("data-role");
    if (role && role !== "clerk") rows[role] = m;
  });
  function $(id) { return document.getElementById(id); }

  function clerkChoice() {
    var provider = $("clerkProvider").value || "tencent";
    var item = providers.find(function (p) { return p.id === provider; });
    var base = provider === "other"
      ? ($("clerkAddress").value || "").trim()
      : ((item && item.base_url) || "");
    return { provider: provider, base_url: base, model: ($("clerkModel").value || "").trim() };
  }
  function clerkSnapshot(c) {
    return [c.provider, c.base_url, c.model].join("\n");
  }
  // 保存期间锁住这一行：否则删密钥、停用、改模型都会被测试回来的结果盖掉
  function lockClerkRow(on) {
    ["clerkProvider", "clerkModel", "clerkAddress", "clerkCheck", "clerkKey", "clerkClear", "clerkForget"].forEach(function (id) {
      $(id).disabled = on;
    });
  }
  function clerkQuery(c) {
    return "project=" + encodeURIComponent(project)
      + "&provider=" + encodeURIComponent(c.provider)
      + "&base_url=" + encodeURIComponent(c.base_url)
      + "&model=" + encodeURIComponent(c.model);
  }
  function fillClerkModels(providerId) {
    var list = $("clerkModels");
    list.innerHTML = "";
    var item = providers.find(function (p) { return p.id === providerId; });
    (item && item.models || []).forEach(function (name) {
      var op = document.createElement("option");
      op.value = name;
      list.appendChild(op);
    });
  }
  function syncClerkExtras() {
    var other = $("clerkProvider").value === "other";
    $("clerkAddress").hidden = !other;
    $("clerkCost").hidden = !other;
    fillClerkModels($("clerkProvider").value);
  }
  function setClerkState(cls, text) {
    var box = $("clerkStateBox");
    box.className = "state " + (cls || "");
    $("clerkState").textContent = text;
  }
  function paintClerk(saved) {
    // 没设过的项目沿用最近一次：页面必须显示实际在用的那一家
    var params = new URLSearchParams(location.search);
    var inherited = !saved && params.get("clerk_provider");
    var setting = saved || (inherited ? { provider: params.get("clerk_provider"), base_url: params.get("clerk_base"), model: params.get("clerk_model") } : null);
    var provider = (setting && setting.provider) || "tencent";
    var item = providers.find(function (p) { return p.id === provider; }) || providers[0];
    if (!item) return;
    $("clerkProvider").value = item.id;
    $("clerkAddress").value = (setting && setting.base_url) || item.base_url || "";
    $("clerkModel").value = (setting && setting.model) || (item.models && item.models[0]) || "";
    syncClerkExtras();
    clerkStopped = !!(setting && setting.disabled);
    $("clerkClear").textContent = clerkStopped ? "启用" : "停用";
    $("clerkKey").textContent = clerkReady || clerkStopped ? "更换密钥" : "填写密钥";
    if (clerkStopped) setClerkState("", "已停用");
    else if (clerkReady) setClerkState("ok", inherited ? "已配置（沿用最近一次设置）" : "已配置");
    else setClerkState("", "还没配置密钥");
    clerkBaseline = clerkSnapshot(clerkChoice());
  }
  function loadClerkRow() {
    return get("/api/clerk/providers").then(function (d) {
      providers = d.providers || [];
      var sel = $("clerkProvider");
      sel.innerHTML = "";
      providers.forEach(function (p) {
        var op = document.createElement("option");
        op.value = p.id;
        op.textContent = p.name;
        sel.appendChild(op);
      });
      return get("/api/clerk/setting");
    }).then(function (d) {
      paintClerk(d.setting);
    }).catch(function (err) {
      setClerkState("bad", err.message || "读不到行政设置");
    });
  }

  function post(path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-Chat-Token": encodeURIComponent(token) },
      body: JSON.stringify(Object.assign({ project: project }, body)) })
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || "出错了"); return d; }); });
  }
  function get(path, extra) {
    return fetch(path + "?" + new URLSearchParams(Object.assign({ project: project, token: token }, extra)).toString())
      .then(function (r) { return r.json().then(function (d) { if (!r.ok) throw new Error(d.error || "出错了"); return d; }); });
  }

  function toolSelect(role) {
    return rows[role].querySelector(".tool-select") || rows[role].querySelector("select");
  }

  function fill(role, options, value) {
    var sel = toolSelect(role);
    sel.innerHTML = "";
    if (role === "dispatcher") options = [{ value: "", label: "请选择…" }].concat(options, [{ value: "custom", label: "自定义命令…" }]);
    options.forEach(function (o) { var op = document.createElement("option"); op.value = o.value; op.textContent = o.label; sel.appendChild(op); });
    if (role === "dispatcher" && value && value.indexOf("custom:") === 0) {
      sel.value = "custom";
      var box = rows.dispatcher.querySelector(".custom");
      box.hidden = false; box.value = value.slice(7);
    } else {
      sel.value = value || "";
    }
    sel.addEventListener("change", function () {
      if (role === "dispatcher") rows.dispatcher.querySelector(".custom").hidden = sel.value !== "custom";
      renderRoleModelBox(role);
      invalidate([role]);
    });
  }

  function renderRoleModelBox(role) {
    var box = rows[role].querySelector(".model-box");
    if (!box) return;
    var tool = toolSelect(role).value;
    if (tool !== "codex" && tool !== "cursor" && tool !== "agy" && tool !== "qwen") {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;
    box.innerHTML = "";

    var pick = pickOf(role, tool);
    var opts = (modelOptions[tool] || []).slice();
    var curVal = pick.model || "";
    var inPreset = opts.some(function (o) { return o.value === curVal; });
    if (curVal && !inPreset) {
      opts.unshift({ value: curVal, label: curVal + "（当前）" });
    }
    opts.push({ value: "__custom__", label: "其他自定义模型名…" });

    var row1 = document.createElement("div");
    row1.className = "model-row";
    var lbl1 = document.createElement("span");
    lbl1.className = "model-lbl";
    lbl1.textContent = "使用模型";
    var sel1 = document.createElement("select");
    sel1.className = "model-sub";
    sel1.setAttribute("aria-label", ROLE[role] + "模型");
    opts.forEach(function (o) {
      var op = document.createElement("option");
      op.value = o.value;
      op.textContent = o.label;
      sel1.appendChild(op);
    });
    sel1.value = curVal || (opts[0] && opts[0].value) || "";
    row1.appendChild(lbl1);
    row1.appendChild(sel1);
    box.appendChild(row1);

    var customInput = document.createElement("input");
    customInput.type = "text";
    customInput.className = "model-sub model-custom";
    customInput.placeholder = "输入模型名，例如 " + (curVal || "gpt-6.1-sol");
    customInput.hidden = true;
    box.appendChild(customInput);

    sel1.addEventListener("change", function () {
      if (sel1.value === "__custom__") {
        customInput.hidden = false;
        customInput.value = pick.model || "";
        customInput.focus();
      } else {
        customInput.hidden = true;
        pick.model = sel1.value;
        if (tool === "codex") renderRoleModelBox(role);
        onModelModified(role);
      }
    });
    customInput.addEventListener("input", function () {
      var v = customInput.value.trim();
      if (v) {
        pick.model = v;
        onModelModified(role);
      }
    });

    if (tool === "codex") {
      var matched = (modelOptions.codex || []).find(function (m) { return m.value === pick.model; });
      var allowedEfforts = (matched && matched.efforts && matched.efforts.length) ? matched.efforts : null;
      var effortOpts = (modelOptions.codex_effort || []).filter(function (o) {
        return !allowedEfforts || allowedEfforts.indexOf(o.value) !== -1;
      });
      if (effortOpts.length && !effortOpts.some(function (o) { return o.value === pick.effort; })) {
        pick.effort = effortOpts[effortOpts.length - 1].value;
      }
      var row2 = document.createElement("div");
      row2.className = "model-row effort";
      var lbl2 = document.createElement("span");
      lbl2.className = "model-lbl";
      lbl2.textContent = "思考强度";
      var sel2 = document.createElement("select");
      sel2.className = "model-sub";
      sel2.setAttribute("aria-label", ROLE[role] + "思考强度");
      effortOpts.forEach(function (o) {
        var op = document.createElement("option");
        op.value = o.value;
        op.textContent = o.label;
        sel2.appendChild(op);
      });
      sel2.value = pick.effort || "xhigh";
      sel2.addEventListener("change", function () {
        pick.effort = sel2.value;
        onModelModified(role);
      });
      row2.appendChild(lbl2);
      row2.appendChild(sel2);
      box.appendChild(row2);
    } else {
      var none = document.createElement("span");
      none.className = "na";
      none.textContent = "—";
      box.appendChild(none);
    }
  }

  // 这个岗位此刻的模型选择；换了工具就从这家的整体设置起步
  function pickOf(role, tool) {
    var p = picks[role];
    if (!p || p.tool !== tool) {
      p = { tool: tool, model: currentModels[tool] || "" };
      if (tool === "codex") p.effort = currentModels.codex_effort || "xhigh";
      picks[role] = p;
    }
    return p;
  }
  function rolePicks() {
    var t = team(), out = {};
    ALL_ROLES.forEach(function (r) {
      var tool = r === "dispatcher" ? t.dispatcher : t[r];
      if (tool === "codex" || tool === "cursor" || tool === "agy" || tool === "qwen") out[r] = pickOf(r, tool);
    });
    return out;
  }
  function snapshot() { return JSON.stringify({ team: team(), picks: rolePicks() }); }
  function teamChanged() { return installed && !!savedSnapshot && snapshot() !== savedSnapshot; }
  // 改了没保存就离开（点齿轮重开、退出应用）时不能丢（④）：暂存在项目里（应用每次换端口，页面自己存的会丢），回来恢复
  // 一次一次排队写：连着改两下，后写的不会被先写的盖掉（复查第 2 轮）
  var draftChain = Promise.resolve();
  function writeDraft(draft) {
    draftChain = draftChain.then(function () { return post("/api/team/draft", { draft: draft }); })
      .catch(function () { /* 存不了就算了 */ });
  }
  function keepDraft() {
    if (!installed) return;
    writeDraft(teamChanged() ? { base: JSON.parse(savedSnapshot), now: JSON.parse(snapshot()) } : null);
  }
  function dropDraft() { if (installed) writeDraft(null); }

  function renderAllModelBoxes() {
    ALL_ROLES.forEach(renderRoleModelBox);
  }

  function team() {
    var disp = toolSelect("dispatcher").value;
    if (disp === "custom") {
      var cmd = rows.dispatcher.querySelector(".custom").value.trim();
      disp = cmd ? "custom:" + cmd : "";
    }
    return { dispatcher: disp, tester: toolSelect("tester").value,
             developer: toolSelect("developer").value, reviewer: toolSelect("reviewer").value };
  }
  function activeRoles() { return installed ? ALL_ROLES : ["dispatcher"]; }
  function isAlreadyConfigured() {
    return (installed && info && info.team && info.team.configured) || (!installed && !!savedDispatcher);
  }

  function setState(role, cls, text, how) {
    var s = rows[role].querySelector(".state");
    s.className = "state " + cls;
    s.innerHTML = '<span class="dot"></span><span class="txt"></span>';
    s.querySelector(".txt").textContent = text;
    if (how) { var h = document.createElement("span"); h.className = "how"; h.textContent = "怎么办：" + how; s.appendChild(h); }
  }

  function setIdle(role, changed) {
    var t = team();
    if (!TeamCore.needsCheck(t, role)) setState(role, "", "不复查，不用检测");
    else if (!isAlreadyConfigured() || (role === "dispatcher" && !t.dispatcher)) setState(role, "", "还没检测");
    else if (changed) setState(role, "", "已调整，还没检测");
    else setState(role, "ok", "已配置");
  }

  function refresh() {
    var t = team(), active = activeRoles(), configured = isAlreadyConfigured();
    var busy = Object.keys(pending).length > 0;
    ALL_ROLES.forEach(function (r) {
      var b = rows[r].querySelector(".check-one");
      b.hidden = !TeamCore.needsCheck(t, r);
      b.disabled = !!pending[r] || (r === "dispatcher" && !t.dispatcher);
      b.textContent = pending[r] ? "检测中…" : "检测";
      b.setAttribute("aria-label", ROLE[r] + (pending[r] ? "检测中" : "检测生效"));
    });
    $("save").textContent = configured ? "保存并返回对话" : "保存并开始";
    $("save").disabled = !TeamCore.canSave({ team: t, active: active, results: results, configured: configured, busy: busy }) && !clerkChanged() && !(clerkReady && !configured);

    var need = active.filter(function (r) { return TeamCore.needsCheck(t, r); });
    var bad = need.filter(function (r) { return results[r] && !results[r].ok; });
    var sum = $("summary");
    sum.className = "summary";
    if (bad.length) { sum.className = "summary bad"; sum.textContent = bad.length + " 位没通，处理好后再点“检测”，或换一家 / 换个模型。"; }
    else if (need.length && need.every(function (r) { return results[r] && results[r].ok; })) { sum.className = "summary ok"; sum.textContent = "全部通了"; }
    else sum.textContent = "";

    $("sameWarn").hidden = !(installed && t.developer === t.reviewer);
    var d = t.dispatcher || "";
    $("dispNote").textContent = d.indexOf("custom:") === 0 ? "自定义调度员检测只能查“装没装”。" : "";
  }

  // 改了选择：只清这几行的结果；还在路上的检测结果也不再要（它测的是改之前的选择）
  function invalidate(roles) {
    roles.forEach(function (r) { delete results[r]; delete pending[r]; setIdle(r, true); });
    refresh();
    keepDraft();
  }

  function onModelModified(role) {
    invalidate([role]);
  }

  function apply(r) {
    results[r.role] = r;
    var shared = r.shared_with && team()[r.shared_with] === team()[r.role];
    setState(r.role, r.ok ? "ok" : "bad", r.reason + (shared ? "（和" + ROLE[r.shared_with] + "同一家，一起测过）" : ""), r.ok ? "" : r.how);
  }

  function waitingOn(id, roles) { return roles.filter(function (r) { return pending[r] === id; }); }

  // 这次检测没给出结果的行标红，不能让它一直停在“检测中”
  function giveUp(id, roles, message) {
    waitingOn(id, roles).forEach(function (r) {
      delete pending[r];
      results[r] = { ok: false };
      setState(r, "bad", message);
    });
    refresh();
  }

  function watch(run, id, roles, started) {
    var limit = (checkTimeout + 30) * 1000;
    var timer = setInterval(function () {
      if (!waitingOn(id, roles).length) { clearInterval(timer); return; }
      // 到时间一定给结果（③：agy 检测曾一直停在“检测中”）
      if (Date.now() - started > limit) {
        clearInterval(timer);
        giveUp(id, roles, "超过 " + Math.round(limit / 60000) + " 分钟还没有结果，算没通：可能卡住了。再点一次“检测”，或换一家、换个模型");
        return;
      }
      get("/api/team/check", { run: run }).then(function (d) {
        d.results.forEach(function (r) {
          if (pending[r.role] === id) { delete pending[r.role]; apply(r); }
        });
        if (d.done) { clearInterval(timer); giveUp(id, roles, "这次检测没出结果，再点一次“检测生效”"); }
        refresh();
      }).catch(function () { /* 下一次再取 */ });
    }, 700);
  }

  function check(role) {
    var t = team(), roles = TeamCore.rolesSharingTool(t, role, activeRoles(), rolePicks()), id = ++runSeq;
    roles.forEach(function (r) {
      delete results[r];
      pending[r] = id;
      setState(r, "run", "检测中…在临时文件夹里试着建一个文件");
    });
    refresh();
    var started = Date.now();   // 从点“检测”那一刻算：连发起检测的请求本身卡住，也要到时间给结果（复查第 1 轮）
    var guard = setTimeout(function () {
      giveUp(id, roles, "超过 " + Math.round((checkTimeout + 30) / 60) + " 分钟还没有结果，算没通：可能卡住了。再点一次“检测”，或换一家、换个模型");
    }, (checkTimeout + 30) * 1000);
    post("/api/team/check", { team: t, models: currentModels, role_models: rolePicks(), roles: roles })
      .then(function (d) { watch(d.run, id, roles, started); })
      .catch(function (err) { giveUp(id, roles, err.message); })
      .then(function () { if (!waitingOn(id, roles).length) clearTimeout(guard); });
  }

  function reset() {
    results = {};
    activeRoles().forEach(function (r) { setIdle(r, false); });
    refresh();
  }

  function done(dispatcher) {
    // 在超级助手窗口里，这个地址会被窗口接住：切到和调度员的对话
    location.href = "superagent://saved" + (dispatcher ? "?dispatcher=" + encodeURIComponent(dispatcher) : "");
    setTimeout(function () { $("summary").className = "summary ok"; $("summary").textContent = "已保存。"; }, 300);
  }

  function canSaveTeam() {
    return TeamCore.canSave({
      team: team(), active: activeRoles(), results: results,
      configured: isAlreadyConfigured(), busy: Object.keys(pending).length > 0
    });
  }
  function clerkChanged() {
    return !!clerkBaseline && clerkSnapshot(clerkChoice()) !== clerkBaseline;
  }
  function saveTeamThenDone() {
    var t = team();
    if (!installed) { done(t.dispatcher); return; }
    post("/api/team/save", { team: t, models: currentModels, role_models: rolePicks() }).then(function () { dropDraft(); done(); })
      .catch(function (err) { $("summary").className = "summary bad"; $("summary").textContent = err.message; });
  }

  function restoreDraft() {
    if (!installed) return;
    get("/api/team/draft").then(function (d) {
      var rowsToApply = TeamCore.draftRows(JSON.parse(savedSnapshot), d && d.draft);
      var atLoad = JSON.parse(savedSnapshot), current = JSON.parse(snapshot());
      function rowAtLoad(r) { return JSON.stringify([atLoad.team[r], (atLoad.picks || {})[r] || null]); }
      function rowNow(r) { return JSON.stringify([current.team[r], (current.picks || {})[r] || null]); }
      // 读草稿期间用户已经动过的行不碰（复查第 2 轮）
      var roles = Object.keys(rowsToApply).filter(function (r) { return rowNow(r) === rowAtLoad(r); });
      if (!roles.length) return;
      roles.forEach(function (r) {
        var v = rowsToApply[r].tool;
        if (r === "dispatcher" && v && v.indexOf("custom:") === 0) {
          toolSelect(r).value = "custom";
          var box = rows.dispatcher.querySelector(".custom");
          box.hidden = false; box.value = v.slice(7);
        } else {
          toolSelect(r).value = v || "";
        }
        if (rowsToApply[r].pick) picks[r] = rowsToApply[r].pick;
      });
      renderAllModelBoxes();
      // 恢复的行一律要重新检测才能保存：上次可能就是测不通才没存（复查第 1 轮）
      roles.forEach(function (r) {
        results[r] = { ok: false, restored: true };
        setState(r, "bad", "上次改过还没检测：请点“检测”，通了才能保存");
      });
      refresh();
      $("summary").className = "summary";
      $("summary").textContent = "上次改了还没保存，已帮你恢复（要重新检测再保存）。";
    }).catch(function () { /* 读不到就算了 */ });
  }

  window.SuperAgentTeam = {
    clerkResult: function (ok, reason) {
      clerkWaiting = false;
      lockClerkRow(false);
      if (ok) {
        clerkReady = true;
        clerkStopped = false;
        $("clerkClear").textContent = "停用";
        clerkBaseline = clerkSent || clerkSnapshot(clerkChoice());
        setClerkState("ok", "已配置");
        if (canSaveTeam()) saveTeamThenDone();
        else if (!isAlreadyConfigured()) leaveWithoutTeam();   // 团队还没配：只用行政也能回到对话
        else {
          refresh();
          $("summary").className = "summary bad";
          $("summary").textContent = "行政已保存。团队有没通过检测的项，还没保存。";
        }
      } else {
        refresh();
        setClerkState("bad", reason || "没有保存成功");
        $("summary").className = "summary bad";
        $("summary").textContent = reason || "行政设置没有保存，团队也没改。";
      }
    },
    clerkCheckResult: function (ok, reason) {
      var checked = clerkCheckFor;
      clerkCheckFor = "";
      lockClerkRow(false);
      if (checked && checked !== clerkSnapshot(clerkChoice())) return;  // 测完已经换了选择
      setClerkState(ok ? "ok" : "bad", ok ? (reason || "通了") : (reason || "没有通"));
    }
  };

  $("clerkProvider").addEventListener("change", function () {
    var item = providers.find(function (p) { return p.id === $("clerkProvider").value; });
    if (item && item.id !== "other") $("clerkAddress").value = item.base_url || "";
    // 选“其他”时不能带着别家的地址，不然密钥会存到别家的域名下
    else if (providers.some(function (p) { return p.base_url && p.base_url === $("clerkAddress").value; })) $("clerkAddress").value = "";
    if (item && item.models && item.models.length && item.models.indexOf($("clerkModel").value) < 0) {
      $("clerkModel").value = item.models[0];
    }
    syncClerkExtras();
    setClerkState("", "已调整，还没保存");
    refresh();
  });
  $("clerkModel").addEventListener("input", function () { setClerkState("", "已调整，还没保存"); refresh(); });
  $("clerkAddress").addEventListener("input", function () { setClerkState("", "已调整，还没保存"); refresh(); });
  $("clerkCheck").addEventListener("click", function () {
    clerkCheckFor = clerkSnapshot(clerkChoice());
    setClerkState("run", "检测中…");
    location.href = "superagent://clerk-check?" + clerkQuery(clerkChoice());
  });
  $("clerkKey").addEventListener("click", function () {
    clerkCheckFor = "";
    location.href = "superagent://clerk-setup?" + clerkQuery(clerkChoice());
  });
  $("clerkClear").addEventListener("click", function () {
    if (!clerkStopped) { location.href = "superagent://clerk-clear?" + clerkQuery(clerkChoice()); return; }
    clerkCheckFor = "";
    clerkSent = clerkSnapshot(clerkChoice());
    lockClerkRow(true);
    setClerkState("run", "正在测试并启用…");
    location.href = "superagent://clerk-enable?" + clerkQuery(clerkChoice());
  });
  $("clerkForget").addEventListener("click", function () {
    location.href = "superagent://clerk-forget?" + clerkQuery(clerkChoice());
  });

  ALL_ROLES.forEach(function (r) {
    rows[r].querySelector(".check-one").addEventListener("click", function () { check(r); });
  });
  // 团队有改动却存不了：说清楚是谁没测通，问要不要不保存就回去（②：以前悄悄丢掉）
  function leaveWithoutTeam() {
    if (!teamChanged()) { done(); return; }
    var why = TeamCore.unsavedReason({ team: team(), active: activeRoles(), results: results, configured: isAlreadyConfigured() });
    $("summary").className = "summary bad";
    $("summary").textContent = "团队的改动还没存：" + (why || "有一行没测通") + "。";
    if (window.confirm("团队的改动还没存：" + (why || "有一行没测通") + "。\n不保存，直接回到对话吗？（改动会留着，下次打开设置还在）")) done();
  }
  $("save").addEventListener("click", function () {
    if (clerkWaiting) return;
    if (!clerkChanged()) {
      if (canSaveTeam()) saveTeamThenDone();
      else leaveWithoutTeam();   // 只用行政：团队没配也能回到对话；但改了的不能悄悄丢
      return;
    }
    clerkWaiting = true;
    clerkSent = clerkSnapshot(clerkChoice());
    lockClerkRow(true);
    $("save").disabled = true;
    setClerkState("run", "正在测试并保存…");
    location.href = "superagent://clerk-apply?" + clerkQuery(clerkChoice());
  });
  rows.dispatcher.querySelector(".custom").addEventListener("input", function () { invalidate(["dispatcher"]); });

  if (!project || !token) {
    $("problem").hidden = false;
    $("problem").textContent = "请从超级助手窗口打开团队配置。";
  } else {
    get("/api/team").then(function (d) {
      info = d;
      installed = d.installed;
      if (d.models) currentModels = Object.assign(currentModels, d.models);
      if (d.role_models) picks = JSON.parse(JSON.stringify(d.role_models));
      if (d.check_timeout) checkTimeout = d.check_timeout;
      if (d.model_options) modelOptions = Object.assign(modelOptions, d.model_options);
      $("workers").hidden = !installed;
      $("noGit").hidden = installed;
      if (d.problem) { $("problem").hidden = false; $("problem").textContent = "原来的配置有问题，已换成默认值：" + d.problem; }
      fill("dispatcher", d.options.dispatcher, installed ? d.team.dispatcher : savedDispatcher);
      fill("tester", d.options.worker, d.team.tester);
      fill("developer", d.options.worker, d.team.developer);
      fill("reviewer", d.options.reviewer, d.team.reviewer);
      renderAllModelBoxes();
      reset();
      savedSnapshot = snapshot();
      restoreDraft();
      return loadClerkRow();
    }).catch(function (err) { $("problem").hidden = false; $("problem").textContent = "读不到团队配置：" + err.message; });
  }
})();
