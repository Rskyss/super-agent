"""跑卡卡点修理 卡 A（施工单缺节就不开工）补充测试。

只补验收测试（tests/test_card_gap_a_acceptance.py）没盯死的边界：
1. P1 验收命令提取：同一节里说明文字在前、正式行在后；空/无反引号；防止跨行误抓走查命令；
   非行首“- 验收命令：”写法不认；老格式 order_gaps 与生成一致；_plan_from_order 与检查共用规则。
2. P2 order_gaps 边界：编码错误（非 UTF-8 坏字节）只报“读不了”；三项全缺；
   “要做什么”空正文；“允许改”仅在“不许改”后出现路径；
   “没填完”覆盖验收命令/做法步骤/不做什么/多处去重，且无关节（状态表）的空白不误报。
3. P3 拦截与作废：新卡与已有成功方案的卡失败后，均删除开工.json、把方案.md 写回空白模板、
   状态保持待对齐且批准被拦；补好后重写可恢复并批准；右栏 summary 去掉 .md 与目录路径的精确格式；
   全文 text 给齐相对路径、各缺项补法与具体哪项没填完；CLI 入口返回码 2。
4. 原有行为守卫：项目说明文件不存在且施工单缺节时照旧由 AI 写方案；
   同时提到不存在的施工单与存在但缺节的施工单时仍拦截后者；失败不改坏需求卡原文。
"""
import json
import os
import unittest
from pathlib import Path

from acceptance_helpers import GOOD_PLAN, ProjectCase

ORDER_REL = "docs/改右栏/施工单-改右栏.md"

SUMMARY = "## 一、要做什么（一句话）\n\n右栏加一行字。\n\n"
ALLOWED = "## 二、允许改哪些文件\n\n- `src/app.py`\n\n**不许改**：上面没列的任何文件。\n\n"
DOING = "## 三、具体做法\n\n### P1 加一行\n\n照现有写法加。{extra}\n\n"
STATUS_TABLE = "## 四、状态表\n\n不涉及\n\n"
NOT_DO = "## 六、不做什么\n\n不改别的。\n\n"
CHECK_CMD = "- 验收命令：`python3 -m unittest tests.test_app`\n"


def make_order(
    summary=SUMMARY,
    allowed=ALLOWED,
    doing=None,
    status_table=STATUS_TABLE,
    not_do=NOT_DO,
    check_section=None,
):
    body_doing = DOING.format(extra="") if doing is None else doing
    body_check = ("## 八、验收\n\n" + CHECK_CMD + "- 走查：不涉及\n") if check_section is None else check_section
    return "# 施工单 改右栏：右栏加一行\n\n> 依据 方案。\n\n" + summary + allowed + body_doing + status_table + not_do + body_check


