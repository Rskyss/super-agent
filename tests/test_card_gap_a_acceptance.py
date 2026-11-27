"""跑卡卡点修理 卡 A（第 ④ 类）验收：任务卡点名了施工单、程序却摘不出关键内容时，记“方案失败”并写清缺哪几项，
不再悄悄换成 AI 写方案。没点名施工单（或点名的文件不存在）、批准前点过“继续改”、项目说明还没确认的卡，照旧由 AI 写方案。

缺项用 collab.planner.order_gaps(project, rel) 列出，固定说法：
  “要做什么”“允许改哪些文件”“验收命令”“没填完”“读不了”。
（“施工单写了要看页面、格式没认出”不在本卡：用户 11-26 定去掉，记已知问题，靠施工单模板的固定写法兜着。）
右栏那句（方案失败的 summary）固定写成：“<施工单文件名> 缺：<缺项用顿号连>。补好后点“重写方案”。”
点开看的全文（text）里有施工单相对路径，并给每个缺项一行补法例子（见 FIX_HINT）。

来历：11-26 train 卡 4b 的施工单少了“验收命令：”一行，程序摘不出就悄悄让 AI 写方案，AI 只挑了一条测试命令、
丢了“放进项目前要看页面”。见 docs/跑卡卡点修理/需求共识.md 口径 8；施工单第 1 轮复查 8 条意见。
"""
import json
import os
import unittest
from pathlib import Path

from acceptance_helpers import GOOD_PLAN, ProjectCase

ORDER_REL = "docs/改右栏/施工单-改右栏.md"

SUMMARY = "## 一、要做什么（一句话）\n\n右栏加一行字。\n\n"
ALLOWED = "## 二、允许改哪些文件\n\n- `src/app.py`\n\n**不许改**：上面没列的任何文件。\n\n"
DOING = "## 三、具体做法\n\n### P1 加一行\n\n照现有写法加。{extra}\n\n## 六、不做什么\n\n不改别的。\n\n"
CHECK_CMD = "- 验收命令：`python3 -m unittest tests.test_app`\n"


FIX_HINT = {   # 全文里每个缺项必须出现的补法例子（照规则包《施工单模板》的写法）
    "要做什么": "## 一、要做什么（一句话）",
    "允许改哪些文件": "## 二、允许改哪些文件",
    "验收命令": "- 验收命令：`",
    "没填完": "（待填）",
    "读不了": "读不了",
}


def order_text(summary=True, allowed=True, check=True, look="- 走查：不涉及\n", doing_extra="", allowed_text=None):
    return ("# 施工单 改右栏：右栏加一行\n\n> 依据 方案。\n\n"
            + (SUMMARY if summary else "")
            + ((allowed_text or ALLOWED) if allowed else "")
            + DOING.format(extra=doing_extra)
            + "## 八、验收\n\n" + (CHECK_CMD if check else "") + look)


