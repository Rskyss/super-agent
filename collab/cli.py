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
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

from collab import vcs
from collab.agents import ask as default_ask
from collab.agents import kill_current_tool_process_group
from collab.archive import archive_card
from collab.install import InstallError, find_old_package, install_into, migrate
from collab.config import ConfigError, load_models, run_tools
from collab.dispatch import run_card
from collab.planner import plan_card
from collab.flow import record as flow_record
from collab.judge import judge as default_judge
from collab.scope import read_allowed
from collab.ledger import ledger_add
from collab.state import Card
from collab.usage import count_lines, find_latest_session, record_card_usage, usage_since
from collab.watch import TERMINAL_STATES, snapshot
from collab.workspace import PLAN_TEMPLATE, has_blanks, init_collab, profile_path, render_board

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
    if not Path(args.project).is_dir():
        print(f"项目目录不存在：{args.project}", file=sys.stderr)
        return 1
    collab = init_collab(args.project)
    print(f"已接入：{collab}（施工过程记录只留在本机，已加进不提交名单）")
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
    flow_record(card_dir, "需求", src="你", dst="调度员", summary=text, text=text)
    print(f"已创建 {args.card_id}，状态：待对齐（写好方案、用户批准后才能开工）")
    return 0


def cmd_confirm_profile(args, **_):
    """用户确认项目说明后调用：之后才允许批准任何方案。"""
    collab = _collab(args.project)
    if not collab.exists():
        print("请先执行 init", file=sys.stderr)
        return 1
    profile = profile_path(args.project)
    if not profile.exists():
        print("还没有项目说明：请先起草 docs/项目说明.md（模板见调度手册），用户看过再确认", file=sys.stderr)
        return 1
    if has_blanks(profile.read_text(encoding="utf-8")):
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
    flow_record(card_dir, "批准", src="你", dst="调度员", summary="批准方案，可以开工",
                text=(card_dir / "方案.md").read_text(encoding="utf-8"))
    ledger_add(args.project, f"{args.card_id}：方案已确认，可以开工")
    print(f"{args.card_id} 方案已确认，可以开工")
    return 0


def cmd_plan(args, **_):
    return plan_card(args.project, args.card_id)


def cmd_status(args, **_):
    collab = _collab(args.project)
    render_board(collab)
    print((collab / "任务板.md").read_text(encoding="utf-8"))
    return 0