class CardGapAExtraBase(ProjectCase):
    def setUp(self):
        super().setUp()
        os.environ["FAKE_PLAN"] = json.dumps(GOOD_PLAN, ensure_ascii=False)
        docs = Path(self.project) / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "项目说明.md").write_text("测试项目\n", encoding="utf-8")
        from collab.workspace import init_collab

        init_collab(self.project)
        cfg = self.collab / "配置.json"
        data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {}
        data["project_profile_confirmed"] = True
        cfg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def write_order(self, content, rel=ORDER_REL):
        order = Path(self.project) / rel
        order.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            order.write_bytes(content)
        else:
            order.write_text(content, encoding="utf-8")
        return order

    def card_by_order(self, rel=ORDER_REL, extra=""):
        cdir = self.make_card()
        (cdir / "需求卡.md").write_text(
            f"# 按施工单做\n\n## 要做什么\n按施工单做：{rel}\n{extra}",
            encoding="utf-8",
        )
        return cdir

    def plan(self, card_id="CARD-001"):
        from collab.planner import plan_card

        return plan_card(self.project, card_id)

    def gaps(self, rel=ORDER_REL):
        from collab.planner import order_gaps

        return order_gaps(self.project, rel)

    def ai_calls(self):
        if not self.codex_log.exists():
            return 0
        return sum("--output-schema" in line for line in self.codex_log.read_text(encoding="utf-8").splitlines())

    def flow(self, cdir):
        from collab.flow import read_flow

        return read_flow(cdir)

    def last_fail(self, cdir):
        fails = [e for e in self.flow(cdir) if e.get("kind") == "方案失败"]
        self.assertTrue(fails, "应记一条“方案失败”")
        return fails[-1]

    def full_text(self, cdir, event):
        from collab.flow import text_of

        return text_of(cdir, event) or ""

    def assert_blocked_and_invalidated(self, cdir, expected_gaps, rel=ORDER_REL):
        """验证拦截返回 2、不叫 AI、作废旧方案和开工资料、无法批准，且摘要和全文符合格式。"""
        from collab.workspace import PLAN_TEMPLATE, has_blanks

        self.assertEqual(self.plan(), 2)
        self.assertEqual(self.ai_calls(), 0, "施工单缺项时绝不调用 AI")
        self.assertFalse((cdir / "开工.json").exists(), "失败后不能残留开工.json")
        plan_path = cdir / "方案.md"
        self.assertTrue(plan_path.exists(), "失败后应写回空白方案模板")
        plan_text = plan_path.read_text(encoding="utf-8")
        self.assertEqual(plan_text, PLAN_TEMPLATE, "失败后方案.md 应恢复为空白模板")
        self.assertTrue(has_blanks(plan_text))
        self.assertEqual(self.state(), "待对齐")

        fail = self.last_fail(cdir)
        self.assertEqual(fail.get("src"), "程序")
        self.assertEqual(fail.get("dst"), "你")

        stem = Path(rel).stem
        summary = fail.get("summary") or ""
        self.assertTrue(summary.startswith(f"{stem} 缺："), f"右栏摘要应以去掉 .md 的文件名开头：{summary}")
        self.assertTrue(summary.endswith("。补好后点“重写方案”。"), f"右栏摘要应以固定提示结尾：{summary}")
        self.assertNotIn(".md", summary, "右栏摘要不应带 .md 后缀")
        self.assertNotIn("/", summary, "右栏摘要不应带目录路径")
        middle = summary[len(f"{stem} 缺：") : -len("。补好后点“重写方案”。")]
        self.assertEqual(sorted(middle.split("、")), sorted(expected_gaps), f"摘要缺项应恰好匹配：{summary}")

        full = self.full_text(cdir, fail)
        self.assertIn(rel, full, "全文应包含施工单相对路径")

        code, out = self.cli("approve", self.project, "CARD-001")
        self.assertNotEqual(code, 0, f"方案失败后 approve 必须被拦：{out}")
        self.assertEqual(self.state(), "待对齐")
        return fail, full


class P1CommandExtractionTests(CardGapAExtraBase):
    """P1：验收命令只摘一次，检查和生成共用同一规则。"""

    def test_note_before_formal_line_in_check_section_uses_formal_command(self):
        # 同一“验收”节里，说明行提到旧命令在前，正式“- 验收命令：”在后：必须取正式行
        check = (
            "## 八、验收\n\n"
            "说明：之前用过的验收命令：`npm run old`\n"
            "- 验收命令：`python3 -m unittest tests.test_real`\n"
            "- 走查：不涉及\n"
        )
        self.write_order(make_order(check_section=check))
        self.assertEqual(self.gaps(), [])

        cdir = self.card_by_order()
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.ai_calls(), 0)
        start = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
        self.assertEqual(start["test_command"], "python3 -m unittest tests.test_real")
        plan_md = (cdir / "方案.md").read_text(encoding="utf-8")
        self.assertIn("python3 -m unittest tests.test_real", plan_md)
        self.assertNotIn("npm run old", plan_md)

    def test_formal_line_without_command_does_not_steal_walk_command_on_next_line(self):
        # “- 验收命令：”这一行本身没写反引号命令，下一行有走查命令：绝不能跨行把走查命令抓来当验收命令
        from collab.planner import _plan_from_order

        check = (
            "## 八、验收\n\n"
            "- 验收命令：见下方说明\n"
            "- 走查命令：`python3 tests/walk_demo.py`\n"
        )
        self.write_order(make_order(check_section=check))
        self.assertEqual(self.gaps(), ["验收命令"])
        self.assertIsNone(_plan_from_order(self.project, ORDER_REL))

        cdir = self.card_by_order()
        self.assert_blocked_and_invalidated(cdir, ["验收命令"])

    def test_empty_or_whitespace_backticks_in_check_command_counts_as_missing(self):
        for bad_line in ("- 验收命令：``\n", "- 验收命令：`   `\n", "- 验收命令：\n"):
            with self.subTest(bad_line=bad_line):
                check = "## 八、验收\n\n" + bad_line + "- 走查：不涉及\n"
                self.write_order(make_order(check_section=check))
                self.assertEqual(self.gaps(), ["验收命令"])

    def test_bullet_not_starting_with_formal_prefix_does_not_count(self):
        # 虽然是列表项、也含“验收命令：”和反引号，但不是以“- 验收命令：”开头：不算正式验收命令
        for bad_line in (
            "- 旧的验收命令：`npm run old`\n",
            "- 说明：验收命令：`npm run old`\n",
        ):
            with self.subTest(bad_line=bad_line):
                check = "## 八、验收\n\n" + bad_line + "- 走查：不涉及\n"
                self.write_order(make_order(check_section=check))
                self.assertEqual(self.gaps(), ["验收命令"])

    def test_plan_from_order_and_gaps_agree_when_only_doing_mentions_command(self):
        # 做法节有旧验收命令、验收节没有正式行：检查报缺、_plan_from_order 也必须返回 None
        from collab.planner import _plan_from_order

        self.write_order(
            make_order(
                doing=DOING.format(extra="\n\n旧验收命令：`npm run old`"),
                check_section="## 八、验收\n\n- 走查：不涉及\n",
            )
        )
        self.assertEqual(self.gaps(), ["验收命令"])
        self.assertIsNone(_plan_from_order(self.project, ORDER_REL))

    def test_old_format_without_check_section_gaps_and_missing(self):
        # 老格式（没有“验收”一节）：有全文验收命令时无缺项；没有时也报缺“验收命令”并拦住
        old_ok = make_order(check_section="## 收尾\n\n验收命令：`python3 -m unittest tests.test_old`\n")
        self.write_order(old_ok)
        self.assertEqual(self.gaps(), [])

        old_missing = make_order(check_section="## 收尾\n\n跑一遍测试。\n")
        self.write_order(old_missing)
        self.assertEqual(self.gaps(), ["验收命令"])
        cdir = self.card_by_order()
        self.assert_blocked_and_invalidated(cdir, ["验收命令"])


