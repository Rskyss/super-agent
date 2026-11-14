"""第二期 1 天实测给做卡 AI 用的三个命令（不进产品）。

用法（在实测工作文件夹里）：
  python3 lab.py blocks                       看积木块、模型名单、能不能用，和已有的能力卡
  python3 lab.py check 卡片.json               用产品现成的规则查卡片格式
  python3 lab.py trial 卡片.json '{"项": "例子"}'   用产品现成的办法真实试做一次

积木库资料夹由环境变量 SUPER_AGENT_BLOCKS_HOME 指定（实测用隔离的临时资料夹，不碰真实积木库）。
不读、不写钥匙串：只能试做不需要密钥的积木块（AI 助手类）。
"""
import json
import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))

from collab import abilities, abilities_run, blocks  # noqa: E402
from collab.blocks import BlockError  # noqa: E402

CATEGORY = {"agent": "AI 助手", "llm_api": "大模型 API", "search": "联网搜索", "tool": "普通工具"}


def _home() -> str:
    h = os.environ.get("SUPER_AGENT_BLOCKS_HOME")
    if not h:
        sys.exit("没设置 SUPER_AGENT_BLOCKS_HOME：实测必须用隔离的积木库资料夹")
    return h


def _usable(card_id, home):
    last = blocks.last_record(card_id, home, type="check")
    if not last:
        return "没检测过"
    return "能用" if last.get("ok") else f"不能用（{last.get('reason', '')}）"


def _models(card, home):
    last = blocks.last_record(card["id"], home, type="check") or {}
    names = list(last.get("models") or [])
    if card.get("model") and card["model"] not in names:
        names.insert(0, card["model"])
    return names


def cmd_blocks():
    home = _home()
    print("== 积木块 ==")
    for card in blocks.list_cards(home):
        cat = CATEGORY.get(blocks.category(card), "未知")
        need_key = cat in ("大模型 API", "联网搜索")
        note = "（要密钥，这次实测不能试做）" if need_key else ""
        print(f"- 编号 {card['id']}｜{card.get('name', '')}｜{cat}｜{_usable(card['id'], home)}{note}")
        models = _models(card, home)
        if models:
            more = f" 等共 {len(models)} 个" if len(models) > 12 else ""
            print(f"  模型名单：{'、'.join(models[:12])}{more}；默认 {card.get('model') or models[0]}")
    print("\n== 已有能力卡 ==")
    have = abilities.list_abilities(home)
    if not have:
        print("（还没有）")
    for a in have:
        print(f"- {a['id']}｜{a.get('title', '')}｜用 {a.get('block')}｜{a.get('description', '')}")


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.exit(f"读不了卡片文件：{exc}")


def _check(card, home):
    abilities.validate_ability(card)
    bcard = blocks.load_card(card["block"], home)
    warns = abilities.fits_block(card, bcard, _models(bcard, home), models_complete=False)
    return bcard, warns


def cmd_check(path):
    home = _home()
    card = _load(path)
    try:
        _, warns = _check(card, home)
    except (BlockError, ValueError, KeyError) as exc:
        print(f"没过：{exc}")
        sys.exit(1)
    print("格式检查通过" + ("；提醒：" + "；".join(warns) if warns else ""))


def cmd_trial(path, inputs_text):
    home = _home()
    card = _load(path)
    try:
        inputs = json.loads(inputs_text)
    except ValueError as exc:
        sys.exit(f"输入不是合法的 JSON：{exc}")
    try:
        bcard, _ = _check(card, home)
    except (BlockError, ValueError, KeyError) as exc:
        print(f"没过格式检查，先改卡片：{exc}")
        sys.exit(1)
    if CATEGORY.get(blocks.category(bcard)) != "AI 助手":
        print("这次实测只能试做 AI 助手类积木块（别的要密钥）")
        sys.exit(1)
    try:
        res = abilities_run.run(card["id"], inputs, home, card=card, kind="trial", path=os.environ.get("PATH", ""))
    except (BlockError, ValueError, KeyError) as exc:
        print(f"试做没跑起来：{exc}")
        sys.exit(1)
    ok = res.get("ok")
    print("试做通过" if ok else f"试做没成：{res.get('reason', '')}")
    out = res.get("output")
    shown = json.dumps(out, ensure_ascii=False)[:3000] if not isinstance(out, str) else out[:3000]
    print(f"交回的结果（截前 3000 字）：\n{shown}")
    for f in res.get("files") or []:
        print(f"成果文件：{f}")
    sys.exit(0 if ok else 1)


def main(argv):
    if not argv or argv[0] not in ("blocks", "check", "trial"):
        sys.exit(__doc__)
    if argv[0] == "blocks":
        cmd_blocks()
    elif argv[0] == "check" and len(argv) == 2:
        cmd_check(argv[1])
    elif argv[0] == "trial" and len(argv) == 3:
        cmd_trial(argv[1], argv[2])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
