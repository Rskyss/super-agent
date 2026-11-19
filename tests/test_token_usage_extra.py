"""CARD-001 补充测试：只用临时会话、假模型和本地 Node，不碰真实会话。

对应施工单 A 和拆解方案：补读数边界、流程容错、累计去重及计时刷新。
原有验收测试、检查脚本和样本保持原样。
"""
import contextlib
import builtins
import importlib
import io
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from acceptance_helpers import GOOD_PLAN, ProjectCase, needs_node, run_node
from collab.agents import AgentResult
from collab.flow import read_flow, record


MAIN = "22222222-3333-7444-8555-666666666666"
CHILD = "22222222-3333-7444-8555-777777777777"
OTHER = "22222222-3333-7444-8555-888888888888"
HEADER = f"session id: {MAIN}\n"


def tokens(total=120, children=0):
    return dict(ok=True, total=total, input=100, cached=80, output=20,
                reasoning=5, children=children)


def count_line(total=120, **changes):
    usage = dict(input_tokens=100, cached_input_tokens=80, output_tokens=20,
                 reasoning_output_tokens=5, total_tokens=total)
    usage.update(changes)
    return json.dumps({"type": "event_msg", "payload": {
        "type": "token_count", "info": {"total_token_usage": usage}}}) + "\n"


def put_session(home, sid=MAIN, parent=None, day="2001/02/03", total=120):
    folder = Path(home) / "sessions" / day
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-2001-02-03T08-00-00-{sid}.jsonl"
    meta = {"type": "session_meta", "payload": {
        "id": sid, "session_id": sid if parent is None else parent}}
    path.write_text(json.dumps(meta) + "\n" + count_line(total), encoding="utf-8")
    return path


@contextlib.contextmanager
def count_reads(target):
    """给真实文件加读量计数，防止把大文件全读完再截取也混过测试。"""
    sizes = []

    class Reader:
        def __init__(self, fh):
            self.fh = fh

        def __getattr__(self, name):
            return getattr(self.fh, name)

        def __enter__(self):
            self.fh.__enter__()
            return self

        def __exit__(self, *args):
            return self.fh.__exit__(*args)

        def measured(self, value):
            sizes.append(len(value.encode("utf-8") if isinstance(value, str) else value))
            return value

        def read(self, *args):
            return self.measured(self.fh.read(*args))

        def readline(self, *args):
            return self.measured(self.fh.readline(*args))

        def readlines(self, *args):
            return [self.measured(line) for line in self.fh.readlines(*args)]

        def __iter__(self):
            return self

        def __next__(self):
            line = self.readline()
            if not line:
                raise StopIteration
            return line

    def instrument(original):
        def wrapped(file, *args, **kwargs):
            fh = original(file, *args, **kwargs)
            return Reader(fh) if isinstance(file, (str, bytes, os.PathLike)) and \
                os.path.realpath(os.fsdecode(file)) == os.path.realpath(target) else fh
        return wrapped

    with mock.patch("builtins.open", side_effect=instrument(builtins.open)), \
            mock.patch("io.open", side_effect=instrument(io.open)):
        yield sizes


