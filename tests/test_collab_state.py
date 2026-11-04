import tempfile
import unittest
from pathlib import Path

from collab.state import Card, InvalidTransition, MAX_REWORK


class CardStateTest(unittest.TestCase):
    def test_happy_path_reaches_merged(self):
        card = Card("CARD-001")
        for to in ["待派发", "开发中", "待测试", "测试中", "待验收", "已合并"]:
            card.transition(to)
        self.assertEqual(card.state, "已合并")

    def test_illegal_transition_is_rejected(self):
        card = Card("CARD-001")
        with self.assertRaises(InvalidTransition):
            card.transition("已合并")  # 待对齐 不能直接跳到已合并
        self.assertEqual(card.state, "待对齐")

    def test_failed_test_goes_back_to_dev_and_counts_rework(self):
        card = Card("CARD-001", state="测试中")
        escalate = card.on_test_failed()
        self.assertFalse(escalate)
        self.assertEqual(card.state, "开发中")
        self.assertEqual(card.rework, 1)

    def test_three_reworks_then_fourth_failure_escalates(self):
        card = Card("CARD-001", state="测试中")
        for _ in range(MAX_REWORK):
            self.assertFalse(card.on_test_failed())  # 退回开发
            card.transition("待测试")  # 开发交活后重新进入测试
            card.transition("测试中")
        self.assertEqual(card.rework, MAX_REWORK)
        self.assertTrue(card.on_test_failed())
        self.assertEqual(card.state, "需Claude介入")

    def test_state_survives_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "状态.json"
            card = Card("CARD-007", state="开发中", rework=2)
            card.save(path)
            loaded = Card.load(path)
            self.assertEqual((loaded.card_id, loaded.state, loaded.rework),
                             ("CARD-007", "开发中", 2))


if __name__ == "__main__":
    unittest.main()
