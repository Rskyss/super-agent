"""老测试的假 agy 换成会说 ACP 的（docs/agy改走ACP/，11-22）。

派活叫 agy 改走 `agy acp` 之后，原来那种“被叫一次、印几句话就走”的假 agy 接不上了。这里把原来的脚本存成 <名字>.inner，
<名字> 本身换成一个小壳：被叫 `agy acp` 时由 tests/fake_agy_acp.py 用 ACP 说话、收到活就照老办法跑 .inner；
被叫别的（比如 `agy --version`）直接交给 .inner。老测试要它演什么，它照旧演什么。"""
import stat
import sys
from pathlib import Path

FAKE_ACP = Path(__file__).resolve().with_name("fake_agy_acp.py")


def write_fake_agy(path, script: str) -> None:
    """script：原来假 agy 的完整内容（带 #! 开头）。"""
    path = Path(path)
    inner = path.with_name(path.name + ".inner")
    inner.write_text(script, encoding="utf-8")
    inner.chmod(inner.stat().st_mode | stat.S_IEXEC)
    path.write_text(f'#!/bin/sh\nif [ "$1" = acp ]; then exec "{sys.executable}" "{FAKE_ACP}" --inner "{inner}" "$@"; fi\n'
                    f'exec "{inner}" "$@"\n', encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