class IsolatedHome(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.home = self.tmp / "codex"
        (self.home / "sessions").mkdir(parents=True)
        patcher = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def usage_module(self):
        # 在用例内加载：缺少实现时仍能发现并执行其余补测。
        return importlib.import_module("collab.token_usage")

    def assert_unreadable(self, result):
        self.assertFalse(result["ok"])
        self.assertTrue(result["reason"].strip(), "读不到时要有能看懂的原因")


class ReadBoundaries(IsolatedHome):
    def test_session_marker_in_body_is_not_a_header(self):
        reader = self.usage_module()
        self.assertEqual(reader.session_id_from_text("引用 session id: " + MAIN), "")
        self.assertEqual(reader.session_id_from_text("提示\n" + HEADER + "完成"), MAIN)

    def test_default_home_only_computes_path_without_reading_real_sessions(self):
        reader = self.usage_module()
        with mock.patch.dict(os.environ):
            os.environ.pop("CODEX_HOME", None)
            with mock.patch.object(Path, "home", return_value=self.tmp), \
                    mock.patch("os.path.expanduser", side_effect=lambda value:
                               str(self.tmp) + value[1:] if value.startswith("~") else value):
                # 同时兼容 expanduser 和 Path.home 两种实现；两者均落到临时目录。
                self.assertEqual(Path(reader.codex_home()), self.tmp / ".codex")

    def test_find_falls_back_to_old_dates_and_matches_entire_suffix(self):
        reader = self.usage_module()
        path = put_session(self.home)
        decoy = path.with_name(path.stem + "-extra.jsonl")
        path.rename(decoy)
        self.assertIsNone(reader.find_session_file(MAIN))
        path = put_session(self.home)
        self.assertEqual(Path(reader.find_session_file(MAIN)), path)

    def test_recent_dates_are_preferred_over_old_duplicate(self):
        reader = self.usage_module()
        put_session(self.home, total=1)
        for offset in (0, 1):
            with self.subTest(days_ago=offset):
                day = (date.today() - timedelta(days=offset)).strftime("%Y/%m/%d")
                recent = put_session(self.home, sid=OTHER if offset else MAIN, day=day)
                old_id = OTHER if offset else MAIN
                put_session(self.home, sid=old_id, total=2)
                self.assertEqual(Path(reader.find_session_file(old_id)), recent)

    def test_cached_path_does_not_cache_usage_or_leak_between_homes(self):
        reader = self.usage_module()
        path = put_session(self.home)
        self.assertEqual(reader.usage_from_text(HEADER)["total"], 120)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(count_line(250))
        self.assertEqual(reader.usage_from_text(HEADER)["total"], 250)
        second_home = self.tmp / "another-codex"
        second = put_session(second_home, total=900)
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(second_home)}):
            self.assertEqual(Path(reader.find_session_file(MAIN)), second)
            self.assertEqual(reader.usage_from_text(HEADER)["total"], 900)

    def test_latest_valid_record_survives_bad_and_empty_tail_records(self):
        reader = self.usage_module()
        path = put_session(self.home)
        bad_tails = [
            "这不是 JSON\n", "null\n", "[]\n",
            json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": None}}) + "\n",
            json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {}}}) + "\n",
            json.dumps({"type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": None}}}) + "\n",
            '{"type":"event_msg","payload":',
        ]
        with path.open("a", encoding="utf-8") as fh:
            fh.write(count_line(250))
            fh.writelines(bad_tails)
        got = reader.read_total(path)
        self.assertTrue(got["ok"])
        self.assertEqual(got["total"], 250)
        self.assertEqual(got["cached"], 80)

    def test_last_round_and_non_token_events_are_not_cumulative_usage(self):
        reader = self.usage_module()
        path = put_session(self.home)
        event = json.loads(count_line(900))
        event["payload"]["info"] = {"last_token_usage": {"total_tokens": 9999}}
        wrong_kind = json.loads(count_line(800))
        wrong_kind["type"] = "response_item"
        wrong_payload = json.loads(count_line(700))
        wrong_payload["payload"]["type"] = "message"
        with path.open("a", encoding="utf-8") as fh:
            for line in (event, wrong_kind, wrong_payload):
                fh.write(json.dumps(line) + "\n")
        self.assertEqual(reader.read_total(path)["total"], 120)

    def test_zero_usage_is_valid_and_not_replaced_by_previous_count(self):
        reader = self.usage_module()
        path = put_session(self.home)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(count_line(0, input_tokens=0, cached_input_tokens=0,
                                output_tokens=0, reasoning_output_tokens=0))
        got = reader.read_total(path)
        self.assertTrue(got["ok"])
        self.assertEqual([got[k] for k in ("total", "input", "cached", "output", "reasoning")],
                         [0, 0, 0, 0, 0])

    def test_tail_search_expands_past_multiple_windows(self):
        reader = self.usage_module()
        path = put_session(self.home, total=321)
        # 比 256 KB、512 KB、1 MB 都长，必须继续向前找；中文跨块也不能误判。
        filler = json.dumps({"type": "response_item", "payload": "继续输出" * 90},
                            ensure_ascii=False) + "\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(filler * 1800)
        self.assertEqual(reader.read_total(path)["total"], 321)

    def test_recent_usage_does_not_require_reading_large_session_prefix(self):
        reader = self.usage_module()
        path = put_session(self.home)
        with path.open("a", encoding="utf-8") as fh:
            fh.write((json.dumps({"type": "response_item", "payload": "x" * 1000}) + "\n") * 2000)
            fh.write(count_line(321))
        with count_reads(path) as sizes:
            got = reader.read_total(path)
        self.assertEqual(got["total"], 321)
        self.assertGreater(sum(sizes), 0, "必须真的读临时文件")
        self.assertLessEqual(sum(sizes), 256 * 1024, "尾部已有用量时只需第一块 256 KB")

    def test_unrecognised_usage_structure_returns_reason(self):
        reader = self.usage_module()
        path = self.tmp / "坏记录.jsonl"
        for value in (None, [], "不是用量", {"other": 123}):
            with self.subTest(value=value):
                path.write_text(json.dumps({"type": "event_msg", "payload": {
                    "type": "token_count", "info": {"total_token_usage": value}}}) + "\n",
                    encoding="utf-8")
                self.assert_unreadable(reader.read_total(path))

    def test_children_require_parent_id_different_own_id_and_same_date(self):
        reader = self.usage_module()
        put_session(self.home, total=1000)
        put_session(self.home, CHILD, parent=MAIN, total=11)
        put_session(self.home, OTHER, parent=MAIN, total=19)
        put_session(self.home, "unrelated", parent="other-parent", total=5000)
        put_session(self.home, "different-date", parent=MAIN, day="2001/02/04", total=7000)
        bad = put_session(self.home, "unreadable-child", parent=MAIN, total=90)
        bad.write_text(bad.read_text(encoding="utf-8").splitlines()[0] + "\n坏行\n", encoding="utf-8")
        self.assertEqual(reader.children_total(MAIN), 30)
        self.assertEqual(reader.children_total("missing-session"), 0)

    def test_child_relationship_is_read_only_from_first_line(self):
        reader = self.usage_module()
        put_session(self.home)
        child = put_session(self.home, CHILD, parent=MAIN, total=999)
        original = child.read_text(encoding="utf-8")
        child.write_text('{"type":"response_item"}\n' + original, encoding="utf-8")
        self.assertEqual(reader.children_total(MAIN), 0)

    def test_unrelated_session_scan_only_reads_its_metadata_line(self):
        reader = self.usage_module()
        put_session(self.home)
        unrelated = put_session(self.home, OTHER, parent="another-parent", total=9999)
        first = unrelated.read_bytes().splitlines(keepends=True)[0]
        with unrelated.open("ab") as fh:
            fh.write(b"x" * (1024 * 1024))
        with count_reads(unrelated) as sizes:
            got = reader.children_total(MAIN)
        self.assertEqual(got, 0)
        self.assertGreater(sum(sizes), 0)
        self.assertLessEqual(sum(sizes), len(first), "无关会话只看第一行，不扫正文")

    def test_log_header_after_first_16kb_is_not_read(self):
        reader = self.usage_module()
        put_session(self.home)
        log = self.tmp / "实时.log"
        log.write_bytes(b"x" * (16 * 1024) + b"\n" + HEADER.encode())
        self.assert_unreadable(reader.usage_from_log(log))

    def test_live_log_read_is_bounded_to_16kb(self):
        reader = self.usage_module()
        put_session(self.home)
        log = self.tmp / "大实时输出.log"
        log.write_bytes(HEADER.encode() + b"x" * (1024 * 1024))
        with count_reads(log) as sizes:
            got = reader.usage_from_log(log)
        self.assertEqual(got["total"], 120)
        self.assertGreater(sum(sizes), 0)
        self.assertLessEqual(sum(sizes), 16 * 1024, "实时日志只读开头 16 KB")

    def test_log_directory_and_none_paths_do_not_raise(self):
        reader = self.usage_module()
        for path in (self.tmp, None, self.tmp / "missing"):
            with self.subTest(path=path):
                self.assert_unreadable(reader.usage_from_log(path))
                self.assert_unreadable(reader.read_total(path))

    def test_sums_every_component_and_preserves_input_records(self):
        reader = self.usage_module()
        items = [tokens(120, 7), tokens(250, 9), {"ok": False, "reason": "缺一轮"}]
        before = json.dumps(items, sort_keys=True)
        got = reader.sum_tokens(items)
        self.assertEqual({k: got[k] for k in ("ok", "total", "input", "cached", "output",
                                             "reasoning", "children", "missing")},
                         dict(ok=True, total=370, input=200, cached=160, output=40,
                              reasoning=10, children=16, missing=1))
        self.assertEqual(json.dumps(items, sort_keys=True), before)
        self.assertEqual(reader.new_tokens(got), 80)

    def test_all_missing_reports_first_reason_and_exact_count(self):
        reader = self.usage_module()
        got = reader.sum_tokens([{"ok": False, "reason": "第一轮没读到"},
                                 {"ok": False, "reason": "第二轮没读到"}])
        self.assert_unreadable(got)
        self.assertEqual(got["reason"], "第一轮没读到")
        self.assertEqual(got["missing"], 2)


