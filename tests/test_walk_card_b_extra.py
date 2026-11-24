"""卡 B 补充自动化测试（测试员编写，对应施工单 docs/走查截图/施工单-卡B.md）：

补齐验收测试未覆盖的关键边界与安全/交互场景：
1. P1 resolve_walk_image 共用路径解析边界：项目与卡片绑定（软链接目录、缺状态文件、卡号格式）、
   相对路径初筛（`.` / `..` / 反斜杠 / 绝对路径 / `.PNG` 大小写 / 目录伪装）、
   `kind="shot"`（顶层图、子目录软链接、`.png` 软链指向非图片）、
   `kind="design"`（副本优先与回退、软链接副本目录拦截、副本内 `.git`/`.collab`/非 png 拦截、未 resolve 项目别名）；
2. P1 card_view walk_shots 挑选规则与全分支默认字段：
   同交活多次走查取最后一条（先失败后通过、两次均通过）、新开发员交活清空旧走查、
   非开发员回复（测试员/复查员）不清空走查、老页面检查（walk 非 True）不干扰、
   无开发员回复记录时也能读取、脏数据条目跳过、已合并卡删副本后回退主项目、
   以及 card_view 全部提前返回分支（未接入/走 _chat_view/无卡/无流转）都带 `walk_shots: []`；
3. P2 `/api/clerk/card/image` 只读看图接口补充：
   400 参数校验全组合、非法卡号格式归 404、URL 编码中文口令、Cache-Control 响应头、
   目录伪装 `.png` 不 500、大写 `.PNG` 放行、软链接副本与 `.` 路径拦截、严格只读不写文件、
   不破坏原有 `/api/chat/image` 路由；
4. P3 前端实跑补充（调用 tests/walk_card_b_extra_dom.js）与关键布局/样式防回退守门。
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from acceptance_helpers import ROOT, needs_node
from collab.flow import record
from collab.judge import FAIL, PASS
from collab.state import Card
from collab.workspace import init_collab
from desktop.view import card_view
from test_acceptance_11_fixes import Case
from test_walk_card_b_acceptance import PNG_A, PNG_B


class _FakeChatSession:
    """贴近真实 ChatSession.live_summary 结构的最小替身，用来覆盖 _chat_view 分支。"""

    def live_summary(self, now):
        return {
            "title": "正在对齐需求",
            "user_detail": "想看走查截图",
            "step_text": "正在读项目文件",
            "asking": False,
            "busy": True,
            "elapsed": 5,
            "quiet": 1,
            "updated": now.timestamp(),
            "handoffs": [],
        }


class ResolveWalkImageExtraTest(unittest.TestCase):
    """P1 共用路径解析函数 resolve_walk_image 的安全与边界测试。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "项目"
        self.root.mkdir()
        self.collab = init_collab(self.root)
        self.cdir = self.collab / "cards" / "CARD-001"
        self.cdir.mkdir(parents=True)
        Card("CARD-001", state="待验收").save(self.cdir / "状态.json")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "设计图.png").write_bytes(PNG_B)
        (self.root / "docs" / "大写图.PNG").write_bytes(PNG_B)
        (self.base / "外部图.png").write_bytes(PNG_A)

    def _put_shot(self, rel: str, data: bytes = PNG_A) -> Path:
        p = self.cdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def test_project_and_card_binding_checks(self):
        from desktop.view import resolve_walk_image

        shot_rel = "走查截图/第1次/01-主图.png"
        self._put_shot(shot_rel)

        # 空项目、不存在目录、指向普通文件 -> None
        plain_file = self.base / "普通文件"
        plain_file.write_text("x", encoding="utf-8")
        self.assertIsNone(resolve_walk_image("", "CARD-001", "shot", shot_rel))
        self.assertIsNone(resolve_walk_image(str(self.base / "不存在"), "CARD-001", "shot", shot_rel))
        self.assertIsNone(resolve_walk_image(str(plain_file), "CARD-001", "shot", shot_rel))

        # 非法卡片编号格式 -> None；超过 3 位数字（如 CARD-1000）应放行
        for bad_id in ("", "CARD-1", "CARD-01", "card-001", "CARD-001a", "../CARD-001", "调度中"):
            with self.subTest(bad_id=bad_id):
                self.assertIsNone(resolve_walk_image(str(self.root), bad_id, "design", "docs/设计图.png"))

        c1000 = self.collab / "cards" / "CARD-1000"
        c1000.mkdir(parents=True)
        Card("CARD-1000", state="待验收").save(c1000 / "状态.json")
        self.assertEqual(
            resolve_walk_image(str(self.root), "CARD-1000", "design", "docs/设计图.png"),
            (self.root / "docs" / "设计图.png").resolve(),
        )

        # 缺少 状态.json 或 状态.json 是目录 -> None
        c_no_state = self.collab / "cards" / "CARD-002"
        (c_no_state / "走查截图").mkdir(parents=True)
        (c_no_state / "走查截图" / "a.png").write_bytes(PNG_A)
        self.assertIsNone(resolve_walk_image(str(self.root), "CARD-002", "shot", "走查截图/a.png"))
        (c_no_state / "状态.json").mkdir()
        self.assertIsNone(resolve_walk_image(str(self.root), "CARD-002", "shot", "走查截图/a.png"))

        # .collab / cards / card 目录自身为软链接 -> 一律拒绝
        real_card = self.base / "真实卡"
        (real_card / "走查截图").mkdir(parents=True)
        (real_card / "走查截图" / "a.png").write_bytes(PNG_A)
        Card("CARD-003", state="待验收").save(real_card / "状态.json")
        (self.collab / "cards" / "CARD-003").symlink_to(real_card)
        self.assertIsNone(resolve_walk_image(str(self.root), "CARD-003", "shot", "走查截图/a.png"))

        # .collab 自身是软链接
        proj_sym_collab = self.base / "软链协作项目"
        proj_sym_collab.mkdir()
        (proj_sym_collab / ".collab").symlink_to(self.collab)
        self.assertIsNone(resolve_walk_image(str(proj_sym_collab), "CARD-001", "shot", shot_rel))

        # .collab/cards 自身是软链接
        proj_sym_cards = self.base / "软链卡片目录项目"
        (proj_sym_cards / ".collab").mkdir(parents=True)
        (proj_sym_cards / ".collab" / "cards").symlink_to(self.collab / "cards")
        self.assertIsNone(resolve_walk_image(str(proj_sym_cards), "CARD-001", "shot", shot_rel))

    def test_relative_path_filtering_and_shot_boundaries(self):
        from desktop.view import resolve_walk_image

        top_shot = self._put_shot("走查截图/顶层图.png")
        upper_shot = self._put_shot("走查截图/第1次/大写.PNG")
        (self.cdir / "走查截图" / "目录.png").mkdir(parents=True)
        (self.cdir / "需求卡.md").write_text("# 卡", encoding="utf-8")
        (self.cdir / "走查截图" / "第1次" / "链向文本.png").symlink_to(self.cdir / "需求卡.md")

        ext_sub = self.base / "外部子目录"
        ext_sub.mkdir()
        (ext_sub / "外图.png").write_bytes(PNG_A)
        (self.cdir / "走查截图" / "外链子目录").symlink_to(ext_sub)

        # 顶层图与大写 .PNG 应放行
        self.assertEqual(resolve_walk_image(str(self.root), "CARD-001", "shot", "走查截图/顶层图.png"), top_shot.resolve())
        self.assertEqual(resolve_walk_image(str(self.root), "CARD-001", "shot", "走查截图/第1次/大写.PNG"), upper_shot.resolve())

        # 通过软链接别名传入 project 也能正常解析
        proj_alias = self.base / "项目别名"
        proj_alias.symlink_to(self.root)
        self.assertEqual(resolve_walk_image(str(proj_alias), "CARD-001", "shot", "走查截图/顶层图.png"), top_shot.resolve())

        # 各种非法路径、非法 kind、目录伪装、软链接绕过 -> 一律 None
        bad_cases = (
            ("shot", ""),
            ("shot", "   "),
            ("shot", None),
            ("shot", "走查截图"),
            ("shot", "/走查截图/顶层图.png"),
            ("shot", "走查截图/./顶层图.png"),
            ("shot", "走查截图/第1次/../顶层图.png"),
            ("shot", "走查截图\\顶层图.png"),
            ("shot", "走查截图/目录.png"),
            ("shot", "走查截图/第1次/链向文本.png"),
            ("shot", "走查截图/外链子目录/外图.png"),
            ("SHOT", "走查截图/顶层图.png"),
            ("other", "走查截图/顶层图.png"),
            ("", "走查截图/顶层图.png"),
            ("design", "./docs/设计图.png"),
            ("design", "docs\\设计图.png"),
            ("design", "/docs/设计图.png"),
            ("DESIGN", "docs/设计图.png"),
        )
        for kind, rel in bad_cases:
            with self.subTest(kind=kind, rel=rel):
                self.assertIsNone(resolve_walk_image(str(self.root), "CARD-001", kind, rel))

    def test_design_worktree_priority_and_symlink_defenses(self):
        from desktop.view import resolve_walk_image

        # 大小写 .PNG 设计图应放行，目录伪装 .png 应拒绝
        self.assertEqual(
            resolve_walk_image(str(self.root), "CARD-001", "design", "docs/大写图.PNG"),
            (self.root / "docs" / "大写图.PNG").resolve(),
        )
        (self.root / "docs" / "假目录.png").mkdir()
        self.assertIsNone(resolve_walk_image(str(self.root), "CARD-001", "design", "docs/假目录.png"))

        # 真实副本目录优先于主项目；副本里指向 .git/.collab/非 png 的软链全部拦截
        wt_base = self.root.parent / f"{self.root.name}-worktrees"
        wt = wt_base / "CARD-001"
        (wt / "docs").mkdir(parents=True)
        (wt / ".git").mkdir()
        (wt / ".git" / "秘.png").write_bytes(PNG_A)
        (wt / "docs" / "设计图.png").write_bytes(PNG_A)
        (wt / "docs" / "文本.txt").write_text("hi", encoding="utf-8")
        (wt / "docs" / "链git.png").symlink_to(wt / ".git" / "秘.png")
        (wt / "docs" / "链非图.png").symlink_to(wt / "docs" / "文本.txt")
        self.assertEqual(
            resolve_walk_image(str(self.root), "CARD-001", "design", "docs/设计图.png"),
            (wt / "docs" / "设计图.png").resolve(),
        )
        self.assertIsNone(resolve_walk_image(str(self.root), "CARD-001", "design", "docs/链git.png"))
        self.assertIsNone(resolve_walk_image(str(self.root), "CARD-001", "design", "docs/链非图.png"))

        # 副本的卡片目录 wt_dir 自身是软链接 -> 绝不能从该软链接副本读图，只能安全回退主项目
        ext_wt = self.base / "假副本"
        (ext_wt / "docs").mkdir(parents=True)
        (ext_wt / "docs" / "仅假副本.png").write_bytes(PNG_A)
        (ext_wt / "docs" / "设计图.png").write_bytes(PNG_A)
        shutil.rmtree(wt)
        wt.symlink_to(ext_wt)
        self.assertIsNone(resolve_walk_image(str(self.root), "CARD-001", "design", "docs/仅假副本.png"))
        self.assertEqual(
            resolve_walk_image(str(self.root), "CARD-001", "design", "docs/设计图.png"),
            (self.root / "docs" / "设计图.png").resolve(),
        )

        # worktrees 根目录 wt_base 自身是软链接 -> 同样绝不放行软链接副本，回退主项目
        ext_wt_base = self.base / "假副本总目录"
        (ext_wt_base / "CARD-001" / "docs").mkdir(parents=True)
        (ext_wt_base / "CARD-001" / "docs" / "仅假总目录.png").write_bytes(PNG_A)
        shutil.rmtree(wt_base)
        wt_base.symlink_to(ext_wt_base)
        self.assertIsNone(resolve_walk_image(str(self.root), "CARD-001", "design", "docs/仅假总目录.png"))
        self.assertEqual(
            resolve_walk_image(str(self.root), "CARD-001", "design", "docs/设计图.png"),
            (self.root / "docs" / "设计图.png").resolve(),
        )