def cmd_run(args, ask_fn=None, judge_fn=None, **_):
    # 开工必须用户亲手允许：调度员在 Codex 的受保护环境里直接开工时（没申请权限），一律拒绝，
    # 逼它申请提升权限，用户就会在聊天里看到“允许吗？”。用户点了允许后命令在受保护环境外运行，这个标记就不在了。
    if os.environ.get("CODEX_SANDBOX"):
        print("开工需要用户亲手允许：请申请提升权限后重新运行这条命令（用户会看到“允许吗？”）。"
              "在受保护环境里不能开工，这次什么都没做。", file=sys.stderr)
        return 3
    project = Path(args.project)
    if not vcs.is_git_repo(project):
        print(f"{project} 不是 git 仓库，请先 git init", file=sys.stderr)
        return 1
    state_path = _card_state_path(args.project, args.card_id)
    if not state_path.exists():
        print(f"没有卡片 {args.card_id}，请先 new-card", file=sys.stderr)
        return 1
    card = Card.load(state_path)
    resuming = card.state in ("开发中", "待测试", "测试中")
    resume_blocked = getattr(args, "resume_blocked", False)
    if card.state == "需Claude介入" and resume_blocked:
        card.transition("开发中")
        card.save(state_path)
        render_board(_collab(args.project))
        resuming = True
    if card.state != "待派发" and not resuming:
        print(f"{args.card_id} 当前状态是「{card.state}」，方案确认后（待派发）才能开工", file=sys.stderr)
        return 1
    allowed = read_allowed((_collab(args.project) / "cards" / args.card_id / "需求卡.md")
                           .read_text(encoding="utf-8"))
    if not allowed:
        print("需求卡里还没有写明允许改动的文件范围（“## 允许改动的范围”下面列出文件）", file=sys.stderr)
        return 1

    # 用谁干活：命令里写了就用命令里的，没写就用团队配置（在动手之前确定，配置坏了就在这里停下）
    try:
        tools = run_tools(args.project, dev=args.dev_tool, test=args.test_tool, review=args.review_tool)
    except ConfigError as exc:
        print(f"团队配置有问题：{exc}", file=sys.stderr)
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

    reviewer = tools["review"]
    run_summary = (f"接着上次中断处继续施工：测试员 {tools['test']}，开发员 {tools['dev']}，复查员 {reviewer or '不复查'}"
                   if resuming else
                   f"开始施工：测试员 {tools['test']}，开发员 {tools['dev']}，复查员 {reviewer or '不复查'}")
    main_pid = os.getpid()
    from collab.phase import process_start
    started_at = process_start(main_pid)
    _stopping = {"done": False}

    def _on_signal(signum, frame):
        # 第一次收到结束信号后忽略后续，确保能走完收尾
        if _stopping["done"]:
            return
        _stopping["done"] = True
        kill_current_tool_process_group()
        raise SystemExit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (OSError, ValueError):
            pass

    flow_record(_collab(args.project) / "cards" / args.card_id, "开跑", src="调度员", dst="程序",
                summary=run_summary,
                tools={"测试员": tools["test"], "开发员": tools["dev"], "复查员": reviewer},
                pid=main_pid, started=started_at,
                text=f"判定命令：{args.test_command}\n页面检查：{args.page_check or '无'}\n独立副本：{worktree}")
    card, verdict = run_card(
        card, collab_dir=_collab(args.project), worktree=str(worktree),
        test_command=["/bin/sh", "-c", args.test_command],
        dev_adapter=tools["dev"], test_adapter=tools["test"],
        ask_fn=ask_fn or default_ask, judge_fn=judge_fn or default_judge,
        models=load_models(args.project),
        allowed=allowed,
        page_command=["/bin/sh", "-c", args.page_check] if args.page_check else None,
        reviewer_adapter=reviewer,
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
        if args.once or state in TERMINAL_STATES:
            return 0
        time.sleep(args.interval)


def cmd_install(args, **_):
    try:
        target = install_into(args.project, update=args.update)
    except InstallError as exc:
        print(f"接入失败：{exc}", file=sys.stderr)
        return 1
    print("从 v0.2 起不需要再把协作包复制进项目了，直接用超级助手里的那一份。")
    print(f"已接入：{target}（施工过程记录只留在本机，已加进不提交名单）")
    if find_old_package(args.project) is not None:
        print("这个项目里还有一份旧的协作包副本（collab/），可以用 migrate 命令处理。")
    print("下一步：起草项目说明 docs/项目说明.md，用户确认后执行 confirm-profile")
    return 0


def cmd_check(args, **_):
    from collab.check import ROLE_NAMES, check_team
    try:
        results = check_team(args.project, timeout=args.timeout)
    except ConfigError as exc:
        print(f"团队配置有问题：{exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        for r in results:
            mark = "✅" if r["ok"] else "❌"
            same = f"（和{ROLE_NAMES[r['shared_with']]}同一家，只测一次）" if r["shared_with"] else ""
            print(f"{mark} {r['role_name']}：{r['tool_name'] or '—'}　{r['reason']}{same}")
            if r["how"] and not r["ok"]:
                print(f"   怎么办：{r['how']}")
    return 0 if all(r["ok"] for r in results) else 2


def cmd_migrate(args, **_):
    if not Path(args.project).is_dir():
        print(f"项目目录不存在：{args.project}", file=sys.stderr)
        return 1
    report = migrate(args.project, remove_old_package=args.remove_old_package)
    if report["old_package"]:
        print("已删除项目里的旧协作包（collab/）。" if report["removed"]
              else "项目里有一份旧协作包（collab/），这次没有删；要删请加 --remove-old-package。")
    else:
        print("项目里没有旧协作包。")
    if report["profile_moved"]:
        print("项目档案已复制成 docs/项目说明.md（旧文件保留）。")
    if report["ignored"]:
        print("已把 .collab/ 加进不提交名单。")
    print("协作记录一条没动。")
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

    p = sub.add_parser("install", help="（旧命令）现在等于接入：不复制协作包，只建协作区")
    p.add_argument("project")
    p.add_argument("--update", action="store_true", help="兼容旧用法，效果与不加相同")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("init", help="接入一个项目：建 .collab/ 并加进不提交名单（不复制协作包）")
    p.add_argument("project")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("check", help="检测连通：团队里每个人真干一件小事，建出文件才算通")
    p.add_argument("project")
    p.add_argument("--json", action="store_true", help="结果用 JSON 输出（给窗口用）")
    p.add_argument("--timeout", type=float, default=120, help="每家最长等多少秒，默认 120")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("migrate", help="旧装法的项目改成新方式：搬项目说明、加不提交名单；可选删除旧协作包")
    p.add_argument("project")
    p.add_argument("--remove-old-package", action="store_true", help="删除项目里的旧协作包副本（用户同意后才加）")
    p.set_defaults(func=cmd_migrate)

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
    p.add_argument("--dev-tool", default=None, choices=["cursor", "codex", "agy"],
                   help="开发员（写代码）；不写就用团队配置（默认 Cursor）")
    p.add_argument("--test-tool", default=None, choices=["cursor", "codex", "agy"],
                   help="测试员（写测试）；不写就用团队配置（默认 Codex）")
    p.add_argument("--review-tool", default=None, choices=["cursor", "codex", "agy", "none"],
                   help="独立复查（只读）；none 表示不复查；不写就用团队配置（默认 agy）")
    p.add_argument("--resume-blocked", action="store_true",
                   help="从「需Claude介入」转入「开发中」后继续（须与断点续跑一起用）")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("confirm-profile", help="确认项目说明（之后才能批准方案）")
    p.add_argument("project")
    p.set_defaults(func=cmd_confirm_profile)

    p = sub.add_parser("approve", help="确认拆解方案，卡片进入待派发")
    p.add_argument("project")
    p.add_argument("card_id")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("plan", help="按需求卡写拆解方案（只读）")
    p.add_argument("project")
    p.add_argument("card_id")
    p.set_defaults(func=cmd_plan)

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
