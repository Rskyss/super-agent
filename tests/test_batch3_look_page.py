"""第三批二（③）：施工单要求“看页面 / 对照原型图”的卡，复查通过后停在“等看页面”，点“已看过”才能放进项目。

依据 docs/外部项目试跑修复/第三批/方案.md 第二节。"""
import json
import tempfile
import unittest
from pathlib import Path

from acceptance_helpers import ServerCase
from collab.flow import read_flow, record
from collab.phase import card_phase
from collab.state import Card

ORDER = """# 施工单

## 一、要做什么（一句话）

导师驾驶舱加任务标签。

## 二、允许改哪些文件

- `lib/a.ts`

## 三、具体做法

### 1. 加标签

## 八、验收

- 验收命令：`npm test`
- 走查：不涉及（放进主项目前必须停下，打开本地页面对照 `导师端原型对照/04-驾驶舱改后.png` 与 `docs/图/05-终局.png` 看一遍确认再合并）
"""


class PlannerReadsLook(unittest.TestCase):
    def test_look_required_and_images(self):
        from collab.planner import _plan_from_order
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "docs" / "单").mkdir(parents=True)
            (Path(tmp) / "docs" / "单" / "施工单.md").write_text(ORDER, encoding="utf-8")
            (Path(tmp) / "docs" / "单" / "导师端原型对照").mkdir()
            (Path(tmp) / "docs" / "单" / "导师端原型对照" / "04-驾驶舱改后.png").write_bytes(b"png")
            data = _plan_from_order(tmp, "docs/单/施工单.md")
        self.assertTrue(data["look_required"])
        self.assertEqual(data["look_images"][0], "docs/单/导师端原型对照/04-驾驶舱改后.png",
                         "相对施工单的图要换成项目里的路径")
        self.assertIn("docs/图/05-终局.png", data["look_images"])
        self.assertIn("对照", data["look_line"])

    def test_not_needed(self):
        from collab.planner import _plan_from_order
        order = ORDER.replace(ORDER.split("- 走查：")[1], "不涉及\n")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "施工单.md").write_text(order, encoding="utf-8")
            data = _plan_from_order(tmp, "施工单.md")
        self.assertFalse(data.get("look_required"))


class LookGate(ServerCase):
    def setUp(self):
        super().setUp()
        self.cdir = self.make_card()
        card = Card.load(self.cdir / "状态.json")
        card.state = "待验收"
        card.save(self.cdir / "状态.json")
        (self.cdir / "开工.json").write_text(json.dumps({
            "test_command": "true", "look_required": True,
            "look_images": ["docs/图/04.png"], "look_line": "对照原型图 04 看一遍",
        }, ensure_ascii=False), encoding="utf-8")
        record(self.cdir, "结束", src="程序", dst="你", state="待验收", summary="做完了，等你确认")

    def act(self, action, text=""):
        return self.post("/api/clerk/card", {"project": self.project, "card": "CARD-001", "action": action,
                                             "text": text}, token="口令")

    def test_waits_for_look(self):
        phase = card_phase(self.project, "CARD-001")
        self.assertEqual(phase["code"], "look_pending")
        self.assertEqual(phase["label"], "等看页面")
        self.assertNotIn("accept", phase["actions"], "没看页面不能放进项目")
        self.assertIn("look_ok", phase["actions"])
        self.assertIn("look_diff", phase["actions"])
        self.assertIn("revise", phase["actions"])

    def test_accept_refused_before_look(self):
        status, body = self.act("accept")
        self.assertNotEqual(status, 200)

    def test_merge_refused_before_look(self):
        from collab.merge import merge_card
        got = merge_card(Path(self.project), "CARD-001")
        self.assertFalse(got["ok"])
        self.assertIn("先看页面", got["reason"])

    def test_look_ok_then_normal_review(self):
        status, body = self.act("look_ok")
        self.assertEqual(status, 200, body)
        ev = [e for e in read_flow(self.cdir) if e.get("kind") == "看页面"][-1]
        self.assertEqual(ev["choice"], "一致")
        self.assertEqual(ev["src"], "你")
        phase = card_phase(self.project, "CARD-001")
        self.assertEqual(phase["code"], "review")
        self.assertIn("accept", phase["actions"])

    def test_look_diff_needs_words_and_records(self):
        status, _ = self.act("look_diff", "")
        self.assertNotEqual(status, 200, "有出入要写一句哪里不一样")
        status, body = self.act("look_diff", "任务标签在右边，图上在左边")
        self.assertEqual(status, 200, body)
        ev = [e for e in read_flow(self.cdir) if e.get("kind") == "看页面"][-1]
        self.assertEqual(ev["choice"], "有出入")
        self.assertIn("右边", ev["note"])
        phase = card_phase(self.project, "CARD-001")
        self.assertEqual(phase["code"], "review", "写了有出入可以照样放进项目")
        self.assertIn("右边", phase.get("look_note", ""))

    def test_new_round_needs_look_again(self):
        self.act("look_ok")
        record(self.cdir, "结束", src="程序", dst="你", state="待验收", summary="又做完一轮")
        self.assertEqual(card_phase(self.project, "CARD-001")["code"], "look_pending", "改过之后要重新看")

    def test_view_lists_images(self):
        from desktop.view import card_view
        v = card_view(self.project, "CARD-001")
        look = v.get("look") or {}
        self.assertEqual(look.get("line"), "对照原型图 04 看一遍")
        self.assertEqual([i["path"] for i in look.get("images", [])], ["docs/图/04.png"])

    def test_card_without_look_unchanged(self):
        (self.cdir / "开工.json").write_text(json.dumps({"test_command": "true"}), encoding="utf-8")
        self.assertEqual(card_phase(self.project, "CARD-001")["code"], "review")