class OrderGapBase(ProjectCase):
    def setUp(self):
        super().setUp()
        # 设好假 AI 方案：程序要是还悄悄掉进 AI 写方案，这张卡会“成功”，调用记录里也会留下痕迹
        os.environ["FAKE_PLAN"] = json.dumps(GOOD_PLAN, ensure_ascii=False)
        docs = Path(self.project) / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "项目说明.md").write_text("测试项目", encoding="utf-8")
        from collab.workspace import init_collab
        init_collab(self.project)
        cfg = self.collab / "配置.json"
        data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {}
        data["project_profile_confirmed"] = True
        cfg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def write_order(self, text, rel=ORDER_REL):
        order = Path(self.project) / rel
        order.parent.mkdir(parents=True, exist_ok=True)
        order.write_text(text, encoding="utf-8")
        return order

    def card_by_order(self, extra="", rel=ORDER_REL):
        cdir = self.make_card()
        (cdir / "需求卡.md").write_text(f"# 按施工单做\n\n## 要做什么\n按施工单做：{rel}\n{extra}", encoding="utf-8")
        return cdir

    def plan(self):
        from collab.planner import plan_card
        return plan_card(self.project, "CARD-001")

    def ai_calls(self):
        if not self.codex_log.exists():
            return 0
        return sum("--output-schema" in line for line in self.codex_log.read_text(encoding="utf-8").splitlines())

    def flow(self, cdir):
        from collab.flow import read_flow
        return read_flow(cdir)

    def last_fail(self, cdir):
        fails = [e for e in self.flow(cdir) if e.get("kind") == "方案失败"]
        self.assertTrue(fails, "摘不出时应记一条“方案失败”")
        return fails[-1]

    def full_text(self, cdir, event):
        from collab.flow import text_of
        return text_of(cdir, event) or ""

    def gaps(self, rel=ORDER_REL):
        from collab.planner import order_gaps
        return order_gaps(self.project, rel)

    def assert_blocked(self, cdir, *missing):
        import re
        self.assertEqual(self.plan(), 2)
        self.assertEqual(self.ai_calls(), 0, "摘不出时一次都不能叫 AI")
        fail = self.last_fail(cdir)
        summary = fail.get("summary") or ""
        m = re.search(r"缺：(.+?)。", summary)
        self.assertTrue(m, "右栏那句照固定格式写“缺：……。”：" + summary)
        self.assertEqual(sorted(m.group(1).split("、")), sorted(missing), "实际缺什么报什么，已填的不误报")
        self.assertIn("重写方案", summary, "告诉用户补好后点“重写方案”")
        self.assertIn("施工单-改右栏", summary, "右栏那句写施工单名字")
        self.assertNotIn("docs/", summary, "右栏不显示文件路径（规则包：进度说明不显示路径）")
        full = self.full_text(cdir, fail)
        self.assertIn(ORDER_REL, full, "完整路径放在点开看的全文里")
        for item in missing:
            self.assertIn(FIX_HINT[item], full, f"全文给出“{item}”的补法例子")
        if "验收命令" in missing:
            self.assertRegex(full, r"- 验收命令：`[^`\s][^`]*`", "验收命令的补法要给一条写全的示例命令")
        return fail
        self.assertFalse((cdir / "开工.json").exists(), "失败后不能留着能生效的开工资料")
        from collab.workspace import has_blanks
        self.assertTrue(has_blanks((cdir / "方案.md").read_text(encoding="utf-8")), "失败后方案是空白模板，批准拦得住")
        self.assertEqual(self.state(), "待对齐")


class GapsListed(OrderGapBase):
    """order_gaps 实际缺什么就报什么，已填的不误报。"""

    def test_complete_order_has_no_gaps(self):
        self.write_order(order_text())
        self.assertEqual(self.gaps(), [])

    def test_each_missing_item_alone(self):
        cases = [
            (order_text(summary=False), ["要做什么"]),
            (order_text(allowed=False), ["允许改哪些文件"]),
            (order_text(check=False), ["验收命令"]),
        ]
        for text, want in cases:
            with self.subTest(want=want):
                self.write_order(text)
                self.assertEqual(sorted(self.gaps()), sorted(want))

    def test_two_missing_both_listed_nothing_else(self):
        self.write_order(order_text(allowed=False, check=False))
        self.assertEqual(sorted(self.gaps()), sorted(["允许改哪些文件", "验收命令"]))

    def test_placeholder_in_allowed_files_counts_as_unfilled(self):
        from collab.workspace import PLACEHOLDER
        self.write_order(order_text(allowed_text=f"## 二、允许改哪些文件\n\n- `{PLACEHOLDER}`\n\n"))
        self.assertIn("没填完", self.gaps())

    def test_placeholder_in_summary_counts_as_unfilled(self):
        from collab.workspace import PLACEHOLDER
        self.write_order(order_text().replace("右栏加一行字。", f"右栏加一行字。{PLACEHOLDER}"))
        self.assertEqual(self.gaps(), ["没填完"])

    def test_command_only_counts_inside_check_section(self):
        # 验收一节没有正式命令，只在做法里提到一句旧命令：不能拿它当验收命令
        self.write_order(order_text(check=False, doing_extra="\n\n旧验收命令：`npm run old`"))
        self.assertEqual(self.gaps(), ["验收命令"])

    def test_look_line_in_right_format_is_fine(self):
        self.write_order(order_text(look="- 走查：放进项目前要看页面：核对右栏文字。\n"))
        self.assertEqual(self.gaps(), [])

    def test_walk_not_needed_is_fine(self):
        self.write_order(order_text(look="- 走查：不涉及\n"))
        self.assertEqual(self.gaps(), [])

    def test_only_walk_command_is_fine(self):
        self.write_order(order_text(look="- 走查命令：`python3 tests/walk_改右栏.py`\n"))
        self.assertEqual(self.gaps(), [])

    def test_placeholder_in_walk_line_counts_as_unfilled(self):
        from collab.workspace import PLACEHOLDER
        self.write_order(order_text(look=f"- 走查：放进项目前要看页面：{PLACEHOLDER}\n"))
        self.assertEqual(self.gaps(), ["没填完"])

    def test_command_must_be_the_formal_line(self):
        # 验收一节里只有一句说明提到旧命令、没有正式的“- 验收命令：”行：不算有验收命令
        self.write_order(order_text(check=False, look="- 走查：不涉及\n说明：旧验收命令：`npm run old`\n"))
        self.assertEqual(self.gaps(), ["验收命令"])

    def test_placeholder_in_walk_command_counts_as_unfilled(self):
        from collab.workspace import PLACEHOLDER
        self.write_order(order_text(look=f"- 走查命令：`python3 {PLACEHOLDER}`\n"))
        self.assertEqual(self.gaps(), ["没填完"])

    def test_unreadable_order(self):
        order = self.write_order(order_text())
        order.chmod(0)
        self.addCleanup(order.chmod, 0o644)
        if os.access(order, os.R_OK):
            self.skipTest("以管理员身份跑，读权限拦不住")
        self.assertEqual(self.gaps(), ["读不了"])


