"""66 号单：能力卡片的格式、存放、填内容、查结果，三张预置能力卡片。

能力卡片 = 基础层某个积木块的一种固定用法。“你要填什么”照 MCP 工具的 inputSchema 写（只收一个小子集），
“交回什么”只有文字、列表、图片、文件四种，“怎么算干成了”由程序查。
依据：docs/积木库/能力层/方案.md 技术设计“能力卡片字段”“存放”。
这张单没有界面，也不真跑任何积木块（运行是 67 号单）。
"""
import copy
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from collab import abilities, blocks
from collab.abilities import AbilityError
from collab.blocks import BlockError


def png_bytes(w, h):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg_bytes(w, h):
    # SOI + APP0 + SOF0（只要文件头里的宽高能读出来）+ EOI
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xd9"


def text_card(**over):
    card = {
        "id": "write-demo", "title": "写短文", "description": "按主题写一篇短文",
        "block": "bailian", "model": "qwen-plus",
        "inputSchema": {"type": "object", "properties": {
            "topic": {"type": "string", "title": "主题", "maxLength": 200},
            "words": {"type": "integer", "title": "字数", "minimum": 10, "maximum": 5000, "default": 100},
            "notes": {"type": "string", "title": "参考资料", "maxLength": 8000}},
            "required": ["topic"]},
        "how": {"kind": "llm_api", "system": "只写正文。", "prompt": "写{topic}，约{words}字。{notes}"},
        "output": {"type": "text", "check": {"min_ratio": 0.5, "max_ratio": 2.0, "of": "words"}},
    }
    card.update(over)
    return card


def search_card(**over):
    card = {
        "id": "search-demo", "title": "搜一搜", "description": "按关键词搜", "block": "web-search",
        "inputSchema": {"type": "object", "properties": {
            "q": {"type": "string", "title": "关键词", "maxLength": 200},
            "n": {"type": "integer", "title": "条数", "minimum": 1, "maximum": 20, "default": 8},
            "when": {"type": "string", "title": "时间范围", "enum": ["不限", "最近一周"], "default": "不限"}},
            "required": ["q"]},
        "how": {"kind": "search", "query": "{q}", "max_results": "{n}", "time_range": "{when}"},
        "output": {"type": "list", "check": {"min_items": 1}},
    }
    card.update(over)
    return card


def image_card(**over):
    card = {
        "id": "draw-demo", "title": "画图", "description": "画一张图", "block": "codex",
        "inputSchema": {"type": "object", "properties": {
            "scene": {"type": "string", "title": "画面描述", "maxLength": 1000},
            "shape": {"type": "string", "title": "画幅", "enum": ["横版", "竖版"], "default": "横版"}},
            "required": ["scene"]},
        "how": {"kind": "cli", "prompt": "画{scene}，{shape}，存成 输出.png", "output_file": "输出.png"},
        "output": {"type": "image", "check": {"min_side": 256}},
    }
    card.update(over)
    return card