if __name__ == "__main__":
    unittest.main()


from acceptance_helpers import STATIC, needs_node, run_node  # noqa: E402


@needs_node()
class PanelButtons(unittest.TestCase):
    def test_look_buttons_have_labels(self):
        script = ("const assert = require('assert');"
                  f"const P = require({json.dumps(str(STATIC / 'panel.js'))});"
                  "const b = P.actionButtons({ actions: ['look_ok', 'look_diff', 'revise'] }, true);"
                  "assert.deepStrictEqual(b.map(x => x.label), ['已看过，和图一致', '已看过，有出入', '不满意，继续改']);")
        done = run_node(script)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_page_has_look_parts(self):
        html = (STATIC / "panel.html").read_text(encoding="utf-8")
        for part in ('id="lookRow"', 'id="lookImages"', 'id="lookBox"', 'id="lookRedo"', 'id="lookKeep"'):
            self.assertIn(part, html)


class ImageShorthand(unittest.TestCase):
    def test_glob_and_bare_name(self):
        from collab.look import look_images
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "docs" / "单"
            (d / "导师端").mkdir(parents=True)
            (d / "市场组").mkdir()
            (d / "导师端" / "02-改后.png").write_bytes(b"x")
            (d / "导师端" / "02-改前.png").write_bytes(b"x")
            (d / "市场组" / "05-复盘.png").write_bytes(b"x")
            got = look_images("对照 `导师端/02*.png` 与 `05-复盘.png`", tmp, "docs/单/施工单.md")
        self.assertEqual(got, ["docs/单/导师端/02-改前.png", "docs/单/导师端/02-改后.png", "docs/单/市场组/05-复盘.png"])


