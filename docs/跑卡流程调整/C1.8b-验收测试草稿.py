"""C1.8b 的验收测试草稿（从 C1.8 第 2、3 轮测试里挪出来，2026-11-18）。
C1.8a 只做主体，这 8 项测的是“没有正式复查结论时回头读复查员原话”那块老读法会出的错；
C1.8b 整块删掉老读法后，把它们放回 tests/test_flowfix_c18_resume.py 的 ResumeRound2/ResumeRound3（同一套测试工具），必须全过。
"""
    def test_new_review_saved_then_interrupted_is_judged_by_its_list(self):
        """看不懂停下后接着跑，新复查回复刚存下就中断：再续跑要按清单判定，不能把这份回复原文直接交给写代码的（严重 1）。"""
        self.run_flow([RAW, RAW])
        fresh = review("不通过", [item(what="导出后无法保存")])
        with self.interrupt_at("回复", src="复查员"), self.assertRaises(_returns.Interrupted):
            self.run_flow([fresh], resume=True)
        card = self.run_flow([review("通过")], resume=True)
        devs = self.prompts("开发员")
        self.assertEqual(len(devs), 2)
        self.assertIn("导出后无法保存", devs[-1])
        self.assertNotIn("复查过程：看了改动。", devs[-1], "交的是清单条目，不是回复原文")
        self.assertEqual(card.state, "待验收")



    def test_rewrite_second_line_reject_with_pass_list_cannot_pass(self):
        """原话写通过但漏了清单，重写第二行写不通过、清单却写通过：不能放行（中等 3）。"""
        rewrite = "先说一句：重写如下。\n" + review("通过").replace("结论：通过", "结论：不通过", 1)
        card = self.run_flow(["结论：通过\n看了改动，没问题。", rewrite])
        self.assertNotEqual(card.state, "待验收")



    def test_second_resume_never_picks_up_the_void_reply(self):
        """看不懂停下 → 接着跑，复查员这次没给结论又停下 → 再接着跑：作废的原话仍不能交给写代码的（严重 1）。"""
        self.run_flow([RAW, RAW])
        card = self.run_flow(["看了一下，先这样。"], resume=True)
        self.assertEqual(card.state, "需Claude介入")
        card = self.run_flow([review("通过")], resume=True)
        self.assertNotIn("按钮颜色不对", "\n".join(self.prompts("开发员")))
        self.assertEqual(len(self.prompts("开发员")), 1)
        self.assertEqual(card.state, "待验收")



    def test_void_reply_does_not_use_up_a_return(self):
        """看不懂停下后接着跑，新清单指出这次改的中等问题：要退回写代码的，不能因为旧回复多算一次而直接放行（严重 2）。"""
        self.run_flow([RAW, RAW])
        card = self.run_flow([review("不通过", [item(what="导出后无法保存")]), review("通过")], resume=True)
        self.assertEqual(len(self.prompts("开发员")), 2, "要退回写代码的")
        self.assertEqual(card.state, "待验收")



    def test_second_line_reject_with_note_cannot_pass_as_original(self):
        """第二行写“结论：不通过（说明）”、清单写通过：当原话时不能放行（中等 3）。"""
        card = self.run_flow([self.TRICKY, self.TRICKY])
        self.assertNotEqual(card.state, "待验收")



    def test_second_line_reject_with_note_cannot_pass_as_rewrite(self):
        """同上，出现在格式重写里也不能放行（中等 3）。"""
        card = self.run_flow(["结论：通过\n看了改动，没问题。", self.TRICKY])
        self.assertNotEqual(card.state, "待验收")



    def test_stop_then_no_verdict_then_saved_and_interrupted(self):
        """看不懂停下 → 接着跑没给结论又停下 → 再接着跑拿到合格清单、刚存下就中断 → 再续跑：按清单退回，不交原文（严重 1）。"""
        self.run_flow([RAW, RAW])
        self.run_flow(["看了一下，先这样。"], resume=True)
        fresh = review("不通过", [item(what="导出后无法保存")])
        with self.interrupt_at("回复", src="复查员"), self.assertRaises(_returns.Interrupted):
            self.run_flow([fresh], resume=True)
        card = self.run_flow([review("通过")], resume=True)
        devs = self.prompts("开发员")
        self.assertEqual(len(devs), 2)
        self.assertIn("导出后无法保存", devs[-1])
        self.assertNotIn("复查过程：看了改动。", devs[-1])
        self.assertEqual(card.state, "待验收")



    def test_void_reply_does_not_count_as_same_place(self):
        """作废的旧回复和后来的清单写了同一处：停下判断不能因此说“同一个问题反复出现”（中等 4）。"""
        void = "结论：不通过\n导出页样式乱了，位置 app/x.py:5。"
        self.run_flow([void, void])
        # 每轮正文写法不同：测试替身默认的“复查过程：看了改动。”每轮一样，会被当成同一个问题
        later = [review("不通过", [item(level="严重", what="搜索结果为空时崩溃", where="app/a.py:1")], prose="第一轮看了搜索。"),
                 checked("不通过", [item(level="严重", what="分享链接打不开", where="app/b.py:2")], [(1, "改好了")]).replace(
                     "复查过程：看了改动。", "第二轮看了分享。"),
                 checked("不通过", [item(level="严重", what="导出时把原文件清空", where="app/x.py:5")], [(1, "改好了")]).replace(
                     "复查过程：看了改动。", "第三轮看了导出。")]
        card = self.run_flow(later, resume=True)
        self.assertEqual(card.state, "需Claude介入")
        self.assertNotIn("反复出现", self.events("转人工")[-1].get("say") or "")