@contextlib.contextmanager
def broken_usage(consumer):
    """同时覆盖模块调用和直接导入，断言对象仍然是最终流程记录。"""
    reader = importlib.import_module("collab.token_usage")
    with contextlib.ExitStack() as stack:
        for name in ("usage_from_text", "usage_from_log"):
            stack.enter_context(mock.patch.object(reader, name, side_effect=OSError("模拟读取失败")))
            stack.enter_context(mock.patch.object(consumer, name, side_effect=OSError("模拟读取失败"),
                                                 create=True))
        yield


class RecordingFailures(IsolatedHome):
    def setUp(self):
        super().setUp()
        self.cdir = self.tmp / "card"
        self.work = self.tmp / "work"
        self.cdir.mkdir()
        self.work.mkdir()
        self.needs = self.cdir / "需求卡.md"
        self.needs.write_text("# 改颜色\n", encoding="utf-8")

    def ask(self, output=HEADER, live=HEADER, ok=True):
        def fake(adapter, prompt, cwd, live_path=None, **kwargs):
            if live_path:
                Path(live_path).write_text(live, encoding="utf-8")
            return AgentResult(ok=ok, output=output, attempts=1,
                               final="## 要改的文件\n改颜色" if ok else "")
        return fake

    def last(self, kind):
        return [e for e in read_flow(self.cdir) if e["kind"] == kind][-1]

    def dispatch(self, ask, adapter="codex"):
        from collab.dispatch import _dispatch
        return _dispatch(ask, self.cdir, "复查员", adapter, "复查", "看看", self.work, {}, 1)

    def test_output_without_header_falls_back_to_live_log_for_both_tools(self):
        put_session(self.home, total=345)
        for adapter in ("codex", "qwen"):
            with self.subTest(adapter=adapter):
                self.assertTrue(self.dispatch(self.ask(output="结论：通过"), adapter).ok)
                self.assertEqual(self.last("回复")["tokens"]["total"], 345)

    def test_valid_output_takes_priority_over_different_live_session(self):
        put_session(self.home, total=345)
        put_session(self.home, OTHER, total=999)
        self.dispatch(self.ask(live=f"session id: {OTHER}\n"))
        self.assertEqual(self.last("回复")["tokens"]["total"], 345)

    def test_failed_worker_still_records_consumption_and_reply_link(self):
        put_session(self.home, total=345)
        self.assertFalse(self.dispatch(self.ask(ok=False)).ok)
        reply = self.last("回复")
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["reply_to"], self.last("派活")["seq"])
        self.assertEqual(reply["tokens"]["total"], 345)

    def test_usage_exception_preserves_worker_success_and_records_reply(self):
        from collab import dispatch
        with broken_usage(dispatch):
            result = self.dispatch(self.ask())
        self.assertTrue(result.ok)
        reply = self.last("回复")
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["reply_to"], self.last("派活")["seq"])
        self.assert_unreadable(reply["tokens"])

    def test_unsuccessful_handover_still_records_tokens_for_both_tools(self):
        from collab.dispatch import _make_handover
        put_session(self.home, total=345)
        for adapter in ("codex", "qwen"):
            with self.subTest(adapter=adapter):
                _make_handover(self.ask(ok=False), self.cdir, adapter, {}, self.needs, self.work)
                event = self.last("交接资料")
                self.assertFalse(event["ok"])
                self.assertEqual(event["tokens"]["total"], 345)

    def test_handover_usage_exception_does_not_discard_successful_document(self):
        from collab import dispatch
        with broken_usage(dispatch):
            dispatch._make_handover(self.ask(), self.cdir, "codex", {}, self.needs, self.work)
        self.assertTrue((self.cdir / dispatch.HANDOVER).read_text(encoding="utf-8").strip())
        event = self.last("交接资料")
        self.assertTrue(event["ok"])
        self.assert_unreadable(event["tokens"])

    def test_unsupported_handovers_do_not_record_tokens(self):
        # token 第二版按新口径改（Claude，登记见 docs/token用量第二版/老测试改写登记.md）：agy、Cursor 备交接资料也记用量；这里的假输出不是它们的格式，所以记“读不到”并写原因
        from collab.dispatch import _make_handover
        put_session(self.home)
        for adapter in ("agy", "cursor"):
            with self.subTest(adapter=adapter):
                _make_handover(self.ask(), self.cdir, adapter, {}, self.needs, self.work)
                got = self.last("交接资料").get("tokens")
                self.assertIsInstance(got, dict)
                self.assertFalse(got.get("ok"))
                self.assertTrue(str(got.get("reason") or "").strip())