class Round1LookFixes(ServerCase):
    """第三批复查第 1 轮 1、7、6。"""

    def test_walk_command_line_before_walk_line(self):
        from collab.planner import _plan_from_order
        order = ORDER.replace("- 验收命令：`npm test`\n", "- 验收命令：`npm test`\n- 走查命令：`node walk.js`\n")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "施工单.md").write_text(order, encoding="utf-8")
            data = _plan_from_order(tmp, "施工单.md")
        self.assertTrue(data["look_required"], "走查命令写在前面也要认出“要看页面”")

    def test_ai_plan_branch_keeps_look(self):
        from collab.planner import _order_look
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "施工单.md").write_text(ORDER, encoding="utf-8")
            self.assertTrue(_order_look(tmp, "施工单.md").get("look_required"))
        src = (Path(__file__).resolve().parent.parent / "collab" / "planner.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("start.update(_order_look(project, rel))"), 1, "AI 写方案那条路也存看页面要求")

    def test_merge_refusal_keeps_look_buttons(self):
        cdir = self.make_card()
        card = Card.load(cdir / "状态.json")
        card.state = "待验收"
        card.save(cdir / "状态.json")
        (cdir / "开工.json").write_text(json.dumps({"test_command": "true", "look_required": True}), encoding="utf-8")
        record(cdir, "结束", src="程序", dst="你", state="待验收", summary="做完了")
        from collab.merge import merge_card
        self.assertFalse(merge_card(Path(self.project), "CARD-001")["ok"])
        self.assertFalse([e for e in read_flow(cdir) if e.get("kind") == "合并失败"])
        self.assertEqual(card_phase(self.project, "CARD-001")["code"], "look_pending")

    def test_redo_does_not_post_look_diff(self):
        js = (STATIC / "panel.js").read_text(encoding="utf-8")
        redo = js.split('$("lookRedo").addEventListener', 1)[1].split("});", 1)[0]
        self.assertNotIn("look_diff", redo, "退回去改不能先放行")
        self.assertNotIn("lookDiff(", redo)


class Round2LookFixes(ServerCase):
    def setUp(self):
        super().setUp()
        self.cdir = self.make_card()
        card = Card.load(self.cdir / "状态.json")
        card.state = "待验收"
        card.save(self.cdir / "状态.json")
        record(self.cdir, "结束", src="程序", dst="你", state="待验收", summary="做完了")

    def test_old_card_reads_work_order(self):
        docs = Path(self.project) / "docs"
        docs.mkdir(exist_ok=True)
        (docs / "施工单-01.md").write_text(ORDER, encoding="utf-8")
        req = self.cdir / "需求卡.md"
        req.write_text(req.read_text(encoding="utf-8") + "\n按施工单做：docs/施工单-01.md\n", encoding="utf-8")
        (self.cdir / "开工.json").write_text(json.dumps({"test_command": "npm test"}), encoding="utf-8")
        self.assertEqual(card_phase(self.project, "CARD-001")["code"], "look_pending", "升级前开工的卡也要先看页面")

    def test_preview_allowed_while_looking(self):
        from desktop.preview import _OPEN_STAGES
        self.assertIn("look_pending", _OPEN_STAGES)

    def test_jpg_design_image_opens(self):
        from desktop.view import resolve_walk_image
        img = Path(self.project) / "docs" / "图.jpg"
        img.parent.mkdir(exist_ok=True)
        img.write_bytes(b"jpg")
        self.assertIsNotNone(resolve_walk_image(self.project, "CARD-001", "design", "docs/图.jpg"))
        self.assertIsNone(resolve_walk_image(self.project, "CARD-001", "shot", "docs/图.jpg"), "截图仍只认 png")

    def test_archive_records_look(self):
        (self.cdir / "开工.json").write_text(json.dumps({"test_command": "true", "look_required": True}),
                                            encoding="utf-8")
        record(self.cdir, "看页面", src="你", dst="程序", choice="有出入", note="标签位置和图不一样", summary="看过")
        from collab.archive import _acceptance_text
        text = _acceptance_text("功能", Card.load(self.cdir / "状态.json"), self.cdir, "已合并")
        self.assertIn("标签位置和图不一样", text)


class Round3LookFixes(Round2LookFixes):
    def test_bold_label_keeps_glob_in_path(self):
        from collab.planner import _order_look
        order = ORDER.replace("- 走查：不涉及（", "- **走查**：不涉及（`原型/**/登录.png` ")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "原型" / "子").mkdir(parents=True)
            (Path(tmp) / "原型" / "子" / "登录.png").write_bytes(b"png")
            (Path(tmp) / "施工单.md").write_text(order, encoding="utf-8")
            got = _order_look(tmp, "施工单.md")
        self.assertIn("原型/子/登录.png", got["look_images"], "路径里的 ** 不能被当加粗删掉")

    def test_bold_walk_line(self):
        from collab.planner import _order_look
        order = ORDER.replace("- 走查：", "- **走查**：")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "施工单.md").write_text(order, encoding="utf-8")
            self.assertTrue(_order_look(tmp, "施工单.md").get("look_required"))

    def test_old_merge_failure_does_not_hide_look(self):
        (self.cdir / "开工.json").write_text(json.dumps({"test_command": "true", "look_required": True}),
                                            encoding="utf-8")
        record(self.cdir, "合并失败", src="程序", dst="你", summary="老版本留下的失败")
        phase = card_phase(self.project, "CARD-001")
        self.assertEqual(phase["code"], "look_pending")
        self.assertIn("look_ok", phase["actions"])


class SecondConfirmLook(unittest.TestCase):
    def test_bold_with_colon_inside(self):
        from collab.planner import _order_look
        order = ORDER.replace("- 走查：", "- **走查：**")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "施工单.md").write_text(order, encoding="utf-8")
            self.assertTrue(_order_look(tmp, "施工单.md").get("look_required"))


class FinalConfirmViewHow(unittest.TestCase):
    def test_bold_walk_text_goes_to_view_how(self):
        from collab.planner import _plan_from_order
        order = ORDER.replace("- 走查：", "- **走查：**")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "施工单.md").write_text(order, encoding="utf-8")
            data = _plan_from_order(tmp, "施工单.md")
        self.assertIn("对照", data["view_how"], "右栏“在哪看”要显示走查正文")