def api_block(**over):
    b = {"id": "bailian", "kind": "llm_api", "name": "阿里百炼", "provider": "bailian", "key_account": "bailian",
         "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}
    b.update(over)
    return blocks.validate_card(b)


def codex_block():
    return blocks.validate_card(dict(blocks.BUILTIN["codex"], source="builtin"))


def tool_block():
    return blocks.validate_card({"id": "maptool", "kind": "cli", "role": "tool", "name": "地图工具",
                                 "command": "map-tool", "probe": ["search", "--limit", "1"]})


def tavily_block():
    return blocks.search_card("tavily")


class Case(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "积木库"
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def assertRefused(self, card, *words):
        with self.assertRaises(AbilityError) as ctx:
            abilities.validate_ability(card)
        msg = str(ctx.exception)
        self.assertRegex(msg, r"[一-鿿]", "拒收原因要用中文说")
        for w in words:
            self.assertIn(w, msg)
        return msg


# ---------- 基本的东西 ----------

class Basics(Case):
    def test_error_is_block_error(self):
        # 页面和管家那边已经按 BlockError 接住错误，能力卡片的错也要能被同样接住
        self.assertTrue(issubclass(AbilityError, BlockError))

    def test_labels(self):
        self.assertEqual(abilities.OUTPUT_LABELS, {"text": "文字", "list": "列表", "image": "图片", "file": "文件"})
        self.assertEqual(abilities.TIME_RANGES, {"不限": "none", "最近一天": "day", "最近一周": "week",
                                                 "最近一个月": "month"})

    def test_three_good_cards_pass_and_get_defaults(self):
        for make in (text_card, search_card, image_card):
            original = make()
            kept = copy.deepcopy(original)
            card = abilities.validate_ability(original)
            self.assertEqual(original, kept, "不能改传进来的那份")
            self.assertEqual(card["source"], "clerk")
            self.assertIsNone(card["example_run"])
            self.assertEqual(card["backup_models"], [])

    def test_cli_card_gets_default_timeout(self):
        self.assertEqual(abilities.validate_ability(image_card())["how"]["timeout"], 300)

    def test_search_defaults(self):
        c = search_card(how={"kind": "search", "query": "{q}"},
                        inputSchema={"type": "object", "properties": {
                            "q": {"type": "string", "title": "关键词", "maxLength": 200}}, "required": ["q"]})
        how = abilities.validate_ability(c)["how"]
        self.assertEqual(how["max_results"], 8)
        self.assertEqual(how["time_range"], "none")


# ---------- 顶层字段 ----------

class TopFields(Case):
    def test_not_a_dict(self):
        with self.assertRaises(AbilityError):
            abilities.validate_ability(["x"])

    def test_bad_id(self):
        for bad in ("", "Write", "-x", "写文章", "a" * 41, "a b", None):
            self.assertRefused(text_card(id=bad), "编号")

    def test_unknown_field(self):
        self.assertRefused(text_card(color="red"), "color")

    def test_title_and_description(self):
        self.assertRefused(text_card(title=""), "名字")
        self.assertRefused(text_card(title="字" * 21), "名字")
        self.assertRefused(text_card(description=" "), "说明")
        self.assertRefused(text_card(description="字" * 201), "说明")

    def test_block_id(self):
        self.assertRefused(text_card(block="Bai Lian"), "积木块")
        self.assertRefused(text_card(block=None), "积木块")

    def test_source(self):
        self.assertEqual(abilities.validate_ability(text_card(source="builtin"))["source"], "builtin")
        self.assertRefused(text_card(source="web"), "来源")

    def test_example_run(self):
        ok = abilities.validate_ability(text_card(example_run="20261111-120000-ab12"))
        self.assertEqual(ok["example_run"], "20261111-120000-ab12")
        self.assertRefused(text_card(example_run="../x"), "示例")

    def test_secret_anywhere_refused(self):
        self.assertRefused(text_card(how={"kind": "llm_api", "prompt": "写{topic}{words}{notes} sk-abcdefghijklmnopqrstuv"}),
                           "密钥")
        self.assertRefused(text_card(description="api_key=abcdef123456"), "密钥")


class Models(Case):
    def test_model_rules(self):
        self.assertRefused(text_card(model="-m"), "模型")
        self.assertRefused(text_card(model="有 空格"), "模型")

    def test_llm_api_needs_model(self):
        c = text_card()
        del c["model"]
        self.assertRefused(c, "模型")

    def test_cli_model_optional(self):
        self.assertNotIn("model", abilities.validate_ability(image_card()))
        self.assertEqual(abilities.validate_ability(image_card(model="gpt-6.1-sol"))["model"], "gpt-6.1-sol")

    def test_search_has_no_model(self):
        self.assertRefused(search_card(model="x"), "模型")
        self.assertRefused(search_card(backup_models=["x"]), "模型")

    def test_backup_models(self):
        ok = abilities.validate_ability(text_card(backup_models=["qwen-max", "qwen-turbo"]))
        self.assertEqual(ok["backup_models"], ["qwen-max", "qwen-turbo"])
        self.assertRefused(text_card(backup_models=["a", "b", "c"]), "备用")
        self.assertRefused(text_card(backup_models=["qwen-plus"]), "备用")     # 和主模型一样
        self.assertRefused(text_card(backup_models=["a", "a"]), "备用")
        self.assertRefused(text_card(backup_models="a"), "备用")
        self.assertRefused(text_card(backup_models=["-x"]), "模型")
        self.assertRefused(image_card(backup_models=["a"]), "备用")             # 没有主模型就不能写备用


# ---------- 你要填什么（inputSchema 小子集） ----------

def with_props(make, props, required=None, how=None):
    c = make()
    c["inputSchema"] = {"type": "object", "properties": props, "required": required or []}
    c["output"] = {"type": c["output"]["type"]}   # 换掉了输入项，原来按某一项算字数的检查也去掉
    if how is not None:
        c["how"] = how
    return c


class InputSchema(Case):
    def test_top_shape(self):
        c = text_card()
        c["inputSchema"]["type"] = "array"
        self.assertRefused(c, "object")
        c = text_card()
        c["inputSchema"]["additionalProperties"] = False
        self.assertRefused(c, "additionalProperties")
        c = text_card()
        c["inputSchema"] = []
        self.assertRefused(c)

    def test_need_one_to_ten(self):
        self.assertRefused(with_props(text_card, {}, how={"kind": "llm_api", "prompt": "写点什么"}), "1 到 10")
        props = {f"k{i}": {"type": "string", "title": "项", "maxLength": 10} for i in range(11)}
        prompt = "".join("{k%d}" % i for i in range(11))
        self.assertRefused(with_props(text_card, props, how={"kind": "llm_api", "prompt": prompt}), "1 到 10")

    def test_key_names(self):
        for bad in ("Topic", "主题", "1x", "a-b", "a" * 33):
            props = {bad: {"type": "string", "title": "主题", "maxLength": 10}}
            self.assertRefused(with_props(text_card, props, how={"kind": "llm_api", "prompt": "{%s}" % bad}), "项名")

    def test_title_required(self):
        props = {"topic": {"type": "string", "maxLength": 10}}
        self.assertRefused(with_props(text_card, props, ["topic"], {"kind": "llm_api", "prompt": "{topic}"}), "title")
        props = {"topic": {"type": "string", "title": "字" * 21, "maxLength": 10}}
        self.assertRefused(with_props(text_card, props, ["topic"], {"kind": "llm_api", "prompt": "{topic}"}), "title")

    def test_only_string_and_integer(self):
        for t in ("number", "boolean", "array", "object", None):
            props = {"topic": {"type": t, "title": "主题"}}
            self.assertRefused(with_props(text_card, props, ["topic"], {"kind": "llm_api", "prompt": "{topic}"}),
                               "string", "integer")

    def test_unknown_keyword_refused(self):
        props = {"topic": {"type": "string", "title": "主题", "maxLength": 10, "pattern": ".*"}}
        self.assertRefused(with_props(text_card, props, ["topic"], {"kind": "llm_api", "prompt": "{topic}"}), "pattern")

    def test_string_needs_maxlength_or_enum(self):
        props = {"topic": {"type": "string", "title": "主题"}}
        self.assertRefused(with_props(text_card, props, ["topic"], {"kind": "llm_api", "prompt": "{topic}"}),
                           "maxLength")
        for bad in (0, 8001, "10", True):
            props = {"topic": {"type": "string", "title": "主题", "maxLength": bad}}
            self.assertRefused(with_props(text_card, props, ["topic"], {"kind": "llm_api", "prompt": "{topic}"}),
                               "maxLength")

    def test_enum(self):
        def card(enum, default=None):
            p = {"type": "string", "title": "画幅", "enum": enum}
            if default is not None:
                p["default"] = default
            return with_props(text_card, {"shape": p}, ["shape"], {"kind": "llm_api", "prompt": "{shape}"})
        abilities.validate_ability(card(["横版", "竖版"], "竖版"))
        self.assertRefused(card(["只有一个"]), "enum")
        self.assertRefused(card([str(i) for i in range(11)]), "enum")
        self.assertRefused(card(["横版", "横版"]), "enum")
        self.assertRefused(card(["横版", ""]), "enum")
        self.assertRefused(card(["横版", 3]), "enum")
        self.assertRefused(card(["横版", "竖版"], "方形"), "default")

    def test_integer_needs_range(self):
        def card(**p):
            base = {"type": "integer", "title": "字数"}
            base.update(p)
            return with_props(text_card, {"words": base}, ["words"], {"kind": "llm_api", "prompt": "{words}"})
        abilities.validate_ability(card(minimum=1, maximum=10, default=5))
        self.assertRefused(card(maximum=10), "minimum")
        self.assertRefused(card(minimum=1), "maximum")
        self.assertRefused(card(minimum=10, maximum=1), "minimum")
        self.assertRefused(card(minimum=-1, maximum=10), "minimum")
        self.assertRefused(card(minimum=1, maximum=100001), "maximum")
        self.assertRefused(card(minimum=1.5, maximum=10), "minimum")
        self.assertRefused(card(minimum=1, maximum=10, default=11), "default")
        self.assertRefused(card(minimum=1, maximum=10, default="5"), "default")
        self.assertRefused(card(minimum=1, maximum=10, maxLength=3), "maxLength")

    def test_default_and_examples_types(self):
        props = {"topic": {"type": "string", "title": "主题", "maxLength": 5, "default": "太长了太长了"}}
        self.assertRefused(with_props(text_card, props, ["topic"], {"kind": "llm_api", "prompt": "{topic}"}), "default")
        props = {"topic": {"type": "string", "title": "主题", "maxLength": 20, "examples": ["a", "b", "c", "d"]}}
        self.assertRefused(with_props(text_card, props, ["topic"], {"kind": "llm_api", "prompt": "{topic}"}), "examples")
        props = {"topic": {"type": "string", "title": "主题", "maxLength": 20, "examples": [3]}}
        self.assertRefused(with_props(text_card, props, ["topic"], {"kind": "llm_api", "prompt": "{topic}"}), "examples")
        props = {"topic": {"type": "string", "title": "主题", "maxLength": 20, "description": "字" * 101}}
        self.assertRefused(with_props(text_card, props, ["topic"], {"kind": "llm_api", "prompt": "{topic}"}),
                           "description")

    def test_required(self):
        c = text_card()
        c["inputSchema"]["required"] = ["nope"]
        self.assertRefused(c, "nope")
        c = text_card()
        c["inputSchema"]["required"] = ["topic", "topic"]
        self.assertRefused(c, "required")
        c = text_card()
        c["inputSchema"]["required"] = "topic"
        self.assertRefused(c, "required")

    def test_required_missing_is_fine(self):
        c = text_card()
        del c["inputSchema"]["required"]
        self.assertEqual(abilities.validate_ability(c)["inputSchema"]["required"], [])


# ---------- 怎么执行（how）和模板 ----------

class How(Case):
    def test_kind(self):
        self.assertRefused(text_card(how={"kind": "shell", "prompt": "{topic}"}), "search", "llm_api", "cli")

    def test_fields_per_kind(self):
        self.assertRefused(text_card(how={"kind": "llm_api", "prompt": "{topic}{words}{notes}", "query": "x"}), "query")
        self.assertRefused(search_card(how={"kind": "search", "query": "{q}", "prompt": "x"}), "prompt")
        self.assertRefused(image_card(how={"kind": "cli", "prompt": "{scene}{shape}", "output_file": "输出.png",
                                          "command": "rm"}), "command")

    def test_unknown_placeholder(self):
        self.assertRefused(text_card(how={"kind": "llm_api", "prompt": "写{topic}{words}{notes}{who}"}), "who")
        self.assertRefused(text_card(how={"kind": "llm_api", "prompt": "写{topic}{words}{notes}{}"}), "占位")

    def test_required_must_be_used(self):
        self.assertRefused(text_card(how={"kind": "llm_api", "prompt": "随便写点{words}{notes}"}), "topic")

    def test_placeholder_in_system_counts(self):
        abilities.validate_ability(text_card(how={"kind": "llm_api", "system": "主题是{topic}", "prompt": "写{words}字"}))

    def test_template_length(self):
        self.assertRefused(text_card(how={"kind": "llm_api", "prompt": "{topic}" + "字" * 4000}), "4000")
        self.assertRefused(text_card(how={"kind": "llm_api", "prompt": "  "}), "交代")

    def test_search_limits(self):
        self.assertRefused(search_card(how={"kind": "search", "query": "{q}", "max_results": 21}), "1 到 20")
        self.assertRefused(search_card(how={"kind": "search", "query": "{q}", "max_results": 0}), "1 到 20")
        self.assertRefused(search_card(how={"kind": "search", "query": "{q}", "time_range": "year"}), "time_range")
        abilities.validate_ability(search_card(how={"kind": "search", "query": "{q}", "max_results": 5,
                                                    "time_range": "week"}))

    def test_search_reference_must_fit(self):
        # max_results 引用的必须是整数项，范围在 1 到 20 里
        c = search_card()
        c["inputSchema"]["properties"]["n"]["maximum"] = 50
        self.assertRefused(c, "1 到 20")
        c = search_card(how={"kind": "search", "query": "{q}", "max_results": "{q}"})
        self.assertRefused(c, "整数")
        # time_range 引用的必须是 enum，而且每个选项都是认得的时间范围
        c = search_card()
        c["inputSchema"]["properties"]["when"]["enum"] = ["不限", "最近十年"]
        self.assertRefused(c, "最近十年")
        c = search_card(how={"kind": "search", "query": "{q}", "time_range": "{q}"})
        self.assertRefused(c, "时间范围")

    def test_cli_fields(self):
        self.assertRefused(image_card(how={"kind": "cli", "prompt": "{scene}{shape}"}), "output_file")
        self.assertRefused(image_card(how={"kind": "cli", "prompt": "{scene}{shape}", "output_file": "../x.png"}),
                           "文件名")
        self.assertRefused(image_card(how={"kind": "cli", "prompt": "{scene}{shape}", "output_file": "a/b.png"}),
                           "文件名")
        for t in (29, 601, "300", True):
            self.assertRefused(image_card(how={"kind": "cli", "prompt": "{scene}{shape}", "output_file": "输出.png",
                                              "timeout": t}), "timeout")


# ---------- 交回什么（output） ----------

class Output(Case):
    def test_type(self):
        self.assertRefused(text_card(output={"type": "video"}), "文字", "列表", "图片", "文件")
        self.assertRefused(text_card(output={"type": "text", "color": 1}), "color")

    def test_kind_and_type_must_match(self):
        self.assertRefused(text_card(output={"type": "list"}), "文字")       # 大模型 API 这一版只交回文字
        self.assertRefused(search_card(output={"type": "text"}), "列表")     # 搜索只交回列表
        self.assertRefused(image_card(output={"type": "list"}))              # 命令行工具不交回列表

    def test_text_checks(self):
        abilities.validate_ability(text_card(output={"type": "text", "check": {"min_chars": 10, "max_chars": 100}}))
        abilities.validate_ability(text_card(output={"type": "text"}))
        self.assertRefused(text_card(output={"type": "text", "check": {"min_chars": 100, "max_chars": 10}}), "字数")
        self.assertRefused(text_card(output={"type": "text", "check": {"min_chars": 10, "min_ratio": 0.5,
                                                                      "of": "words"}}), "字数")
        self.assertRefused(text_card(output={"type": "text", "check": {"min_ratio": 0.5, "of": "topic"}}), "整数")
        self.assertRefused(text_card(output={"type": "text", "check": {"min_ratio": 0.5, "of": "nope"}}), "nope")
        self.assertRefused(text_card(output={"type": "text", "check": {"min_ratio": 0, "of": "words"}}), "倍")
        self.assertRefused(text_card(output={"type": "text", "check": {"max_ratio": 11, "of": "words"}}), "倍")
        self.assertRefused(text_card(output={"type": "text", "check": {"min_ratio": 0.5}}), "of")

    def test_list_image_file_checks(self):
        self.assertRefused(search_card(output={"type": "list", "check": {"min_items": 0}}), "条")
        self.assertRefused(search_card(output={"type": "list", "check": {"min_items": 51}}), "条")
        self.assertEqual(abilities.validate_ability(search_card(output={"type": "list"}))["output"]["check"]["min_items"], 1)
        self.assertEqual(abilities.validate_ability(image_card(output={"type": "image"}))["output"]["check"]["min_side"], 256)
        self.assertRefused(image_card(output={"type": "image", "check": {"min_side": 8}}), "宽高")
        self.assertRefused(image_card(output={"type": "image", "check": {"min_side": 5000}}), "宽高")
        self.assertRefused(image_card(how={"kind": "cli", "prompt": "{scene}{shape}", "output_file": "输出.gif"}),
                           "png")
        c = image_card(how={"kind": "cli", "prompt": "{scene}{shape}", "output_file": "结果.md"},
                       output={"type": "file", "check": {"extensions": ["md", "txt"]}})
        abilities.validate_ability(c)
        self.assertRefused(image_card(how={"kind": "cli", "prompt": "{scene}{shape}", "output_file": "结果.md"},
                                      output={"type": "file"}), "extensions")
        self.assertRefused(image_card(how={"kind": "cli", "prompt": "{scene}{shape}", "output_file": "结果.md"},
                                      output={"type": "file", "check": {"extensions": ["pdf"]}}), "md")
        self.assertRefused(image_card(how={"kind": "cli", "prompt": "{scene}{shape}", "output_file": "结果.md"},
                                      output={"type": "file", "check": {"extensions": [".md"]}}), "扩展名")


# ---------- 和积木块对不对得上 ----------

class FitsBlock(Case):
    def test_kinds_must_match(self):
        self.assertEqual(abilities.fits_block(text_card(), api_block()), [])
        self.assertEqual(abilities.fits_block(search_card(), tavily_block()), [])
        self.assertEqual(abilities.fits_block(image_card(), codex_block()), [])
        with self.assertRaises(AbilityError) as ctx:
            abilities.fits_block(text_card(block="codex"), codex_block())   # 编号对得上、类别对不上
        self.assertIn("大模型 API", str(ctx.exception))
        with self.assertRaises(AbilityError):
            abilities.fits_block(image_card(), api_block())
        with self.assertRaises(AbilityError) as ctx:
            abilities.fits_block(image_card(block="maptool"), tool_block())   # 普通工具不接交代
        self.assertIn("AI 助手", str(ctx.exception))
        with self.assertRaises(AbilityError):
            abilities.fits_block(search_card(), api_block())

    def test_block_id_must_match(self):
        with self.assertRaises(AbilityError) as ctx:
            abilities.fits_block(text_card(block="other"), api_block())
        self.assertIn("other", str(ctx.exception))

    def test_model_list(self):
        names = ["qwen-plus", "qwen-max"]
        self.assertEqual(abilities.fits_block(text_card(backup_models=["qwen-max"]), api_block(), names), [])
        with self.assertRaises(AbilityError) as ctx:
            abilities.fits_block(text_card(model="nope"), api_block(), names)
        self.assertIn("nope", str(ctx.exception))
        with self.assertRaises(AbilityError):
            abilities.fits_block(text_card(backup_models=["nope"]), api_block(), names)
        # 名单可能没拿全：不拒收，只给提醒
        warns = abilities.fits_block(text_card(model="nope"), api_block(), names, models_complete=False)
        self.assertTrue(warns and "nope" in warns[0])
        # 没有名单：不查
        self.assertEqual(abilities.fits_block(text_card(model="nope"), api_block(), None), [])


# ---------- 存放 ----------

class Storage(Case):
    def test_save_load_list_remove(self):
        saved = abilities.save_ability(text_card(), self.root)
        path = self.root / "能力层" / "write-demo.json"
        self.assertTrue(path.is_file())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), saved)
        self.assertEqual(abilities.load_ability("write-demo", self.root), saved)
        abilities.save_ability(search_card(), self.root)
        ids = [c["id"] for c in abilities.list_abilities(self.root)]
        self.assertEqual(ids, ["search-demo", "write-demo"])
        abilities.add_run("write-demo", {"type": "run", "ok": True}, self.root)
        abilities.remove_ability("write-demo", self.root)
        self.assertFalse(path.exists())
        self.assertEqual(len(abilities.runs("write-demo", self.root)), 1, "移出能力层，记录留着")
        with self.assertRaises(AbilityError):
            abilities.load_ability("write-demo", self.root)
        with self.assertRaises(AbilityError):
            abilities.remove_ability("write-demo", self.root)

    def test_save_refuses_bad(self):
        with self.assertRaises(AbilityError):
            abilities.save_ability(text_card(id="BAD"), self.root)
        self.assertFalse((self.root / "能力层").exists() and any((self.root / "能力层").glob("*.json")))

    def test_does_not_touch_base_layer(self):
        abilities.save_ability(text_card(), self.root)
        self.assertFalse((self.root / "基础层").exists())

    def test_bad_file_listed_with_problem(self):
        folder = self.root / "能力层"
        folder.mkdir(parents=True)
        (folder / "broken.json").write_text("{坏了", encoding="utf-8")
        (folder / "wrongid.json").write_text(json.dumps(text_card()), encoding="utf-8")
        abilities.save_ability(search_card(), self.root)
        items = {c["id"]: c for c in abilities.list_abilities(self.root)}
        self.assertIn("problem", items["broken"])
        self.assertIn("problem", items["wrongid"])
        self.assertNotIn("problem", items["search-demo"])

    def test_load_bad_id(self):
        with self.assertRaises(AbilityError):
            abilities.load_ability("../基础层/codex", self.root)

    def test_runs(self):
        for i in range(3):
            abilities.add_run("write-demo", {"type": "run", "ok": i == 2, "n": i}, self.root)
        self.assertTrue((self.root / "记录" / "能力" / "write-demo.jsonl").is_file())
        all_runs = abilities.runs("write-demo", self.root)
        self.assertEqual([r["n"] for r in all_runs], [0, 1, 2])
        self.assertTrue(all("time" in r for r in all_runs))
        self.assertEqual([r["n"] for r in abilities.runs("write-demo", self.root, limit=2)], [1, 2])
        self.assertEqual(abilities.runs("nobody", self.root), [])
        with self.assertRaises(BlockError):
            abilities.add_run("write-demo", {"type": "run", "said": "sk-abcdefghijklmnopqrstuvwx"}, self.root)
        with self.assertRaises(BlockError):
            abilities.add_run("write-demo", {"ok": True}, self.root)       # 要写 type

    def test_result_folder(self):
        folder = abilities.result_folder("write-demo", "20261111-120000-ab12", self.root)
        self.assertEqual(folder, self.root / "成果" / "write-demo" / "20261111-120000-ab12")
        self.assertFalse(folder.exists(), "只算路径，不建文件夹")
        with self.assertRaises(AbilityError):
            abilities.result_folder("write-demo", "../../x", self.root)

    def test_new_run_id(self):
        a, b = abilities.new_run_id(), abilities.new_run_id()
        self.assertNotEqual(a, b)
        self.assertRegex(a, r"^\d{8}-\d{6}-[0-9a-f]{4}$")
        # 运行编号能当示例编号用
        abilities.validate_ability(text_card(example_run=a))


