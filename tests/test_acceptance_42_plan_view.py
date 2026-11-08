"""42 号单：方案写明做完后怎么看结果。写错了只影响预览，不能耽误施工。"""
import json

from acceptance_helpers import GOOD_PLAN, ProjectCase
from collab.planner import _plan_schema, _render_plan_md, _validate_answer, plan_card
from desktop.view import card_view


VIEW = {"view_how": "打开后点首页，按钮应该是蓝色", "view_command": "npm run dev", "view_url": "http://127.0.0.1:5174/"}


class Schema(ProjectCase):
    def test_asked_for(self):
        schema = _plan_schema()
        for key in VIEW:
            self.assertEqual(schema["properties"][key], {"type": "string"})
            self.assertIn(key, schema["required"])

    def test_old_answers_still_pass(self):
        self.assertEqual(_validate_answer(dict(GOOD_PLAN), False), (True, ""))

    def test_bad_view_fields_do_not_fail_the_plan(self):
        for bad in ({"view_command": "npm 'run"}, {"view_url": "https://example.com/"}, {"view_url": "file:///x"}):
            self.assertEqual(_validate_answer({**GOOD_PLAN, **VIEW, **bad}, False), (True, ""), bad)

    def test_user_can_read_it_when_approving(self):
        text = _render_plan_md(dict(GOOD_PLAN, **VIEW))
        self.assertIn("## 做完后怎么看结果", text)
        self.assertIn("打开后点首页", text)
        self.assertIn("npm run dev", text)


class Saved(ProjectCase):
    def plan(self, **extra):
        import os
        self.make_card()
        os.environ["FAKE_PLAN"] = json.dumps(dict(GOOD_PLAN, profile="# 项目说明\n小项目", **extra))
        self.assertEqual(plan_card(self.project, "CARD-001"), 0)
        return json.loads((self.collab / "cards" / "CARD-001" / "开工.json").read_text(encoding="utf-8"))

    def test_empty_fields_are_not_written(self):
        self.assertEqual(self.plan(), {"test_command": GOOD_PLAN["test_command"]})

    def test_kept_with_the_start_record(self):
        start = self.plan(**VIEW)
        self.assertEqual(start["test_command"], GOOD_PLAN["test_command"])
        for key, value in VIEW.items():
            self.assertEqual(start[key], value)

    def test_unsafe_command_or_address_is_dropped(self):
        start = self.plan(view_how="看看", view_command="npm run dev", view_url="https://example.com/")
        self.assertEqual((start["view_how"], start.get("view_command", ""), start.get("view_url", "")), ("看看", "", ""))

    def test_review_shows_what_to_check(self):
        self.plan(**VIEW)
        cdir = self.collab / "cards" / "CARD-001"
        (cdir / "状态.json").write_text(json.dumps({"card_id": "CARD-001", "state": "待验收", "rework": 0}), encoding="utf-8")
        info = card_view(self.project, "CARD-001")["accept"]
        self.assertEqual(info["result"], GOOD_PLAN["result"])
        self.assertEqual(info["how"], VIEW["view_how"])
        self.assertTrue(info["can_preview"])

    def test_other_stages_have_no_review_info(self):
        self.make_card(state="待对齐", plan=True)
        self.assertIsNone(card_view(self.project, "CARD-001").get("accept"))