class PlanBlocked(OrderGapBase):
    def test_missing_check_command_blocks(self):
        self.write_order(order_text(check=False))
        cdir = self.card_by_order()
        self.assert_blocked(cdir, "验收命令")

    def test_missing_allowed_files_blocks(self):
        self.write_order(order_text(allowed=False))
        cdir = self.card_by_order()
        self.assert_blocked(cdir, "允许改哪些文件")

    def test_missing_summary_blocks(self):
        self.write_order(order_text(summary=False))
        cdir = self.card_by_order()
        self.assert_blocked(cdir, "要做什么")

    def test_two_missing_both_in_summary(self):
        self.write_order(order_text(allowed=False, check=False))
        cdir = self.card_by_order()
        fail = self.assert_blocked(cdir, "允许改哪些文件", "验收命令")
        self.assertNotIn("要做什么", fail.get("summary") or "", "已填的不误报")

    def test_unfilled_blocks_with_reason(self):
        from collab.workspace import PLACEHOLDER
        self.write_order(order_text().replace("右栏加一行字。", f"右栏加一行字。{PLACEHOLDER}"))
        cdir = self.card_by_order()
        self.assert_blocked(cdir, "没填完")
        self.assertIn("要做什么", self.full_text(cdir, self.last_fail(cdir)), "写明是哪一项没填完")

    def test_unfilled_allowed_names_the_field(self):
        from collab.workspace import PLACEHOLDER
        self.write_order(order_text(allowed_text=f"## 二、允许改哪些文件\n\n- `{PLACEHOLDER}`\n\n"))
        cdir = self.card_by_order()
        self.assert_blocked(cdir, "没填完")
        self.assertIn("允许改哪些文件", self.full_text(cdir, self.last_fail(cdir)), "写明是哪一项没填完")

    def test_unreadable_blocks(self):
        order = self.write_order(order_text())
        cdir = self.card_by_order()
        order.chmod(0)
        self.addCleanup(order.chmod, 0o644)
        if os.access(order, os.R_OK):
            self.skipTest("以管理员身份跑，读权限拦不住")
        fail = self.assert_blocked(cdir, "读不了")
        full = self.full_text(cdir, fail)
        self.assertTrue("Permission denied" in full or "权限" in full, "全文写明读不了的原因（权限还是编码）：" + full)

    def test_walk_line_placeholder_blocks(self):
        from collab.workspace import PLACEHOLDER
        self.write_order(order_text(look=f"- 走查：放进项目前要看页面：{PLACEHOLDER}\n"))
        cdir = self.card_by_order()
        self.assert_blocked(cdir, "没填完")

    def test_command_only_in_a_note_blocks(self):
        self.write_order(order_text(check=False, look="- 走查：不涉及\n说明：旧验收命令：`npm run old`\n"))
        cdir = self.card_by_order()
        self.assert_blocked(cdir, "验收命令")

    def test_walk_command_placeholder_blocks(self):
        from collab.workspace import PLACEHOLDER
        self.write_order(order_text(look=f"- 走查命令：`python3 {PLACEHOLDER}`\n"))
        cdir = self.card_by_order()
        self.assert_blocked(cdir, "没填完")
        full = self.full_text(cdir, self.last_fail(cdir))
        self.assertIn("走查", full, "写明是走查那一行没填完")

    def test_formal_command_is_what_goes_into_start_sheet(self):
        # 验收一节有正式命令，前面做法里提到一句旧命令：开工资料里必须是正式命令，不叫 AI
        self.write_order(order_text(doing_extra="\n\n旧验收命令：`npm run old`"))
        cdir = self.card_by_order()
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.ai_calls(), 0)
        start = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
        self.assertEqual(start.get("test_command"), "python3 -m unittest tests.test_app")

    def test_old_format_without_check_section_still_plans(self):
        # 没有“验收”一节的老格式：照旧认全文第一处验收命令，照施工单生成，不拦、不叫 AI
        text = (SUMMARY + ALLOWED + DOING.format(extra="")
                + "## 四、验收\n\n验收命令：`python3 -m unittest tests.test_old`\n")
        text = text.replace("## 四、验收", "## 收尾").replace("## 收尾\n\n", "## 收尾\n\n")
        self.write_order("# 施工单 老格式\n\n" + text)
        cdir = self.card_by_order()
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.ai_calls(), 0)
        start = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
        self.assertEqual(start.get("test_command"), "python3 -m unittest tests.test_old")

    def test_real_new_card_then_fixed_then_plans_from_order(self):
        # 用真实建卡入口（会先放一份空白方案模板），缺验收命令 → 失败；补好后重写方案 → 照施工单生成
        from collab.tasks import new_card
        self.write_order(order_text(check=False))
        card_id = new_card(self.project, "改右栏", f"按施工单做：{ORDER_REL}", "不改别的", "验收全过")
        self.assertEqual(card_id, "CARD-001")
        cdir = self.collab / "cards" / card_id
        self.assert_blocked(cdir, "验收命令")
        self.write_order(order_text())
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.ai_calls(), 0)
        start = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
        self.assertEqual(start.get("test_command"), "python3 -m unittest tests.test_app")
        plans = [e for e in self.flow(cdir) if e.get("kind") == "方案"]
        self.assertEqual(plans[-1].get("src"), "程序", "补好后照施工单生成，不是 AI 写的")

    def test_existing_plan_then_order_broken_cannot_be_approved(self):
        # 先照施工单生成成功，再把施工单改坏、重写方案失败：旧方案和旧开工资料不能再被批准开工
        self.write_order(order_text())
        cdir = self.card_by_order()
        self.assertEqual(self.plan(), 0)
        self.assertTrue((cdir / "开工.json").exists())
        self.write_order(order_text(check=False))
        self.assert_blocked(cdir, "验收命令")
        code, out = self.cli("approve", self.project, "CARD-001")
        self.assertNotEqual(code, 0, "失败后不能批准开工：" + out)
        self.assertEqual(self.state(), "待对齐")


