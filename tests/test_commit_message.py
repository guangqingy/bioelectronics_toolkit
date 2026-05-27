from __future__ import annotations

import unittest

from dev_scripts.check_commit_message import is_valid_subject


class CommitMessageTests(unittest.TestCase):
    def test_conventional_commit_subjects_are_accepted(self) -> None:
        self.assertTrue(is_valid_subject("feat(web): add self-check command"))
        self.assertTrue(is_valid_subject("fix: handle missing manifest path"))
        self.assertTrue(is_valid_subject("docs(readme)!: document release boundary"))

    def test_non_conventional_subject_is_rejected(self) -> None:
        self.assertFalse(is_valid_subject("Improve histology analysis"))
        self.assertFalse(is_valid_subject("Add missing dependencies"))

    def test_merge_and_fixup_subjects_are_accepted(self) -> None:
        self.assertTrue(is_valid_subject("Merge pull request #12 from user/topic"))
        self.assertTrue(is_valid_subject("fixup! feat(web): add self-check command"))


if __name__ == "__main__":
    unittest.main()