class P2OrderGapsEdgeTests(CardGapAExtraBase):
    """P2：order_gaps 各类缺项与“没填完”判定边界。"""

    def test_invalid_utf8_file_reports_only_unreadable_and_blocks_with_reason(self):
        # 非 UTF-8 坏字节文件：读不了时只返回 ["读不了"]，全文写明“读不了”和实际读取错误原文
        order = self.write_order(b"\xff\xfe\x80\x81invalid-utf8")
        with self.assertRaises(UnicodeDecodeError) as cm:
            order.read_text(encoding="utf-8")
        expected_reason = str(cm.exception)

        self.assertEqual(self.gaps(), ["读不了"])

        cdir = self.card_by_order()
        _, full = self.assert_blocked_and_invalidated(cdir, ["读不了"])
        self.assertIn("读不了", full)
        self.assertIn(
            expected_reason,
            full,
            f"全文应包含读取失败的具体错误原文（不能只靠固定提示里的字眼通过）：{full}",
        )

    def test_all_three_core_items_missing_reports_all_three(self):
        self.write_order("# 施工单 改右栏\n\n## 三、具体做法\n\n### P1 做点什么\n\n## 八、验收\n\n- 走查：不涉及\n")
        self.assertEqual(sorted(self.gaps()), sorted(["要做什么", "允许改哪些文件", "验收命令"]))

        cdir = self.card_by_order()
        _, full = self.assert_blocked_and_invalidated(cdir, ["要做什么", "允许改哪些文件", "验收命令"])
        self.assertIn("## 一、要做什么（一句话）", full)
        self.assertIn("## 二、允许改哪些文件", full)
        self.assertRegex(full, r"- 验收命令：`[^`\s][^`]*`")

    def test_empty_summary_section_or_only_subheading_counts_as_missing(self):
        for empty_summary in (
            "## 一、要做什么（一句话）\n\n",
            "## 一、要做什么（一句话）\n\n### 子标题不算正文\n\n",
        ):
            with self.subTest(empty_summary=empty_summary):
                self.write_order(make_order(summary=empty_summary))
                self.assertEqual(self.gaps(), ["要做什么"])

    def test_allowed_section_with_paths_only_after_not_allowed_marker_counts_as_missing(self):
        # “允许改”一节遇到 **不许改** 停止：后面的反引号路径不能当成允许改的文件
        bad_allowed = (
            "## 二、允许改哪些文件\n\n"
            "**不许改**：\n"
            "- `src/app.py`\n\n"
        )
        self.write_order(make_order(allowed=bad_allowed))
        self.assertEqual(self.gaps(), ["允许改哪些文件"])

    def test_placeholder_in_check_command_reports_unfilled_and_names_field(self):
        from collab.workspace import PLACEHOLDER

        check = f"## 八、验收\n\n- 验收命令：`python3 -m unittest {PLACEHOLDER}`\n- 走查：不涉及\n"
        self.write_order(make_order(check_section=check))
        self.assertEqual(self.gaps(), ["没填完"])

        cdir = self.card_by_order()
        _, full = self.assert_blocked_and_invalidated(cdir, ["没填完"])
        self.assertIn(PLACEHOLDER, full)
        self.assertIn("验收命令", full, f"全文应写明是验收命令里还有（待填）：{full}")

    def test_placeholder_in_walk_line_names_walk_in_full_text(self):
        from collab.workspace import PLACEHOLDER

        check = f"## 八、验收\n\n{CHECK_CMD}- 走查：放进项目前要看页面：{PLACEHOLDER}\n"
        self.write_order(make_order(check_section=check))
        self.assertEqual(self.gaps(), ["没填完"])

        cdir = self.card_by_order()
        _, full = self.assert_blocked_and_invalidated(cdir, ["没填完"])
        self.assertIn(PLACEHOLDER, full)
        self.assertIn("走查", full, f"全文应写明是走查那一行里还有（待填）：{full}")

    def test_placeholder_in_steps_or_not_do_counts_as_unfilled(self):
        # 具体做法步骤和“不做什么”会进入 _render_plan_md 生成的方案全文，留有（待填）也算没填完
        from collab.workspace import PLACEHOLDER

        cases = [
            ("steps", make_order(doing=f"## 三、具体做法\n\n### P1 {PLACEHOLDER}\n\n照着做。\n\n")),
            ("not_do", make_order(not_do=f"## 六、不做什么\n\n{PLACEHOLDER}\n\n")),
        ]
        for label, content in cases:
            with self.subTest(label=label):
                self.write_order(content)
                self.assertEqual(self.gaps(), ["没填完"])
                cdir = self.card_by_order()
                _, full = self.assert_blocked_and_invalidated(cdir, ["没填完"])
                self.assertIn(PLACEHOLDER, full)

    def test_multiple_placeholders_report_unfilled_once_and_name_each_field(self):
        from collab.workspace import PLACEHOLDER

        order = make_order(
            summary=f"## 一、要做什么（一句话）\n\n右栏加一行。{PLACEHOLDER}\n\n",
            check_section=f"## 八、验收\n\n- 验收命令：`python3 -m unittest {PLACEHOLDER}`\n- 走查：不涉及\n",
        )
        self.write_order(order)
        self.assertEqual(self.gaps(), ["没填完"], "多处含（待填）时缺项列表里“没填完”只出现一次")

        cdir = self.card_by_order()
        _, full = self.assert_blocked_and_invalidated(cdir, ["没填完"])
        self.assertIn("要做什么", full)
        self.assertIn("验收命令", full)

    def test_placeholder_in_non_plan_section_does_not_false_positive(self):
        # “状态表”不会进方案全文、允许文件、验收命令或走查命令：里面的空白标记不应误报成“没填完”
        from collab.workspace import PLACEHOLDER

        table_with_blank = (
            "## 四、状态表\n\n"
            "| 什么时候 | 谁的东西 | 存什么 | 恢复成什么 |\n"
            "|---|---|---|---|\n"
            f"| 正常情况 | {PLACEHOLDER} | 备注 | 备注 |\n\n"
        )
        self.write_order(make_order(status_table=table_with_blank))
        self.assertEqual(self.gaps(), [])

        cdir = self.card_by_order()
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.ai_calls(), 0)


