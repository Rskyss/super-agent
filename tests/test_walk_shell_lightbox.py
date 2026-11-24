"""方案 1（纯单向图集快照 + 页面批次号）：纯原生管理全窗大图与翻页，零 /api/view 副作用，零双向传话。"""
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from acceptance_helpers import ROOT, STATIC, SWIFT, needs_node


def _block_after(text: str, start: str) -> str:
    idx = text.find(start)
    if idx < 0:
        return ""
    open_idx = text.find("{", idx)
    if open_idx < 0:
        return ""
    depth = 0
    for pos in range(open_idx, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx:pos + 1]
    return ""


def _decl_block(text: str, start: str) -> str:
    idx = text.find(start)
    if idx < 0:
        return ""
    open_idx = text.find("{", idx)
    if open_idx < 0:
        return ""
    depth = 0
    for pos in range(open_idx, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return text[idx:pos + 1]
    return ""


def _case_body(text: str, name: str) -> str:
    m = re.search(r'case "%s":(.*?)(?=\n\s*case "|\n\s*default:)' % re.escape(name), text, re.S)
    return m.group(1) if m else ""


def _run_swift_snippet(code: str) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        src = tmp_path / "snippet.swift"
        cache = tmp_path / "module_cache"
        cache.mkdir()
        src.write_text("import Foundation\n" + code, encoding="utf-8")
        env = dict(os.environ)
        env["CLANG_MODULE_CACHE_PATH"] = str(cache)
        env["SWIFT_MODULE_CACHE_PATH"] = str(cache)
        proc = subprocess.run(
            ["swift", "-module-cache-path", str(cache), str(src)],
            capture_output=True, text=True, timeout=180, cwd=str(ROOT), env=env,
        )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return proc.stdout


class WalkShellLightboxTest(unittest.TestCase):
    @needs_node()
    def test_chat_js_one_way_snapshot_and_no_draft_leak(self):
        proc = subprocess.run(
            ["node", str(Path(__file__).resolve().parent / "walk_shell_lightbox_dom.js")],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)

        chat_js = (STATIC / "chat.js").read_text(encoding="utf-8")
        for removed in ("walk-image-close", "walk_close", "stepWalkLightbox", "walkOverlayClosed"):
            self.assertNotIn(removed, chat_js, f"chat.js 不应再保留双向通信残留：{removed}")

    def test_swift_walk_gallery_pure_functions_runtime(self):
        swift = SWIFT.read_text(encoding="utf-8")
        item_decl = _decl_block(swift, "struct WalkGalleryItem:")
        parse_decl = _decl_block(swift, "func parseWalkGallery(")
        next_decl = _decl_block(swift, "func nextWalkGalleryIndex(")
        self.assertTrue(item_decl and parse_decl and next_decl)

        driver = "\n".join([item_decl, parse_decl, next_decl]) + """
let sameJSON = "[{\\"kind\\":\\"shot\\",\\"path\\":\\"s/01.png\\",\\"title\\":\\"第 1 步：同名 · 本次截图\\"},{\\"kind\\":\\"design\\",\\"path\\":\\"docs/same.png\\",\\"title\\":\\"第 1 步：同名 · 设计图\\"},{\\"kind\\":\\"shot\\",\\"path\\":\\"s/02.png\\",\\"title\\":\\"第 2 步：同名 · 本次截图\\"},{\\"kind\\":\\"design\\",\\"path\\":\\"docs/same.png\\",\\"title\\":\\"第 2 步：同名 · 设计图\\"}]"
let (sameItems, idx2) = parseWalkGallery(raw: sameJSON, clickedKind: "design", clickedPath: "docs/same.png", clickedTitle: "第 2 步：同名 · 设计图", clickedIndex: 3)
assert(sameItems.count == 4 && idx2 == 3 && sameItems[idx2].title == "第 2 步：同名 · 设计图", "同名步骤按 index 匹配失败")

let (_, idxOutOfRange) = parseWalkGallery(raw: sameJSON, clickedKind: "design", clickedPath: "docs/same.png", clickedTitle: "第 2 步：同名 · 设计图", clickedIndex: 99)
assert(idxOutOfRange == 3, "index 越界时按 title 精确匹配失败")

let mixedJSON = "[{\\"kind\\":\\"evil\\",\\"path\\":\\"x.png\\",\\"title\\":\\"bad\\"},{\\"kind\\":\\"shot\\",\\"path\\":\\"\\",\\"title\\":\\"empty\\"},{\\"kind\\":\\"shot\\",\\"path\\":\\"s/01.png\\",\\"title\\":\\"第一步\\"}]"
let (mixedItems, mixedIdx) = parseWalkGallery(raw: mixedJSON, clickedKind: "shot", clickedPath: "s/01.png", clickedTitle: "第一步", clickedIndex: 0)
assert(mixedItems.count == 1 && mixedIdx == 0 && mixedItems[0].path == "s/01.png", "非法项未过滤干净")

for badRaw in [nil, "", "not-json", "[{\\"kind\\":\\"evil\\",\\"path\\":\\"x.png\\"}]"] as [String?] {
    let (fbItems, fbIdx) = parseWalkGallery(raw: badRaw, clickedKind: "design", clickedPath: "d/ok.png", clickedTitle: "退回单图", clickedIndex: 2)
    assert(fbItems.count == 1 && fbIdx == 0 && fbItems[0] == WalkGalleryItem(kind: "design", path: "d/ok.png", title: "退回单图"), "异常输入未安全退回单图")
}

assert(nextWalkGalleryIndex(current: 0, count: 3, delta: -1) == 2)
assert(nextWalkGalleryIndex(current: 2, count: 3, delta: 1) == 0)
assert(nextWalkGalleryIndex(current: 1, count: 3, delta: 1) == 2)
assert(nextWalkGalleryIndex(current: 1, count: 3, delta: -1) == 0)
assert(nextWalkGalleryIndex(current: 0, count: 1, delta: 1) == nil)
assert(nextWalkGalleryIndex(current: 0, count: 1, delta: -1) == nil)
assert(nextWalkGalleryIndex(current: 1, count: 3, delta: 0) == nil)
print("SWIFT_RUNTIME_OK")
"""
        out = _run_swift_snippet(driver)
        self.assertIn("SWIFT_RUNTIME_OK", out)

    def test_swift_native_gallery_wiring_and_safety(self):
        swift = SWIFT.read_text(encoding="utf-8")

        load_chat = _block_after(swift, "func loadChat(")
        reuse_branch = _block_after(load_chat, 'if mode != "fresh"')
        self.assertTrue(reuse_branch)
        self.assertNotIn("chatPageSeq", reuse_branch, "复用已加载聊天页时不应递增页面批次号")
        load_main = load_chat.split(reuse_branch, 1)[1]
        seq_inc = "chatPageSeq += 1"
        shell_param = r"&shell=1&shell_page=\(chatPageSeq)"
        self.assertIn("closeWalkImageOverlay()", load_main)
        self.assertEqual(load_main.count(seq_inc), 1, "loadChat 主加载路径应恰好递增一次 chatPageSeq")
        self.assertIn(shell_param, load_main)
        self.assertIn("chatWeb.load(", load_main)
        inc_pos = load_main.index(seq_inc)
        param_pos = load_main.index(shell_param)
        load_pos = load_main.index("chatWeb.load(")
        self.assertLess(load_main.index("closeWalkImageOverlay()"), load_pos)
        self.assertLess(inc_pos, param_pos, "chatPageSeq += 1 必须先于拼装 shell_page URL 参数")
        self.assertLess(param_pos, load_pos, "shell_page URL 参数必须用于 chatWeb.load 加载")
        self.assertNotIn("chatPageSeq", load_main[param_pos + len(shell_param):],
                         "拼装 shell_page 参数之后绝不能再次改动 chatPageSeq，以免新页面批次号立刻过期")
        self.assertRegex(
            load_main,
            r'let\s+query\s*=[\s\S]*?&shell=1&shell_page=\\\(chatPageSeq\)"\s*'
            r'if\s+let\s+url\s*=\s*URL\(string:\s*"http://127\.0\.0\.1:\\\(port\)/static/chat\.html\?\\\(query\)"\)\s*\{\s*'
            r'chatWeb\.load\(URLRequest\(url:\s*url\)\)\s*\}',
            "chatWeb.load 必须直接加载拼入了 shell_page 的 query 地址",
        )

        main_frame_fn = _block_after(swift, "func isChatMainFrame(")
        self.assertRegex(
            main_frame_fn,
            r"webView\s*===\s*chatWeb\s*&&\s*action\.sourceFrame\.isMainFrame\s*&&\s*action\.targetFrame\?\.isMainFrame\s*==\s*true",
        )

        self.assertFalse(_case_body(swift, "walk-image-close"), "不应再存在 case \"walk-image-close\":")
        self.assertNotIn("walk_close", _case_body(swift, "place"), "case \"place\": 不应夹带 walk_close")

        walk_case = _case_body(swift, "walk-image")
        self.assertTrue(walk_case)
        self.assertRegex(
            walk_case,
            r"guard\s+isChatMainFrame\(webView,\s*navigationAction\),\s*window\.isVisible,\s*leftView\s*==\s*\.chat,\s*let\s+activeCard\s*=\s*selectedCard,\s*chatLoadedKey\s*!=\s*nil,\s*let\s+page\s*=\s*Int\(value\(\"page\"\)\s*\?\?\s*\"\"\),\s*page\s*==\s*chatPageSeq,\s*value\(\"project\"\)\s*==\s*project\.path,\s*let\s+card\s*=\s*value\(\"card\"\),\s*card\s*==\s*activeCard,\s*let\s+kind\s*=\s*value\(\"kind\"\),\s*\(kind\s*==\s*\"shot\"\s*\|\|\s*kind\s*==\s*\"design\"\),\s*let\s+rel\s*=\s*value\(\"path\"\),\s*!rel\.isEmpty\s+else",
        )
        self.assertIn("parseWalkGallery(", walk_case)
        self.assertIn("openWalkImageOverlay(card: card, items: items, index: startIndex)", walk_case)

        open_fn = _block_after(swift, "func openWalkImageOverlay(")
        self.assertTrue(open_fn)
        self.assertNotIn("/api/view", open_fn, "看图绝不能再发可能触发施工排队的 /api/view")
        self.assertNotIn("walkGalleryTask", open_fn, "不应再有后台拉图集任务")
        self.assertIn("overlay.configure(title: items[index].title, index: index, total: items.count)", open_fn)
        self.assertIn("loadWalkOverlayImage(card: card, item: items[index], index: index, total: items.count)", open_fn)
        self.assertRegex(
            open_fn,
            r"event\.window\s*===\s*self\.window\s*,\s*self\.walkOverlay\s*!=\s*nil\s*,\s*self\.window\.attachedSheet\s*==\s*nil",
            "键盘监听必须限定主窗口、遮罩存在且无弹窗 sheet",
        )

        step_fn = _block_after(swift, "func stepWalkGallery(")
        self.assertTrue(step_fn)
        self.assertNotIn("evaluateJavaScript", step_fn, "翻页必须纯原生完成，绝不反向调网页")
        self.assertIn("nextWalkGalleryIndex(", step_fn)
        self.assertIn("loadWalkOverlayImage(", step_fn)

        load_fn = _block_after(swift, "func loadWalkOverlayImage(")
        self.assertTrue(load_fn)
        self.assertNotIn("/api/view", load_fn, "取图函数绝不能调用可能触发施工排队的 /api/view")
        self.assertNotIn("walkGalleryTask", load_fn)
        self.assertEqual(load_fn.count("URLSession.shared.dataTask"), 1, "取图函数应只发出 1 个只读看图请求")
        self.assertIn("walkImageTask?.cancel()", load_fn)
        self.assertIn("walkImageSeq += 1", load_fn)
        self.assertIn("/api/clerk/card/image?", load_fn)
        self.assertIn("chatToken", load_fn)
        self.assertNotIn("configure(", load_fn[:load_fn.index("guard let imgURL")],
                         "切图请求准备前绝不提前更改标题或页码")
        url_guard_tail = load_fn[load_fn.index("URL(string:"):].split("return\n        }", 1)[1]
        self.assertNotIn("configure(", url_guard_tail[:url_guard_tail.index("URLSession.shared.dataTask")],
                         "切图请求返回前绝不提前更改标题或页码")
        self.assertRegex(
            load_fn,
            r"self\.walkImageSeq\s*==\s*seq,\s*let\s+currentOverlay\s*=\s*self\.walkOverlay,\s*self\.project\.path\s*==\s*askedProject,\s*self\.walkOverlayCard\s*==\s*card",
        )
        self.assertEqual(
            load_fn.count("configure(title: item.title, index: index, total: total)"),
            3,
            "URL失败分支、加载成功分支、加载失败分支都必须带着完整 total 配置按钮和标题",
        )
        self.assertNotIn("NSImage(contentsOfFile:", load_fn)
        self.assertNotIn("Data(contentsOf: URL(fileURLWithPath:", load_fn)

        close_fn = _block_after(swift, "func closeWalkImageOverlay(")
        self.assertTrue(close_fn)
        self.assertNotIn("walkOpenSeq", close_fn)
        self.assertNotIn("walkGalleryTask", close_fn)
        self.assertIn("walkImageSeq += 1", close_fn)
        self.assertIn("walkImageTask?.cancel()", close_fn)
        self.assertIn("walkGalleryItems = []", close_fn)
        self.assertIn("NSEvent.removeMonitor(monitor)", close_fn)

        server_stopped = _block_after(swift, "func serverStopped(")
        after_guard = server_stopped.split("guard !quitting else { return }", 1)[1]
        before_first_if = re.split(r"\n\s*if\b", after_guard, maxsplit=1)[0]
        self.assertIn("chatPageSeq += 1", before_first_if, "停服必须在进入条件分支前使页面批次号失效")
        self.assertIn("closeWalkImageOverlay()", before_first_if, "停服必须在进入条件分支前关掉看图遮罩")

        close_chat = _block_after(swift, "func closeChatOnServer(")
        if_clear = _block_after(close_chat, "if clearView")
        self.assertIn("chatPageSeq += 1", if_clear)
        self.assertIn("closeWalkImageOverlay()", if_clear)

        overlay_cls = _block_after(swift, "final class WalkImageOverlayView:")
        self.assertIn("displayedImageRect()", overlay_cls)
        self.assertIn("scrollWheel", overlay_cls)
        for part_name, part in (
            ("walk-image", walk_case),
            ("openWalkImageOverlay", open_fn),
            ("stepWalkGallery", step_fn),
            ("loadWalkOverlayImage", load_fn),
            ("closeWalkImageOverlay", close_fn),
            ("WalkImageOverlayView", overlay_cls),
        ):
            self.assertNotIn("/api/view", part, f"{part_name} 绝不能调用可能触发施工排队的 /api/view")
            self.assertNotIn("walkGalleryTask", part, f"{part_name} 不应保留后台拉图集任务")
            if part_name != "loadWalkOverlayImage":
                self.assertNotIn("URLSession", part, f"{part_name} 不应额外发起网络请求")
        self.assertNotIn("hasImage", swift, "移除多余的 hasImage 异步判定残留")

        for hook in ("func selectCard(", "func showLeft(", "func switchTo(", "func loadChat(",
                     "func newChat(", "func windowShouldClose(", "func serverStopped(", "func applicationWillTerminate("):
            self.assertIn("closeWalkImageOverlay()", _block_after(swift, hook), f"{hook} 必须调用 closeWalkImageOverlay()")

        self.assertNotIn("selectCard(nil)", _block_after(swift, "func newChat("),
                         "绝不能在 newChat 中调用 selectCard(nil)，以免将空白主对话旧草稿带进新对话")

    def test_fallback_css_unboxed_and_keeps_close_button_visible(self):
        css = (STATIC / "chat.css").read_text(encoding="utf-8")
        m_lb = re.search(r"#walkLightbox\s*\{([^}]*)\}", css)
        self.assertIsNotNone(m_lb)
        self.assertIn("overflow: auto", m_lb.group(1))
        self.assertRegex(css, r"#walkLightbox\[hidden\][^{]*\{[^}]*display:\s*none\s*!important")
        m_card = re.search(r"\.walk-lightbox-card\s*\{([^}]*)\}", css)
        self.assertIsNotNone(m_card)
        self.assertIn("background: transparent", m_card.group(1))
        self.assertIn("box-shadow: none", m_card.group(1))
        m_title = re.search(r"#walkLightboxTitle\s*\{([^}]*)\}", css)
        self.assertIsNotNone(m_title)
        self.assertIn("white-space: nowrap", m_title.group(1))
        self.assertIn("text-overflow: ellipsis", m_title.group(1))
        self.assertIn("overflow: hidden", m_title.group(1))
        m_img = re.search(r"#walkLightboxImg\s*\{([^}]*)\}", css)
        self.assertIsNotNone(m_img)
        m_calc = re.search(r"max-height:\s*calc\(92vh\s*-\s*(\d+)px\)", m_img.group(1))
        self.assertIsNotNone(m_calc)
        self.assertGreaterEqual(int(m_calc.group(1)), 60)


if __name__ == "__main__":
    unittest.main()