class PlanFailureIsolation(ProjectCase):
    def test_usage_exception_does_not_turn_successful_plan_into_failure(self):
        from collab import planner
        home = Path(self.tmp) / "codex"
        home.mkdir()
        os.environ["CODEX_HOME"] = str(home)
        cdir = self.make_card(allowed=False)
        plan = dict(GOOD_PLAN, profile="# 项目说明\n这是一个修改颜色的临时测试项目。\n")

        def fake_codex(argv, **kwargs):
            Path(argv[argv.index("-o") + 1]).write_text(json.dumps(plan), encoding="utf-8")
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": HEADER})()

        with broken_usage(planner), mock.patch.object(planner.subprocess, "run", side_effect=fake_codex):
            result = planner.plan_card(self.project, "CARD-001")
        self.assertEqual(result, 0)
        events = read_flow(cdir)
        self.assertNotIn("方案失败", [e["kind"] for e in events])
        self.assertTrue((cdir / "方案.md").is_file())
        self.assertTrue((cdir / "开工.json").is_file())
        event = [e for e in events if e["kind"] == "方案"][-1]
        self.assertFalse(event["tokens"]["ok"])
        self.assertTrue(event["tokens"]["reason"].strip())


class PanelAccumulation(ProjectCase):
    def setUp(self):
        super().setUp()
        self.home = Path(self.tmp) / "codex"
        (self.home / "sessions").mkdir(parents=True)
        os.environ["CODEX_HOME"] = str(self.home)
        self.cdir = self.make_card(state="开发中")

    def view(self):
        from desktop.view import card_view
        return card_view(self.project, "CARD-001")

    def station(self, view, key):
        return next(s for s in view["stations"] if s["key"] == key)

    def sent(self, role="开发员", tool="codex", live=True, round_no=1):
        return record(self.cdir, "派活", src="调度员", dst=role, tool=tool,
                      step="开发", round=round_no, summary="干活", live=live)

    def replied(self, sent, usage=None):
        extra = {} if usage is None else {"tokens": usage}
        return record(self.cdir, "回复", src=sent["dst"], dst="调度员", tool=sent["tool"],
                      step=sent["step"], reply_to=sent["seq"], round=sent["round"],
                      ok=True, summary="完成", **extra)

    def test_no_events_branch_has_zero_totals_and_no_station_usage(self):
        (self.cdir / "流转.jsonl").write_text("", encoding="utf-8")
        view = self.view()
        self.assertEqual(view["tokens"], {"total": 0, "other": 0})
        self.assertTrue(all("tokens" not in s for s in view["stations"]))

    def test_events_without_usage_have_zero_totals(self):
        self.assertEqual(self.view()["tokens"], {"total": 0, "other": 0})

    def test_adding_usage_preserves_every_existing_station_field(self):
        from desktop import view
        stations = self.view()["stations"]
        before = json.loads(json.dumps(stations))
        event = {"kind": "方案", "src": "Codex", "tokens": tokens(100)}
        view._add_tokens(stations, [event], self.cdir)
        for old, new in zip(before, stations):
            with self.subTest(station=old["key"]):
                self.assertEqual({key: value for key, value in new.items() if key != "tokens"}, old)
        self.assertEqual(self.station({"stations": stations}, "disp")["tokens"]["total"], 100)

    def test_repeated_plan_runs_sum_and_children_go_to_other_once(self):
        record(self.cdir, "方案", src="Codex", tokens=tokens(100, 3))
        record(self.cdir, "方案", src="Codex", tokens=tokens(200, 4))
        view = self.view()
        self.assertEqual(self.station(view, "disp")["tokens"]["total"], 300)
        self.assertEqual(view["tokens"], {"total": 307, "other": 7})

    def test_live_grows_then_reply_freezes_even_if_log_keeps_growing(self):
        session = put_session(self.home, total=120)
        sent = self.sent()
        log = self.cdir / sent["live"]
        log.write_text(HEADER, encoding="utf-8")
        self.assertEqual(self.station(self.view(), "dev")["tokens"]["total"], 120)
        with session.open("a", encoding="utf-8") as fh:
            fh.write(count_line(250))
        self.assertEqual(self.station(self.view(), "dev")["tokens"]["total"], 250)
        self.replied(sent, tokens(250))
        with session.open("a", encoding="utf-8") as fh:
            fh.write(count_line(9999))
        for _ in range(2):
            view = self.view()
            self.assertEqual(self.station(view, "dev")["tokens"]["total"], 250)
            self.assertEqual(view["tokens"]["total"], 250)

    def test_reply_to_matching_keeps_unanswered_earlier_round(self):
        put_session(self.home, total=120)
        early = self.sent(round_no=1)
        (self.cdir / early["live"]).write_text(HEADER, encoding="utf-8")
        later = self.sent(round_no=2)
        self.replied(later, tokens(250))
        view = self.view()
        self.assertEqual(self.station(view, "dev")["tokens"]["total"], 370)
        self.assertEqual(view["tokens"]["total"], 370)

    def test_completed_old_step_is_missing_without_backfilling_live_log(self):
        put_session(self.home, total=9999)
        for tool in ("codex", "qwen"):
            with self.subTest(tool=tool):
                sent = self.sent(role="测试员", tool=tool)
                (self.cdir / sent["live"]).write_text(HEADER, encoding="utf-8")
                self.replied(sent)
        # token 第二版按新口径改（Claude，登记见 docs/token用量第二版/老测试改写登记.md）：老卡记录里没有用量的轮，读它当时的实时输出补上（原来是“不补、算没读到”）
        view = self.view()
        usage = self.station(view, "test")["tokens"]
        self.assertTrue(usage["ok"])
        self.assertEqual(usage["total"], 9999 * 2)
        self.assertFalse(usage.get("missing"))
        self.assertEqual((view["tokens"]["total"], view["tokens"]["other"]), (9999 * 2, 0))

    def test_supported_and_unsupported_rounds_only_count_supported_usage(self):
        self.replied(self.sent(tool="qwen"), tokens(200))
        for tool in ("agy", "cursor"):
            self.replied(self.sent(tool=tool), tokens(9999, 9999))
        self.replied(self.sent(tool="codex"), {"ok": False, "reason": "会话没找到"})
        # token 第二版按新口径改（Claude，登记见 docs/token用量第二版/老测试改写登记.md）：agy、Cursor 也算进环节合计，辅助会话照旧算进“其他”
        view = self.view()
        usage = self.station(view, "dev")["tokens"]
        self.assertTrue(usage["ok"])
        self.assertEqual(usage["total"], 200 + 9999 * 2)
        self.assertEqual(usage["missing"], 1)
        self.assertEqual((view["tokens"]["total"], view["tokens"]["other"]), (200 + 9999 * 2 + 9999 * 2, 9999 * 2))

    def test_unsupported_live_steps_show_no_usage_even_with_codex_header(self):
        put_session(self.home, total=9999)
        for role, tool, key in (("测试员", "agy", "test"), ("开发员", "cursor", "dev")):
            sent = self.sent(role=role, tool=tool)
            (self.cdir / sent["live"]).write_text(HEADER, encoding="utf-8")
        # token 第二版按新口径改（Claude，登记见 docs/token用量第二版/老测试改写登记.md）：在跑的 agy 读实时输出（这里不是它的格式 → 读不到并写原因），Cursor 在跑 → 写“这一轮结束后出数”
        view = self.view()
        test = self.station(view, "test")["tokens"]
        self.assertFalse(test.get("ok"))
        self.assertTrue(str(test.get("reason") or "").strip())
        dev = self.station(view, "dev")["tokens"]   # Cursor 在跑、记录还不是它的格式：写原因（等出数或认不出都行），不编数
        self.assertFalse(dev.get("ok"))
        self.assertTrue(str(dev.get("reason") or "").strip())
        self.assertEqual(view["tokens"]["total"], 0)

    def test_handover_draft_and_all_children_are_other_once(self):
        record(self.cdir, "方案", src="Codex", tokens=tokens(100, 2))
        self.replied(self.sent(role="测试员"), tokens(200, 3))
        record(self.cdir, "交接资料", src="调度员", ok=True, tokens=tokens(40, 5))
        record(self.cdir, "交接资料", src="调度员", ok=False, tokens=tokens(10, 7))
        draft = self.sent(role="调度员")
        self.replied(draft, tokens(60, 11))
        view = self.view()
        self.assertEqual(view["tokens"], {"total": 438, "other": 138})
        self.assertEqual(self.station(view, "test")["tokens"]["total"], 200)

    def test_plan_live_is_not_counted_before_successful_plan_record(self):
        put_session(self.home, total=9999)
        sent = record(self.cdir, "写方案", src="程序", tool="codex", live=True, summary="正在写方案")
        (self.cdir / sent["live"]).write_text(HEADER, encoding="utf-8")
        view = self.view()
        self.assertNotIn("tokens", self.station(view, "disp"))
        self.assertEqual(view["tokens"], {"total": 0, "other": 0})

    def test_missing_live_log_is_reported_without_breaking_view(self):
        sent = self.sent(role="复查员", tool="qwen")
        (self.cdir / sent["live"]).unlink()
        view = self.view()
        usage = self.station(view, "rev")["tokens"]
        self.assertFalse(usage["ok"])
        self.assertEqual(usage["missing"], 1)
        self.assertTrue(usage["reason"].strip())
        self.assertEqual(view["card"]["id"], "CARD-001")


