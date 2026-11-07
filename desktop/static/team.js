/* 团队与模型配置页：选调度员和三位执行员 + 各家工具具体使用的模型 → 每行各自“检测生效”或直接保存 → 通知窗口切到对话。
   调度员没有默认值：第一次必须用户亲手选。 */
"use strict";

/* 规则层：不碰页面，tests/team_page_check.js 用 node 直接检查。 */
var TeamCore = {
  // 选“不复查”的那行不用测
  needsCheck: function (team, role) { return !(role === "reviewer" && team.reviewer === "none"); },
  // 这一行和哪些行用同一家工具：同一家共用一套模型，一起测、一起失效
  rolesSharingTool: function (team, role, active) {
    if (!TeamCore.needsCheck(team, role)) return [];
    return active.filter(function (r) { return team[r] === team[role] && TeamCore.needsCheck(team, r); });
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
  var currentModels = { codex: "gpt-6.1-sol", codex_effort: "xhigh", cursor: "auto", agy: "gemini-4-argon-high" };
  var modelOptions = { codex: [], codex_effort: [], cursor: [], agy: [] };
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
    if (tool !== "codex" && tool !== "cursor" && tool !== "agy") {
      box.hidden = true;
      box.innerHTML = "";
      return;
    }
    box.hidden = false;
    box.innerHTML = "";

    var opts = (modelOptions[tool] || []).slice();
    var curVal = currentModels[tool] || "";
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
        customInput.value = currentModels[tool] || "";
        customInput.focus();
      } else {
        customInput.hidden = true;
        currentModels[tool] = sel1.value;
        if (tool === "codex") renderRoleModelBox(role);
        syncOtherModelBoxes(role);
        onModelModified(role);
      }
    });
    customInput.addEventListener("input", function () {
      var v = customInput.value.trim();
      if (v) {
        currentModels[tool] = v;
        syncOtherModelBoxes(role);
        onModelModified(role);
      }
    });

    if (tool === "codex") {
      var matched = (modelOptions.codex || []).find(function (m) { return m.value === currentModels.codex; });
      var allowedEfforts = (matched && matched.efforts && matched.efforts.length) ? matched.efforts : null;
      var effortOpts = (modelOptions.codex_effort || []).filter(function (o) {
        return !allowedEfforts || allowedEfforts.indexOf(o.value) !== -1;
      });
      if (effortOpts.length && !effortOpts.some(function (o) { return o.value === currentModels.codex_effort; })) {
        currentModels.codex_effort = effortOpts[effortOpts.length - 1].value;
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
      sel2.value = currentModels.codex_effort || "xhigh";
      sel2.addEventListener("change", function () {
        currentModels.codex_effort = sel2.value;
        syncOtherModelBoxes(role);
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

  function syncOtherModelBoxes(changedRole) {
    ALL_ROLES.forEach(function (r) {
      if (r !== changedRole) renderRoleModelBox(r);
    });
  }

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
  }

  function onModelModified(role) {
    invalidate(TeamCore.rolesSharingTool(team(), role, activeRoles()));
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

  function watch(run, id, roles) {
    var timer = setInterval(function () {
      if (!waitingOn(id, roles).length) { clearInterval(timer); return; }
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
    var t = team(), roles = TeamCore.rolesSharingTool(t, role, activeRoles()), id = ++runSeq;
    roles.forEach(function (r) {
      delete results[r];
      pending[r] = id;
      setState(r, "run", "检测中…在临时文件夹里试着建一个文件");
    });
    refresh();
    post("/api/team/check", { team: t, models: currentModels, roles: roles })
      .then(function (d) { watch(d.run, id, roles); })
      .catch(function (err) { giveUp(id, roles, err.message); });
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
    post("/api/team/save", { team: t, models: currentModels }).then(function () { done(); })
      .catch(function (err) { $("summary").className = "summary bad"; $("summary").textContent = err.message; });
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
        else if (!isAlreadyConfigured()) done();   // 团队还没配：只用行政也能回到对话
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
  $("save").addEventListener("click", function () {
    if (clerkWaiting) return;
    if (!clerkChanged()) {
      if (canSaveTeam()) saveTeamThenDone();
      else done();   // 只用行政：团队没配也能回到对话
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
      return loadClerkRow();
    }).catch(function (err) { $("problem").hidden = false; $("problem").textContent = "读不到团队配置：" + err.message; });
  }
})();
