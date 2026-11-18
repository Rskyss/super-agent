"""去掉调度员 D6：右栏原来叫“调度”的那一站改叫“写方案”，固定显示 Codex；模型取写方案记录，没有就取固定的低档设置。"""
import json

from acceptance_helpers import ProjectCase
from collab.flow import record
from collab.state import Card
from desktop.view import card_view

KEYS = ["you", "disp", "test", "dev", "check", "rev", "accept"]


def station(view, key):
    return next(s for s in view["stations"] if s["key"] == key)


class WritePlanStation(ProjectCase):
    def write_config(self, data):
        (self.collab).mkdir(exist_ok=True)
        (self.collab / "配置.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_station_is_named_write_plan_and_is_always_codex(self):
        self.make_card()
        for kwargs in ({}, {"dispatcher": "agy"}, {"dispatcher": "custom:qwen --yolo"}, {"dispatcher": "codex"}):
            disp = station(card_view(self.project, **kwargs), "disp")
            self.assertEqual((disp["role"], disp["who"]), ("写方案", "Codex"), kwargs)

    def test_other_six_stations_are_unchanged(self):
        self.make_card()
        view = card_view(self.project)
        self.assertEqual([s["key"] for s in view["stations"]], KEYS)
        self.assertEqual([s["role"] for s in view["stations"] if s["key"] != "disp"],
                         ["提需求", "写测试", "写代码", "跑测试", "复查", "确认"])

    def test_a_project_without_collab_folder_still_shows_the_station(self):
        view = card_view(self.project)   # 还没接入
        self.assertEqual(station(view, "disp")["role"], "写方案")

    def test_model_comes_from_the_write_plan_record_when_there_is_one(self):
        cdir = self.make_card(state="待派发")
        record(cdir, "写方案", src="程序", dst="Codex", summary="开始写方案", model="GPT-6-Astra（低）")
        self.assertEqual(station(card_view(self.project), "disp")["model"], "GPT-6-Astra（低）")

    def test_model_without_a_record_is_the_fixed_low_setting_not_the_testers_high(self):
        self.write_config({"codex": "gpt-6.1-sol", "codex_effort": "xhigh",
                           "team": {"tester": "codex", "developer": "cursor", "reviewer": "agy"},
                           "role_models": {"tester": {"tool": "codex", "model": "gpt-6.1-sol", "effort": "high"}}})
        self.make_card(state="待派发")
        self.assertEqual(station(card_view(self.project), "disp")["model"], "GPT-6.1-Sol（低）")

    def test_old_config_with_a_dispatcher_setting_does_not_change_the_label(self):
        self.write_config({"team": {"dispatcher": "agy", "tester": "codex", "developer": "agy", "reviewer": "codex"},
                           "role_models": {"dispatcher": {"tool": "agy", "model": "gemini-4-argon-low"}}})
        self.make_card(state="待派发")
        disp = station(card_view(self.project, dispatcher="agy"), "disp")
        self.assertEqual((disp["who"], disp["model"]), ("Codex", "GPT-6.1-Sol（低）"))

    def test_a_card_in_progress_does_not_claim_the_plan_step_supervises_it(self):
        cdir = self.make_card(state="开发中")
        record(cdir, "开跑", src="程序", dst="程序", summary="开始施工",
               tools={"测试员": "codex", "开发员": "agy", "复查员": "codex"})
        disp = station(card_view(self.project), "disp")
        self.assertNotIn("盯进度", disp["detail"])
        self.assertNotIn("调度员", disp["detail"])
