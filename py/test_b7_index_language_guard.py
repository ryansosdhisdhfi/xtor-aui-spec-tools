#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aidoc_index as idx  # noqa: E402


class IndexLanguageGuardTests(unittest.TestCase):
    def test_index_summary_prompt_is_english_oriented(self) -> None:
        self.assertIn("must be in English", idx.INDEX_SUMMARY_ROLE)
        self.assertIn("Use English", idx.INDEX_SUMMARY_SYSTEM)

    def test_contains_cjk_detection(self) -> None:
        self.assertTrue(idx._contains_cjk("本章介绍PIPE接口"))
        self.assertFalse(idx._contains_cjk("This section describes the PIPE interface."))

    def test_normalize_summary_payload_still_reads_standard_keys(self) -> None:
        summary, keywords = idx._normalize_summary_payload(
            {"summary": "English summary", "keywords": ["pipe", "phy"]}
        )
        self.assertEqual(summary, "English summary")
        self.assertEqual(keywords, ["pipe", "phy"])


if __name__ == "__main__":
    unittest.main()
