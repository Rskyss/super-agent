import unittest

from collab.scope import out_of_scope, read_allowed


class ReadAllowedTest(unittest.TestCase):
    def test_reads_bullets_under_scope_heading(self):
        text = "# 需求\n\n## 允许改动的范围\n- src/components/extract/*\n- tests/*\n\n## 其他\n- 不相关\n"
        self.assertEqual(read_allowed(text), ["src/components/extract/*", "tests/*"])

    def test_missing_heading_returns_none(self):
        self.assertIsNone(read_allowed("# 需求\n只有正文\n"))


class OutOfScopeTest(unittest.TestCase):
    def test_files_inside_scope_are_fine(self):
        self.assertEqual(out_of_scope(["src/components/extract/a.tsx"], ["src/components/extract/*"]), [])

    def test_files_outside_scope_are_reported(self):
        self.assertEqual(out_of_scope(["README.md", "src/lib/x.ts"], ["src/components/extract/*"]),
                         ["README.md", "src/lib/x.ts"])

    def test_directory_entries_inside_scope_are_fine(self):
        self.assertEqual(out_of_scope(["src/components/extract/__tests__/"], ["src/components/extract/*"]), [])

    def test_collab_and_dependency_links_are_always_ignored(self):
        self.assertEqual(out_of_scope([".collab/", "node_modules", "汇报.md"], ["src/*"]), ["汇报.md"])


class CacheFilesTest(unittest.TestCase):
    def test_build_caches_are_never_out_of_scope(self):
        changed = ["__pycache__/", "tests/__pycache__/", "a.pyc", ".pytest_cache/", "README.md"]
        self.assertEqual(out_of_scope(changed, ["src/*"]), ["README.md"])


if __name__ == "__main__":
    unittest.main()
