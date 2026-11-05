# -*- coding: utf-8 -*-
"""The Atlanta Fed wage tracker (app/adapters/us_wage_tracker.py) takes its
workbook's address from the tracker page: on 2026-11-05 the file had moved
from datafiles/chcs/ to research-and-data/data/ and the old path answered 404."""
import unittest

from app.adapters import us_wage_tracker as a


class DataUrl(unittest.TestCase):
    def test_the_link_on_the_page_is_used(self):
        html = u'<a href="/-/media/Project/Atlanta/FRBA/Documents/research-and-data/data/wage-growth-tracker/wage-growth-data.xlsx?la=en">Download</a>'
        self.assertEqual(a.data_url(html),
                         "https://www.atlantafed.org/-/media/Project/Atlanta/FRBA/Documents/research-and-data/data/"
                         "wage-growth-tracker/wage-growth-data.xlsx?la=en")

    def test_no_link_means_none(self):
        self.assertIsNone(a.data_url(u"<p>moved again</p>"))
        self.assertTrue(a.URL.endswith("wage-growth-data.xlsx"))


if __name__ == "__main__":
    unittest.main()