class P3BlockInvalidateAndRecoveryTests(CardGapAExtraBase):
    """P3 与状态表：旧方案/开工资料作废、摘要路径清理、补好后恢复、CLI 入口与原有行为守卫。"""

    def test_existing_plan_invalidated_completely_then_recovers_and_approves(self):
        # 已有成功方案 + 开工.json → 施工单改坏再重写 → 删开工.json、方案恢复空白模板、不能批准
        # → 补好施工单再重写 → 重新生成方案与开工.json，可以批准进入待派发
        self.write_order(make_order())
        cdir = self.card_by_order()
        self.assertEqual(self.plan(), 0)
        self.assertTrue((cdir / "开工.json").exists())
        self.assertIn("右栏加一行字。", (cdir / "方案.md").read_text(encoding="utf-8"))

        self.write_order(make_order(check_section="## 八、验收\n\n- 走查：不涉及\n"))
        self.assert_blocked_and_invalidated(cdir, ["验收命令"])

        self.write_order(make_order())
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.ai_calls(), 0)
        self.assertTrue((cdir / "开工.json").exists())
        self.assertIn("右栏加一行字。", (cdir / "方案.md").read_text(encoding="utf-8"))
        code, out = self.cli("approve", self.project, "CARD-001")
        self.assertEqual(code, 0, f"补好施工单并重写方案后应能批准：{out}")
        self.assertEqual(self.state(), "待派发")

    def test_nested_order_path_omitted_from_summary_but_kept_in_full_text(self):
        nested_rel = "docs/跑卡卡点/子目录/施工单-多层路径.md"
        self.write_order(make_order(allowed=""), rel=nested_rel)
        cdir = self.card_by_order(rel=nested_rel)
        fail, full = self.assert_blocked_and_invalidated(cdir, ["允许改哪些文件"], rel=nested_rel)
        summary = fail.get("summary") or ""
        self.assertEqual(summary, "施工单-多层路径 缺：允许改哪些文件。补好后点“重写方案”。")
        self.assertNotIn("跑卡卡点", summary)
        self.assertNotIn("子目录", summary)
        self.assertIn(nested_rel, full)

    def test_cli_plan_returns_2_and_invalidates_when_order_has_gaps(self):
        from collab.workspace import PLAN_TEMPLATE

        self.write_order(make_order())
        cdir = self.card_by_order()
        self.assertEqual(self.plan(), 0)
        self.assertTrue((cdir / "开工.json").exists())

        self.write_order(make_order(summary=""))
        code, out = self.cli("plan", self.project, "CARD-001")
        self.assertEqual(code, 2, f"CLI plan 遇缺项施工单应返回 2：{out}")
        self.assertEqual(self.ai_calls(), 0)
        self.assertFalse((cdir / "开工.json").exists())
        self.assertEqual((cdir / "方案.md").read_text(encoding="utf-8"), PLAN_TEMPLATE)

    def test_missing_profile_file_with_broken_order_still_uses_ai(self):
        # 项目说明文件不存在（非模板）且施工单缺节：照旧由 AI 写方案并起草项目说明，不拦
        profile = Path(self.project) / "docs" / "项目说明.md"
        profile.unlink()
        plan = dict(GOOD_PLAN, profile="# 项目说明\n\n这是一个新项目，测试命令是 true。\n")
        os.environ["FAKE_PLAN"] = json.dumps(plan, ensure_ascii=False)

        self.write_order(make_order(check_section="## 八、验收\n\n- 走查：不涉及\n"))
        self.card_by_order()
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.ai_calls(), 1, "项目说明文件不存在时照旧由 AI 写方案")
        self.assertIn("这是一个新项目", profile.read_text(encoding="utf-8"))

    def test_missing_order_and_existing_broken_order_blocks_existing_one(self):
        # 需求卡先提到一份不存在的施工单，又提到一份存在但缺节的施工单：应拦住存在的那份，不叫 AI
        self.write_order(make_order(check_section="## 八、验收\n\n- 走查：不涉及\n"))
        cdir = self.make_card()
        (cdir / "需求卡.md").write_text(
            f"# 按施工单做\n\n## 要做什么\n参考 docs/旧/施工单-不存在.md，实际按 {ORDER_REL} 做。\n",
            encoding="utf-8",
        )
        self.assert_blocked_and_invalidated(cdir, ["验收命令"])

    def test_failed_order_does_not_corrupt_requirement_card(self):
        # 施工单缺项失败时，不能改坏需求卡原有正文和范围
        self.write_order(make_order(check_section="## 八、验收\n\n- 走查：不涉及\n"))
        cdir = self.card_by_order()
        before = (cdir / "需求卡.md").read_text(encoding="utf-8")
        self.assert_blocked_and_invalidated(cdir, ["验收命令"])
        after = (cdir / "需求卡.md").read_text(encoding="utf-8")
        self.assertEqual(after, before, "施工单缺项失败时不应改写需求卡.md")


if __name__ == "__main__":
    unittest.main()
