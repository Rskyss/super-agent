"""流程修理第 17 条（试跑记录 ⑪）：卡片标题下面一行大白话“此刻在干什么”，跟着实时输出更新。

68b 里用户看着施工十几分钟只有计时，不知道在干嘛。
"""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from desktop.view import live_hint

ROOT = Path(__file__).resolve().parent.parent
CODEX_LIVE = ("user\n交代……\ncodex\n先读规则和任务卡片。\nexec\ncat a.py\n succeeded in 0ms:\n...\n"
              "codex\n68b 的 30 项验收测试已通过，接着查“提议”当动词的说法。\n\nexec\npython3 -m unittest x\n")


class LiveHint(unittest.TestCase):
    def test_last_codex_sentence(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "live.log"
            p.write_text(CODEX_LIVE, encoding="utf-8")
            self.assertEqual(live_hint(p), "68b 的 30 项验收测试已通过，接着查“提议”当动词的说法。")

    def test_long_sentence_is_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "live.log"
            p.write_text("codex\n" + "很长" * 100 + "\n", encoding="utf-8")
            self.assertLessEqual(len(live_hint(p)), 61)

    def test_no_output_gives_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "live.log"
            p.write_text("", encoding="utf-8")
            self.assertEqual(live_hint(p), "")
            self.assertEqual(live_hint(Path(tmp) / "没有.log"), "")


@unittest.skipUnless(shutil.which("node"), "这台电脑没有 node")
class HeaderLine(unittest.TestCase):
    def test_line_text(self):
        script = (
            "var C=require(%r);"
            "var t=C.cardNowLine({now:{text:'开发员 Cursor 正在写代码',detail:'',tone:'run'},elapsed:383,quiet:65,live:true});"
            "if(t!=='开发员 Cursor 正在写代码 · 已干 6 分钟 · 1 分钟前有动静') throw new Error(t);"
            "var d=C.cardNowLine({now:{text:'复查员 Codex 正在复查',detail:'测试已通过，接着查说法。'},elapsed:30,quiet:3,live:true});"
            "if(d.indexOf('：测试已通过，接着查说法。')<0||d.indexOf('刚刚有动静')<0) throw new Error(d);"
            "var e=C.cardNowLine({now:{text:'做完了，等你确认'},elapsed:3600,quiet:null,live:false});"
            # 复查第 1 轮：“已干多久”是当前这个人干了多久，不是整张卡的总时间
            "var w=C.cardNowLine({now:{text:'开发员 Cursor 正在写代码'},elapsed:1800,worker_elapsed:60,quiet:5,live:true});"
            "if(w.indexOf('已干 1 分钟')<0) throw new Error(w);"
            "if(e!=='做完了，等你确认') throw new Error(e);"
            "console.log('ok');" % str(ROOT / "desktop" / "static" / "chat.js"))
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


CURSOR_STREAM = "\n".join([
    '{"type":"system","subtype":"init"}',
    '{"type":"thinking","subtype":"delta","text":"嗯"}',
    '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"先看 calc.py 再改。"}]}}',
    '{"type":"tool_call","subtype":"started","tool_call":{"readToolCall":{"args":{"path":"/w/CARD-001/calc.py"}}}}',
]) + "\n"


class CursorLive(unittest.TestCase):
    """复查第 1 轮：Cursor 原来干完才吐一次结果，标题行看不到它在干什么；改成边干边吐（stream-json）"""

    def test_cursor_streams(self):
        from collab.agents import cursor_command
        cmd = cursor_command("干活")
        self.assertEqual(cmd[cmd.index("--output-format") + 1], "stream-json")

    def test_hint_from_cursor_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "live.log"
            p.write_text(CURSOR_STREAM, encoding="utf-8")
            self.assertEqual(live_hint(p), "正在看 calc.py")
            p.write_text(CURSOR_STREAM + '{"type":"tool_call","subtype":"started","tool_call":{"shellToolCall":'
                         '{"args":{"command":"python3 -m unittest discover -s tests -q"}}}}\n', encoding="utf-8")
            self.assertEqual(live_hint(p), "正在跑：python3 -m unittest discover -s tests -q")
            p.write_text(CURSOR_STREAM + '{"type":"assistant","message":{"content":[{"type":"text","text":"测试全过了。"}]}}\n',
                         encoding="utf-8")
            self.assertEqual(live_hint(p), "测试全过了。")

    def test_final_reply_still_read_from_stream(self):
        from collab.agents import readable
        out = CURSOR_STREAM + '{"type":"result","subtype":"success","result":"汇报：改好了"}\n'
        self.assertEqual(readable(out), "汇报：改好了")


class PageGuards(unittest.TestCase):
    def test_line_ignores_stale_and_overlapping_requests(self):
        """复查第 1 轮：切卡后不能显示上一张卡的进度；慢请求不能叠加、旧结果不能盖新结果"""
        js = (ROOT / "desktop" / "static" / "chat.js").read_text(encoding="utf-8")
        part = js.split("function showNow()")[1].split("showNow();")[0]
        self.assertIn("nowBusy", part, "上一次还没回来就不再发")
        self.assertIn("forCard !== nowCard", part, "回来的结果不是当前这张卡的就丢掉")


class WorkerElapsed(unittest.TestCase):
    def test_view_gives_current_worker_time(self):
        import datetime
        from collab.flow import record
        from collab.state import Card
        from desktop.view import card_view
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            cdir = p / ".collab" / "cards" / "CARD-001"
            cdir.mkdir(parents=True)
            (cdir / "需求卡.md").write_text("# 卡\n", encoding="utf-8")
            Card("CARD-001", state="开发中").save(cdir / "状态.json")
            record(cdir, "开跑", src="调度员", dst="程序", summary="开始施工")
            record(cdir, "派活", src="调度员", dst="开发员", tool="cursor", step="开发", round=1, summary="写", live=True)
            later = datetime.datetime.now() + datetime.timedelta(seconds=90)
            v = card_view(p, "CARD-001", now=later)
            self.assertIn("worker_elapsed", v)
            self.assertLessEqual(v["worker_elapsed"], v["elapsed"])


if __name__ == "__main__":
    unittest.main()
