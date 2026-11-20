"""规则包第③步：派活的话搬进 agents/交代/ 以后，和搬之前一字不差（docs/规则包/方案-第③步.md）。

快照是搬家前用旧代码拼出来的（tests/prompt_cases.py）；搬完以后用新代码再拼一遍逐条比对。
以后有意改说法：改 agents/交代/ 里的文件，再运行 `python3 tests/prompt_cases.py <角色>` 更新快照，提交里写明改了什么。
"""
import json
import unittest
from pathlib import Path

import prompt_cases

SNAPSHOTS = Path(__file__).resolve().parent / "fixtures" / "交代快照"


class SameWords(unittest.TestCase):
    def test_every_role(self):
        for path in sorted(SNAPSHOTS.glob("*.json")):
            role = path.stem
            want = json.loads(path.read_text(encoding="utf-8"))
            got = prompt_cases.render(role)
            self.assertEqual(sorted(got), sorted(want), f"{role} 的情况列表变了")
            for case, text in want.items():
                with self.subTest(role=role, case=case):
                    self.assertEqual(got[case], text)


if __name__ == "__main__":
    unittest.main()
