"""能力卡片的格式、存放、填内容、查结果（66 号单）。

能力卡片 = 基础层某个积木块的一种固定用法。
依据：docs/积木库/能力层/方案.md 技术设计“能力卡片字段”“存放”、施工单 66。
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import secrets
import shutil
import struct
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from collab.blocks import (
    BlockError,
    home,
    category,
    CATEGORY_LABELS,
    _ID,
    _MODEL,
    _DONE_FILE,
    _has_secret,
    _json_safe,
    _text,
    _PLACEHOLDER,
    append_jsonl,
    read_jsonl,
)


class AbilityError(BlockError):
    """能力卡片相关异常，继承自 BlockError。"""
    pass


OUTPUT_LABELS = {"text": "文字", "list": "列表", "image": "图片", "file": "文件"}
TIME_RANGES = {"不限": "none", "最近一天": "day", "最近一周": "week", "最近一个月": "month"}

ABILITY_DIR = "能力层"
RUN_RECORD_DIR = ("记录", "能力")
RESULT_DIR = "成果"

ALLOWED_TOP_FIELDS = {
    "id", "title", "description", "block", "model", "backup_models",
    "inputSchema", "how", "output", "source", "example_run"
}

_RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
_PROP_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_EXT_NAME = re.compile(r"^[a-z0-9]{1,8}$")


def _check_ability_id(cid) -> str:
    if not isinstance(cid, str) or not _ID.fullmatch(cid):
        raise AbilityError(f"编号 {cid!r} 不对：只能用小写字母、数字和横线，开头不能是横线，最长 40 个字")
    return cid


def _ability_path(ability_id: str, root=None) -> Path:
    return home(root) / ABILITY_DIR / f"{_check_ability_id(ability_id)}.json"


def _run_record_path(ability_id: str, root=None) -> Path:
    return home(root) / RUN_RECORD_DIR[0] / RUN_RECORD_DIR[1] / f"{_check_ability_id(ability_id)}.jsonl"


def _parse_template(template: str, properties: dict, name: str = "交代") -> set[str]:
    """严格解析模板中的占位符，只许出现成对、不嵌套、已声明的 {项名}。"""
    if not isinstance(template, str) or not template.strip():
        raise AbilityError(f"{name}不能为空，去掉空白后要有内容")
    if len(template) > 4000:
        raise AbilityError(f"{name}太长：最长 4000 个字")

    used = set()
    in_brace = False
    start = -1
    for i, ch in enumerate(template):
        if ch == "{":
            if in_brace:
                raise AbilityError(f"{name}里的花括号不能嵌套")
            in_brace = True
            start = i
        elif ch == "}":
            if not in_brace:
                raise AbilityError(f"{name}里有单独未配对的花括号 }}")
            ph = template[start + 1:i]
            if not ph:
                raise AbilityError(f"{name}里有空的占位 {{}}，花括号里必须写明项名")
            if ph not in properties:
                raise AbilityError(f"{name}里引用了未声明的占位项 {{{ph}}}")
            used.add(ph)
            in_brace = False

    if in_brace:
        raise AbilityError(f"{name}里有未闭合的花括号 {{")

    return used


def validate_ability(card) -> dict:
    """检查一张能力卡片，返回补齐默认值的新卡，不改传进来的那份。
    检查顺序：是不是一组字段 → 有没有密钥 → 顶层字段 → 模型 → inputSchema → how → output。
    """
    # 1. 是不是一组字段
    if not isinstance(card, dict):
        raise AbilityError("能力卡片必须写成一组字段")

    # 2. 有没有密钥
    if _has_secret(card):
        raise AbilityError("密钥不能写进能力卡片，密钥只存在钥匙串里")

    card = copy.deepcopy(card)

    # 3. 顶层字段
    extra = sorted(set(card) - ALLOWED_TOP_FIELDS)
    if extra:
        raise AbilityError(f"卡片里有不认识的项：{'、'.join(extra)}")

    cid = card.get("id")
    _check_ability_id(cid)

    title = card.get("title")
    if not isinstance(title, str) or not (1 <= len(title.strip()) <= 20):
        raise AbilityError("名字不能为空，去掉空白后 1 到 20 个字")

    desc = card.get("description")
    if not isinstance(desc, str) or not (1 <= len(desc.strip()) <= 200):
        raise AbilityError("说明不能为空，去掉空白后 1 到 200 个字")

    block = card.get("block")
    if not isinstance(block, str) or not _ID.fullmatch(block):
        raise AbilityError(f"积木块编号 {block!r} 不对：只能用小写字母、数字和横线，最长 40 个字")

    card.setdefault("source", "clerk")
    if card["source"] not in ("builtin", "clerk"):
        raise AbilityError("来源只能是 builtin 或 clerk")

    card.setdefault("example_run", None)
    if card["example_run"] is not None:
        if not isinstance(card["example_run"], str) or not _RUN_ID.fullmatch(card["example_run"]):
            raise AbilityError(f"示例运行编号 {card['example_run']!r} 不对：格式为 年月日-时分秒-4位十六进制")

    # 4. 模型
    how = card.get("how")
    how_kind = how.get("kind") if isinstance(how, dict) else None

    if how_kind == "llm_api":
        if "model" not in card:
            raise AbilityError("大模型 API 的能力卡片必须指定模型")
        if not isinstance(card["model"], str) or not _MODEL.fullmatch(card["model"]):
            raise AbilityError("模型名不对：要以字母或数字开头，只能用字母、数字和 . _ : / @ + -")
    elif how_kind == "search":
        if "model" in card:
            raise AbilityError("联网搜索的能力卡片不许指定模型")
        if card.get("backup_models"):
            raise AbilityError("联网搜索的能力卡片不许指定备用模型")
    else:
        if "model" in card and (not isinstance(card["model"], str) or not _MODEL.fullmatch(card["model"])):
            raise AbilityError("模型名不对：要以字母或数字开头，只能用字母、数字和 . _ : / @ + -")

    card.setdefault("backup_models", [])
    backup = card["backup_models"]
    if not isinstance(backup, list):
        raise AbilityError("备用模型必须是列表")
    for m in backup:
        if not isinstance(m, str):
            raise AbilityError("备用模型列表里的每一项必须是文字")
        if not _MODEL.fullmatch(m):
            raise AbilityError(f"模型名 {m!r} 不对：要以字母或数字开头，只能用字母、数字和 . _ : / @ + -")
    if len(backup) > 2:
        raise AbilityError("备用模型最多 2 个")
    if "model" not in card and backup:
        raise AbilityError("没有主模型时不能指定备用模型")
    if len(backup) != len(set(backup)):
        raise AbilityError("备用模型不能重复")
    if "model" in card and card["model"] in backup:
        raise AbilityError("备用模型不能和主模型一样")

    # 5. inputSchema
    schema = card.get("inputSchema")
    if not isinstance(schema, dict):
        raise AbilityError("inputSchema 必须是一组字段")

    extra_schema = sorted(set(schema) - {"type", "properties", "required"})
    if extra_schema:
        raise AbilityError(f"inputSchema 包含不认识的项：{'、'.join(extra_schema)}")

    if schema.get("type") != "object":
        raise AbilityError("inputSchema 的 type 必须是 object")

    props = schema.get("properties")
    if not isinstance(props, dict) or not (1 <= len(props) <= 10):
        raise AbilityError("inputSchema 的 properties 必须有 1 到 10 项")

    for k, p in props.items():
        if not isinstance(k, str) or not _PROP_NAME.fullmatch(k):
            raise AbilityError(f"项名 {k!r} 不对：必须以小写字母开头，只能用小写字母、数字和下划线，最长 32 个字")
        if not isinstance(p, dict):
            raise AbilityError(f"项 {k} 必须是一组字段")

        ptype = p.get("type")
        if ptype not in ("string", "integer"):
            raise AbilityError(f"项 {k} 的 type 只能是 string 或 integer")

        title = p.get("title")
        if not isinstance(title, str) or not (1 <= len(title.strip()) <= 20):
            raise AbilityError(f"项 {k} 的 title 必填，去掉空白后 1 到 20 个字")

        if "description" in p:
            pdesc = p["description"]
            if not isinstance(pdesc, str) or len(pdesc) > 100:
                raise AbilityError(f"项 {k} 的 description 最多 100 个字")

        if ptype == "string":
            allowed_p = {"type", "title", "description", "maxLength", "enum", "default", "examples"}
            extra_p = sorted(set(p) - allowed_p)
            if extra_p:
                raise AbilityError(f"项 {k} 包含不认识的属性：{'、'.join(extra_p)}")

            if "maxLength" not in p and "enum" not in p:
                raise AbilityError(f"项 {k} 必须指定 maxLength 或 enum 至少一个")

            if "maxLength" in p:
                ml = p["maxLength"]
                if not isinstance(ml, int) or isinstance(ml, bool) or not (1 <= ml <= 8000):
                    raise AbilityError(f"项 {k} 的 maxLength 必须是 1 到 8000 的整数")

            if "enum" in p:
                en = p["enum"]
                if not isinstance(en, list) or not (2 <= len(en) <= 10):
                    raise AbilityError(f"项 {k} 的 enum 必须是 2 到 10 个选项")
                for v in en:
                    if not isinstance(v, str):
                        raise AbilityError(f"项 {k} 的 enum 选项必须是文字")
                    if not (1 <= len(v.strip()) <= 20):
                        raise AbilityError(f"项 {k} 的 enum 选项必须是 1 到 20 个字的文字")
                if len(en) != len(set(en)):
                    raise AbilityError(f"项 {k} 的 enum 不能有重复项")

            if "default" in p:
                df = p["default"]
                if not isinstance(df, str):
                    raise AbilityError(f"项 {k} 的 default 必须是文字")
                if "maxLength" in p and len(df) > p["maxLength"]:
                    raise AbilityError(f"项 {k} 的 default 字数超过了 maxLength")
                if "enum" in p and df not in p["enum"]:
                    raise AbilityError(f"项 {k} 的 default 必须在 enum 选项中")

            if "examples" in p:
                exs = p["examples"]
                if not isinstance(exs, list) or len(exs) > 3:
                    raise AbilityError(f"项 {k} 的 examples 必须是最多 3 项的列表")
                for ex in exs:
                    if not isinstance(ex, str):
                        raise AbilityError(f"项 {k} 的 examples 每一项必须是文字")
                    if "maxLength" in p and len(ex) > p["maxLength"]:
                        raise AbilityError(f"项 {k} 的 examples 字数超过了 maxLength")
                    if "enum" in p and ex not in p["enum"]:
                        raise AbilityError(f"项 {k} 的 examples 必须在 enum 选项中")

        elif ptype == "integer":
            allowed_p = {"type", "title", "description", "minimum", "maximum", "default", "examples"}
            extra_p = sorted(set(p) - allowed_p)
            if extra_p:
                raise AbilityError(f"项 {k} 包含不认识的属性：{'、'.join(extra_p)}")

            if "minimum" not in p:
                raise AbilityError(f"项 {k} 必须指定 minimum")
            if "maximum" not in p:
                raise AbilityError(f"项 {k} 必须指定 maximum")

            mn = p["minimum"]
            if not isinstance(mn, int) or isinstance(mn, bool) or not (0 <= mn <= 100000):
                raise AbilityError(f"项 {k} 的 minimum 必须是 0 到 100000 的整数")

            mx = p["maximum"]
            if not isinstance(mx, int) or isinstance(mx, bool) or not (0 <= mx <= 100000):
                raise AbilityError(f"项 {k} 的 maximum 必须是 0 到 100000 的整数")

            if mn > mx:
                raise AbilityError(f"项 {k} 的 minimum 不能大于 maximum")

            if "default" in p:
                df = p["default"]
                if not isinstance(df, int) or isinstance(df, bool) or not (mn <= df <= mx):
                    raise AbilityError(f"项 {k} 的 default 必须是 minimum 到 maximum 之间的整数")

            if "examples" in p:
                exs = p["examples"]
                if not isinstance(exs, list) or len(exs) > 3:
                    raise AbilityError(f"项 {k} 的 examples 必须是最多 3 项的列表")
                for ex in exs:
                    if not isinstance(ex, int) or isinstance(ex, bool) or not (mn <= ex <= mx):
                        raise AbilityError(f"项 {k} 的 examples 必须是 minimum 到 maximum 之间的整数")

    schema.setdefault("required", [])
    req = schema["required"]
    if not isinstance(req, list):
        raise AbilityError("required 必须是列表")
    for r in req:
        if not isinstance(r, str):
            raise AbilityError("required 里的每一项必须是文字")
        if r not in props:
            raise AbilityError(f"required 里的项 {r} 没有在 properties 里声明")
    if len(req) != len(set(req)):
        raise AbilityError("required 不能有重复项")

    # 6. how
    if not isinstance(how, dict):
        raise AbilityError("how 必须是一组字段")

    kind = how.get("kind")
    if kind not in ("search", "llm_api", "cli"):
        raise AbilityError("how 的 kind 不认识：只能是 search、llm_api 或 cli")

    fields_by_kind = {
        "search": {"kind", "query", "max_results", "time_range"},
        "llm_api": {"kind", "system", "prompt"},
        "cli": {"kind", "prompt", "output_file", "timeout"},
    }
    extra_how = sorted(set(how) - fields_by_kind[kind])
    if extra_how:
        raise AbilityError(f"how 包含不认识的项：{'、'.join(extra_how)}")

    used_props = set()

    if kind == "search":
        used_props.update(_parse_template(how.get("query"), props, "query 交代"))

        how.setdefault("max_results", 8)
        mr = how["max_results"]
        if isinstance(mr, str) and mr.startswith("{") and mr.endswith("}"):
            ref = mr[1:-1]
            if ref not in props:
                raise AbilityError(f"max_results 引用的项 {ref} 未在 inputSchema 中声明")
            ref_prop = props[ref]
            if ref_prop["type"] != "integer":
                raise AbilityError("max_results 引用的项必须是整数")
            if ref_prop["minimum"] < 1 or ref_prop["maximum"] > 20:
                raise AbilityError("max_results 引用的整数项范围必须在 1 到 20 之间")
            if ref not in req and "default" not in ref_prop:
                raise AbilityError(f"max_results 引用的项 {ref} 必须是必填项或指定 default 默认值")
            used_props.add(ref)
        else:
            if not isinstance(mr, int) or isinstance(mr, bool) or not (1 <= mr <= 20):
                raise AbilityError("max_results 必须是 1 到 20 的整数")

        how.setdefault("time_range", "none")
        tr = how["time_range"]
        if isinstance(tr, str) and tr.startswith("{") and tr.endswith("}"):
            ref = tr[1:-1]
            if ref not in props:
                raise AbilityError(f"time_range 引用的项 {ref} 未在 inputSchema 中声明")
            ref_prop = props[ref]
            if "enum" not in ref_prop:
                raise AbilityError("time_range 引用的项必须带有 enum 时间范围选项")
            for opt in ref_prop["enum"]:
                if opt not in TIME_RANGES:
                    raise AbilityError(f"time_range 引用的选项 {opt} 不是合法的时间范围")
            if ref not in req and "default" not in ref_prop:
                raise AbilityError(f"time_range 引用的项 {ref} 必须是必填项或指定 default 默认值")
            used_props.add(ref)
        else:
            if tr not in ("none", "day", "week", "month"):
                raise AbilityError("time_range 只能是 none、day、week、month 或引用选项项")

    elif kind == "llm_api":
        used_props.update(_parse_template(how.get("prompt"), props, "prompt 交代"))
        if "system" in how:
            used_props.update(_parse_template(how["system"], props, "system 交代"))

    elif kind == "cli":
        used_props.update(_parse_template(how.get("prompt"), props, "prompt 交代"))

        out_f = how.get("output_file")
        if not isinstance(out_f, str) or not _DONE_FILE.fullmatch(out_f):
            raise AbilityError("output_file 文件名不对：只能写普通文件名，不能带路径")

        how.setdefault("timeout", 300)
        timeout = how["timeout"]
        if not isinstance(timeout, int) or isinstance(timeout, bool) or not (30 <= timeout <= 600):
            raise AbilityError("timeout 必须是 30 到 600 的整数")

    for r in req:
        if r not in used_props:
            raise AbilityError(f"必填项 {r} 必须在模板中或作为参数被引用使用")

    # 7. output
    output = card.get("output")
    if not isinstance(output, dict):
        raise AbilityError("output 必须是一组字段")

    extra_out = sorted(set(output) - {"type", "check"})
    if extra_out:
        raise AbilityError(f"output 包含不认识的项：{'、'.join(extra_out)}")

    out_type = output.get("type")
    if out_type not in ("text", "list", "image", "file"):
        raise AbilityError("output 的 type 只能是文字、列表、图片、文件四种之一")

    if kind == "search" and out_type != "list":
        raise AbilityError("联网搜索的能力卡片只能交回列表")
    if kind == "llm_api" and out_type != "text":
        raise AbilityError("大模型 API 的能力卡片只能交回文字")
    if kind == "cli" and out_type == "list":
        raise AbilityError("命令行工具不能交回列表")

    output.setdefault("check", {})
    check = output["check"]
    if not isinstance(check, dict):
        raise AbilityError("check 必须是一组字段")

    if out_type == "text":
        has_chars = any(k in check for k in ("min_chars", "max_chars"))
        has_ratio = any(k in check for k in ("min_ratio", "max_ratio", "of"))
        if has_chars and has_ratio:
            raise AbilityError("字数限制和倍数限制不能混用")

        if has_chars:
            extra_c = sorted(set(check) - {"min_chars", "max_chars"})
            if extra_c:
                raise AbilityError(f"check 包含不认识的项：{'、'.join(extra_c)}")
            for k in ("min_chars", "max_chars"):
                if k in check:
                    val = check[k]
                    if not isinstance(val, int) or isinstance(val, bool) or not (1 <= val <= 100000):
                        raise AbilityError(f"字数限制 {k} 必须是 1 到 100000 的整数")
            if "min_chars" in check and "max_chars" in check:
                if check["min_chars"] > check["max_chars"]:
                    raise AbilityError("字数下限不能大于字数上限")

        elif has_ratio:
            extra_c = sorted(set(check) - {"min_ratio", "max_ratio", "of"})
            if extra_c:
                raise AbilityError(f"check 包含不认识的项：{'、'.join(extra_c)}")
            if "of" not in check:
                raise AbilityError("使用倍数限制时必须指定 of 项")
            of_prop = check["of"]
            if not isinstance(of_prop, str):
                raise AbilityError("check.of 必须是文字")
            if of_prop not in props:
                raise AbilityError(f"of 引用的项 {of_prop} 未在 properties 里声明")
            if props[of_prop]["type"] != "integer":
                raise AbilityError(f"of 引用的项 {of_prop} 必须是整数类型")

            for k in ("min_ratio", "max_ratio"):
                if k in check:
                    val = check[k]
                    if not isinstance(val, (int, float)) or isinstance(val, bool) or not (0 < val <= 10):
                        raise AbilityError(f"{k} 必须是大于 0 且不超过 10 的倍数")
            if "min_ratio" in check and "max_ratio" in check:
                if check["min_ratio"] > check["max_ratio"]:
                    raise AbilityError("最小倍数不能大于最大倍数")
        else:
            if check:
                raise AbilityError(f"check 包含不认识的项：{'、'.join(sorted(check))}")

    elif out_type == "list":
        check.setdefault("min_items", 1)
        extra_c = sorted(set(check) - {"min_items"})
        if extra_c:
            raise AbilityError(f"check 包含不认识的项：{'、'.join(extra_c)}")
        mi = check["min_items"]
        if not isinstance(mi, int) or isinstance(mi, bool) or not (1 <= mi <= 50):
            raise AbilityError("min_items 必须是 1 到 50 的整数条")

    elif out_type == "image":
        check.setdefault("min_side", 256)
        extra_c = sorted(set(check) - {"min_side"})
        if extra_c:
            raise AbilityError(f"check 包含不认识的项：{'、'.join(extra_c)}")
        ms = check["min_side"]
        if not isinstance(ms, int) or isinstance(ms, bool) or not (16 <= ms <= 4096):
            raise AbilityError("min_side 必须是 16 到 4096 的整数宽高")
        out_f = how.get("output_file", "")
        if not (out_f.lower().endswith(".png") or out_f.lower().endswith(".jpg") or out_f.lower().endswith(".jpeg")):
            raise AbilityError("出图的 output_file 必须以 .png、.jpg 或 .jpeg 结尾")

    elif out_type == "file":
        if "extensions" not in check:
            raise AbilityError("文件类型的 check 必须指定 extensions")
        extra_c = sorted(set(check) - {"extensions"})
        if extra_c:
            raise AbilityError(f"check 包含不认识的项：{'、'.join(extra_c)}")
        exts = check["extensions"]
        if not isinstance(exts, list) or not (1 <= len(exts) <= 5):
            raise AbilityError("extensions 必须是 1 到 5 项的列表")
        for e in exts:
            if not isinstance(e, str) or not _EXT_NAME.fullmatch(e):
                raise AbilityError(f"扩展名 {e!r} 不对：只能用 1 到 8 位小写字母或数字，不能带点")
        out_f = how.get("output_file", "")
        out_ext = out_f.rsplit(".", 1)[-1].lower() if "." in out_f else ""
        if out_ext not in exts:
            raise AbilityError(f"output_file 的扩展名 {out_ext} 不在允许的 extensions 列表中")

    return card


def fits_block(card, block, models=None, models_complete=True) -> list:
    """对照积木块检查能力卡片是否匹配。"""
    card = validate_ability(card)

    if card["block"] != block.get("id"):
        raise AbilityError(f"能力卡片用的积木块编号 {card['block']} 和积木块编号 {block.get('id')} 对不上")

    cat = category(block)
    cat_label = CATEGORY_LABELS.get(cat, "未知类别")
    how_kind = card["how"]["kind"]

    if how_kind == "search":
        if cat != "search":
            raise AbilityError(f"联网搜索的能力卡片必须配联网搜索积木块，当前是{cat_label}")
    elif how_kind == "llm_api":
        if cat != "llm_api":
            raise AbilityError(f"大模型 API 的能力卡片必须配大模型 API 积木块，当前是{cat_label}")
    elif how_kind == "cli":
        if cat == "tool":
            raise AbilityError("命令行能力卡片必须配 AI 助手，普通工具不接交代")
        if cat != "agent":
            raise AbilityError(f"命令行能力卡片必须配 AI 助手，当前是{cat_label}")

    warns = []
    if models is not None:
        needed = []
        if "model" in card and card["model"]:
            needed.append(card["model"])
        needed.extend(card.get("backup_models", []))

        for m in needed:
            if m not in models:
                if models_complete:
                    raise AbilityError(f"模型 {m} 不在服务商的模型名单里")
                warns.append(f"模型 {m} 不在服务商当前的模型名单里")

    return warns


def save_ability(card: dict, root=None) -> dict:
    """校验后写入能力层/<编号>.json，先写临时文件再替换。不碰基础层。"""
    clean = validate_ability(card)
    path = _ability_path(clean["id"], root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{clean['id']}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(clean, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return clean


def load_ability(ability_id: str, root=None) -> dict:
    """读、校验能力卡片，文件名和卡里编号对不上就拒收。"""
    path = _ability_path(ability_id, root)
    if not path.is_file():
        raise AbilityError(f"能力层里没有 {ability_id}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise AbilityError(f"{ability_id} 的能力卡片读不出来，文件可能坏了") from exc
    try:
        card = validate_ability(data)
    except AbilityError:
        raise
    except Exception as exc:
        raise AbilityError(f"{ability_id} 的能力卡片有问题：{exc}") from exc
    if card["id"] != ability_id:
        raise AbilityError(f"{ability_id} 的能力卡片里写的编号是 {card['id']}，对不上")
    return card


def list_abilities(root=None) -> list:
    """按文件名排序；坏文件单独一项 {"id": 文件名, "problem": 原因}，不影响别的。"""
    folder = home(root) / ABILITY_DIR
    if not folder.is_dir():
        return []
    items = []
    for path in sorted(folder.glob("*.json")):
        try:
            items.append(load_ability(path.stem, root))
        except AbilityError as exc:
            items.append({"id": path.stem, "problem": str(exc)})
        except Exception as exc:
            items.append({"id": path.stem, "problem": f"{path.stem} 的能力卡片读不了：{exc}"})
    return items


def remove_ability(ability_id: str, root=None) -> None:
    """只删卡片文件，记录和成果留着；没有这张就拒收。"""
    path = _ability_path(ability_id, root)
    if not path.is_file():
        raise AbilityError(f"能力层里没有 {ability_id}")
    path.unlink()


def add_run(ability_id: str, record: dict, root=None) -> dict:
    """追加一条运行记录到 记录/能力/<编号>.jsonl。"""
    try:
        _check_ability_id(ability_id)
    except BlockError as exc:
        raise AbilityError(str(exc)) from exc
    try:
        raw = json.dumps(record, ensure_ascii=False)
        parsed = json.loads(raw)
    except Exception as exc:
        raise AbilityError(f"记录无法序列化为 JSON：{exc}") from exc
    if not isinstance(parsed, dict) or not _text(parsed.get("type")):
        raise AbilityError("记录要写 type，并且是文字")
    if _has_secret(parsed):
        raise AbilityError("记录里不能有密钥")
    item = dict(parsed, time=datetime.now().isoformat(timespec="seconds"))
    if not _json_safe(item):
        raise AbilityError("记录里有存不下来的内容（怪字符或不是文字的值）")
    path = _run_record_path(ability_id, root)
    return append_jsonl(path, item)


def runs(ability_id: str, root=None, limit: int | None = None) -> list:
    """读指定能力卡片的运行记录。"""
    try:
        _check_ability_id(ability_id)
    except BlockError as exc:
        raise AbilityError(str(exc)) from exc
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit < 1):
        raise AbilityError("limit 要是大于 0 的整数")
    path = _run_record_path(ability_id, root)
    return read_jsonl(path, limit=limit)


_run_id_lock = threading.Lock()
_current_sec = ""
_used_suffixes: set[str] = set()


def new_run_id() -> str:
    """生成唯一的运行编号：年月日-时分秒-4位十六进制，同一秒内加锁保证不重复。"""
    global _current_sec, _used_suffixes
    with _run_id_lock:
        while True:
            now_str = datetime.now().strftime("%Y%m%d-%H%M%S")
            if now_str != _current_sec:
                _current_sec = now_str
                _used_suffixes.clear()
            if len(_used_suffixes) >= 65536:
                time.sleep(0.01)
                continue
            candidate = secrets.token_hex(2)
            if candidate not in _used_suffixes:
                _used_suffixes.add(candidate)
                return f"{now_str}-{candidate}"


def result_folder(ability_id: str, run_id: str, root=None) -> Path:
    """返回 成果/<编号>/<运行编号>/ 的路径，只算路径不建文件夹。"""
    _check_ability_id(ability_id)
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise AbilityError(f"运行编号 {run_id!r} 不对")
    return home(root) / RESULT_DIR / ability_id / run_id


BUILTIN_ABILITIES = {
    "search-web": {
        "id": "search-web",
        "title": "搜资料",
        "description": "按关键词搜资料，适合查最新事实、找参考资料",
        "block": "web-search",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "string",
                    "title": "关键词",
                    "maxLength": 200,
                    "examples": ["人工智能最新进展"]
                },
                "count": {
                    "type": "integer",
                    "title": "条数",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 8,
                    "examples": [8]
                },
                "period": {
                    "type": "string",
                    "title": "时间范围",
                    "enum": ["不限", "最近一天", "最近一周", "最近一个月"],
                    "default": "不限",
                    "examples": ["不限"]
                }
            },
            "required": ["keywords"]
        },
        "how": {
            "kind": "search",
            "query": "{keywords}",
            "max_results": "{count}",
            "time_range": "{period}"
        },
        "output": {
            "type": "list",
            "check": {
                "min_items": 1
            }
        }
    },
    "write-article": {
        "id": "write-article",
        "title": "写文章",
        "description": "按主题写一篇文章，适合写公众号、说明文",
        "block": "bailian",
        "model": "qwen3.7-plus",
        "backup_models": ["qwen3.6-plus"],
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "title": "主题",
                    "maxLength": 200,
                    "examples": ["周末带孩子去哪玩"]
                },
                "words": {
                    "type": "integer",
                    "title": "大概字数",
                    "minimum": 100,
                    "maximum": 20000,
                    "default": 800,
                    "examples": [800]
                },
                "notes": {
                    "type": "string",
                    "title": "参考资料",
                    "maxLength": 8000,
                    "examples": ["参考新闻要点"]
                }
            },
            "required": ["topic"]
        },
        "how": {
            "kind": "llm_api",
            "system": "你是一个优秀的写作助手。请直接输出文章正文，不要输出任何开场白、问候语或客套话。如果用户在要求后面附了资料，请参考它。",
            "prompt": "写一篇关于{topic}的文章，约{words}字。{notes}"
        },
        "output": {
            "type": "text",
            "check": {
                "min_ratio": 0.5,
                "max_ratio": 2.0,
                "of": "words"
            }
        }
    },
    "draw-image": {
        "id": "draw-image",
        "title": "出图",
        "description": "按画面描述生成一张图片",
        "block": "codex",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scene": {
                    "type": "string",
                    "title": "画面描述",
                    "maxLength": 1000,
                    "examples": ["一只可爱的橘猫在阳光下打瞌睡"]
                },
                "shape": {
                    "type": "string",
                    "title": "画幅",
                    "enum": ["横版", "竖版", "方形"],
                    "default": "横版",
                    "examples": ["横版"]
                }
            },
            "required": ["scene"]
        },
        "how": {
            "kind": "cli",
            "prompt": "用你自带的 $imagegen 出图功能画一张图：{scene}。画幅：{shape}。把图片存成当前文件夹里的 输出.png。存好后只回复 OK。",
            "output_file": "输出.png",
            "timeout": 300
        },
        "output": {
            "type": "image",
            "check": {
                "min_side": 256
            }
        }
    }
}


def ensure_builtin(root=None) -> list:
    """只在 能力层/ 文件夹还不存在时把三张预置卡片写进去（source: builtin）。不留半套，也不怕同时打开。"""
    target = home(root) / ABILITY_DIR
    if target.exists():
        return []

    home_dir = home(root)
    home_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=home_dir, prefix=".builtin_tmp_"))
    try:
        created = []
        for cid, card in sorted(BUILTIN_ABILITIES.items()):
            clean = validate_ability(dict(card, source="builtin"))
            file_path = tmp_dir / f"{clean['id']}.json"
            file_path.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            created.append(cid)

        try:
            tmp_dir.rename(target)
        except (FileExistsError, OSError):
            if target.exists():
                return []
            raise
        return created
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def prepare(card: dict, inputs: dict) -> dict:
    """按 inputSchema 校验 inputs，返回准备好的运行参数和每一项的最终值。替换只做一遍。"""
    card = validate_ability(card)
    if not isinstance(inputs, dict):
        raise AbilityError("inputs 必须是一组字段")

    schema = card["inputSchema"]
    props = schema["properties"]
    req = schema.get("required", [])

    extra = sorted(set(inputs) - set(props))
    if extra:
        raise AbilityError(f"输入参数包含不认识的项：{'、'.join(extra)}")

    values = {}
    for k, p in props.items():
        title = p["title"]
        ptype = p["type"]
        val = inputs.get(k)

        if k in req:
            if val is None or (isinstance(val, str) and not val.strip()):
                raise AbilityError(f"必填项 {title} 没有填或只有空白")

        if val is not None and not (isinstance(val, str) and val == "" and k not in req and "default" not in p):
            if ptype == "string":
                if not isinstance(val, str):
                    raise AbilityError(f"{title} 必须是文字")
                if "maxLength" in p and len(val) > p["maxLength"]:
                    raise AbilityError(f"{title} 超过了最大字数限制 {p['maxLength']}")
                if "enum" in p and val not in p["enum"]:
                    raise AbilityError(f"{title} 必须在选项中选择")
                values[k] = val
            elif ptype == "integer":
                if not isinstance(val, int) or isinstance(val, bool):
                    raise AbilityError(f"{title} 必须是整数")
                if not (p["minimum"] <= val <= p["maximum"]):
                    raise AbilityError(f"{title} 必须在 {p['minimum']} 到 {p['maximum']} 之间")
                values[k] = val
        else:
            if "default" in p:
                values[k] = p["default"]
            else:
                values[k] = "" if ptype == "string" else None

    # 替换只做一遍，填入内容中的占位原样保留
    def expand_template(tpl: str) -> str:
        return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if values.get(m.group(1)) is not None else "", tpl)

    how = card["how"]
    kind = how["kind"]
    result = {"kind": kind, "values": values}

    if kind == "llm_api":
        result["system"] = expand_template(how.get("system", "")) if "system" in how else ""
        result["prompt"] = expand_template(how["prompt"])
    elif kind == "search":
        result["query"] = expand_template(how["query"])
        mr = how["max_results"]
        if isinstance(mr, str) and mr.startswith("{") and mr.endswith("}"):
            result["max_results"] = values[mr[1:-1]]
        else:
            result["max_results"] = mr

        tr = how["time_range"]
        if isinstance(tr, str) and tr.startswith("{") and tr.endswith("}"):
            choice = values[tr[1:-1]]
            result["time_range"] = TIME_RANGES[choice]
        else:
            result["time_range"] = tr
    elif kind == "cli":
        result["prompt"] = expand_template(how["prompt"])
        result["output_file"] = how["output_file"]
        result["timeout"] = how["timeout"]

    return result


def image_size(path: Path | str) -> tuple[int, int] | None:
    """只读文件头获取 PNG 或 JPEG 图片的宽高 (宽, 高)。PNG 必须有完整 IHDR（至少 33 字节）；JPEG 前 64KB 扫描。读不全、读不出返回 None。"""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as f:
            header = f.read(33)
            # PNG 检查: 必须有完整 IHDR 前 33 字节
            if header.startswith(b"\x89PNG\r\n\x1a\n"):
                if len(header) >= 33 and header[12:16] == b"IHDR":
                    w, h = struct.unpack(">II", header[16:24])
                    return (w, h)
                return None
            # JPEG 检查: \xff\xd8 开头，扫描最多前 64KB
            if header.startswith(b"\xff\xd8"):
                f.seek(2)
                scanned = 2
                while scanned < 65536:
                    b = f.read(1)
                    scanned += 1
                    if not b:
                        return None
                    if b != b"\xff":
                        continue
                    while b == b"\xff":
                        b = f.read(1)
                        scanned += 1
                        if not b:
                            return None
                    marker = b[0]
                    # SOF markers: 0xC0 .. 0xCF, 除了 0xC4, 0xC8, 0xCC
                    if (0xc0 <= marker <= 0xcf) and marker not in (0xc4, 0xc8, 0xcc):
                        length_bytes = f.read(2)
                        scanned += 2
                        if len(length_bytes) < 2:
                            return None
                        data = f.read(5)
                        scanned += 5
                        if len(data) < 5:
                            return None
                        _, h, w = struct.unpack(">BHH", data)
                        return (w, h)
                    elif marker in (0xd8, 0xd9, 0xda):
                        return None
                    else:
                        length_bytes = f.read(2)
                        scanned += 2
                        if len(length_bytes) < 2:
                            return None
                        length = struct.unpack(">H", length_bytes)[0]
                        if length < 2:
                            return None
                        f.seek(length - 2, os.SEEK_CUR)
                        scanned += (length - 2)
    except Exception:
        return None
    return None


def check_output(card: dict, result, values: dict) -> tuple[bool, str]:
    """检查能力卡片执行结果是否合格，通过返回 (True, "")，不通过返回 (False, 中文原因)。"""
    card = validate_ability(card)
    out = card["output"]
    out_type = out["type"]
    check = out.get("check", {})

    if out_type == "text":
        if not isinstance(result, str) or not result.strip():
            return False, "没有内容"
        char_count = len("".join(result.split()))

        if "min_chars" in check and char_count < check["min_chars"]:
            return False, f"字数不足：实际 {char_count} 字，要求不少于 {check['min_chars']} 字"
        if "max_chars" in check and char_count > check["max_chars"]:
            return False, f"字数过多：实际 {char_count} 字，要求不超过 {check['max_chars']} 字"

        if "of" in check:
            base = values.get(check["of"])
            if base is not None:
                if "min_ratio" in check:
                    min_exp = math.ceil(base * check["min_ratio"])
                    if char_count < min_exp:
                        return False, f"字数不足：实际 {char_count} 字，要求不少于 {min_exp} 字"
                if "max_ratio" in check:
                    max_exp = math.floor(base * check["max_ratio"])
                    if char_count > max_exp:
                        return False, f"字数过多：实际 {char_count} 字，要求不超过 {max_exp} 字"

        return True, ""

    elif out_type == "list":
        if not isinstance(result, list):
            return False, "结果不是列表"
        if not result:
            return False, "一条都没有"

        min_items = check.get("min_items", 1)
        if len(result) < min_items:
            return False, f"条数不足：实际 {len(result)} 条，要求至少 {min_items} 条"

        is_search = card["how"]["kind"] == "search"
        for i, item in enumerate(result, 1):
            if not isinstance(item, dict):
                return False, f"第 {i} 条不是一组字段"
            title = item.get("title")
            if not isinstance(title, str) or not title.strip():
                return False, f"第 {i} 条没有标题"
            if is_search:
                url = item.get("url")
                if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
                    return False, f"第 {i} 条没有链接"

        return True, ""

    elif out_type == "image":
        if not isinstance(result, (str, Path)):
            return False, "输出结果不是文件路径"
        path = Path(result)
        if not path.is_file():
            return False, f"没有找到 {path.name}"

        try:
            with open(path, "rb") as f:
                head = f.read(16)
        except Exception:
            return False, f"无法读取 {path.name}"

        if not head.startswith(b"\x89PNG\r\n\x1a\n") and not head.startswith(b"\xff\xd8"):
            return False, f"{path.name} 不是 PNG 或 JPG 图片"

        size = image_size(path)
        if size is None:
            return False, f"打不开 {path.name}"

        w, h = size
        min_side = check.get("min_side", 256)
        if w < min_side or h < min_side:
            return False, f"图片太小：{w}×{h}，要求宽高都不小于 {min_side}"

        return True, ""

    elif out_type == "file":
        if not isinstance(result, (str, Path)):
            return False, "输出结果不是文件路径"
        path = Path(result)
        if not path.is_file():
            return False, f"没有找到 {path.name}"

        try:
            if path.stat().st_size == 0:
                return False, f"{path.name} 是空文件"
        except Exception:
            return False, f"无法读取 {path.name}"

        exts = check.get("extensions", [])
        ext = path.suffix.lstrip(".").lower()
        if ext not in exts:
            return False, f"文件扩展名 .{ext} 不在允许的列表中"

        return True, ""

    return False, "未知输出类型"
