"""命令行入口：init / new-card / run / status。

用法（任意目录都能调用，包本身不依赖位置）：
  python3 <包路径>/collab init <项目目录>
  python3 <包路径>/collab new-card <项目目录> CARD-001 --requirement 需求.md
  python3 <包路径>/collab run <项目目录> CARD-001 --test-command "pytest -q"
  python3 <包路径>/collab status <项目目录>
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from datetime import datetime
from pathlib import Path

from collab import vcs
from collab.agents import ask as default_ask
from collab.archive import archive_card
from collab.install import InstallError, install_into
from collab.config import load_models
from collab.dispatch import run_card
from collab.judge import judge as default_judge
from collab.scope import read_allowed
from collab.ledger import ledger_add
from collab.state import Card
from collab.usage import count_lines, find_latest_session, record_card_usage, usage_since
from collab.watch import snapshot
from collab.workspace import PLAN_TEMPLATE, has_blanks, init_collab, render_board

_DEV_ADAPTER = "cursor"
_TEST_ADAPTER = "codex"


def _collab(project: str) -> Path:
    return Path(project) / ".collab"


def _card_state_path(project: str, card_id: str) -> Path:
    return _collab(project) / "cards" / card_id / "状态.json"


def _backup_row(project: str, card_id: str, tag: str) -> None:
    ledger = _collab(project) / "备份记录.md"
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(f"| {datetime.now():%Y-%m-%d %H:%M} | {tag} | {card_id} |\n")


def cmd_init(args, **_):
    collab = init_collab(args.project)
    print(f"已初始化协作区：{collab}")
    return 0


def cmd_new_card(args, **_):
    collab = _collab(args.project)
    if not collab.exists():
        print("请先执行 init", file=sys.stderr)
        return 1
    card_dir = collab / "cards" / args.card_id
    card_dir.mkdir(parents=True, exist_ok=True)
    text = Path(args.requirement).read_text(encoding="utf-8")
    (card_dir / "需求卡.md").write_text(text, encoding="utf-8")
    Card(args.card_id, state="待对齐").save(card_dir / "状态.json")  # 方案确认后才能派发
    if not (card_dir / "方案.md").exists():
        (card_dir / "方案.md").write_text(PLAN_TEMPLATE, encoding="utf-8")
    render_board(collab)
    print(f"已创建 {args.card_id}，状态：待对齐（写好方案、用户批准后才能开工）")
    return 0


def cmd_confirm_profile(args, **_):
    """用户确认项目说明后调用：之后才允许批准任何方案。"""
    collab = _collab(args.project)
    if not collab.exists():
        print("请先执行 init", file=sys.stderr)
        return 1
    profile = collab / "项目档案.md"
    if profile.exists() and has_blanks(profile.read_text(encoding="utf-8")):
        print("项目说明还有空白（（待填）），请先补全再确认", file=sys.stderr)
        return 1
    settings_path = collab / "配置.json"
    settings = {}
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["project_profile_confirmed"] = True
    settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    ledger_add(args.project, "项目说明已确认")
    print("项目说明已确认")
    return 0


def cmd_approve(args, **_):
    """用户确认拆解方案后调用：卡片从「待对齐」进入「待派发」。"""
    collab = _collab(args.project)
    card_dir = collab / "cards" / args.card_id
    state_path = card_dir / "状态.json"
    if not state_path.exists():
        print(f"没有卡片 {args.card_id}，请先 new-card", file=sys.stderr)
        return 1
    settings = {}
    if (collab / "配置.json").exists():
        settings = json.loads((collab / "配置.json").read_text(encoding="utf-8"))
    if not settings.get("project_profile_confirmed"):
        print("请先确认项目说明（confirm-profile）", file=sys.stderr)
        return 1
    if not (card_dir / "方案.md").exists():
        print("请先写好拆解方案（方案.md）", file=sys.stderr)
        return 1
    if has_blanks((card_dir / "方案.md").read_text(encoding="utf-8")):
        print("方案还有空白（（待填）），请补全后再确认", file=sys.stderr)
        return 1
    card = Card.load(state_path)
    card.transition("待派发")
    card.save(state_path)
    render_board(collab)
    ledger_add(args.project, f"{args.card_id}：方案已确认，可以开工")
    print(f"{args.card_id} 方案已确认，可以开工")
    return 0


def cmd_status(args, **_):
    collab = _collab(args.project)
    render_board(collab)
    print((collab / "任务板.md").read_text(encoding="utf-8"))
    return 0


def cmd_run(args, ask_fn=None, judge_fn=None, **_):
    project = Path(args.project)
    if not vcs.is_git_repo(project):
        print(f"{project} 不是 git 仓库，请先 git init", file=sys.stderr)
        return 1
    state_path = _card_state_path(args.project, args.card_id)
    if not state_path.exists():
        print(f"没有卡片 {args.card_id}，请先 new-card", file=sys.stderr)
        return 1
    card = Card.load(state_path)
    if card.state != "待派发":
        print(f"{args.card_id} 当前状态是「{card.state}」，方案确认后（待派发）才能开工", file=sys.stderr)
        return 1
    allowed = read_allowed((_collab(args.project) / "cards" / args.card_id / "需求卡.md")
                           .read_text(encoding="utf-8"))
    if not allowed:
        print("需求卡里还没有写明允许改动的文件范围（“## 允许改动的范围”下面列出文件）", file=sys.stderr)
        return 1

    tag = vcs.snapshot(project, f"pre-{args.card_id}-{datetime.now():%Y%m%d-%H%M%S}")
    _backup_row(args.project, args.card_id, tag)

    worktree = project.parent / f"{project.name}-worktrees" / args.card_id
    worktree.parent.mkdir(parents=True, exist_ok=True)
    vcs.add_worktree(project, args.card_id, worktree)
    vcs.link_dependencies(project, worktree)
    vcs.copy_local_settings(project, worktree)

    session = None
    if args.session_jsonl == "auto":
        session = find_latest_session(Path.home() / ".claude" / "projects")
        if session is None:
            print("没找到 Claude 会话记录，本次不记录额度", file=sys.stderr)
    elif args.session_jsonl:
        session = Path(args.session_jsonl)
    offset = count_lines(session) if session else 0

    card, verdict = run_card(
        card, collab_dir=_collab(args.project), worktree=str(worktree),
        test_command=shlex.split(args.test_command),
        dev_adapter=args.dev_tool, test_adapter=args.test_tool,
        ask_fn=ask_fn or default_ask, judge_fn=judge_fn or default_judge,
        models=load_models(args.project),
        allowed=allowed, page_command=shlex.split(args.page_check) if args.page_check else None,
        reviewer_adapter=None if args.review_tool == "none" else args.review_tool,
    )
    if session:
        record_card_usage(_collab(args.project), args.card_id, usage_since(session, offset))

    ledger_add(args.project, f"{args.card_id}：运行结束，状态「{card.state}」，返工 {card.rework} 次")
    print(f"{args.card_id} 最终状态：{card.state}（返工 {card.rework} 次）")
    return 0 if card.state == "待验收" else 2


def cmd_watch(args, **_):
    import time
    from datetime import datetime as _dt
    while True:
        text = snapshot(args.project, args.card_id)
        print(f"--- {_dt.now():%H:%M:%S} ---\n{text}\n", flush=True)
        state = Card.load(_card_state_path(args.project, args.card_id)).state
        if args.once or state in ("待验收", "已合并", "需Claude介入"):
            return 0
        time.sleep(args.interval)


def cmd_install(args, **_):
    try:
        target = install_into(args.project, update=args.update)
    except InstallError as exc:
        print(f"安装失败：{exc}", file=sys.stderr)
        return 1
    print(f"已安装到 {target}；协作区：{Path(args.project).resolve() / '.collab'}")
    print("下一步：起草项目说明（.collab/项目档案.md），用户确认后执行 confirm-profile")
    return 0


def cmd_archive(args, **_):
    try:
        target = archive_card(args.project, args.card_id, args.feature)
    except (FileNotFoundError, ValueError) as exc:
        print(f"归档失败：{exc}", file=sys.stderr)
        return 1
    print(f"已归档到 {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="collab", description="多 agent 协作包")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("install", help="把协作包装进一个项目，并初始化协作区")
    p.add_argument("project")
    p.add_argument("--update", action="store_true", help="已装过时，用最新版本替换包文件（协作记录不动）")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("init", help="在项目里生成协作区")
    p.add_argument("project")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("new-card", help="新建需求卡")
    p.add_argument("project")
    p.add_argument("card_id")
    p.add_argument("--requirement", required=True, help="需求卡正文文件")
    p.set_defaults(func=cmd_new_card)

    p = sub.add_parser("run", help="派发一张卡")
    p.add_argument("project")
    p.add_argument("card_id")
    p.add_argument("--test-command", required=True, help="判定用的测试命令，只看退出码")
    p.add_argument("--session-jsonl", help="Claude 会话记录路径，用于记录额度；填 auto 自动找最新的一份")
    p.add_argument("--page-check", help="真实页面检查命令（由项目定义），通过才算待验收")
    p.add_argument("--dev-tool", default="cursor", choices=["cursor", "codex", "agy"], help="开发员（写代码）")
    p.add_argument("--test-tool", default="codex", choices=["cursor", "codex", "agy"], help="测试员（写测试）")
    p.add_argument("--review-tool", default="agy", choices=["cursor", "codex", "agy", "none"],
                   help="独立复查（只读）；none 表示不复查")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("confirm-profile", help="确认项目说明（之后才能批准方案）")
    p.add_argument("project")
    p.set_defaults(func=cmd_confirm_profile)

    p = sub.add_parser("approve", help="确认拆解方案，卡片进入待派发")
    p.add_argument("project")
    p.add_argument("card_id")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("watch", help="实时查看某张卡的进度（每隔几秒刷新）")
    p.add_argument("project")
    p.add_argument("card_id")
    p.add_argument("--interval", type=int, default=5)
    p.add_argument("--once", action="store_true", help="只显示一次就退出")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("archive", help="把卡片记录归档到项目的 docs/<功能名>/")
    p.add_argument("project")
    p.add_argument("card_id")
    p.add_argument("--feature", required=True, help="功能名，作为 docs 下的文件夹名")
    p.set_defaults(func=cmd_archive)

    p = sub.add_parser("status", help="查看任务板")
    p.add_argument("project")
    p.set_defaults(func=cmd_status)
    return parser


def main(argv=None, ask_fn=None, judge_fn=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args, ask_fn=ask_fn, judge_fn=judge_fn)
