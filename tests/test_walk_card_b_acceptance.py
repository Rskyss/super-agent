"""P1 卡 B 验收测试（写施工单的角色写，施工方不许改）：卡片里显示走查截图与只读看图接口。

施工单：docs/走查截图/施工单-卡B.md。
- card_view 给出本次交活最后一次通过走查的 walk_shots（不含绝对路径，校验截图与设计图边界）；
- /api/clerk/card/image 按一次性口令鉴权，只放行本卡走查截图目录下的 png 与主项目/副本内的设计图 png；
- chat.js 在卡片原文下显示“走查截图”、支持并排对照、点击弹窗看大图、收起/展开且 3 秒刷新不重置。
"""
import json
import os
import struct
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

from acceptance_helpers import ROOT, needs_node
from collab.flow import record
from collab.judge import FAIL, PASS
from collab.state import Card
from collab.workspace import init_collab
from desktop.view import card_view
from test_acceptance_11_fixes import Case


def _make_png(width: int, height: int, rgba: tuple[int, int, int, int]) -> bytes:
    def _chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + ctype
            + data
            + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(rgba) * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b"")


PNG_A = _make_png(1, 1, (220, 40, 40, 255))
PNG_B = _make_png(2, 1, (40, 120, 220, 255))


class CardViewWalkShotsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "项目"
        self.root.mkdir()
        self.collab = init_collab(self.root)
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 界面卡\n", encoding="utf-8")
        Card("CARD-001", state="待验收").save(self.cdir / "状态.json")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "设计图.png").write_bytes(PNG_B)
        (Path(self.tmp.name) / "外面.png").write_bytes(PNG_A)

    def _put_shot(self, folder: str, name: str, data: bytes = PNG_A) -> str:
        rel = f"走查截图/{folder}/{name}"
        target = self.cdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return rel

    def test_returns_last_passing_walk_without_absolute_paths(self):
        record(self.cdir, "回复", src="开发员", dst="调度员", ok=True, summary="改好了")
        record(self.cdir, "判定", src="程序", result=PASS, round=1, summary="测试过")
        s1 = self._put_shot("第1次", "01-第一步.png")
        s2 = self._put_shot("第1次", "02-第二步.png")
        record(self.cdir, "页面检查", src="程序", result=PASS, walk=True, folder="走查截图/第1次",
               shots=[{"步骤": "第一步", "截图": s1, "设计图": "docs/设计图.png"},
                      {"步骤": "第二步", "截图": s2, "设计图": ""}],
               summary="走查过了，截了 2 张图")
        v = card_view(str(self.root), "CARD-001")
        self.assertEqual(v.get("walk_shots"), [
            {"step": "第一步", "shot": s1, "design": "docs/设计图.png"},
            {"step": "第二步", "shot": s2, "design": ""},
        ])
        dumped = json.dumps(v, ensure_ascii=False)
        self.assertNotIn(str(self.root), dumped)
        self.assertNotIn(str(self.root.resolve()), dumped)

    def test_pass_then_fail_or_after_revision_returns_empty(self):
        record(self.cdir, "回复", src="开发员", dst="调度员", ok=True, summary="改好了")
        s1 = self._put_shot("第1次", "01-图.png")
        record(self.cdir, "页面检查", src="程序", result=PASS, walk=True, folder="走查截图/第1次",
               shots=[{"步骤": "图", "截图": s1, "设计图": ""}], summary="走查过了，截了 1 张图")
        # 同一次交活又重跑走查却没过 → 不显示旧过图
        record(self.cdir, "页面检查", src="程序", result=FAIL, walk=True, folder="走查截图/第2次",
               shots=[], summary="走查没过：卡在“右栏”这一步")
        self.assertEqual(card_view(str(self.root), "CARD-001").get("walk_shots"), [])

        # 新一轮有走查过了，但之后用户点了“继续改”（追加要求）→ 也不显示旧图
        s3 = self._put_shot("第3次", "01-新图.png")
        record(self.cdir, "页面检查", src="程序", result=PASS, walk=True, folder="走查截图/第3次",
               shots=[{"步骤": "新图", "截图": s3, "设计图": ""}], summary="走查过了，截了 1 张图")
        self.assertEqual(len(card_view(str(self.root), "CARD-001").get("walk_shots")), 1)
        record(self.cdir, "追加要求", src="你", dst="调度员", summary="再改大一点", text="再改大一点")
        self.assertEqual(card_view(str(self.root), "CARD-001").get("walk_shots"), [])

    def test_filters_bad_shots_and_validates_design_in_project_or_worktree(self):
        wt = self.root.parent / f"{self.root.name}-worktrees" / "CARD-001"
        (wt / "docs").mkdir(parents=True)
        (wt / "docs" / "副本设计图.png").write_bytes(PNG_B)
        # 同名设计图同时在主项目与副本存在：待验收期间优先取副本版本
        (self.root / "docs" / "同名设计图.png").write_bytes(PNG_B)
        (wt / "docs" / "同名设计图.png").write_bytes(PNG_A)

        ok_shot = self._put_shot("第1次", "01-正常.png")
        ok_wt_shot = self._put_shot("第1次", "02-副本设计图.png")
        ok_same_shot = self._put_shot("第1次", "03-同名设计图.png")
        ok_bad_design_shot = self._put_shot("第1次", "04-设计图越界.png")
        sym_shot = self.cdir / "走查截图" / "第1次" / "05-外链.png"
        sym_shot.symlink_to(Path(self.tmp.name) / "外面.png")

        record(self.cdir, "回复", src="开发员", dst="调度员", ok=True, summary="改好了")
        record(self.cdir, "页面检查", src="程序", result=PASS, walk=True, folder="走查截图/第1次",
               shots=[
                   {"步骤": "正常", "截图": ok_shot, "设计图": "docs/设计图.png"},
                   {"步骤": "副本图", "截图": ok_wt_shot, "设计图": "docs/副本设计图.png"},
                   {"步骤": "同名图", "截图": ok_same_shot, "设计图": "docs/同名设计图.png"},
                   {"步骤": "越界设计", "截图": ok_bad_design_shot, "设计图": "../外面.png"},
                   {"步骤": "缺图", "截图": "走查截图/第1次/04-不存在.png", "设计图": ""},
                   {"步骤": "外链图", "截图": "走查截图/第1次/05-外链.png", "设计图": ""},
                   {"步骤": "越界截图", "截图": "../需求卡.md", "设计图": ""},
               ],
               summary="走查过了，截了 7 张图")

        got = card_view(str(self.root), "CARD-001").get("walk_shots")
        self.assertEqual(got, [
            {"step": "正常", "shot": ok_shot, "design": "docs/设计图.png"},
            {"step": "副本图", "shot": ok_wt_shot, "design": "docs/副本设计图.png"},
            {"step": "同名图", "shot": ok_same_shot, "design": "docs/同名设计图.png"},
            {"step": "越界设计", "shot": ok_bad_design_shot, "design": ""},
        ])

    def test_rejects_symlinked_shots_dir_and_case_or_non_png_design_bypasses(self):
        # 整目录软链接：走查截图目录本身是软链接指向外部目录 -> 全部拦截
        ext_dir = Path(self.tmp.name) / "外目录"
        (ext_dir / "第1次").mkdir(parents=True)
        (ext_dir / "第1次" / "01-外图.png").write_bytes(PNG_A)

        cdir2 = self.collab / "cards" / "CARD-002"
        cdir2.mkdir(parents=True)
        (cdir2 / "需求卡.md").write_text("# 外链目录卡\n", encoding="utf-8")
        Card("CARD-002", state="待验收").save(cdir2 / "状态.json")
        (cdir2 / "走查截图").symlink_to(ext_dir)
        record(cdir2, "回复", src="开发员", dst="调度员", ok=True, summary="改好了")
        record(cdir2, "页面检查", src="程序", result=PASS, walk=True, folder="走查截图/第1次",
               shots=[{"步骤": "外目录图", "截图": "走查截图/第1次/01-外图.png", "设计图": ""}],
               summary="走查过了，截了 1 张图")
        self.assertEqual(card_view(str(self.root), "CARD-002").get("walk_shots"), [])

        # 设计图大小写绕过 .COLLAB / .GIT、软链接指向 .collab / .git 内部 PNG、软链接指向非 png 文件 -> design 置空但保留截图
        ok_shot = self._put_shot("第1次", "01-正常.png")
        (self.root / ".git").mkdir(exist_ok=True)
        (self.root / ".git" / "机密.png").write_bytes(PNG_B)
        (self.root / "docs" / "笔记.txt").write_text("不是图片", encoding="utf-8")
        (self.root / "docs" / "指向协作.png").symlink_to(self.cdir / ok_shot)
        (self.root / "docs" / "指向git.png").symlink_to(self.root / ".git" / "机密.png")
        (self.root / "docs" / "假图.png").symlink_to(self.root / "docs" / "笔记.txt")
        s_case = self._put_shot("第1次", "02-大小写.png")
        s_collab = self._put_shot("第1次", "03-指协作.png")
        s_nonpng = self._put_shot("第1次", "04-假图.png")
        s_git = self._put_shot("第1次", "05-git.png")
        s_git_case = self._put_shot("第1次", "06-GIT.png")
        s_git_link = self._put_shot("第1次", "07-指git.png")
        record(self.cdir, "回复", src="开发员", dst="调度员", ok=True, summary="又改好")
        record(self.cdir, "页面检查", src="程序", result=PASS, walk=True, folder="走查截图/第1次",
               shots=[
                   {"步骤": "大小写绕过", "截图": s_case, "设计图": ".COLLAB/cards/CARD-001/走查截图/第1次/01-正常.png"},
                   {"步骤": "软链进协作", "截图": s_collab, "设计图": "docs/指向协作.png"},
                   {"步骤": "软链非图片", "截图": s_nonpng, "设计图": "docs/假图.png"},
                   {"步骤": "进git图片", "截图": s_git, "设计图": ".git/机密.png"},
                   {"步骤": "大写GIT图片", "截图": s_git_case, "设计图": ".GIT/机密.png"},
                   {"步骤": "软链进git", "截图": s_git_link, "设计图": "docs/指向git.png"},
               ],
               summary="走查过了，截了 6 张图")
        got = card_view(str(self.root), "CARD-001").get("walk_shots")
        self.assertEqual(got, [
            {"step": "大小写绕过", "shot": s_case, "design": ""},
            {"step": "软链进协作", "shot": s_collab, "design": ""},
            {"step": "软链非图片", "shot": s_nonpng, "design": ""},
            {"step": "进git图片", "shot": s_git, "design": ""},
            {"step": "大写GIT图片", "shot": s_git_case, "design": ""},
            {"step": "软链进git", "shot": s_git_link, "design": ""},
        ])

    def test_card_without_walk_or_not_installed_keeps_empty_list(self):
        plain = Path(self.tmp.name) / "未接入"
        plain.mkdir()
        self.assertEqual(card_view(str(plain)).get("walk_shots"), [])
        self.assertEqual(card_view(str(self.root), "CARD-001").get("walk_shots"), [])

    def test_card_without_walk_keeps_existing_view_fields_unchanged(self):
        # 守门：有流转但没走查的卡，原有字段和状态文案保持不变
        record(self.cdir, "需求", src="你", dst="调度员", summary="界面卡")
        v = card_view(str(self.root), "CARD-001")
        self.assertEqual(v["card"]["id"], "CARD-001")
        self.assertEqual(v["card"]["state"], "待验收")
        self.assertEqual(len(v["stations"]), 7)
        self.assertEqual(v["now"]["text"], "做完了，等你确认")