@needs_node()
class PanelFormatting(unittest.TestCase):
    def test_numeric_boundaries_and_zero_missing_format(self):
        result = run_node(r'''
const assert = require('assert');
const P = require('./desktop/static/panel.js');
for (const [n, text] of [[0, '0'], [999, '999'], [1000, '1,000'], [9999, '9,999'],
                          [10000, '1.0 万'], [100000000, '1.0 亿']]) {
  assert.strictEqual(P.metaText({state:'done', tokens:{ok:true, total:n, missing:0}}), text + ' token');
}
assert.strictEqual(P.tokenTitle({ok:true, total:120, input:100, cached:80, output:20}),
  '输入 100（其中缓存读取 80）· 输出 20 · 新消耗 40（不含缓存读取）');
assert.strictEqual(P.tokenTitle(undefined), '');
assert.strictEqual(P.totalText(undefined), '');
assert.strictEqual(P.totalText({total:9999, other:120}), '本卡合计 9,999 token');
assert.strictEqual(P.totalTitle({total:9999, other:120}), '其中交接资料等其他 120 token');
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_render_tick_finish_and_total_visibility_without_browser(self):
        # Node 中执行原 panel.js；只替换 DOM、网络与定时器，执行真实 renderFlow / tick。
        result = run_node(r'''
const assert = require('assert'), fs = require('fs'), vm = require('vm');
class Element {
  constructor() {
    this.textContent = ''; this.innerHTML = ''; this.hidden = true;
    this.style = {}; this.children = []; this.selectors = new Map(); this.attrs = {};
    this.classList = {add(){}, remove(){}, toggle(){}};
  }
  querySelector(s) {
    if (!this.selectors.has(s)) this.selectors.set(s, new Element());
    return this.selectors.get(s);
  }
  addEventListener() {}
  appendChild(child) { this.children.push(child); }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k]; }
}
const html = fs.readFileSync('./desktop/static/panel.html', 'utf8');
const ids = new Map([...html.matchAll(/id="([^"]+)"/g)].map(m => [m[1], new Element()]));
const $ = id => ids.get(id) || null;
const usage = {ok:true, total:8512, input:8000, cached:6000, output:512};
const station = {key:'dev', role:'写代码', who:'Codex', state:'active', elapsed:12,
                 quiet:1, detail:'汇报：正在改颜色', tokens:usage};
let payload = {installed:true, card:{id:'CARD-001'}, stations:[station], handoffs:[],
               tokens:{total:8632, other:120}};
let nextPoll, tick;
const context = {
  console, URLSearchParams, Date, module:{exports:{}}, location:{search:'?project=fake'}, window:{},
  document:{getElementById:$, createElement:() => new Element()},
  fetch:async () => ({json:async () => payload}),
  setTimeout:fn => {nextPoll = fn;}, setInterval:fn => {tick = fn;}
};
vm.runInNewContext(fs.readFileSync('./desktop/static/panel.js', 'utf8'), context);
const flush = async () => {for(let i=0;i<16;i++) await Promise.resolve();};
(async () => {
  await flush();
  const b = $('steps').children[0].querySelector('button');
  assert.ok(b.querySelector('.k').textContent.includes('8,512 token'), '运行中要显示用量');
  assert.ok(b.querySelector('.k').textContent.includes('已干'), '原有计时要保留');
  assert.ok(b.title.includes('正在改颜色'), '原有提示要保留');
  assert.ok(b.title.includes('输入 8,000（其中缓存读取 6,000）'), '提示要有用量明细');
  tick();
  assert.ok(b.querySelector('.k').textContent.includes('8,512 token'), '半秒刷新不能擦掉用量');
  assert.ok($('tokenTotal'), '合计元素必须在实际 HTML 里');
  assert.strictEqual($('tokenTotal').hidden, false);
  assert.strictEqual($('tokenTotal').textContent, '本卡合计 8,632 token');
  payload = {...payload, stations:[{...station, state:'done', elapsed:null, quiet:null}]};
  nextPoll(); await flush();
  assert.strictEqual(b.querySelector('.k').textContent, '8,512 token', '结束后只有用量');
  tick();
  assert.strictEqual(b.querySelector('.k').textContent, '8,512 token');
  payload = {...payload, stations:[{...station, state:'done', tokens:undefined}],
             tokens:{total:0, other:0}};
  nextPoll(); await flush();
  assert.strictEqual(b.querySelector('.k').textContent, '');
  assert.strictEqual($('tokenTotal').hidden, true, '切到零用量时要收起旧合计');
})().catch(e => {console.error(e); process.exitCode = 1;});
''')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
