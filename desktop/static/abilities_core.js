/* 能力卡片的规则层：表单、收集输入、结果转换、交代原文这些纯函数，不碰页面。
   能力层页面（abilities.js）和聊天里的能力草稿（chat.js）共用这一份；
   tests/abilities_page_check.js、tests/ability_69b_check.js 用 node 直接检查。 */
"use strict";

var AbilitiesCore = {
  esc: function (s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  },

  statusView: function (item) {
    var st = (item && item.status) || "ok";
    if (st === "ok") return { cls: "ok", text: "能用" };
    if (st === "failed") return { cls: "warn", text: "上次没成" };
    return { cls: "idle", text: "不能用" };
  },

  subText: function (item) {
    item = item || {};
    var name = item.block_name || "";
    if (item.model) {
      return name ? name + " · " + item.model : item.model;
    }
    return name;
  },

  outputText: function (type) {
    var map = { text: "文字", list: "列表", image: "图片", file: "文件" };
    return map[type] || type || "";
  },

  checkText: function (output, inputSchema, kind) {
    output = output || {};
    var type = output.type || "";
    var check = output.check || {};

    if (type === "text") {
      if (check.of && check.min_ratio !== undefined && check.max_ratio !== undefined) {
        var ofKey = check.of;
        var ofTitle = ofKey;
        if (inputSchema && inputSchema.properties && inputSchema.properties[ofKey] && inputSchema.properties[ofKey].title) {
          ofTitle = inputSchema.properties[ofKey].title;
        }
        var ratioName = function (r) {
          if (r === 0.5) return "一半";
          if (r === 1) return "一样多";
          if (r === 2) return "两倍";
          if (r === 3) return "三倍";
          return r + " 倍";
        };
        return "有内容，字数在你填的「" + ofTitle + "」的" + ratioName(check.min_ratio) + "到" + ratioName(check.max_ratio) + "之间";
      }
      if (check.min_chars !== undefined && check.max_chars !== undefined) {
        return "有内容，字数在 " + check.min_chars + " 到 " + check.max_chars + " 之间";
      }
      if (check.min_chars !== undefined) {
        return "有内容，不少于 " + check.min_chars + " 字";
      }
      if (check.max_chars !== undefined) {
        return "有内容，不超过 " + check.max_chars + " 字";
      }
      return "有内容";
    }

    if (type === "list") {
      var n = check.min_items !== undefined ? check.min_items : 1;
      var suffix = kind === "search" ? "和链接" : "";
      return "至少 " + n + " 条，每条都有标题" + suffix;
    }

    if (type === "image") {
      var side = check.min_side !== undefined ? check.min_side : 256;
      return "存出了图片，是 PNG 或 JPG，宽高都不小于 " + side;
    }

    if (type === "file") {
      var exts = (check.extensions || []).join("、");
      return "存出了文件，不是空的，扩展名是 " + exts + " 之一";
    }

    return "有内容";
  },

  formFields: function (inputSchema) {
    if (!inputSchema || !inputSchema.properties) return [];
    var props = inputSchema.properties;
    var reqs = Array.isArray(inputSchema.required) ? inputSchema.required : [];
    var fields = [];
    for (var key in props) {
      if (!Object.prototype.hasOwnProperty.call(props, key)) continue;
      var p = props[key] || {};
      var kind = "text";
      var options = null;
      if (Array.isArray(p.enum) && p.enum.length) {
        kind = "select";
        options = p.enum;
      } else if (p.type === "integer" || p.type === "number") {
        kind = "number";
      } else if (p.type === "string" && p.maxLength !== undefined && p.maxLength > 200) {
        kind = "textarea";
      }
      var val = p.default !== undefined ? p.default : "";
      var placeholder = (Array.isArray(p.examples) && p.examples.length) ? "例如：" + p.examples[0] : "";
      var isReq = reqs.indexOf(key) >= 0;
      fields.push({
        name: key,
        title: p.title || key,
        kind: kind,
        options: options,
        value: val,
        placeholder: placeholder,
        required: isReq,
        min: p.minimum !== undefined ? p.minimum : null,
        max: p.maximum !== undefined ? p.maximum : null,
        maxLength: p.maxLength !== undefined ? p.maxLength : null,
      });
    }
    return fields;
  },

  collectInputs: function (fields, raw) {
    fields = fields || [];
    raw = raw || {};
    var inputs = {};
    for (var i = 0; i < fields.length; i++) {
      var f = fields[i];
      var rawVal = raw[f.name];
      var strVal = String(rawVal !== undefined && rawVal !== null ? rawVal : "").trim();
      if (!strVal) {
        if (f.required) {
          return { ok: false, error: "请填写「" + f.title + "」" };
        }
        continue;
      }
      if (f.kind === "number") {
        if (!/^-?\d+$/.test(strVal)) {
          return { ok: false, error: "「" + f.title + "」要填 " + f.min + " 到 " + f.max + " 的整数" };
        }
        var num = parseInt(strVal, 10);
        if ((f.min !== null && num < f.min) || (f.max !== null && num > f.max)) {
          return { ok: false, error: "「" + f.title + "」要填 " + f.min + " 到 " + f.max + " 的整数" };
        }
        inputs[f.name] = num;
      } else {
        if (f.maxLength !== null && strVal.length > f.maxLength) {
          return { ok: false, error: "「" + f.title + "」最多 " + f.maxLength + " 个字" };
        }
        inputs[f.name] = strVal;
      }
    }
    return { ok: true, inputs: inputs };
  },

  resultView: function (result, card) {
    result = result || {};
    var status = result.status || (result.ok ? "ok" : "error");
    var sec = result.seconds !== undefined ? result.seconds : 0;
    var secStr = sec < 1 ? "不到 1 秒" : Math.round(sec) + " 秒";

    var tone = "bad";
    var head = "";
    var said = result.platform_said || "";
    var tip = "";
    var ask = null;
    var body = null;

    if (status === "ok") {
      tone = "ok";
      said = ""; // 成了不显示工具最后一句，没成时才给用户看平台原话
      if (result.model) {
        head = "用了 " + result.model + " · " + secStr;
      } else {
        head = sec < 1 ? "用时不到 1 秒" : ("用时 " + secStr);
      }
    } else if (status === "format") {
      tone = "bad";
      head = "没成：" + (result.reason || "");
    } else if (status === "model") {
      tone = "warn";
      head = "模型不通：" + (result.reason || "");
      if (result.next_model) {
        ask = { model: result.next_model, text: "要换成 " + result.next_model + " 再试一次吗？" };
      } else {
        if (card && card.how && card.how.kind === "llm_api") {
          tip = "可以在上面的模型下拉框换一个再试。";
        } else {
          tip = "这张卡没有备用模型。";
        }
      }
    } else if (status === "block") {
      tone = "bad";
      head = "积木块的问题：" + (result.reason || "");
      tip = result.how || "";
    } else {
      tone = "bad";
      head = "没成：" + (result.reason || "");
      tip = result.how || "";
    }

    if (result.output !== null && result.output !== undefined) {
      var outType = (card && card.output && card.output.type) || "";
      if (outType === "text") {
        body = { type: "text", text: String(result.output) };
      } else if (outType === "list") {
        body = { type: "list", items: Array.isArray(result.output) ? result.output : [] };
      } else if (outType === "image") {
        body = { type: "image", name: String(result.output) };
      } else if (outType === "file") {
        body = { type: "file", name: String(result.output) };
      }
    }

    return {
      tone: tone,
      head: head,
      said: said,
      tip: tip,
      ask: ask,
      body: body,
    };
  },

  runsView: function (records) {
    if (!Array.isArray(records)) return [];
    var out = [];
    for (var i = 0; i < records.length; i++) {
      var r = records[i] || {};
      var t = String(r.time || "");
      var timeStr = "";
      var m = t.match(/^\d{4}-(\d{2}-\d{2})[T ](\d{2}:\d{2})/);
      if (m) {
        timeStr = m[1] + " " + m[2];
      } else {
        timeStr = t;
      }
      out.push({
        time: timeStr,
        ok: !!r.ok,
        text: r.ok ? "成了" : "没成",
        model: r.model || "",
        reason: r.ok ? "" : (r.reason || ""),
      });
    }
    return out;
  },

  promptText: function (card) {
    if (!card || !card.how) return "";
    var kind = card.how.kind;
    if (kind === "llm_api") {
      var sys = card.how.system || "";
      var pr = card.how.prompt || "";
      return "系统：" + sys + "\n\n要求：" + pr;
    }
    if (kind === "cli") {
      return card.how.prompt || "";
    }
    return "";
  },

  removeConfirm: function (card) {
    var title = (card && card.title) || "";
    return "把「" + title + "」移出能力层？记录和成果都留着。";
  },

  canChangeModel: function (card, block) {
    if (!card || !card.how) return false;
    if (card.how.kind === "llm_api") return true;
    if (card.how.kind === "cli") {
      var PRESET_COMMANDS = { codex: "codex", cursor: "agent", agy: "agy" };
      var bid = card.block;
      if (!bid || !PRESET_COMMANDS[bid]) return false;
      if (block) {
        if (!block.exists) return false;
        if (block.command && block.command !== PRESET_COMMANDS[bid]) return false;
        if (block.verified === false) return false;
      }
      return true;
    }
    return false;
  },

  // 三个模型框的值整理成要存的样子：空的不要、重复的只留一个、和主模型一样的备用去掉
  modelChoice: function (main, b1, b2) {
    var backups = [];
    [b1, b2].forEach(function (m) {
      if (m && m !== main && backups.indexOf(m) < 0) backups.push(m);
    });
    return { model: main, backup_models: backups };
  },

  inputsView: function (inputSchema, inputs) {
    if (!inputs || typeof inputs !== "object") return [];
    inputSchema = inputSchema || {};
    var props = inputSchema.properties || {};
    var out = [];
    var keys = Object.keys(props);
    for (var i = 0; i < keys.length; i++) {
      var k = keys[i];
      if (k in inputs && inputs[k] !== null && inputs[k] !== undefined) {
        var str = String(inputs[k]);
        if (!str.trim()) continue;
        var p = props[k] || {};
        var tit = p.title || k;
        out.push({ title: tit, value: str });
      }
    }
    return out;
  },
};

if (typeof module !== "undefined") module.exports = AbilitiesCore;