# ---------- 预置三张 ----------

class Builtin(Case):
    def test_three_presets_valid(self):
        self.assertEqual(sorted(abilities.BUILTIN_ABILITIES), ["draw-image", "search-web", "write-article"])
        for cid, card in abilities.BUILTIN_ABILITIES.items():
            clean = abilities.validate_ability(dict(card, source="builtin"))
            self.assertEqual(clean["id"], cid)

    def test_presets_shape(self):
        s = abilities.BUILTIN_ABILITIES["search-web"]
        self.assertEqual((s["block"], s["how"]["kind"], s["output"]["type"]), ("web-search", "search", "list"))
        self.assertEqual(s["title"], "搜资料")
        w = abilities.BUILTIN_ABILITIES["write-article"]
        self.assertEqual((w["block"], w["model"], w["backup_models"]), ("bailian", "qwen3.7-plus", ["qwen3.6-plus"]))
        self.assertEqual((w["how"]["kind"], w["output"]["type"], w["title"]), ("llm_api", "text", "写文章"))
        d = abilities.BUILTIN_ABILITIES["draw-image"]
        self.assertEqual((d["block"], d["how"]["kind"], d["output"]["type"], d["title"]),
                         ("codex", "cli", "image", "出图"))
        self.assertIn("$imagegen", d["how"]["prompt"])
        self.assertTrue(d["how"]["output_file"].endswith(".png"))

    def test_presets_fit_their_blocks(self):
        b = abilities.BUILTIN_ABILITIES
        abilities.fits_block(b["search-web"], tavily_block())
        abilities.fits_block(b["draw-image"], codex_block())
        abilities.fits_block(b["write-article"], api_block(), ["qwen3.7-plus", "qwen3.6-plus"])

    def test_ensure_builtin_first_time_only(self):
        made = abilities.ensure_builtin(self.root)
        self.assertEqual(sorted(made), ["draw-image", "search-web", "write-article"])
        for cid in made:
            self.assertEqual(abilities.load_ability(cid, self.root)["source"], "builtin")
        # 用户移出一张后，再打开不会自己冒回来
        abilities.remove_ability("draw-image", self.root)
        self.assertEqual(abilities.ensure_builtin(self.root), [])
        self.assertEqual(sorted(c["id"] for c in abilities.list_abilities(self.root)), ["search-web", "write-article"])

    def test_ensure_builtin_does_not_overwrite(self):
        abilities.ensure_builtin(self.root)
        card = abilities.load_ability("write-article", self.root)
        card["model"] = "qwen3.6-plus"
        card["backup_models"] = []
        abilities.save_ability(card, self.root)
        abilities.ensure_builtin(self.root)
        self.assertEqual(abilities.load_ability("write-article", self.root)["model"], "qwen3.6-plus")