class CardImageEndpointTest(Case):
    def setUp(self):
        super().setUp()
        self.cdir = self.make_card(state="待验收")
        self.cdir2 = self.make_card(card_id="CARD-002", state="待验收")
        self.cdir3 = self.make_card(card_id="CARD-003", state="待验收")
        self.wt = self.make_copy("CARD-001")
        (Path(self.project) / "docs").mkdir(exist_ok=True)
        (Path(self.project) / "docs" / "设计图.png").write_bytes(PNG_B)
        (Path(self.project) / "docs" / "同名设计图.png").write_bytes(PNG_B)
        (Path(self.project) / "docs" / "笔记.txt").write_text("不是图片", encoding="utf-8")
        (Path(self.project) / ".git" / "机密.png").write_bytes(PNG_B)
        (self.wt / "docs").mkdir(exist_ok=True)
        (self.wt / "docs" / "仅副本设计图.png").write_bytes(PNG_A)
        (self.wt / "docs" / "同名设计图.png").write_bytes(PNG_A)

        self.shot_rel = "走查截图/第1次/01-右栏.png"
        shot_file = self.cdir / self.shot_rel
        shot_file.parent.mkdir(parents=True)
        shot_file.write_bytes(PNG_A)

        shot2_file = self.cdir2 / "走查截图" / "第1次" / "01-别卡.png"
        shot2_file.parent.mkdir(parents=True)
        shot2_file.write_bytes(PNG_B)

        ext_dir = Path(self.tmp) / "外目录"
        (ext_dir / "第1次").mkdir(parents=True)
        (ext_dir / "第1次" / "01-图.png").write_bytes(PNG_A)
        (self.cdir3 / "走查截图").symlink_to(ext_dir)

        (Path(self.tmp) / "外面.png").write_bytes(PNG_B)
        (self.cdir / "走查截图" / "第1次" / "坏链接.png").symlink_to(Path(self.tmp) / "外面.png")
        (self.wt / "docs" / "越界设计.png").symlink_to(Path(self.tmp) / "外面.png")
        (Path(self.project) / "docs" / "指向协作.png").symlink_to(shot_file)
        (Path(self.project) / "docs" / "指向git.png").symlink_to(Path(self.project) / ".git" / "机密.png")
        (Path(self.project) / "docs" / "假图.png").symlink_to(Path(self.project) / "docs" / "笔记.txt")
        (self.cdir / "走查截图" / "第1次" / "不是图.txt").write_text("txt", encoding="utf-8")

        self.plain_dir = Path(self.tmp) / "普通目录"
        (self.plain_dir / "docs").mkdir(parents=True)
        (self.plain_dir / "docs" / "设计图.png").write_bytes(PNG_B)

    def _raw_get(self, endpoint="/api/clerk/card/image", header_token="口令", **query):
        qs = urllib.parse.urlencode(query)
        req = urllib.request.Request(self.base + endpoint + ("?" + qs if qs else ""))
        if header_token is not None:
            req.add_header("X-Chat-Token", urllib.parse.quote(header_token))
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, resp.headers.get("Content-Type", ""), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read()

    def test_serves_card_shot_and_project_or_worktree_design(self):
        st, ctype, body = self._raw_get(project=self.project, card="CARD-001", kind="shot", path=self.shot_rel)
        self.assertEqual((st, ctype, body), (200, "image/png", PNG_A))

        st2, ctype2, body2 = self._raw_get(project=self.project, card="CARD-001", kind="design", path="docs/设计图.png")
        self.assertEqual((st2, ctype2, body2), (200, "image/png", PNG_B))

        st3, ctype3, body3 = self._raw_get(project=self.project, card="CARD-001", kind="design", path="docs/仅副本设计图.png")
        self.assertEqual((st3, ctype3, body3), (200, "image/png", PNG_A))

        # 同名设计图：待验收副本存在时优先读副本（PNG_A）；删掉副本文件后自动回退主项目（PNG_B）
        st_wt, ctype_wt, body_wt = self._raw_get(project=self.project, card="CARD-001", kind="design", path="docs/同名设计图.png")
        self.assertEqual((st_wt, ctype_wt, body_wt), (200, "image/png", PNG_A))
        (self.wt / "docs" / "同名设计图.png").unlink()
        st_main, ctype_main, body_main = self._raw_get(project=self.project, card="CARD-001", kind="design", path="docs/同名设计图.png")
        self.assertEqual((st_main, ctype_main, body_main), (200, "image/png", PNG_B))

        # 也允许通过 URL query token 鉴权（<img> 标签浏览器请求就是这样带口令）
        st4, ctype4, body4 = self._raw_get(header_token=None, project=self.project, card="CARD-001", kind="shot",
                                           path=self.shot_rel, token="口令")
        self.assertEqual((st4, ctype4, body4), (200, "image/png", PNG_A))

    def test_rejects_bad_token_traversal_symlinks_and_non_png(self):
        st_no, _, _ = self._raw_get(header_token=None, project=self.project, card="CARD-001", kind="shot", path=self.shot_rel)
        self.assertEqual(st_no, 403)
        st_bad, _, _ = self._raw_get(header_token="错口令", project=self.project, card="CARD-001", kind="shot", path=self.shot_rel)
        self.assertEqual(st_bad, 403)

        bad_400_cases = (
            dict(project="", card="CARD-001", kind="shot", path=self.shot_rel),
            dict(project=str(Path(self.tmp) / "不存在"), card="CARD-001", kind="shot", path=self.shot_rel),
            dict(project=self.project, card="../CARD-001", kind="shot", path=self.shot_rel),
            dict(project=self.project, card="CARD-001", kind="other", path=self.shot_rel),
            dict(project=self.project, card="CARD-001", kind="shot", path=""),
        )
        for params in bad_400_cases:
            with self.subTest(bad_400=params):
                st, _, body = self._raw_get(**params)
                self.assertEqual(st, 400, (params, body))
                err = json.loads(body.decode("utf-8")).get("error")
                self.assertTrue(bool(err), (params, body))

        bad_404_cases = (
            dict(project=self.project, card="CARD-001", kind="shot", path="../状态.json"),
            dict(project=self.project, card="CARD-001", kind="shot", path="../CARD-002/走查截图/第1次/01-别卡.png"),
            dict(project=self.project, card="CARD-001", kind="shot", path="走查截图\\第1次\\01-右栏.png"),
            dict(project=self.project, card="CARD-001", kind="shot", path="走查截图/../../外面.png"),
            dict(project=self.project, card="CARD-001", kind="shot", path="走查截图/第1次/坏链接.png"),
            dict(project=self.project, card="CARD-003", kind="shot", path="走查截图/第1次/01-图.png"),
            dict(project=self.project, card="CARD-001", kind="shot", path="走查截图/第1次/不是图.txt"),
            dict(project=self.project, card="CARD-001", kind="shot", path=str((self.cdir / self.shot_rel).resolve())),
            dict(project=self.project, card="CARD-001", kind="shot", path="需求卡.md"),
            dict(project=self.project, card="CARD-001", kind="design", path="../外面.png"),
            dict(project=self.project, card="CARD-001", kind="design", path="docs/越界设计.png"),
            dict(project=self.project, card="CARD-001", kind="design", path="docs/指向协作.png"),
            dict(project=self.project, card="CARD-001", kind="design", path="docs/指向git.png"),
            dict(project=self.project, card="CARD-001", kind="design", path="docs/假图.png"),
            dict(project=self.project, card="CARD-001", kind="design", path="docs/笔记.txt"),
            dict(project=self.project, card="CARD-001", kind="design", path=".collab/cards/CARD-001/走查截图/第1次/01-右栏.png"),
            dict(project=self.project, card="CARD-001", kind="design", path=".COLLAB/cards/CARD-001/走查截图/第1次/01-右栏.png"),
            dict(project=self.project, card="CARD-001", kind="design", path=".git/HEAD"),
            dict(project=self.project, card="CARD-001", kind="design", path=".git/机密.png"),
            dict(project=self.project, card="CARD-001", kind="design", path=".GIT/机密.png"),
            dict(project=self.project, card="CARD-001", kind="design", path=str((Path(self.project) / "docs" / "设计图.png").resolve())),
            dict(project=self.project, card="CARD-999", kind="design", path="docs/设计图.png"),
            dict(project=str(self.plain_dir), card="CARD-001", kind="design", path="docs/设计图.png"),
        )
        for params in bad_404_cases:
            with self.subTest(bad_404=params):
                st, _, body = self._raw_get(**params)
                self.assertEqual(st, 404, (params, body))
                self.assertEqual(json.loads(body.decode("utf-8")), {"error": "没有这张图"}, params)

    def test_image_endpoint_is_strictly_readonly_even_for_missing_blocks_home(self):
        from unittest import mock
        from desktop.clerk import blocks_tools

        missing_home = Path(self.tmp) / "不存在积木库"
        self.assertFalse(missing_home.exists())
        with mock.patch.object(blocks_tools.blocks, "home", return_value=missing_home):
            st, _, body = self._raw_get(project=str(missing_home), card="CARD-001", kind="shot", path=self.shot_rel)
        self.assertEqual(st, 400, body)
        self.assertTrue(bool(json.loads(body.decode("utf-8")).get("error")), body)
        self.assertFalse(missing_home.exists(), "只读看图接口不能创建积木库目录")


class CardWalkDomTest(unittest.TestCase):
    @needs_node()
    def test_chat_page_displays_side_by_side_lightbox_and_fold(self):
        proc = subprocess.run(
            ["node", str(Path(__file__).resolve().parent / "walk_card_b_dom.js")],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
