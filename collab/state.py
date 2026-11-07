"""卡片状态机：只判断"状态能不能这样流转"，不做任何外部调用。

返工规则：测试没过 → 退回开发，返工次数 +1；返工满 MAX_REWORK 次后，再没过就转"需Claude介入"。
"""
from __future__ import annotations

import json
from pathlib import Path

MAX_REWORK = 3

_TRANSITIONS = {
    "待对齐": {"待派发", "已放弃"},
    "待派发": {"开发中", "已放弃"},
    "开发中": {"待测试", "需Claude介入", "已放弃"},
    "待测试": {"测试中", "已放弃"},
    "测试中": {"待验收", "开发中", "需Claude介入", "已放弃"},
    "待验收": {"已合并", "开发中", "需Claude介入", "已放弃"},
    "需Claude介入": {"开发中", "已合并", "已放弃"},
    "已合并": set(),
    "已放弃": set(),
}


class InvalidTransition(Exception):
    pass


class Card:
    def __init__(self, card_id: str, state: str = "待对齐", rework: int = 0):
        self.card_id = card_id
        self.state = state
        self.rework = rework

    def transition(self, to: str) -> None:
        if to not in _TRANSITIONS.get(self.state, set()):
            raise InvalidTransition(f"{self.card_id}: {self.state} → {to} 不允许")
        self.state = to

    def on_test_failed(self) -> bool:
        """测试没过。返回 True 表示已转"需Claude介入"（返工用完），False 表示退回开发。"""
        if self.rework >= MAX_REWORK:
            self.transition("需Claude介入")
            return True
        self.rework += 1
        self.transition("开发中")
        return False

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"card_id": self.card_id, "state": self.state, "rework": self.rework},
            ensure_ascii=False, indent=2)
        spare = path.with_suffix(path.suffix + ".tmp")
        spare.write_text(payload, encoding="utf-8")
        spare.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Card":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data["card_id"], data["state"], data["rework"])