# ---------- 填内容 ----------

class Prepare(Case):
    def test_text_card(self):
        p = abilities.prepare(text_card(), {"topic": "春天"})
        self.assertEqual(p["values"], {"topic": "春天", "words": 100, "notes": ""})
        self.assertEqual(p["prompt"], "写春天，约100字。")
        self.assertEqual(p["system"], "只写正文。")

    def test_no_second_expansion(self):
        p = abilities.prepare(text_card(), {"topic": "{words}", "words": 50})
        self.assertEqual(p["prompt"], "写{words}，约50字。")

    def test_input_errors(self):
        card = text_card()
        cases = [
            ({}, "主题"),                                   # 必填没填，用 title 说
            ({"topic": "  "}, "主题"),
            ({"topic": "字" * 201}, "200"),
            ({"topic": "春天", "words": "100"}, "字数"),
            ({"topic": "春天", "words": 5}, "字数"),
            ({"topic": "春天", "words": True}, "字数"),
            ({"topic": "春天", "who": "x"}, "who"),
            ("春天", None),
        ]
        for inputs, word in cases:
            with self.assertRaises(AbilityError, msg=repr(inputs)) as ctx:
                abilities.prepare(card, inputs)
            if word:
                self.assertIn(word, str(ctx.exception))

    def test_enum_input(self):
        p = abilities.prepare(image_card(), {"scene": "猫"})
        self.assertEqual(p["values"]["shape"], "横版")
        self.assertEqual(p["prompt"], "画猫，横版，存成 输出.png")
        self.assertEqual(p["output_file"], "输出.png")
        self.assertEqual(p["timeout"], 300)
        with self.assertRaises(AbilityError) as ctx:
            abilities.prepare(image_card(), {"scene": "猫", "shape": "方形"})
        self.assertIn("画幅", str(ctx.exception))

    def test_search(self):
        p = abilities.prepare(search_card(), {"q": "上海 公园", "when": "最近一周"})
        self.assertEqual((p["query"], p["max_results"], p["time_range"]), ("上海 公园", 8, "week"))
        p = abilities.prepare(search_card(how={"kind": "search", "query": "{q} 攻略", "max_results": 3,
                                               "time_range": "month"}), {"q": "杭州"})
        self.assertEqual((p["query"], p["max_results"], p["time_range"]), ("杭州 攻略", 3, "month"))

    def test_preset_examples_are_valid_inputs(self):
        # 每张预置卡的 examples 本身要能填进去（页面拿它当占位提示）
        for card in abilities.BUILTIN_ABILITIES.values():
            props = card["inputSchema"]["properties"]
            sample = {k: p["examples"][0] for k, p in props.items() if p.get("examples")}
            for k in card["inputSchema"].get("required", []):
                self.assertIn(k, sample, f"{card['id']} 的必填项 {k} 要给一个例子")
            abilities.prepare(card, sample)


