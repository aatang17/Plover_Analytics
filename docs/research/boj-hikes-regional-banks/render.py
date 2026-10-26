# -*- coding: utf-8 -*-
"""draft.md -> out/boj-hikes-regional-banks.md: each chart placeholder becomes the
image, a caption and the link to its underlying data."""
import io, os, re
HERE = os.path.dirname(os.path.abspath(__file__))
AS_OF = "2026-09-24"
CHARTS = {
    1: ("charts/01-rates.png", "https://www.stat-search.boj.or.jp/", "Bank of Japan Time-Series Data Search (FM01, IR04, IR02)"),
    2: ("charts/02-gain-by-bank.png", "https://ploveranalytics.com/banks.html?as_of=%s" % AS_OF, None),
    3: ("charts/03-driver.png", "https://ploveranalytics.com/banks.html?as_of=%s" % AS_OF, None),
    4: ("charts/04-gain-vs-cost.png", "https://ploveranalytics.com/banks.html?as_of=%s" % AS_OF, None),
    5: ("charts/05-next-increase.png", "https://ploveranalytics.com/banks.html?as_of=%s" % AS_OF, None),
}
s = io.open(os.path.join(HERE, "draft.md"), encoding="utf-8").read()
def sub(m):
    n, title = int(m.group(1)), m.group(2)
    img, url, label = CHARTS[n]
    link = "[%s](%s)" % (label, url) if label else "[Underlying data](%s), frozen at 24 September 2026" % url
    return "![%s](%s)\n\n*Chart %d. %s. %s.*" % (title, img, n, title, link)
s, k = re.subn(r"\*\*\[Chart (\d) — (.+?)\]\*\*", sub, s)
assert k == 5, k
os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
io.open(os.path.join(HERE, "out", "boj-hikes-regional-banks.md"), "w", encoding="utf-8").write(s)
print("charts placed:", k)
