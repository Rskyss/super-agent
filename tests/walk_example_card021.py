"""走查脚本示例：拿 CARD-021（右栏停下提示）的卡片资料，截左栏、中间（新对话、打开卡片）、右栏和点开“交接”后的右栏。

P1 W0-1 的验收用，也是写走查脚本的样板（规则包《测试怎么写》引用它）。
单独跑：python3 tests/walk_example_card021.py  （截图存进 WALK_SHOTS_DIR，没给就存临时文件夹并打印位置）
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import walk_tool  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="walk-proj-") as tmp:
        project = walk_tool.make_project(Path(tmp) / "proj", cards=["CARD-021"])
        with walk_tool.Walk(project) as w:
            nav = w.open("nav", card="CARD-021")
            w.shot(nav, "左栏", expect="等待处理")
            chat = w.open("chat")
            w.shot(chat, "中间新对话", expect="行政")
            card = w.open_card("CARD-021")
            w.shot(card, "中间打开卡片", expect="右栏停下提示")
            panel = w.open("panel", card="CARD-021")
            w.shot(panel, "右栏流程", expect="右栏停下提示")
            with w.step("右栏点开交接"):
                panel.click("text=交接")
                w.shot(panel, "右栏点开交接", expect="全部交接")
            print(f"截图在：{w.shots_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