# ---------- 查结果 ----------

class CheckOutput(Case):
    def test_text(self):
        card = abilities.validate_ability(text_card())
        values = {"topic": "x", "words": 100, "notes": ""}
        self.assertEqual(abilities.check_output(card, "字" * 60, values), (True, ""))
        ok, why = abilities.check_output(card, "  \n ", values)
        self.assertFalse(ok)
        self.assertIn("没有内容", why)
        ok, why = abilities.check_output(card, "字" * 20, values)
        self.assertFalse(ok)
        self.assertIn("20", why)
        self.assertIn("50", why)
        ok, why = abilities.check_output(card, "字" * 201, values)
        self.assertFalse(ok)
        self.assertIn("200", why)
        # 空白不算字
        ok, _ = abilities.check_output(card, "字 " * 49 + "\n" * 30, values)
        self.assertFalse(ok)
        ok, why = abilities.check_output(card, None, values)
        self.assertFalse(ok)

    def test_text_fixed(self):
        card = abilities.validate_ability(text_card(output={"type": "text", "check": {"min_chars": 3, "max_chars": 5}}))
        self.assertTrue(abilities.check_output(card, "四个字的", {})[0])
        self.assertFalse(abilities.check_output(card, "两字", {})[0])
        self.assertFalse(abilities.check_output(card, "六个字的句子", {})[0])

    def test_list(self):
        card = abilities.validate_ability(search_card(output={"type": "list", "check": {"min_items": 2}}))
        good = [{"title": "a", "url": "https://a.cn", "summary": "s"}, {"title": "b", "url": "http://b.cn"}]
        self.assertEqual(abilities.check_output(card, good, {}), (True, ""))
        ok, why = abilities.check_output(card, [], {})
        self.assertFalse(ok)
        self.assertIn("一条都没有", why)
        ok, why = abilities.check_output(card, good[:1], {})
        self.assertFalse(ok)
        self.assertIn("1", why)
        ok, why = abilities.check_output(card, [good[0], {"title": "", "url": "https://x.cn"}], {})
        self.assertFalse(ok)
        self.assertIn("第 2 条", why)
        self.assertIn("标题", why)
        ok, why = abilities.check_output(card, [good[0], {"title": "c", "url": "ftp://x"}], {})
        self.assertFalse(ok)
        self.assertIn("链接", why)
        self.assertFalse(abilities.check_output(card, "不是列表", {})[0])

    def test_image(self):
        card = abilities.validate_ability(image_card())
        p = self.dir / "输出.png"
        ok, why = abilities.check_output(card, p, {})
        self.assertFalse(ok)
        self.assertIn("输出.png", why)
        p.write_bytes(png_bytes(512, 300))
        self.assertEqual(abilities.check_output(card, p, {}), (True, ""))
        self.assertEqual(abilities.image_size(p), (512, 300))
        p.write_bytes(png_bytes(512, 100))
        ok, why = abilities.check_output(card, p, {})
        self.assertFalse(ok)
        self.assertIn("512×100", why)
        p.write_bytes(b"not an image at all")
        ok, why = abilities.check_output(card, p, {})
        self.assertFalse(ok)
        self.assertIn("不是 PNG 或 JPG", why)
        self.assertIsNone(abilities.image_size(p))
        p.write_bytes(png_bytes(512, 512)[:20])     # 半截文件
        self.assertFalse(abilities.check_output(card, p, {})[0])

    def test_jpeg(self):
        card = abilities.validate_ability(image_card(how={"kind": "cli", "prompt": "{scene}{shape}",
                                                          "output_file": "out.jpg"}))
        p = self.dir / "out.jpg"
        p.write_bytes(jpeg_bytes(800, 600))
        self.assertEqual(abilities.image_size(p), (800, 600))
        self.assertTrue(abilities.check_output(card, p, {})[0])

    def test_file(self):
        card = abilities.validate_ability(image_card(how={"kind": "cli", "prompt": "{scene}{shape}",
                                                          "output_file": "结果.md"},
                                                     output={"type": "file", "check": {"extensions": ["md"]}}))
        p = self.dir / "结果.md"
        self.assertFalse(abilities.check_output(card, p, {})[0])
        p.write_text("", encoding="utf-8")
        ok, why = abilities.check_output(card, p, {})
        self.assertFalse(ok)
        self.assertIn("空", why)
        p.write_text("# 标题", encoding="utf-8")
        self.assertTrue(abilities.check_output(card, p, {})[0])
        q = self.dir / "结果.txt"
        q.write_text("x", encoding="utf-8")
        self.assertFalse(abilities.check_output(card, q, {})[0])


# ---------- 守住原有的东西 ----------

class Untouched(Case):
    def test_base_layer_still_works(self):
        saved = blocks.save_card(dict(blocks.BUILTIN["codex"], source="builtin"), self.root)
        abilities.ensure_builtin(self.root)
        self.assertEqual(blocks.load_card("codex", self.root), saved)
        self.assertEqual([c["id"] for c in blocks.list_cards(self.root)], ["codex"], "能力卡片不能混进基础层")
        blocks.add_record("codex", {"type": "check", "ok": True}, self.root)
        abilities.add_run("codex", {"type": "run", "ok": True}, self.root)
        self.assertEqual(len(blocks.records("codex", self.root)), 1, "能力卡片的记录不能写进积木块的记录")


if __name__ == "__main__":
    unittest.main()