class CardViewWalkSelectionExtraTest(unittest.TestCase):
    """P1 card_view 走查记录挑选规则、脏数据容错与全返回分支默认字段测试。"""

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

    def _put_shot(self, folder: str, name: str) -> str:
        rel = f"走查截图/{folder}/{name}"
        p = self.cdir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(PNG_A)
        return rel

    def test_picks_last_passing_walk_and_respects_boundaries(self):
        # 1. 无开发员回复记录（since=0）时，直接有通过的走查也能显示
        s0 = self._put_shot("第0次", "01-初始.png")
        record(self.cdir, "页面检查", src="程序", result=PASS, walk=True,
               shots=[{"步骤": "初始", "截图": s0, "设计图": ""}], summary="走查过了")
        self.assertEqual(card_view(str(self.root), "CARD-001").get("walk_shots"), [
            {"step": "初始", "shot": s0, "design": ""},
        ])

        # 2. 开发员新一轮交活后还没走查 -> 旧走查必须清空
        record(self.cdir, "回复", src="开发员", dst="调度员", ok=True, summary="第1次交活")
        self.assertEqual(card_view(str(self.root), "CARD-001").get("walk_shots"), [])

        # 3. 同一次交活内：先 FAIL 后 PASS，再一次 PASS -> 只取最后一条 PASS 的截图
        s1 = self._put_shot("第1次", "01-旧过.png")
        s2 = self._put_shot("第2次", "01-最新.png")
        record(self.cdir, "页面检查", src="程序", result=FAIL, walk=True, shots=[], summary="走查没过")
        record(self.cdir, "页面检查", src="程序", result=PASS, walk=True,
               shots=[{"步骤": "旧过", "截图": s1, "设计图": ""}], summary="第1次过")
        record(self.cdir, "页面检查", src="程序", result=PASS, walk=True,
               shots=[{"步骤": "最新", "截图": s2, "设计图": "docs/设计图.png"}], summary="第2次过")
        self.assertEqual(card_view(str(self.root), "CARD-001").get("walk_shots"), [
            {"step": "最新", "shot": s2, "design": "docs/设计图.png"},
        ])

        # 4. 非开发员的回复（复查员/测试员）与非 walk 的老页面检查（即便失败）都不影响本次走查截图
        record(self.cdir, "页面检查", src="程序", result=FAIL, summary="老页面检查没过")
        record(self.cdir, "回复", src="复查员", dst="调度员", ok=True, summary="结论：通过")
        record(self.cdir, "回复", src="测试员", dst="调度员", ok=True, summary="补充说明")
        self.assertEqual(card_view(str(self.root), "CARD-001").get("walk_shots"), [
            {"step": "最新", "shot": s2, "design": "docs/设计图.png"},
        ])

        # 5. 开发员再次交活后，只有非 walk 的老页面检查 PASS（带 shots）-> 必须返回空
        record(self.cdir, "回复", src="开发员", dst="调度员", ok=True, summary="第2次交活")
        record(self.cdir, "页面检查", src="程序", result=PASS, walk=False,
               shots=[{"步骤": "老检查", "截图": s2, "设计图": ""}], summary="老检查过")
        self.assertEqual(card_view(str(self.root), "CARD-001").get("walk_shots"), [])

    def test_sanitizes_malformed_shots_and_outputs_fixed_three_keys(self):
        s_ok = self._put_shot("第1次", "01-好图.png")
        record(self.cdir, "回复", src="开发员", dst="调度员", ok=True, summary="改好了")
        record(self.cdir, "页面检查", src="程序", result=PASS, walk=True,
               shots=[
                   None,
                   "不是字典",
                   123,
                   {"步骤": "   ", "截图": s_ok, "设计图": ""},
                   {"步骤": "没截图", "截图": "", "设计图": "docs/设计图.png"},
                   {"步骤": "  有空白  ", "截图": f"  {s_ok}  ", "设计图": "  docs/设计图.png  ", "额外字段": "不该吐出"},
               ],
               summary="走查过了")
        got = card_view(str(self.root), "CARD-001").get("walk_shots")
        self.assertEqual(got, [{"step": "有空白", "shot": s_ok, "design": "docs/设计图.png"}])
        self.assertEqual(set(got[0].keys()), {"step", "shot", "design"})

        # shots 不是列表（比如字符串或字典）时返回 []，不抛异常
        record(self.cdir, "页面检查", src="程序", result=PASS, walk=True, shots="异常值", summary="走查过了")
        self.assertEqual(card_view(str(self.root), "CARD-001").get("walk_shots"), [])

    def test_all_card_view_branches_always_include_walk_shots_key(self):
        chat = _FakeChatSession()

        # 1. 未接入项目（无 chat 与有 chat 走 _chat_view）
        plain = Path(self.tmp.name) / "未接入项目"
        plain.mkdir()
        v_plain = card_view(str(plain))
        self.assertIn("walk_shots", v_plain)
        self.assertEqual(v_plain["walk_shots"], [])

        v_plain_chat = card_view(str(plain), chat_session=chat)
        self.assertIn("walk_shots", v_plain_chat)
        self.assertEqual(v_plain_chat["walk_shots"], [])

        # 2. 已接入但项目里还没有任何卡片（无 chat 与有 chat）
        empty_proj = Path(self.tmp.name) / "空已接入项目"
        empty_proj.mkdir()
        init_collab(empty_proj)
        v_empty = card_view(str(empty_proj))
        self.assertIn("walk_shots", v_empty)
        self.assertEqual(v_empty["walk_shots"], [])
        v_empty_chat = card_view(str(empty_proj), chat_session=chat)
        self.assertIn("walk_shots", v_empty_chat)
        self.assertEqual(v_empty_chat["walk_shots"], [])

        # 3. 选中了一张没有流转记录的卡
        v_no_events = card_view(str(self.root), "CARD-001")
        self.assertIn("walk_shots", v_no_events)
        self.assertEqual(v_no_events["walk_shots"], [])

        # 4. 已合并的卡（无副本目录）回退主项目设计图，仍能显示 walk_shots；
        #    不指定 card_id 且主对话正忙时走 _chat_view 分支，也带 walk_shots=[]
        s1 = self._put_shot("第1次", "01-合并后.png")
        record(self.cdir, "回复", src="开发员", dst="调度员", ok=True, summary="改好")
        record(self.cdir, "页面检查", src="程序", result=PASS, walk=True,
               shots=[{"步骤": "合并后仍可看", "截图": s1, "设计图": "docs/设计图.png"}], summary="走查过了")
        Card("CARD-001", state="已合并").save(self.cdir / "状态.json")
        v_merged = card_view(str(self.root), "CARD-001")
        self.assertEqual(v_merged["walk_shots"], [
            {"step": "合并后仍可看", "shot": s1, "design": "docs/设计图.png"},
        ])
        v_merged_chat = card_view(str(self.root), None, chat_session=chat)
        self.assertIn("walk_shots", v_merged_chat)
        self.assertEqual(v_merged_chat["walk_shots"], [])


