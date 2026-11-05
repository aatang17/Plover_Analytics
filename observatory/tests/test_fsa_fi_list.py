# -*- coding: utf-8 -*-
"""The FSA's regional-institution list (app/adapters/fsa_fi_list.py) finds the
newest edition's two workbooks on the page, wherever the FSA puts them. On
2026-11-04 the 2025 files answered 404 and the 2026 pair sat at a new path."""
import unittest

from app.adapters import fsa_fi_list as a

PAGE = u'''
<a href="/policy/chusho/shihyou/zenkoku/2026-1.xlsx">地域銀行</a>
<a href="/policy/chusho/shihyou/zenkoku/2026-2.xlsx">信用金庫・信用組合</a>
<a href="/policy/chusho/shihyou/zenkoku/2025-1.xlsx">前年</a>
<a href="/policy/chusho/shihyou/zenkoku/2025-2.xlsx">前年</a>
'''


class LinkedFiles(unittest.TestCase):
    def test_the_newest_complete_pair_wins(self):
        self.assertEqual(a.linked_files(PAGE), {
            "banks": "https://www.fsa.go.jp/policy/chusho/shihyou/zenkoku/2026-1.xlsx",
            "coops": "https://www.fsa.go.jp/policy/chusho/shihyou/zenkoku/2026-2.xlsx"})

    def test_a_year_with_one_file_is_passed_over(self):
        page = PAGE.replace("2026-2.xlsx", "2026-2.pdf")
        self.assertEqual(a.linked_files(page)["banks"],
                         "https://www.fsa.go.jp/policy/chusho/shihyou/zenkoku/2025-1.xlsx")

    def test_a_moved_folder_still_fits(self):
        page = u'<a href="https://www.fsa.go.jp/new/place/2027-1.xlsx">a</a><a href="/new/place/2027-2.xlsx">b</a>'
        self.assertEqual(a.linked_files(page), {
            "banks": "https://www.fsa.go.jp/new/place/2027-1.xlsx",
            "coops": "https://www.fsa.go.jp/new/place/2027-2.xlsx"})

    def test_nothing_linked_means_the_last_known_pair(self):
        self.assertEqual(a.linked_files(u"<p>redesigned</p>"), {})
        self.assertEqual(set(a.FILES), {"banks", "coops"})


if __name__ == "__main__":
    unittest.main()
