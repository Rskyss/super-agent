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
            "if(e!=='做完了，等你确认') throw new Error(e);"
            "console.log('ok');" % str(ROOT / "desktop" / "static" / "chat.js"))
        out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


if __name__ == "__main__":
    unittest.main()