class CardImageEndpointExtraTest(Case):
    """P2 `/api/clerk/card/image` 只读看图接口补充测试。"""

    def setUp(self):
        super().setUp()
        self.cdir = self.make_card(state="待验收")
        self.wt = self.make_copy("CARD-001")
        (Path(self.project) / "docs").mkdir(exist_ok=True)
        (Path(self.project) / "docs" / "设计图.png").write_bytes(PNG_B)
        (Path(self.project) / "docs" / "大写.PNG").write_bytes(PNG_B)
        (Path(self.project) / "docs" / "假目录.png").mkdir(exist_ok=True)
        self.shot_rel = "走查截图/第1次/01-主图.png"
        shot_file = self.cdir / self.shot_rel
        shot_file.parent.mkdir(parents=True)
        shot_file.write_bytes(PNG_A)
        (self.cdir / "走查截图" / "第1次" / "大写.PNG").write_bytes(PNG_A)
        (self.cdir / "走查截图" / "第1次" / "链文本.png").symlink_to(self.cdir / "状态.json")
        (self.cdir / "走查截图" / "目录.png").mkdir()

    def _raw_get(self, endpoint="/api/clerk/card/image", header_token="口令", **query):
        qs = urllib.parse.urlencode(query)
        req = urllib.request.Request(self.base + endpoint + ("?" + qs if qs else ""))
        if header_token is not None:
            req.add_header("X-Chat-Token", urllib.parse.quote(header_token))
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, dict(resp.headers.items()), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers.items()), exc.read()

    def test_headers_uppercase_png_and_encoded_query_token(self):
        # 正常返回应带 image/png 与 Cache-Control: no-store
        st, headers, body = self._raw_get(project=self.project, card="CARD-001", kind="shot", path=self.shot_rel)
        self.assertEqual(st, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(body, PNG_A)

        # 大写 .PNG 截图与设计图都应正常放行
        st_up1, h_up1, b_up1 = self._raw_get(project=self.project, card="CARD-001", kind="shot", path="走查截图/第1次/大写.PNG")
        self.assertEqual((st_up1, h_up1.get("Content-Type"), b_up1), (200, "image/png", PNG_A))
        st_up2, h_up2, b_up2 = self._raw_get(project=self.project, card="CARD-001", kind="design", path="docs/大写.PNG")
        self.assertEqual((st_up2, h_up2.get("Content-Type"), b_up2), (200, "image/png", PNG_B))

        # URL 编码的中文 query token 也能正常鉴权
        st_q, _, b_q = self._raw_get(
            header_token=None,
            project=self.project,
            card="CARD-001",
            kind="design",
            path="docs/设计图.png",
            token="口令",
        )
        self.assertEqual((st_q, b_q), (200, PNG_B))

    def test_400_and_404_edge_cases_and_no_side_effects(self):
        not_dir = Path(self.tmp) / "不是目录.txt"
        not_dir.write_text("x", encoding="utf-8")

        # 补全 400 参数校验（缺卡号、缺 kind、纯空白 path、project 是文件而非目录）
        bad_400 = (
            dict(project=self.project, card="", kind="shot", path=self.shot_rel),
            dict(project=self.project, kind="shot", path=self.shot_rel),
            dict(project=self.project, card="CARD-001", kind="", path=self.shot_rel),
            dict(project=self.project, card="CARD-001", path=self.shot_rel),
            dict(project=self.project, card="CARD-001", kind="shot", path="   "),
            dict(project=str(not_dir), card="CARD-001", kind="shot", path=self.shot_rel),
        )
        for params in bad_400:
            with self.subTest(bad_400=params):
                st, _, body = self._raw_get(**params)
                self.assertEqual(st, 400, (params, body))
                self.assertTrue(bool(json.loads(body.decode("utf-8")).get("error")), (params, body))

        # 补全 404 安全边界（不返回 500，统一 404 {"error": "没有这张图"}）
        bad_404 = (
            dict(project=self.project, card="CARD-1", kind="design", path="docs/设计图.png"),
            dict(project=self.project, card="FOO_BAR", kind="design", path="docs/设计图.png"),
            dict(project=self.project, card="CARD-001", kind="shot", path="走查截图/./第1次/01-主图.png"),
            dict(project=self.project, card="CARD-001", kind="shot", path="走查截图/目录.png"),
            dict(project=self.project, card="CARD-001", kind="shot", path="走查截图/第1次/链文本.png"),
            dict(project=self.project, card="CARD-001", kind="design", path="./docs/设计图.png"),
            dict(project=self.project, card="CARD-001", kind="design", path="docs/假目录.png"),
        )
        for params in bad_404:
            with self.subTest(bad_404=params):
                st, _, body = self._raw_get(**params)
                self.assertEqual(st, 404, (params, body))
                self.assertEqual(json.loads(body.decode("utf-8")), {"error": "没有这张图"}, params)

        # 未接入目录绝不会因调用看图接口而被建出 .collab
        plain_dir = Path(self.tmp) / "纯只读目录"
        (plain_dir / "docs").mkdir(parents=True)
        (plain_dir / "docs" / "设计图.png").write_bytes(PNG_B)
        st_plain, _, body_plain = self._raw_get(project=str(plain_dir), card="CARD-001", kind="design", path="docs/设计图.png")
        self.assertEqual(st_plain, 404, body_plain)
        self.assertFalse((plain_dir / ".collab").exists(), "看图接口严格只读，不能在未接入目录创建 .collab")

        # 守门：原有 /api/chat/image 路由仍然照常工作（无聊天会话时返回 404）
        st_chat, _, body_chat = self._raw_get(
            endpoint="/api/chat/image",
            project=self.project,
            path=str(Path(self.project) / "docs" / "设计图.png"),
        )
        self.assertEqual(st_chat, 404, body_chat)
        self.assertEqual(json.loads(body_chat.decode("utf-8")), {"error": "没有这张图"})


class CardWalkDomAndCssExtraTest(unittest.TestCase):
    """P3 前端实跑补充测试与关键样式/不动文件守门。"""

    @needs_node()
    def test_chat_page_extra_interactions_and_compatibility(self):
        proc = subprocess.run(
            ["node", str(Path(__file__).resolve().parent / "walk_card_b_extra_dom.js")],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("OK", proc.stdout)

    def test_css_scoping_and_chat_html_untouched(self):
        # Node 假 DOM 不计算真实 CSS 布局规则，因此对施工单要求的样式作用域做最小结构检查
        static = ROOT / "desktop" / "static"
        html = (static / "chat.html").read_text(encoding="utf-8")
        css = (static / "chat.css").read_text(encoding="utf-8")

        # 明确不改 chat.html：#cardWalk 和 #walkLightbox 应由 chat.js 动态创建，不应写进 chat.html
        self.assertNotIn("cardWalk", html, "不应修改 chat.html，走查截图区应由 chat.js 动态创建")
        self.assertNotIn("walkLightbox", html, "不应修改 chat.html，大图浮层应由 chat.js 动态创建")

        # 通用 .chat-head 不能加全局 max-height，必须仅限 .chat-head.has-walk
        m_head = re.search(r"(?<![\w-])\.chat-head\s*\{([^}]*)\}", css)
        self.assertIsNotNone(m_head, "chat.css 中缺少 .chat-head 规则")
        self.assertNotIn("max-height", m_head.group(1), "不能给通用 .chat-head 加全局 max-height")

        m_has_walk = re.search(r"\.chat-head\.has-walk\s*\{([^}]*)\}", css)
        self.assertIsNotNone(m_has_walk, "缺少 .chat-head.has-walk 限高规则")
        self.assertIn("max-height", m_has_walk.group(1))
        self.assertIn("overflow-y: auto", m_has_walk.group(1))

        # 隐藏态与大图浮层隐藏态必须确保真正藏住
        self.assertRegex(css, r"#cardWalk\[hidden\][^{]*\{[^}]*display:\s*none\s*!important")
        self.assertRegex(css, r"#walkLightbox\[hidden\][^{]*\{[^}]*display:\s*none\s*!important")


if __name__ == "__main__":
    unittest.main()
