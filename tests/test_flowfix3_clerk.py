"""流程修理第三批 ㉖ ㊴ ㊵：用户说“按施工单某某路径建卡、不用读文件”时，行政直接把路径写进卡片建卡，不去读文件；
出确认框后只说“点确认就建卡，之后调度员写方案给你批”，不再问“要不要顺手安排开工和复查”。"""
import unittest

from desktop.clerk import desk, tasks


class Rules(unittest.TestCase):
    def test_build_from_path_without_reading(self):
        self.assertIn("按施工单", desk.ROLE)
        self.assertIn("路径原样写进", desk.ROLE)
        self.assertIn("不用先读", desk.ROLE)

    def test_no_extra_question_after_box(self):
        self.assertIn("开工和复查由调度员按流程安排", desk.ROLE)
        reply = tasks._TASK_SHOWN
        self.assertIn("不要再问", reply)
        self.assertIn("开工", reply)


if __name__ == "__main__":
    unittest.main()
