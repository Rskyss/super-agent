"""P1 卡 B 的走查脚本（写施工单的角色写，施工方不许改）：中间区卡片里显示走查截图并可点击查看大图。

搭一个假项目，放两张卡：
- CARD-002：没走查的普通卡（不能显示走查截图区域）；
- CARD-001：带 2 步走查记录的卡（第 1 步有设计图、第 2 步无设计图），截展示图和大图浮层。
单独跑：python3 tests/walk_卡B.py  （截图存进 WALK_SHOTS_DIR，没给就存临时文件夹并打印位置）
"""
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import walk_tool  # noqa: E402
from collab.flow import record  # noqa: E402
from collab.judge import PASS  # noqa: E402
from collab.state import Card  # noqa: E402

SAMPLE_SHOT_1 = ROOT / "docs" / "借鉴DSH" / "页面对齐" / "05-你验收.png"
SAMPLE_SHOT_2 = ROOT / "docs" / "借鉴DSH" / "页面对齐" / "04-调度员干活.png"
SAMPLE_DESIGN = ROOT / "docs" / "借鉴DSH" / "页面对齐" / "14-复查没过停下.png"
DESIGN_REL = "docs/页面对齐/设计图-右栏.png"


def _seed_card(project: Path, card_id: str, title: str, with_walk: bool) -> None:
    cdir = project / ".collab" / "cards" / card_id
    cdir.mkdir(parents=True)
    (cdir / "需求卡.md").write_text(f"# {title}\n\n## 要做什么\n中间卡片展示效果\n", encoding="utf-8")
    Card(card_id, state="待验收").save(cdir / "状态.json")
    record(cdir, "需求", src="你", dst="调度员", summary=title)
    if with_walk:
        sdir = cdir / "走查截图" / "第1次"
        sdir.mkdir(parents=True)
        shutil.copy2(SAMPLE_SHOT_1, sdir / "01-右栏总览.png")
        shutil.copy2(SAMPLE_SHOT_2, sdir / "02-点开交接.png")
        shots = [
            {"步骤": "右栏总览", "截图": "走查截图/第1次/01-右栏总览.png", "设计图": DESIGN_REL},
            {"步骤": "点开交接", "截图": "走查截图/第1次/02-点开交接.png", "设计图": ""},
        ]
        record(cdir, "回复", src="开发员", dst="调度员", ok=True, summary="汇报：已改好")
        record(cdir, "判定", src="程序", result=PASS, round=1, summary="第 1 轮：测试全部通过")
        record(cdir, "页面检查", src="程序", result=PASS, walk=True, folder="走查截图/第1次",
               shots=shots, summary="走查过了，截了 2 张图")
    (project.parent / f"{project.name}-worktrees" / card_id).mkdir(parents=True, exist_ok=True)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="walk-card-b-") as tmp:
        project = walk_tool.make_project(Path(tmp) / "proj")
        design_dst = project / DESIGN_REL
        design_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SAMPLE_DESIGN, design_dst)
        _seed_card(project, "CARD-002", "普通卡片", with_walk=False)
        _seed_card(project, "CARD-001", "带走查的卡", with_walk=True)

        with walk_tool.Walk(project) as w:
            p2 = w.open_card("CARD-002")
            w.shot(p2, "没走查的卡正常打开", expect="普通卡片")
            if p2.locator("#cardWalk").count() > 0 and p2.locator("#cardWalk").is_visible():
                raise walk_tool.WalkError("“没走查的卡正常打开”这一步不应该显示走查截图区域")

            with w.step("卡片显示走查截图"):
                p1 = w.open_card("CARD-001")
                p1.wait_for_function("""() => {
                    const imgs = [...document.querySelectorAll('#cardWalk img')];
                    return imgs.length === 3 && imgs.every(i => i.complete && i.naturalWidth >= 100);
                }""")
                w.shot(p1, "卡片显示走查截图", expect="走查截图（2 张）")

            with w.step("点开截图看大图"):
                p1.locator("#cardWalk .walk-thumb").first.click()
                p1.wait_for_function("""() => {
                    const img = document.querySelector('#walkLightbox img');
                    return !!img && img.complete && img.naturalWidth >= 100;
                }""")
                w.shot(p1, "点开截图看大图", expect="关闭")
            print(f"截图在：{w.shots_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