class StillAi(OrderGapBase):
    """这几种照旧由 AI 写方案。需要起草项目说明的（说明文件不存在或还是模板）也照旧，由
    tests/test_batch4_extra_round54.py::test_order_needing_project_profile_falls_back_to_ai 守着。"""

    def test_card_without_order(self):
        cdir = self.make_card()
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.ai_calls(), 1)
        start = json.loads((cdir / "开工.json").read_text(encoding="utf-8"))
        self.assertEqual(start.get("test_command"), GOOD_PLAN["test_command"])

    def test_named_order_file_missing(self):
        self.card_by_order(rel="docs/改右栏/施工单-不存在.md")
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.ai_calls(), 1, "点名的施工单不存在：照旧由 AI 写")

    def test_profile_needed_with_broken_order(self):
        # 项目说明还是模板（要先起草）且施工单缺命令：照旧由 AI 写方案并起草项目说明，不拦
        from collab.workspace import PROFILE_TEMPLATE, profile_path
        profile = profile_path(self.project)
        profile.write_text(PROFILE_TEMPLATE, encoding="utf-8")
        plan = dict(GOOD_PLAN, profile="# 项目说明\n\n做协作工具，测试命令是 true。\n")
        os.environ["FAKE_PLAN"] = json.dumps(plan, ensure_ascii=False)
        self.write_order(order_text(check=False))
        self.card_by_order()
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.ai_calls(), 1)
        self.assertIn("做协作工具", profile.read_text(encoding="utf-8"))

    def test_revised_card_with_broken_order(self):
        self.write_order(order_text(check=False))
        self.card_by_order(extra="\n## 追加要求\n右栏那行字改短一点\n")
        self.assertEqual(self.plan(), 0)
        self.assertEqual(self.ai_calls(), 1, "批准前点过“继续改”：照旧由 AI 重写，施工单缺节也不拦")


if __name__ == "__main__":
    unittest.main()
